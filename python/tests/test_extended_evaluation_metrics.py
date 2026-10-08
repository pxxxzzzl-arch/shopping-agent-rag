"""Keep product and FAQ denominators explicit in the offline source-label report."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from shopping_agent.config import Settings
from shopping_agent.evaluation import evaluate_retrieval_modes
from shopping_agent.workflow import create_service


def _refs(*source_ids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(source_id=source_id) for source_id in source_ids]


def _recommendation(*source_ids: str) -> SimpleNamespace:
    return SimpleNamespace(evidence=_refs(*source_ids))


def test_product_and_faq_metrics_exclude_legacy_empty_faq_vacuity(tmp_path):
    examples = [
        ("product", ["G1"], [_recommendation("G1"), _recommendation("X")], []),
        ("negation", ["G2"], [_recommendation("X"), _recommendation("G2")], []),
        ("product", ["G3"], [_recommendation("G3"), _recommendation("G3")], []),
        ("product", ["G4", "G5"], [], []),
        ("faq", ["F1"], [], _refs("F1")),
        ("faq", ["F2"], [], []),
    ] + [("unanswerable", [], [], []) for _ in range(14)]
    rows = []
    responses = {}
    all_sources = {"G1", "G2", "G3", "G4", "G5", "F1", "F2", "X"}
    for index, (topic, gold, recommendations, knowledge_evidence) in enumerate(examples):
        query = f"评测题 {index}"
        answerable = topic != "unanswerable"
        rows.append({
            "case_id": f"T{index:03d}",
            "query": query,
            "topic": topic,
            "relevant_source_ids": gold,
            "forbidden_source_ids": [],
            "answerability": "answerable" if answerable else "unanswerable",
            "support_claim": query if answerable else None,
            "synthetic": True,
        })
        responses[query] = SimpleNamespace(
            recommendations=recommendations,
            knowledge_evidence=knowledge_evidence,
        )
    labels_path = tmp_path / "retrieval_labels.jsonl"
    labels_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    class Retriever:
        def __init__(self):
            self.calls = 0

        def search(self, query, limit, mode):
            self.calls += 1
            gold = rows[int(query.split()[-1])]["relevant_source_ids"]
            hits = [SimpleNamespace(document=SimpleNamespace(source_id=source_id)) for source_id in gold]
            return SimpleNamespace(hits=hits, fallback_reason=None)

    class Service:
        def __init__(self):
            self.embedding = SimpleNamespace(name="test", calls=0)
            self.composer = SimpleNamespace(calls=0)
            self.retriever = Retriever()
            self.faq_retriever = Retriever()
            self.store = SimpleNamespace(
                get_document=lambda source_id: object() if source_id in all_sources else None
            )

        async def recommend(self, request):
            return responses[request.query]

    report = asyncio.run(evaluate_retrieval_modes(Service(), labels_path))
    assert "six FAQ cases" in report["metric_definitions"]["all_recommendations_gold_supported_rate"]
    for result in report["modes"].values():
        assert result["answerable_cases"] == 6
        # The legacy numerator includes F1 even though F1 returns no products.
        assert result["fully_supported_answers"] == 2
        assert result["all_recommendations_gold_supported_rate"] == 0.3333
        assert result["product_first_recommendation_gold_hits"] == 2
        assert result["product_first_recommendation_gold_hit_denominator"] == 4
        assert result["product_first_recommendation_gold_hit_rate"] == 0.5
        assert result["product_all_recommendations_unique_gold_supported_cases"] == 1
        assert result["product_all_recommendations_unique_gold_supported_denominator"] == 3
        assert result["product_all_recommendations_unique_gold_supported_rate"] == 0.3333
        assert result["product_recommendation_count"] == 6
        assert result["product_average_recommendation_count_denominator"] == 4
        assert result["product_average_recommendation_count"] == 1.5
        assert result["faq_gold_citation_hits"] == 1
        assert result["faq_gold_citation_denominator"] == 2
        assert result["faq_gold_citation_rate"] == 0.5


def test_bundled_label_denominators_include_negation_and_separate_faq():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    report = asyncio.run(evaluate_retrieval_modes(service))
    assert report["label_count"] == 32
    for result in report["modes"].values():
        assert result["product_first_recommendation_gold_hit_denominator"] == 23
        assert result["product_all_recommendations_unique_gold_supported_denominator"] == 23
        assert result["product_average_recommendation_count_denominator"] == 23
        assert result["faq_gold_citation_denominator"] == 6
        assert result["answerable_cases"] == 29
