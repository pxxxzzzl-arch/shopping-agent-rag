"""Explicit transfer attributes must be verified against each product's source."""

from __future__ import annotations

import asyncio
import json

from shopping_agent.config import Settings
from shopping_agent.product_requirements import required_claims
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def _catalog(tmp_path, category, products):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    rows = [
        {
            "kind": "product",
            "product_id": product_id,
            "name": f"合成{category} {product_id}",
            "category": category,
            "price": price,
            "stock": stock,
            "description": description,
            "tags": tags,
            "reviews": [],
            "data_origin": "synthetic_demo",
        }
        for product_id, price, stock, description, tags in products
    ]
    path = tmp_path / "transfer-attribute-products.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(path)
    return service


def _ids(service, query, count=5):
    response = asyncio.run(service.recommend(ShopRequest(
        user_id="transfer-attribute-regression",
        query=query,
        num_items=count,
    )))
    return {item.product_id for item in response.recommendations}


def test_wireless_connection_accepts_bluetooth_or_24g_but_not_charging_or_wired(tmp_path):
    query = "找能无线连接、可热插拔的机械键盘，要现货。"
    names = {rule.name for rule in required_claims(query)}
    assert "无线连接" in names
    assert "不附充电线" not in names

    service = _catalog(tmp_path, "键盘", [
        ("BT", 399, 3, "68 键机械键盘；支持蓝牙连接；支持热插拔。", []),
        ("RADIO", 429, 3, "84 键机械键盘；支持 2.4GHz 接收器连接；支持热插拔。", []),
        ("WIRED", 299, 3, "104 键机械键盘；仅支持 USB-C 有线连接；支持热插拔。", []),
        ("CHARGING", 349, 3, "机械键盘；仅有线连接；支持无线充电；支持热插拔。", []),
        ("DENIED", 379, 3, "机械键盘；不支持无线连接，仅支持 USB-C 有线连接；支持热插拔。", []),
        ("SOLD-OUT", 389, 0, "机械键盘；支持蓝牙连接；支持热插拔。", []),
    ])
    assert _ids(service, query) == {"BT", "RADIO"}


def test_vesa_installation_requires_positive_source_and_respects_denial(tmp_path):
    service = _catalog(tmp_path, "显示器", [
        ("MOUNT-YES", 1199, 2, "27 英寸显示器；支持 VESA 支架安装。", []),
        ("MOUNT-SPEC", 1399, 2, "27 英寸显示器；VESA 100×100 安装孔位。", []),
        ("MOUNT-NO", 1099, 2, "27 英寸显示器；不支持 VESA 支架安装。", ["VESA"]),
        ("MOUNT-UNKNOWN", 999, 2, "27 英寸显示器；原装底座可升降。", []),
        ("MOUNT-SOLD-OUT", 1299, 0, "27 英寸显示器；支持 VESA 支架安装。", []),
    ])
    query = "我有显示器支架，挑支持 VESA 安装且库存大于零的显示器。"
    assert _ids(service, query) == {"MOUNT-YES", "MOUNT-SPEC"}


def test_esim_needs_positive_source_even_when_budgeted_phone_is_available(tmp_path):
    service = _catalog(tmp_path, "手机", [
        ("ESIM-YES", 2299, 2, "256GB 手机；支持 eSIM。", []),
        ("ESIM-NO", 1899, 2, "256GB 手机；不支持 eSIM。", ["eSIM"]),
        ("ESIM-UNKNOWN", 1799, 2, "256GB 手机；支持实体双 SIM 卡。", []),
        ("ESIM-SOLD-OUT", 1999, 0, "256GB 手机；支持 eSIM。", []),
    ])
    assert _ids(service, "手机预算压到 2000 元以内，还必须有 eSIM，能买到现货吗？") == set()
    assert _ids(service, "手机预算压到 2500 元以内，还必须有 eSIM，能买到现货吗？") == {
        "ESIM-YES"
    }


def test_98_keys_do_not_match_75_keys_even_with_same_switches(tmp_path):
    query = "想要 98 键、静音轴、能热插拔的机械键盘，现在有货吗？"
    products = [
        ("KEY-98", 699, 2, "98 键机械键盘；静音轴；支持热插拔。", []),
        ("KEY-75", 599, 2, "75 键机械键盘；静音轴；支持热插拔。", []),
        ("KEY-198", 899, 2, "198 键机械键盘；静音轴；支持热插拔。", []),
    ]
    service = _catalog(tmp_path, "键盘", products)
    assert _ids(service, query) == {"KEY-98"}

    sold_out = _catalog(tmp_path, "键盘", [
        (product_id, price, 0 if product_id == "KEY-98" else stock, description, tags)
        for product_id, price, stock, description, tags in products
    ])
    assert _ids(sold_out, query) == set()
