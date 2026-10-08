"""Three retrieval modes, source provenance and independent offline comparison."""

from __future__ import annotations

import asyncio

from shopping_agent.config import Settings
from shopping_agent.evaluation import evaluate_retrieval_modes
from shopping_agent.retrieval import (
    EvidenceDocument, EvidenceIndex, OllamaEmbedding, chunk_documents,
)
from shopping_agent.workflow import create_service


class FixedEmbedding:
    name = "fixed-test-embedding"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [
            [1.0, 0.0] if "B语义" in text or text == "轻便"
            else [0.0, 1.0]
            for text in texts
        ]


def test_bm25_vector_and_hybrid_execute_distinct_rankings():
    documents = [
        EvidenceDocument("A:description", "A", "description", "轻便随身"),
        EvidenceDocument("B:description", "B", "description", "B语义远途"),
    ]
    index = EvidenceIndex(documents, FixedEmbedding())
    lexical = index.search("轻便", mode="bm25")
    vector = index.search("轻便", mode="vector")
    hybrid = index.search("轻便", mode="hybrid")
    assert [hit.document.source_id for hit in lexical.hits] == ["A:description"]
    assert [hit.document.source_id for hit in vector.hits] == ["B:description"]
    assert {hit.document.source_id for hit in hybrid.hits} == {
        "A:description", "B:description"
    }
    assert (lexical.actual_mode, vector.actual_mode, hybrid.actual_mode) == (
        "bm25", "vector", "hybrid"
    )


def test_long_sources_are_chunked_and_deduplicated_by_current_source_id():
    source = EvidenceDocument(
        "DOC:description", "DOC", "description",
        "第一段轻便。" * 50 + "第二段耐用。" * 50,
        version=3,
    )
    pieces = chunk_documents([source], max_chars=80)
    assert len(pieces) > 2
    assert all(piece.source_id == source.source_id and piece.version == 3 for piece in pieces)
    hits = EvidenceIndex([source]).search("轻便", mode="bm25", limit=5).hits
    assert len(hits) == 1
    assert hits[0].document.source_id == source.source_id


def test_vector_failure_falls_back_to_bm25_without_losing_provenance():
    class FailingEmbedding:
        name = "failing-test-embedding"

        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("vector endpoint timed out")
            return [[1.0, 0.0] for _ in texts]

    index = EvidenceIndex(
        [EvidenceDocument("P:description", "P", "description", "防汗运动耳机")],
        FailingEmbedding(),
    )
    result = index.search("防汗", mode="vector")
    assert result.actual_mode == "bm25"
    assert result.hits[0].document.source_id == "P:description"
    assert "TimeoutError" in result.fallback_reason


def test_real_embedding_adapter_requires_explicit_local_model():
    adapter = OllamaEmbedding("nomic-embed-text", "http://127.0.0.1:11434")
    assert adapter.name == "ollama:nomic-embed-text"
    assert adapter.calls == 0
    try:
        OllamaEmbedding("nomic-embed-text", "https://example.org")
    except ValueError as exc:
        assert "local" in str(exc)
    else:
        raise AssertionError("A remote embedding endpoint was accepted")


def test_independent_labels_report_real_recall_support_latency_and_calls():
    service = create_service(Settings(database_url="sqlite://", seed_demo=True))
    report = asyncio.run(evaluate_retrieval_modes(service))
    assert report["label_count"] == 32
    assert len(report["label_sha256"]) == 64
    assert report["embedding_provider"] == "hash-surrogate"
    assert set(report["modes"]) == {"bm25", "vector", "hybrid"}
    for mode, result in report["modes"].items():
        assert result["answerable_cases"] == 29
        assert result["unanswerable_cases"] == 3
        assert result["recall_at_5"] >= 0.9
        assert 0 < result["evidence_support_rate"] < 1
        assert result["supported_citations"] < result["retrieved_citations"]
        assert result["search_latency_p50_ms"] >= 0
        assert result["no_answer_safe_rate"] == 1
        assert result["model_call_count"] == 0
        assert result["external_api_cost_usd"] == 0
        if mode == "bm25":
            assert result["embedding_call_count"] == 0
        else:
            assert result["embedding_call_count"] > 0
    assert report["modes"]["vector"]["embedding_call_count"] > 0
