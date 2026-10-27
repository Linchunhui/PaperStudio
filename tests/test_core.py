import hashlib
import math
import xml.etree.ElementTree as ET

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import core, pdf
from app.core_routes import router


def make_pdf(text="A native PDF page with selectable evidence.", pages=2, rotated=False, cropped=False):
    with fitz.open() as doc:
        for _ in range(pages):
            page = doc.new_page(width=612, height=792)
            page.insert_text((80, 110), text, fontsize=13)
            page.draw_rect(fitz.Rect(80, 150, 240, 210), color=(0.1, 0.5, 0.4))
            if cropped:
                page.set_cropbox(fitz.Rect(40, 40, 500, 700))
            if rotated:
                page.set_rotation(90)
        return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    core.init_db()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as session:
        yield session


def upload(client, content=None, project="inbox", title="Paper A"):
    response = client.post("/api/papers", data={"project_id": project, "title": title}, files={"file": ("paper.pdf", content or make_pdf(), "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def project(client, name="Child", parent_id=None):
    response = client.post("/api/projects", json={"name": name, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def annotate(client, paper, project_id="inbox", **kwargs):
    payload = {"project_id": project_id, "side": "original", "page": 1, "quote": "selectable evidence", "note": "Inspect conditions before generalizing.", "color": "yellow", "source_sha": paper["sha256"], "rects": [{"x": .1, "y": .1, "width": .3, "height": .03}]}
    payload.update(kwargs)
    return client.post(f"/api/papers/{paper['id']}/annotations", json=payload)


def test_original_pdf_upload_hash_dedup_and_membership(client):
    content = make_pdf()
    paper = upload(client, content)
    other = project(client, "Other research")
    duplicate = upload(client, content, other["id"], "Another display title")
    assert duplicate["id"] == paper["id"]
    assert len(duplicate["memberships"]) == 2
    assert duplicate["title"] == "Paper A"
    assert paper["sha256"] == hashlib.sha256(content).hexdigest()
    response = client.get(f"/api/papers/{paper['id']}/file")
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert len(list((core.DATA_DIR / "files").iterdir())) == 1


def test_vector_svg_and_text_coordinates_on_rotated_cropped_page(client):
    paper = upload(client, make_pdf(rotated=True, cropped=True))
    page = client.get(f"/api/papers/{paper['id']}/pages/1").json()
    assert (page["width"], page["height"]) == (660, 460)
    assert page["has_text"] and "selectable evidence" in "".join(s["text"] for s in page["text"])
    span = page["text"][0]
    assert span["rotation"] == pytest.approx(90)
    for span in page["text"]:
        theta = math.radians(span["rotation"])
        u = (math.cos(theta) * span["width"], math.sin(theta) * span["width"])
        v = (-math.sin(theta) * span["height"], math.cos(theta) * span["height"])
        for a, b in [(0, 0), u, v, (u[0] + v[0], u[1] + v[1])]:
            assert 0 <= span["x"] + a <= page["width"]
            assert 0 <= span["y"] + b <= page["height"]
    response = client.get(page["svg_url"])
    assert response.status_code == 200
    root = ET.fromstring(response.text)
    assert "660" in root.attrib["width"] and "460" in root.attrib["height"]
    assert "<path" in response.text and "<image" not in response.text
    assert "<script" not in response.text
    assert client.get(f"/api/papers/{paper['id']}/pages/0").status_code == 404
    assert client.get(f"/api/papers/{paper['id']}/pages/3.svg").status_code == 404


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_native_png_preserves_pdf_pixels_crop_rotation_and_source_version(client, rotation):
    with fitz.open(stream=make_pdf(cropped=True), filetype="pdf") as doc:
        doc[0].insert_text((80, 140), "Faux bold label", render_mode=2, border_width=.15)
        doc[0].set_rotation(rotation)
        original = doc.tobytes()
    paper = upload(client, original)
    metadata = client.get(f"/api/papers/{paper['id']}/pages/1").json()
    url = metadata["image_url"]
    response = client.get(url, params={"width": 920})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"].startswith("private")
    with fitz.open(stream=original, filetype="pdf") as doc:
        native = doc[0]
        expected = native.get_pixmap(matrix=fitz.Matrix(920 / native.rect.width, 920 / native.rect.width), alpha=False)
        actual = fitz.Pixmap(response.content)
        assert actual.samples == expected.samples
        clip = fitz.Rect(native.rect.width * .1, native.rect.height * .1, native.rect.width * .6, native.rect.height * .4)
        scale = min(920 / clip.width, 8)
        crop = client.get(url, params={"width": 920, "x": .1, "y": .1, "w": .5, "h": .3})
        assert crop.status_code == 200
        expected_crop = native.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        assert fitz.Pixmap(crop.content).samples == expected_crop.samples
    assert client.get(url.replace(paper["sha256"], "0" * 64)).status_code == 409
    assert client.get(url, params={"x": .8, "w": .3}).status_code == 422
    assert client.get(url, params={"width": 10000}).status_code == 422
    assert client.get(url, params={"x": "nan"}).status_code == 422
    assert client.get(f"/api/papers/{paper['id']}/file").content == original


def test_project_tree_cycle_rejected_and_descendant_filter(client):
    parent = project(client, "Parent")
    child = project(client, "Child", parent["id"])
    leaf = project(client, "Leaf", child["id"])
    paper = upload(client, project=leaf["id"])
    response = client.patch(f"/api/projects/{parent['id']}", json={"parent_id": leaf["id"]})
    assert response.status_code == 422
    assert core.get_project(parent["id"])["parent_id"] is None
    assert client.get(f"/api/papers?project_id={parent['id']}").json() == []
    assert client.get(f"/api/papers?project_id={parent['id']}&include_children=true").json()[0]["id"] == paper["id"]
    assert client.patch(f"/api/projects/{child['id']}", json={"parent_id": None}).json()["parent_id"] is None


def test_notes_and_reading_state_are_project_specific(client):
    paper = upload(client)
    second = project(client)
    client.post(f"/api/papers/{paper['id']}/projects", json={"project_id": second["id"]})
    note = annotate(client, paper).json()
    other = annotate(client, paper, project_id=second["id"], note="A different research question").json()
    assert note["id"] != other["id"]
    assert client.get(f"/api/papers/{paper['id']}/annotations?project_id=inbox").json()[0]["note"].startswith("Inspect")
    assert client.get(f"/api/papers/{paper['id']}/annotations?project_id={second['id']}").json()[0]["note"] == "A different research question"
    state = client.patch(f"/api/papers/{paper['id']}/reading", json={"project_id": "inbox", "status": "read", "last_page": 2})
    assert state.status_code == 200
    memberships = {m["project_id"]: m for m in core.get_paper(paper["id"])["memberships"]}
    assert memberships["inbox"]["status"] == "read"
    assert memberships[second["id"]]["status"] == "unread"
    assert client.patch(f"/api/papers/{paper['id']}/reading", json={"project_id": "inbox", "status": "reading", "last_page": 3}).status_code == 422
    updated = client.patch(f"/api/annotations/{note['id']}", json={"note": "Revised question", "color": "green"})
    assert updated.json()["note"] == "Revised question"
    exported = client.get(f"/api/papers/{paper['id']}/notes.md?project_id=inbox")
    assert "Revised question" in exported.text and "A different research question" not in exported.text
    assert client.delete(f"/api/annotations/{note['id']}").status_code == 204
    assert client.get(f"/api/papers/{paper['id']}/annotations?project_id=inbox").json() == []


def test_note_validation_hash_page_rectangle_and_membership(client):
    paper = upload(client)
    unlinked = project(client)
    assert annotate(client, paper, project_id=unlinked["id"]).status_code == 409
    assert annotate(client, paper, source_sha="0" * 64).status_code == 409
    assert annotate(client, paper, page=5).status_code == 422
    assert annotate(client, paper, rects=[{"x": .9, "y": .1, "width": .3, "height": .03}]).status_code == 422
    assert annotate(client, paper, quote="  ").status_code == 422
    assert annotate(client, paper, side="translation").status_code == 409
    assert annotate(client, paper).status_code == 201


def test_translation_preserves_original_and_versioned_annotations(client):
    original = make_pdf()
    paper = upload(client, original)
    endpoint = f"/api/papers/{paper['id']}/translation"
    bad = client.post(endpoint, files={"file": ("translated.pdf", make_pdf(pages=1), "application/pdf")})
    assert bad.status_code == 422
    translated = make_pdf("Translated document version one.")
    response = client.post(endpoint, files={"file": ("translated.pdf", translated, "application/pdf")})
    assert response.status_code == 200
    attached = response.json()
    assert attached["translated_sha256"] == hashlib.sha256(translated).hexdigest()
    note = annotate(client, attached, side="translation", source_sha=attached["translated_sha256"])
    assert note.status_code == 201
    old_svg = client.get(f"/api/papers/{paper['id']}/pages/1?side=translation").json()["svg_url"]
    client.post(endpoint, files={"file": ("translated.pdf", make_pdf("New translation version."), "application/pdf")})
    notes = client.get(f"/api/papers/{paper['id']}/annotations?project_id=inbox").json()
    assert notes[0]["is_current"] is False
    assert client.get(old_svg).status_code == 409
    assert client.get(f"/api/papers/{paper['id']}/file").content == original
    assert core.get_paper(paper["id"])["sha256"] == paper["sha256"]


def test_invalid_pdf_and_unowned_resources_rejected(client):
    invalid = client.post("/api/papers", files={"file": ("payload.pdf", b"<script>alert(1)</script>", "application/pdf")})
    assert invalid.status_code == 422
    paper = upload(client)
    with core.db() as connection:
        connection.execute("INSERT INTO projects(id,name,parent_id,created_at,owner_id) VALUES('foreign','Private',NULL,?,'other')", (core.now(),))
    assert client.post(f"/api/papers/{paper['id']}/projects", json={"project_id": "foreign"}).status_code == 404
    assert client.get("/api/papers?project_id=foreign").status_code == 404
    assert client.get(f"/api/papers/{paper['id']}/file?side=../../private").status_code == 422
    assert client.get("/api/papers?q=%25").json() == []


def test_translation_cas_preserves_new_manual_upload(client):
    paper = upload(client)
    original_sha = paper["sha256"]
    first = core.attach_translation(paper["id"], make_pdf("First translation"), expected_translation_sha=None)
    manual_bytes = make_pdf("Human revised translation")
    manual = core.attach_translation(paper["id"], manual_bytes)
    for stale_baseline in (None, first["translated_sha256"]):
        with pytest.raises(HTTPException) as error:
            core.attach_translation(paper["id"], make_pdf("Outdated background result"), expected_translation_sha=stale_baseline)
        assert error.value.status_code == 409
        assert "保留现有译文" in error.value.detail
        assert core.get_paper(paper["id"])["translated_sha256"] == manual["translated_sha256"]
        assert core.paper_path(paper["id"], "translation").read_bytes() == manual_bytes
    revised = core.attach_translation(paper["id"], make_pdf("Explicit updated background result"), expected_translation_sha=manual["translated_sha256"])
    assert revised["translated_sha256"] != manual["translated_sha256"]
    assert revised["sha256"] == original_sha


def test_two_translation_jobs_with_same_empty_baseline_cannot_both_win(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    paper = upload(client)
    outputs = [make_pdf("Translation worker A"), make_pdf("Translation worker B")]
    barrier = Barrier(2)
    original_store = core._store_pdf

    def synchronized_store(content, digest):
        stored = original_store(content, digest)
        barrier.wait(timeout=5)
        return stored

    monkeypatch.setattr(core, "_store_pdf", synchronized_store)

    def attach(content):
        try:
            core.attach_translation(paper["id"], content, expected_translation_sha=None)
            return 200, hashlib.sha256(content).hexdigest()
        except HTTPException as error:
            return error.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attach, outputs))
    assert sorted(status for status, _ in results) == [200, 409]
    winner_sha = next(sha for status, sha in results if status == 200)
    assert core.get_paper(paper["id"])["translated_sha256"] == winner_sha


def _progressive_run(paper):
    run_id = core.uid()
    with core.db() as connection:
        connection.execute(
            "INSERT INTO translation_runs(id,paper_id,job_id,source_sha,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, paper["id"], core.uid(), paper["sha256"], "running", core.now(), core.now()),
        )
        connection.execute("UPDATE papers SET active_translation_run_id=? WHERE id=?", (run_id, paper["id"]))
        for page in range(1, paper["page_count"] + 1):
            connection.execute(
                "INSERT INTO translation_pages(run_id,page_no,status,updated_at) VALUES(?,?,'queued',?)",
                (run_id, page, core.now()),
            )
    return run_id


def _ready_page(run_id, page_no, text):
    content = make_pdf(text, pages=1)
    digest = hashlib.sha256(content).hexdigest()
    filename = core._store_pdf(content, digest)
    with core.db() as connection:
        connection.execute("UPDATE translation_pages SET status='ready',asset_sha=?,filename=? WHERE run_id=? AND page_no=?", (digest, filename, run_id, page_no))
    return digest


def test_page_manifest_and_continuous_reading_position(client):
    paper = upload(client, make_pdf(rotated=True, cropped=True))
    manifest = client.get(f"/api/papers/{paper['id']}/page-manifest").json()
    assert manifest["source_sha"] == paper["sha256"]
    assert len(manifest["pages"]) == 2
    assert manifest["pages"][1] == {"page": 2, "width": 660, "height": 460, "rotation": 90}
    assert all("text" not in page for page in manifest["pages"])
    url = f"/api/papers/{paper['id']}/reading"
    response = client.patch(url, json={"project_id": "inbox", "status": "reading", "last_page": 2, "page_offset": .625})
    assert response.status_code == 200 and response.json()["page_offset"] == .625
    assert client.patch(url, json={"project_id": "inbox", "status": "read"}).json()["page_offset"] == .625
    assert client.patch(url, json={"project_id": "inbox", "status": "read", "last_page": 1}).json()["page_offset"] == 0
    assert client.patch(url, json={"project_id": "inbox", "status": "read", "page_offset": 1.1}).status_code == 422


def test_progressive_ready_page_is_readable_and_anchor_survives_later_page(client):
    paper = upload(client)
    run_id = _progressive_run(paper)
    sha = _ready_page(run_id, 1, "First translated page")
    url = f"/api/papers/{paper['id']}"
    first = client.get(url + "/pages/1?side=translation").json()
    assert first["source_sha"] == sha and first["translation_run_id"] == run_id and first["page"] == 1
    assert client.get(first["svg_url"]).status_code == 200
    assert client.get(url + "/pages/2?side=translation").status_code == 409
    assert client.get(url + "/file?side=translation").status_code == 409
    manifest = client.get(url + "/translation-pages").json()
    assert manifest["active_run_id"] == run_id and manifest["mode"] == "progressive"
    assert [page["status"] for page in manifest["pages"]] == ["ready", "queued"]
    note = annotate(client, paper, side="translation", source_sha=sha, translation_run_id=run_id)
    assert note.status_code == 201, note.text
    assert annotate(client, paper, side="translation", source_sha=sha).status_code == 409
    assert annotate(client, paper, side="translation", source_sha=sha, translation_run_id=core.uid()).status_code == 409
    _ready_page(run_id, 2, "Second translated page")
    assert core.get_paper(paper["id"])["translated_sha256"] is None
    assert client.get(url + "/annotations?project_id=inbox").json()[0]["is_current"] is True
    assert client.get(first["svg_url"]).status_code == 200
    second = client.get(url + "/pages/2?side=translation").json()
    assert second["page"] == 2 and "Second translated page" in "".join(item["text"] for item in second["text"])


def test_manual_translation_supersedes_run_but_preserves_historical_page_asset(client):
    paper = upload(client)
    run_id = _progressive_run(paper)
    sha = _ready_page(run_id, 1, "Page ready before manual import")
    url = f"/api/papers/{paper['id']}"
    old_page = client.get(url + "/pages/1?side=translation").json()
    assert annotate(client, paper, side="translation", source_sha=sha, translation_run_id=run_id).status_code == 201
    response = client.post(url + "/translation", files={"file": ("manual.pdf", make_pdf("Human version"), "application/pdf")})
    assert response.status_code == 200 and response.json()["active_translation_run_id"] is None
    assert client.get(url + "/translation-pages").json()["mode"] == "manual"
    with core.db() as connection:
        assert connection.execute("SELECT status FROM translation_runs WHERE id=?", (run_id,)).fetchone()["status"] == "superseded"
        assert [r["status"] for r in connection.execute("SELECT status FROM translation_pages WHERE run_id=? ORDER BY page_no", (run_id,))] == ["ready", "cancelled"]
    assert client.get(url + "/annotations?project_id=inbox").json()[0]["is_current"] is False
    assert client.get(old_page["svg_url"]).status_code == 200
    assert annotate(client, paper, side="translation", source_sha=sha, translation_run_id=run_id).status_code == 409
    other = upload(client, make_pdf("Unrelated PDF"))
    assert client.get(f"/api/papers/{other['id']}/pages/1.svg?side=translation&v={sha}").status_code == 409


def test_additive_migration_keeps_v01_papers_manual_translation_notes_and_reading(client):
    paper = upload(client)
    manual = core.attach_translation(paper["id"], make_pdf("Existing manual translation"))
    original_note = annotate(client, paper).json()
    manual_note = annotate(client, paper, side="translation", source_sha=manual["translated_sha256"]).json()
    client.patch(f"/api/papers/{paper['id']}/reading", json={"project_id": "inbox", "status": "read", "last_page": 2})
    # Recreate the v0.1 shape while retaining actual records and original bytes.
    with core.db() as connection:
        connection.execute("ALTER TABLE annotations DROP COLUMN translation_run_id")
        connection.execute("ALTER TABLE project_papers DROP COLUMN page_offset")
        connection.execute("ALTER TABLE papers DROP COLUMN active_translation_run_id")
        connection.execute("DROP TABLE translation_pages")
        connection.execute("DROP TABLE translation_runs")
    core.init_db()
    core.init_db()  # The migration is idempotent.
    restored = core.get_paper(paper["id"])
    assert restored["sha256"] == paper["sha256"]
    assert restored["translated_sha256"] == manual["translated_sha256"]
    assert restored["active_translation_run_id"] is None
    notes = core.annotation_list(paper["id"], "inbox")
    assert {n["id"] for n in notes} == {original_note["id"], manual_note["id"]}
    assert all(n["is_current"] for n in notes)
    membership = core.require_membership(paper["id"], "inbox")
    assert (membership["status"], membership["last_page"], membership["page_offset"]) == ("read", 2, 0)
    assert client.get(f"/api/papers/{paper['id']}/pages/2?side=translation").status_code == 200

