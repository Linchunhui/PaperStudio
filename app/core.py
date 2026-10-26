"""Persistent projects, immutable PDFs and permission-scoped reading notes."""
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
            translated_page_count INTEGER, active_translation_run_id TEXT, created_at TEXT NOT NULL,
            owner_id TEXT NOT NULL DEFAULT 'local', UNIQUE(owner_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS project_papers (
            paper_id TEXT NOT NULL REFERENCES papers(id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            status TEXT NOT NULL DEFAULT 'unread', last_page INTEGER NOT NULL DEFAULT 1,
            page_offset REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL, PRIMARY KEY(paper_id, project_id)
        );
        CREATE TABLE IF NOT EXISTS annotations (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL,
            project_id TEXT NOT NULL, side TEXT NOT NULL,
            page INTEGER NOT NULL, quote TEXT NOT NULL, note TEXT NOT NULL,
            color TEXT NOT NULL, rects TEXT NOT NULL, source_sha TEXT NOT NULL,
            translation_run_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            FOREIGN KEY(paper_id, project_id) REFERENCES project_papers(paper_id, project_id)
        );
        CREATE INDEX IF NOT EXISTS annotation_context ON annotations(paper_id, project_id, page);
        """)
        # Additive migrations preserve existing local reading data.
        for table, column, declaration in [
            ("papers", "active_translation_run_id", "TEXT"),
            ("annotations", "translation_run_id", "TEXT"),
            ("project_papers", "page_offset", "REAL NOT NULL DEFAULT 0"),
        ]:
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
        connection.execute(
            "INSERT OR IGNORE INTO projects(id,name,parent_id,created_at,owner_id) VALUES(?,?,?,?,?)",
            ("inbox", "待读收件箱", None, now(), OWNER_ID),
        )
    # Separate commit: translation tables reference the already-created library.
    from . import translations
    translations.init_db()
    from . import auth
    auth.init_auth()


def current_user_id() -> str:
    from . import auth
    return auth.current_user_id()


def resolve_project_id(project_id: str) -> str:
    if project_id == "inbox" and current_user_id() != "local":
        from . import auth
        return auth.current_user()["inbox_id"]
    return project_id


def _project_role(connection, project: dict, user_id: str) -> str | None:
    if project["owner_id"] == user_id:
        return "owner"
    roles = [row["role"] for row in connection.execute(
        "SELECT gm.role FROM project_shares ps JOIN group_members gm ON gm.group_id=ps.group_id WHERE ps.project_id=? AND gm.user_id=?",
        (project["id"], user_id),
    )]
    return next((role for role in ("admin", "editor", "viewer") if role in roles), None)


def visible_project_ids(*, write=False) -> list[str]:
    user_id = current_user_id()
    with db() as connection:
        rows = connection.execute("SELECT DISTINCT p.* FROM projects p LEFT JOIN project_shares ps ON ps.project_id=p.id LEFT JOIN group_members gm ON gm.group_id=ps.group_id WHERE p.owner_id=? OR gm.user_id=?", (user_id,user_id)).fetchall()
        return [r["id"] for r in rows if not write or _project_role(connection, dict(r), user_id) != "viewer"]


def get_project(project_id: str, *, write=False) -> dict:
    project_id = resolve_project_id(project_id)
    with db() as connection:
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        role = _project_role(connection, dict(row), current_user_id()) if row else None
    if not row or not role:
        raise HTTPException(404, "项目不存在")
    if write and role not in {"owner", "admin", "editor"}:
        raise HTTPException(403, "当前项目为只读权限")
    return dict(row) | {"access_role": role, "can_edit": role != "viewer"}


def get_paper(paper_id: str, *, write=False) -> dict:
    user_id = current_user_id()
    with db() as connection:
        row = connection.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            raise HTTPException(404, "论文不存在")
        result = dict(row)
        memberships = []
        for membership in connection.execute("SELECT pp.* FROM project_papers pp WHERE pp.paper_id=? ORDER BY pp.updated_at DESC", (paper_id,)):
            project = connection.execute("SELECT * FROM projects WHERE id=?", (membership["project_id"],)).fetchone()
            role = _project_role(connection, dict(project), user_id) if project else None
            if not role:
                continue
            item = dict(membership) | {"access_role": role, "can_edit": role != "viewer",
                                       "project_owner_id": project["owner_id"]}
            progress = connection.execute("SELECT status,last_page,page_offset,updated_at FROM reading_progress WHERE user_id=? AND paper_id=? AND project_id=?", (user_id,paper_id,project["id"])).fetchone()
            if progress:
                item.update(dict(progress))
            elif project["owner_id"] != user_id:
                item.update(status="unread",last_page=1,page_offset=0)
            memberships.append(item)
        if result["owner_id"] != user_id and not memberships:
            raise HTTPException(404, "论文不存在")
        # Copying a read-only shared paper into one's own project must not grant
        # permission to replace the original owner's globally shared PDF asset.
        result["can_edit"] = result["owner_id"] == user_id or any(
            m["can_edit"] and m["project_owner_id"] == result["owner_id"] for m in memberships)
        if write and not result["can_edit"]:
            raise HTTPException(403, "当前论文为只读权限")
        result["memberships"] = memberships
        result["has_translation"] = bool(result["translated_filename"] or result["active_translation_run_id"])
    return result


def require_membership(paper_id: str, project_id: str, *, write=False) -> dict:
    project = get_project(project_id, write=write)
    paper = get_paper(paper_id)
    membership = next((m for m in paper["memberships"] if m["project_id"] == project["id"]), None)
    if not membership:
        raise HTTPException(409, "请先将这篇论文加入所选项目")
    return membership


def link_paper(paper_id: str, project_id: str) -> dict:
    get_paper(paper_id)
    project_id = get_project(project_id, write=True)["id"]
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO project_papers(paper_id,project_id,status,last_page,updated_at) VALUES(?,?,'unread',1,?)", (paper_id, project_id, now()))
    return require_membership(paper_id, project_id)


def paper_asset_path(paper: dict, side: str = "original") -> Path:
    """Resolve the immutable file belonging to one metadata snapshot."""
    if side not in {"original", "translation"}:
        raise HTTPException(422, "PDF 栏位必须为 original 或 translation")
    if side == "translation" and paper.get("active_translation_run_id"):
        from . import translations
        return translations.merged_asset(paper)
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
    if side == "translation" and paper.get("active_translation_run_id"):
        with db() as connection:
            row = connection.execute("SELECT merged_sha FROM translation_runs WHERE id=? AND paper_id=?", (paper["active_translation_run_id"], paper["id"])).fetchone()
        if not row or not row["merged_sha"]:
            raise HTTPException(409, "译文仍在逐页生成，完整 PDF 尚未就绪")
        return row["merged_sha"]
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
    project_id = get_project(project_id, write=True)["id"]
    info = pdf.inspect_pdf(content)
    digest = hashlib.sha256(content).hexdigest()
    paper_id = uid()
    clean_title = title.strip() or info["title"] or Path(filename).stem or "未命名论文"
    if len(clean_title) > 500:
        raise HTTPException(422, "论文标题不能超过 500 字符")
    stored_filename = _store_pdf(content, digest)
    with db() as connection:
        connection.execute("INSERT OR IGNORE INTO papers(id,title,filename,sha256,page_count,created_at,owner_id) VALUES(?,?,?,?,?,?,?)", (paper_id, clean_title, stored_filename, digest, info["page_count"], now(), current_user_id()))
        paper_id = connection.execute("SELECT id FROM papers WHERE owner_id=? AND sha256=?", (current_user_id(), digest)).fetchone()["id"]
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
    paper = get_paper(paper_id, write=True)
    info = pdf.inspect_pdf(pdf_bytes)
    if info["page_count"] != paper["page_count"]:
        raise HTTPException(422, "首版按原文页码对应译文，请上传与原文页数相同的单语译文 PDF（不是左右合并版）")
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    stored = _store_pdf(pdf_bytes, digest)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT translated_sha256,active_translation_run_id FROM papers WHERE id=?",
            (paper_id,),
        ).fetchone()
        if not current:
            raise HTTPException(404, "论文不存在")
        if expected_translation_sha is not _TRANSLATION_UNSET and (
            current["translated_sha256"] != expected_translation_sha or current["active_translation_run_id"]
        ):
            raise HTTPException(409, "译文已在任务运行期间更新，保留现有译文；请确认后重新提交翻译任务")
        if current["active_translation_run_id"]:
            run_id = current["active_translation_run_id"]
            connection.execute("UPDATE translation_runs SET status='superseded',updated_at=? WHERE id=?", (now(), run_id))
            connection.execute("UPDATE translation_pages SET status='cancelled',error='已由手动导入的译文替代',updated_at=? WHERE run_id=? AND status!='ready'", (now(), run_id))
        connection.execute("UPDATE papers SET translated_filename=?,translated_sha256=?,translated_page_count=?,active_translation_run_id=NULL WHERE id=?", (stored, digest, info["page_count"], paper_id))
    return get_paper(paper_id)


def annotation_list(paper_id: str, project_id: str) -> list[dict]:
    project_id = get_project(project_id)["id"]
    require_membership(paper_id, project_id)
    paper = get_paper(paper_id)
    with db() as connection:
        rows = connection.execute("SELECT * FROM annotations WHERE paper_id=? AND project_id=? AND (owner_id=? OR visibility='shared') ORDER BY page,created_at,id", (paper_id, project_id, current_user_id())).fetchall()
        active_run = paper.get("active_translation_run_id")
        page_hashes = {row["page_no"]: row["asset_sha"] for row in connection.execute(
            "SELECT page_no,asset_sha FROM translation_pages WHERE run_id=? AND status='ready'", (active_run,)
        )} if active_run else {}
    result = []
    for row in rows:
        entry = dict(row)
        entry["rects"] = json.loads(entry["rects"])
        if entry["side"] == "original":
            entry["is_current"] = entry["source_sha"] == paper["sha256"]
        elif active_run:
            entry["is_current"] = (
                entry.get("translation_run_id") == active_run
                and entry["source_sha"] == page_hashes.get(entry["page"])
            )
        else:
            entry["is_current"] = not entry.get("translation_run_id") and entry["source_sha"] == paper["translated_sha256"]
        result.append(decorate_annotation(entry))
    return result


def decorate_annotation(result: dict) -> dict:
    result = dict(result)
    with db() as connection:
        author = connection.execute("SELECT display_name FROM users WHERE id=?", (result["owner_id"],)).fetchone()
    result["author"] = author["display_name"] if author else "本地所有者"
    result["can_edit"] = result["owner_id"] == current_user_id()
    if not result["can_edit"] and result["visibility"] == "shared":
        result["can_edit"] = get_project(result["project_id"])["can_edit"]
    return result


def get_annotation(annotation_id: str, *, write=False) -> dict:
    with db() as connection:
        row = connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone()
    if not row or (row["visibility"] != "shared" and row["owner_id"] != current_user_id()):
        raise HTTPException(404, "笔记不存在")
    require_membership(row["paper_id"], row["project_id"])
    result = decorate_annotation(dict(row))
    if write and not result["can_edit"]:
        raise HTTPException(403, "当前笔记为只读权限")
    result["rects"] = json.loads(result["rects"])
    return result


def record_annotation_version(connection, annotation: dict, action: str) -> None:
    entry = dict(annotation)
    connection.execute("INSERT INTO annotation_versions(id,annotation_id,version,editor_id,visibility,content_json,action,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (uid(), entry["id"], entry["version"], current_user_id(), entry["visibility"], json.dumps(entry,ensure_ascii=False),action,now()))
