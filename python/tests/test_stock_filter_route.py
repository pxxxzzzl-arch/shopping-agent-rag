"""Positive stock constraints select products without invoking FAQ retrieval."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _ask(service, query: str):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="stock-filter-route", query=query, num_items=3,
    )))


@pytest.mark.parametrize("stock_condition", ["库存大于零", "库存 > 0"])
def test_positive_stock_filter_runs_product_flow_only(stock_condition: str) -> None:
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = (
        Path(__file__).resolve().parents[1]
        / "shopping_agent" / "data" / "transfer_products.jsonl"
    )
    service.store.import_documents_jsonl(catalog)

    response = _ask(
        service, f"我有显示器支架，挑支持 VESA 安装且{stock_condition}的显示器。"
    )

    assert response.route == "product"
    assert response.recommendations
    assert all(item.stock > 0 for item in response.recommendations)
    assert any(tool.startswith("product_retrieval:") for tool in response.tool_trace)
    assert not any(tool.startswith("faq_retrieval:") for tool in response.tool_trace)
    assert response.knowledge_evidence == []


@pytest.mark.parametrize(
    "query, expected_route",
    [
        ("显示器库存是否实时？", "faq"),
        ("缺货商品能否推荐购买？", "faq"),
        ("挑库存大于零的显示器，并说明库存是否实时", "mixed"),
        ("挑库存大于零的显示器，并说明缺货规则", "mixed"),
        ("挑库存大于零的显示器，并说明退货政策", "mixed"),
    ],
)
def test_stock_policy_keeps_faq_route(query: str, expected_route: str) -> None:
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))

    response = _ask(service, query)

    assert response.route == expected_route
    assert any(tool.startswith("faq_retrieval:") for tool in response.tool_trace)
    assert (any(tool.startswith("product_retrieval:") for tool in response.tool_trace)) == (
        expected_route == "mixed"
    )
