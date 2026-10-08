"""The import command reports success only after valid, atomic writes."""

from __future__ import annotations

import json
from pathlib import Path

from shopping_agent.catalog_cli import main
from shopping_agent.storage import CatalogStore


def _product(product_id: str) -> dict[str, object]:
    return {
        "product_id": product_id,
        "name": "测试耳机",
        "category": "耳机",
        "price": 299,
        "stock": 7,
        "description": "支持通勤使用",
        "tags": ["蓝牙"],
        "reviews": ["佩戴舒适"],
    }


def test_import_catalog_from_cli(tmp_path: Path, capsys) -> None:
    catalog = tmp_path / "products.jsonl"
    catalog.write_text(json.dumps(_product("USER-1"), ensure_ascii=False) + "\n", encoding="utf-8")
    database_url = f"sqlite:///{tmp_path / 'catalog.db'}"

    assert main(["import", str(catalog), "--database-url", database_url]) == 0
    assert "导入成功：1 件" in capsys.readouterr().out
    stored = CatalogStore(database_url).get_product("USER-1")
    assert stored is not None
    assert stored.price == 299
    assert stored.stock == 7


def test_invalid_jsonl_fails_without_partial_import(tmp_path: Path, capsys) -> None:
    catalog = tmp_path / "broken.jsonl"
    catalog.write_text(
        json.dumps(_product("USER-1"), ensure_ascii=False) + "\n{bad json}\n",
        encoding="utf-8",
    )
    database_url = f"sqlite:///{tmp_path / 'catalog.db'}"

    assert main(["import", str(catalog), "--database-url", database_url]) == 1
    captured = capsys.readouterr()
    assert "导入失败" in captured.err
    assert "line 2" in captured.err
    assert "导入成功" not in captured.out
    assert CatalogStore(database_url).list_products(in_stock_only=False) == []


def test_empty_file_is_not_reported_as_success(tmp_path: Path, capsys) -> None:
    catalog = tmp_path / "empty.jsonl"
    catalog.write_text("\n", encoding="utf-8")

    assert main(["import", str(catalog), "--database-url", f"sqlite:///{tmp_path / 'catalog.db'}"]) == 1
    captured = capsys.readouterr()
    assert "没有商品记录" in captured.err
    assert "导入成功" not in captured.out
