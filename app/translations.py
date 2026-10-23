"""Immutable page artifacts for progressively visible PDF translations.

The merged PDF is an export. Annotation identity belongs to a run and page
artifact, and never changes when a later page becomes available.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
from fastapi import HTTPException

from . import core, pdf


class Stopped(RuntimeError):
    """The owning job was cancelled/interrupted before a page could commit."""


def init_db():
    # Deliberately no FK to jobs: core initializes before the job queue does.
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS translation_runs (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
            job_id TEXT NOT NULL UNIQUE, source_sha TEXT NOT NULL,
            status TEXT NOT NULL, merged_filename TEXT, merged_sha TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS translation_run_paper ON translation_runs(paper_id);
        CREATE TABLE IF NOT EXISTS translation_pages (
            run_id TEXT NOT NULL REFERENCES translation_runs(id), page_no INTEGER NOT NULL,
            status TEXT NOT NULL, asset_sha TEXT, filename TEXT, error TEXT,
            updated_at TEXT NOT NULL, PRIMARY KEY(run_id,page_no)
        );
        CREATE INDEX IF NOT EXISTS translation_page_asset ON translation_pages(asset_sha);
        """)


def create_run(con, payload: dict, job_id: str, run_id: str):
    """Create/activate a generation inside the caller's queue-write transaction."""
    paper = con.execute("SELECT * FROM papers WHERE id=? AND owner_id=?", (payload["paper_id"], core.OWNER_ID)).fetchone()
    if not paper or paper["sha256"] != payload["source_sha"]:
        raise HTTPException(409, "原文已变化，请重新提交翻译任务")
    if paper["translated_sha256"] != payload["translation_base_sha"] or paper["active_translation_run_id"] != payload.get("translation_base_run_id"):
        raise HTTPException(409, "译文已在提交期间更新，保留现有译文；请重新提交翻译任务")
    previous = paper["active_translation_run_id"]
    now = core.now()
    if previous:
        con.execute("UPDATE translation_runs SET status='superseded',updated_at=? WHERE id=?", (now, previous))
        con.execute("UPDATE translation_pages SET status='cancelled',updated_at=? WHERE run_id=? AND status!='ready'", (now, previous))
    con.execute("INSERT INTO translation_runs(id,paper_id,job_id,source_sha,status,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?)", (run_id, payload["paper_id"], job_id, payload["source_sha"], now, now))
    con.executemany("INSERT INTO translation_pages(run_id,page_no,status,updated_at) VALUES(?,?,'queued',?)", ((run_id, n, now) for n in range(1, payload["page_count"] + 1)))
    con.execute("UPDATE papers SET active_translation_run_id=? WHERE id=?", (run_id, payload["paper_id"]))


def _active(con, run_id: str, job_id: str, *, running: bool = True):
    row = con.execute("SELECT r.*,p.active_translation_run_id,p.sha256,j.status AS job_status FROM translation_runs r JOIN papers p ON p.id=r.paper_id JOIN jobs j ON j.id=r.job_id WHERE r.id=? AND r.job_id=? AND p.owner_id=?", (run_id, job_id, core.OWNER_ID)).fetchone()
    if not row or row["active_translation_run_id"] != run_id or row["sha256"] != row["source_sha"]:
        raise HTTPException(409, "译文已在任务提交后更新，保留现有译文；请确认后重新提交翻译任务")
    if running and row["job_status"] != "running":
        raise Stopped()
    return row


def validate_run(run_id: str, job_id: str, *, running: bool = True):
    with core.db() as con:
        return dict(_active(con, run_id, job_id, running=running))


def resume_run(con, run_id: str, job_id: str):
    _active(con, run_id, job_id, running=False)
    now = core.now()
    con.execute("UPDATE translation_runs SET status='queued',updated_at=? WHERE id=?", (now, run_id))
    con.execute("UPDATE translation_pages SET status='queued',error=NULL,updated_at=? WHERE run_id=? AND status!='ready'", (now, run_id))


def stop_run(con, job_id: str, status: str):
    """Preserve ready pages; only pending work changes state on stop/restart."""
    now = core.now()
    row = con.execute("SELECT id FROM translation_runs WHERE job_id=? AND status IN ('queued','running','failed','interrupted','cancelled')", (job_id,)).fetchone()
    if not row:
        return
    con.execute("UPDATE translation_runs SET status=?,updated_at=? WHERE id=?", (status, now, row["id"]))
    page_status = "queued" if status == "interrupted" else "cancelled"
    con.execute("UPDATE translation_pages SET status=?,updated_at=? WHERE run_id=? AND status IN ('queued','running')", (page_status, now, row["id"]))


def pending_pages(run_id: str, job_id: str, start_page: int = 1) -> list[int]:
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        _active(con, run_id, job_id)
        con.execute("UPDATE translation_runs SET status='running',updated_at=? WHERE id=?", (core.now(), run_id))
        rows = con.execute("SELECT page_no FROM translation_pages WHERE run_id=? AND status!='ready' ORDER BY page_no", (run_id,)).fetchall()
    pages = [row["page_no"] for row in rows]
    # The current reading page gets a head start; the rest remain deterministic.
    preferred = [n for n in pages if start_page <= n <= start_page + 2]
    return preferred + [n for n in pages if n not in preferred]


def start_page(run_id: str, job_id: str, page: int):
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        _active(con, run_id, job_id)
        con.execute("UPDATE translation_pages SET status='running',error=NULL,updated_at=? WHERE run_id=? AND page_no=? AND status!='ready'", (core.now(), run_id, page))


def publish_page(run_id: str, job_id: str, page: int, content: bytes):
    info = pdf.inspect_pdf(content)
    if info["page_count"] != 1:
        raise HTTPException(422, "逐页翻译必须返回单页 PDF；该页未发布，可重试")
    digest = hashlib.sha256(content).hexdigest()
    filename = core._store_pdf(content, digest)
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        _active(con, run_id, job_id)
        # Cancellation/manual replacement and this update serialize on SQLite.
        changed = con.execute("UPDATE translation_pages SET status='ready',asset_sha=?,filename=?,error=NULL,updated_at=? WHERE run_id=? AND page_no=? AND status='running'", (digest, filename, core.now(), run_id, page)).rowcount
        if not changed:
            raise Stopped()


def fail_page(run_id: str, job_id: str, page: int, message: str):
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        _active(con, run_id, job_id)
        con.execute("UPDATE translation_pages SET status='failed',error=?,updated_at=? WHERE run_id=? AND page_no=? AND status!='ready'", (message, core.now(), run_id, page))


def counts(run_id: str) -> dict:
    with core.db() as con:
        rows = con.execute("SELECT status,COUNT(*) AS n FROM translation_pages WHERE run_id=? GROUP BY status", (run_id,)).fetchall()
    result = {row["status"]: row["n"] for row in rows}
    return {"total_pages": sum(result.values()), "ready_pages": result.get("ready", 0), "failed_pages": result.get("failed", 0), "pending_pages": result.get("queued", 0) + result.get("running", 0), "translation_run_id": run_id, "scope": "progressive-pdf-translation", "visual_evidence_verified": False}


def finish_run(run_id: str, job_id: str) -> dict:
    """Merge ready pages for download without changing any page identities."""
    with core.db() as con:
        _active(con, run_id, job_id)
        rows = con.execute("SELECT * FROM translation_pages WHERE run_id=? ORDER BY page_no", (run_id,)).fetchall()
    coverage = counts(run_id)
    if not rows or any(row["status"] != "ready" for row in rows):
        with core.db() as con:
            con.execute("BEGIN IMMEDIATE")
            _active(con, run_id, job_id)
            con.execute("UPDATE translation_runs SET status='failed',updated_at=? WHERE id=?", (core.now(), run_id))
        return coverage
    with pdf.PDF_LOCK, fitz.open() as merged:
        for row in rows:
            with fitz.open(_path(row["filename"])) as page_pdf:
                merged.insert_pdf(page_pdf)
        content = merged.tobytes(garbage=3, deflate=True)
    digest = hashlib.sha256(content).hexdigest()
    filename = core._store_pdf(content, digest)
    with core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        _active(con, run_id, job_id)
        con.execute("UPDATE translation_runs SET status='succeeded',merged_filename=?,merged_sha=?,updated_at=? WHERE id=?", (filename, digest, core.now(), run_id))
    return coverage


def _path(filename: str | None) -> Path:
    if not filename:
        raise HTTPException(404, "译文页面文件不可用")
    path = (core.DATA_DIR / filename).resolve()
    if not path.is_relative_to(core.DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "译文页面文件不可用")
    return path


def page_asset(paper: dict, page: int, v: str | None = None) -> dict:
    if not 1 <= page <= paper["page_count"]:
        raise HTTPException(404, "论文页码不存在")
    with core.db() as con:
        if v:
            row = con.execute("SELECT t.* FROM translation_pages t JOIN translation_runs r ON r.id=t.run_id WHERE r.paper_id=? AND t.page_no=? AND t.asset_sha=? AND t.status='ready' ORDER BY r.created_at DESC LIMIT 1", (paper["id"], page, v)).fetchone()
        else:
            row = con.execute("SELECT * FROM translation_pages WHERE run_id=? AND page_no=?", (paper.get("active_translation_run_id"), page)).fetchone()
    if not row or row["status"] != "ready":
        raise HTTPException(404 if v else 409, "这一页译文尚未就绪；已完成页可先阅读")
    return {"path": _path(row["filename"]), "source_sha": row["asset_sha"], "translation_run_id": row["run_id"], "pdf_page": 1}


def merged_asset(paper: dict) -> Path:
    with core.db() as con:
        row = con.execute("SELECT * FROM translation_runs WHERE id=? AND paper_id=?", (paper.get("active_translation_run_id"), paper["id"])).fetchone()
    if not row or not row["merged_filename"] or row["status"] != "succeeded":
        raise HTTPException(409, "译文尚未全部完成；已完成页面可在阅读器中查看")
    return _path(row["merged_filename"])


def manifest(paper_id: str) -> dict:
    paper = core.get_paper(paper_id)
    run_id = paper.get("active_translation_run_id")
    if run_id:
        with core.db() as con:
            run = con.execute("SELECT * FROM translation_runs WHERE id=? AND paper_id=?", (run_id, paper_id)).fetchone()
            rows = con.execute("SELECT * FROM translation_pages WHERE run_id=? ORDER BY page_no", (run_id,)).fetchall()
        if not run:
            raise HTTPException(409, "译文任务记录不可用，请重新提交")
        return {"active_run_id": run_id, "job_id": run["job_id"], "mode": "progressive", "status": run["status"], "download_ready": bool(run["merged_filename"] and run["status"] == "succeeded"), "pages": [{"page": row["page_no"], "status": row["status"], "source_sha": row["asset_sha"], "translation_run_id": run_id, "error": row["error"]} for row in rows]}
    if paper["translated_filename"]:
        return {"active_run_id": None, "job_id": None, "mode": "manual", "status": "succeeded", "download_ready": True, "pages": [{"page": n, "status": "ready", "source_sha": paper["translated_sha256"], "translation_run_id": None, "error": None} for n in range(1, paper["page_count"] + 1)]}
    return {"active_run_id": None, "job_id": None, "mode": "none", "status": "none", "download_ready": False, "pages": []}
