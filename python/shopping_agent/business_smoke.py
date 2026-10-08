"""Small, explicit live smoke of the production HTTP API on synthetic data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import threading
import time

import httpx
import uvicorn

from .app import create_app
from .config import Settings
from .embeddings import BAILIAN_BASE_URL, BAILIAN_MODEL
from .storage import verbatim_source_text


CASES = (
    ("bm25_zero_cloud", "推荐降噪耳机，预算500元", "bm25", "product", False),
    ("product_vector", "推荐降噪耳机，预算500元", "vector", "product", False),
    ("faq_hybrid", "演示商品是真实在售商品吗？", "hybrid", "faq", False),
    ("mixed_hybrid", "推荐降噪耳机，预算500元，并说明演示商品是真实在售商品吗？", "hybrid", "mixed", False),
    ("mixed_reuse", "推荐降噪耳机，预算500元，并说明演示商品是真实在售商品吗？", "hybrid", "mixed", False),
    ("faq_vector", "演示商品是真实在售商品吗？", "vector", "faq", False),
    ("unsupported_capability", "推荐会飞的手机", "hybrid", "product", True),
    ("empty_budget", "推荐耳机，预算1元", "hybrid", "product", True),
)


def snapshot() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    paths = sorted(Path(__file__).parent.glob("*.py"))
    paths += sorted((Path(__file__).parent / "data").glob("demo_*.jsonl"))
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def check_facts(body, store, query):
    """Check current rows and verbatim quotes, not open-ended semantic accuracy."""
    for item in body["recommendations"]:
        product = store.get_product(item["product_id"])
        assert product is not None and product.stock > 0, "unavailable product"
        assert item["price"] == product.price and item["stock"] == product.stock
        if "500" in query:
            assert product.price <= 500, "budget exceeded"
        assert item["evidence"], "missing product evidence"
        for evidence in item["evidence"]:
            source = store.get_document(evidence["source_id"])
            assert source is not None and source.product_id == product.product_id
            assert evidence["excerpt"] in verbatim_source_text(source), "stale/nonverbatim quote"
    for evidence in body["knowledge_evidence"]:
        source = store.get_document(evidence["source_id"])
        assert source is not None and evidence["excerpt"] in verbatim_source_text(source)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("hash", "bailian"), default="hash")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--max-http-calls", type=int, default=24)
    parser.add_argument("--base-url", default=BAILIAN_BASE_URL)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.max_http_calls < 1:
        parser.error("--max-http-calls must be positive")
    try:
        output = args.output.open("x", encoding="utf-8")
    except FileExistsError:
        print("Smoke refused overwrite; no API calls made")
        return 1
    report = {"schema_version": 1, "kind": "live_cloud_http" if args.provider == "bailian" else "offline_http",
              "created_at": datetime.now(timezone.utc).isoformat(), "status": "failed",
              "synthetic_data_only": True, "provider": args.provider,
              "max_http_calls": args.max_http_calls, "code_and_input_sha256": snapshot(),
              "cases": [], "real_semantic_accuracy": None, "online_business_uplift": None,
              "actual_billed_cost": None}
    server = None
    thread = None
    sock = None
    app = None
    key = os.getenv("SHOPPING_EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    try:
        if args.provider == "bailian" and not args.allow_remote:
            raise ValueError("Live cloud smoke requires explicit --allow-remote")
        configured = Settings(database_url="sqlite://", seed_demo=True,
                              embedding_provider=args.provider,
                              embedding_model=BAILIAN_MODEL if args.provider == "bailian" else "",
                              embedding_base_url=args.base_url if args.provider == "bailian" else "",
                              embedding_api_key=key if args.provider == "bailian" else "",
                              embedding_allow_remote=args.allow_remote,
                              embedding_timeout_seconds=15, tool_timeout_seconds=45)
        # Validate before starting a server, without making an embedding request.
        from .embeddings import create_embedding
        create_embedding(configured)
        app = create_app(settings=configured)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        if not server.started:
            raise ValueError("Local production API failed to start")
        adapter = app.state.service.embedding
        def count():
            return adapter.metadata()["http_attempted_calls"] if args.provider == "bailian" else 0
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=100, trust_env=False) as client:
            health = client.get("/health")
            assert health.status_code == 200 and health.json()["catalog_products"] == 30
            assert count() == 0, "startup or health made cloud calls"
            report["health"] = health.json()
            for case_id, query, mode, route, no_answer in CASES:
                service = app.state.service
                before = count()
                # Reserve a conservative upper bound before each API request.
                reserve = 0 if mode == "bm25" else 2 + sum(
                    math.ceil(len(index.documents) / 10)
                    for index in (service.retriever, service.faq_retriever)
                    if index.vector is None)
                if args.provider == "bailian" and before + reserve > args.max_http_calls:
                    raise ValueError("Stopped before request: cloud HTTP budget insufficient")
                started = time.perf_counter()
                result = client.post("/api/v1/shop/recommend", json={
                    "user_id": "synthetic-release-smoke", "query": query,
                    "num_items": 3, "retrieval_mode": mode})
                assert result.status_code == 200, "production API returned non-200"
                body = result.json()
                row = {"case_id": case_id, "query": query, "requested_mode": mode,
                       "expected_route": route, "expected_no_recommendations": no_answer,
                       "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                       "embedding_http_delta": count() - before,
                       "response": body, "status": "failed"}
                report["cases"].append(row)
                assert body["route"] == route, "unexpected route"
                assert body["configured_embedding_provider"] == args.provider
                check_facts(body, service.store, query)
                if no_answer:
                    assert not body["recommendations"], "unsafe recommendation"
                else:
                    routes = ("product", "faq") if route == "mixed" else (route,)
                    assert body["effective_retrieval_modes"] == {value: mode for value in routes}
                    for value in routes:
                        diagnostic = body["retrieval_diagnostics"][value]
                        assert diagnostic["status"] == "success" and not diagnostic["fallback_reason"]
                        if mode != "bm25":
                            assert diagnostic["actual_provider"] == args.provider and diagnostic["embedding_used"]
                    if "product" in routes:
                        assert body["recommendations"], "expected grounded products"
                    if "faq" in routes:
                        assert body["knowledge_evidence"], "expected FAQ evidence"
                if mode == "bm25":
                    assert count() == before, "BM25 made cloud calls"
                if case_id == "mixed_reuse" and args.provider == "bailian":
                    assert count() - before == 2, "unchanged corpus rebuilt its index"
                assert count() <= args.max_http_calls
                row["status"] = "passed"
                print(f"{case_id}: PASS route={body['route']} mode={mode} cloud_http_delta={row['embedding_http_delta']}", flush=True)
        report["status"] = "passed"
    except Exception as exc:
        # Transport messages are sanitized; suppress arbitrary assertion/error text.
        report["failure"] = {"type": type(exc).__name__, "message": "Smoke failed; inspect completed cases and sanitized diagnostics"}
        if isinstance(exc, ValueError) and (str(exc).startswith("Live cloud smoke requires") or str(exc).startswith("Stopped before request")):
            report["failure"]["message"] = str(exc)
    finally:
        if app is not None and app.state.service is not None and args.provider == "bailian":
            report["embedding_metadata"] = app.state.service.embedding.metadata()
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        if sock is not None:
            sock.close()
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if key and key in encoded:
            encoded = json.dumps({"status": "failed", "failure": "Credential detected; report suppressed"})
            report["status"] = "failed"
        with output:
            output.write(encoded + "\n")
    print(f"Business HTTP smoke {report['status'].upper()}; report={args.output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
