"""Versioned source provenance and atomic product/FAQ maintenance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shopping_agent.catalog_cli import main
from shopping_agent.seed import seed_demo_data, seed_demo_faq_data
from shopping_agent.storage import CatalogStore


def _write(path: Path, *records: dict[str, object]) -> Path:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def _product(product_id: str = "P-1", **changes: object) -> dict[str, object]:
    return {
        "kind": "product",
        "product_id": product_id,
        "name": "测试充电器",
        "category": "配件",
        "price": 39.9,
        "stock": 2,
        "description": "支持 USB-C 设备，具体兼容性需要核对协议。",
        "tags": ["USB-C"],
        "reviews": ["测试评价：包装里没有线。"],
        "source_url": "https://example.test/catalog/p-1",
        "license": "authorized-demo-import",
        "updated_at": "2026-09-23T10:00:00+08:00",
        **changes,
    }


def _faq(faq_id: str = "FAQ-1", **changes: object) -> dict[str, object]:
    return {
        "kind": "faq",
        "faq_id": faq_id,
        "question": "是否附带充电线？",
        "answer": "此测试 FAQ 说明不附带充电线。",
        "source_url": "https://example.test/help/faq-1",
        "license": "authorized-demo-import",
        "updated_at": "2026-09-23T10:00:00+08:00",
        **changes,
    }


@pytest.fixture
def store(tmp_path: Path) -> CatalogStore:
    catalog = CatalogStore(f"sqlite:///{tmp_path / 'sources.db'}")
    catalog.initialize()
    return catalog


def test_mixed_import_records_provenance_and_exact_original_line(store: CatalogStore, tmp_path: Path) -> None:
    product, faq = _product(), _faq()
    path = _write(tmp_path / "documents.jsonl", product, faq)
    assert store.import_documents_jsonl(path) == 2

    documents = {document.source_id: document for document in store.list_documents()}
    assert set(documents) == {"P-1:description", "P-1:review:1", "FAQ-1:answer"}
    description = store.get_document("P-1:description")
    assert description is not None
    assert description.product_id == "P-1"
    assert description.source_url == "https://example.test/catalog/p-1"
    assert description.license == "authorized-demo-import"
    assert description.updated_at == "2026-09-23T02:00:00+00:00"
    assert description.version == 1
    assert json.loads(description.original_text) == product
    assert store.get_source("P-1:review:1") is not None
    answer = store.get_document("FAQ-1:answer")
    assert answer is not None and answer.product_id is None and answer.faq_id == "FAQ-1"
    assert json.loads(answer.original_text) == faq

    reopened = CatalogStore(str(store.engine.url))
    assert reopened.get_document("FAQ-1:answer") == answer
    assert reopened.get_faq("FAQ-1") is not None


def test_invalid_later_line_rolls_back_product_update_and_faq_insert(store: CatalogStore, tmp_path: Path) -> None:
    store.import_documents_jsonl(_write(tmp_path / "initial.jsonl", _product()))
    original = store.get_product("P-1")
    old_source = store.get_document("P-1:description")
    path = _write(tmp_path / "invalid.jsonl", _product(stock=99), _faq(), _faq("FAQ-BAD", answer=""))

    with pytest.raises(ValueError, match="line 3"):
        store.import_documents_jsonl(path)

    assert store.get_product("P-1") == original
    assert store.get_document("P-1:description") == old_source
    assert store.get_faq("FAQ-1") is None
    assert store.list_documents() == [
        document for document in store.list_documents() if document.product_id == "P-1"
    ]


@pytest.mark.parametrize(
    "bad_record",
    [
        _product(source_url=None),
        _product(license=None),
        _product(kind=[]),
        _product(updated_at="2026-09-23T10:00:00"),
        _product(updated_at=None),
    ],
)
def test_new_import_rejects_missing_or_invalid_provenance_atomically(
    store: CatalogStore, tmp_path: Path, bad_record: dict[str, object]
) -> None:
    path = _write(tmp_path / "bad-metadata.jsonl", _faq(), bad_record)
    with pytest.raises(ValueError, match="line 2"):
        store.import_documents_jsonl(path)
    assert store.get_faq("FAQ-1") is None
    assert store.list_documents() == []


def test_product_update_delete_and_reimport_never_revive_stale_evidence(store: CatalogStore, tmp_path: Path) -> None:
    path = tmp_path / "product.jsonl"
    store.import_documents_jsonl(_write(path, _product()))
    store.record_event("alice", "P-1", "click")
    old_ids = {document.source_id for document in store.list_documents()}
    assert old_ids == {"P-1:description", "P-1:review:1"}

    store.import_documents_jsonl(_write(path, _product(description="新版说明：仅限 USB-C。", reviews=[])))
    assert all(store.get_document(source_id) is None for source_id in old_ids)
    assert {document.source_id for document in store.list_documents()} == {"P-1:description:v2"}
    assert store.get_document("P-1:description:v2").version == 2  # type: ignore[union-attr]

    assert store.delete_product("P-1") is True
    assert store.get_product("P-1") is None
    assert store.get_stock("P-1") == 0
    assert store.get_document("P-1:description:v2") is None
    assert store.list_documents() == []
    assert store.get_user_category_counts("alice") == {}

    store.import_documents_jsonl(_write(path, _product()))
    assert {document.source_id for document in store.list_documents()} == {
        "P-1:description:v3", "P-1:review:1:v3"
    }
    assert all(store.get_document(source_id) is None for source_id in old_ids)


def test_faq_update_delete_and_demo_seed_do_not_restore_deleted_source(store: CatalogStore, tmp_path: Path) -> None:
    path = tmp_path / "faq.jsonl"
    store.seed_faq_from_jsonl(_write(path, _faq()))
    assert store.get_document("FAQ-1:answer") is not None
    store.seed_faq_from_jsonl(_write(path, _faq(answer="新版答案。")))
    assert store.get_document("FAQ-1:answer") is None
    assert store.get_document("FAQ-1:answer:v2").text.endswith("新版答案。")  # type: ignore[union-attr]
    assert store.delete_faq("FAQ-1") is True
    assert store.get_document("FAQ-1:answer:v2") is None
    assert store.get_faq("FAQ-1") is None

    seed_demo_faq_data(store)
    demo_id = "DEMO-FAQ-01:answer"
    assert store.get_document(demo_id) is not None
    assert store.delete_faq("DEMO-FAQ-01") is True
    seed_demo_faq_data(store)
    assert store.get_document(demo_id) is None
    assert store.get_faq("DEMO-FAQ-01") is None
    assert len(store.list_faqs()) == 5


def test_demo_product_source_ids_stay_compatible_and_seed_is_non_destructive(store: CatalogStore, tmp_path: Path) -> None:
    assert seed_demo_data(store) == 30
    source = store.get_document("DEMO-H02:description")
    assert source is not None and source.license == "synthetic_demo"
    assert "合成演示商品" in source.original_text
    changed = _product(
        product_id="DEMO-H02",
        name="已获准更新的测试商品",
        source_url="https://example.test/changed",
    )
    store.import_documents_jsonl(_write(tmp_path / "override.jsonl", changed))
    updated = store.get_product("DEMO-H02")
    assert updated is not None and updated.name == "已获准更新的测试商品"
    assert source.source_id != next(
        document.source_id for document in store.list_documents() if document.product_id == "DEMO-H02"
    )
    seed_demo_data(store)
    assert store.get_product("DEMO-H02") == updated


def test_cli_import_lookup_and_delete_faq(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    path = _write(tmp_path / "docs.jsonl", _faq())
    assert main(["import-documents", str(path), "--database-url", database_url]) == 0
    assert "1 份文档" in capsys.readouterr().out
    assert main(["source", "FAQ-1:answer", "--database-url", database_url]) == 0
    lookup = json.loads(capsys.readouterr().out)
    assert json.loads(lookup["original_text"])["answer"] == _faq()["answer"]
    assert main(["delete-faq", "FAQ-1", "--database-url", database_url]) == 0
    capsys.readouterr()
    assert main(["source", "FAQ-1:answer", "--database-url", database_url]) == 1
    assert "当前来源不存在" in capsys.readouterr().err
