"""Bundled, fully synthetic catalog for local demonstrations."""

from __future__ import annotations

from pathlib import Path

from shopping_agent.storage import CatalogStore


DEMO_PRODUCTS_PATH = Path(__file__).parent / "data" / "demo_products.jsonl"
DEMO_FAQ_PATH = Path(__file__).parent / "data" / "demo_faq.jsonl"


def seed_demo_data(store: CatalogStore) -> int:
    """Create schema and idempotently load the bundled demo products."""

    store.initialize()
    return store.seed_from_jsonl(DEMO_PRODUCTS_PATH, overwrite=False)


def seed_demo_faq_data(store: CatalogStore) -> int:
    """Install synthetic FAQ once without overwriting an imported revision."""

    store.initialize()
    return store.seed_faq_from_jsonl(DEMO_FAQ_PATH, overwrite=False)
