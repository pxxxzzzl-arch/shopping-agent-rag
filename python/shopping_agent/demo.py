"""Local-only presentation shell around the unchanged production shopping API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from fastapi.responses import FileResponse
import uvicorn

from .app import create_app
from .config import Settings
from .embeddings import BAILIAN_BASE_URL, BAILIAN_MODEL

ROOT = Path(__file__).resolve().parents[2]


def create_demo_app(settings=None):
    app = create_app(settings=settings or Settings(database_url="sqlite://"))

    @app.get("/", include_in_schema=False)
    @app.get("/demo", include_in_schema=False)
    def demo_page():
        return FileResponse(ROOT / "docs/demo/index.html")

    @app.get("/demo/info", include_in_schema=False)
    def info():
        service = app.state.service
        return {"provider": service.embedding.provider, "model": service.embedding.model,
                "synthetic_data_only": True, "database": "isolated demonstration database"}

    @app.get("/demo/evidence", include_in_schema=False)
    def evidence():
        paths = sorted((ROOT / "python/reports").glob("business_cloud_smoke_*.json"))
        if not paths:
            return {"available": False}
        report = json.loads(paths[-1].read_text())
        meta = report.get("embedding_metadata", {})
        return {"available": True, "kind": report.get("kind"), "status": report.get("status"),
                "created_at": report.get("created_at"), "cases": len(report.get("cases", [])),
                "passed_cases": sum(row["status"] == "passed" for row in report.get("cases", [])),
                "http_calls": meta.get("http_attempted_calls"),
                "http_successes": meta.get("http_successful_calls"),
                "response_models": meta.get("response_models", []),
                "dimensions": meta.get("requested_dimensions"),
                "tokens": meta.get("reported_total_tokens"), "file": paths[-1].name}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--provider", choices=("hash", "bailian"), default="hash")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    if args.provider == "bailian" and not args.allow_remote:
        parser.error("--provider bailian requires explicit --allow-remote")
    settings = Settings(database_url="sqlite://", embedding_provider=args.provider,
                        embedding_model=BAILIAN_MODEL if args.provider == "bailian" else "",
                        embedding_base_url=BAILIAN_BASE_URL if args.provider == "bailian" else "",
                        embedding_api_key=(os.getenv("SHOPPING_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")) if args.provider == "bailian" else "",
                        embedding_allow_remote=args.allow_remote,
                        embedding_timeout_seconds=15, tool_timeout_seconds=45)
    uvicorn.run(create_demo_app(settings), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
