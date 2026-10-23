import json
import socket
import threading
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import core, imports, jobs, safe_download
from app.import_routes import router
from app.source_resolver import resolve_source


def make_pdf(text="A real PDF records a problem, method and evidence that can be read and independently checked."):
    with fitz.open() as doc:
        for index in range(2):
            page = doc.new_page()
            if text:
                page.insert_text((60, 80), f"Page {index + 1}: {text}")
        return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    imports._STOP.clear()
    jobs._STOP.clear()
    core.init_db()
    jobs.init_jobs()
    imports.init_imports()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as session:
        yield session
    imports._STOP.clear()
    jobs._STOP.clear()


@pytest.fixture
def fake_network(monkeypatch):
    calls = []
    monkeypatch.setattr(safe_download.socket, "getaddrinfo", lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))])
    def download(url, directory, **kwargs):
        calls.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "test-download.pdf"
        data = make_pdf()
        path.write_bytes(data)
        return {"path": path, "resolved_url": url, "size_bytes": len(data)}
    monkeypatch.setattr(safe_download, "download_pdf", download)
    return calls


def drain(limit=30):
    for _ in range(limit):
        if not imports.run_once():
            return
    raise AssertionError("Import queue did not settle")


def test_arxiv_normalization_preserves_version_and_old_ids():
    for url in ["https://arxiv.org/abs/2501.12948v2", "http://www.arxiv.org/pdf/2501.12948v2.pdf?download=1", "arxiv:2501.12948v2"]:
        resolved = resolve_source(url)
        assert resolved["download_url"] == "https://arxiv.org/pdf/2501.12948v2"
        assert resolved["version"] == 2
    assert resolve_source("https://arxiv.org/abs/hep-th/9901001v3")["arxiv_id"] == "hep-th/9901001"
    assert resolve_source("https://arxiv.org/abs/2501.12948")["version_policy"] == "download-sha-snapshot"
    for value in ["https://doi.org/10.1234/paper", "10.1234/paper", "file:///etc/passwd", "https://user:pass@example.org/file.pdf", "https://arxiv.org/list/cs.AI/recent"]:
        with pytest.raises(HTTPException):
            resolve_source(value)


def test_real_pdf_available_before_optional_parse_and_missing_ai(client, fake_network):
    response = client.post("/api/imports", json={"url": "https://arxiv.org/abs/2501.12948v2", "project_id": "inbox", "processing": {"parse": True, "brief_modes": ["abstract", "summary"]}})
    assert response.status_code == 202, response.text
    plan = response.json()
    assert plan["paper_id"] is None
    duplicate = client.post("/api/imports", json={"url": "https://arxiv.org/pdf/2501.12948v2", "processing": plan["processing"]}).json()
    assert duplicate["id"] == plan["id"]
    assert imports.run_once()
    available = imports.get_import(plan["id"])
    assert available["paper_id"] and available["steps"][0]["status"] == "succeeded"
    assert core.paper_path(available["paper_id"]).read_bytes().startswith(b"%PDF")
    assert available["steps"][1]["status"] == "queued"
    drain()
    completed = client.get("/api/imports/" + plan["id"]).json()
    assert completed["status"] == "blocked"
    assert [step["status"] for step in completed["steps"]] == ["succeeded", "succeeded", "blocked", "blocked"]
    parsed = client.get(f"/api/papers/{available['paper_id']}/parsed").json()
    assert len(parsed["pages"]) == 2 and "[PDF 第 2 页]" in parsed["fulltext"]
    assert parsed["coverage"]["visual_evidence_verified"] is False
    assert len(fake_network) == 1 and not list((core.DATA_DIR / "downloads").glob("*.pdf"))


def test_upload_options_auto_parse_and_no_notes_summary(client):
    response = client.post("/api/imports/file", files={"file": ("paper.pdf", make_pdf(), "application/pdf")}, data={"processing": json.dumps({"parse": False, "brief_modes": ["summary"]})})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["paper_id"] and result["processing"]["parse"] is True
    assert result["title"] == core.get_paper(result["paper_id"])["title"]
    assert [(step["kind"], step["mode"]) for step in result["steps"]] == [("download", ""), ("parse", ""), ("brief", "summary")]
    drain()
    assert client.get("/api/imports").json()[0]["paper_id"] == result["paper_id"]
    for value in ["[]", '{"brief_modes": [{}]}', '{"notes-summary": true}']:
        bad = client.post("/api/imports/file", files={"file": ("paper.pdf", make_pdf(), "application/pdf")}, data={"processing": value})
        assert bad.status_code == 422


def test_scanned_pdf_kept_and_text_dependents_blocked(client):
    result = client.post("/api/imports/file", files={"file": ("scan.pdf", make_pdf(""), "application/pdf")}, data={"processing": '{"guide": true}'}).json()
    drain()
    result = imports.get_import(result["id"])
    assert result["paper_id"] and result["status"] == "blocked"
    assert "OCR" in result["steps"][1]["error"]
    assert result["steps"][2]["status"] == "blocked"


def test_cancel_during_download_rejects_late_file(client, fake_network, monkeypatch):
    plan = client.post("/api/imports", json={"url": "https://example.org/paper.pdf"}).json()
    def late_download(url, directory, **kw):
        imports.cancel(plan["id"])
        path = core.DATA_DIR / "late.pdf"
        path.write_bytes(make_pdf())
        return {"path": path, "resolved_url": url, "size_bytes": path.stat().st_size}
    monkeypatch.setattr(safe_download, "download_pdf", late_download)
    assert imports.run_once()
    assert imports.get_import(plan["id"])["status"] == "cancelled"
    with core.db() as con:
        assert con.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    assert not (core.DATA_DIR / "late.pdf").exists()


def test_cancel_then_immediate_retry_cannot_publish_old_download(client, fake_network, monkeypatch):
    plan = client.post("/api/imports", json={"url": "https://example.org/paper.pdf"}).json()
    started, release = threading.Event(), threading.Event()
    original = safe_download.download_pdf
    def delayed(url, directory, **kwargs):
        started.set()
        assert release.wait(5)
        # Deliberately ignore cancellation like a late third-party response.
        kwargs["progress"](123, 123)
        return original(url, directory, **kwargs)
    monkeypatch.setattr(safe_download, "download_pdf", delayed)
    thread = threading.Thread(target=imports.run_once)
    thread.start()
    assert started.wait(5)
    imports.cancel(plan["id"])
    imports.retry(plan["id"])
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    current = imports.get_import(plan["id"])
    assert current["status"] == "queued" and current["paper_id"] is None
    assert current["steps"][0]["status"] == "queued"
    assert "downloaded_bytes" not in current["steps"][0]["progress"]
    with core.db() as con:
        assert con.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0
    monkeypatch.setattr(safe_download, "download_pdf", original)
    drain()
    assert imports.get_import(plan["id"])["status"] == "succeeded"


def test_cancel_retry_cannot_publish_old_parse_cache(client, monkeypatch):
    plan = client.post("/api/imports/file", files={"file": ("paper.pdf", make_pdf(), "application/pdf")}).json()
    started, release = threading.Event(), threading.Event()
    original = imports.pdf.extract_pages
    def delayed(path):
        started.set()
        assert release.wait(5)
        return original(path)
    monkeypatch.setattr(imports.pdf, "extract_pages", delayed)
    thread = threading.Thread(target=imports.run_once)
    thread.start()
    assert started.wait(5)
    imports.cancel(plan["id"])
    imports.retry(plan["id"])
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert imports.get_parse_artifact(plan["paper_id"]) is None
    assert imports.get_import(plan["id"])["steps"][1]["status"] == "queued"
    monkeypatch.setattr(imports.pdf, "extract_pages", original)
    drain()
    assert imports.get_parse_artifact(plan["paper_id"])["source_sha"]


def test_retry_preserves_download_and_parse_then_follows_model_job(client, fake_network, monkeypatch):
    plan = client.post("/api/imports", json={"url": "https://example.org/paper.pdf", "processing": {"brief_modes": ["summary"]}}).json()
    drain()
    assert imports.get_import(plan["id"])["status"] == "blocked"
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    assert client.post(f"/api/imports/{plan['id']}/retry").status_code == 200
    drain()
    result = imports.get_import(plan["id"])
    job_id = result["steps"][-1]["job_id"]
    assert job_id and len(fake_network) == 1
    with core.db() as con:
        con.execute("UPDATE jobs SET status='failed',error='test transient failure' WHERE id=?", (job_id,))
    assert imports.get_import(plan["id"])["status"] == "partial"
    imports.retry(plan["id"])
    drain()
    assert jobs.get_job(job_id)["status"] == "queued"
    with core.db() as con:
        con.execute("UPDATE jobs SET status='succeeded',error=NULL,result_markdown='A checked summary' WHERE id=?", (job_id,))
    assert imports.get_import(plan["id"])["status"] == "succeeded"
    assert len(fake_network) == 1


def test_cancelling_import_does_not_cancel_another_plan_shared_job(client, monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    paper = core.add_paper(make_pdf(), "inbox")
    first = imports.create_import(paper_id=paper["id"], project_id="inbox", processing={"guide": True})
    second = imports.create_import(paper_id=paper["id"], project_id="inbox", processing={"guide": True, "brief_modes": ["abstract"]})
    drain()
    first_job = next(s["job_id"] for s in imports.get_import(first["id"])["steps"] if s["kind"] == "guide")
    second_job = next(s["job_id"] for s in imports.get_import(second["id"])["steps"] if s["kind"] == "guide")
    assert first_job == second_job
    imports.cancel(first["id"])
    assert jobs.get_job(second_job)["status"] == "queued"
    imports.cancel(second["id"])
    assert jobs.get_job(second_job)["status"] == "cancelled"


def test_cancel_import_keeps_preexisting_reader_job(client, monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("AI_MODEL", "test-model")
    paper = core.add_paper(make_pdf(), "inbox")
    job = jobs.enqueue(paper["id"], "inbox", "guide")
    plan = imports.create_import(paper_id=paper["id"], project_id="inbox", processing={"guide": True})
    drain()
    assert imports.get_import(plan["id"])["steps"][-1]["job_id"] == job["id"]
    imports.cancel(plan["id"])
    assert jobs.get_job(job["id"])["status"] == "queued"


def test_restart_marks_download_interrupted_without_retry(client, fake_network, monkeypatch):
    plan = client.post("/api/imports", json={"url": "https://example.org/paper.pdf"}).json()
    with core.db() as con:
        con.execute("UPDATE imports SET status='running' WHERE id=?", (plan["id"],))
        con.execute("UPDATE import_steps SET status='running' WHERE id=?", (plan["steps"][0]["id"],))
    # Avoid a thread in a deterministic restart recovery test.
    class NoThread:
        def __init__(self, **kw): pass
        def start(self): pass
        def is_alive(self): return False
    monkeypatch.setattr(imports.threading, "Thread", NoThread)
    monkeypatch.setattr(imports, "_THREAD", None)
    imports.start_worker()
    result = imports.get_import(plan["id"])
    assert result["status"] == "interrupted"
    assert result["steps"][0]["status"] == "interrupted"
    assert fake_network == []


def test_non_pdf_download_fails_without_paper(client, fake_network, monkeypatch):
    plan = client.post("/api/imports", json={"url": "https://example.org/landing"}).json()
    def html_download(url, directory, **kw):
        path = core.DATA_DIR / "wrong.pdf"
        path.write_bytes(b"<html>Access denied</html>")
        return {"path": path, "resolved_url": url, "size_bytes": path.stat().st_size}
    monkeypatch.setattr(safe_download, "download_pdf", html_download)
    drain()
    result = imports.get_import(plan["id"])
    assert result["status"] == "failed" and result["paper_id"] is None


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "192.0.2.1", "224.0.0.1"])
def test_private_or_special_dns_rejected(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))])
    with pytest.raises(safe_download.DownloadError):
        safe_download.validated_target("https://example.org/paper.pdf")


def test_downloader_revalidates_redirect_and_does_not_open_private_ip(tmp_path, monkeypatch):
    def dns(host, port, **kw):
        ip = "93.184.216.34" if host == "example.org" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    opened = []
    class Response:
        status = 302
        def getheader(self, key, default=None): return "http://secret.example/metadata" if key == "Location" else default
    class Connection:
        def __init__(self, host, port, address, **kw): opened.append(host)
        def request(self, *args, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(safe_download, "_PinnedConnection", Connection)
    with pytest.raises(safe_download.DownloadError):
        safe_download.download_pdf("https://example.org/file.pdf", tmp_path)
    assert opened == ["example.org"]


def test_socket_is_ip_pinned_and_tls_validates_original_host(monkeypatch):
    seen = {}
    class Sock:
        def settimeout(self, timeout): pass
        def connect(self, address): seen["connected"] = address
        def close(self): pass
    class Context:
        def wrap_socket(self, sock, server_hostname):
            seen["tls_hostname"] = server_hostname
            return sock
    monkeypatch.setattr(socket, "socket", lambda *args: Sock())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: pytest.fail("Pinned connect must not resolve the hostname again"))
    monkeypatch.setattr(safe_download.ssl, "create_default_context", lambda: Context())
    connection = safe_download._PinnedConnection("example.org", 443, (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)), tls=True, timeout=3)
    connection.connect()
    assert seen == {"connected": ("93.184.216.34", 443), "tls_hostname": "example.org"}


def test_stream_size_limit_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))])
    class Response:
        status = 200
        def getheader(self, key, default=None): return default
        def read(self, size): return b"x" * size
    class Connection:
        sock = None
        def __init__(self, *args, **kw): pass
        def request(self, *args, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(safe_download, "_PinnedConnection", Connection)
    with pytest.raises(safe_download.DownloadError):
        safe_download.download_pdf("https://example.org/file.pdf", tmp_path, max_bytes=10)
    assert list(tmp_path.iterdir()) == []
