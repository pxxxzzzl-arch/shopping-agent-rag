"""End-to-end checks of the new shopping workflow with no paid model calls."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from shopping_agent.app import create_app
from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _service():
    return create_service(Settings(database_url="sqlite://", seed_demo=True))


def test_api_recommends_grounded_in_stock_products_with_budget():
    app = create_app(service=_service())
    with TestClient(app) as client:
        assert client.get("/health").json()["catalog_products"] == 30
        result = client.post(
            "/api/v1/shop/recommend",
            json={
                "user_id": "test-user",
                "query": "地铁通勤的降噪耳机，预算500元",
                "num_items": 3,
            },
        )
    assert result.status_code == 200
    body = result.json()
    assert 1 <= len(body["recommendations"]) <= 3
    assert body["retrieval_used"] is True
    assert all(item["category"] == "耳机" for item in body["recommendations"])
    assert all(item["price"] <= 500 and item["stock"] > 0 for item in body["recommendations"])
    assert all(item["evidence"] for item in body["recommendations"])
    assert all("DEMO-H05" != item["product_id"] for item in body["recommendations"])


def test_api_records_behavior_and_validates_requested_count():
    service = _service()
    app = create_app(service=service)
    with TestClient(app) as client:
        event = client.post(
            "/api/v1/events",
            json={"user_id": "buyer", "product_id": "DEMO-H02", "event_type": "click"},
        )
        invalid = client.post(
            "/api/v1/shop/recommend",
            json={"user_id": "buyer", "query": "耳机", "num_items": 0},
        )
        blank_query = client.post(
            "/api/v1/shop/recommend",
            json={"user_id": "buyer", "query": "   "},
        )
        blank_user = client.post(
            "/api/v1/events",
            json={"user_id": "  ", "product_id": "DEMO-H02", "event_type": "click"},
        )
    assert event.status_code == 201
    assert service.store.get_user_category_counts("buyer") == {"耳机": 1}
    assert invalid.status_code == 422
    assert blank_query.status_code == 422
    assert blank_user.status_code == 422


def test_last_category_in_compound_phrase_is_requested_item():
    response = asyncio.run(
        _service().recommend(ShopRequest(user_id="buyer", query="手机充电配件", num_items=2))
    )
    assert response.recommendations
    assert all(item.category == "配件" for item in response.recommendations)


def test_compatibility_phrase_does_not_replace_requested_category():
    response = asyncio.run(
        _service().recommend(ShopRequest(user_id="buyer", query="想买耳机给手机用", num_items=2))
    )
    assert response.recommendations
    assert all(item.category == "耳机" for item in response.recommendations)


def test_negated_category_is_not_selected():
    service = _service()
    for query in ("推荐耳机，不要手机", "不要手机，推荐耳机"):
        response = asyncio.run(
            service.recommend(ShopRequest(user_id="buyer", query=query, num_items=3))
        )
        assert response.recommendations
        assert all(item.category == "耳机" for item in response.recommendations)


def test_explicit_noise_cancellation_constraint_is_respected():
    service = _service()
    wants_noise_cancellation = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="推荐主动降噪耳机", num_items=5))
    )
    avoids_noise_cancellation = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="不要主动降噪的耳机", num_items=5))
    )
    assert wants_noise_cancellation.recommendations
    assert avoids_noise_cancellation.recommendations
    for item in wants_noise_cancellation.recommendations:
        assert any("降噪" in tag for tag in service.store.get_product(item.product_id).tags)
    for item in avoids_noise_cancellation.recommendations:
        assert all("降噪" not in tag for tag in service.store.get_product(item.product_id).tags)


def test_unverified_waterproof_claim_does_not_return_products():
    response = asyncio.run(
        _service().recommend(
            ShopRequest(user_id="buyer", query="我要防水手机，能水下拍照", num_items=5)
        )
    )
    assert response.recommendations == []
    assert response.retrieval_used is False


def test_multiple_budget_phrases_use_strictest_cap():
    response = asyncio.run(
        _service().recommend(
            ShopRequest(
                user_id="buyer",
                query="想买耳机，预算1000元，但必须500元以内",
                num_items=10,
            )
        )
    )
    assert response.recommendations
    assert all(item.price <= 500 for item in response.recommendations)


def test_low_than_budget_excludes_equal_price():
    response = asyncio.run(
        _service().recommend(
            ShopRequest(user_id="buyer", query="低于299元的耳机", num_items=10)
        )
    )
    assert response.recommendations
    assert all(item.price < 299 for item in response.recommendations)


def test_stock_change_never_falls_back_to_unavailable_products(monkeypatch):
    service = _service()
    original = service.store.get_product

    def newly_sold_out(product_id):
        product = original(product_id)
        if product is not None and product.category == "耳机":
            return replace(product, stock=0)
        return product

    monkeypatch.setattr(service.store, "get_product", newly_sold_out)
    response = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="降噪耳机", num_items=3))
    )
    assert response.recommendations == []
    assert "没有满足条件" in response.answer


def test_category_change_is_rechecked_before_return(monkeypatch):
    service = _service()
    original = service.store.get_product

    def changed_category(product_id):
        product = original(product_id)
        if product is not None and product.category == "耳机":
            return replace(product, category="手机")
        return product

    monkeypatch.setattr(service.store, "get_product", changed_category)
    response = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="降噪耳机", num_items=3))
    )
    assert response.recommendations == []


def test_model_failure_uses_grounded_template(monkeypatch):
    service = _service()

    class FailingModel:
        async def ainvoke(self, _messages):
            raise RuntimeError("simulated model outage")

    monkeypatch.setattr(service.composer, "model", FailingModel())
    response = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="降噪耳机", num_items=2))
    )
    assert response.recommendations
    assert response.llm_used is False
    assert any("模型选择失败" in warning for warning in response.warnings)
    assert any("[DEMO-" in item.reason for item in response.recommendations)


def test_model_cannot_inject_unsupported_price_or_discount(monkeypatch):
    service = _service()

    class FabricatingModel:
        async def ainvoke(self, _messages):
            class Result:
                content = "价格1元，打九折 [DEMO-H02:description]"

            return Result()

    monkeypatch.setattr(service.composer, "model", FabricatingModel())
    response = asyncio.run(
        service.recommend(ShopRequest(user_id="buyer", query="地铁通勤降噪耳机", num_items=2))
    )
    assert response.llm_used is False
    assert "价格1元" not in response.answer
    assert "打九折" not in response.answer


def test_valid_model_selection_renders_only_verified_facts(monkeypatch):
    service = _service()

    class SelectingModel:
        async def ainvoke(self, _messages):
            class Result:
                content = '{"product_id":"DEMO-H02","source_id":"DEMO-H02:description"}'

            return Result()

    monkeypatch.setattr(service.composer, "model", SelectingModel())
    response = asyncio.run(
        service.recommend(
            ShopRequest(user_id="buyer", query="地铁通勤降噪耳机，预算500元", num_items=2)
        )
    )
    assert response.llm_used is True
    assert "云听 Commuter" in response.answer
    assert "¥299" in response.answer
    assert "[DEMO-H02:description]" in response.answer
