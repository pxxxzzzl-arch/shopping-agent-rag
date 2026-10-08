"""Benchmark contracts using controlled vectors, never evidence of a real model."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from shopping_agent.embedding_benchmark import (
    BailianEmbedding, MeasuredEmbedding, query_inputs, retrieve_queries, run_strategy, score_rankings,
)
from shopping_agent.retrieval import EvidenceDocument, EvidenceIndex, HashEmbedding


def label(case_id, sources, answerability='answerable'):
    return {'case_id': case_id, 'query': '合成耳机', 'relevant_source_ids': sources,
            'answerability': answerability, 'topic': 'product'}


def test_metrics_exact_fractions_top5_and_unanswerable_separation():
    labels = [label(str(i), ['gold']) for i in range(4)]
    labels += [label(f'u{i}', [], 'unanswerable') for i in range(3)]
    rankings = [
        {'case_id': '0', 'top5_source_ids': ['gold']},
        {'case_id': '1', 'top5_source_ids': ['miss', 'gold']},
        {'case_id': '2', 'top5_source_ids': ['a', 'b', 'c', 'd', 'gold']},
        {'case_id': '3', 'top5_source_ids': ['a', 'b', 'c', 'd', 'e', 'gold']},
        *[{'case_id': f'u{i}', 'top5_source_ids': ['gold']} for i in range(3)],
    ]
    scored = score_rankings(rankings, labels)
    assert scored['recall_at_5'] == {'numerator': 3, 'denominator': 4, 'value': .75}
    assert scored['mrr_at_5']['numerator_fraction'] == '17/10'
    assert scored['mrr_at_5']['denominator'] == 4
    assert scored['mrr_at_5']['value'] == .425
    assert [row['returned_count'] for row in scored['unanswerable_returns']] == [1, 1, 1]
    missed = score_rankings([{'case_id': 'a', 'top5_source_ids': []}], [label('a', ['g'])])
    assert missed['recall_at_5']['value'] == missed['mrr_at_5']['value'] == 0
    unanswerable = score_rankings([{'case_id': 'u', 'top5_source_ids': []}],
                                 [label('u', [], 'unanswerable')])
    assert unanswerable['recall_at_5']['value'] is None
    assert unanswerable['mrr_at_5']['value'] is None


@pytest.mark.parametrize('rankings,labels', [
    ([{'case_id': 'x', 'top5_source_ids': []}] * 2, [label('x', ['g'])]),
    ([{'case_id': 'x', 'top5_source_ids': []}], [label('x', ['g'])] * 2),
    ([{'case_id': 'x', 'top5_source_ids': []}], [label('y', ['g'])]),
])
def test_metric_case_contract(rankings, labels):
    with pytest.raises(ValueError, match='unique cases'):
        score_rankings(rankings, labels)


class RecordingEmbedding:
    name = 'controlled-test-only'

    def __init__(self):
        self.inputs = []

    def embed(self, texts):
        self.inputs.append(list(texts))
        return [[1., float(len(text) % 7 + 1), 2.] for text in texts]


DOCS = [EvidenceDocument('p', 'p1', 'description', '合成耳机降噪'),
        EvidenceDocument('f', None, 'faq', '合成退款政策')]


@pytest.mark.parametrize('mode', ['bm25', 'vector', 'hybrid'])
def test_labels_cannot_change_queries_corpus_or_ranking(mode):
    original = [label('q1', ['p']), label('q2', ['f'])]
    original[1]['query'] = '合成退款'
    poisoned = deepcopy(original)
    for row in poisoned:
        row.update(relevant_source_ids=['secret-label-only'], topic='secret-topic',
                   answerability='unanswerable', supported_claims=['secret-answer'])
    assert query_inputs(original) == query_inputs(poisoned)
    outputs, inputs = [], []
    for labels in (original, poisoned):
        adapter = RecordingEmbedding()
        measured = MeasuredEmbedding(adapter)
        index = EvidenceIndex(DOCS, measured)
        rows = retrieve_queries(index, query_inputs(labels), mode, measured)
        outputs.append([row['top5_source_ids'] for row in rows])
        inputs.append(adapter.inputs)
        assert {doc.source_id for doc in index.documents} == {'p', 'f'}
    assert outputs[0] == outputs[1]
    assert inputs[0] == inputs[1]
    assert 'secret' not in json.dumps(inputs)
    assert score_rankings([{'case_id': 'q1', 'top5_source_ids': ['p']},
                           {'case_id': 'q2', 'top5_source_ids': ['f']}], original)['recall_at_5']['value'] == 1


@pytest.mark.parametrize('mode', ['bm25', 'vector', 'hybrid'])
def test_actual_mode_and_call_counts(mode):
    result = run_strategy(DOCS, query_inputs([label('q', ['p'])]), mode,
                          RecordingEmbedding(), 'controlled-test-only')
    assert result['status'] == 'success'
    assert result['actual_mode_counts'] == {mode: 1}
    assert result['cases'][0]['requested_mode'] == result['cases'][0]['actual_mode'] == mode
    assert result['fallback_count'] == 0
    assert result['vector_dimensions'] == 3
    assert result['index_embedding_calls']['attempted_calls'] == 1
    assert result['query_embedding_calls']['attempted_calls'] == (mode != 'bm25')


@pytest.mark.parametrize('bad', [[], [0., 0.], [float('nan')], [float('inf')],
                                  [True, 1.], [1e200], [1e-200]])
def test_invalid_vectors_fail_without_metrics(bad):
    class Invalid:
        name = 'controlled-invalid'

        def embed(self, texts):
            return [bad for _ in texts]

    result = run_strategy(DOCS, query_inputs([label('q', ['p'])]), 'vector', Invalid(), 'test')
    assert result['status'] == 'failed'
    assert result['failure_stage'] == 'index'
    assert result['recall_at_5'] is None and result['mrr_at_5'] is None
    assert result['embedding_calls']['attempted_calls'] == 1
    assert result['embedding_calls']['successful_calls'] == 0


@pytest.mark.parametrize('mode', ['vector', 'hybrid'])
def test_dimension_drift_records_actual_fallback_and_fails(mode):
    class Changing:
        name = 'controlled-changing'
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return [[1., 2.] if self.calls == 1 else [1., 2., 3.] for _ in texts]

    result = run_strategy(DOCS, query_inputs([label('q', ['p']), label('q2', ['p'])]),
                          mode, Changing(), 'test')
    assert result['status'] == 'failed'
    assert result['failure_stage'] == 'query'
    assert result['actual_mode_counts'] == {'bm25': 1}
    assert result['fallback_count'] == 1
    assert result['cases'][0]['fallback_reason']
    assert result['query_p50_ms'] is None and result['recall_at_5'] is None
    assert result['embedding_calls']['attempted_calls'] == 2


@pytest.fixture
def controlled_server():
    state = {'inputs': [], 'fault': None, 'compatible_inputs': [], 'auth_headers': []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, payload, status=200):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/api/tags':
                self.respond({'models': [{'name': 'controlled-test-only', 'size': 100,
                                          'digest': 'controlled-not-a-real-model-digest'}]})
            elif self.path == '/api/version':
                self.respond({'version': 'controlled-test-service'})
            else:
                self.respond({'error': 'unknown path'}, 404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/v1/embeddings':
                assert body['model'] == 'text-embedding-v4'
                assert body['dimensions'] == 1024 and body['encoding_format'] == 'float'
                texts = body['input']
                assert 1 <= len(texts) <= 10
                state['compatible_inputs'].append(texts)
                state['auth_headers'].append(self.headers.get('Authorization'))
                if state['fault'] in {'auth', 'redirect'}:
                    self.respond({'error': {'message': self.headers.get('Authorization')}},
                                 401 if state['fault'] == 'auth' else 302)
                    return
                if state['fault'] == 'remote-query' and len(texts) == 1:
                    self.respond({'error': 'controlled query failure'}, 500)
                    return
                vectors = [[float(int(text[1:]) + 1)] + [1.] * 1023 if text.startswith('#')
                           else [float(len(text) % 7 + 1)] + [1.] * 1023 for text in texts]
                if state['fault'] == 'boolean':
                    vectors[0][0] = True
                elif state['fault'] == 'remote-zero':
                    vectors[0] = [0.] * 1024
                elif state['fault'] == 'remote-dimensions':
                    vectors[0] = [1., 2., 3.]
                rows = [{'index': i, 'embedding': v} for i, v in reversed(list(enumerate(vectors)))]
                if state['fault'] == 'duplicate':
                    rows[-1]['index'] = rows[0]['index']
                data = {'model': 'wrong-model' if state['fault'] == 'model' else body['model'],
                        'data': rows, 'usage': {'total_tokens': len(texts) * 3},
                        'id': 'controlled-compatible-test-id'}
                if state['fault'] == 'no-usage':
                    data.pop('usage')
                self.respond(data)
                return
            assert body['model'] == 'controlled-test-only'
            if self.path == '/api/show':
                self.respond({'capabilities': ['embedding'], 'model_info': {'test_only': True}})
            elif self.path == '/api/embed':
                texts = body['input']
                state['inputs'].append(texts)
                if state['fault'] == 'query' and len(texts) == 1:
                    self.respond({'error': 'controlled query failure'}, 500)
                elif state['fault'] == 'invalid':
                    self.respond({'embeddings': [[0., 0., 0.] for _ in texts]})
                else:
                    self.respond({'embeddings': [[1., float(len(text) % 7 + 1), 2.] for text in texts]})
            else:
                self.respond({'error': 'unknown path'}, 404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def cli(output, endpoint):
    return subprocess.run([sys.executable, '-m', 'shopping_agent.embedding_benchmark',
                           '--model', 'controlled-test-only', '--base-url', endpoint,
                           '--timeout', '1', '--output', str(output)],
                          cwd=Path(__file__).resolve().parents[1],
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': '.'},
                          capture_output=True, text=True, timeout=30)


def test_cli_controlled_http_contract_and_refuse_overwrite(controlled_server, tmp_path):
    endpoint, state = controlled_server
    output = tmp_path / 'controlled-test-only.json'
    completed = cli(output, endpoint)
    assert completed.returncode == 0, completed.stderr
    assert 'SUCCESS' in completed.stdout
    report = json.loads(output.read_text())
    assert report['status'] == 'success' and report['synthetic'] is True
    assert (report['catalog_product_count'], report['catalog_faq_count']) == (30, 6)
    assert (report['answerable_cases'], report['unanswerable_cases']) == (29, 3)
    assert len(report['input_sha256']) == 3 and len(report['code_sha256']) == 64
    assert report['model_metadata']['digest'] == 'controlled-not-a-real-model-digest'
    for name, mode in [('bm25', 'bm25'), ('hash_vector', 'vector'),
                       ('ollama_vector', 'vector'), ('ollama_hybrid', 'hybrid')]:
        row = report['strategies'][name]
        assert row['status'] == 'success'
        assert row['actual_mode_counts'] == {mode: 32}
        assert row['fallback_count'] == 0
        assert len(row['cases']) == 32
        assert row['recall_at_5']['denominator'] == row['mrr_at_5']['denominator'] == 29
        assert len(row['unanswerable_returns']) == 3
        assert row['query_p95_ms'] >= row['query_p50_ms'] >= 0
        assert row['index_build_ms'] >= 0
        assert all(len(case['top5_source_ids']) <= 5 for case in row['cases'])
        if name.startswith('ollama'):
            assert row['vector_dimensions'] == 3
            assert row['embedding_calls']['attempted_calls'] == 33
            assert row['embedding_calls']['successful_calls'] == 33
    assert len(state['inputs']) == 66
    content, count = output.read_bytes(), len(state['inputs'])
    repeated = cli(output, endpoint)
    assert repeated.returncode != 0 and 'refused overwrite' in repeated.stderr
    assert output.read_bytes() == content and len(state['inputs']) == count


@pytest.mark.parametrize('fault', ['query', 'invalid'])
def test_cli_http_failure_never_claims_success(controlled_server, tmp_path, fault):
    endpoint, state = controlled_server
    state['fault'] = fault
    output = tmp_path / 'failed-controlled-test.json'
    completed = cli(output, endpoint)
    assert completed.returncode != 0 and 'SUCCESS' not in completed.stdout
    report = json.loads(output.read_text())
    assert report['status'] == 'failed'
    assert report['strategies']['bm25']['status'] == 'success'
    for name in ('ollama_vector', 'ollama_hybrid'):
        row = report['strategies'][name]
        assert row['status'] == 'failed'
        assert row['recall_at_5'] is None and row['mrr_at_5'] is None
        if fault == 'query':
            assert row['actual_mode_counts'] == {'bm25': 1}
            assert row['fallback_count'] == 1
        else:
            assert row['failure_stage'] == 'index'


def test_cli_unreachable_endpoint_is_blocked(tmp_path):
    server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    output = tmp_path / 'unreachable.json'
    completed = cli(output, f'http://127.0.0.1:{port}')
    assert completed.returncode != 0 and 'SUCCESS' not in completed.stdout
    report = json.loads(output.read_text())
    assert report['status'] == 'failed' and report['model_metadata'] is None
    for name in ('ollama_vector', 'ollama_hybrid'):
        row = report['strategies'][name]
        assert row['status'] == 'blocked' and row['recall_at_5'] is None


def test_setup_explicit_invalid_python_fails_before_install_even_with_existing_env(tmp_path):
    invalid = tmp_path / 'invalid-python'
    invalid.write_text('#!/bin/sh\nexit 1\n')
    invalid.chmod(0o755)
    script = Path(__file__).resolve().parents[2] / 'scripts/setup_shopping_env.sh'
    completed = subprocess.run(['bash', str(script)], capture_output=True, text=True,
                               env={**os.environ, 'SHOPPING_PYTHON': str(invalid)}, timeout=10)
    assert completed.returncode != 0
    assert 'SHOPPING_PYTHON must' in completed.stderr
    assert 'verified' not in completed.stdout and 'pip' not in completed.stdout


def compatible_cli(output, endpoint, *, key='test-secret-only', allow_remote=True):
    command = [sys.executable, '-m', 'shopping_agent.embedding_benchmark', '--provider', 'bailian',
               '--model', 'text-embedding-v4', '--base-url', endpoint, '--timeout', '2', '--output', str(output)]
    if allow_remote:
        command.append('--allow-remote')
    return subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': '.',
                               'SHOPPING_EMBEDDING_API_KEY': key, 'DASHSCOPE_API_KEY': ''})


def test_bailian_batches_reordered_indices_and_real_http_counts(controlled_server):
    endpoint, state = controlled_server
    adapter = BailianEmbedding('text-embedding-v4', endpoint + '/v1', 'test-secret-only')
    texts = [f'#{i}' for i in range(23)]
    result = adapter.embed(texts)
    assert [vector[0] for vector in result] == list(range(1, 24))
    assert [len(batch) for batch in state['compatible_inputs']] == [10, 10, 3]
    assert [text for batch in state['compatible_inputs'] for text in batch] == texts
    metadata = adapter.metadata()
    assert metadata['http_attempted_calls'] == metadata['http_successful_calls'] == 3
    assert metadata['reported_total_tokens'] == 69
    assert metadata['model_version'] is None and metadata['digest'] is None
    assert metadata['actual_billed_cost'] is None
    assert metadata['response_models'] == ['text-embedding-v4']


@pytest.mark.parametrize('fault', ['duplicate', 'boolean', 'remote-zero', 'remote-dimensions', 'model', 'auth', 'redirect'])
def test_bailian_invalid_response_and_secret_redaction(controlled_server, fault):
    endpoint, state = controlled_server
    state['fault'] = fault
    adapter = BailianEmbedding('text-embedding-v4', endpoint + '/v1', 'test-secret-only')
    with pytest.raises(ValueError) as raised:
        adapter.embed(['a', 'b'])
    assert 'test-secret-only' not in str(raised.value)
    assert adapter.metadata()['http_attempted_calls'] == 1
    assert adapter.metadata()['http_successful_calls'] == 0
    assert adapter.metadata()['reported_total_tokens'] is None
    assert len(state['compatible_inputs']) == 1  # no redirect or automatic retries


def test_bailian_missing_usage_does_not_invent_zero_tokens(controlled_server):
    endpoint, state = controlled_server
    state['fault'] = 'no-usage'
    adapter = BailianEmbedding('text-embedding-v4', endpoint + '/v1', 'test-secret-only')
    assert len(adapter.embed(['a'])) == 1
    assert adapter.metadata()['reported_total_tokens'] is None
    assert adapter.metadata()['estimated_list_price_cny'] is None


def test_bailian_cli_four_strategy_contract_and_overwrite(controlled_server, tmp_path):
    endpoint, state = controlled_server
    output = tmp_path / 'controlled-bailian-only.json'
    completed = compatible_cli(output, endpoint + '/v1')
    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text())
    assert report['status'] == 'success' and report['provider'] == 'bailian'
    assert set(report['strategies']) == {'bm25', 'hash_vector', 'bailian_vector', 'bailian_hybrid'}
    for name, mode in [('bailian_vector', 'vector'), ('bailian_hybrid', 'hybrid')]:
        item = report['strategies'][name]
        assert item['actual_mode_counts'] == {mode: 32}
        assert item['fallback_count'] == 0 and item['vector_dimensions'] == 1024
        assert item['index_embedding_calls']['attempted_calls'] == 10
        assert item['query_embedding_calls']['attempted_calls'] == 32
        assert item['embedding_calls']['attempted_calls'] == 42
        assert item['embedding_calls']['adapter_attempted_calls'] == 33
        assert item['embedding_calls']['input_texts'] == 128
        assert item['provider_metadata']['reported_total_tokens'] == 384
        assert item['provider_metadata']['response_models'] == ['text-embedding-v4']
        assert item['recall_at_5']['denominator'] == item['mrr_at_5']['denominator'] == 29
        assert len(item['unanswerable_returns']) == 3
    assert len(state['compatible_inputs']) == 84
    assert set(state['auth_headers']) == {'Bearer test-secret-only'}
    assert 'test-secret-only' not in output.read_text() + completed.stdout + completed.stderr
    before, requests = output.read_bytes(), len(state['compatible_inputs'])
    repeated = compatible_cli(output, endpoint + '/v1')
    assert repeated.returncode != 0 and 'refused overwrite' in repeated.stderr
    assert output.read_bytes() == before and len(state['compatible_inputs']) == requests


@pytest.mark.parametrize('fault', ['remote-query', 'auth'])
def test_bailian_cli_failure_keeps_offline_baselines(controlled_server, tmp_path, fault):
    endpoint, state = controlled_server
    state['fault'] = fault
    output = tmp_path / 'controlled-bailian-failure.json'
    completed = compatible_cli(output, endpoint + '/v1')
    assert completed.returncode != 0 and 'SUCCESS' not in completed.stdout
    assert 'test-secret-only' not in output.read_text() + completed.stdout + completed.stderr
    report = json.loads(output.read_text())
    assert report['strategies']['bm25']['status'] == report['strategies']['hash_vector']['status'] == 'success'
    for name in ('bailian_vector', 'bailian_hybrid'):
        item = report['strategies'][name]
        assert item['status'] == 'failed' and item['recall_at_5'] is None and item['mrr_at_5'] is None
        if fault == 'remote-query':
            assert item['actual_mode_counts'] == {'bm25': 1} and item['fallback_count'] == 1


@pytest.mark.parametrize('key,allow_remote', [('', True), ('test-secret-only', False)])
def test_bailian_cli_missing_config_blocks_before_network(tmp_path, key, allow_remote):
    output = tmp_path / 'blocked.json'
    completed = compatible_cli(output, 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                               key=key, allow_remote=allow_remote)
    assert completed.returncode != 0 and 'SUCCESS' not in completed.stdout
    report = json.loads(output.read_text())
    assert report['status'] == 'failed'
    assert report['strategies']['bm25']['status'] == 'success'
    for name in ('bailian_vector', 'bailian_hybrid'):
        item = report['strategies'][name]
        assert item['status'] == 'blocked' and item['failure_stage'] == 'configuration'
        assert item['recall_at_5'] is None and item['embedding_calls'] is None


@pytest.mark.parametrize('endpoint', ['http://dashscope.aliyuncs.com/compatible-mode/v1',
                                    'https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1',
                                    'https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1',
                                    'https://dashscope.aliyuncs.com/compatible-mode/v1?api_key=secret',
                                    'https://dashscope.aliyuncs.com/wrong-path'])
def test_bailian_rejects_unsafe_endpoint_before_network(endpoint):
    with pytest.raises(ValueError, match='official HTTPS'):
        BailianEmbedding('text-embedding-v4', endpoint, 'test-secret-only', allow_remote=True)


def test_bailian_cli_unreachable_endpoint_is_failed_without_success(tmp_path):
    server = ThreadingHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
    endpoint = f'http://127.0.0.1:{server.server_port}/v1'
    server.server_close()
    output = tmp_path / 'controlled-unreachable-bailian.json'
    completed = compatible_cli(output, endpoint)
    assert completed.returncode != 0 and 'SUCCESS' not in completed.stdout
    report = json.loads(output.read_text())
    for name in ('bailian_vector', 'bailian_hybrid'):
        item = report['strategies'][name]
        assert item['status'] == 'failed' and item['recall_at_5'] is None
        assert item['embedding_calls']['attempted_calls'] == 1
