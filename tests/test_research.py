import json

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import ai, auth, core, evidence, jobs, reading, research
from app.research_routes import router


def pdf_bytes(*pages):
    with fitz.open() as doc:
        for text in pages:
            page = doc.new_page(width=600, height=800)
            page.insert_textbox((60, 70, 540, 700), text, fontsize=12)
        return doc.tobytes()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_BASE_URL", "http://127.0.0.1:9001/v1")
    monkeypatch.setenv("AI_MODEL", "test-research")
    monkeypatch.setenv("AI_MAX_INPUT_CHARS", "50000")
    monkeypatch.setenv("BABELDOC_BIN", "missing-test-babeldoc")
    core.init_db()
    jobs.init_jobs()
    research.init_research()
    research._STOP.clear()
    with core.db() as con:
        con.execute("INSERT INTO projects(id,name,created_at,owner_id) VALUES('study','Study',?,'local')", (core.now(),))
    paper = core.add_paper(pdf_bytes("The method reduces latency by 20 percent on Dataset A.",
                                     "The evaluation includes 120 samples. Limitations include a small dataset."), "study", "Paper A")
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, paper
    research._STOP.clear()


def model_answer(system, source, cfg):
    if system == research.VERIFY_SYSTEM:
        values = json.loads(source)["statements"]
        return json.dumps({"supported": [v["index"] for v in values]})
    material = json.loads(source)["materials"]
    selected = next(m for m in material if "20 percent" in m["text"])
    return json.dumps({"claims": [{"text": "在 Dataset A 上延迟降低 20%。", "citations": [{"source_id": selected["id"], "quote": "reduces latency by 20 percent on Dataset A."}]}]})


def test_qa_has_server_locations_and_exact_original(env, monkeypatch):
    client, paper = env
    monkeypatch.setattr(ai, "complete", model_answer)
    job = research.enqueue("study", "qa", paper["id"], "延迟降低多少？")
    assert job["status"] == "queued"
    assert research.run_once()
    answer = research.get_job(job["id"])
    assert answer["status"] == "succeeded", answer
    citation = answer["result"]["claims"][0]["citations"][0]
    assert citation["quote"] == "reduces latency by 20 percent on Dataset A."
    assert citation["source_sha"] == paper["sha256"]
    assert citation["page"] == 1 and citation["rects"] and citation["block_id"]
    assert citation["href"].startswith("/read/" + paper["id"])
    assert answer["coverage"]["scanned_pages"] == 2
    assert answer["visibility"] == "private"


def test_fabricated_quote_and_unknown_source_never_published(env, monkeypatch):
    _, paper = env
    def mock(system, source, cfg):
        if system == research.VERIFY_SYSTEM:
            pytest.fail("No invalid claim reaches semantic verification")
        first = json.loads(source)["materials"][0]
        return json.dumps({"claims": [
            {"text": "Invented result", "citations": [{"source_id": first["id"], "quote": "Accuracy improved by 99 percent"}]},
            {"text": "Wrong source", "citations": [{"source_id": "p-attacker", "quote": first["text"]}]}]})
    monkeypatch.setattr(ai, "complete", mock)
    job = research.enqueue("study", "qa", paper["id"], "What result?")
    research.run_once()
    result = research.get_job(job["id"])
    assert result["status"] == "succeeded"
    assert result["result"]["claims"] == []
    assert result["coverage"]["rejected_citations_or_items"] == 2


def test_exact_but_irrelevant_quote_fails_support_check(env, monkeypatch):
    _, paper = env
    def mock(system, source, cfg):
        if system == research.VERIFY_SYSTEM:
            return '{"supported": []}'
        first = json.loads(source)["materials"][0]
        return json.dumps({"claims": [{"text": "This proves a 99% accuracy gain.", "citations": [{"source_id": first["id"], "quote": first["text"]}]}]})
    monkeypatch.setattr(ai, "complete", mock)
    job = research.enqueue("study", "qa", paper["id"], "Accuracy gain?")
    research.run_once()
    result = research.get_job(job["id"])
    assert result["result"]["claims"] == []
    assert result["coverage"]["unsupported_statements_removed"] == 1


def note(paper, *, text="Personal hypothesis", owner="local", visibility="private"):
    identifier = core.uid()
    with core.db() as con:
        con.execute("""INSERT INTO annotations(id,paper_id,project_id,side,page,quote,note,color,rects,source_sha,created_at,updated_at,owner_id,visibility)
                       VALUES(?,?,?,'original',1,'The method',?,'yellow','[]',?,?,?,?,?)""",
                    (identifier, paper["id"], "study", text, paper["sha256"], core.now(), core.now(), owner, visibility))
    return identifier


def add_member(*, role="viewer"):
    with core.db() as con:
        con.execute("INSERT INTO users(id,username,display_name,password_hash,role,inbox_id,created_at) VALUES('peer','peer','Peer','unused','user','peer-inbox',?)", (core.now(),))
        con.execute("INSERT INTO projects(id,name,created_at,owner_id) VALUES('peer-inbox','Inbox',?,'peer')", (core.now(),))
        con.execute("INSERT INTO groups VALUES('group','Group','local',?)", (core.now(),))
        con.execute("INSERT INTO group_members VALUES('group','peer',?,?)", (role, core.now()))
        con.execute("INSERT INTO project_shares VALUES('study','group','local',?)", (core.now(),))


def test_project_scans_all_papers_and_only_accessible_notes(env):
    _, paper = env
    second = core.add_paper(pdf_bytes("Second paper discusses the benchmark."), "study", "Paper B")
    unrelated = core.add_paper(pdf_bytes("Unrelated secret paper."), "inbox", "Secret")
    add_member()
    own = note(paper, text="Owner private")
    other = note(paper, text="Peer private", owner="peer")
    public = note(paper, text="Peer shared", owner="peer", visibility="shared")
    sources, coverage = evidence.collect("study", None, None, "qa")
    assert coverage["total_papers"] == 2 and coverage["scanned_pages"] == 3
    assert {s["paper_id"] for s in sources} == {paper["id"], second["id"]}
    assert {s.get("annotation_id") for s in sources if s["type"] == "note"} == {own, public}
    with auth.as_user("peer"):
        peer_sources, _ = evidence.collect("study", None, None, "qa")
    assert {s.get("annotation_id") for s in peer_sources if s["type"] == "note"} == {other, public}
    assert unrelated["id"] not in {s["paper_id"] for s in sources}


def test_project_qa_returns_more_than_first_paper(env, monkeypatch):
    _, first = env
    second = core.add_paper(pdf_bytes("The second method improves robustness on Benchmark B."), "study", "Paper B")
    def mock(system, source, cfg):
        if system == research.VERIFY_SYSTEM:
            return json.dumps({"supported": [s["index"] for s in json.loads(source)["statements"]]})
        materials = json.loads(source)["materials"]
        titles = {m["paper_title"] for m in materials}
        assert {"Paper A", "Paper B"} <= titles
        item = next(m for m in materials if m["paper_title"] == "Paper B")
        return json.dumps({"claims": [{"text": "第二种方法评测了鲁棒性。", "citations": [{"source_id": item["id"], "quote": item["text"]}]}]})
    monkeypatch.setattr(ai, "complete", mock)
    job = research.enqueue("study", "qa", question="Compare the two methods")
    research.run_once()
    result = research.get_job(job["id"])
    assert result["status"] == "succeeded", result
    assert result["result"]["claims"][0]["citations"][0]["paper_id"] == second["id"]
    assert result["coverage"]["scanned_papers"] == 2


def test_private_evidence_blocks_sharing_and_revocation_hides_answers(env, monkeypatch):
    _, paper = env
    add_member()
    annotation = note(paper)
    monkeypatch.setattr(ai, "complete", model_answer)
    job = research.enqueue("study", "qa", paper["id"], "延迟？")
    research.run_once()
    with pytest.raises(HTTPException) as err:
        research.set_visibility(job["id"], "shared")
    assert err.value.status_code == 409
    with auth.as_user("peer"), pytest.raises(HTTPException):
        research.get_job(job["id"])
    with core.db() as con:
        con.execute("UPDATE annotations SET visibility='shared' WHERE id=?", (annotation,))
    research.set_visibility(job["id"], "shared")
    with auth.as_user("peer"):
        assert research.get_job(job["id"])["visibility"] == "shared"
    with core.db() as con:
        con.execute("UPDATE annotations SET visibility='private' WHERE id=?", (annotation,))
    with auth.as_user("peer"), pytest.raises(HTTPException):
        research.get_job(job["id"])


def test_changed_private_note_cannot_be_shared_by_later_public_replacement(env, monkeypatch):
    _, paper = env
    annotation = note(paper, text="Private old information")
    monkeypatch.setattr(ai, "complete", model_answer)
    job = research.enqueue("study", "qa", paper["id"], "What method?")
    research.run_once()
    with core.db() as con:
        con.execute("UPDATE annotations SET visibility='shared',note='Public replacement' WHERE id=?", (annotation,))
    with pytest.raises(HTTPException) as err:
        research.set_visibility(job["id"], "shared")
    assert err.value.status_code == 409


def test_model_cancellation_never_publishes_result(env, monkeypatch):
    _, paper = env
    job = research.enqueue("study", "qa", paper["id"], "Latency?")
    def mock(system, source, cfg):
        research.cancel(job["id"])
        return model_answer(system, source, cfg)
    monkeypatch.setattr(ai, "complete", mock)
    research.run_once()
    result = research.get_job(job["id"])
    assert result["status"] == "cancelled" and result["result"] is None


def test_read_endpoints_are_model_free_and_lineage_is_explicit(env, monkeypatch):
    client, paper = env
    monkeypatch.setattr(ai, "complete", lambda *args: pytest.fail("GET must not trigger a model"))
    assert client.get("/api/research/jobs", params={"project_id": "study"}).json() == []
    assert client.get("/api/research/knowledge", params={"project_id": "study"}).json()["items"] == []
    response = client.post("/api/research/jobs", json={"project_id": "study", "paper_id": paper["id"], "kind": "lineage"})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_selection_rejects_forgery_and_stale_hash(env):
    _, paper = env
    block = reading._extract(paper, 1)[0]
    with pytest.raises(HTTPException):
        evidence.selected_sources(paper, "study", {"source_sha": "0" * 64, "page": 1, "quote": block["text"]}, "summary")
    with pytest.raises(HTTPException):
        evidence.selected_sources(paper, "study", {"source_sha": paper["sha256"], "page": 1, "quote": "Invented words"}, "explain")
    actual = evidence.selected_sources(paper, "study", {"source_sha": paper["sha256"], "page": 1, "block_id": block["id"], "quote": "reduces latency by 20 percent"}, "summary")
    assert actual[0]["text"] == "reduces latency by 20 percent"


def test_visual_region_is_rendered_from_source_and_cited(env, monkeypatch):
    client, paper = env
    calls = []
    def mock(system, source, images, cfg):
        assert images[0].startswith("data:image/png;base64,")
        calls.append(system)
        if system == research.VERIFY_SYSTEM:
            return '{"supported":[0]}'
        material = next(m for m in json.loads(source)["materials"] if m["type"] == "figure")
        return json.dumps({"claims": [{"text": "区域内显示论文中的文字与条件。", "citations": [{"source_id": material["id"], "quote": ""}]}]})
    monkeypatch.setattr(ai, "complete_vision", mock)
    job = research.enqueue("study", "figure", paper["id"], selection={"source_sha": paper["sha256"], "page": 1, "rects": [{"x": .05, "y": .05, "width": .9, "height": .3}]})
    research.run_once()
    result = research.get_job(job["id"])
    assert result["status"] == "succeeded", result
    citation = result["result"]["claims"][0]["citations"][0]
    response = client.get(citation["image_url"])
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
    assert citation["source_sha"] == paper["sha256"] and citation["type"] == "figure"
    assert len(calls) == 2


def test_retrieval_reports_context_omissions_without_claiming_full_model_read(env):
    _, paper = env
    core.add_paper(pdf_bytes("Another paper about benchmarks."), "study", "Paper B")
    sources, coverage = evidence.collect("study", None, None, "qa")
    selected, retrieval = evidence.retrieve(sources, "benchmarks", 1000, "qa")
    assert coverage["scanned_papers"] == 2
    assert retrieval["selected_chunks"] == len(selected)
    assert retrieval["all_chunks_fit"] is False
    assert len(retrieval["omitted_paper_ids"]) >= 1


def test_knowledge_filter_and_reference_integrity(env, monkeypatch):
    _, paper = env
    source = evidence.selected_sources(paper, "study", {"source_sha": paper["sha256"], "page": 1, "block_id": reading._extract(paper, 1)[0]["id"]}, "summary")[0]
    output = {"lineage": [{"reference": "A completely fabricated prior paper", "label": "fake", "relationship": "improves it", "citations": [{"source_id": source["id"], "quote": source["text"]}]}]}
    checked, rejected = research._validate_output(output, [source], "test", "lineage")
    assert checked["lineage"] == [] and rejected == 1
    def mock(system, content, cfg):
        if system == research.VERIFY_SYSTEM:
            return '{"supported":[0]}'
        item = next(m for m in json.loads(content)["materials"] if "Dataset A" in m["text"])
        return json.dumps({"knowledge": [{"category": "dataset", "label": "Dataset A", "description": "用于延迟评测。", "related_labels": ["latency"], "citations": [{"source_id": item["id"], "quote": item["text"]}]}]})
    monkeypatch.setattr(ai, "complete", mock)
    task = research.enqueue("study", "knowledge", paper["id"])
    research.run_once()
    assert research.get_job(task["id"])["status"] == "succeeded"
    assert research.knowledge("study", category="dataset", label="latency")["total"] == 1
    assert research.knowledge("study", category="method")["total"] == 0


def test_session_identity_survives_background_worker_and_shared_result_permissions(tmp_path, monkeypatch):
    from app import catalog, imports
    from app.main import app
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setenv("PAPERSTUDIO_SEED_DEMO", "0")
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_BASE_URL", "http://127.0.0.1:9001/v1")
    monkeypatch.setenv("AI_MODEL", "test-research")
    monkeypatch.setenv("BABELDOC_BIN", "missing-test-babeldoc")
    monkeypatch.delenv("PAPERSTUDIO_SETUP_TOKEN", raising=False)
    original_catalog = catalog.init_catalog
    monkeypatch.setattr(catalog, "init_catalog", lambda: original_catalog(seed=False))
    for module in (jobs, imports, research):
        monkeypatch.setattr(module, "start_worker", lambda: None)
    research._STOP.clear()
    password = "a-long-test-only-password"
    with TestClient(app) as owner:
        assert owner.get("/api/research/jobs", params={"project_id": "inbox"}).status_code == 401
        setup = owner.post("/api/auth/setup", json={"setup_token": (tmp_path / "bootstrap-token").read_text().strip(), "username": "owner", "password": password})
        assert setup.status_code == 201, setup.text
        owner.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        owner_id = setup.json()["user"]["id"]
        project = owner.post("/api/projects", json={"name": "Evidence study"}).json()["id"]
        upload = owner.post("/api/papers", files={"file": ("evidence.pdf", pdf_bytes("The method reduces latency by 20 percent on Dataset A."), "application/pdf")}, data={"project_id": project})
        assert upload.status_code == 201, upload.text
        paper = upload.json()
        group = owner.post("/api/groups", json={"name": "Research team"}).json()["id"]
        invite = owner.post(f"/api/groups/{group}/invitations", json={"role": "viewer"}).json()["token"]
        with TestClient(app, raise_server_exceptions=True) as viewer:
            registration = viewer.post("/api/auth/register", json={"invite_token": invite, "username": "viewer", "password": password})
            assert registration.status_code == 201, registration.text
            viewer.headers["X-CSRF-Token"] = registration.json()["csrf_token"]
            assert viewer.post("/api/research/jobs", json={"project_id": project, "kind": "qa", "question": "Latency?"}).status_code == 404
            owner.post(f"/api/projects/{project}/shares", json={"group_id": group})
            def mock(system, source, cfg):
                assert auth.current_user_id() == owner_id
                return model_answer(system, source, cfg)
            monkeypatch.setattr(ai, "complete", mock)
            created = owner.post("/api/research/jobs", json={"project_id": project, "paper_id": paper["id"], "kind": "qa", "question": "Latency?"})
            assert created.status_code == 202, created.text
            identifier = created.json()["id"]
            research.run_once()
            result = owner.get(f"/api/research/jobs/{identifier}")
            assert result.json()["status"] == "succeeded", result.text
            assert viewer.get(f"/api/research/jobs/{identifier}").status_code == 404
            assert owner.patch(f"/api/research/jobs/{identifier}/visibility", json={"visibility": "shared"}).status_code == 200
            shared = viewer.get(f"/api/research/jobs/{identifier}")
            assert shared.status_code == 200 and not shared.json()["can_edit"]
            assert viewer.post(f"/api/research/jobs/{identifier}/cancel").status_code == 403
            assert viewer.patch(f"/api/research/jobs/{identifier}/visibility", json={"visibility": "private"}).status_code == 403


def make_lineage(env, monkeypatch, *, reference="[1] Jane. Prior Theory of Latency. arXiv:2501.12345."):
    _, _paper = env
    parent = core.add_paper(pdf_bytes("References\n" + reference), "study", "Parent with references")
    def mock(system, source, cfg):
        if system == research.VERIFY_SYSTEM:
            return json.dumps({"supported": [s["index"] for s in json.loads(source)["statements"]]})
        material = next(m for m in json.loads(source)["materials"] if reference in m["text"])
        return json.dumps({"lineage": [{"reference": reference, "label": "Prior Theory of Latency",
                                        "relationship": "本文将该工作列入参考文献，尚未读取它的全文。",
                                        "citations": [{"source_id": material["id"], "quote": reference}]}]})
    monkeypatch.setattr(ai, "complete", mock)
    task = research.enqueue("study", "lineage", parent["id"])
    research.run_once()
    result = research.get_job(task["id"])
    assert result["status"] == "succeeded" and len(result["result"]["lineage"]) == 1
    return task


def reference_answer(system, source, cfg):
    if system == research.VERIFY_SYSTEM:
        return json.dumps({"supported": [s["index"] for s in json.loads(source)["statements"]]})
    material = next(m for m in json.loads(source)["materials"] if "caching removes repeated work" in m["text"])
    return json.dumps({"claims": [{"text": "该前序方法使用缓存减少重复计算。", "citations": [{"source_id": material["id"], "quote": "caching removes repeated work"}]}]})


def test_reference_download_is_explicit_private_cited_and_not_auto_added(env, monkeypatch):
    from app import reference_material, safe_download
    client, _ = env
    lineage = make_lineage(env, monkeypatch)
    add_member()
    downloads = []
    prior_bytes = pdf_bytes("Prior Theory of Latency\nThe proposed caching removes repeated work under fixed inputs.")
    def download(url, directory, **kwargs):
        downloads.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ".test-download.pdf"
        path.write_bytes(prior_bytes)
        return {"path": path, "resolved_url": url, "size_bytes": len(prior_bytes)}
    monkeypatch.setattr(safe_download, "download_pdf", download)
    monkeypatch.setattr(ai, "complete", reference_answer)
    with core.db() as con:
        before = con.execute("SELECT count(*) FROM project_papers WHERE project_id='study'").fetchone()[0]
    created = client.post(f"/api/research/jobs/{lineage['id']}/references/0/expand", json={})
    assert created.status_code == 202, created.text
    assert downloads == []
    research.run_once()
    result = research.get_job(created.json()["id"])
    assert result["status"] == "succeeded", result
    assert downloads == ["https://arxiv.org/pdf/2501.12345"]
    material = result["result"]["reference_material"]
    asset_id = material["asset_id"]
    assert material["added_to_project"] is False
    from app import parsing
    assert parsing.load_cached(material["source_sha"], "reference:local:study") is not None
    assert parsing.load_cached(material["source_sha"]) is None
    citation = result["result"]["claims"][0]["citations"][0]
    assert citation["type"] == "reference" and citation["paper_id"] is None
    assert citation["quote"] == "caching removes repeated work"
    assert citation["reference_asset_id"] == asset_id and citation["page"] == 1
    asset_url = f"/api/research/reference-assets/{asset_id}/file"
    assert client.get(asset_url).content == prior_bytes
    with core.db() as con:
        assert con.execute("SELECT count(*) FROM project_papers WHERE project_id='study'").fetchone()[0] == before
    with auth.as_user("peer"), pytest.raises(HTTPException) as err:
        reference_material.require_asset(asset_id)
    assert err.value.status_code == 404
    research.set_visibility(created.json()["id"], "shared")
    with auth.as_user("peer"):
        assert reference_material.require_asset(asset_id)["id"] == asset_id
    research.set_visibility(created.json()["id"], "private")
    with auth.as_user("peer"), pytest.raises(HTTPException):
        reference_material.require_asset(asset_id)
    research.set_visibility(created.json()["id"], "shared")
    with core.db() as con:
        con.execute("DELETE FROM group_members WHERE user_id='peer'")
    with auth.as_user("peer"), pytest.raises(HTTPException):
        reference_material.require_asset(asset_id)
    imported = client.post(f"/api/research/reference-assets/{asset_id}/import", json={"project_id": "study"})
    assert imported.status_code == 201
    with core.db() as con:
        assert con.execute("SELECT count(*) FROM project_papers WHERE project_id='study'").fetchone()[0] == before + 1


def test_reference_existing_project_pdf_is_reused_without_download(env, monkeypatch):
    from app import safe_download
    lineage = make_lineage(env, monkeypatch)
    prior = core.add_paper(pdf_bytes("The proposed caching removes repeated work."), "study", "Prior Theory of Latency")
    monkeypatch.setattr(safe_download, "download_pdf", lambda *a, **k: pytest.fail("Existing PDF must be reused"))
    monkeypatch.setattr(ai, "complete", reference_answer)
    task = research.expand_reference(lineage["id"], 0)
    research.run_once()
    result = research.get_job(task["id"])
    assert result["status"] == "succeeded", result
    material = result["result"]["reference_material"]
    assert material["asset_id"] is None and material["paper_id"] == prior["id"]
    assert result["result"]["claims"][0]["citations"][0]["paper_id"] == prior["id"]


def test_reference_unresolved_doi_and_ambiguous_ids_do_not_download(env, monkeypatch):
    from app import safe_download, reference_material
    lineage = make_lineage(env, monkeypatch, reference="[1] Jane. Prior Theory of Latency. doi:10.1234/example.")
    monkeypatch.setattr(safe_download, "download_pdf", lambda *a, **k: pytest.fail("No precise PDF identity"))
    with pytest.raises(HTTPException) as err:
        research.expand_reference(lineage["id"], 0)
    assert err.value.status_code == 422
    with pytest.raises(HTTPException) as err:
        reference_material._link({"reference": "arXiv:2501.12345 and arXiv:2502.12345"})
    assert err.value.status_code == 422
