"""Integrated application boundaries and true-PDF bootstrap."""
from fastapi.testclient import TestClient
from app import core
from app.main import app


def test_bootstrap_reader_and_same_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO', '1')
    monkeypatch.delenv('AI_BASE_URL', raising=False)
    monkeypatch.delenv('AI_MODEL', raising=False)
    with TestClient(app) as client:
        papers = client.get('/api/papers').json()
        assert len(papers) == 1
        paper = papers[0]
        assert paper['page_count'] == paper['translated_page_count'] == 2
        assert client.get(f"/read/{paper['id']}?project=inbox").status_code == 200
        for side in ['original', 'translation']:
            page = client.get(f"/api/papers/{paper['id']}/pages/1", params={'side': side}).json()
            assert page['has_text'] and page['source_sha']
            svg = client.get(page['svg_url'])
            assert svg.status_code == 200 and '<svg' in svg.text
            assert '<path' in svg.text and '<image' not in svg.text
            pdf = client.get(f"/api/papers/{paper['id']}/file", params={'side': side})
            assert pdf.content.startswith(b'%PDF-')
        sources = client.get('/api/sources').json()
        assert len(sources['sources']) == 144
        assert client.get('/api/capabilities').json()['ai_configured'] is False
        assert client.post('/api/projects', json={'name':'blocked'}, headers={'Origin':'https://untrusted.example'}).status_code == 403
        assert client.post('/api/projects', json={'name':'allowed'}, headers={'Origin':'http://testserver'}).status_code == 201
        assert client.get('/data/paperstudio.sqlite3').status_code == 404
