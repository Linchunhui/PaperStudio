"""Real PDF/SQLite alignment publication around a controlled BabelDOC process."""
import json
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai, core, jobs, translations
from app.core_routes import router
from app.jobs_routes import router as jobs_router


def document(texts):
    with fitz.open() as doc:
        for text in texts:
            page = doc.new_page()
            page.insert_text((60, 80), text)
        return doc.tobytes()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('AI_BASE_URL', 'http://localhost:9011/v1')
    monkeypatch.setenv('AI_MODEL', 'local-test')
    monkeypatch.delenv('AI_API_KEY', raising=False)
    monkeypatch.setattr(ai, 'translation_executable', lambda: '/opt/babeldoc')
    jobs._STOP.clear()
    core.init_db()
    jobs.init_jobs()
    app = FastAPI()
    app.include_router(router)
    app.include_router(jobs_router)
    with TestClient(app) as client:
        yield client
    jobs._STOP.clear()


def test_real_adapter_publishes_map_with_page_and_discards_private_tracker(setup, monkeypatch):
    client = setup
    paper = core.add_paper(document(['First original paragraph.', 'Second original paragraph.']), 'inbox', 'Alignment paper')
    endpoint = f"/api/papers/{paper['id']}/pages/1/alignment"
    assert client.get(endpoint).json()['status'] == 'pending'
    job = jobs.enqueue(paper['id'], 'inbox', 'translate')
    pending = client.get(endpoint).json()
    assert pending['translation_run_id'] == job['translation_run_id'] and pending['translation_sha'] is None
    calls, tracking_paths = [], []
    first = {}

    class Process:
        returncode = 0

        def __init__(self, argv, **kwargs):
            assert '--working-dir' in argv and '--debug' not in argv
            assert kwargs['shell'] is False
            source = Path(argv[argv.index('--files') + 1])
            with fitz.open(source) as doc:
                assert len(doc) == 1
                text = doc[0].get_text().strip()
            calls.append(text)
            if len(calls) == 2:
                # The first map is already usable while later pages translate.
                first.update(client.get(endpoint).json())
                assert first['status'] == 'ready' and len(first['paragraphs']) == 1
                assert first['original_sha'] == paper['sha256']
                assert first['translation_run_id'] == job['translation_run_id']
            translated = 'Translated ' + text
            out = Path(argv[argv.index('--output') + 1])
            (out / 'source.zh.mono.pdf').write_bytes(document([translated]))
            tracking = Path(argv[argv.index('--working-dir') + 1]) / source.stem / 'translate_tracking.json'
            tracking.parent.mkdir(parents=True)
            tracking.write_text(json.dumps({'page': [{'paragraph': [{
                'pdf_unicode': text, 'input': text, 'output': translated, 'placeholders': [],
                'llm_translate_trackers': [{'prompt': 'PRIVATE_PROMPT_NOT_FOR_STORAGE'}]
            }]}]}))
            tracking_paths.append(tracking)

        def poll(self):
            return 0

    monkeypatch.setattr(ai.subprocess, 'Popen', Process)
    assert jobs.run_one()
    assert jobs.get_job(job['id'])['status'] == 'succeeded'
    complete = client.get(endpoint).json()
    assert complete == first
    assert complete['translation_sha'] == client.get(f"/api/papers/{paper['id']}/pages/1?side=translation").json()['source_sha']
    pair = complete['paragraphs'][0]
    assert pair['original']['text'] == 'First original paragraph.'
    assert pair['translation']['text'] == 'Translated First original paragraph.'
    assert pair['original']['rects'] and pair['translation']['rects']
    assert all(not path.exists() for path in tracking_paths)
    with core.db() as con:
        saved = con.execute('SELECT alignment_json FROM translation_pages WHERE run_id=?', (job['translation_run_id'],)).fetchall()
    assert len(saved) == 2 and all('PRIVATE_PROMPT_NOT_FOR_STORAGE' not in row['alignment_json'] for row in saved)
    assert client.get(f"/api/papers/{paper['id']}/pages/3/alignment").status_code == 404

    core.attach_translation(paper['id'], document(['Manual first paragraph.', 'Manual second paragraph.']))
    current = client.get(endpoint).json()
    assert current['status'] == 'unavailable' and current['reason'] == 'no_tracking'
    assert current['paragraphs'] == [] and current['translation_run_id'] is None
    assert current['translation_sha'] != first['translation_sha']


@pytest.mark.parametrize('tracking_kind', ['missing', 'invalid', 'too_large', 'symlink'])
def test_bad_tracking_never_hides_valid_translated_pdf(setup, monkeypatch, tmp_path, tracking_kind):
    paper = core.add_paper(document(['Original with no usable trace.']), 'inbox')

    class Process:
        returncode = 0

        def __init__(self, argv, **kwargs):
            out = Path(argv[argv.index('--output') + 1])
            (out / 'source.zh.mono.pdf').write_bytes(document(['Valid translated PDF remains readable.']))
            base = Path(argv[argv.index('--working-dir') + 1]) / 'source'
            base.mkdir()
            trace = base / 'translate_tracking.json'
            if tracking_kind == 'invalid':
                trace.write_text('not json')
            elif tracking_kind == 'too_large':
                trace.write_bytes(b' ' * (8 * 1024 * 1024 + 1))
            elif tracking_kind == 'symlink':
                external = tmp_path / 'outside-tracker.json'
                external.write_text('{}')
                trace.symlink_to(external)

        def poll(self):
            return 0

    monkeypatch.setattr(ai.subprocess, 'Popen', Process)
    job = jobs.enqueue(paper['id'], 'inbox', 'translate')
    jobs.run_one()
    assert jobs.get_job(job['id'])['status'] == 'succeeded'
    response = setup.get(f"/api/papers/{paper['id']}/pages/1/alignment").json()
    assert response['status'] == 'unavailable' and response['paragraphs'] == []
    assert setup.get(f"/api/papers/{paper['id']}/pages/1?side=translation").status_code == 200


def test_mapping_migration_and_cancel_are_bound_to_the_same_page(setup):
    paper = core.add_paper(document(['Original paragraph.']), 'inbox')
    with core.db() as con:
        con.execute('ALTER TABLE translation_pages DROP COLUMN alignment_json')
    core.init_db()
    core.init_db()
    job = jobs.enqueue(paper['id'], 'inbox', 'translate')
    jobs._claim()
    translations.start_page(job['translation_run_id'], job['id'], 1)
    jobs.cancel(job['id'])
    with pytest.raises(translations.Stopped):
        translations.publish_page(job['translation_run_id'], job['id'], 1, document(['Late translation.']), alignment={'status': 'ready', 'paragraphs': []})
    response = setup.get(f"/api/papers/{paper['id']}/pages/1/alignment").json()
    assert response['translation_sha'] is None and response['paragraphs'] == []
    with core.db() as con:
        assert con.execute('SELECT alignment_json FROM translation_pages').fetchone()['alignment_json'] is None


def test_pipeline_upgrade_does_not_reuse_old_translation_without_alignment(setup, monkeypatch):
    paper = core.add_paper(document(['Original paragraph.']), 'inbox')
    monkeypatch.setattr(jobs, 'TRANSLATION_PIPELINE_VERSION', 'old-page-only')
    old = jobs.enqueue(paper['id'], 'inbox', 'translate')
    monkeypatch.setattr(ai, 'run_translation_page', lambda *args: document(['Old translated paragraph.']))
    jobs.run_one()
    assert jobs.get_job(old['id'])['status'] == 'succeeded'
    monkeypatch.setattr(jobs, 'TRANSLATION_PIPELINE_VERSION', 'page-pdf-paragraph-tracking-v1')
    new = jobs.enqueue(paper['id'], 'inbox', 'translate')
    assert new['id'] != old['id'] and new['translation_run_id'] != old['translation_run_id']
    assert jobs.enqueue(paper['id'], 'inbox', 'translate')['id'] == new['id']


def test_explicit_retranslation_rebuilds_missing_map_but_coalesces_active_clicks(setup, monkeypatch):
    paper = core.add_paper(document(['Original paragraph.']), 'inbox')
    monkeypatch.setattr(ai, 'run_translation_page', lambda *args: document(['Translated but no tracker.']))
    old = jobs.enqueue(paper['id'], 'inbox', 'translate')
    jobs.run_one()
    assert jobs.enqueue(paper['id'], 'inbox', 'translate')['id'] == old['id']
    body = {'paper_id': paper['id'], 'project_id': 'inbox', 'kind': 'translate', 'force': True}
    response = setup.post('/api/jobs', json=body)
    assert response.status_code == 202
    new = response.json()
    assert new['id'] != old['id'] and new['translation_run_id'] != old['translation_run_id']
    assert setup.post('/api/jobs', json=body).json()['id'] == new['id']
    assert setup.post('/api/jobs', json={**body, 'kind': 'brief'}).status_code == 422
    assert setup.post('/api/jobs', json={**body, 'force': 'yes'}).status_code == 422
