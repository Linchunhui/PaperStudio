import hashlib
import json

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ai, core, jobs, reading
from app.core_routes import router as core_router
from app.jobs_routes import router as jobs_router
from app.reading_routes import router as reading_router


def make_pdf(*, rotation=0, crop=False, blank=False):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        if not blank:
            page.insert_text((60, 70), "A paper heading", fontsize=18)
            page.insert_textbox((60, 120, 270, 200), "Left first paragraph\nwith a line break.", fontsize=11)
            page.insert_textbox((330, 120, 540, 200), "Right first paragraph\nwith its own text.", fontsize=11)
            page.insert_textbox((60, 240, 270, 320), "Left second paragraph.", fontsize=11)
            page.insert_textbox((330, 240, 540, 320), "Right second paragraph.", fontsize=11)
        if crop:
            page.set_cropbox(fitz.Rect(30, 30, 570, 770))
        page.set_rotation(rotation)
        doc.new_page(width=600, height=800).insert_text((60, 100), "A different page.")
        return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_BASE_URL", "http://127.0.0.1:9001/v1")
    monkeypatch.setenv("AI_MODEL", "paragraph-test")
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MAX_INPUT_CHARS", raising=False)
    monkeypatch.delenv("AI_MAX_OUTPUT_TOKENS", raising=False)
    jobs._STOP.clear()
    core.init_db()
    jobs.init_jobs()
    app = FastAPI()
    app.include_router(core_router)
    app.include_router(jobs_router)
    app.include_router(reading_router)
    with TestClient(app) as session:
        yield session
    jobs._STOP.clear()


def upload(client, **kwargs):
    response = client.post("/api/papers", files={"file": ("paper.pdf", make_pdf(**kwargs), "application/pdf")}, data={"project_id": "inbox"})
    assert response.status_code == 201, response.text
    return response.json()


def page(client, paper, number=1, project="inbox"):
    response = client.get(f"/api/papers/{paper['id']}/pages/{number}/reading", params={"project_id": project} if project else {})
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, paper, block=None, **overrides):
    if block is None:
        block = page(client, paper)["blocks"][1]
    body = {"project_id": "inbox", "page": 1, "block_id": block["id"], "source_sha": paper["sha256"], **overrides}
    return client.post(f"/api/papers/{paper['id']}/reading/translate", json=body)


def test_reading_get_is_model_free_ordered_and_stable(client, monkeypatch):
    paper = upload(client)
    monkeypatch.setattr(ai, "complete", lambda *a: pytest.fail("GET must never call a model"))
    first = page(client, paper)
    second = page(client, paper)
    assert first == second
    assert first["source_sha"] == paper["sha256"] and first["has_text"]
    assert len(first["blocks"]) == 5
    assert [b["text"] for b in first["blocks"]] == ["A paper heading", "Left first paragraph with a line break.", "Left second paragraph.", "Right first paragraph with its own text.", "Right second paragraph."]
    assert first["blocks"][0]["type"] == "heading"
    assert first["blocks"][1]["type"] == "paragraph"
    assert all(b["translation"] is None for b in first["blocks"])
    assert len(first["blocks"][1]["rects"]) == 2
    assert client.get("/api/jobs").json() == []
    assert first["warnings"]
    assert page(client, paper, 2)["blocks"][0]["id"] not in {b["id"] for b in first["blocks"]}


def test_full_width_sections_separate_column_reading_regions(client):
    with fitz.open() as document:
        source = document.new_page(width=600, height=800)
        source.insert_text((60, 55), "A FULL WIDTH HEADING TO SEPARATE COLUMNS", fontsize=18)
        source.insert_text((60, 120), "Left first paragraph", fontsize=11)
        source.insert_text((330, 120), "Right first paragraph", fontsize=11)
        source.insert_text((60, 240), "Left second paragraph", fontsize=11)
        source.insert_text((330, 240), "Right second paragraph", fontsize=11)
        source.insert_text((60, 390), "ANOTHER WIDE HEADING TO START THE NEXT SECTION", fontsize=15)
        source.insert_text((60, 440), "Left third paragraph", fontsize=11)
        source.insert_text((330, 440), "Right third paragraph", fontsize=11)
        paper = core.add_paper(document.tobytes(), "inbox")
    content = page(client, paper)["blocks"]
    assert [b["text"] for b in content][1:5] == ["Left first paragraph", "Left second paragraph", "Right first paragraph", "Right second paragraph"]
    assert "ANOTHER WIDE" in content[5]["text"]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_reflow_rectangles_match_cropped_rotated_native_pdf(client, rotation):
    paper = upload(client, rotation=rotation, crop=True)
    result = page(client, paper)
    heading = result["blocks"][0]
    with fitz.open(core.paper_path(paper["id"])) as document:
        source = document[0]
        bbox = source.search_for("A paper heading")[0] * source.rotation_matrix
        rect = heading["rects"][0]
        assert rect["x"] == pytest.approx(bbox.x0 / source.rect.width)
        assert rect["y"] == pytest.approx(bbox.y0 / source.rect.height)
        assert rect["width"] == pytest.approx(bbox.width / source.rect.width)
        assert rect["height"] == pytest.approx(bbox.height / source.rect.height)
    for block in result["blocks"]:
        for r in block["rects"]:
            assert 0 <= r["x"] < r["x"] + r["width"] <= 1.000001
            assert 0 <= r["y"] < r["y"] + r["height"] <= 1.000001


def test_blank_page_and_invalid_scope_are_explicit(client, monkeypatch):
    paper = upload(client, blank=True)
    result = page(client, paper)
    assert result["blocks"] == [] and not result["has_text"]
    assert "扫描页" in result["warnings"][0]
    assert client.get(f"/api/papers/{paper['id']}/pages/0/reading").status_code == 404
    assert client.get(f"/api/papers/{paper['id']}/pages/3/reading").status_code == 404
    assert client.get("/api/papers/missing/pages/1/reading").status_code == 404
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert client.get(f"/api/papers/{paper['id']}/pages/1/reading", params={"project_id": other}).status_code == 409
    monkeypatch.setenv("AI_MODEL", "")
    assert not page(client, paper)["has_text"]


def test_paragraph_job_is_explicit_source_scoped_cached_and_durable(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    seen = []
    def complete(system, source, cfg):
        seen.append((system, json.loads(source), cfg["model"]))
        return "左侧第一段，包含换行。"
    monkeypatch.setattr(ai, "complete", complete)
    first = submit(client, paper, block).json()
    again = submit(client, paper, block).json()
    assert first["kind"] == "paragraph-translate" and first["status"] == "queued"
    assert first["block_id"] == block["id"] and first["page"] == 1
    assert first["skill_adapter"] == "paragraph-text"
    assert first["skill_version"] == hashlib.sha256(reading.TRANSLATION_SYSTEM.encode()).hexdigest()
    assert again["id"] == first["id"] and again["reused"]
    assert not seen
    assert jobs.run_one()
    finished = client.get(f"/api/jobs/{first['id']}").json()
    assert finished["status"] == "succeeded" and finished["result_markdown"] == "左侧第一段，包含换行。"
    assert finished["coverage"]["scope"] == "pdf-paragraph"
    assert seen[0][1]["paragraph_text"] == block["text"]
    assert "只返回该段中文译文" in seen[0][0]
    assert "Right first" not in json.dumps(seen)
    assert submit(client, paper, block).json()["id"] == first["id"]
    assert len(seen) == 1
    assert page(client, paper)["blocks"][1]["translation"]["id"] == first["id"]
    jobs.init_jobs()  # Existing cached result survives schema initialization.
    assert page(client, paper)["blocks"][1]["translation"]["result_markdown"] == "左侧第一段，包含换行。"
    monkeypatch.setenv("AI_MODEL", "")
    assert page(client, paper)["blocks"][1]["translation"]["result_markdown"] == "左侧第一段，包含换行。"
    assert submit(client, paper, block).status_code == 409
    assert core.get_paper(paper["id"])["active_translation_run_id"] is None


def test_translation_validation_never_accepts_client_text_or_foreign_blocks(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    monkeypatch.setattr(ai, "complete", lambda *a: pytest.fail("invalid input called model"))
    assert submit(client, paper, block, source_sha="0" * 64).status_code == 409
    assert submit(client, paper, block, block_id="unknown").status_code == 404
    assert submit(client, paper, block, page=2).status_code == 404
    assert submit(client, paper, block, text="forged source").status_code == 422
    assert submit(client, paper, block, page=True).status_code == 422
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    assert submit(client, paper, block, project_id=other).status_code == 409
    monkeypatch.setenv("AI_MODEL", "")
    assert submit(client, paper, block).status_code == 409
    assert page(client, paper)["blocks"][1]["translation"] is None
    assert client.get("/api/jobs").json() == []


def test_cache_isolates_provider_prompt_and_project(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    monkeypatch.setattr(ai, "complete", lambda *a: "译文")
    first = submit(client, paper, block).json()
    jobs.run_one()
    monkeypatch.setenv("AI_MODEL", "another-model")
    assert page(client, paper)["blocks"][1]["translation"] is None
    second = submit(client, paper, block).json()
    assert first["id"] != second["id"]
    jobs.run_one()
    monkeypatch.setattr(reading, "PROMPT_VERSION", "changed-prompt-version")
    assert page(client, paper)["blocks"][1]["translation"] is None
    third = submit(client, paper, block).json()
    assert third["id"] not in {first["id"], second["id"]}
    other = client.post("/api/projects", json={"name": "Other"}).json()["id"]
    client.post(f"/api/papers/{paper['id']}/projects", json={"project_id": other})
    assert page(client, paper, project=other)["blocks"][1]["translation"] is None
    assert submit(client, paper, block, project_id=other).json()["id"] != third["id"]


def test_cancel_retry_and_provider_changes_use_existing_job_guards(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    called = []
    monkeypatch.setattr(ai, "complete", lambda *args: called.append(1) or "译文")
    first = submit(client, paper, block).json()
    assert client.post(f"/api/jobs/{first['id']}/cancel").json()["status"] == "cancelled"
    assert not jobs.run_one() and not called
    assert client.post(f"/api/jobs/{first['id']}/retry").json()["status"] == "queued"
    monkeypatch.setenv("AI_MODEL", "changed-while-queued")
    jobs.run_one()
    assert client.get(f"/api/jobs/{first['id']}").json()["status"] == "failed"
    assert not called
    monkeypatch.setenv("AI_MODEL", "paragraph-test")
    client.post(f"/api/jobs/{first['id']}/retry")
    jobs.run_one()
    assert called == [1]


def test_input_and_extraction_limits_do_not_truncate_or_call_model(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    monkeypatch.setattr(ai, "complete", lambda *a: pytest.fail("bounded failure called model"))
    monkeypatch.setattr(reading, "MAX_BLOCK_CHARS", 10)
    assert submit(client, paper, block).status_code == 422
    monkeypatch.setattr(reading, "MAX_BLOCK_CHARS", 24000)
    monkeypatch.setattr(reading, "TRANSLATION_SYSTEM", "x" * 1100)
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "1000")
    job = submit(client, paper, block).json()
    jobs.run_one()
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    monkeypatch.setattr(reading, "MAX_PAGE_CHARS", 10)
    assert client.get(f"/api/papers/{paper['id']}/pages/1/reading").status_code == 422


def test_cancel_while_model_runs_does_not_publish_late_translation(client, monkeypatch):
    paper = upload(client)
    job = submit(client, paper).json()
    def complete(*args):
        jobs.cancel(job["id"])
        return "Late text should not publish"
    monkeypatch.setattr(ai, "complete", complete)
    jobs.run_one()
    result = jobs.get_job(job["id"])
    assert result["status"] == "cancelled" and result["result_markdown"] is None


def test_paragraph_jobs_are_prioritized_over_other_queued_work(client, monkeypatch):
    paper = upload(client)
    ordinary = jobs.enqueue(paper["id"], "inbox", "brief")
    paragraph = submit(client, paper).json()
    monkeypatch.setattr(ai, "complete", lambda *a: "段落译文")
    assert jobs.run_one()
    assert jobs.get_job(paragraph["id"])["status"] == "succeeded"
    assert jobs.get_job(ordinary["id"])["status"] == "queued"


def test_layout_translation_services_paragraphs_at_page_boundaries(client, monkeypatch):
    paper = upload(client)
    block = page(client, paper)["blocks"][1]
    monkeypatch.setattr(ai, "translation_executable", lambda: "/controlled/babeldoc")
    events = []
    paragraph = []
    def translate_page(payload, number, cancelled, progress):
        events.append(f"pdf-{number}")
        if number == 1:
            paragraph.append(submit(client, paper, block).json())
        with fitz.open() as document:
            document.new_page().insert_text((50, 100), "Translated page")
            return document.tobytes()
    def complete(*args):
        events.append("paragraph")
        return "所选段落译文"
    monkeypatch.setattr(ai, "run_translation_page", translate_page)
    monkeypatch.setattr(ai, "complete", complete)
    layout = jobs.enqueue(paper["id"], "inbox", "translate")
    assert jobs.run_one()
    assert events == ["pdf-1", "paragraph", "pdf-2"]
    assert jobs.get_job(layout["id"])["status"] == "succeeded"
    assert jobs.get_job(paragraph[0]["id"])["status"] == "succeeded"

