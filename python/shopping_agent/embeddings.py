"""Shared embedding transport and explicit business configuration factory.

No evaluation imports, ranking rules, model downloads or automatic cloud calls.
"""
from __future__ import annotations

import math
import re
import threading
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .retrieval import HashEmbedding, OllamaEmbedding

BAILIAN_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
BAILIAN_MODEL = 'text-embedding-v4'


class BailianEmbedding:
    """Explicit opt-in HTTPS embedding; HTTP loopback is for contract tests only.

    Only the compatible transport is new. EvidenceIndex still owns all ranking.
    No retries, redirects or environment proxies; no keys or remote error bodies
    enter reports. Response index is checked before reordering a batch.
    """

    provider = "bailian"
    safe_errors = True

    def __init__(self, model, base_url, api_key, timeout=30, *, allow_remote=False):
        try:
            parsed = urlsplit(base_url)
        except (TypeError, ValueError):
            # urlsplit's NFKC errors can echo the full netloc (including secrets).
            raise ValueError('Invalid embedding endpoint URL; details suppressed') from None
        try:
            port = parsed.port
        except ValueError:
            raise ValueError('Invalid embedding endpoint port') from None
        local = parsed.hostname in {'localhost', '127.0.0.1'}
        official = (parsed.hostname in {'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com'}
                    or bool(re.fullmatch(r'[A-Za-z0-9-]+\.(cn-beijing|ap-southeast-1|cn-hongkong)\.maas\.aliyuncs\.com', parsed.hostname or '')))
        if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path.rstrip('/') not in {'/compatible-mode/v1', '/v1'}
                or not ((local and parsed.scheme == 'http' and port is not None)
                        or (official and parsed.scheme == 'https' and port in (None, 443)))):
            raise ValueError('Bailian needs an official HTTPS base URL; only loopback HTTP is allowed for tests')
        if not local and not allow_remote:
            raise ValueError('Remote embedding requires --allow-remote to send synthetic benchmark texts')
        if not api_key or not api_key.strip() or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
            raise ValueError('Set SHOPPING_EMBEDDING_API_KEY or DASHSCOPE_API_KEY locally')
        if model != BAILIAN_MODEL:
            raise ValueError('Bailian benchmark currently verifies text-embedding-v4 only')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Embedding timeout must be positive and finite')
        self._stats_lock = threading.RLock()
        self.model, self.base_url, self.timeout = model, base_url.rstrip('/'), timeout
        self._api_key = api_key
        self.name = 'bailian:' + model
        self.http_attempts = self.http_successes = self.http_input_texts = 0
        self.usage_tokens = 0
        self.usage_known = True
        self.request_ids = []
        self.response_models = set()
        self.beijing_price = parsed.hostname == 'dashscope.aliyuncs.com' or (parsed.hostname or '').endswith('.cn-beijing.maas.aliyuncs.com')

    def metadata(self):
        with self._stats_lock:
            return self._metadata_locked()

    def _metadata_locked(self):
        complete_usage = self.usage_known and self.http_successes and self.http_attempts == self.http_successes
        return {'provider': 'bailian', 'requested_model': self.model,
                'response_models': sorted(self.response_models), 'model_version': None,
                'digest': None, 'requested_dimensions': 1024, 'batch_size': 10,
                'http_attempted_calls': self.http_attempts, 'http_successful_calls': self.http_successes,
                'http_input_texts': self.http_input_texts,
                'request_ids': list(self.request_ids),
                'reported_total_tokens': self.usage_tokens if complete_usage else None,
                'estimated_list_price_cny': (self.usage_tokens * .5 / 1_000_000
                                             if self.beijing_price and complete_usage else None),
                'actual_billed_cost': None,
                'price_basis': 'Beijing text-embedding-v4 synchronous list price 0.5 CNY/million tokens, checked 2026-10-07; not an invoice or free-quota claim',
                'price_source': 'https://help.aliyun.com/zh/model-studio/text-embedding-v4'}

    def embed(self, texts):
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('Embedding inputs must be nonempty strings')
        result = []
        with httpx.Client(timeout=self.timeout, trust_env=False, follow_redirects=False) as client:
            for start in range(0, len(texts), 10):
                batch = list(texts[start:start + 10])
                with self._stats_lock:
                    self.http_attempts += 1
                    self.http_input_texts += len(batch)
                try:
                    response = client.post(self.base_url + '/embeddings',
                                           headers={'Authorization': 'Bearer ' + self._api_key},
                                           json={'model': self.model, 'input': batch,
                                                 'dimensions': 1024, 'encoding_format': 'float'})
                except httpx.TimeoutException:
                    raise ValueError('Embedding endpoint timed out') from None
                except httpx.HTTPError:
                    raise ValueError('Embedding transport failed (details suppressed to protect credentials)') from None
                if response.status_code != 200:
                    raise ValueError(f'Embedding endpoint returned HTTP {response.status_code}; response body suppressed')
                try:
                    data = response.json()
                except ValueError:
                    raise ValueError('Embedding endpoint returned invalid JSON') from None
                if not isinstance(data, dict) or data.get('model') != self.model:
                    raise ValueError('Embedding response model is missing or mismatched')
                rows = data.get('data')
                if (not isinstance(rows, list) or len(rows) != len(batch)
                        or any(not isinstance(row, dict) or type(row.get('index')) is not int for row in rows)
                        or {row['index'] for row in rows} != set(range(len(batch)))):
                    raise ValueError('Embedding response has invalid or duplicate input indices')
                vectors = [row.get('embedding') for row in sorted(rows, key=lambda row: row['index'])]
                for vector in vectors:
                    if (not isinstance(vector, list) or len(vector) != 1024
                            or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
                        raise ValueError('Embedding response needs 1024 finite numeric dimensions')
                    squared_norm = sum(v * v for v in vector)
                    if not math.isfinite(squared_norm) or squared_norm == 0:
                        raise ValueError('Embedding response has invalid vector norm')
                with self._stats_lock:
                    self.http_successes += 1
                    self.response_models.add(self.model)
                    usage = data.get('usage')
                    tokens = usage.get('total_tokens') if isinstance(usage, dict) else None
                    if type(tokens) is int and tokens >= 0:
                        self.usage_tokens += tokens
                    else:
                        self.usage_known = False
                    request_id = response.headers.get('x-request-id') or data.get('id')
                    if (isinstance(request_id, str) and len(request_id) <= 128
                            and re.fullmatch(r'[A-Za-z0-9_.:/-]+', request_id)
                            and self._api_key not in request_id):
                        self.request_ids.append(request_id)
                    self.request_ids[:] = self.request_ids[-1024:]
                result.extend(vectors)
        return result


def create_embedding(settings: Settings):
    """Resolve explicit providers; preserve legacy model-only Ollama selection."""
    provider = settings.embedding_provider or ("ollama" if settings.embedding_model else "hash")
    if provider == "hash":
        adapter = HashEmbedding()
        adapter.provider = "hash"
        adapter.model = None
        return adapter
    if provider == "ollama":
        adapter = OllamaEmbedding(settings.embedding_model,
                                  settings.embedding_base_url or "http://127.0.0.1:11434",
                                  settings.embedding_timeout_seconds)
        adapter.provider = "ollama"
        return adapter
    if provider == "bailian":
        if not settings.embedding_allow_remote:
            raise ValueError("Bailian requires SHOPPING_EMBEDDING_ALLOW_REMOTE=true; no HTTP request sent")
        return BailianEmbedding(settings.embedding_model or BAILIAN_MODEL,
                                settings.embedding_base_url or BAILIAN_BASE_URL,
                                settings.embedding_api_key, settings.embedding_timeout_seconds,
                                allow_remote=True)
    raise ValueError("Unknown shopping embedding provider; expected hash, ollama or bailian")
