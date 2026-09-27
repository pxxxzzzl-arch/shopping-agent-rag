"""Independent regressions for acceptance findings outside the original cases."""

from __future__ import annotations

import asyncio
import json

import pytest

from shopping_agent.config import Settings
from shopping_agent.retrieval import OllamaEmbedding
from shopping_agent.schemas import ShopRequest
from shopping_agent.workflow import create_service


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434@evil.example",
        "http://localhost:11434@evil.example",
        "http://localhost.evil.example:11434",
        "http://127.0.0.1:11434/redirect",
        "http://127.0.0.1:11434#remote",
    ],
)
def test_embedding_endpoint_rejects_nonlocal_authority(url: str) -> None:
    with pytest.raises(ValueError, match="local"):
        OllamaEmbedding("local-model", url)
    assert OllamaEmbedding("local-model", "http://127.0.0.1:11434").port == 11434


def test_embedding_endpoint_does_not_follow_redirects(monkeypatch) -> None:
    class RedirectResponse:
        status = 302

        def read(self):
            return b""

    class LocalConnection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 11434, 8)

        def request(self, method, path, body, headers):
            assert method == "POST" and path == "/api/embed"
            assert b"local-data" in body

        def getresponse(self):
            return RedirectResponse()

        def close(self):
            pass

    monkeypatch.setattr("shopping_agent.retrieval.HTTPConnection", LocalConnection)
    with pytest.raises(ValueError, match="HTTP 302"):
        OllamaEmbedding("local-model").embed(["local-data"])


def test_embedding_endpoint_reads_local_response(monkeypatch) -> None:
    class SuccessResponse:
        status = 200

        def read(self):
            return b'{"embeddings":[[1.0,0.0]]}'

    class LocalConnection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("127.0.0.1", 11434)

        def request(self, method, path, body, headers):
            assert method == "POST" and path == "/api/embed"
            assert b"local-data" in body

        def getresponse(self):
            return SuccessResponse()

        def close(self):
            pass

    monkeypatch.setattr("shopping_agent.retrieval.HTTPConnection", LocalConnection)
    assert OllamaEmbedding("local-model").embed(["local-data"]) == [[1.0, 0.0]]


def test_followup_with_category_keeps_the_previous_product() -> None:
    service = create_service(Settings(database_url="sqlite://"))
    first = asyncio.run(service.recommend(ShopRequest(
        user_id="buyer", conversation_id="one", query="推荐一款预算100元的耳机", num_items=1,
    )))
    followup = asyncio.run(service.recommend(ShopRequest(
        user_id="buyer", conversation_id="one", query="这款耳机还有货吗？", num_items=1,
    )))
    assert [item.product_id for item in first.recommendations] == ["DEMO-H01"]
    assert [item.product_id for item in followup.recommendations] == ["DEMO-H01"]


def test_explicit_unverified_use_scenario_abstains() -> None:
    service = create_service(Settings(database_url="sqlite://"))
    for query in ("推荐适合火星旅行的耳机", "推荐适合火星旅行的平板"):
        response = asyncio.run(service.recommend(ShopRequest(
            user_id="buyer", query=query, num_items=2,
        )))
        assert response.recommendations == [], query
    ordinary = asyncio.run(service.recommend(ShopRequest(
        user_id="buyer", query="推荐适合长期打字的键盘", num_items=3,
    )))
    assert ordinary.recommendations
    assert all(item.category == "键盘" for item in ordinary.recommendations)


def test_index_refresh_retries_if_import_commits_between_reads(tmp_path, monkeypatch) -> None:
    service = create_service(Settings(
        database_url=f"sqlite:///{tmp_path / 'catalog.sqlite3'}", seed_demo=False,
    ))
    record = {
        "kind": "faq", "faq_id": "NEW-FAQ", "question": "月球配送多久？",
        "answer": "月球配送暂无保证。", "data_origin": "synthetic_demo",
    }
    document_path = tmp_path / "faq.jsonl"
    document_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    original_list = service.store.list_documents
    injected = False

    def raced_list():
        nonlocal injected
        snapshot = original_list()
        if not injected:
            injected = True
            service.store.import_documents_jsonl(document_path)
        return snapshot

    monkeypatch.setattr(service.store, "list_documents", raced_list)
    service.refresh_index()
    assert service._indexed_revisions == service.store.document_revision_signature()
    assert any(
        document.source_id == "NEW-FAQ:answer"
        for document in service.faq_retriever.documents
    )
    response = asyncio.run(service.recommend(ShopRequest(
        user_id="buyer", query="月球配送多久？",
    )))
    assert [ref.source_id for ref in response.knowledge_evidence] == ["NEW-FAQ:answer"]
