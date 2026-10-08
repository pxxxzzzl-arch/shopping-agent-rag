"""Adversarial, source-backed contracts for typed shopping conditions."""

from __future__ import annotations

import json

from shopping_agent.constraints import judge_conditions, parse_conditions
from shopping_agent.storage import CatalogStore


def _store(tmp_path, descriptions: dict[str, str]) -> CatalogStore:
    store = CatalogStore("sqlite://")
    store.initialize()
    path = tmp_path / "audit-products.jsonl"
    path.write_text(
        "\n".join(json.dumps({
            "kind": "product", "product_id": product_id,
            "name": "合成测试商品", "category": "手机", "price": 1000,
            "stock": 2, "description": description, "tags": [], "reviews": [],
            "data_origin": "synthetic_demo",
        }, ensure_ascii=False) for product_id, description in descriptions.items()),
        encoding="utf-8",
    )
    store.import_documents_jsonl(path)
    return store


def _judge(store: CatalogStore, product_id: str, query: str, field: str):
    judgments = judge_conditions(
        parse_conditions(query), store.get_product(product_id), store,
    )
    return next(item for item in judgments if item.field == field)


def test_explicit_wireless_charging_and_satellite_requirements_are_parsed(tmp_path):
    store = _store(tmp_path, {"ORDINARY": "普通手机，支持蓝牙连接。"})
    for query, field in (
        ("推荐必须支持无线充电功能的手机", "wireless_charging"),
        ("推荐有卫星通信的手机", "satellite_communication"),
    ):
        judgment = _judge(store, "ORDINARY", query, field)
        assert judgment.status == "unknown"
        assert judgment.evidence == []


def test_bluetooth_version_requires_the_exact_documented_version(tmp_path):
    store = _store(tmp_path, {
        "OLD": "支持蓝牙 5.0 连接。",
        "NEW": "支持蓝牙 5.3 连接。",
    })
    query = "推荐必须支持蓝牙 5.3 的手机"
    old = _judge(store, "OLD", query, "bluetooth_version")
    new = _judge(store, "NEW", query, "bluetooth_version")
    assert old.status == "refuted"
    assert new.status == "supported"
    assert new.evidence and "5.3" in new.evidence[0].excerpt


def test_non_in_ear_is_an_exclusion_and_optional_nfc_is_not_absence(tmp_path):
    store = _store(tmp_path, {
        "IN_EAR": "入耳式耳机。",
        "OPEN": "开放式耳机。",
    })
    query = "推荐非入耳式耳机"
    assert _judge(store, "IN_EAR", query, "wearing_style").status == "refuted"
    supported = _judge(store, "OPEN", query, "wearing_style")
    assert supported.operator == "!=" and supported.status == "supported"
    for optional in (
        "不需要 NFC 的手机", "无需 NFC 的手机", "不要求 NFC 的手机",
        "NFC 可有可无的手机", "可有可无 NFC 的手机",
    ):
        assert not any(item.field == "nfc" for item in parse_conditions(optional))
    for optional in ("推荐 65W 充电可有可无的手机", "推荐 65W 快充可有可无的手机"):
        assert not any(item.field == "power_w" for item in parse_conditions(optional))


def test_negated_source_facts_do_not_become_positive_evidence(tmp_path):
    store = _store(tmp_path, {
        "NO_FAST": "这款手机不支持 65W 充电。",
        "NO_NFC": "这款手机未配备 NFC。",
        "NOT_OLED": "这不是 OLED 屏。",
    })
    for product_id, query, field in (
        ("NO_FAST", "推荐支持 65W 充电的手机", "power_w"),
        ("NO_NFC", "推荐支持 NFC 的手机", "nfc"),
        ("NOT_OLED", "推荐 OLED 屏的手机", "panel_type"),
    ):
        judgment = _judge(store, product_id, query, field)
        assert judgment.status == "refuted"
        assert judgment.evidence


def test_unspecified_source_mentions_are_unknown_before_and_after_attribute(tmp_path):
    store = _store(tmp_path, {
        "CHARGING_BEFORE": "支持蓝牙 5.0。未说明无线充电能力。",
        "CHARGING_AFTER": "支持蓝牙 5.0。无线充电能力未说明。",
        "NFC_BEFORE": "未提及 NFC 能力。",
        "NFC_AFTER": "NFC 是否支持未提及。",
        "OLED_BEFORE": "未标注 OLED 屏幕信息。",
        "OLED_AFTER": "OLED 屏幕参数未标注。",
        "POWER_BEFORE": "未注明 65W 充电规格。",
        "POWER_AFTER": "65W 充电能力未注明。",
        "CHARGING_QUESTION": "尚未说明是否支持无线充电。",
        "NFC_QUESTION": "没有说明是否有 NFC。",
        "NFC_PENDING": "NFC 功能尚未标注。",
    })
    examples = (
        ("CHARGING_BEFORE", "推荐必须支持无线充电功能的手机", "wireless_charging"),
        ("CHARGING_AFTER", "推荐必须支持无线充电功能的手机", "wireless_charging"),
        ("NFC_BEFORE", "推荐支持 NFC 的手机", "nfc"),
        ("NFC_AFTER", "推荐支持 NFC 的手机", "nfc"),
        ("OLED_BEFORE", "推荐 OLED 屏的手机", "panel_type"),
        ("OLED_AFTER", "推荐 OLED 屏的手机", "panel_type"),
        ("POWER_BEFORE", "推荐支持 65W 充电的手机", "power_w"),
        ("POWER_AFTER", "推荐支持 65W 充电的手机", "power_w"),
        ("CHARGING_QUESTION", "推荐必须支持无线充电功能的手机", "wireless_charging"),
        ("NFC_QUESTION", "推荐支持 NFC 的手机", "nfc"),
        ("NFC_PENDING", "推荐支持 NFC 的手机", "nfc"),
    )
    for product_id, query, field in examples:
        judgment = _judge(store, product_id, query, field)
        assert judgment.status == "unknown", (product_id, judgment)
        assert judgment.evidence
