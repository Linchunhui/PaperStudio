import json
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import recommendation_rerank as ranking
from app.recommendation_rerank_routes import router, RerankInput


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setenv('PAPERSTUDIO_ENABLE_LLM_RERANK', '1')
    monkeypatch.setattr(ranking, 'public_candidates', lambda ids: [{'id': i, 'title': i, 'abstract': ''} for i in ids])
    monkeypatch.setattr(ranking.providers, 'settings', lambda: {'timeout': 600, 'max_output_tokens': 8000, 'max_input_chars': 400000})


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv('PAPERSTUDIO_ENABLE_LLM_RERANK', raising=False)
    monkeypatch.setattr(ranking.providers, 'complete', lambda *a: pytest.fail('must not call'))
    with pytest.raises(HTTPException) as e:
        ranking.rerank(['a', 'b'], 'OCR')
    assert e.value.status_code == 409


def test_selected_public_fields_only_and_bounded(ready, monkeypatch):
    def complete(system, source, cfg):
        assert cfg['timeout'] == 60 and cfg['max_output_tokens'] == 2048
        assert json.loads(source) == {'interests': 'OCR', 'papers': [{'id': 'a', 'title': 'a', 'abstract': ''}, {'id': 'b', 'title': 'b', 'abstract': ''}]}
        return '{"ids":["b","a"]}'
    monkeypatch.setattr(ranking.providers, 'complete', complete)
    assert ranking.rerank(['a', 'b'], 'OCR') == {'ids': ['b', 'a'], 'mode': 'llm', 'changed': True}


@pytest.mark.parametrize('answer', ['{"ids":["a","a"]}', '{"ids":["x","b"]}', '{"ids":["a"]}', '{"ids":["a",{}]}', 'not json', '{"ids":["a","b"],"secret":"bad"}'])
def test_invalid_results_preserve_order(ready, monkeypatch, answer):
    monkeypatch.setattr(ranking.providers, 'complete', lambda *a: answer)
    result = ranking.rerank(['a', 'b'], 'OCR')
    assert result['ids'] == ['a', 'b'] and result['mode'] == 'original'
    assert 'secret' not in str(result)


def test_failure_and_busy_preserve_order(ready, monkeypatch):
    def fail(*a):
        raise ranking.ExecutionError('secret service detail')
    monkeypatch.setattr(ranking.providers, 'complete', fail)
    assert ranking.rerank(['a', 'b'], 'OCR')['reason'] == 'unavailable_or_invalid_response'
    ranking._LOCK.acquire()
    try:
        assert ranking.rerank(['a', 'b'], 'OCR')['reason'] == 'busy'
    finally:
        ranking._LOCK.release()


def test_request_requires_explicit_confirmation_and_unique_ids():
    with pytest.raises(ValueError):
        RerankInput(candidate_ids=['a', 'b'], interests='OCR')
    with pytest.raises(ValueError):
        RerankInput(candidate_ids=['a', 'a'], interests='OCR', confirm_model_call=True)
    with pytest.raises(ValueError):
        RerankInput(candidate_ids=['a', 'b'], interests=' ', confirm_model_call=True)


def test_route_is_post_only_and_requires_session_csrf(ready, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(ranking.providers, 'complete', lambda *a: '{"ids":["b","a"]}')
    body = {'candidate_ids': ['a', 'b'], 'interests': 'OCR', 'confirm_model_call': True}
    with TestClient(app) as client:
        assert client.get('/api/recommendations/rerank').status_code == 405
        assert client.post('/api/recommendations/rerank', json=body).status_code == 401
    app = FastAPI()
    app.include_router(router)
    @app.middleware('http')
    async def session(request, next):
        request.state.session = {'csrf_token': 'token', 'user': {'id': 'reader'}}
        return await next(request)
    with TestClient(app) as client:
        assert client.post('/api/recommendations/rerank', json=body).status_code == 403
        assert client.post('/api/recommendations/rerank', json=body, headers={'x-csrf-token': 'token', 'x-reader-account': 'other'}).status_code == 409
        assert client.post('/api/recommendations/rerank', json=body, headers={'x-csrf-token': 'token'}).json()['ids'] == ['b', 'a']


def test_catalog_projection_never_includes_extra_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(ranking.core, 'DATA_DIR', tmp_path)
    with ranking.core.db() as con:
        con.execute('CREATE TABLE catalog_papers(id TEXT PRIMARY KEY, data TEXT)')
        con.execute('INSERT INTO catalog_papers VALUES(?,?)', ('a', json.dumps({'title': 'Public title', 'abstract': 'Public abstract', 'notes': 'private', 'raw': {'token': 'secret'}})))
    assert ranking.public_candidates(['a']) == [{'id': 'a', 'title': 'Public title', 'abstract': 'Public abstract'}]
    with pytest.raises(HTTPException) as error:
        ranking.public_candidates(['missing'])
    assert error.value.status_code == 404
