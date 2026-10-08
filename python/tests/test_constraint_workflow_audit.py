"""Independent regression for excluded wearing styles in the product route."""

import asyncio
import json

import pytest

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


@pytest.mark.parametrize("query", ["推荐不要入耳式的耳机", "推荐非入耳式耳机"])
def test_excluded_in_ear_style_keeps_open_ear_candidate(tmp_path, query):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = tmp_path / "styles.jsonl"
    rows = [
        ("OPEN", "合成开放式耳机", "开放式耳机，不堵耳道。"),
        ("IN", "合成入耳式耳机", "入耳式耳机，深入耳道。"),
    ]
    catalog.write_text("\n".join(json.dumps({
        "kind": "product", "product_id": product_id, "name": name,
        "category": "耳机", "price": 100, "stock": 2,
        "description": description, "tags": [], "reviews": [],
        "data_origin": "synthetic_demo",
    }, ensure_ascii=False) for product_id, name, description in rows), encoding="utf-8")
    service.store.import_documents_jsonl(catalog)

    response = asyncio.run(service.recommend(ShopRequest(
        user_id="audit", query=query, num_items=5,
    )))
    assert [item.product_id for item in response.recommendations] == ["OPEN"]


@pytest.mark.parametrize("query", [
    "推荐不需要手写笔的平板", "推荐手写笔可有可无的平板",
])
def test_optional_stylus_does_not_exclude_either_product(tmp_path, query):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = tmp_path / "optional.jsonl"
    rows = [
        ("PEN", "合成平板甲", "支持手写笔。"),
        ("PLAIN", "合成平板乙", "适合阅读。"),
    ]
    catalog.write_text("\n".join(json.dumps({
        "kind": "product", "product_id": product_id, "name": name,
        "category": "平板", "price": 100, "stock": 2,
        "description": description, "tags": [], "reviews": [],
        "data_origin": "synthetic_demo",
    }, ensure_ascii=False) for product_id, name, description in rows), encoding="utf-8")
    service.store.import_documents_jsonl(catalog)

    response = asyncio.run(service.recommend(ShopRequest(
        user_id="audit", query=query, num_items=5,
    )))
    assert {item.product_id for item in response.recommendations} == {"PEN", "PLAIN"}


@pytest.mark.parametrize("query", [
    "推荐不需要 65W 充电的手机", "推荐 65W 充电可有可无的手机",
])
def test_optional_wattage_does_not_exclude_matching_or_other_wattage(tmp_path, query):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = tmp_path / "wattage.jsonl"
    rows = [
        ("FAST", "支持 65W 充电。"),
        ("SLOW", "支持 45W 充电。"),
    ]
    catalog.write_text("\n".join(json.dumps({
        "kind": "product", "product_id": product_id,
        "name": f"合成手机{product_id}", "category": "手机",
        "price": 100, "stock": 2, "description": description,
        "tags": [], "reviews": [], "data_origin": "synthetic_demo",
    }, ensure_ascii=False) for product_id, description in rows), encoding="utf-8")
    service.store.import_documents_jsonl(catalog)

    response = asyncio.run(service.recommend(ShopRequest(
        user_id="audit", query=query, num_items=5,
    )))
    assert {item.product_id for item in response.recommendations} == {"FAST", "SLOW"}
