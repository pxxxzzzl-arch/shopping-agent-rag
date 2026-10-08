"""Offline checks on synthetic shopping questions, never online business metrics.

The bundled cases are hand-written questions. They contain no real user events,
clicks, conversions, or measured business outcomes. Rates here describe only
the local service's behavior on those questions.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping


DEFAULT_CASES_PATH = Path(__file__).parent / "data" / "eval_cases.jsonl"
DEFAULT_RETRIEVAL_LABELS_PATH = Path(__file__).parent / "data" / "retrieval_labels.jsonl"


@dataclass(frozen=True)
class EvaluationCase:
    query: str
    expected_category: str | None
    requires_evidence: bool
    num_items: int
    forbidden_product_ids: tuple[str, ...] = ()
    max_price_cap: float | None = None
    expected_empty: bool = False


def load_cases(path: str | Path) -> list[EvaluationCase]:
    """Read and validate a JSONL file of synthetic evaluation questions."""

    cases: list[EvaluationCase] = []
    with Path(path).open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_number} must be a JSON object")

            query = row.get("query")
            category = row.get("expected_category")
            requires_evidence = row.get("requires_evidence")
            num_items = row.get("num_items", 5)
            forbidden_ids = row.get("forbidden_product_ids", [])
            price_cap = row.get("max_price_cap")
            expected_empty = row.get("expected_empty", False)
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"Line {line_number} needs a nonempty query")
            if category is not None and (
                not isinstance(category, str) or not category.strip()
            ):
                raise ValueError(f"Line {line_number} has an invalid expected_category")
            if not isinstance(requires_evidence, bool):
                raise ValueError(f"Line {line_number} needs a boolean requires_evidence")
            if isinstance(num_items, bool) or not isinstance(num_items, int) or not 1 <= num_items <= 10:
                raise ValueError(f"Line {line_number} needs num_items between 1 and 10")
            if not isinstance(forbidden_ids, list) or not all(
                isinstance(product_id, str) and product_id for product_id in forbidden_ids
            ):
                raise ValueError(f"Line {line_number} has invalid forbidden_product_ids")
            if price_cap is not None and (
                isinstance(price_cap, bool)
                or not isinstance(price_cap, (int, float))
                or not math.isfinite(price_cap)
                or price_cap <= 0
            ):
                raise ValueError(f"Line {line_number} has invalid max_price_cap")
            if not isinstance(expected_empty, bool):
                raise ValueError(f"Line {line_number} needs boolean expected_empty")
            if row.get("synthetic") is not True:
                raise ValueError(f"Line {line_number} must be labeled synthetic=true")

            cases.append(
                EvaluationCase(
                    query=query.strip(),
                    expected_category=category.strip() if category is not None else None,
                    requires_evidence=requires_evidence,
                    num_items=num_items,
                    forbidden_product_ids=tuple(forbidden_ids),
                    max_price_cap=float(price_cap) if price_cap is not None else None,
                    expected_empty=expected_empty,
                )
            )

    if not cases:
        raise ValueError("Evaluation file contains no cases")
    return cases


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _percentile(values: list[float], fraction: float) -> float | None:
    """Linearly interpolate sorted observations at zero-based fractional rank."""

    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 2
    )


async def evaluate(service: Any, cases_path: str | Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    """Run cases serially and return transparent, offline-only quality metrics.

    ``category_hit_rate`` is the fraction of labeled cases with at least one
    requested-category item in the returned top-k. ``in_stock_rate`` is the
    fraction of all returned items with positive stock. ``source_attachment_rate``
    is the fraction of source-required cases where the result is nonempty
    and every returned item carries a source. It does not measure whether
    the source supports every requested claim. Errors count as empty responses
    and are listed by case.
    ``hard_constraint_pass_rate`` checks labeled forbidden IDs and price caps.
    ``expected_presence_accuracy`` checks whether a case should have results.
    No expected labels are passed to the recommendation service.
    """

    from shopping_agent.schemas import ShopRequest

    cases = load_cases(cases_path)
    category_cases = 0
    category_hits = 0
    evidence_cases = 0
    evidence_hits = 0
    recommendation_count = 0
    in_stock_count = 0
    empty_cases = 0
    constraint_cases = 0
    constraint_passes = 0
    presence_passes = 0
    latencies_ms: list[float] = []
    errors: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        if case.expected_category is not None:
            category_cases += 1
        if case.requires_evidence:
            evidence_cases += 1

        # Distinct synthetic users avoid carrying personalization across cases.
        request = ShopRequest(
            user_id=f"synthetic_eval_{index:03d}",
            query=case.query,
            num_items=case.num_items,
        )
        started = perf_counter()
        try:
            response = await service.recommend(request)
        except Exception as exc:
            latencies_ms.append((perf_counter() - started) * 1000)
            empty_cases += 1
            errors.append({"case": index, "error_type": type(exc).__name__})
            continue

        measured_ms = (perf_counter() - started) * 1000
        reported_ms = _field(response, "total_latency_ms")
        if (
            isinstance(reported_ms, (int, float))
            and not isinstance(reported_ms, bool)
            and math.isfinite(reported_ms)
            and reported_ms >= 0
        ):
            latencies_ms.append(float(reported_ms))
        else:
            latencies_ms.append(measured_ms)

        recommendations = _field(response, "recommendations", []) or []
        if not isinstance(recommendations, (list, tuple)):
            raise TypeError(f"Case {index}: recommendations must be a list")
        if not recommendations:
            empty_cases += 1
        if bool(recommendations) != case.expected_empty:
            presence_passes += 1

        if case.forbidden_product_ids or case.max_price_cap is not None:
            constraint_cases += 1
            if recommendations and all(
                _field(item, "product_id") not in case.forbidden_product_ids
                and (
                    case.max_price_cap is None
                    or (
                        isinstance(_field(item, "price"), (int, float))
                        and _field(item, "price") <= case.max_price_cap
                    )
                )
                for item in recommendations
            ):
                constraint_passes += 1

        recommendation_count += len(recommendations)
        if case.expected_category is not None and any(
            _field(item, "category") == case.expected_category
            for item in recommendations
        ):
            category_hits += 1
        if case.requires_evidence and recommendations and all(
            bool(_field(item, "evidence", [])) for item in recommendations
        ):
            evidence_hits += 1
        for item in recommendations:
            stock = _field(item, "stock")
            if (
                isinstance(stock, (int, float))
                and not isinstance(stock, bool)
                and math.isfinite(stock)
                and stock > 0
            ):
                in_stock_count += 1

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "dataset": "synthetic_evaluation_questions",
        "case_count": len(cases),
        "category_hit_rate": rate(category_hits, category_cases),
        "category_cases": category_cases,
        "in_stock_rate": rate(in_stock_count, recommendation_count),
        "recommendation_count": recommendation_count,
        "source_attachment_rate": rate(evidence_hits, evidence_cases),
        "source_required_cases": evidence_cases,
        "latency_p50_ms": _percentile(latencies_ms, 0.5),
        "latency_p95_ms": _percentile(latencies_ms, 0.95),
        "empty_result_rate": rate(empty_cases, len(cases)),
        "expected_presence_accuracy": rate(presence_passes, len(cases)),
        "hard_constraint_pass_rate": rate(constraint_passes, constraint_cases),
        "hard_constraint_cases": constraint_cases,
        "error_count": len(errors),
        "errors": errors,
    }


def load_retrieval_labels(path: str | Path = DEFAULT_RETRIEVAL_LABELS_PATH) -> list[dict[str, Any]]:
    """Independent source labels, read only by offline evaluation."""
    labels = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(labels) < 20 or any(
        row.get("synthetic") is not True
        or row.get("topic") not in {"product", "faq", "negation", "unanswerable"}
        or not isinstance(row.get("relevant_source_ids"), list)
        or (
            row.get("answerability") == "answerable"
            and not isinstance(row.get("support_claim"), str)
        )
        for row in labels
    ):
        raise ValueError("Retrieval labels must contain at least 20 valid synthetic cases")
    return labels


async def evaluate_retrieval_modes(
    service: Any, labels_path: str | Path = DEFAULT_RETRIEVAL_LABELS_PATH
) -> dict[str, Any]:
    """Compare actual search and final citations against frozen source labels.

    Recall@5 counts an answerable question as found when any of its gold source
    IDs occurs in top five. Evidence support counts *gold-matching citations*
    among all returned citations, including irrelevant ones. This is not the
    older source-attachment metric. Product and FAQ response metrics have
    separate denominators; a source-ID match does not verify answer wording.
    No labels are passed into recommendation.
    """
    from shopping_agent.schemas import ShopRequest

    labels_path = Path(labels_path)
    labels = load_retrieval_labels(labels_path)
    digest = hashlib.sha256(labels_path.read_bytes()).hexdigest()
    report: dict[str, Any] = {
        "dataset": "synthetic_independent_retrieval_labels",
        "label_count": len(labels),
        "label_sha256": digest,
        "label_path": str(labels_path),
        "embedding_provider": service.embedding.name,
        "modes": {},
        "metric_definitions": {
            "recall_at_5": "answerable cases with at least one gold source in top 5 / answerable cases",
            "evidence_support_rate": "gold-matching top-5 citations / all top-5 citations, including unanswerable cases",
            "gold_claim_coverage": "answerable responses citing at least one current pre-labeled source for support_claim and no forbidden source / answerable cases",
            "all_recommendations_gold_supported_rate": (
                "LEGACY: answerable responses where every recommendation has a gold "
                "source citation, with no forbidden citation / all answerable cases. "
                "Includes six FAQ cases with empty recommendation lists in the "
                "bundled labels; their empty lists pass all(). Each label names "
                "only one source, so this is not real per-item evidence support."
            ),
            "product_first_recommendation_gold_hit_rate": (
                "answerable product/negation cases whose first recommendation "
                "cites a labeled gold source / answerable product/negation cases; "
                "empty results miss"
            ),
            "product_all_recommendations_unique_gold_supported_rate": (
                "answerable product/negation cases with exactly one labeled gold "
                "source where a nonempty result has every recommendation cite "
                "that source, no forbidden citation, and all cited sources still "
                "exist / answerable product/negation cases with exactly one "
                "labeled gold source. This strict single-source proxy is not "
                "proof that every distinct item is actually supported."
            ),
            "product_average_recommendation_count": (
                "recommendations returned across answerable product/negation "
                "cases / answerable product/negation cases, including empty results"
            ),
            "faq_gold_citation_rate": (
                "answerable FAQ cases whose knowledge_evidence cites a labeled "
                "gold FAQ source and no forbidden source / answerable FAQ cases; "
                "a citation match does not prove the answer text faithful"
            ),
            "no_answer_safe_rate": "unanswerable responses with no recommendation and no FAQ citation / unanswerable cases",
        },
    }
    answerable = sum(row["answerability"] == "answerable" for row in labels)
    unanswerable = len(labels) - answerable
    product_cases = sum(
        row["answerability"] == "answerable" and row["topic"] in {"product", "negation"}
        for row in labels
    )
    unique_gold_product_cases = sum(
        row["answerability"] == "answerable"
        and row["topic"] in {"product", "negation"}
        and len(set(row["relevant_source_ids"])) == 1
        for row in labels
    )
    faq_cases = sum(
        row["answerability"] == "answerable" and row["topic"] == "faq"
        for row in labels
    )
    for mode in ("bm25", "vector", "hybrid"):
        recall_hits = 0
        supported_citations = 0
        citation_count = 0
        covered_claims = 0
        fully_supported_answers = 0
        product_first_gold_hits = 0
        product_all_unique_gold_supported = 0
        product_recommendation_count = 0
        faq_gold_citation_hits = 0
        safe_no_answers = 0
        forbidden_cases = 0
        fallback_count = 0
        search_latencies: list[float] = []
        answer_latencies: list[float] = []
        embedding_before = getattr(service.embedding, "calls", 0)
        model_before = getattr(service.composer, "calls", 0)
        retrieval_before = service.retriever.calls + service.faq_retriever.calls
        for index, row in enumerate(labels):
            gold = set(row["relevant_source_ids"])
            forbidden = set(row.get("forbidden_source_ids", []))
            retriever = (
                service.faq_retriever if row["topic"] == "faq" else service.retriever
            )
            started = perf_counter()
            found = retriever.search(row["query"], limit=5, mode=mode)
            search_latencies.append((perf_counter() - started) * 1000)
            if found.fallback_reason:
                fallback_count += 1
            ids = [hit.document.source_id for hit in found.hits]
            citation_count += len(ids)
            supported_citations += sum(source_id in gold for source_id in ids)
            if forbidden.intersection(ids):
                forbidden_cases += 1
            if row["answerability"] == "answerable":
                recall_hits += bool(gold.intersection(ids))

            # The runtime sees only the question and mode; labels stay here.
            request = ShopRequest(
                user_id=f"retrieval_eval_{mode}_{index:03d}",
                query=row["query"],
                num_items=3,
                retrieval_mode=mode,
            )
            started = perf_counter()
            response = await service.recommend(request)
            answer_latencies.append((perf_counter() - started) * 1000)
            answer_sources = {
                ref.source_id for item in response.recommendations for ref in item.evidence
            } | {ref.source_id for ref in response.knowledge_evidence}
            if (
                row["answerability"] == "answerable"
                and row["topic"] in {"product", "negation"}
            ):
                recommendations = response.recommendations
                product_recommendation_count += len(recommendations)
                if recommendations:
                    first_sources = {ref.source_id for ref in recommendations[0].evidence}
                    product_first_gold_hits += bool(gold.intersection(first_sources))
                if len(gold) == 1:
                    product_all_unique_gold_supported += bool(
                        recommendations
                        and not forbidden.intersection(answer_sources)
                        and all(
                            service.store.get_document(source_id) is not None
                            for source_id in answer_sources
                        )
                        and all(
                            any(ref.source_id in gold for ref in item.evidence)
                            for item in recommendations
                        )
                    )
            if row["answerability"] == "answerable" and row["topic"] == "faq":
                faq_sources = {ref.source_id for ref in response.knowledge_evidence}
                faq_gold_citation_hits += bool(
                    gold.intersection(faq_sources)
                    and not forbidden.intersection(faq_sources)
                )
            if row["answerability"] == "answerable":
                current_gold_citation = bool(
                    gold.intersection(answer_sources)
                    and not forbidden.intersection(answer_sources)
                    and all(service.store.get_document(source_id) is not None
                            for source_id in answer_sources)
                )
                covered_claims += current_gold_citation
                fully_supported_answers += bool(
                    current_gold_citation
                    and all(
                        any(ref.source_id in gold for ref in item.evidence)
                        for item in response.recommendations
                    )
                )
            else:
                safe_no_answers += bool(
                    not response.recommendations and not response.knowledge_evidence
                )
        report["modes"][mode] = {
            "recall_at_5": round(recall_hits / answerable, 4) if answerable else None,
            "recall_hits": recall_hits,
            "answerable_cases": answerable,
            "evidence_support_rate": round(supported_citations / citation_count, 4)
            if citation_count else None,
            "supported_citations": supported_citations,
            "retrieved_citations": citation_count,
            "gold_claim_coverage": round(covered_claims / answerable, 4)
            if answerable else None,
            "covered_claims": covered_claims,
            "all_recommendations_gold_supported_rate": round(
                fully_supported_answers / answerable, 4
            ) if answerable else None,
            "fully_supported_answers": fully_supported_answers,
            "product_first_recommendation_gold_hit_rate": round(
                product_first_gold_hits / product_cases, 4
            ) if product_cases else None,
            "product_first_recommendation_gold_hits": product_first_gold_hits,
            "product_first_recommendation_gold_hit_denominator": product_cases,
            "product_all_recommendations_unique_gold_supported_rate": round(
                product_all_unique_gold_supported / unique_gold_product_cases, 4
            ) if unique_gold_product_cases else None,
            "product_all_recommendations_unique_gold_supported_cases": (
                product_all_unique_gold_supported
            ),
            "product_all_recommendations_unique_gold_supported_denominator": (
                unique_gold_product_cases
            ),
            "product_average_recommendation_count": round(
                product_recommendation_count / product_cases, 4
            ) if product_cases else None,
            "product_recommendation_count": product_recommendation_count,
            "product_average_recommendation_count_denominator": product_cases,
            "faq_gold_citation_rate": round(
                faq_gold_citation_hits / faq_cases, 4
            ) if faq_cases else None,
            "faq_gold_citation_hits": faq_gold_citation_hits,
            "faq_gold_citation_denominator": faq_cases,
            "no_answer_safe_rate": round(safe_no_answers / unanswerable, 4)
            if unanswerable else None,
            "safe_no_answers": safe_no_answers,
            "unanswerable_cases": unanswerable,
            "forbidden_source_case_count": forbidden_cases,
            "search_latency_p50_ms": _percentile(search_latencies, 0.5),
            "search_latency_p95_ms": _percentile(search_latencies, 0.95),
            "answer_latency_p50_ms": _percentile(answer_latencies, 0.5),
            "embedding_call_count": getattr(service.embedding, "calls", 0) - embedding_before,
            "model_call_count": getattr(service.composer, "calls", 0) - model_before,
            "retrieval_call_count": (
                service.retriever.calls + service.faq_retriever.calls - retrieval_before
            ),
            "fallback_count": fallback_count,
            "external_api_cost_usd": 0.0 if service.embedding.name == "hash-surrogate" else None,
            "compute_cost": "not measured",
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a shopping service on synthetic questions only"
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="JSONL file containing synthetic evaluation questions",
    )
    parser.add_argument(
        "--multi-labels",
        type=Path,
        default=None,
        help="Optional complete eligible-product-set labels (defaults to bundled labels)",
    )
    args = parser.parse_args()

    from shopping_agent.workflow import create_service
    from shopping_agent.evaluation_multi import (
        DEFAULT_MULTI_LABELS_PATH, evaluate_multi_product_labels,
    )

    service = create_service()
    result = asyncio.run(evaluate(service, args.cases))
    result["retrieval_comparison"] = asyncio.run(evaluate_retrieval_modes(service))
    result["multi_product_comparison"] = asyncio.run(
        evaluate_multi_product_labels(service, args.multi_labels or DEFAULT_MULTI_LABELS_PATH)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
