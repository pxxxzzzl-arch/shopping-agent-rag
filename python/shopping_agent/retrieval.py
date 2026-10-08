"""Dependency-free lexical retrieval for product knowledge and review evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from http.client import HTTPConnection
import json
import math
import re
import threading
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit


_WORD_RE = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]+")


def _terms(text: str) -> Counter[str]:
    """Index English words and both Chinese characters and adjacent pairs.

    Chinese bigrams distinguish phrases such as ``降噪`` from documents that
    happen to contain the same individual characters in unrelated places.
    Characters also allow a query to match text with spaces between words.
    """
    terms: Counter[str] = Counter()
    for match in _WORD_RE.finditer(text.lower()):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff":
            terms.update(f"zh1:{character}" for character in word)
            terms.update(
                f"zh2:{word[index:index + 2]}"
                for index in range(len(word) - 1)
            )
        else:
            terms[f"en:{word}"] += 1
    return terms


@dataclass(frozen=True)
class EvidenceDocument:
    source_id: str
    product_id: str | None
    source_type: str
    text: str
    source_url: str = ""
    license: str = ""
    updated_at: str = ""
    version: int = 1
    chunk_index: int = 0


@dataclass(frozen=True)
class RetrievalHit:
    document: EvidenceDocument
    score: float


def chunk_documents(
    documents: Sequence[EvidenceDocument], max_chars: int = 320
) -> list[EvidenceDocument]:
    """Split long sources without changing the ID that resolves to the original."""
    if max_chars < 40:
        raise ValueError("max_chars must be at least 40")
    chunks: list[EvidenceDocument] = []
    from dataclasses import replace

    for document in documents:
        text = " ".join(document.text.split())
        if not text:
            continue
        if len(text) <= max_chars:
            chunks.append(document)
            continue
        pieces = re.split(r"(?<=[。！？.!?；;])", text)
        current = ""
        part_index = 0
        for piece in pieces:
            while len(piece) > max_chars:
                if current:
                    chunks.append(replace(document, text=current, chunk_index=part_index))
                    part_index += 1
                    current = ""
                chunks.append(replace(document, text=piece[:max_chars], chunk_index=part_index))
                part_index += 1
                piece = piece[max_chars:]
            if len(current) + len(piece) > max_chars and current:
                chunks.append(replace(document, text=current, chunk_index=part_index))
                part_index += 1
                current = ""
            current += piece
        if current:
            chunks.append(replace(document, text=current, chunk_index=part_index))
    return chunks


class EmbeddingAdapter(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashEmbedding:
    """Offline comparison surrogate; never described as a semantic model."""

    name = "hash-surrogate"

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        vectors: list[list[float]] = []
        for value in texts:
            vector = [0.0] * self.dimensions
            for term, count in _terms(value).items():
                digest = hashlib.sha256(term.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1 if digest[4] % 2 else -1
                vector[index] += count * sign
            vectors.append(vector)
        return vectors


class OllamaEmbedding:
    """Real embedding through a locally hosted Ollama /api/embed endpoint.

    The caller explicitly supplies the model and endpoint. No request is made
    during import, and the default service uses the offline surrogate.
    """

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout: float = 8):
        if not model.strip():
            raise ValueError("Ollama embedding model is required")
        parsed = urlsplit(base_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Ollama embedding endpoint needs a valid local port") from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama embedding endpoint must be local")
        self.model = model
        self.base_url = f"http://{parsed.hostname}:{port}"
        self.host = parsed.hostname
        self.port = port
        self.timeout = timeout
        self.name = f"ollama:{model}"
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        # A direct connection does not honor proxy settings or follow redirects
        # from a local process to an external host.
        connection = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request(
                "POST", "/api/embed", body=payload,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"Embedding endpoint returned HTTP {response.status}")
            decoded = json.loads(response.read())
        finally:
            connection.close()
        self.calls += 1
        vectors = decoded.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise ValueError("Embedding response has the wrong count")
        if any(not isinstance(row, list) or not row or not all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in row
        ) for row in vectors):
            raise ValueError("Embedding response contains invalid vectors")
        if len({len(row) for row in vectors}) != 1:
            raise ValueError("Embedding dimensions differ")
        return [[float(value) for value in row] for row in vectors]


def _normalized(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    return tuple(value / norm for value in vector) if norm else tuple(0.0 for _ in vector)


class VectorRetriever:
    """Exact cosine index of source chunks; rebuilt after document changes."""

    def __init__(self, documents: Sequence[EvidenceDocument], embedding: EmbeddingAdapter):
        self.documents = tuple(documents)
        self.embedding = embedding
        vectors = embedding.embed([document.text for document in documents]) if documents else []
        if len(vectors) != len(documents):
            raise ValueError("Embedding adapter returned the wrong count")
        if vectors and len({len(vector) for vector in vectors}) != 1:
            raise ValueError("Embedding dimensions differ")
        self.vectors = tuple(_normalized(vector) for vector in vectors)

    def search(
        self, query: str, product_ids: set[str] | None = None, limit: int = 5
    ) -> list[RetrievalHit]:
        if not query.strip() or limit <= 0 or not self.documents:
            return []
        vector = _normalized(self.embedding.embed([query])[0])
        if len(vector) != len(self.vectors[0]):
            raise ValueError("Query embedding dimension changed")
        scored = []
        for index, (document, stored) in enumerate(zip(self.documents, self.vectors)):
            if product_ids is not None and document.product_id not in product_ids:
                continue
            score = sum(left * right for left, right in zip(vector, stored))
            if score > 0:
                scored.append((score, index, document))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [RetrievalHit(document, score) for score, _, document in scored[:limit]]


@dataclass(frozen=True)
class SearchResult:
    hits: list[RetrievalHit]
    requested_mode: str
    actual_mode: str
    fallback_reason: str | None = None


class EvidenceIndex:
    """Switch BM25, vector and reciprocal-rank hybrid with source deduplication."""

    def __init__(
        self, documents: Sequence[EvidenceDocument],
        embedding: EmbeddingAdapter | None = None,
        *, lazy_vector: bool = False,
        on_search: Callable[[SearchResult, EvidenceIndex], None] | None = None,
    ):
        self.calls = 0
        self.documents = tuple(chunk_documents(documents))
        self.sparse = SparseRetriever(self.documents)
        self.embedding = embedding or HashEmbedding()
        self.vector: VectorRetriever | None = None
        self.vector_error: str | None = None
        self._vector_attempted = False
        self._vector_lock = threading.Lock()
        self.on_search = on_search
        if not lazy_vector:
            self._ensure_vector()

    def _ensure_vector(self) -> None:
        # Cache a failed build too. Recovery is an explicit new index/snapshot,
        # rather than charging every subsequent request for another failed build.
        with self._vector_lock:
            if self._vector_attempted:
                return
            self._vector_attempted = True
            try:
                self.vector = VectorRetriever(self.documents, self.embedding)
            except Exception as exc:
                self.vector_error = self._safe_error(exc)

    def _safe_error(self, exc: Exception) -> str:
        # Shared cloud transport deliberately produces credential-free errors.
        # Other/custom adapters remain type-only, preserving the old boundary.
        return str(exc) if getattr(self.embedding, "safe_errors", False) else type(exc).__name__

    @staticmethod
    def _deduplicate(hits: Sequence[RetrievalHit], limit: int) -> list[RetrievalHit]:
        result: list[RetrievalHit] = []
        seen: set[str] = set()
        for hit in hits:
            source_id = hit.document.source_id
            if source_id in seen:
                continue
            seen.add(source_id)
            result.append(hit)
            if len(result) >= limit:
                break
        return result

    def search(
        self, query: str, product_ids: set[str] | None = None,
        limit: int = 5, mode: str = "bm25",
    ) -> SearchResult:
        result = self._search(query, product_ids, limit, mode)
        if self.on_search is not None:
            self.on_search(result, self)
        return result

    def _search(
        self, query: str, product_ids: set[str] | None = None,
        limit: int = 5, mode: str = "bm25",
    ) -> SearchResult:
        self.calls += 1
        if mode not in {"bm25", "vector", "hybrid"}:
            raise ValueError(f"Unknown retrieval mode: {mode}")
        sparse = self.sparse.search(query, product_ids, max(limit * 4, 20))
        if mode == "bm25":
            return SearchResult(self._deduplicate(sparse, limit), mode, "bm25")
        self._ensure_vector()
        if self.vector is None:
            return SearchResult(
                self._deduplicate(sparse, limit), mode, "bm25",
                f"vector index unavailable: {self.vector_error}",
            )
        try:
            vector = self.vector.search(query, product_ids, max(limit * 4, 20))
        except Exception as exc:
            return SearchResult(
                self._deduplicate(sparse, limit), mode, "bm25",
                f"vector retrieval failed: {self._safe_error(exc)}",
            )
        if mode == "vector":
            return SearchResult(self._deduplicate(vector, limit), mode, "vector")
        fused: dict[str, tuple[float, RetrievalHit]] = {}
        for ranking in (sparse, vector):
            for rank, hit in enumerate(ranking, start=1):
                key = hit.document.source_id
                score, old_hit = fused.get(key, (0.0, hit))
                fused[key] = (score + 1 / (60 + rank), old_hit)
        ordered = [
            RetrievalHit(hit.document, score)
            for score, hit in sorted(fused.values(), key=lambda row: (-row[0], row[1].document.source_id))
        ]
        return SearchResult(ordered[:limit], mode, "hybrid")


class SparseRetriever:
    """Rank evidence by BM25, with deterministic tie ordering.

    The corpus is indexed at construction time. Search filters by product ID
    when supplied, and never returns documents with no lexical overlap.
    """

    def __init__(self, documents: Sequence[EvidenceDocument]) -> None:
        self._documents = tuple(documents)
        self._term_counts = tuple(_terms(doc.text) for doc in self._documents)
        self._lengths = tuple(sum(counts.values()) for counts in self._term_counts)
        self._average_length = (
            sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for counts in self._term_counts:
            self._document_frequency.update(counts.keys())

    def search(
        self,
        query: str,
        product_ids: set[str] | None = None,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        if limit <= 0 or not self._documents or not self._average_length:
            return []

        query_terms = _terms(query)
        if not query_terms:
            return []

        n_documents = len(self._documents)
        k1 = 1.5
        b = 0.75
        scored: list[tuple[float, int, EvidenceDocument]] = []
        for index, (document, counts, length) in enumerate(
            zip(self._documents, self._term_counts, self._lengths)
        ):
            if product_ids is not None and document.product_id not in product_ids:
                continue

            score = 0.0
            length_normalizer = k1 * (
                1 - b + b * length / self._average_length
            )
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                inverse_frequency = math.log1p(
                    (n_documents - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                phrase_weight = 2.0 if term.startswith("zh2:") else 1.0
                score += (
                    phrase_weight
                    * inverse_frequency
                    * frequency
                    * (k1 + 1)
                    / (frequency + length_normalizer)
                )

            if score > 0:
                scored.append((score, index, document))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            RetrievalHit(document=document, score=score)
            for score, _, document in scored[:limit]
        ]
