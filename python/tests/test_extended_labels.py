"""Guard the independent, synthetic retrieval labels against accidental drift."""

from __future__ import annotations

import json
from pathlib import Path


DATA = Path(__file__).resolve().parents[1] / "shopping_agent" / "data"
LABELS = DATA / "retrieval_labels.jsonl"
EVALUATION = DATA / "eval_cases.jsonl"
PRODUCTS = DATA / "demo_products.jsonl"
FAQ = DATA / "demo_faq.jsonl"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_independent_labels_are_complete_and_disjoint_from_existing_cases() -> None:
    rows = _rows(LABELS)
    old_queries = {row["query"] for row in _rows(EVALUATION)}

    assert len(rows) >= 20
    assert len({row["case_id"] for row in rows}) == len(rows)
    assert len({row["query"] for row in rows}) == len(rows)
    assert not old_queries.intersection(row["query"] for row in rows)
    assert {row["topic"] for row in rows} == {
        "product", "faq", "negation", "unanswerable"
    }

    expected_fields = {
        "case_id", "query", "topic", "relevant_source_ids",
        "forbidden_source_ids", "answerability", "support_claim", "synthetic",
    }
    for row in rows:
        assert set(row) == expected_fields
        assert row["synthetic"] is True
        assert isinstance(row["query"], str) and row["query"].strip()
        relevant = row["relevant_source_ids"]
        forbidden = row["forbidden_source_ids"]
        assert isinstance(relevant, list) and len(relevant) == len(set(relevant))
        assert isinstance(forbidden, list) and len(forbidden) == len(set(forbidden))
        assert not set(relevant).intersection(forbidden)
        if row["answerability"] == "answerable":
            assert relevant
            assert isinstance(row["support_claim"], str) and row["support_claim"].strip()
        else:
            assert row["answerability"] == "unanswerable"
            assert not relevant and row["support_claim"] is None


def test_labeled_product_sources_exist_in_synthetic_catalog() -> None:
    products = {row["product_id"] for row in _rows(PRODUCTS)}
    faqs = {row["faq_id"] for row in _rows(FAQ)}
    labels = _rows(LABELS)
    source_ids = {
        source_id
        for row in labels
        for source_id in row["relevant_source_ids"] + row["forbidden_source_ids"]
    }
    for source_id in source_ids:
        if source_id.startswith("DEMO-FAQ-"):
            assert source_id.endswith(":answer")
            assert source_id.removesuffix(":answer") in faqs
            continue
        product_id, suffix = source_id.split(":", maxsplit=1)
        assert product_id in products
        assert suffix == "description"
