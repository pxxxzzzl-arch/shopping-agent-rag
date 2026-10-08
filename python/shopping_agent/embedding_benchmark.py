"""Reproducible retrieval comparison; never feeds relevance labels to retrieval.

The production EvidenceIndex and OllamaEmbedding implementations are reused.
Only vector validation, measurement and scoring live here; no ranking rules do.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from http.client import HTTPConnection
import json
import math
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from typing import Sequence

from .evaluation import load_retrieval_labels, _percentile
from .retrieval import EvidenceDocument, EvidenceIndex, HashEmbedding, OllamaEmbedding
from .seed import DEMO_PRODUCTS_PATH, DEMO_FAQ_PATH, seed_demo_data, seed_demo_faq_data
from .storage import CatalogStore

from .embeddings import BAILIAN_BASE_URL, BAILIAN_MODEL, BailianEmbedding

LABELS = Path(__file__).parent / 'data/retrieval_labels.jsonl'


def query_inputs(labels: list[dict]) -> list[dict]:
    """Strip every gold field before the search phase, including topic routing."""
    return [{'case_id': row['case_id'], 'query': row['query']} for row in labels]


def score_rankings(rankings: list[dict], labels: list[dict]) -> dict:
    """Case-hit Recall@5 and first-relevant-source MRR@5, as exact fractions."""
    by_id = {row['case_id']: row for row in rankings}
    if (len(by_id) != len(rankings) or len({row['case_id'] for row in labels}) != len(labels)
            or set(by_id) != {row['case_id'] for row in labels}):
        raise ValueError('Rankings and labels must contain the same unique cases')
    hits = 0
    reciprocal = Fraction(0)
    answerable = 0
    no_answer = []
    for label in labels:
        row = by_id[label['case_id']]
        sources = row['top5_source_ids'][:5]
        if label['answerability'] == 'unanswerable':
            no_answer.append({'case_id': label['case_id'], 'returned_count': len(sources),
                              'top5_source_ids': sources})
            continue
        if label['answerability'] != 'answerable' or not label['relevant_source_ids']:
            raise ValueError('Answerable cases need gold sources')
        answerable += 1
        rank = next((index for index, source in enumerate(sources, 1)
                     if source in set(label['relevant_source_ids'])), None)
        if rank is not None:
            hits += 1
            reciprocal += Fraction(1, rank)
    return {
        'recall_at_5': {'numerator': hits, 'denominator': answerable,
                        'value': hits / answerable if answerable else None},
        'mrr_at_5': {'numerator': float(reciprocal), 'numerator_fraction': str(reciprocal),
                     'denominator': answerable,
                     'value': float(reciprocal / answerable) if answerable else None},
        'unanswerable_returns': no_answer,
    }


class MeasuredEmbedding:
    """Measure actual adapter calls and reject corrupt or changing vectors."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.name = adapter.name
        self.attempts = 0
        self.successes = 0
        self.text_count = 0
        self.dimensions = None
        self.last_error = None

    def embed(self, texts):
        self.attempts += 1
        self.text_count += len(texts)
        try:
            vectors = self.adapter.embed(texts)
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError('Wrong vector count')
            for vector in vectors:
                if (not isinstance(vector, list) or not vector or any(
                    type(value) not in (float, int) or not math.isfinite(value)
                    for value in vector
                )):
                    raise ValueError('Non-finite, boolean or empty vector')
                norm = math.hypot(*vector)
                squared_norm = sum(value * value for value in vector)
                if not math.isfinite(norm) or norm == 0 or not math.isfinite(squared_norm) or squared_norm == 0:
                    raise ValueError('Zero or overflowing vector norm')
                if self.dimensions is None:
                    self.dimensions = len(vector)
                if len(vector) != self.dimensions:
                    raise ValueError('Embedding dimensions changed')
            self.successes += 1
            return vectors
        except Exception as exc:
            self.last_error = f'{type(exc).__name__}: {exc}'
            raise

    def counts(self):
        if isinstance(self.adapter, BailianEmbedding):
            return {'attempted_calls': self.adapter.http_attempts,
                    'successful_calls': self.adapter.http_successes,
                    'input_texts': self.adapter.http_input_texts,
                    'adapter_attempted_calls': self.attempts, 'adapter_successful_calls': self.successes}
        return {'attempted_calls': self.attempts, 'successful_calls': self.successes,
                'input_texts': self.text_count}


def retrieve_queries(index: EvidenceIndex, queries: list[dict], mode: str, embedding) -> list[dict]:
    """Search sees only case IDs and original questions; relevance is scored later."""
    rows = []
    for query in queries:
        before = embedding.attempts
        started = perf_counter()
        found = index.search(query['query'], limit=5, mode=mode)
        rows.append({
            **query, 'requested_mode': mode, 'actual_mode': found.actual_mode,
            'fallback_reason': found.fallback_reason,
            'latency_ms': (perf_counter() - started) * 1000,
            'embedding_attempts': embedding.attempts - before,
            'top5_source_ids': [hit.document.source_id for hit in found.hits],
            'top5_scores': [hit.score for hit in found.hits],
        })
        if found.actual_mode != mode or found.fallback_reason:
            break
    return rows


def run_strategy(documents, queries, mode, adapter, provider, model=None) -> dict:
    embedding = MeasuredEmbedding(adapter)
    result = {
        'status': 'failed', 'retrieval_provider': 'bm25' if mode == 'bm25' else provider,
        'embedding_provider': provider, 'model': model,
        'requested_mode': mode, 'actual_mode_counts': None, 'fallback_count': None,
        'vector_dimensions': None, 'index_build_ms': None,
        'query_p50_ms': None, 'query_p95_ms': None,
        'recall_at_5': None, 'mrr_at_5': None, 'unanswerable_returns': None,
        'embedding_calls': None, 'index_embedding_calls': None, 'query_embedding_calls': None,
        'cases': [], 'error': None,
    }
    started = perf_counter()
    index = EvidenceIndex(documents, embedding)
    elapsed = (perf_counter() - started) * 1000
    result['index_embedding_calls'] = embedding.counts()
    result['index_attempt_ms'] = elapsed
    if index.vector is None:
        result.update(error=embedding.last_error or f'Vector index failed: {index.vector_error}',
                      failure_stage='index', embedding_calls=embedding.counts())
        return result
    result.update(index_build_ms=elapsed, vector_dimensions=embedding.dimensions)
    before_counts = embedding.counts()
    rows = retrieve_queries(index, queries, mode, embedding)
    result['cases'] = rows
    result['embedding_calls'] = embedding.counts()
    result['query_embedding_calls'] = {key: value - before_counts[key]
                                       for key, value in embedding.counts().items()}
    actual = {}
    for row in rows:
        actual[row['actual_mode']] = actual.get(row['actual_mode'], 0) + 1
    result['actual_mode_counts'] = actual
    result['fallback_count'] = sum(row['actual_mode'] != mode or bool(row['fallback_reason']) for row in rows)
    if result['fallback_count'] or len(rows) != len(queries):
        result.update(error=embedding.last_error or 'Retrieval fell back or did not finish', failure_stage='query')
        return result
    result.update(status='success', query_p50_ms=_percentile([row['latency_ms'] for row in rows], .5),
                  query_p95_ms=_percentile([row['latency_ms'] for row in rows], .95))
    return result


def _request(adapter, method, path, payload=None):
    connection = HTTPConnection(adapter.host, adapter.port, timeout=adapter.timeout)
    try:
        connection.request(method, path, body=json.dumps(payload).encode() if payload is not None else None,
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status != 200 or not isinstance(data, dict):
            raise ValueError(f'{path} HTTP {response.status}: {data}')
        return data
    finally:
        connection.close()


def local_model_metadata(adapter: OllamaEmbedding) -> dict:
    """Only query the adapter's already validated loopback endpoint; never pull."""
    tags = _request(adapter, 'GET', '/api/tags')
    entry = next((row for row in tags.get('models', []) if row.get('name') in {
        adapter.model, adapter.model + ':latest'
    } or row.get('model') == adapter.model), None)
    if entry is None:
        raise ValueError('Requested model is not installed locally; no download attempted')
    if not isinstance(entry.get('size'), int) or entry['size'] <= 0 or not entry.get('digest'):
        raise ValueError('Local model has no installed weights/digest; remote models are not permitted')
    show = _request(adapter, 'POST', '/api/show', {'model': adapter.model})
    if show.get('remote_host') or show.get('remote_model'):
        raise ValueError('Remote model inference is not permitted')
    try:
        version = _request(adapter, 'GET', '/api/version').get('version')
        version_error = None
    except Exception as exc:
        version, version_error = None, f'{type(exc).__name__}: {exc}'
    return {'provider': 'ollama', 'requested_model': adapter.model,
            'installed_model': entry['name'], 'digest': entry['digest'],
            'size_bytes': entry['size'], 'modified_at': entry.get('modified_at'),
            'capabilities': show.get('capabilities'), 'details': entry.get('details'),
            'model_info': show.get('model_info'), 'server_version': version,
            'server_version_error': version_error}


def _hash_files(paths) -> dict:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def run_benchmark(model: str, base_url: str, timeout: float = 30, *,
                  provider: str = 'ollama', api_key: str = '', allow_remote: bool = False) -> dict:
    if provider not in {'ollama', 'bailian'}:
        raise ValueError('Unknown embedding provider')
    # Configuration errors preserve the two offline baselines without network IO.
    adapter = None
    configuration_error = None
    try:
        adapter = (OllamaEmbedding(model, base_url, timeout) if provider == 'ollama'
                   else BailianEmbedding(model, base_url, api_key, timeout, allow_remote=allow_remote))
    except ValueError as exc:
        configuration_error = str(exc)
    store = CatalogStore('sqlite://')
    try:
        seed_demo_data(store)
        seed_demo_faq_data(store)
        if len(store.list_products(in_stock_only=False)) != 30 or len(store.list_faqs()) != 6:
            raise ValueError('Bundled synthetic catalog does not have 30 products and 6 FAQs')
        documents = [EvidenceDocument(
            source_id=source.source_id, product_id=source.product_id, source_type=source.source_type,
            text=source.text, license=source.license, version=source.version,
        ) for source in store.list_documents()]
        labels = load_retrieval_labels(LABELS)
        if (len(labels) != 32 or sum(row['answerability'] == 'answerable' for row in labels) != 29
                or sum(row['answerability'] == 'unanswerable' for row in labels) != 3):
            raise ValueError('Frozen labels must contain 29 answerable and 3 unanswerable cases')
        if len({row['case_id'] for row in labels}) != 32:
            raise ValueError('Duplicate label case IDs')
        known = {document.source_id for document in documents}
        if any(not set(row['relevant_source_ids']).issubset(known) for row in labels):
            raise ValueError('A labeled source is absent from the imported corpus')
        queries = query_inputs(labels)
        code = sorted(Path(__file__).parent.glob('*.py'))
        report = {
            'status': 'failed', 'synthetic': True,
            'dataset': 'frozen agent-authored synthetic retrieval labels; not human/online evidence',
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'python_version': platform.python_version(), 'platform': platform.platform(),
            'input_sha256': _hash_files([DEMO_PRODUCTS_PATH, DEMO_FAQ_PATH, LABELS]),
            'code_file_sha256': _hash_files(code),
            'code_sha256': hashlib.sha256(b''.join(p.name.encode() + b'\0' + p.read_bytes() for p in code)).hexdigest(),
            'catalog_product_count': 30, 'catalog_faq_count': 6, 'source_document_count': len(documents),
            'answerable_cases': 29, 'unanswerable_cases': 3, 'case_count': 32,
            'provider': provider, 'requested_model': model,
            'endpoint': adapter.base_url if adapter else None,
            'model_metadata': None, 'model_metadata_error': configuration_error,
            'method': 'One unified product+FAQ corpus; queries only, no label-based topic routing/filtering; existing EvidenceIndex unchanged; top 5 source deduplication',
            'limitations': ['Recall is answerable-case hit rate, not factual support or source recall.',
                           'Unanswerable top-5 results are retrieval output, not safe abstention.',
                           'Hash is a nonsemantic surrogate. No generation or user data. Remote calls may be billed; actual invoice cost is unknown.',
                           'Each strategy builds independently; model warm-up/hardware can affect timings.',
                           'EvidenceIndex also prepares a hash vector index for BM25; its build call is included, but BM25 queries embed nothing.'],
            'metric_definitions': {'recall_at_5': 'answerable cases with a gold source at ranks 1..5 / 29',
                                   'mrr_at_5': 'sum of first gold-source reciprocal ranks (zero if missing) / 29',
                                   'latency': 'serial wall-clock query latency including query embedding; excludes index build'},
            'compute_cost': None, 'strategies': {},
        }
        specs = [('bm25', 'bm25', HashEmbedding(), 'hash-surrogate', None),
                 ('hash_vector', 'vector', HashEmbedding(), 'hash-surrogate', None)]
        for name, mode, embedding, baseline_provider, model_name in specs:
            item = run_strategy(documents, queries, mode, embedding, baseline_provider, model_name)
            if item['status'] == 'success':
                item.update(score_rankings(item['cases'], labels))
            report['strategies'][name] = item
        if adapter is not None:
            try:
                report['model_metadata'] = (local_model_metadata(adapter) if provider == 'ollama'
                                            else adapter.metadata())
            except Exception as exc:
                report['model_metadata_error'] = f'{type(exc).__name__}: {exc}'
        for name, mode in [(provider + '_vector', 'vector'), (provider + '_hybrid', 'hybrid')]:
            if report['model_metadata_error']:
                item = {'status': 'blocked', 'embedding_provider': provider, 'model': model,
                        'requested_mode': mode, 'actual_mode_counts': None, 'fallback_count': None,
                        'vector_dimensions': None, 'index_build_ms': None, 'query_p50_ms': None,
                        'query_p95_ms': None, 'embedding_calls': None, 'cases': [],
                        'recall_at_5': None, 'mrr_at_5': None, 'unanswerable_returns': None,
                        'failure_stage': 'configuration' if configuration_error else 'model_metadata',
                        'error': report['model_metadata_error']}
            else:
                real_adapter = (OllamaEmbedding(model, base_url, timeout) if provider == 'ollama'
                                else BailianEmbedding(model, base_url, api_key, timeout, allow_remote=allow_remote))
                item = run_strategy(documents, queries, mode, real_adapter, provider, model)
                if provider == 'bailian':
                    item['provider_metadata'] = real_adapter.metadata()
                if item['status'] == 'success':
                    item.update(score_rankings(item['cases'], labels))
            report['strategies'][name] = item
        if all(item['status'] == 'success' for item in report['strategies'].values()):
            report['status'] = 'success'
        return report
    finally:
        store.engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['ollama', 'bailian'], default='ollama')
    parser.add_argument('--model')
    parser.add_argument('--base-url')
    parser.add_argument('--allow-remote', action='store_true', help='Allow sending synthetic benchmark texts to configured Bailian HTTPS endpoint')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    args.model = args.model or os.getenv('SHOPPING_EMBEDDING_MODEL') or (BAILIAN_MODEL if args.provider == 'bailian' else '')
    if not args.model:
        parser.error('--model is required for Ollama')
    args.base_url = (args.base_url or os.getenv('SHOPPING_EMBEDDING_BASE_URL')
                     or (BAILIAN_BASE_URL if args.provider == 'bailian' else 'http://127.0.0.1:11434'))
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be positive and finite')
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve the new path before any model work; racing runs cannot overwrite.
        with args.output.open('x', encoding='utf-8') as destination:
            try:
                report = run_benchmark(args.model, args.base_url, args.timeout, provider=args.provider,
                                       api_key=os.getenv('SHOPPING_EMBEDDING_API_KEY') or os.getenv('DASHSCOPE_API_KEY', ''),
                                       allow_remote=args.allow_remote)
            except Exception as exc:
                report = {'status': 'failed', 'synthetic': True, 'strategies': None,
                          'error': f'{type(exc).__name__}: {exc}', 'metrics': None}
            json.dump(report, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.write('\n')
    except FileExistsError:
        print(f'Benchmark refused overwrite: {args.output}', file=sys.stderr)
        return 1
    if report['status'] != 'success':
        print(f'Embedding benchmark FAILED; diagnostics: {args.output}', file=sys.stderr)
        for name, item in (report.get('strategies') or {}).items():
            if item['status'] != 'success':
                print(f"{name}: {item.get('error')}", file=sys.stderr)
        return 1
    print(f'Embedding benchmark SUCCESS: {args.output}')
    for name, item in report['strategies'].items():
        print(f"{name}: Recall@5={item['recall_at_5']['numerator']}/29 "
              f"MRR@5={item['mrr_at_5']['value']:.4f}; "
              f"actual={item['actual_mode_counts']}; fallbacks={item['fallback_count']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
