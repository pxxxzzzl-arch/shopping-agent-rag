"""Offline evaluation with complete, independently labeled product answer sets.

The labels are used only here. A matching source ID is a weak evidence proxy;
it does not automatically prove that every sentence of the reply is true.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .schemas import ShopRequest


DEFAULT_MULTI_LABELS_PATH = Path(__file__).parent / "data" / "multi_product_labels.jsonl"

_FAILURE_TRACE_MARKERS = {
    "failure_fallback", "product_agent_failure", "faq_retrieval_failure",
}
_FAILURE_WARNING_MARKERS = (
    "工具流程失败", "商品工具失败", "商品检索失败", "FAQ 检索失败",
    "FAQ 来源核验失败", "FAQ 最终来源核验失败", "索引刷新失败",
)


def _reported_execution_failures(response: Any) -> list[str]:
    """Recognize failures that the service converted into a normal response."""
    trace = getattr(response, "tool_trace", None) or []
    warnings = getattr(response, "warnings", None) or []
    markers = [str(entry) for entry in trace if entry in _FAILURE_TRACE_MARKERS]
    markers.extend(
        marker for marker in _FAILURE_WARNING_MARKERS
        if any(marker in str(warning) for warning in warnings)
    )
    return sorted(set(markers))


def _catalog_snapshot_sha256(service: Any) -> str | None:
    list_documents = getattr(service.store, "list_documents", None)
    if not callable(list_documents):
        return None
    documents = list_documents()
    serialized = json.dumps(
        [
            {
                "source_id": source.source_id,
                "product_id": source.product_id,
                "version": source.version,
                "text": source.text,
            }
            for source in sorted(documents, key=lambda source: source.source_id)
        ],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _implementation_snapshot_sha256() -> str:
    """Identify the package implementation used for this evaluation run."""
    package_dir = Path(__file__).parent
    digests = [
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(package_dir.glob("*.py"))
    ]
    payload = json.dumps(digests, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_multi_product_cases(path: str | Path = DEFAULT_MULTI_LABELS_PATH) -> list[dict[str, Any]]:
    """Validate a reviewed complete eligible-product set per question."""
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    required = {
        "case_id", "query", "num_items", "eligible_product_ids",
        "gold_sources_by_product", "synthetic", "label_rationale",
    }
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError(f"Line {line_number} must contain exactly the multi-product label fields")
        case_id = row["case_id"]
        query = row["query"]
        k = row["num_items"]
        gold = row["eligible_product_ids"]
        sources = row["gold_sources_by_product"]
        if (
            row["synthetic"] is not True
            or not isinstance(case_id, str) or not case_id.strip()
            or not isinstance(query, str) or not query.strip()
            or case_id in seen_ids or query in seen_queries
            or isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 10
            or not isinstance(gold, list)
            or any(not isinstance(product_id, str) or not product_id for product_id in gold)
            or len(gold) != len(set(gold))
            or not isinstance(sources, dict) or set(sources) != set(gold)
            or not isinstance(row["label_rationale"], str)
            or not row["label_rationale"].strip()
        ):
            raise ValueError(f"Invalid multi-product label on line {line_number}")
        if any(
            not isinstance(source_ids, list) or not source_ids
            or len(source_ids) != len(set(source_ids))
            or any(
                not isinstance(source_id, str)
                or not source_id.startswith(product_id + ":")
                for source_id in source_ids
            )
            for product_id, source_ids in sources.items()
        ):
            raise ValueError(f"Invalid gold source map on line {line_number}")
        cases.append(row)
        seen_ids.add(case_id)
        seen_queries.add(query)
    if not cases:
        raise ValueError("Multi-product label file is empty")
    return cases


async def evaluate_multi_product_labels(
    service: Any,
    labels_path: str | Path = DEFAULT_MULTI_LABELS_PATH,
) -> dict[str, Any]:
    """Measure product-set correctness and current gold-ID citation separately."""
    labels_path = Path(labels_path)
    cases = load_multi_product_cases(labels_path)
    answerable = [row for row in cases if row["eligible_product_ids"]]
    multi_gold = sum(len(row["eligible_product_ids"]) >= 2 for row in cases)
    unanswerable = len(cases) - len(answerable)
    report: dict[str, Any] = {
        "dataset": "synthetic_multi_product_answer_sets",
        "label_count": len(cases),
        "label_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "catalog_snapshot_sha256": _catalog_snapshot_sha256(service),
        "implementation_snapshot_sha256": _implementation_snapshot_sha256(),
        "embedding_provider": getattr(getattr(service, "embedding", None), "name", None),
        "answerable_cases": len(answerable),
        "multi_gold_cases": multi_gold,
        "unanswerable_cases": unanswerable,
        "mode_key_semantics": "Each modes key is the requested retrieval mode, not necessarily the executed product retrieval mode.",
        "metric_definitions": {
            "item_micro_precision": "unique eligible returned IDs in the first k / all returned items; duplicate IDs count as incorrect",
            "answerable_macro_precision": "mean per-answerable-question precision; empty answers score zero",
            "full_gold_recall_at_k": "unique eligible IDs in first k / all eligible IDs across answerable questions",
            "capacity_recall_at_k": "unique eligible IDs in first k / sum(min(k, number of eligible IDs)) across answerable questions",
            "exact_set_at_k_rate": "questions with exactly min(k, eligible count) unique eligible items and no extras; empty-gold questions require no items or FAQ citations",
            "answerable_exact_set_at_k_rate": "answerable questions with exactly min(k, eligible count) unique eligible items and no extras / answerable questions",
            "gold_source_id_attachment_rate": "eligible returned items in first k citing a current labeled source belonging to that product / eligible returned items in first k; source-ID match is not semantic claim verification",
            "exact_set_with_gold_citation_rate": "answerable exact-set questions whose every returned item cites a current labeled source / answerable questions; still requires human answer-text review",
            "unanswerable_abstention_rate": "empty-gold questions with neither product recommendations nor FAQ citations / empty-gold questions",
            "actual_product_retrieval_modes": "counts of response.effective_retrieval_modes['product'] by executed mode; missing means no product mode was reported",
            "actual_product_retrieval_mode_mismatch_count": "responses whose reported executed product mode differs from the requested mode; missing modes are counted separately",
            "error_count": "cases with a raised exception, a reported tool execution failure, or a gold-source lookup exception; service fallback responses are failures",
        },
        "modes": {},
    }
    for mode in ("bm25", "vector", "hybrid"):
        returned_total = 0
        correct_total = 0
        all_gold_total = 0
        capacity_total = 0
        macro_precision_sum = 0.0
        exact_cases = 0
        answerable_exact_cases = 0
        exact_multi_cases = 0
        gold_cited_items = 0
        exact_with_citation = 0
        safe_empty = 0
        errors: list[dict[str, str]] = []
        actual_product_modes: dict[str, int] = {}
        actual_product_mode_missing = 0
        actual_product_mode_mismatches = 0
        for index, row in enumerate(cases):
            gold = set(row["eligible_product_ids"])
            k = row["num_items"]
            all_gold_total += len(gold)
            capacity_total += min(k, len(gold))
            request = ShopRequest(
                user_id=f"multi_gold_eval_{mode}_{index:03d}",
                query=row["query"], num_items=k, retrieval_mode=mode,
            )
            failure_types: list[str] = []
            failure_details: list[str] = []
            try:
                response = await service.recommend(request)
                items = list(response.recommendations)
                faq_citations = list(response.knowledge_evidence)
                effective_modes = getattr(response, "effective_retrieval_modes", None)
                actual_product_mode = (
                    effective_modes.get("product") if isinstance(effective_modes, Mapping)
                    else None
                )
                if isinstance(actual_product_mode, str) and actual_product_mode:
                    actual_product_modes[actual_product_mode] = (
                        actual_product_modes.get(actual_product_mode, 0) + 1
                    )
                    actual_product_mode_mismatches += actual_product_mode != mode
                else:
                    actual_product_mode_missing += 1
                reported_failures = _reported_execution_failures(response)
                if reported_failures:
                    failure_types.append("ReportedExecutionFailure")
                    failure_details.extend(reported_failures)
            except Exception as exc:
                items = []
                faq_citations = []
                actual_product_mode_missing += 1
                failure_types.append(type(exc).__name__)
                failure_details.append("recommend")
            returned_total += len(items)
            seen: set[str] = set()
            correct_items = []
            for item in items[:k]:
                product_id = item.product_id
                if product_id in gold and product_id not in seen:
                    correct_items.append(item)
                seen.add(product_id)
            correct_total += len(correct_items)
            if gold:
                macro_precision_sum += len(correct_items) / len(items) if items else 0.0

            cited_this_case = 0
            for item in correct_items:
                product_id = item.product_id
                expected_sources = set(row["gold_sources_by_product"][product_id])
                cited_source_ids = {ref.source_id for ref in item.evidence}
                cited_current_source = False
                for source_id in cited_source_ids & expected_sources:
                    try:
                        source = service.store.get_document(source_id)
                    except Exception as exc:
                        failure_types.append(type(exc).__name__)
                        failure_details.append("gold_source_lookup")
                        break
                    cited_current_source |= bool(
                        source is not None and source.product_id == product_id
                    )
                if "gold_source_lookup" in failure_details:
                    break
                if cited_current_source:
                    cited_this_case += 1
            failed = bool(failure_types)
            if not failed:
                gold_cited_items += cited_this_case
            else:
                errors.append({
                    "case_id": row["case_id"],
                    "error_type": "|".join(sorted(set(failure_types))),
                    "detail": ",".join(sorted(set(failure_details))),
                })

            expected_count = min(k, len(gold))
            returned_ids = [item.product_id for item in items]
            exact = bool(
                not failed
                and len(items) == expected_count
                and len(set(returned_ids)) == len(returned_ids)
                and set(returned_ids) <= gold
                and (bool(gold) or not faq_citations)
            )
            exact_cases += exact
            if gold:
                answerable_exact_cases += exact
            if len(gold) >= 2:
                exact_multi_cases += exact
            exact_with_citation += bool(gold and exact and cited_this_case == len(items))
            if not gold:
                safe_empty += bool(not items and not faq_citations and not failed)

        def rate(numerator: int | float, denominator: int) -> float | None:
            return round(numerator / denominator, 4) if denominator else None

        report["modes"][mode] = {
            "requested_retrieval_mode": mode,
            "actual_product_retrieval_modes": dict(sorted(actual_product_modes.items())),
            "actual_product_retrieval_mode_missing_count": actual_product_mode_missing,
            "actual_product_retrieval_mode_mismatch_count": actual_product_mode_mismatches,
            "item_micro_precision": rate(correct_total, returned_total),
            "correct_returned_items": correct_total,
            "returned_items": returned_total,
            "answerable_macro_precision": rate(macro_precision_sum, len(answerable)),
            "full_gold_recall_at_k": rate(correct_total, all_gold_total),
            "eligible_product_count": all_gold_total,
            "capacity_recall_at_k": rate(correct_total, capacity_total),
            "capacity_at_k": capacity_total,
            "exact_set_at_k_rate": rate(exact_cases, len(cases)),
            "exact_set_cases": exact_cases,
            "answerable_exact_set_at_k_rate": rate(answerable_exact_cases, len(answerable)),
            "answerable_exact_set_cases": answerable_exact_cases,
            "multi_gold_exact_set_at_k_rate": rate(exact_multi_cases, multi_gold),
            "multi_gold_exact_set_cases": exact_multi_cases,
            "gold_source_id_attachment_rate": rate(gold_cited_items, correct_total),
            "gold_source_id_attached_items": gold_cited_items,
            "exact_set_with_gold_citation_rate": rate(exact_with_citation, len(answerable)),
            "exact_set_with_gold_citation_cases": exact_with_citation,
            "unanswerable_abstention_rate": rate(safe_empty, unanswerable),
            "safe_unanswerable_cases": safe_empty,
            "error_count": len(errors),
            "errors": errors,
        }
    return report
