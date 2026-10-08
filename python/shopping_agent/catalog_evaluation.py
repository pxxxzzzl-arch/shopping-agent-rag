"""Run the complete-set benchmark against an isolated imported catalog."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Sequence

from .answer import AnswerComposer
from .config import Settings
from .evaluation_multi import evaluate_multi_product_labels, load_multi_product_cases
from .storage import CatalogStore
from .workflow import ShoppingService


def evaluate_catalog(catalog_path: str | Path, labels_path: str | Path) -> dict:
    """Import one catalog into a fresh in-memory database and score its labels."""
    catalog_path = Path(catalog_path)
    labels_path = Path(labels_path)
    settings = Settings(database_url="sqlite://", seed_demo=False)
    store = CatalogStore(settings.database_url)
    store.initialize()
    imported = store.import_documents_jsonl(catalog_path)
    if imported == 0:
        raise ValueError("Catalog file did not import any documents")
    products = store.list_products(in_stock_only=False)
    if not products:
        raise ValueError("Catalog file did not import any products")

    # Fail invalid gold labels before making any recommendation requests.
    for row in load_multi_product_cases(labels_path):
        for product_id, source_ids in row["gold_sources_by_product"].items():
            product = store.get_product(product_id)
            if product is None or product.stock <= 0:
                raise ValueError(
                    f"{row['case_id']}: gold product {product_id} is absent or out of stock"
                )
            for source_id in source_ids:
                source = store.get_document(source_id)
                if source is None or source.product_id != product_id:
                    raise ValueError(
                        f"{row['case_id']}: gold source {source_id} is not current for {product_id}"
                    )

    service = ShoppingService(store, AnswerComposer(settings), settings)
    report = asyncio.run(evaluate_multi_product_labels(service, labels_path))
    report["catalog_file_sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    report["catalog_product_count"] = len(products)
    report["catalog_imported_documents"] = imported
    report["isolation"] = "fresh in-memory SQLite; demo seeding and experiments disabled"
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a labeled synthetic catalog in an isolated SQLite database"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Write JSON report; existing files are preserved")
    args = parser.parse_args(argv)
    report = evaluate_catalog(args.catalog, args.labels)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(serialized)
    else:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
