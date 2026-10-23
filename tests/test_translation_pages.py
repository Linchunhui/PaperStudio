"""Progressive publication, interruption, and immutable annotation identity."""
import json
import threading

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai, core, jobs, translations
from app.core_routes import router as core_router
from app.jobs_routes import router as jobs_router


def pdf_bytes(pages=1, text="Translated page"):
    with fitz.open() as doc:
        for n in range(pages):
            page = doc.new_page()
            page.insert_text((50, 70), f"{text} {n + 1}")
        return doc.tobytes()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:9011/v1")
    monkeypatch.setenv("AI_MODEL", "test-local-model")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    jobs._STOP.clear()
    core.init_db()
    jobs.init_jobs()
    app = FastAPI()
    app.include_router(core_router)
    app.include_router(jobs_router)
    paper = core.add_paper(pdf_bytes(3, "Original evidence"), "inbox", "Progressive paper")
    with TestClient(app) as client:
        yield paper, client
    jobs._STOP.clear()


def manifest(client, paper):
    return client.get(f"/api/papers/{paper['id']}/translation-pages").json()


def annotate(client, paper, page):
    response = client.post(f"/api/papers/{paper['id']}/annotations", json={
        "project_id": "inbox", "side": "translation", "page": page["page"],
        "quote": "Translated page", "note": "Important evidence", "color": "yellow",
        "source_sha": page["source_sha"], "translation_run_id": page["translation_run_id"],
        "rects": [{"x": .1, "y": .1, "width": .2, "height": .04}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_page_is_available_before_later_pages_and_retry_keeps_anchors(setup, monkeypatch):
    paper, client = setup
    job = jobs.enqueue(paper["id"], "inbox", "translate")
    assert jobs.enqueue(paper["id"], "inbox", "translate")["id"] == job["id"]
    run_id = job["translation_run_id"]
    seen = {}

    def translate(payload, page, cancelled, progress):
        if page == 2:
            state = manifest(client, paper)
            assert state["active_run_id"] == run_id
            assert [x["status"] for x in state["pages"]] == ["ready", "running", "queued"]
            first = client.get(f"/api/papers/{paper['id']}/pages/1?side=translation")
            assert first.status_code == 200
            seen["first"] = first.json()
            assert client.get(seen["first"]["svg_url"]).status_code == 200
            assert client.get(f"/api/papers/{paper['id']}/pages/2?side=translation").status_code == 409
            seen["annotation"] = annotate(client, paper, state["pages"][0])
            raise ai.ExecutionError("Temporary page failure")
        return pdf_bytes(text=f"Translated page {page}")

    monkeypatch.setattr(ai, "run_translation_page", translate)
    assert jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "failed"
    assert result["coverage"]["ready_pages"] == 2
    state = manifest(client, paper)
    assert [p["status"] for p in state["pages"]] == ["ready", "failed", "ready"]
    assert client.get(f"/api/papers/{paper['id']}/file?side=translation").status_code == 409
    calls = []
    monkeypatch.setattr(ai, "run_translation_page", lambda payload, page, *args: calls.append(page) or pdf_bytes(text="Recovered page"))
    assert jobs.retry(job["id"])["status"] == "queued"
    jobs.run_one()
    assert calls == [2]
    assert jobs.get_job(job["id"])["status"] == "succeeded"
    state = manifest(client, paper)
    assert state["download_ready"] and state["active_run_id"] == run_id
    assert state["pages"][0]["source_sha"] == seen["first"]["source_sha"]
    notes = core.annotation_list(paper["id"], "inbox")
    assert notes[0]["id"] == seen["annotation"]["id"] and notes[0]["is_current"]
    assert client.get(seen["first"]["svg_url"]).status_code == 200
    download = client.get(f"/api/papers/{paper['id']}/file?side=translation")
    assert download.status_code == 200
    with fitz.open(stream=download.content, filetype="pdf") as doc:
        assert len(doc) == 3
    assert jobs.enqueue(paper["id"], "inbox", "translate")["id"] == job["id"]


@pytest.mark.parametrize("action", ["cancel", "manual"])
def test_late_page_after_concurrent_cancel_or_manual_import_is_not_published(setup, monkeypatch, action):
    paper, client = setup
    job = jobs.enqueue(paper["id"], "inbox", "translate")
    page_started, release = threading.Event(), threading.Event()
    seen = {}

    def translate(payload, page, cancelled, progress):
        if page == 2:
            page_started.set()
            assert release.wait(5), "test failed to release translator"
        return pdf_bytes(text=f"Translated page {page}")

    monkeypatch.setattr(ai, "run_translation_page", translate)
    worker = threading.Thread(target=jobs.run_one)
    worker.start()
    try:
        assert page_started.wait(5)
        state = manifest(client, paper)
        assert state["pages"][0]["status"] == "ready"
        seen["annotation"] = annotate(client, paper, state["pages"][0])
        if action == "cancel":
            assert jobs.cancel(job["id"])["status"] == "cancelled"
        else:
            manual = pdf_bytes(3, "Manual revision")
            core.attach_translation(paper["id"], manual)
        release.set()
        worker.join(5)
        assert not worker.is_alive()
    finally:
        release.set()
        worker.join(5)
    with core.db() as con:
        pages = con.execute("SELECT * FROM translation_pages WHERE run_id=? ORDER BY page_no", (job["translation_run_id"],)).fetchall()
    assert [p["status"] for p in pages] == ["ready", "cancelled", "cancelled"]
    assert pages[1]["asset_sha"] is None
    if action == "cancel":
        assert core.annotation_list(paper["id"], "inbox")[0]["is_current"]
        calls = []
        monkeypatch.setattr(ai, "run_translation_page", lambda payload, page, *args: calls.append(page) or pdf_bytes())
        jobs.retry(job["id"])
        jobs.run_one()
        assert calls == [2, 3]
        assert jobs.get_job(job["id"])["status"] == "succeeded"
    else:
        assert jobs.get_job(job["id"])["status"] == "failed"
        assert manifest(client, paper)["mode"] == "manual"
        assert core.paper_path(paper["id"], "translation").read_bytes() == manual
        assert not core.annotation_list(paper["id"], "inbox")[0]["is_current"]
        with pytest.raises(Exception) as error:
            jobs.retry(job["id"])
        assert error.value.status_code == 409


def test_restart_preserves_ready_pages_and_retries_only_unfinished(setup, monkeypatch):
    paper, client = setup
    job = jobs.enqueue(paper["id"], "inbox", "translate")
    jobs._claim()
    run_id = job["translation_run_id"]
    translations.pending_pages(run_id, job["id"])
    translations.start_page(run_id, job["id"], 1)
    translations.publish_page(run_id, job["id"], 1, pdf_bytes())
    translations.start_page(run_id, job["id"], 2)
    before = manifest(client, paper)["pages"][0]["source_sha"]
    jobs.start_worker()
    try:
        assert jobs.get_job(job["id"])["status"] == "interrupted"
        assert [p["status"] for p in manifest(client, paper)["pages"]] == ["ready", "queued", "queued"]
    finally:
        jobs.stop_worker()
        jobs._STOP.clear()
    calls = []
    monkeypatch.setattr(ai, "run_translation_page", lambda payload, page, *args: calls.append(page) or pdf_bytes())
    jobs.retry(job["id"])
    jobs.run_one()
    assert calls == [2, 3]
    assert manifest(client, paper)["pages"][0]["source_sha"] == before


def test_stale_retry_cannot_reset_a_page_already_claimed_by_another_retry(setup, monkeypatch):
    paper, client = setup
    job = jobs.enqueue(paper["id"], "inbox", "translate")
    jobs.cancel(job["id"])
    configure = jobs._configuration

    def concurrent_retry(kind):
        cfg = configure(kind)
        # Deterministic interleaving: another retry+claim wins after this request
        # reads the cancelled row but before it acquires its write transaction.
        with core.db() as con:
            con.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
            con.execute("UPDATE translation_runs SET status='running' WHERE id=?", (job["translation_run_id"],))
            con.execute("UPDATE translation_pages SET status='running' WHERE run_id=? AND page_no=1", (job["translation_run_id"],))
        return cfg

    monkeypatch.setattr(jobs, "_configuration", concurrent_retry)
    result = jobs.retry(job["id"])
    assert result["status"] == "running" and result["reused"]
    assert manifest(client, paper)["pages"][0]["status"] == "running"
    translations.publish_page(job["translation_run_id"], job["id"], 1, pdf_bytes())
    assert manifest(client, paper)["pages"][0]["status"] == "ready"


def test_late_error_does_not_mark_a_retried_queued_run_failed(setup):
    paper, client = setup
    job = jobs.enqueue(paper["id"], "inbox", "translate")
    jobs._claim()
    jobs.cancel(job["id"])
    jobs.retry(job["id"])
    # An old provider invocation fails after the user already canceled/retried.
    jobs._fail(job["id"], "Late error from previous invocation")
    assert jobs.get_job(job["id"])["status"] == "queued"
    assert manifest(client, paper)["status"] == "queued"
