"""Product-set metrics must expose coverage, duplicates, and citation gaps."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from shopping_agent.evaluation_multi import (
    evaluate_multi_product_labels, load_multi_product_cases,
)


def _item(product_id: str, *sources: str):
    return SimpleNamespace(
        product_id=product_id,
        evidence=[SimpleNamespace(source_id=source_id) for source_id in sources],
    )


def _label(case_id: str, k: int, eligible: list[str]):
    return {
        "case_id": case_id,
        "query": f"评测问题 {case_id}",
        "num_items": k,
        "eligible_product_ids": eligible,
        "gold_sources_by_product": {
            product_id: [f"{product_id}:description"] for product_id in eligible
        },
        "synthetic": True,
        "label_rationale": "依据当前合成商品描述。",
    }


def _evaluate(tmp_path, labels, responses, *, errors=(), source_error=False):
    path = tmp_path / "multi-product.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in labels) + "\n",
        encoding="utf-8",
    )

    def get_document(source_id):
        if source_error:
            raise RuntimeError("simulated source lookup failure")
        return SimpleNamespace(product_id=source_id.split(":", 1)[0])

    class Service:
        store = SimpleNamespace(get_document=get_document)

        async def recommend(self, request):
            if request.query in errors:
                raise RuntimeError("simulated failure")
            return responses[request.query]

    return asyncio.run(evaluate_multi_product_labels(Service(), path))


def test_multi_gold_metrics_separate_precision_coverage_and_citations(tmp_path):
    labels = [
        _label("A", 3, ["A1", "A2"]),
        _label("B", 1, ["B1", "B2"]),
        _label("C", 2, ["C1"]),
        _label("D", 2, []),
    ]
    responses = {
        labels[0]["query"]: SimpleNamespace(
            recommendations=[_item("A1", "A1:description"), _item("A2", "A2:description")],
            knowledge_evidence=[],
        ),
        labels[1]["query"]: SimpleNamespace(
            recommendations=[_item("B1", "A1:description")], knowledge_evidence=[],
        ),
        labels[2]["query"]: SimpleNamespace(
            recommendations=[_item("WRONG")], knowledge_evidence=[],
        ),
        labels[3]["query"]: SimpleNamespace(recommendations=[], knowledge_evidence=[]),
    }
    report = _evaluate(tmp_path, labels, responses)
    assert (report["answerable_cases"], report["multi_gold_cases"], report["unanswerable_cases"]) == (3, 2, 1)
    for result in report["modes"].values():
        assert result["item_micro_precision"] == 0.75
        assert result["answerable_macro_precision"] == 0.6667
        assert result["full_gold_recall_at_k"] == 0.6
        assert result["capacity_recall_at_k"] == 0.75
        assert result["exact_set_at_k_rate"] == 0.75
        assert result["answerable_exact_set_at_k_rate"] == 0.6667
        assert result["multi_gold_exact_set_at_k_rate"] == 1.0
        assert result["gold_source_id_attachment_rate"] == 0.6667
        assert result["exact_set_with_gold_citation_rate"] == 0.3333
        assert result["unanswerable_abstention_rate"] == 1.0


def test_duplicate_items_and_failed_empty_answers_never_count_as_success(tmp_path):
    labels = [_label("A", 2, ["A1", "A2"]), _label("B", 2, [])]
    responses = {
        labels[0]["query"]: SimpleNamespace(
            recommendations=[_item("A1", "A1:description"), _item("A1", "A1:description")],
            knowledge_evidence=[],
        ),
    }
    report = _evaluate(tmp_path, labels, responses, errors={labels[1]["query"]})
    for result in report["modes"].values():
        assert result["item_micro_precision"] == 0.5
        assert result["capacity_recall_at_k"] == 0.5
        assert result["exact_set_cases"] == 0
        assert result["safe_unanswerable_cases"] == 0
        assert result["error_count"] == 1


def test_bundled_multi_product_labels_cover_multiple_and_empty_sets():
    cases = load_multi_product_cases()
    assert len(cases) >= 20
    assert sum(len(row["eligible_product_ids"]) >= 2 for row in cases) >= 8
    assert sum(not row["eligible_product_ids"] for row in cases) >= 4


def test_empty_gold_with_faq_citation_is_not_structural_abstention(tmp_path):
    label = _label("NO-ANSWER", 2, [])
    response = SimpleNamespace(
        recommendations=[],
        knowledge_evidence=[SimpleNamespace(source_id="UNRELATED:answer")],
    )
    report = _evaluate(tmp_path, [label], {label["query"]: response})
    for result in report["modes"].values():
        assert result["exact_set_cases"] == 0
        assert result["safe_unanswerable_cases"] == 0
        assert result["exact_set_with_gold_citation_rate"] is None


def test_swallowed_tool_failure_empty_response_is_not_successful_abstention(tmp_path):
    labels = [_label(case_id, 2, []) for case_id in ("FALLBACK", "AGENT", "WARNING")]
    responses = {
        labels[0]["query"]: SimpleNamespace(
            recommendations=[], knowledge_evidence=[],
            tool_trace=["planner", "failure_fallback"], warnings=[],
            effective_retrieval_modes={},
        ),
        labels[1]["query"]: SimpleNamespace(
            recommendations=[], knowledge_evidence=[],
            tool_trace=["planner", "product_agent_failure"], warnings=[],
            effective_retrieval_modes={},
        ),
        labels[2]["query"]: SimpleNamespace(
            recommendations=[], knowledge_evidence=[], tool_trace=[],
            warnings=["商品工具失败：TimeoutError"],
            effective_retrieval_modes={},
        ),
    }
    report = _evaluate(tmp_path, labels, responses)
    for result in report["modes"].values():
        assert result["error_count"] == 3
        assert all(error["error_type"] == "ReportedExecutionFailure" for error in result["errors"])
        assert result["actual_product_retrieval_mode_missing_count"] == 3
        assert result["safe_unanswerable_cases"] == 0
        assert result["exact_set_cases"] == 0


def test_report_distinguishes_requested_and_executed_product_modes(tmp_path):
    label = _label("A", 2, ["A1"])
    response = SimpleNamespace(
        recommendations=[_item("A1", "A1:description")], knowledge_evidence=[],
        tool_trace=["product_retrieval:bm25"], warnings=[],
        effective_retrieval_modes={"product": "bm25"},
    )
    report = _evaluate(tmp_path, [label], {label["query"]: response})
    assert "requested" in report["mode_key_semantics"]
    for requested_mode, result in report["modes"].items():
        assert result["requested_retrieval_mode"] == requested_mode
        assert result["actual_product_retrieval_modes"] == {"bm25": 1}
        assert result["actual_product_retrieval_mode_missing_count"] == 0
        assert result["actual_product_retrieval_mode_mismatch_count"] == (requested_mode != "bm25")


def test_gold_source_lookup_error_is_counted_without_aborting_report(tmp_path):
    label = _label("A", 2, ["A1"])
    response = SimpleNamespace(
        recommendations=[_item("A1", "A1:description")], knowledge_evidence=[],
    )
    report = _evaluate(
        tmp_path, [label], {label["query"]: response}, source_error=True,
    )
    for result in report["modes"].values():
        assert result["error_count"] == 1
        assert result["errors"][0]["detail"] == "gold_source_lookup"
        assert result["gold_source_id_attached_items"] == 0
        assert result["exact_set_cases"] == 0
