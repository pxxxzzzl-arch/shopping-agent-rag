"""Tests for bilingual product evidence retrieval."""

from shopping_agent.retrieval import EvidenceDocument, SparseRetriever


def _document(source_id: str, product_id: str, text: str) -> EvidenceDocument:
    return EvidenceDocument(source_id, product_id, "review", text)


def test_chinese_phrase_ranks_relevant_product_first() -> None:
    retriever = SparseRetriever(
        [
            _document("review-1", "headphones", "主动降噪耳机，适合地铁通勤"),
            _document("review-2", "stand", "耳机支架，桌面收纳方便"),
            _document("review-3", "mouse", "无线鼠标，适合办公"),
        ]
    )

    hits = retriever.search("降噪耳机")

    assert hits[0].document.source_id == "review-1"
    assert hits[0].score > hits[1].score > 0
    assert all(hit.document.product_id != "mouse" for hit in hits)


def test_english_words_are_case_insensitive() -> None:
    retriever = SparseRetriever(
        [
            _document("review-1", "a", "Bluetooth headphones with noise cancellation"),
            _document("review-2", "b", "Mechanical keyboard for gaming"),
        ]
    )

    hits = retriever.search("BLUETOOTH headphones")

    assert [hit.document.source_id for hit in hits] == ["review-1"]


def test_product_filter_applies_before_returning_hits() -> None:
    retriever = SparseRetriever(
        [
            _document("review-1", "a", "降噪耳机"),
            _document("review-2", "b", "降噪耳机"),
        ]
    )

    assert [hit.document.product_id for hit in retriever.search("降噪", {"b"})] == [
        "b"
    ]
    assert retriever.search("降噪", set()) == []


def test_empty_queries_corpus_and_non_matches_return_nothing() -> None:
    document = _document("review-1", "a", "蓝牙耳机")
    retriever = SparseRetriever([document])

    assert SparseRetriever([]).search("蓝牙") == []
    assert SparseRetriever([_document("empty", "b", "")]).search("蓝牙") == []
    assert retriever.search("") == []
    assert retriever.search("!!!") == []
    assert retriever.search("keyboard") == []
    assert retriever.search("蓝牙", limit=0) == []


def test_limit_and_ties_follow_input_order() -> None:
    retriever = SparseRetriever(
        [
            _document("first", "a", "portable charger"),
            _document("second", "b", "portable charger"),
        ]
    )

    assert [hit.document.source_id for hit in retriever.search("charger", limit=1)] == [
        "first"
    ]
    assert [hit.document.source_id for hit in retriever.search("charger")] == [
        "first",
        "second",
    ]
