"""Independent synthetic-catalog grounding evaluation; labels never enter service requests."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Sequence

from .answer import AnswerComposer
from .config import Settings
from .schemas import EvidenceRef, ShopRequest, ShopResponse
from .storage import CatalogStore
from .workflow import ShoppingService

DATA = Path(__file__).parent / "data"
MODES = ("bm25", "vector", "hybrid")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _excerpt_in_order(excerpt: str, original: str) -> bool:
    """A claim_excerpt may join original windows with an ellipsis; order matters."""
    windows = [part.strip() for part in re.split(r"\s*…\s*", excerpt) if part.strip()]
    if not windows:
        return False
    cursor = 0
    for window in windows:
        position = original.find(window, cursor)
        if position < 0:
            return False
        cursor = position + len(window)
    return True


def validate_reference(store: CatalogStore, ref: EvidenceRef, subject_id: str) -> dict:
    """Check a current subject-owned citation and its ordered verbatim original text."""
    if ref.source_type == "catalog_snapshot":
        if not ref.source_id.startswith(f"catalog:{subject_id}:sha256:"):
            return {"valid": False, "reason": "wrong_subject"}
        current = store.catalog_snapshot_source(subject_id)
        if current is None or current[0] != ref.source_id:
            return {"valid": False, "reason": "stale_snapshot"}
        if not ref.excerpt or ref.excerpt not in current[1]:
            return {"valid": False, "reason": "fabricated_excerpt"}
        return {"valid": True, "reason": ""}
    source = store.get_document(ref.source_id)
    if source is None:
        return {"valid": False, "reason": "missing_or_stale_source"}
    if (source.product_id or source.faq_id) != subject_id:
        return {"valid": False, "reason": "wrong_subject"}
    if source.source_type != ref.source_type:
        return {"valid": False, "reason": "wrong_source_type"}
    if not _excerpt_in_order(ref.excerpt, source.original_text):
        return {"valid": False, "reason": "fabricated_or_reordered_excerpt"}
    return {"valid": True, "reason": ""}


def validate_atom_evidence(store: CatalogStore, ref: EvidenceRef, atom: dict) -> dict:
    """Require both current verbatim evidence and this gold atom's source and polarity."""
    result = validate_reference(store, ref, atom["subject_id"])
    if not result["valid"]:
        return result
    if atom["status"] != "supported":
        return {"valid": False, "reason": "gold_not_supported"}
    for gold in atom["evidence"]:
        if (gold["source_id"] == ref.source_id and gold["source_type"] == ref.source_type
                and gold["polarity"] == atom["polarity"]
                and gold["excerpt"] in ref.excerpt):
            return {"valid": True, "reason": ""}
    return {"valid": False, "reason": "gold_source_excerpt_or_polarity_mismatch"}


def _reference_detail(store: CatalogStore, ref: EvidenceRef, subject_id: str) -> dict:
    """Expose provenance checks without changing the citation scoring contract."""
    current = (store.is_current_catalog_snapshot(ref.source_id) if ref.source_type == "catalog_snapshot"
               else store.get_document(ref.source_id) is not None)
    return {"source_id": ref.source_id, "source_type": ref.source_type,
            "excerpt": ref.excerpt, "current": current,
            **validate_reference(store, ref, subject_id)}


def _prediction_details(case: dict, response: ShopResponse, store: CatalogStore) -> list[dict]:
    details = []
    for item_index, item in enumerate(response.recommendations):
        for index, judgment in enumerate(item.condition_judgments):
            matches = [(atom_index, atom) for atom_index, atom in enumerate(case["atomic_conditions"])
                       if atom["subject_id"] == item.product_id and _match_judgment(judgment, atom)]
            references = [_reference_detail(store, ref, item.product_id) for ref in judgment.evidence]
            matched_gold = []
            for atom_index, atom in matches:
                checked = [validate_atom_evidence(store, ref, atom) for ref in judgment.evidence]
                supported = (judgment.status == "supported" and not judgment.conflict
                             and any(result["valid"] for result in checked))
                matched_gold.append({
                    "atom_index": atom_index, "subject_id": atom["subject_id"],
                    "field": atom["field"], "operator": atom["operator"],
                    "expected": atom["expected"], "unit": atom["unit"],
                    "polarity": atom["polarity"], "gold_status": atom["status"],
                    "evidence_supported": supported,
                    "reference_gold_checks": [
                        {"source_id": ref.source_id, "valid": result["valid"], "reason": result["reason"]}
                        for ref, result in zip(judgment.evidence, checked)
                    ],
                })
            if judgment.status != "supported":
                unsupported_reason = "judgment_not_supported"
            elif judgment.conflict:
                unsupported_reason = "judgment_conflict"
            elif not matches:
                unsupported_reason = "no_matching_gold_atom"
            elif any(match["evidence_supported"] for match in matched_gold):
                unsupported_reason = None
            elif not judgment.evidence:
                unsupported_reason = "judgment_no_evidence"
            else:
                unsupported_reason = next(
                    (check["reason"] for match in matched_gold
                     for check in match["reference_gold_checks"] if not check["valid"]),
                    "gold_evidence_not_supported",
                )
            details.append({
                "prediction_id": f"{item.product_id}@{item_index}#{index}", "subject_id": item.product_id,
                "field": judgment.field, "operator": judgment.operator,
                "expected": judgment.expected, "unit": judgment.unit,
                "status": judgment.status, "conflict": judgment.conflict,
                "references": references, "matched_gold_atoms": matched_gold,
                "unsupported_reason": unsupported_reason,
            })
    return details


def _read_labels(path: Path) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("Gold labels are empty or have duplicate case IDs")
    return cases


def _preflight_gold(store: CatalogStore, cases: list[dict]) -> None:
    """Validate frozen gold against the imported raw catalog before calling the service."""
    for case in cases:
        if case.get("route") not in {"product", "faq", "mixed"}:
            raise ValueError(f"{case['case_id']}: invalid route")
        if not case.get("synthetic"):
            raise ValueError(f"{case['case_id']}: labels must declare synthetic data")
        for product_id in case["eligible_product_ids"]:
            product = store.get_product(product_id)
            if product is None or product.stock <= 0:
                raise ValueError(f"{case['case_id']}: eligible product unavailable: {product_id}")
        for faq_id in case["gold_faq_ids"]:
            if faq_id not in {faq.faq_id for faq in store.list_faqs()}:
                raise ValueError(f"{case['case_id']}: missing FAQ: {faq_id}")
        for atom in case["atomic_conditions"]:
            if atom["status"] not in {"supported", "refuted", "unknown"}:
                raise ValueError(f"{case['case_id']}: invalid atom status")
            if atom["status"] != "unknown" and not atom["evidence"]:
                raise ValueError(f"{case['case_id']}: claim without gold evidence")
            for gold in atom["evidence"]:
                ref = EvidenceRef(source_id=gold["source_id"], source_type=gold["source_type"], excerpt=gold["excerpt"])
                checked = validate_reference(store, ref, atom["subject_id"])
                if not checked["valid"]:
                    raise ValueError(f"{case['case_id']}: invalid gold source: {checked['reason']}")
                if gold["source_type"] == "catalog_snapshot":
                    product = store.get_product(atom["subject_id"])
                    if product is None:
                        raise ValueError(f"{case['case_id']}: missing gold product")
                    raw = next((d.original_text for d in store.get_product_documents(product.product_id)), "")
                else:
                    source = store.get_document(gold["source_id"])
                    raw = source.original_text if source else ""
                if gold["original_excerpt"] not in raw:
                    raise ValueError(f"{case['case_id']}: gold excerpt absent from raw line")


def _same_expected(actual: str | float, gold: str | float) -> bool:
    return actual == gold or str(actual) == str(gold)


def _match_judgment(judgment, atom: dict) -> bool:
    return (judgment.field == atom["field"] and judgment.operator == atom["operator"]
            and _same_expected(judgment.expected, atom["expected"])
            and judgment.unit == atom["unit"])


def score_case(case: dict, response: ShopResponse, store: CatalogStore) -> dict:
    """Score structured claims and citations only, never free-form answer semantics."""
    expected = set(case["eligible_product_ids"])
    returned = [item.product_id for item in response.recommendations]
    citations: list[dict] = []
    for item in response.recommendations:
        for ref in item.evidence:
            citations.append({"context": "recommendation", "subject_id": item.product_id,
                              **_reference_detail(store, ref, item.product_id)})
        for judgment in item.condition_judgments:
            for ref in judgment.evidence:
                citations.append({"context": "condition", "subject_id": item.product_id,
                                  **_reference_detail(store, ref, item.product_id)})
    for ref in response.knowledge_evidence:
        source = store.get_document(ref.source_id)
        faq_id = source.faq_id if source else ""
        citations.append({"context": "faq", "subject_id": faq_id,
                          **_reference_detail(store, ref, faq_id)})
    condition_predictions = _prediction_details(case, response, store)
    atoms: list[dict] = []
    supported_gold = 0
    covered_gold = 0
    for atom_index, atom in enumerate(case["atomic_conditions"]):
        subject = atom["subject_id"]
        item = next((item for item in response.recommendations if item.product_id == subject), None)
        matching_prediction_ids = [prediction["prediction_id"] for prediction in condition_predictions
                                   if prediction["subject_id"] == subject
                                   and prediction["field"] == atom["field"]
                                   and prediction["operator"] == atom["operator"]
                                   and _same_expected(prediction["expected"], atom["expected"])
                                   and prediction["unit"] == atom["unit"]]
        faq_references = []
        if subject.startswith("G4-FAQ-"):
            refs = response.knowledge_evidence
            observed = bool(refs)
            support = any(validate_atom_evidence(store, ref, atom)["valid"] for ref in refs)
            predicted_status = "supported" if support else "unverified" if observed else "not_observed"
            faq_references = [{**_reference_detail(store, ref, subject),
                               "gold_check": validate_atom_evidence(store, ref, atom)} for ref in refs]
        else:
            matching = [j for j in item.condition_judgments if _match_judgment(j, atom)] if item else []
            observed = bool(matching)
            predicted_status = matching[0].status if matching else "not_observed"
            support = any(j.status == "supported" and not j.conflict and any(
                validate_atom_evidence(store, ref, atom)["valid"] for ref in j.evidence
            ) for j in matching)
        eligible_subject = subject in expected or subject in case["gold_faq_ids"]
        coverage_target = atom["status"] == "supported" and eligible_subject
        if atom["status"] == "supported" and eligible_subject:
            supported_gold += 1
            covered_gold += int(support)
        if not coverage_target or support:
            uncovered_reason = None
        elif subject.startswith("G4-FAQ-"):
            uncovered_reason = next(
                (entry["gold_check"]["reason"] for entry in faq_references
                 if not entry["gold_check"]["valid"]),
                "no_faq_citation",
            )
        elif item is None:
            uncovered_reason = "subject_not_returned"
        elif not matching_prediction_ids:
            uncovered_reason = "no_matching_judgment"
        else:
            uncovered_reason = next(
                (prediction["unsupported_reason"] for prediction in condition_predictions
                 if prediction["prediction_id"] in matching_prediction_ids
                 and prediction["unsupported_reason"]),
                "gold_evidence_not_supported",
            )
        atoms.append({"subject_id": subject, "field": atom["field"], "operator": atom["operator"],
                      "expected": atom["expected"], "polarity": atom["polarity"],
                      "gold_status": atom["status"], "predicted_status": predicted_status,
                      "evidence_supported": support, "observed": observed,
                      "atom_index": atom_index, "unit": atom["unit"],
                      "gold_evidence": atom["evidence"], "coverage_target": coverage_target,
                      "matching_prediction_ids": matching_prediction_ids,
                      "faq_references": faq_references, "uncovered_reason": uncovered_reason})
    erroneous_support = 0
    predicted_support = 0
    for item in response.recommendations:
        for judgment in item.condition_judgments:
            if judgment.status != "supported":
                continue
            predicted_support += 1
            gold_matches = [atom for atom in case["atomic_conditions"] if atom["subject_id"] == item.product_id and _match_judgment(judgment, atom)]
            if (judgment.conflict or not any(atom["status"] == "supported" and any(
                    validate_atom_evidence(store, ref, atom)["valid"] for ref in judgment.evidence
                    ) for atom in gold_matches)):
                erroneous_support += 1
    return {
        "case_id": case["case_id"], "route_expected": case["route"], "route_actual": response.route,
        "requested_mode": response.requested_retrieval_mode,
        "effective_retrieval_modes": response.effective_retrieval_modes,
        "expected_product_ids": sorted(expected), "returned_product_ids": returned,
        "exact_set": set(returned) == expected and len(returned) == len(expected),
        "no_answer_safe": (not returned and not response.knowledge_evidence) if not expected and not case["gold_faq_ids"] else None,
        "supported_gold_atoms": supported_gold, "covered_gold_atoms": covered_gold,
        "predicted_support_claims": predicted_support, "erroneous_support_claims": erroneous_support,
        "citations": citations, "valid_citations": sum(c["valid"] for c in citations),
        "atomic_results": atoms, "condition_predictions": condition_predictions,
        "warnings": response.warnings,
        "latency_ms": response.total_latency_ms,
    }


async def _run_cases(service: ShoppingService, cases: list[dict], mode: str) -> list[dict]:
    scored = []
    for case in cases:
        request = ShopRequest(user_id=f"grounding-{case['case_id']}", query=case["query"],
                              num_items=case["num_items"], retrieval_mode=mode)
        response = await service.recommend(request)
        if not response.request_id or response.user_id != request.user_id:
            raise RuntimeError(f"{case['case_id']}: invalid service response")
        if any("失败" in warning or "不可用" in warning for warning in response.warnings):
            raise RuntimeError(f"{case['case_id']}: service warning indicates runtime failure: {response.warnings}")
        scored.append(score_case(case, response, service.store))
    return scored


def _aggregate(rows: list[dict]) -> dict:
    count = len(rows)
    total_gold = sum(row["supported_gold_atoms"] for row in rows)
    total_covered = sum(row["covered_gold_atoms"] for row in rows)
    total_citations = sum(len(row["citations"]) for row in rows)
    valid_citations = sum(row["valid_citations"] for row in rows)
    predicted = sum(row["predicted_support_claims"] for row in rows)
    erroneous = sum(row["erroneous_support_claims"] for row in rows)
    noanswer = [row for row in rows if row["no_answer_safe"] is not None]
    effective = Counter(mode for row in rows for mode in row["effective_retrieval_modes"].values())
    return {
        "case_count": count, "error_count": 0,
        "exact_set_cases": sum(row["exact_set"] for row in rows), "exact_set_denominator": count,
        "exact_set_rate": sum(row["exact_set"] for row in rows) / count,
        "supported_atomic_coverage": {"numerator": total_covered, "denominator": total_gold,
                                      "rate": total_covered / total_gold if total_gold else None},
        "erroneous_support": {"numerator": erroneous, "denominator": predicted,
                              "rate": erroneous / predicted if predicted else None},
        "current_valid_citations": {"numerator": valid_citations, "denominator": total_citations,
                                    "rate": valid_citations / total_citations if total_citations else None},
        "no_answer_abstention": {"numerator": sum(row["no_answer_safe"] for row in noanswer),
                                 "denominator": len(noanswer),
                                 "rate": sum(row["no_answer_safe"] for row in noanswer) / len(noanswer) if noanswer else None},
        "route_matches": sum(row["route_expected"] == row["route_actual"] for row in rows),
        "effective_mode_counts": dict(effective),
        "latency_ms": {"mean": sum(row["latency_ms"] for row in rows) / count,
                       "total": sum(row["latency_ms"] for row in rows)},
        "cases": rows,
    }


def evaluate_grounding(catalog_path: str | Path, labels_path: str | Path) -> dict:
    catalog_path, labels_path = Path(catalog_path), Path(labels_path)
    settings = Settings(database_url="sqlite://", seed_demo=False, llm_api_key="", llm_model="",
                        embedding_model="", experiment_enabled=False)
    store = CatalogStore(settings.database_url)
    store.initialize()
    imported = store.import_documents_jsonl(catalog_path)
    if imported == 0:
        raise ValueError("Empty catalog")
    cases = _read_labels(labels_path)
    _preflight_gold(store, cases)
    service = ShoppingService(store, AnswerComposer(settings), settings)
    reports = {}
    for mode in MODES:
        reports[mode] = _aggregate(asyncio.run(_run_cases(service, cases, mode)))
    code_files = sorted(Path(__file__).parent.glob("*.py"))
    code_hash = hashlib.sha256(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in code_files)).hexdigest()
    return {
        "benchmark": "grounding_v1_audit_recheck", "synthetic": True,
        "run_type": "same-question audit recheck after diagnostic expansion; original first run remains frozen",
        "catalog_sha256": _sha256(catalog_path), "labels_sha256": _sha256(labels_path),
        "code_sha256": code_hash, "catalog_product_count": len(store.list_products(in_stock_only=False)),
        "catalog_faq_count": len(store.list_faqs()), "catalog_imported_documents": imported,
        "isolation": "fresh in-memory SQLite; no demo seeding, external model or experiment",
        "method": "structured condition and FAQ citation match against frozen gold plus current original-text excerpts; free-form answer semantics not scored",
        "modes": reports,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run frozen fourth-catalog grounding evaluation")
    parser.add_argument("--catalog", type=Path, default=DATA / "grounding_v1_catalog.jsonl")
    parser.add_argument("--labels", type=Path, default=DATA / "grounding_v1_labels.jsonl")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output is not None and args.output.exists():
        raise FileExistsError(args.output)
    report = evaluate_grounding(args.catalog, args.labels)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
