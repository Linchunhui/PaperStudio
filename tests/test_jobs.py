import json
from pathlib import Path
import tomllib

import fitz
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai, core, jobs
from app.core_routes import router as core_router
from app.jobs_routes import router as jobs_router


def make_pdf(text="A small paper with a method, evidence and limitations.", pages=2):
    with fitz.open() as doc:
        for index in range(pages):
            page = doc.new_page()
            page.insert_text((60, 80), f"Page {index + 1}: {text}")
        return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_BASE_URL", "http://127.0.0.1:9001/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MAX_INPUT_CHARS", raising=False)
    monkeypatch.delenv("AI_MAX_OUTPUT_TOKENS", raising=False)
    jobs._STOP.clear()
    core.init_db()
    jobs.init_jobs()
    app = FastAPI()
    app.include_router(core_router)
    app.include_router(jobs_router)
    with TestClient(app) as session:
        yield session
    jobs._STOP.clear()


def upload(client):
    response = client.post("/api/papers", files={"file": ("paper.pdf", make_pdf(), "application/pdf")}, data={"project_id": "inbox", "title": "Evidence paper"})
    assert response.status_code == 201, response.text
    return response.json()


def note(client, paper, project="inbox", text="The limitation matters."):
    response = client.post(f"/api/papers/{paper['id']}/annotations", json={"project_id": project, "side": "original", "page": 1, "quote": "method, evidence and limitations", "note": text, "color": "yellow", "source_sha": paper["sha256"], "rects": [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.03}]})
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, paper, kind="brief", **kwargs):
    result = client.post("/api/jobs", json={"paper_id": paper["id"], "project_id": "inbox", "kind": kind, **kwargs})
    assert result.status_code == 202, result.text
    return result.json()


def test_missing_configuration_does_not_create_fake_job(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setenv("AI_MODEL", "")
    response = client.post("/api/jobs", json={"paper_id": paper["id"], "project_id": "inbox", "kind": "brief"})
    assert response.status_code == 409
    assert client.get("/api/jobs").json() == []
    assert client.get("/api/capabilities").json()["ai_configured"] is False
    assert len(client.get("/api/skills").json()) == 4


def test_remote_provider_requires_key(client, monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "https://example.org/v1")
    assert ai.capabilities()["ai_configured"] is False
    monkeypatch.setenv("AI_API_KEY", "not-a-real-secret")
    assert ai.capabilities()["ai_configured"] is True
    monkeypatch.setenv("AI_BASE_URL", "https://user:password@example.org/v1")
    assert ai.capabilities()["ai_configured"] is False


def test_idempotence_and_skill_version_are_persisted(client):
    paper = upload(client)
    first = submit(client, paper)
    repeated = submit(client, paper)
    assert repeated["id"] == first["id"] and repeated["reused"]
    assert first["status"] == "queued"
    assert first["source_sha"] == paper["sha256"]
    assert len(first["skill_version"]) == 64
    different = submit(client, paper, mode="abstract")
    assert different["id"] != first["id"]


def test_notes_snapshot_is_project_scoped_and_immutable(client):
    paper = upload(client)
    first_note = note(client, paper)
    other = client.post("/api/projects", json={"name": "Other project"}).json()
    client.post(f"/api/papers/{paper['id']}/projects", json={"project_id": other["id"]})
    note(client, paper, other["id"], "Other-project private thought")
    job = submit(client, paper, "notes-summary")
    payload = json.loads(jobs._row(job["id"])["payload"])
    assert len(payload["annotations"]) == 1
    assert payload["annotations"][0]["id"] == first_note["id"]
    assert "Other-project" not in json.dumps(payload)
    client.patch(f"/api/annotations/{first_note['id']}", json={"note": "Changed interpretation"})
    new = submit(client, paper, "notes-summary")
    assert new["id"] != job["id"]
    frozen = json.loads(jobs._row(job["id"])["payload"])
    assert frozen["annotations"][0]["note"] == "The limitation matters."
    wrong = client.post("/api/jobs", json={"paper_id": paper["id"], "project_id": other["id"], "kind": "selection-summary", "annotation_id": first_note["id"]})
    assert wrong.status_code == 404


def test_notes_summary_needs_real_notes(client):
    paper = upload(client)
    response = client.post("/api/jobs", json={"paper_id": paper["id"], "project_id": "inbox", "kind": "notes-summary"})
    assert response.status_code == 409


def test_fulltext_all_pages_and_output_are_honest(client, monkeypatch):
    paper = upload(client)
    seen = {}
    def complete(system, source, cfg):
        seen["source"] = source
        return "## 方法\n以证据约束结论。[PDF 第 1 页]"
    monkeypatch.setattr(ai, "complete", complete)
    job = submit(client, paper)
    assert jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert "PDF 第 1 页" in seen["source"] and "PDF 第 2 页" in seen["source"]
    assert result["coverage"]["total_pages"] == 2
    assert result["coverage"]["visual_evidence_verified"] is False
    assert "未做视觉核验" in result["result_markdown"]
    assert submit(client, paper)["id"] == job["id"]


def test_overlong_source_is_rejected_without_call(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "1000")
    called = []
    monkeypatch.setattr(ai, "complete", lambda *args: called.append(True))
    job = submit(client, paper)
    jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "failed"
    assert "未截断全文" in result["error"]
    assert not called and result["result_markdown"] is None


def test_text_jobs_reuse_a_fulltext_cache_without_reparsing(client, monkeypatch):
    from app import imports
    paper = upload(client)
    content, coverage = ai.extract_fulltext(core.paper_path(paper["id"]))
    seen = []

    def cached(source_sha):
        assert source_sha == paper["sha256"]
        return {"fulltext": content, "coverage": coverage}

    monkeypatch.setattr(imports, "get_cached_fulltext", cached)
    monkeypatch.setattr(ai, "extract_fulltext", lambda *_: pytest.fail("Cached PDF was parsed again"))
    monkeypatch.setattr(ai, "complete", lambda system, source, cfg: seen.append(source) or "基于全文文本缓存。[PDF 第 1 页]")
    job = submit(client, paper)
    assert jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert content in seen[0] and result["coverage"]["total_pages"] == 2


def test_cancel_discards_late_ai_output_and_retry_works(client, monkeypatch):
    paper = upload(client)
    job = submit(client, paper)
    def late(payload, cancelled, progress):
        jobs.cancel(job["id"])
        return "Late result must not be committed", {}
    monkeypatch.setattr(ai, "run_text", late)
    jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "cancelled" and result["result_markdown"] is None
    assert client.post(f"/api/jobs/{job['id']}/retry").json()["status"] == "queued"
    monkeypatch.setattr(ai, "run_text", lambda *args: ("A retried result", {"scope": "test"}))
    jobs.run_one()
    assert jobs.get_job(job["id"])["status"] == "succeeded"
    assert jobs.get_job(job["id"])["attempt"] == 2


def test_restart_marks_inflight_interrupted_without_resubmission(client):
    paper = upload(client)
    job = submit(client, paper)
    with core.db() as con:
        con.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    jobs.start_worker()
    try:
        assert jobs.get_job(job["id"])["status"] == "interrupted"
    finally:
        jobs.stop_worker()
    assert jobs.get_job(job["id"])["attempt"] == 0


def test_changed_execution_config_requires_new_job(client, monkeypatch):
    paper = upload(client)
    job = submit(client, paper)
    jobs.cancel(job["id"])
    monkeypatch.setenv("AI_MODEL", "another-model")
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    assert submit(client, paper)["id"] != job["id"]


def test_provider_errors_and_truncation_do_not_leak_secrets(client, monkeypatch):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "SECRET_FROM_PROVIDER"}))
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(ai.ExecutionError, match="HTTP 401") as error:
        ai.complete("system", "source", ai.settings())
    assert "SECRET_FROM_PROVIDER" not in str(error.value)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "Partial"}}]}))
    with pytest.raises(ai.ExecutionError, match="截断"):
        ai.complete("system", "source", ai.settings())


def test_optional_translation_is_explicit_and_config_is_not_in_argv(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(ai, "translation_executable", lambda: None)
    response = client.post("/api/jobs", json={"paper_id": paper["id"], "project_id": "inbox", "kind": "translate"})
    assert response.status_code == 409
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    monkeypatch.setenv("AI_API_KEY", "SECRET_NOT_IN_ARGV")
    observed = {}
    class Process:
        returncode = 0
        def __init__(self, argv, **kwargs):
            observed["argv"] = argv
            config_path = Path(argv[argv.index("--config") + 1])
            observed["config_path"] = config_path
            assert config_path.stat().st_mode & 0o777 == 0o600
            configuration = tomllib.loads(config_path.read_text())["babeldoc"]
            assert configuration["openai-api-key"] == "SECRET_NOT_IN_ARGV"
            assert configuration["no-dual"] and not configuration["no-mono"]
            assert kwargs["shell"] is False
            out = Path(argv[argv.index("--output") + 1])
            # Each invocation really receives exactly one original PDF page.
            source = Path(argv[argv.index("--files") + 1])
            with fitz.open(source) as document:
                assert len(document) == 1
            (out / "paper.zh.mono.pdf").write_bytes(source.read_bytes())
        def poll(self):
            return 0
    monkeypatch.setattr(ai.subprocess, "Popen", Process)
    job = submit(client, paper, "translate")
    jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "succeeded", result
    assert "SECRET_NOT_IN_ARGV" not in " ".join(observed["argv"])
    assert not observed["config_path"].exists()
    with fitz.open(core.paper_path(paper["id"], "translation")) as document:
        assert len(document) == 2
    manifest = client.get(f"/api/papers/{paper['id']}/translation-pages").json()
    assert all(page["status"] == "ready" for page in manifest["pages"])


@pytest.mark.parametrize("has_previous_translation", [False, True])
def test_translation_preserves_manual_revision_imported_during_execution(client, monkeypatch, has_previous_translation):
    paper = upload(client)
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    if has_previous_translation:
        core.attach_translation(paper["id"], make_pdf("Earlier translation."))
    baseline = core.get_paper(paper["id"])["translated_sha256"]
    job = submit(client, paper, "translate")
    assert json.loads(jobs._row(job["id"])["payload"])["translation_base_sha"] == baseline
    manual_pdf = make_pdf("Manual revision must survive a late background result.")
    automatic_pdf = make_pdf("A background translation based on the older snapshot.", pages=1)
    observed = {}

    def translate_while_user_imports(payload, cancelled, progress):
        assert jobs.get_job(job["id"])["status"] == "running"
        response = client.post(f"/api/papers/{paper['id']}/translation", files={"file": ("manual.pdf", manual_pdf, "application/pdf")})
        assert response.status_code == 200, response.text
        observed["manual_sha"] = response.json()["translated_sha256"]
        return automatic_pdf

    monkeypatch.setattr(ai, "run_translation", translate_while_user_imports)
    jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "failed", result
    assert "保留现有译文" in result["error"]
    assert result["result_markdown"] is None
    current = core.get_paper(paper["id"])
    assert current["translated_sha256"] == observed["manual_sha"]
    assert current["sha256"] == paper["sha256"]
    assert core.paper_path(paper["id"], "translation").read_bytes() == manual_pdf
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409

    # Explicitly submitting again snapshots the manual revision and can replace
    # it if no further edit occurs. It must not reuse the obsolete failed job.
    new_job = submit(client, paper, "translate")
    assert new_job["id"] != job["id"]
    assert json.loads(jobs._row(new_job["id"])["payload"])["translation_base_sha"] == observed["manual_sha"]
    monkeypatch.setattr(ai, "run_translation", lambda *args: automatic_pdf)
    jobs.run_one()
    assert jobs.get_job(new_job["id"])["status"] == "succeeded"
    with fitz.open(core.paper_path(paper["id"], "translation")) as document:
        assert len(document) == 2
        assert all("background translation" in page.get_text() for page in document)


def test_stale_queued_translation_does_not_call_provider(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    job = submit(client, paper, "translate")
    manual_pdf = make_pdf("A manual translation imported while the task was queued.")
    core.attach_translation(paper["id"], manual_pdf)
    calls = []
    monkeypatch.setattr(ai, "run_translation", lambda *args: calls.append(True))
    jobs.run_one()
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert "保留现有译文" in jobs.get_job(job["id"])["error"]
    assert not calls
    assert core.paper_path(paper["id"], "translation").read_bytes() == manual_pdf


def test_legacy_translation_without_baseline_requires_new_submission(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(ai, "translation_executable", lambda: "/opt/babeldoc")
    job = submit(client, paper, "translate")
    payload = json.loads(jobs._row(job["id"])["payload"])
    del payload["translation_base_sha"]
    with core.db() as con:
        con.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"]))
    calls = []
    monkeypatch.setattr(ai, "run_translation", lambda *args: calls.append(True))
    jobs.run_one()
    assert jobs.get_job(job["id"])["status"] == "failed"
    assert "没有译文版本快照" in jobs.get_job(job["id"])["error"]
    assert not calls
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409

