"""Explicit shopping requirements must be supported for every returned item."""

from __future__ import annotations

import asyncio
import json

import pytest

from shopping_agent.config import Settings
from shopping_agent.product_requirements import missing_claims, required_claims
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _ask(service, query: str, count: int = 3):
    return asyncio.run(service.recommend(ShopRequest(
        user_id="grounding-test", query=query, num_items=count,
    )))


@pytest.mark.parametrize("query, expected", [
    ("会议耳机需要包耳、能接线，还能连接两台设备", "DEMO-H04"),
    ("一百元以下的耳机，支持 AAC 和防泼溅", "DEMO-H01"),
    ("仅有一个 USB-C 口、折叠插脚、不附充电线的充电头", "DEMO-A01"),
    ("27 英寸 2K 屏，支架能升降和旋转", "DEMO-M02"),
    ("需要 4K、出厂校色和 USB-C 视频输入", "DEMO-M04"),
])
def test_conjunctive_claims_do_not_fill_requested_count_with_unsupported_items(
    query, expected,
):
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    response = _ask(service, query)
    assert [item.product_id for item in response.recommendations] == [expected]
    assert [ref.source_type for ref in response.recommendations[0].evidence][:1] == [
        "description"
    ]


def test_two_supported_products_are_both_returned_and_negative_mentions_do_not_pass(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"演示耳机 {product_id}", "category": "耳机",
            "price": 100, "stock": 3, "description": description,
            "tags": ["AAC"] if product_id == "NO-AAC" else (
                ["防泼溅"] if product_id == "NO-SPLASH" else []
            ),
            "reviews": ["演示评论：支持 AAC 与 IPX4 防泼溅。"]
            if product_id == "REVIEW-ONLY" else [],
            "data_origin": "synthetic_demo",
        }
        for product_id, description in (
            ("BOTH-1", "合成商品。支持 AAC，也有 IPX4 防泼溅。"),
            ("BOTH-2", "合成商品。支持 AAC，也有 IPX5 防泼溅。"),
            ("NO-AAC", "合成商品。不支持 AAC，有 IPX4 防泼溅。"),
            ("NO-SPLASH", "合成商品。支持 AAC，但没有防泼溅能力。"),
            ("REVIEW-ONLY", "合成商品。普通耳机，未列出编码和防护等级。"),
        )
    ]
    path = tmp_path / "claims.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)

    response = _ask(service, "推荐支持 AAC 和防泼溅的耳机", count=4)
    assert {item.product_id for item in response.recommendations} == {
        "BOTH-1", "BOTH-2",
    }
    assert all(
        item.evidence and item.evidence[0].source_type == "description"
        for item in response.recommendations
    )


def test_broad_shopping_request_still_returns_multiple_products():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    response = _ask(service, "推荐几款耳机", count=4)
    assert len(response.recommendations) == 4


def test_arabic_numeral_followed_by_yuan_yixia_sets_a_real_price_cap():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    response = _ask(service, "100元以下的耳机，推荐几款", count=4)
    assert response.recommendations
    assert all(item.price <= 100 for item in response.recommendations)


@pytest.mark.parametrize("size_constraint", ["不到11英寸", "不超过11英寸"])
def test_screen_size_is_not_parsed_as_a_yuan_budget(size_constraint):
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    response = _ask(
        service,
        f"想买屏幕{size_constraint}的平板，预算2000元以内，推荐几款",
        count=3,
    )
    expected = {"DEMO-T01", "DEMO-T05"}
    if size_constraint.startswith("不超过"):
        expected.add("DEMO-T02")
    assert {item.product_id for item in response.recommendations} == expected


def test_one_usb_c_port_does_not_mean_one_port_total(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"演示充电器 {product_id}", "category": "配件",
            "price": 99, "stock": 2, "description": description,
            "tags": ["充电器"], "reviews": [], "data_origin": "synthetic_demo",
        }
        for product_id, description in (
            ("ONE-PORT", "合成演示。单USB-C接口，最大30 W。"),
            ("TWO-PORTS", "合成演示。有一个USB-C接口和一个USB-A接口。"),
            ("CONTRADICTORY", "合成演示。仅有一个USB-C接口。另有一个USB-A接口。"),
        )
    ]
    path = tmp_path / "port-count.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)
    response = _ask(service, "仅有一个 USB-C 口的充电器", count=3)
    assert [item.product_id for item in response.recommendations] == ["ONE-PORT"]


def test_aac_denial_and_review_conflict_are_not_positive_evidence(tmp_path):
    assert "AAC" in missing_claims("推荐支持 AAC 的耳机", "本型号不能支持 AAC")
    assert "AAC" not in {rule.name for rule in required_claims("不要支持 AAC 的耳机")}
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"演示耳机 {product_id}", "category": "耳机",
            "price": 99, "stock": 2, "description": description,
            "tags": [], "reviews": reviews, "data_origin": "synthetic_demo",
        }
        for product_id, description, reviews in (
            ("AAC-YES", "合成演示。支持 AAC。", []),
            ("AAC-DENIED", "合成演示。不能支持 AAC。", []),
            ("AAC-CONFLICT", "合成演示。支持 AAC。", ["实际不支持 AAC。"]),
        )
    ]
    path = tmp_path / "aac-conflict.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)
    response = _ask(service, "推荐支持 AAC 的耳机", count=3)
    assert [item.product_id for item in response.recommendations] == ["AAC-YES"]


def test_long_current_source_excerpt_shows_the_supporting_claim(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    row = {
        "kind": "product", "product_id": "LONG-1", "name": "长文档演示耳机",
        "category": "耳机", "price": 100, "stock": 2,
        "description": "一般资料。" * 130 + "支持 AAC 编码和 IPX4 防泼溅。",
        "tags": [], "reviews": [], "data_origin": "synthetic_demo",
    }
    path = tmp_path / "long-source.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    service.store.import_documents_jsonl(path)
    response = _ask(service, "推荐支持 AAC 和防泼溅的耳机")
    assert [item.product_id for item in response.recommendations] == ["LONG-1"]
    excerpt = response.recommendations[0].evidence[0].excerpt
    assert "AAC" in excerpt and "防泼溅" in excerpt
    assert len(excerpt) <= 500


def test_ram_is_not_confused_with_storage_capacity(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"演示平板 {product_id}", "category": "平板",
            "price": 500, "stock": 2, "description": description,
            "tags": [], "reviews": [], "data_origin": "synthetic_demo",
        }
        for product_id, description in (
            ("RAM-6", "合成演示。6 GB内存与256 GB存储。"),
            ("RAM-8", "合成演示。8 GB内存与128 GB存储。"),
        )
    ]
    path = tmp_path / "memory.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)
    ram = _ask(service, "想买至少8GB内存的平板")
    storage = _ask(service, "想买至少256GB存储的平板")
    assert [item.product_id for item in ram.recommendations] == ["RAM-8"]
    assert [item.product_id for item in storage.recommendations] == ["RAM-6"]


def test_interrogative_you_meiyou_is_not_a_negative_requirement():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    answerable = _ask(service, "有没有带独立数字区的键盘？", count=4)
    impossible = _ask(service, "有没有带独立数字区的68键机械键盘？", count=4)
    assert {item.product_id for item in answerable.recommendations} == {
        "DEMO-K01", "DEMO-K04",
    }
    assert impossible.recommendations == []


def test_single_port_power_and_maximum_budget_are_conjunctive():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    chargers = _ask(service, "有没有单口最高输出至少65W的充电器？", count=4)
    anc = _ask(service, "预算最多300元，想买有主动降噪的耳机", count=4)
    assert {item.product_id for item in chargers.recommendations} == {
        "DEMO-A02", "DEMO-A03",
    }
    assert [item.product_id for item in anc.recommendations] == ["DEMO-H02"]
