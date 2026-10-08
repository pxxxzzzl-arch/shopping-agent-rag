"""Meaningful checks for catalog persistence and recommendation inputs."""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from shopping_agent.seed import seed_demo_data
from shopping_agent.storage import CatalogStore


@pytest.fixture
def store(tmp_path: Path) -> CatalogStore:
    catalog = CatalogStore(f"sqlite:///{tmp_path / 'catalog.db'}")
    seed_demo_data(catalog)
    return catalog


def test_seed_is_idempotent_and_persists_across_store_instances(store: CatalogStore, tmp_path: Path) -> None:
    first = store.list_products(in_stock_only=False)
    assert len(first) >= 24
    assert all(item.product_id.startswith("DEMO-") for item in first)
    assert seed_demo_data(store) == len(first)
    assert len(store.list_products(in_stock_only=False)) == len(first)

    reopened = CatalogStore(f"sqlite:///{tmp_path / 'catalog.db'}")
    assert reopened.get_product(first[0].product_id) == first[0]
    with pytest.raises(FrozenInstanceError):
        first[0].stock = 9  # type: ignore[misc]


def test_catalog_filters_and_stock_are_authoritative(store: CatalogStore) -> None:
    all_products = store.list_products(in_stock_only=False)
    sold_out = [item for item in all_products if item.stock == 0]
    assert sold_out, "demo data should exercise sold-out filtering"
    assert all(item.stock > 0 for item in store.list_products())
    assert all(item.category == "耳机" for item in store.list_products(category="耳机"))
    assert all(item.price <= 300 for item in store.list_products(max_price=300))
    assert store.get_stock(sold_out[0].product_id) == 0
    assert store.get_stock("missing") == 0
    assert store.get_product("missing") is None
    assert any(item.product_id == sold_out[0].product_id for item in store.list_products(in_stock_only=False))


def test_events_are_scoped_to_user_and_counted_by_category(store: CatalogStore) -> None:
    headphone = store.list_products(category="耳机")[0]
    keyboard = store.list_products(category="键盘")[0]
    store.record_event("alice", headphone.product_id, "view")
    store.record_event("alice", headphone.product_id, "click")
    store.record_event("alice", keyboard.product_id, "view")
    store.record_event("bob", keyboard.product_id, "view")
    assert store.get_user_category_counts("alice") == {"耳机": 2, "键盘": 1}
    assert store.get_user_category_counts("bob") == {"键盘": 1}
    assert store.get_user_category_counts("unknown") == {}
    with pytest.raises(ValueError, match="Unknown product_id"):
        store.record_event("alice", "missing", "view")


def test_invalid_jsonl_does_not_partially_seed(store: CatalogStore, tmp_path: Path) -> None:
    original_count = len(store.list_products(in_stock_only=False))
    good = store.list_products(in_stock_only=False)[0]
    path = tmp_path / "bad.jsonl"
    path.write_text(
        json.dumps(
            {
                "product_id": "DEMO-NEW",
                "name": good.name,
                "category": good.category,
                "price": good.price,
                "stock": good.stock,
                "description": good.description,
                "tags": list(good.tags),
                "reviews": list(good.reviews),
            },
            ensure_ascii=False,
        )
        + "\n{invalid-json}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 2"):
        store.seed_from_jsonl(path)
    assert len(store.list_products(in_stock_only=False)) == original_count


@pytest.mark.parametrize("field,bad_value", [("stock", 1.9), ("stock", True), ("price", 1.005)])
def test_invalid_money_or_stock_rejects_entire_batch(
    store: CatalogStore, tmp_path: Path, field: str, bad_value: object
) -> None:
    base = {
        "name": "测试商品",
        "category": "耳机",
        "price": 1.01,
        "stock": 2,
        "description": "测试描述",
        "tags": ["蓝牙"],
        "reviews": [],
    }
    valid = {**base, "product_id": "NEW-VALID"}
    invalid = {**base, "product_id": "NEW-INVALID", field: bad_value}
    path = tmp_path / "bad-product.jsonl"
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in (valid, invalid)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 2"):
        store.seed_from_jsonl(path)
    assert store.get_product("NEW-VALID") is None
    assert store.get_product("NEW-INVALID") is None
