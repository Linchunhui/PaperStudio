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
