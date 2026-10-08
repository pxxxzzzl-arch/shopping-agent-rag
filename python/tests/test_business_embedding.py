"""Real API/lifespan/transport integration; synthetic vectors, never cloud scores."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient
import pytest

from shopping_agent.app import create_app
from shopping_agent.config import Settings
from shopping_agent.embeddings import BailianEmbedding, create_embedding
from shopping_agent.embedding_benchmark import BailianEmbedding as BenchmarkEmbedding


TEST_KEY = "controlled-business-test-only"
QUERIES = {
    "product": "推荐降噪耳机，预算500元",
    "faq": "演示商品是真实在售商品吗？",
    "mixed": "推荐降噪耳机，预算500元，并说明演示商品是真实在售商品吗？",
}


@pytest.fixture
def endpoint(request):
    """Only the external provider is replaced, using actual HTTP sockets."""
    class Provider:
        lock = threading.Lock()
        logs = []
        responses = []
        metadata = []
        fault = None
        target = "index"
        once = False
        delay = .15

    provider = Provider()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = body["input"]
            phase = "query" if len(texts) == 1 else "index"
            with provider.lock:
                fault = provider.fault if phase == provider.target else None
                if fault and provider.once:
                    provider.fault = None
                sequence = len(provider.logs) + 1
                status = fault if isinstance(fault, int) else 200
                provider.logs.append({
                    "sequence": sequence,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "path": self.path, "phase": phase, "input": texts,
                    "model": body["model"], "dimensions": body["dimensions"],
                    "encoding_format": body["encoding_format"],
                    "authorization_valid": self.headers.get("Authorization") == "Bearer " + TEST_KEY,
                    "status": status, "fault": fault,
                })
            if fault == "timeout":
                time.sleep(provider.delay)
            vector = [1.0] + [0.0] * 1023
            if fault == "zero":
                vector = [0.0] * 1024
            elif fault == "bool":
                vector[0] = True
            elif fault == "dimension":
                vector = [1.0, 0.0]
            data = {"model": "text-embedding-v4", "usage": {"total_tokens": len(texts)},
                    "data": [{"index": i, "embedding": vector} for i in reversed(range(len(texts)))]}
            if status != 200:
                data = {"error": "untrusted response body containing " + TEST_KEY}
            encoded = json.dumps(data).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("x-request-id", f"controlled-{sequence}")
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider.url = f"http://127.0.0.1:{server.server_port}/v1"
    yield provider
    server.shutdown()
    server.server_close()
    thread.join()
    evidence_path = os.getenv("SHOPPING_BUSINESS_EVIDENCE_LOG")
    if evidence_path:
        row = {"test": request.node.nodeid, "kind": "controlled_http",
               "http_count": len(provider.logs), "server_requests": provider.logs,
               "api_responses": provider.responses, "adapter_metadata": provider.metadata}
        with Path(evidence_path).open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")


def settings(endpoint, **changes):
    return replace(Settings(database_url="sqlite://", embedding_provider="bailian",
                            embedding_model="text-embedding-v4", embedding_base_url=endpoint.url,
                            embedding_api_key=TEST_KEY, embedding_allow_remote=True,
                            embedding_timeout_seconds=2, tool_timeout_seconds=5), **changes)


@contextmanager
def client_for(endpoint, **changes):
    app = create_app(settings=settings(endpoint, **changes))
    with TestClient(app) as client:
        try:
            yield client
        finally:
            adapter = app.state.service.embedding
            if isinstance(adapter, BailianEmbedding):
                endpoint.metadata.append(adapter.metadata())


def recommend(client, endpoint, route="product", mode="vector", query=None):
    response = client.post("/api/v1/shop/recommend", json={
        "user_id": "controlled-user", "query": query or QUERIES[route],
        "retrieval_mode": mode, "num_items": 3,
    })
    assert response.status_code == 200
    body = response.json()
    with endpoint.lock:
        endpoint.responses.append(body)
    assert TEST_KEY not in response.text
    return body


def assert_current_facts(client, body):
    store = client.app.state.service.store
    for item in body["recommendations"]:
        current = store.get_product(item["product_id"])
        assert current is not None
        assert item["stock"] == current.stock > 0
        assert item["price"] == current.price <= 500
        assert item["evidence"]
        for evidence in item["evidence"]:
            source = store.get_document(evidence["source_id"])
            assert source is not None and source.product_id == current.product_id
    for evidence in body["knowledge_evidence"]:
        assert store.get_document(evidence["source_id"]) is not None


def assert_execution(body, mode, routes, *, fallback=None):
    assert body["configured_embedding_provider"] == "bailian"
    assert body["configured_embedding_model"] == "text-embedding-v4"
    assert body["requested_retrieval_mode"] == mode
    actual = "bm25" if fallback else mode
    assert body["effective_retrieval_modes"] == {route: actual for route in routes}, json.dumps(body, ensure_ascii=False)
    for route in routes:
        diagnostic = body["retrieval_diagnostics"][route]
        assert diagnostic["requested_mode"] == mode
        assert diagnostic["actual_mode"] == actual
        assert diagnostic["actual_provider"] == ("bm25" if fallback else "bailian")
        assert diagnostic["embedding_used"] is (not bool(fallback))
        assert diagnostic["status"] == ("fallback" if fallback else "success")
        if fallback:
            assert fallback in diagnostic["fallback_reason"]
            assert diagnostic["fallback_reason"] in body["warnings"]
        else:
            assert diagnostic["fallback_reason"] is None


def test_default_offline_and_configured_bm25_send_no_cloud_requests(endpoint):
    with client_for(endpoint, embedding_provider="", embedding_model="", embedding_allow_remote=False) as client:
        body = recommend(client, endpoint, mode="hybrid")
        assert body["configured_embedding_provider"] == "hash"
        assert body["retrieval_diagnostics"]["product"]["actual_provider"] == "hash"
        assert body["recommendations"]
    with client_for(endpoint) as client:
        service = client.app.state.service
        assert service.retriever.vector is None and service.faq_retriever.vector is None
        assert client.get("/health").json()["catalog_products"] == 30
        for route in QUERIES:
            body = recommend(client, endpoint, route, "bm25")
            assert body["route"] == route
            for diagnostic in body["retrieval_diagnostics"].values():
                assert diagnostic["actual_provider"] == "bm25"
                assert diagnostic["embedding_used"] is False
                assert diagnostic["index_state"] == "lazy"
        assert service.retriever.vector is None and service.faq_retriever.vector is None
    assert endpoint.logs == []


@pytest.mark.parametrize("route", QUERIES)
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_api_routes_call_shared_bailian_and_reuse_indexes(endpoint, route, mode):
    with client_for(endpoint) as client:
        service = client.app.state.service
        assert service.retriever.embedding is service.faq_retriever.embedding is service.embedding
        assert isinstance(service.embedding, BailianEmbedding)
        assert BenchmarkEmbedding is BailianEmbedding
        assert endpoint.logs == []
        body = recommend(client, endpoint, route, mode)
        routes = ["product", "faq"] if route == "mixed" else [route]
        assert body["route"] == route
        assert_execution(body, mode, routes)
        assert_current_facts(client, body)
        if "product" in routes:
            assert body["recommendations"]
        if "faq" in routes:
            assert body["knowledge_evidence"] and "合成" in body["answer"]
        builds = [log for log in endpoint.logs if log["phase"] == "index"]
        assert builds
        sent_sources = {text for log in builds for text in log["input"]}
        expected_sources = {
            document.text for key in routes
            for document in (service.retriever if key == "product" else service.faq_retriever).documents
        }
        assert sent_sources == expected_sources
        assert all(log["path"] == "/v1/embeddings" and log["authorization_valid"]
                   and log["dimensions"] == 1024 and log["model"] == "text-embedding-v4"
                   and log["encoding_format"] == "float" for log in endpoint.logs)
        assert sum(log["phase"] == "query" for log in endpoint.logs) == len(routes)
        first_count = len(endpoint.logs)
        again = recommend(client, endpoint, route, mode)
        assert_execution(again, mode, routes)
        assert len(endpoint.logs) == first_count + len(routes)
        assert [log for log in endpoint.logs if log["phase"] == "index"] == builds
        assert service.embedding.metadata()["http_attempted_calls"] == len(endpoint.logs)


def test_concurrent_first_mixed_requests_build_each_corpus_once(endpoint):
    with client_for(endpoint) as client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            bodies = list(pool.map(lambda _: recommend(client, endpoint, "mixed", "hybrid"), range(4)))
        for body in bodies:
            assert_execution(body, "hybrid", ["product", "faq"])
            assert_current_facts(client, body)
        indexed = [text for log in endpoint.logs if log["phase"] == "index" for text in log["input"]]
        service = client.app.state.service
        documents = [*service.retriever.documents, *service.faq_retriever.documents]
        assert len(indexed) == len(documents)
        assert set(indexed) == {doc.text for doc in documents}
        assert sum(log["phase"] == "query" for log in endpoint.logs) == 8
        assert service.embedding.metadata()["http_attempted_calls"] == len(endpoint.logs)


@pytest.mark.parametrize("changes, message", [
    ({"embedding_allow_remote": False}, "ALLOW_REMOTE"),
    ({"embedding_api_key": ""}, "API_KEY"),
    ({"embedding_base_url": "https://untrusted.invalid/v1"}, "official HTTPS"),
    ({"embedding_base_url": "https://secret@dashscope.aliyuncs.com/v1"}, "official HTTPS"),
    ({"embedding_provider": "unknown"}, "Unknown"),
])
def test_invalid_configuration_fails_before_http(endpoint, changes, message):
    with pytest.raises(ValueError, match=message) as error:
        with client_for(endpoint, **changes):
            pytest.fail("invalid configuration reached lifespan yield")
    assert TEST_KEY not in str(error.value)
    assert TEST_KEY not in repr(settings(endpoint))
    assert endpoint.logs == []


def test_environment_factory_and_legacy_ollama_selection(endpoint, monkeypatch):
    monkeypatch.setenv("SHOPPING_EMBEDDING_PROVIDER", "bailian")
    monkeypatch.setenv("SHOPPING_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("SHOPPING_EMBEDDING_BASE_URL", endpoint.url)
    monkeypatch.setenv("SHOPPING_EMBEDDING_ALLOW_REMOTE", "true")
    monkeypatch.setenv("SHOPPING_EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", TEST_KEY)
    configured = Settings.from_env()
    assert isinstance(create_embedding(configured), BailianEmbedding)
    assert TEST_KEY not in repr(configured)
    adapter = create_embedding(Settings(embedding_model="existing-local-model"))
    assert adapter.provider == "ollama" and adapter.base_url == "http://127.0.0.1:11434"
    assert create_embedding(Settings()).provider == "hash"
    assert endpoint.logs == []


def test_malformed_unicode_url_does_not_echo_credentials(endpoint):
    malformed = f"https://{TEST_KEY}：hidden@dashscope.aliyuncs.com/v1"
    with pytest.raises(ValueError) as error:
        with client_for(endpoint, embedding_base_url=malformed):
            pytest.fail("malformed URL accepted")
    assert TEST_KEY not in str(error.value)
    assert endpoint.logs == []


@pytest.mark.parametrize("fault, reason", [
    (401, "HTTP 401"), (429, "HTTP 429"), (500, "HTTP 500"),
    ("timeout", "timed out"), ("zero", "norm"),
    ("bool", "finite numeric"), ("dimension", "1024"),
])
@pytest.mark.parametrize("route", ["product", "faq"])
def test_index_failure_cached_and_explicit_refresh_recovers(endpoint, fault, reason, route, caplog, capsys):
    endpoint.fault = fault
    with client_for(endpoint, embedding_timeout_seconds=.05 if fault == "timeout" else 2) as client:
        body = recommend(client, endpoint, route, "hybrid")
        assert_execution(body, "hybrid", [route], fallback=reason)
        assert_current_facts(client, body)
        assert len(endpoint.logs) == 1
        again = recommend(client, endpoint, route, "hybrid")
        assert_execution(again, "hybrid", [route], fallback=reason)
        assert len(endpoint.logs) == 1
        endpoint.fault = None
        service = client.app.state.service
        service.refresh_index()  # documented explicit recovery action
        assert len(endpoint.logs) == 1
        recovered = recommend(client, endpoint, route, "hybrid")
        assert_execution(recovered, "hybrid", [route])
        assert_current_facts(client, recovered)
        assert len(endpoint.logs) > 1
    captured = capsys.readouterr()
    assert TEST_KEY not in caplog.text + captured.out + captured.err
    assert TEST_KEY not in json.dumps(endpoint.metadata)


@pytest.mark.parametrize("fault, reason", [(401, "HTTP 401"), (429, "HTTP 429"), (500, "HTTP 500"),
                                           ("timeout", "timed out"), ("dimension", "1024")])
def test_query_failure_recovers_next_request_without_rebuild(endpoint, fault, reason):
    with client_for(endpoint, embedding_timeout_seconds=.05 if fault == "timeout" else 2) as client:
        recommend(client, endpoint, "mixed", "vector")
        builds = [log for log in endpoint.logs if log["phase"] == "index"]
        endpoint.target, endpoint.fault, endpoint.once = "query", fault, True
        body = recommend(client, endpoint, "product", "vector")
        assert_execution(body, "vector", ["product"], fallback=reason)
        assert_current_facts(client, body)
        body = recommend(client, endpoint, "product", "vector")
        assert_execution(body, "vector", ["product"])
        assert [log for log in endpoint.logs if log["phase"] == "index"] == builds


def test_mixed_reports_partial_failure_per_route(endpoint):
    endpoint.fault, endpoint.once = 429, True
    with client_for(endpoint) as client:
        body = recommend(client, endpoint, "mixed", "hybrid")
        diagnostics = body["retrieval_diagnostics"]
        assert sorted(value["actual_mode"] for value in diagnostics.values()) == ["bm25", "hybrid"]
        for route, diagnostic in diagnostics.items():
            assert diagnostic["actual_mode"] == body["effective_retrieval_modes"][route]
            if diagnostic["actual_mode"] == "bm25":
                assert "HTTP 429" in diagnostic["fallback_reason"] and not diagnostic["embedding_used"]
            else:
                assert diagnostic["embedding_used"] and diagnostic["actual_provider"] == "bailian"
        assert_current_facts(client, body)


@pytest.mark.parametrize("route", ["product", "faq"])
def test_tool_timeout_never_claims_cloud_success(endpoint, route):
    with client_for(endpoint, tool_timeout_seconds=.2, embedding_timeout_seconds=1) as client:
        recommend(client, endpoint, route, "vector")  # warm index, normal transport
        endpoint.target, endpoint.fault, endpoint.delay = "query", "timeout", .5
        body = recommend(client, endpoint, route, "vector")
        diagnostic = body["retrieval_diagnostics"][route]
        assert diagnostic["status"] == "failed"
        assert diagnostic["actual_provider"] is None
        assert diagnostic["embedding_used"] is False
        assert diagnostic["actual_mode"] in (None, "bm25")
        assert "TimeoutError" in diagnostic["fallback_reason"]
        assert any("TimeoutError" in warning for warning in body["warnings"])
        assert_current_facts(client, body)
        # The cancelled wait cannot cancel a network thread. Its late completion
        # must neither mutate this response nor contaminate another request.
        time.sleep(.55)
        endpoint.fault = None
        recovered = recommend(client, endpoint, route, "vector")
        assert_execution(recovered, "vector", [route])
        assert body["retrieval_diagnostics"][route]["status"] == "failed"


def test_concurrent_different_modes_keep_request_local_diagnostics(endpoint):
    with client_for(endpoint) as client:
        tasks = [("product", "vector"), ("faq", "bm25"), ("mixed", "hybrid")]
        with ThreadPoolExecutor(max_workers=3) as pool:
            bodies = list(pool.map(lambda task: recommend(client, endpoint, *task), tasks))
        for (route, mode), body in zip(tasks, bodies):
            routes = ["product", "faq"] if route == "mixed" else [route]
            assert body["requested_retrieval_mode"] == mode
            assert body["effective_retrieval_modes"] == {key: mode for key in routes}, json.dumps(body, ensure_ascii=False)
            for diagnostic in body["retrieval_diagnostics"].values():
                assert diagnostic["requested_mode"] == diagnostic["actual_mode"] == mode
                assert diagnostic["actual_provider"] == ("bm25" if mode == "bm25" else "bailian")
                assert diagnostic["embedding_used"] is (mode != "bm25")
        assert sum(log["phase"] == "query" for log in endpoint.logs) == 3


def test_updated_and_deleted_sources_discard_old_vectors(endpoint, tmp_path):
    with client_for(endpoint) as client:
        recommend(client, endpoint, "mixed", "hybrid")
        service = client.app.state.service
        old_indexes = (service.retriever, service.faq_retriever)
        old_product = service.store.get_product_description_document("DEMO-H02")
        old_faq = next(doc for doc in service.store.list_documents() if doc.faq_id == "DEMO-FAQ-01")
        data = Path(__file__).parents[1] / "shopping_agent/data"
        product = next(json.loads(line) for line in (data / "demo_products.jsonl").read_text().splitlines()
                       if json.loads(line)["product_id"] == "DEMO-H02")
        product.update(price=123, stock=7, description=product["description"] + " 合成更新批次乙。")
        faq = json.loads((data / "demo_faq.jsonl").read_text().splitlines()[0])
        faq["answer"] = "不是。这是合成更新批次乙，禁止真实交易。"
        batch = tmp_path / "authorized-synthetic-update.jsonl"
        batch.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in (product, faq)) + "\n")
        service.store.import_documents_jsonl(batch)
        count = len(endpoint.logs)
        bm25 = recommend(client, endpoint, "mixed", "bm25")
        assert len(endpoint.logs) == count
        assert service.retriever is not old_indexes[0] and service.faq_retriever is not old_indexes[1]
        assert service.retriever.vector is None and service.faq_retriever.vector is None
        assert service.store.get_document(old_product.source_id) is None
        assert service.store.get_document(old_faq.source_id) is None
        body = recommend(client, endpoint, "mixed", "hybrid")
        assert_execution(body, "hybrid", ["product", "faq"])
        assert_current_facts(client, body)
        changed = next(item for item in body["recommendations"] if item["product_id"] == "DEMO-H02")
        assert changed["price"] == 123 and changed["stock"] == 7
        fresh_sources = [text for log in endpoint.logs[count:] if log["phase"] == "index" for text in log["input"]]
        assert any("合成更新批次乙" in text for text in fresh_sources)
        assert "合成更新批次乙" in body["answer"]
        for response in (bm25, body):
            refs = response["knowledge_evidence"] + [ref for item in response["recommendations"] for ref in item["evidence"]]
            assert old_product.source_id not in {ref["source_id"] for ref in refs}
            assert old_faq.source_id not in {ref["source_id"] for ref in refs}
        assert service.store.delete_faq("DEMO-FAQ-01")
        deleted = recommend(client, endpoint, "faq", "vector")
        assert all(not ref["source_id"].startswith("DEMO-FAQ-01:") for ref in deleted["knowledge_evidence"])
        assert "合成更新批次乙" not in deleted["answer"]


@pytest.mark.parametrize("query", ["推荐会飞的手机", "推荐耳机，预算1元", "推荐主动降噪耳机，必须没有主动降噪"])
def test_no_answer_constraints_never_create_recommendations(endpoint, query):
    endpoint.fault = 500
    with client_for(endpoint) as client:
        body = recommend(client, endpoint, query=query)
        assert body["recommendations"] == []
        assert body["retrieval_diagnostics"]["product"]["embedding_used"] is False
        assert endpoint.logs == []


def test_stock_and_explicit_negative_constraints_survive_cloud_failure(endpoint):
    endpoint.fault = 500
    with client_for(endpoint) as client:
        body = recommend(client, endpoint, query="推荐没有主动降噪的耳机，预算500元")
        assert body["recommendations"]
        assert_current_facts(client, body)
        assert all(item["product_id"] != "DEMO-H05" for item in body["recommendations"])
        assert all(any(judgment["status"] == "supported" for judgment in item["condition_judgments"])
                   for item in body["recommendations"])
        assert_execution(body, "vector", ["product"], fallback="HTTP 500")
