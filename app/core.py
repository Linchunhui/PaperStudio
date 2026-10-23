"""Persistent local-single-user projects, immutable PDFs, and anchored notes."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from . import pdf

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PAPERSTUDIO_DATA_DIR", str(ROOT / "data"))).resolve()
OWNER_ID = "local"
_TRANSLATION_UNSET = object()


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA_DIR / "paperstudio.sqlite3", timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=20000")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    (DATA_DIR / "files").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "cache").mkdir(parents=True, exist_ok=True)
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            parent_id TEXT REFERENCES projects(id), created_at TEXT NOT NULL,
            owner_id TEXT NOT NULL DEFAULT 'local'
        );
        CREATE INDEX IF NOT EXISTS project_parent ON projects(parent_id);
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
            sha256 TEXT NOT NULL, page_count INTEGER NOT NULL,
            translated_filename TEXT, translated_sha256 TEXT,
            translated_page_count INTEGER, created_at TEXT NOT NULL,
            owner_id TEXT NOT NULL DEFAULT 'local', UNIQUE(owner_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS project_papers (
            paper_id TEXT NOT NULL REFERENCES papers(id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            status TEXT NOT NULL DEFAULT 'unread', last_page INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL, PRIMARY KEY(paper_id, project_id)
        );
        CREATE TABLE IF NOT EXISTS annotations (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
            project_id TEXT NOT NULL, side TEXT NOT NULL,
            page INTEGER NOT NULL, quote TEXT NOT NULL, note TEXT NOT NULL,
            color TEXT NOT NULL, rects TEXT NOT NULL, source_sha TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(paper_id, project_id) REFERENCES project_papers(paper_id, project_id)
        );
        CREATE INDEX IF NOT EXISTS annotation_context ON annotations(paper_id, project_id, page);
        """)
        connection.execute(
            "INSERT OR IGNORE INTO projects(id,name,parent_id,created_at,owner_id) VALUES(?,?,?,?,?)",
            ("inbox", "待读收件箱", None, now(), OWNER_ID),
        )


def get_project(project_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id, OWNER_ID)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return dict(row)


def get_paper(paper_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM papers WHERE id=? AND owner_id=?", (paper_id, OWNER_ID)).fetchone()
        if not row:
            raise HTTPException(404, "论文不存在")
        result = dict(row)
        result["memberships"] = [dict(r) for r in connection.execute(
            "SELECT pp.* FROM project_papers pp JOIN projects p ON pp.project_id=p.id WHERE pp.paper_id=? AND p.owner_id=? ORDER BY pp.updated_at DESC",
            (paper_id, OWNER_ID),
        )]
        result["has_translation"] = bool(result["translated_filename"])
    return result


def require_membership(paper_id: str, project_id: str) -> dict:
    get_paper(paper_id)
    get_project(project_id)
    with db() as connection:
        row = connection.execute("SELECT * FROM project_papers WHERE paper_id=? AND project_id=?", (paper_id, project_id)).fetchone()
    if not row:
        raise HTTPException(409, "请先将这篇论文加入所选项目")
    return dict(row)


def link_paper(paper_id: str, project_id: str) -> dict:
    get_paper(paper_id)
    get_project(project_id)
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO project_papers(paper_id,project_id,status,last_page,updated_at) VALUES(?,?,'unread',1,?)", (paper_id, project_id, now()))
    return require_membership(paper_id, project_id)


def paper_asset_path(paper: dict, side: str = "original") -> Path:
    """Resolve the immutable file belonging to one metadata snapshot."""
    if side not in {"original", "translation"}:
        raise HTTPException(422, "PDF 栏位必须为 original 或 translation")
    filename = paper["filename"] if side == "original" else paper["translated_filename"]
    if not filename:
        raise HTTPException(404, "译文尚未生成，可先上传同页数的译文 PDF")
    path = (DATA_DIR / filename).resolve()
    if not path.is_relative_to(DATA_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "PDF 文件不可用")
    return path


def paper_path(paper_id: str, side: str = "original") -> Path:
    return paper_asset_path(get_paper(paper_id), side)


def source_sha(paper: dict, side: str) -> str:
    if side not in {"original", "translation"}:
        raise HTTPException(422, "PDF 栏位无效")
    value = paper["sha256"] if side == "original" else paper["translated_sha256"]
    if not value:
        raise HTTPException(409, "译文尚未生成")
    return value


def _store_pdf(content: bytes, digest: str) -> str:
    directory = DATA_DIR / "files"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.pdf"
    if not path.exists():
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Replacements are byte-identical because the target is content-addressed.
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return f"files/{digest}.pdf"


def add_paper(content: bytes, project_id: str, title: str = "", filename: str = "paper.pdf") -> dict:
    get_project(project_id)
    info = pdf.inspect_pdf(content)
    digest = hashlib.sha256(content).hexdigest()
    paper_id = uid()
    clean_title = title.strip() or info["title"] or Path(filename).stem or "未命名论文"
    if len(clean_title) > 500:
        raise HTTPException(422, "论文标题不能超过 500 字符")
    stored_filename = _store_pdf(content, digest)
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO papers(id,title,filename,sha256,page_count,created_at,owner_id) VALUES(?,?,?,?,?,?,?)", (paper_id, clean_title, stored_filename, digest, info["page_count"], now(), OWNER_ID))
        paper_id = connection.execute("SELECT id FROM papers WHERE owner_id=? AND sha256=?", (OWNER_ID, digest)).fetchone()["id"]
        connection.execute("INSERT OR IGNORE INTO project_papers(paper_id,project_id,status,last_page,updated_at) VALUES(?,?,'unread',1,?)", (paper_id, project_id, now()))
    return get_paper(paper_id)


def attach_translation(
    paper_id: str,
    pdf_bytes: bytes,
    filename: str = "translated.pdf",
    *,
    expected_translation_sha: str | None | object = _TRANSLATION_UNSET,
) -> dict:
    """Attach a validated translated PDF, optionally comparing the prior version.

    Omitted expectation is an explicit manual replacement. Background jobs pass
    their enqueue-time hash (including None), so a newer manual upload or another
    completed translation cannot be overwritten by a stale job.
    """
    paper = get_paper(paper_id)
    info = pdf.inspect_pdf(pdf_bytes)
    if info["page_count"] != paper["page_count"]:
        raise HTTPException(422, "首版按原文页码对应译文，请上传与原文页数相同的单语译文 PDF（不是左右合并版）")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    stored = _store_pdf(pdf_bytes, digest)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT translated_sha256 FROM papers WHERE id=? AND owner_id=?",
            (paper_id, OWNER_ID),
        ).fetchone()
        if not current:
            raise HTTPException(404, "论文不存在")
        if expected_translation_sha is not _TRANSLATION_UNSET and current["translated_sha256"] != expected_translation_sha:
            raise HTTPException(409, "译文已在任务运行期间更新，保留现有译文；请确认后重新提交翻译任务")
        connection.execute("UPDATE papers SET translated_filename=?,translated_sha256=?,translated_page_count=? WHERE id=? AND owner_id=?", (stored, digest, info["page_count"], paper_id, OWNER_ID))
    return get_paper(paper_id)


def annotation_list(paper_id: str, project_id: str) -> list[dict]:
    require_membership(paper_id, project_id)
    paper = get_paper(paper_id)
    with db() as connection:
        rows = connection.execute("SELECT * FROM annotations WHERE paper_id=? AND project_id=? ORDER BY page,created_at,id", (paper_id, project_id)).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        entry["rects"] = json.loads(entry["rects"])
        current = paper["sha256"] if entry["side"] == "original" else paper["translated_sha256"]
        entry["is_current"] = entry["source_sha"] == current
        result.append(entry)
    return result


def get_annotation(annotation_id: str) -> dict:
    with db() as connection:
        row = connection.execute("SELECT a.* FROM annotations a JOIN papers p ON a.paper_id=p.id JOIN projects j ON a.project_id=j.id WHERE a.id=? AND p.owner_id=? AND j.owner_id=?", (annotation_id, OWNER_ID, OWNER_ID)).fetchone()
    if not row:
        raise HTTPException(404, "笔记不存在")
    result = dict(row)
    result["rects"] = json.loads(result["rects"])
    return result
