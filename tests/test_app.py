"""Integrated application boundaries and true-PDF bootstrap."""
from contextlib import contextmanager
from fastapi.testclient import TestClient
from app import auth, core, imports, jobs
from app.main import app


@contextmanager
def authenticated_client():
    """Exercise the real setup/login boundary and preserve worker ownership."""
    with TestClient(app) as client:
        response = client.post('/api/auth/setup', json={
            'setup_token': (core.DATA_DIR / 'bootstrap-token').read_text().strip(),
            'username': 'admin', 'password': 'test-admin-password-long',
        })
        assert response.status_code == 201, response.text
        session = response.json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        with auth.as_user(session['user']['id']):
            yield client


def test_bootstrap_reader_and_same_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO', '1')
    monkeypatch.delenv('AI_BASE_URL', raising=False)
    monkeypatch.delenv('AI_MODEL', raising=False)
    with authenticated_client() as client:
        papers = client.get('/api/papers').json()
        assert len(papers) == 1
        paper = papers[0]
        assert paper['page_count'] == paper['translated_page_count'] == 2
        assert client.get(f"/read/{paper['id']}?project=inbox").status_code == 200
        manifest = client.get(f"/api/papers/{paper['id']}/page-manifest").json()
        assert [p['page'] for p in manifest['pages']] == [1, 2]
        translated = client.get(f"/api/papers/{paper['id']}/translation-pages").json()
        assert translated['mode'] == 'manual'
        assert all(p['status'] == 'ready' for p in translated['pages'])
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
        # Opening either view is text extraction only, even when a translated
        # demo PDF exists. It must not enqueue an AI task by itself.
        reading = client.get(f"/api/papers/{paper['id']}/pages/1/reading", params={'project_id': 'inbox'})
        assert reading.status_code == 200, reading.text
        assert reading.json()['source_sha'] == paper['sha256']
        assert reading.json()['blocks'] and all(block['translation'] is None for block in reading.json()['blocks'])
        assert client.get('/api/jobs').json() == []
        assert client.get('/api/catalog/stats').status_code == 200
        assert client.get('/api/catalog', params={'page_size': 1}).status_code == 200
        assert client.post('/api/projects', json={'name':'blocked'}, headers={'Origin':'https://untrusted.example'}).status_code == 403
        assert client.post('/api/projects', json={'name':'allowed'}, headers={'Origin':'http://testserver'}).status_code == 201
        assert client.get('/data/paperstudio.sqlite3').status_code == 404


def test_import_and_discovery_routes_share_application_lifecycle(tmp_path, monkeypatch):
    """A fresh installed app can import, parse and read before AI is configured."""
    import json
    import fitz
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO', '0')
    for key in ('AI_BASE_URL', 'AI_MODEL', 'AI_API_KEY', 'SEARCH_PROVIDER', 'SEARXNG_BASE_URL', 'BRAVE_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    # Advance the durable queue explicitly so this verifies the HTTP contract
    # without depending on thread timing or any external model/network service.
    monkeypatch.setattr(imports, 'start_worker', imports._STOP.clear)
    monkeypatch.setattr(jobs, 'start_worker', jobs._STOP.clear)
    with fitz.open() as document:
        for number in range(2):
            page = document.new_page()
            page.insert_text((60, 80), f'Original page {number + 1}: evidence for the method.')
        content = document.tobytes()
    with authenticated_client() as client:
        config = client.get('/api/discovery/config').json()
        assert config['web_search']['configured'] is False
        assert client.get('/api/discovery/search', params={'q': 'document retrieval', 'scope': 'web'}).status_code == 409
        response = client.post('/api/imports/file',
            files={'file': ('paper.pdf', content, 'application/pdf')},
            data={'project_id': 'inbox', 'processing': json.dumps({'parse': True, 'brief_modes': ['abstract']})})
        assert response.status_code == 202, response.text
        plan = response.json()
        paper_id = plan['paper_id']
        assert client.get(f'/api/papers/{paper_id}/file').content == content
        assert imports.run_once()  # Parse all pages.
        parsed = client.get(f'/api/papers/{paper_id}/parsed').json()
        assert len(parsed['pages']) == 2 and '[PDF 第 2 页]' in parsed['fulltext']
        reading = client.get(f'/api/papers/{paper_id}/pages/2/reading', params={'project_id': 'inbox'}).json()
        assert reading['blocks'][0]['text'].startswith('Original page 2')
        assert client.post(f'/api/papers/{paper_id}/reading/translate', json={
            'project_id': 'inbox', 'page': 2, 'block_id': reading['blocks'][0]['id'],
            'source_sha': reading['source_sha']}).status_code == 409  # Configure AI explicitly.
        assert imports.run_once()  # Missing model blocks only its own step.
        settled = client.get(f"/api/imports/{plan['id']}").json()
        assert settled['status'] == 'blocked'
        assert [step['status'] for step in settled['steps']] == ['succeeded', 'succeeded', 'blocked']
        position = client.patch(f'/api/papers/{paper_id}/reading', json={
            'project_id': 'inbox', 'status': 'reading', 'last_page': 2, 'page_offset': 0.43})
        assert position.status_code == 200 and position.json()['page_offset'] == 0.43
        for path in ('workspace.css', 'reader-continuous.css', 'app.js', 'reader.js'):
            assert client.get('/static/' + path).status_code == 200

