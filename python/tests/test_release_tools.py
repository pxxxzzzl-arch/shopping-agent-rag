"""Release tools must not silently enable cloud calls or report false success."""
import json

import pytest
from fastapi.testclient import TestClient

from shopping_agent.business_smoke import main
from shopping_agent.demo import create_demo_app


def test_live_smoke_requires_explicit_remote_permission_before_startup(tmp_path):
    output = tmp_path / 'blocked.json'
    assert main(['--provider', 'bailian', '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['status'] == 'failed' and report['cases'] == []
    assert 'explicit --allow-remote' in report['failure']['message']
    assert 'embedding_metadata' not in report


def test_smoke_refuses_overwrite_before_any_calls(tmp_path):
    output = tmp_path / 'preserved.json'
    output.write_text('original evidence')
    assert main(['--provider', 'bailian', '--allow-remote', '--output', str(output)]) == 1
    assert output.read_text() == 'original evidence'


def test_default_demo_ignores_cloud_environment_and_preserves_real_api(monkeypatch):
    monkeypatch.setenv('SHOPPING_EMBEDDING_PROVIDER', 'bailian')
    monkeypatch.setenv('SHOPPING_EMBEDDING_ALLOW_REMOTE', 'true')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-only-not-a-real-key')
    with TestClient(create_demo_app()) as client:
        assert client.get('/').status_code == 200
        assert client.get('/demo/info').json()['provider'] == 'hash'
        result = client.post('/api/v1/shop/recommend', json={
            'user_id':'release-test', 'query':'推荐降噪耳机，预算500元', 'num_items':2})
        assert result.status_code == 200
        assert result.json()['configured_embedding_provider'] == 'hash'
        assert result.json()['recommendations']


def test_live_smoke_missing_key_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.delenv('SHOPPING_EMBEDDING_API_KEY', raising=False)
    output = tmp_path / 'missing-key.json'
    assert main(['--provider','bailian','--allow-remote','--output',str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['status'] == 'failed' and report['cases'] == []


def test_smoke_rejects_nonpositive_call_budget_before_output(tmp_path):
    output = tmp_path / 'never-created.json'
    with pytest.raises(SystemExit) as error:
        main(['--max-http-calls','0','--output',str(output)])
    assert error.value.code != 0 and not output.exists()
