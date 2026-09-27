"""An external catalog can be benchmarked without loading demo products."""

from __future__ import annotations

import json

import pytest

from shopping_agent.catalog_evaluation import evaluate_catalog, main


def _files(tmp_path):
    catalog = tmp_path / "new-products.jsonl"
    labels = tmp_path / "new-labels.jsonl"
    products = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"新目录耳机 {product_id}", "category": "耳机",
            "price": price, "stock": 3,
            "description": "合成测试耳机，适合日常收听。",
            "tags": [], "reviews": [], "data_origin": "synthetic_demo",
        }
        for product_id, price in (("NEW-A", 100), ("NEW-B", 150))
    ]
    catalog.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in products) + "\n",
        encoding="utf-8",
    )
    label = {
        "case_id": "new-catalog-1", "query": "推荐两款耳机", "num_items": 2,
        "eligible_product_ids": ["NEW-A", "NEW-B"],
        "gold_sources_by_product": {
            "NEW-A": ["NEW-A:description"],
            "NEW-B": ["NEW-B:description"],
        },
        "synthetic": True, "label_rationale": "两款耳机都有库存。",
    }
    labels.write_text(json.dumps(label, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog, labels


def test_isolated_catalog_scores_only_imported_products(tmp_path):
    catalog, labels = _files(tmp_path)
    report = evaluate_catalog(catalog, labels)
    assert report["catalog_product_count"] == 2
    assert report["catalog_file_sha256"]
    for mode in report["modes"].values():
        assert mode["exact_set_cases"] == 1
        assert mode["correct_returned_items"] == 2
        assert mode["error_count"] == 0


def test_invalid_gold_source_is_rejected_before_scoring(tmp_path):
    catalog, labels = _files(tmp_path)
    row = json.loads(labels.read_text(encoding="utf-8"))
    row["gold_sources_by_product"]["NEW-A"] = ["NEW-A:missing"]
    labels.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="gold source"):
        evaluate_catalog(catalog, labels)


def test_cli_preserves_existing_report(tmp_path):
    catalog, labels = _files(tmp_path)
    output = tmp_path / "first-run.json"
    assert main(["--catalog", str(catalog), "--labels", str(labels), "--output", str(output)]) == 0
    first = output.read_bytes()
    with pytest.raises(FileExistsError):
        main(["--catalog", str(catalog), "--labels", str(labels), "--output", str(output)])
    assert output.read_bytes() == first
