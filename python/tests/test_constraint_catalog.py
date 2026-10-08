"""Frozen synthetic answer sets also require live, per-condition evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from shopping_agent.answer import AnswerComposer
from shopping_agent.config import Settings
from shopping_agent.constraints import judgments_are_current, parse_conditions
from shopping_agent.schemas import ShopRequest
from shopping_agent.storage import CatalogStore
from shopping_agent.workflow import ShoppingService


DATA = Path(__file__).resolve().parents[1] / "shopping_agent" / "data"


@pytest.mark.parametrize("mode", ["bm25", "vector", "hybrid"])
def test_frozen_catalog_recommendations_prove_all_explicit_conditions(mode):
    settings = Settings(database_url="sqlite://", seed_demo=False)
    store = CatalogStore(settings.database_url)
    store.initialize()
    store.import_documents_jsonl(DATA / "constraint_v1_catalog.jsonl")
    service = ShoppingService(store, AnswerComposer(settings), settings)
    rows = [json.loads(line) for line in (
        DATA / "constraint_v1_labels.jsonl"
    ).read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 18
    for row in rows:
        request = ShopRequest(
            user_id="frozen-constraint-audit", query=row["query"],
            num_items=row["num_items"], retrieval_mode=mode,
        )
        response = asyncio.run(service.recommend(request))
        returned = {item.product_id for item in response.recommendations}
        eligible = set(row["eligible_product_ids"])
        assert returned <= eligible, row["case_id"]
        assert len(returned) == min(request.num_items, len(eligible)), row["case_id"]
        for recommendation in response.recommendations:
            parsed = parse_conditions(
                request.query, category=recommendation.category,
                max_price=request.max_price,
            )
            observed = recommendation.condition_judgments
            assert {(j.field, j.operator, j.expected) for j in observed} >= {
                (j.field, j.operator, j.expected) for j in parsed
            }, row["case_id"]
            assert judgments_are_current(observed, store), row["case_id"]
            assert all(
                ref.source_type == "catalog_snapshot"
                or store.get_document(ref.source_id) is not None
                for judgment in observed for ref in judgment.evidence
            ), row["case_id"]
