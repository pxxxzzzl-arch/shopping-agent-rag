"""Inventory availability is a catalog constraint, not a service-policy question."""

from __future__ import annotations

import asyncio

import pytest

from shopping_agent.agent_roles import PlanningAgent, PlanningInput
from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _route(query: str) -> str:
    task = PlanningInput(
        request=ShopRequest(user_id="inventory-route", query=query),
        catalog_categories=("耳机", "显示器", "手机"),
    )
    return PlanningAgent().plan(task).route


@pytest.mark.parametrize(
    "query",
    [
        "想要4K画面还得至少120Hz，目前有库存的显示器里存在这样的组合吗？",
        "现在有库存的耳机有哪些？",
        "这款手机库存还有多少？",
    ],
)
def test_product_inventory_lookup_uses_catalog_route(query: str) -> None:
    assert _route(query) == "product"


@pytest.mark.parametrize(
    "query",
    [
        "演示商品的库存是实时的吗？",
        "库存归零后还会推荐商品吗？",
        "缺货商品能否推荐购买？",
    ],
)
def test_inventory_policy_uses_faq_route(query: str) -> None:
    assert _route(query) == "faq"


def test_recommendation_with_inventory_policy_uses_both_routes() -> None:
    assert _route("推荐有货耳机，并说明库存是否实时") == "mixed"
    assert _route("有库存的耳机有哪些，并说明退货政策") == "mixed"


def test_unsupported_inventory_combination_runs_product_flow() -> None:
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    response = asyncio.run(service.recommend(ShopRequest(
        user_id="inventory-route",
        query="想要4K画面还得至少120Hz，目前有库存的显示器里存在这样的组合吗？",
        num_items=3,
    )))

    assert response.route == "product"
    assert response.recommendations == []
    assert response.knowledge_evidence == []
    assert "catalog" in response.tool_trace
    assert not any(tool.startswith("faq_retrieval:") for tool in response.tool_trace)
