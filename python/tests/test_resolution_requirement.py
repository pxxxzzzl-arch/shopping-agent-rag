"""A stated display resolution is an exact catalog requirement."""

from __future__ import annotations

import asyncio
import json

from shopping_agent.config import Settings
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


def test_exact_pixel_resolution_excludes_retrieval_near_misses(tmp_path):
    service = create_service(Settings(database_url="sqlite://", seed_demo=False))
    catalog = tmp_path / "monitors.jsonl"
    rows = [
        {
            "kind": "product", "product_id": product_id,
            "name": f"合成显示器 {product_id}", "category": "显示器",
            "price": 899, "stock": 3,
            "description": f"27 英寸 IPS 显示器，{resolution} 分辨率。",
            "tags": ["IPS"], "reviews": [], "data_origin": "synthetic_demo",
        }
        for product_id, resolution in (
            ("QHD", "2560×1440"), ("FHD", "1920×1080")
        )
    ]
    catalog.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    service.store.import_documents_jsonl(catalog)
    for mode in ("bm25", "vector", "hybrid"):
        response = asyncio.run(service.recommend(ShopRequest(
            user_id=f"resolution-{mode}",
            query="找一台 IPS 2560 x 1440 显示器",
            num_items=2, retrieval_mode=mode,
        )))
        assert [item.product_id for item in response.recommendations] == ["QHD"]
