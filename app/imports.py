"""Durable import plans. Downloads/parsing never wait behind a model call."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading

from fastapi import HTTPException

from . import auth, core, jobs, pdf, safe_download
from .source_resolver import resolve_source

_STOP = threading.Event()
_WAKE = threading.Event()
_LOCK = threading.RLock()
_THREAD: threading.Thread | None = None
PARSER_VERSION = "babeldoc-first-v2"
MODES = {"abstract", "cards", "summary", "all"}
ACTIVE = {"queued", "running"}
FAILURES = {"failed", "blocked", "interrupted", "cancelled"}
_LOG = logging.getLogger(__name__)


def init_imports():
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS imports (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            paper_id TEXT REFERENCES papers(id), title TEXT NOT NULL DEFAULT '',
            source_json TEXT NOT NULL, processing_json TEXT NOT NULL, status TEXT NOT NULL,
            error TEXT, dedupe_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            owner_id TEXT NOT NULL DEFAULT 'local', generation INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS imports_queue ON imports(status,created_at);
        CREATE INDEX IF NOT EXISTS imports_dedupe ON imports(dedupe_key,status);
        CREATE TABLE IF NOT EXISTS import_steps (
            id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES imports(id),
            kind TEXT NOT NULL, mode TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
            dependencies_json TEXT NOT NULL DEFAULT '[]', job_id TEXT, owns_job INTEGER NOT NULL DEFAULT 0,
            error TEXT, progress_json TEXT NOT NULL DEFAULT '{}', attempt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS import_steps_plan ON import_steps(import_id,created_at);
        CREATE TABLE IF NOT EXISTS paper_sources (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
            source_json TEXT NOT NULL, source_sha TEXT NOT NULL, resolved_url TEXT,
            created_at TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT 'local',
            UNIQUE(paper_id,source_json)
        );
        CREATE TABLE IF NOT EXISTS parse_artifacts (
            source_sha TEXT NOT NULL, parser_version TEXT NOT NULL, filename TEXT NOT NULL,
            coverage_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(source_sha,parser_version)
        );
        """)
        if "generation" not in {row["name"] for row in con.execute("PRAGMA table_info(imports)")}:
            con.execute("ALTER TABLE imports ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
        if "owns_job" not in {row["name"] for row in con.execute("PRAGMA table_info(import_steps)")}:
            con.execute("ALTER TABLE import_steps ADD COLUMN owns_job INTEGER NOT NULL DEFAULT 0")


def normalize_processing(value: dict | None) -> dict:
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {"parse", "translate", "guide", "brief_modes"}:
        raise HTTPException(422, "未知导入处理选项")
    for key in ("parse", "translate", "guide"):
        if key in value and not isinstance(value[key], bool):
            raise HTTPException(422, "处理开关须为布尔值")
    modes = value.get("brief_modes", [])
    if not isinstance(modes, list) or any(not isinstance(mode, str) or mode not in MODES for mode in modes):
        raise HTTPException(422, "粗读模式须为 abstract/cards/summary/all")
    modes = list(dict.fromkeys(modes))
    if "all" in modes:
        modes = ["all"]
    return {"parse": bool(value.get("parse", True) or value.get("guide") or modes),
            "translate": value.get("translate", False), "guide": value.get("guide", False), "brief_modes": modes}


def _row(import_id: str) -> dict:
    with core.db() as con:
        row = con.execute("SELECT * FROM imports WHERE id=? AND owner_id=?", (import_id, auth.current_user_id())).fetchone()
    if not row:
        raise HTTPException(404, "导入任务不存在")
    return dict(row)


def _steps(import_id: str) -> list[dict]:
    with core.db() as con:
        return [dict(row) for row in con.execute("SELECT * FROM import_steps WHERE import_id=? ORDER BY rowid", (import_id,))]


def create_import(*, project_id: str, url: str | None = None, paper_id: str | None = None,
                  title: str = "", processing: dict | None = None, refresh: bool = False,
                  catalog_paper_id: str | None = None) -> dict:
    project_id = core.get_project(project_id, write=True)["id"]
    if bool(url) == bool(paper_id):
        raise HTTPException(422, "请提供一个链接或一个已入库论文 ID")
    if catalog_paper_id and not url:
        raise HTTPException(422, "目录关联需要可核对的论文链接")
    if len(title) > 500:
        raise HTTPException(422, "论文标题不能超过 500 字符")
    processing = normalize_processing(processing)
    if paper_id:
        paper = core.get_paper(paper_id)
        core.link_paper(paper_id, project_id)
        title = title.strip() or paper["title"]
        source = {"kind": "existing-paper", "source_sha": paper["sha256"], "canonical_url": None}
    else:
        source = resolve_source(url)
        if catalog_paper_id:
            from .recommendation_profiles import verify_catalog_source
            source["catalog_paper_id"] = verify_catalog_source(source, catalog_paper_id)
        # Opening an already imported title reuses the current immutable PDF.
        # An explicit refresh still fetches a new version of an unversioned URL.
        existing = None if refresh else _existing_for_source(source)
        if existing:
            paper_id = existing["id"]
            core.link_paper(paper_id, project_id)
            title = title.strip() or existing["title"]
            if catalog_paper_id:
                from .recommendation_profiles import link_paper
                link_paper(paper_id, catalog_paper_id)
        # Validate literal/private targets and DNS before accepting, then again
        # on every actual connection; the download socket is pinned separately.
        if not paper_id:
            try:
                safe_download.validated_target(source["download_url"])
            except safe_download.DownloadError as exc:
                raise HTTPException(422, str(exc)) from None
    frozen = json.dumps({"source": source, "processing": processing, "project_id": project_id, "refresh": refresh}, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(frozen.encode()).hexdigest()
    with _LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        # Only coalesce active requests: an unversioned arXiv URL may have new
        # bytes tomorrow, and explicit re-import must be able to retrieve them.
        old = con.execute("SELECT id FROM imports WHERE dedupe_key=? AND status IN ('queued','running') AND owner_id=? ORDER BY created_at DESC LIMIT 1", (key, auth.current_user_id())).fetchone()
        if old:
            import_id = old["id"]
        else:
            import_id, timestamp = core.uid(), core.now()
            con.execute("INSERT INTO imports(id,project_id,paper_id,title,source_json,processing_json,status,dedupe_key,created_at,updated_at,owner_id) VALUES(?,?,?,?,?,?,'queued',?,?,?,?)", (import_id, project_id, paper_id, title.strip(), json.dumps(source, ensure_ascii=False), json.dumps(processing), key, timestamp, timestamp, auth.current_user_id()))
            download_id = core.uid()
            definitions = [(download_id, "download", "", "succeeded" if paper_id else "queued", [])]
            parse_id = None
            if processing["parse"]:
                parse_id = core.uid()
                definitions.append((parse_id, "parse", "", "queued", [download_id]))
            if processing["guide"]:
                definitions.append((core.uid(), "guide", "", "queued", [parse_id or download_id]))
            for mode in processing["brief_modes"]:
                definitions.append((core.uid(), "brief", mode, "queued", [parse_id or download_id]))
            # Enqueue short text tasks before the long translation task when a
            # plan requests both; both remain independent after their inputs.
            if processing["translate"]:
                definitions.append((core.uid(), "translate", "", "queued", [download_id]))
            for sid, kind, mode, status, dependencies in definitions:
                con.execute("INSERT INTO import_steps(id,import_id,kind,mode,status,dependencies_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (sid, import_id, kind, mode, status, json.dumps(dependencies), timestamp, timestamp))
    _WAKE.set()
    return get_import(import_id)


def _existing_for_source(source: dict) -> dict | None:
    """Reuse only the caller's previously resolved source, never a public directory alias."""
    canonical = source.get("canonical_url")
    if not canonical:
        return None
    with core.db() as con:
        rows = con.execute("SELECT paper_id,source_json FROM paper_sources WHERE owner_id=? ORDER BY created_at DESC", (auth.current_user_id(),)).fetchall()
    for row in rows:
        try:
            previous = json.loads(row["source_json"])
            if previous.get("canonical_url") == canonical and previous.get("kind") == source.get("kind"):
                return core.get_paper(row["paper_id"])
        except (ValueError, HTTPException):
            continue
    return None


def _update_step(step_id: str, *, status: str | None = None, error: str | None = None, progress: dict | None = None, job_id: str | None = None, owns_job: bool | None = None, expected_generation: int | None = None):
    updates, values = ["updated_at=?"], [core.now()]
    if status:
        updates += ["status=?", "error=?"]
        values += [status, error]
    if progress is not None:
        updates.append("progress_json=?")
        values.append(json.dumps(progress, ensure_ascii=False))
    if job_id is not None:
        updates.append("job_id=?")
        values.append(job_id)
    if owns_job is not None:
        updates.append("owns_job=?")
        values.append(int(owns_job))
    condition = " WHERE id=? AND status NOT IN ('cancelled','interrupted')"
    values.append(step_id)
    if expected_generation is not None:
        condition += " AND import_id IN (SELECT id FROM imports WHERE generation=?)"
        values.append(expected_generation)
    with core.db() as con:
        con.execute("UPDATE import_steps SET " + ",".join(updates) + condition, values)


def _refresh(import_id: str):
    with _LOCK:
        row = _row(import_id)
        if row["status"] == "cancelled":
            return
        steps = _steps(import_id)
        for step in steps:
            if step["job_id"] and step["status"] in ACTIVE and not json.loads(step["progress_json"]).get("retry_requested"):
                try:
                    job = jobs.get_job(step["job_id"])
                except HTTPException:
                    _update_step(step["id"], status="failed", error="关联处理任务不存在，请重新导入处理计划")
                    continue
                _update_step(step["id"], status=job["status"], error=job.get("error"), progress={"stage": job.get("stage"), "coverage": job.get("coverage", {})})
        steps = _steps(import_id)
        statuses = {step["id"]: step["status"] for step in steps}
        for step in steps:
            if step["status"] == "queued" and any(statuses.get(dep) in FAILURES for dep in json.loads(step["dependencies_json"])):
                _update_step(step["id"], status="blocked", error="前置步骤尚未成功，修复后可重试导入计划")
                statuses[step["id"]] = "blocked"
        steps = _steps(import_id)
        values = [step["status"] for step in steps]
        if all(v == "succeeded" for v in values):
            status = "succeeded"
        elif any(v == "running" for v in values):
            status = "running"
        elif any(v == "queued" for v in values):
            status = "queued"
        elif "interrupted" in values:
            status = "interrupted"
        elif "failed" in values:
            status = "partial" if row["paper_id"] else "failed"
        elif "blocked" in values:
            status = "blocked"
        elif "cancelled" in values:
            status = "cancelled"
        else:
            status = "failed"
        first_error = next((step["error"] for step in steps if step["error"]), None)
        with core.db() as con:
            con.execute("UPDATE imports SET status=?,error=?,updated_at=? WHERE id=? AND status<>'cancelled'", (status, first_error, core.now(), import_id))


def get_import(import_id: str) -> dict:
    _refresh(import_id)
    row = _row(import_id)
    result = {k: v for k, v in row.items() if k not in {"source_json", "processing_json", "dedupe_key", "owner_id"}}
    result["source"] = json.loads(row["source_json"])
    result["processing"] = json.loads(row["processing_json"])
    result["steps"] = []
    for step in _steps(import_id):
        public = {k: v for k, v in step.items() if k not in {"dependencies_json", "progress_json"}}
        public["dependencies"] = json.loads(step["dependencies_json"])
        public["progress"] = json.loads(step["progress_json"])
        result["steps"].append(public)
    return result


def list_imports(project_id: str | None = None, paper_id: str | None = None) -> list[dict]:
    query, args = "SELECT id FROM imports WHERE owner_id=?", [auth.current_user_id()]
    if project_id:
        project_id = core.get_project(project_id)["id"]
        query += " AND project_id=?"
        args.append(project_id)
    if paper_id:
        core.get_paper(paper_id)
        query += " AND paper_id=?"
        args.append(paper_id)
    with core.db() as con:
        ids = [row["id"] for row in con.execute(query + " ORDER BY created_at DESC,rowid DESC LIMIT 100", args)]
    return [get_import(value) for value in ids]


def cancel(import_id: str) -> dict:
    with _LOCK:
        row = _row(import_id)
        if row["status"] == "succeeded":
            return get_import(import_id)
        for step in _steps(import_id):
            if step["job_id"] and step["status"] in ACTIVE:
                with core.db() as con:
                    # jobs.enqueue deduplicates across plans. Cancel this plan's
                    # subscription, not another plan or a reader-created job.
                    managed = con.execute("SELECT 1 FROM import_steps WHERE job_id=? AND owns_job=1 LIMIT 1", (step["job_id"],)).fetchone()
                    other = con.execute("SELECT 1 FROM import_steps s JOIN imports i ON i.id=s.import_id WHERE s.job_id=? AND s.import_id<>? AND s.status IN ('queued','running') AND i.status<>'cancelled' LIMIT 1", (step["job_id"], import_id)).fetchone()
                if managed and not other:
                    jobs.cancel(step["job_id"])
        with core.db() as con:
            con.execute("UPDATE imports SET status='cancelled',error=NULL,generation=generation+1,updated_at=? WHERE id=?", (core.now(), import_id))
            con.execute("UPDATE import_steps SET status='cancelled',error=NULL,updated_at=? WHERE import_id=? AND status IN ('queued','running','blocked','interrupted')", (core.now(), import_id))
    return get_import(import_id)


def retry(import_id: str) -> dict:
    with _LOCK:
        _refresh(import_id)
        row = _row(import_id)
        if row["status"] in ACTIVE or row["status"] == "succeeded":
            raise HTTPException(409, "只有失败、受阻、取消或中断的导入计划可以重试")
        core.get_project(row["project_id"], write=True)
        with core.db() as con:
            con.execute("UPDATE imports SET status='queued',error=NULL,generation=generation+1,updated_at=? WHERE id=?", (core.now(), import_id))
            con.execute("UPDATE import_steps SET status='queued',error=NULL,progress_json=?,updated_at=? WHERE import_id=? AND status IN ('failed','blocked','cancelled','interrupted')", (json.dumps({"retry_requested": True}), core.now(), import_id))
    _WAKE.set()
    return get_import(import_id)


def _cancelled(import_id: str, expected_generation: int | None = None) -> bool:
    row = _row(import_id)
    return _STOP.is_set() or row["status"] == "cancelled" or (expected_generation is not None and row["generation"] != expected_generation)


def _download(row: dict, step: dict):
    source = json.loads(row["source_json"])
    result = safe_download.download_pdf(source["download_url"], core.DATA_DIR / "downloads", cancelled=lambda: _cancelled(row["id"], row["generation"]), progress=lambda count, total: _update_step(step["id"], progress={"downloaded_bytes": count, "total_bytes": total}, expected_generation=row["generation"]))
    try:
        content = result["path"].read_bytes()
        # Serialize the last cancellation check and publication. Cancellation
        # never removes a PDF which already became available for reading.
        with _LOCK:
            if _cancelled(row["id"], row["generation"]):
                raise safe_download.DownloadCancelled()
            filename = (source.get("arxiv_id") or "linked-paper").replace("/", "-") + ".pdf"
            paper = core.add_paper(content, row["project_id"], row["title"], filename)
            with core.db() as con:
                con.execute("UPDATE imports SET paper_id=?,updated_at=? WHERE id=?", (paper["id"], core.now(), row["id"]))
                con.execute("INSERT OR IGNORE INTO paper_sources(id,paper_id,source_json,source_sha,resolved_url,created_at,owner_id) VALUES(?,?,?,?,?,?,?)", (core.uid(), paper["id"], row["source_json"], paper["sha256"], result["resolved_url"], core.now(), auth.current_user_id()))
            if source.get("catalog_paper_id"):
                from .recommendation_profiles import link_paper
                link_paper(paper["id"], source["catalog_paper_id"])
            _update_step(step["id"], status="succeeded", progress={"downloaded_bytes": result["size_bytes"], "source_sha": paper["sha256"], "page_count": paper["page_count"]}, expected_generation=row["generation"])
    finally:
        result["path"].unlink(missing_ok=True)


def get_cached_fulltext(source_sha: str) -> dict | None:
    with core.db() as con:
        row = con.execute("SELECT * FROM parse_artifacts WHERE source_sha=? ORDER BY CASE WHEN parser_version=? THEN 0 ELSE 1 END,created_at DESC LIMIT 1", (source_sha, PARSER_VERSION)).fetchone()
    if not row:
        return None
    path = (core.DATA_DIR / row["filename"]).resolve()
    if not path.is_relative_to(core.DATA_DIR.resolve()) or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return value if (isinstance(value, dict) and value.get("source_sha") == source_sha
                     and value.get("parser_version") == row["parser_version"]
                     and isinstance(value.get("fulltext"), str) and isinstance(value.get("coverage"), dict)
                     and isinstance(value.get("pages"), list)) else None


def get_parse_artifact(paper_id: str) -> dict | None:
    return get_cached_fulltext(core.get_paper(paper_id)["sha256"])


def _parse(row: dict, step: dict):
    paper = core.get_paper(row["paper_id"])
    cached = get_cached_fulltext(paper["sha256"])
    if cached:
        _update_step(step["id"], status="succeeded", progress={"reused": True, **cached["coverage"]}, expected_generation=row["generation"])
        return
    from . import parsing
    parsed = parsing.ensure_parsed(paper, cancelled=lambda: _cancelled(row["id"], row["generation"]))
    page_texts = [page.get("text") or "\n".join(block.get("text", "") for block in page.get("blocks", [])) for page in parsed["pages"]]
    empty = [index + 1 for index, text in enumerate(page_texts) if not text.strip()]
    coverage = {**parsed.get("coverage", {}), "total_pages": len(page_texts), "text_pages": len(page_texts) - len(empty), "empty_text_pages": empty, "visual_evidence_verified": False, "scope": "pdf-extracted-text", "parser": parsed.get("provider", "unknown")}
    if len(empty) == len(page_texts):
        _update_step(step["id"], status="blocked", error="原始 PDF 已保存，但没有可提取文字层；全文解析与摘要需要先 OCR。", progress=coverage, expected_generation=row["generation"])
        return
    fulltext = "\n".join(f"\n[PDF 第 {i + 1} 页]\n{text.strip() or '[本页未提取到文本，可能为空白页、图像页或扫描页]'}" for i, text in enumerate(page_texts))
    artifact = {**parsed, "source_sha": paper["sha256"], "parser_version": PARSER_VERSION, "provider": parsed.get("provider"), "pages": [{**parsed["pages"][i], "page": i + 1, "text": text} for i, text in enumerate(page_texts)], "fulltext": fulltext, "coverage": coverage}
    directory = core.DATA_DIR / "cache" / "parsed"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (paper["sha256"] + ".json")
    fd, temp = tempfile.mkstemp(prefix=".parse-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(artifact, handle, ensure_ascii=False)
        with _LOCK:
            if _cancelled(row["id"], row["generation"]):
                raise safe_download.DownloadCancelled()
            os.replace(temp, path)
            with core.db() as con:
                con.execute("INSERT OR REPLACE INTO parse_artifacts(source_sha,parser_version,filename,coverage_json,created_at) VALUES(?,?,?,?,?)", (paper["sha256"], PARSER_VERSION, path.relative_to(core.DATA_DIR).as_posix(), json.dumps(coverage), core.now()))
            _update_step(step["id"], status="succeeded", progress=coverage, expected_generation=row["generation"])
    finally:
        Path(temp).unlink(missing_ok=True)


def _claim() -> tuple[dict, dict] | None:
    with _LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = con.execute("SELECT s.*,i.owner_id AS task_owner FROM import_steps s JOIN imports i ON i.id=s.import_id WHERE s.status='queued' AND i.status IN ('queued','running') ORDER BY s.rowid").fetchall()
        for value in rows:
            step = dict(value)
            dependencies = json.loads(step["dependencies_json"])
            if any(con.execute("SELECT status FROM import_steps WHERE id=?", (dep,)).fetchone()["status"] != "succeeded" for dep in dependencies):
                continue
            # Already-enqueued model jobs are polled, not executed in this worker.
            if step["job_id"]:
                try:
                    with auth.as_user(step["task_owner"]):
                        job = jobs.get_job(step["job_id"])
                except HTTPException:
                    con.execute("UPDATE import_steps SET status='failed',error='关联任务已不可访问，请检查项目权限',updated_at=? WHERE id=?", (core.now(), step["id"]))
                    continue
                if job["status"] in ACTIVE or job["status"] == "succeeded":
                    if json.loads(step["progress_json"]).get("retry_requested"):
                        con.execute("UPDATE import_steps SET status=?,progress_json='{}',error=NULL,updated_at=? WHERE id=?", (job["status"], core.now(), step["id"]))
                    continue
            con.execute("UPDATE import_steps SET status='running',attempt=attempt+1,updated_at=? WHERE id=?", (core.now(), step["id"]))
            con.execute("UPDATE imports SET status='running',updated_at=? WHERE id=?", (core.now(), step["import_id"]))
            row = dict(con.execute("SELECT * FROM imports WHERE id=?", (step["import_id"],)).fetchone())
            return row, step
    return None


def run_once() -> bool:
    """Advance at most one download, parse or model-enqueue step, never a model call."""
    with core.db() as con:
        rows = [dict(r) for r in con.execute("SELECT id,owner_id FROM imports WHERE status IN ('queued','running')")]
    for current in rows:
        with auth.as_user(current["owner_id"]):
            try:
                _refresh(current["id"])
            except HTTPException:
                with core.db() as con:
                    con.execute("UPDATE imports SET status='failed',error='导入任务已不可访问，请检查账号与项目权限',updated_at=? WHERE id=?", (core.now(), current["id"]))
    if _STOP.is_set():
        return False
    claimed = _claim()
    if claimed is None:
        return False
    row, step = claimed
    with auth.as_user(row["owner_id"]):
        return _advance(row, step)


def _advance(row: dict, step: dict) -> bool:
    try:
        core.get_project(row["project_id"], write=True)
        if _cancelled(row["id"], row["generation"]):
            raise safe_download.DownloadCancelled()
        if step["kind"] == "download":
            _download(row, step)
        elif step["kind"] == "parse":
            _parse(row, step)
        else:
            with _LOCK:
                if _cancelled(row["id"], row["generation"]):
                    raise safe_download.DownloadCancelled()
                job = jobs.retry(step["job_id"]) if step["job_id"] else jobs.enqueue(row["paper_id"], row["project_id"], step["kind"], step["mode"] or "cards")
                _update_step(step["id"], status=job["status"], job_id=job["id"], owns_job=not job.get("reused", False), progress={}, expected_generation=row["generation"])
    except safe_download.DownloadCancelled:
        _update_step(step["id"], status="interrupted" if _STOP.is_set() else "cancelled", error="导入服务停止，请手动重试" if _STOP.is_set() else None, expected_generation=row["generation"])
    except HTTPException as exc:
        _update_step(step["id"], status="blocked" if exc.status_code == 409 else "failed", error=str(exc.detail), expected_generation=row["generation"])
    except safe_download.DownloadError as exc:
        _update_step(step["id"], status="failed", error=str(exc), expected_generation=row["generation"])
    except Exception as exc:
        from .ai import Cancelled
        if isinstance(exc, Cancelled):
            _update_step(step["id"], status="interrupted" if _STOP.is_set() else "cancelled", expected_generation=row["generation"])
            _refresh(row["id"])
            return True
        _update_step(step["id"], status="failed", error="导入处理失败；请检查公开 PDF 链接或服务配置后重试", expected_generation=row["generation"])
    _refresh(row["id"])
    return True


def _loop():
    while not _STOP.is_set():
        try:
            advanced = run_once()
        except Exception:
            _LOG.exception('Import worker iteration failed; queue will remain available')
            advanced = False
        if not advanced:
            _WAKE.wait(timeout=1)
            _WAKE.clear()


def start_worker():
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        with core.db() as con:
            con.execute("UPDATE import_steps SET status='interrupted',error='服务在下载或解析时停止，请手动重试',updated_at=? WHERE status='running' AND job_id IS NULL", (core.now(),))
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, name="paperstudio-imports", daemon=True)
        _THREAD.start()


def stop_worker():
    _STOP.set()
    _WAKE.set()
    if _THREAD is not None:
        _THREAD.join(timeout=2)
    with _LOCK, core.db() as con:
        con.execute("UPDATE import_steps SET status='interrupted',error='导入服务停止，请手动重试',updated_at=? WHERE status='running' AND job_id IS NULL", (core.now(),))


