"""Shopping paraphrases are checked against current catalog evidence."""

from __future__ import annotations

import asyncio
import json

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _service_with_products(tmp_path, *, category, rows):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = [
        {
            "kind": "product",
            "product_id": product_id,
            "name": f"合成{'充电器' if category == '配件' else category} {product_id}",
            "category": category,
            "price": price,
            "stock": stock,
            "description": description,
            "tags": tags,
            "reviews": [],
            "data_origin": "synthetic_demo",
        }
        for product_id, price, stock, description, tags in rows
    ]
    path = tmp_path / "paraphrase-products.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in catalog),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)
    return service


def _ids(service, query, count=4):
    response = asyncio.run(service.recommend(ShopRequest(
        user_id="paraphrase-regression", query=query, num_items=count,
    )))
    return {item.product_id for item in response.recommendations}


def test_ipx_query_needs_a_positive_grade_and_respects_explicit_denials(tmp_path):
    service = _service_with_products(tmp_path, category="耳机", rows=[
        ("RATED", 199, 5, "开放式耳机，标注 IPX5 防护等级。", []),
        ("NO-GRADE", 179, 5, "轻巧耳机，没有降噪与防水等级。", []),
        ("STALE-TAG", 189, 5, "耳机没有防水等级。", ["IPX4"]),
        ("DENIED", 189, 5, "耳机不支持 IPX4 防护等级。", []),
    ])
    assert _ids(service, "找标了 IPX 防护等级、250元以内的耳机") == {"RATED"}


def test_two_socket_paraphrase_and_usb_c_are_both_required(tmp_path):
    service = _service_with_products(tmp_path, category="配件", rows=[
        ("TWO-MIXED", 159, 5, "充电器有 USB-C 与 USB-A 双口。", ["充电器"]),
        ("THREE", 299, 5, "充电器有两个 USB-C 和一个 USB-A 接口。", ["充电器"]),
        ("ONE", 79, 5, "充电器仅有单 USB-C 接口。", ["充电器"]),
        ("NO-C", 99, 5, "充电器有 USB-A 双口。", ["充电器"]),
    ])
    query = "出差要至少两个插口，其中必须有 USB-C，价格最多300元的充电头"
    assert _ids(service, query) == {"TWO-MIXED", "THREE"}


def test_do_not_block_ear_canal_and_anc_are_conjunctive(tmp_path):
    service = _service_with_products(tmp_path, category="耳机", rows=[
        ("OPEN-ANC", 299, 5, "开放式耳机，不堵耳道，支持主动降噪。", []),
        ("IN-EAR-ANC", 299, 5, "入耳式耳机，支持主动降噪。", []),
        ("OPEN-NO-ANC", 229, 5, "开放式耳机，不堵耳道，不提供主动降噪。", []),
    ])
    assert _ids(service, "耳机不要堵耳道，同时必须主动降噪，预算400元内") == {
        "OPEN-ANC"
    }


def test_expand_to_large_screen_requires_stocked_foldable_evidence(tmp_path):
    service = _service_with_products(tmp_path, category="手机", rows=[
        ("FOLD-IN-STOCK", 5999, 5, "7.6 英寸内屏，多任务分屏。", ["折叠屏"]),
        ("BIG-BUT-FLAT", 3999, 5, "6.8 英寸大屏，适合看文档。", ["大屏"]),
        ("FOLD-OUT", 5899, 0, "7.6 英寸内屏，多任务分屏。", ["折叠屏"]),
    ])
    query = "想要能展开成大屏的折叠手机，预算6500元"
    assert _ids(service, query) == {"FOLD-IN-STOCK"}


def test_unavailable_demo_foldable_causes_abstention():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    query = "想要能展开成大屏的折叠手机，预算6500元，给我三款现在能下单的。"
    assert _ids(service, query, count=3) == set()
