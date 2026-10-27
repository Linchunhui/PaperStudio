"""SQLite-backed single worker. Durable queue, honest restart and cancellation states."""
from __future__ import annotations

import hashlib
import json
import logging
import threading

from fastapi import HTTPException

from . import ai, core, translations, auth

logger = logging.getLogger(__name__)

TRANSLATION_PIPELINE_VERSION = "page-pdf-paragraph-tracking-clean-v2"

_STOP = threading.Event()
_WAKE = threading.Event()
_STATE_LOCK = threading.RLock()
_THREAD: threading.Thread | None = None
_THREADS: dict[str, threading.Thread] = {}
KINDS = set(ai.KIND_SKILL)
MODES = {"abstract", "cards", "summary", "all"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def init_jobs():
    with core.db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, project_id TEXT NOT NULL,
            kind TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
            stage TEXT NOT NULL, error TEXT, result_markdown TEXT,
            payload TEXT NOT NULL, dedupe_key TEXT NOT NULL,
            coverage TEXT NOT NULL DEFAULT '{}', attempt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            owner_id TEXT NOT NULL DEFAULT 'local',
            FOREIGN KEY(paper_id) REFERENCES papers(id),
            FOREIGN KEY(project_id) REFERENCES projects(id))""")
        if "visibility" not in {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}:
            con.execute("ALTER TABLE jobs ADD COLUMN visibility TEXT NOT NULL DEFAULT 'private'")
        con.execute("CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe_key,status)")
        con.execute("CREATE INDEX IF NOT EXISTS jobs_scope ON jobs(paper_id,project_id,created_at)")


def _shared_sources_valid(row):
    try:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict) or not isinstance(payload.get("annotations", []), list):
            return False
        notes = payload.get("annotations", []) + ([payload["annotation"]] if payload.get("annotation") else [])
        if any(not isinstance(note, dict) or not note.get("id") for note in notes):
            return False
    except (ValueError, TypeError):
        return False
    with core.db() as con:
        for note in notes:
            current = con.execute("SELECT * FROM annotations WHERE id=? AND project_id=?", (note["id"], row["project_id"])).fetchone()
            if not current or dict(current).get("visibility") != "shared":
                return False
            if any(current[k] != note.get(k) for k in ("note", "quote", "source_sha")):
                return False
    return True


def can_view(row):
    row = dict(row)
    try:
        core.require_membership(row["paper_id"], row["project_id"])
        return row["owner_id"] == auth.current_user_id() or (row.get("visibility") == "shared" and _shared_sources_valid(row))
    except HTTPException:
        return False


def _row(job_id: str, *, owner_only=False) -> dict:
    with core.db() as con:
        value = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not value or not can_view(value) or (owner_only and value["owner_id"] != auth.current_user_id()):
        raise HTTPException(404, "任务不存在或无权访问")
    return dict(value)


def set_visibility(job_id: str, visibility: str):
    row = _row(job_id, owner_only=True)
    if visibility not in {"private", "shared"}:
        raise HTTPException(422, "共享范围无效")
    if visibility == "shared":
        core.get_project(row["project_id"], write=True)
        if not _shared_sources_valid(row):
            raise HTTPException(409, "结果包含尚未共享或已经修改的私人笔记，不能共享该结果。请先共享对应笔记并重新生成。")
    with core.db() as con:
        con.execute("UPDATE jobs SET visibility=?,updated_at=? WHERE id=? AND owner_id=?", (visibility, core.now(), job_id, auth.current_user_id()))
    return get_job(job_id)


def public_job(row: dict) -> dict:
    result = {key: value for key, value in row.items() if key not in {"payload", "dedupe_key", "owner_id"}}
    result["author_id"] = row.get("owner_id")
    result["can_edit"] = row.get("owner_id") == auth.current_user_id()
    result["can_share"] = False
    if result["can_edit"]:
        try:
            core.get_project(row["project_id"], write=True)
            result["can_share"] = _shared_sources_valid(row)
        except HTTPException:
            pass
    try:
        result["coverage"] = json.loads(row.get("coverage") or "{}")
        if not isinstance(result["coverage"], dict):
            result["coverage"] = {}
    except (ValueError, TypeError):
        result["coverage"] = {}
    try:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, TypeError):
        payload = {}
    skill = payload.get("skill") if isinstance(payload.get("skill"), dict) else {}
    result["source_sha"] = payload.get("source_sha")
    result["skill_id"] = skill.get("id")
    result["skill_version"] = skill.get("sha256")
    result["translation_run_id"] = payload.get("translation_run_id")
    if payload.get("kind") == "paragraph-translate":
        block = payload.get("paragraph") if isinstance(payload.get("paragraph"), dict) else {}
        provider = payload.get("provider") if isinstance(payload.get("provider"), dict) else {}
        result["page"] = block.get("page")
        result["block_id"] = block.get("id")
        result["skill_adapter"] = skill.get("adapter")
        result["translation_model"] = provider.get("model")
        result["selection_scope"] = block.get("scope") == "selection"
        if result["selection_scope"]:
            result["selection_text"] = block.get("text", "")
    return result


def get_job(job_id: str) -> dict:
    return public_job(_row(job_id))


def list_jobs(paper_id: str | None = None, project_id: str | None = None) -> list[dict]:
    projects = core.visible_project_ids()
    if not projects:
        return []
    query = "SELECT * FROM jobs WHERE (owner_id=? OR visibility='shared') AND project_id IN (" + ",".join("?" for _ in projects) + ")"
    args = [auth.current_user_id(), *projects]
    if paper_id:
        core.get_paper(paper_id)
        query += " AND paper_id=?"
        args.append(paper_id)
    if project_id:
        project_id = core.get_project(project_id)["id"]
        query += " AND project_id=?"
        args.append(project_id)
    with core.db() as con:
        rows = con.execute(query + " ORDER BY created_at DESC, rowid DESC LIMIT 250", args).fetchall()
    return [public_job(dict(row)) for row in rows if can_view(row)]


def _membership(paper_id: str, project_id: str):
    return core.require_membership(paper_id, project_id)


def _configuration(kind: str):
    try:
        cfg = ai.settings()
        if kind == "translate" and not ai.translation_executable():
            raise ai.ConfigurationError("未找到 BabelDOC。安装可选依赖并设置 BABELDOC_BIN，或先上传单语译文 PDF。")
        return cfg
    except ai.ConfigurationError as exc:
        raise HTTPException(409, str(exc)) from None


def enqueue(paper_id: str, project_id: str, kind: str, mode: str = "cards", annotation_id: str | None = None, force: bool = False,
            page: int | None = None, block_id: str | None = None, source_sha: str | None = None,
            selected_text: str | None = None, selected_rects: list[dict] | None = None) -> dict:
    if kind not in KINDS or mode not in MODES:
        raise HTTPException(422, "未知任务类型或输出模式")
    if force and kind != "translate":
        raise HTTPException(422, "只有翻译任务支持显式重新生成")
    project_id = core.get_project(project_id)["id"]
    _membership(paper_id, project_id)
    if kind == "translate":
        core.require_membership(paper_id, project_id, write=True)
        core.get_paper(paper_id, write=True)
    cfg = _configuration(kind)
    paper = core.get_paper(paper_id)
    try:
        skill = ai.skill_snapshot(ai.KIND_SKILL[kind])
    except ai.ConfigurationError as exc:
        raise HTTPException(409, str(exc)) from None
    payload = {"schema_version": 1, "paper_id": paper_id, "project_id": project_id, "paper_title": paper["title"], "source_sha": paper["sha256"], "page_count": paper["page_count"], "kind": kind, "mode": mode, "skill": skill, "provider": {"base_url": cfg["base_url"], "model": cfg["model"]}, "input_limits": {"max_input_chars": cfg["max_input_chars"], "max_output_tokens": cfg["max_output_tokens"]}}
    if kind == "paragraph-translate":
        from . import reading
        payload["paragraph"] = (reading.selection_snapshot(paper, page, source_sha, selected_text, selected_rects or [])
                                if selected_text is not None else reading.paragraph_snapshot(paper, page, block_id, source_sha))
        payload["paragraph_prompt_version"] = reading.PROMPT_VERSION
        payload["paragraph_translation_prompt"] = reading.TRANSLATION_SYSTEM
        # The paragraph adapter runs a text-specific role, not the PDF layout
        # instructions. Report the hash of the exact prompt actually executed.
        payload["skill"] = {"id": "paper-translate", "adapter": "paragraph-text",
                            "prompt": reading.TRANSLATION_SYSTEM,
                            "sha256": hashlib.sha256(reading.TRANSLATION_SYSTEM.encode()).hexdigest()}
    if kind == "translate":
        # None is an explicit baseline: a first translation must not overwrite
        # a manually imported PDF that appeared after this job was enqueued.
        payload["translation_base_sha"] = paper["translated_sha256"]
        payload["translation_base_run_id"] = paper.get("active_translation_run_id")
        payload["start_page"] = core.require_membership(paper_id, project_id)["last_page"]
        # Upgraded translations need new tracking metadata; do not silently
        # reuse a completed page-only job created by the previous pipeline.
        payload["translation_pipeline_version"] = TRANSLATION_PIPELINE_VERSION
    if kind in {"selection-summary", "notes-summary"}:
        notes = core.annotation_list(paper_id, project_id)
        if kind == "selection-summary":
            note = next((n for n in notes if n["id"] == annotation_id), None)
            if note is None:
                raise HTTPException(404, "当前项目中没有这条批注")
            payload["annotation"] = note
        else:
            if not notes:
                raise HTTPException(409, "当前项目还没有笔记。先划线或记录想法，再生成读后笔记总结。")
            payload["annotations"] = sorted(notes, key=lambda n: (n["page"], n["created_at"], n["id"]))
    identity = {k: v for k, v in payload.items() if k not in {"translation_base_run_id", "start_page"}}
    text = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode()).hexdigest()
    with _STATE_LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        candidates = con.execute("SELECT * FROM jobs WHERE dedupe_key=? AND status IN ('queued','running','succeeded') AND owner_id=? ORDER BY created_at DESC", (digest, auth.current_user_id())).fetchall()
        current = con.execute("SELECT active_translation_run_id FROM papers WHERE id=?", (paper_id,)).fetchone()
        old = next((r for r in candidates if kind != "translate" or (current["active_translation_run_id"] and json.loads(r["payload"]).get("translation_run_id") == current["active_translation_run_id"])), None)
        # Explicit regeneration bypasses completed translations, for example
        # after enabling tracking in BabelDOC. Repeated clicks still coalesce
        # into the already active job instead of starting duplicate model work.
        if old and (not force or old["status"] in {"queued", "running"}):
            result = public_job(dict(old))
            result["reused"] = True
            return result
        job_id, now = core.uid(), core.now()
        if kind == "translate":
            payload["translation_run_id"] = core.uid()
            payload["schema_version"] = 2
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        con.execute("INSERT INTO jobs(id,paper_id,project_id,kind,mode,status,stage,payload,dedupe_key,created_at,updated_at,owner_id) VALUES(?,?,?,?,?,'queued','等待执行',?,?,?,?,?)", (job_id, paper_id, project_id, kind, mode, text, digest, now, now, auth.current_user_id()))
        if kind == "translate":
            translations.create_run(con, payload, job_id, payload["translation_run_id"])
    _WAKE.set()
    return get_job(job_id)


def cancel(job_id: str) -> dict:
    _row(job_id, owner_only=True)
    with _STATE_LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        changed = con.execute("UPDATE jobs SET status='cancelled',stage='已取消',updated_at=? WHERE id=? AND owner_id=? AND status IN ('queued','running')", (core.now(), job_id, auth.current_user_id())).rowcount
        if changed:
            translations.stop_run(con, job_id, "cancelled")
    return get_job(job_id)


def retry(job_id: str) -> dict:
    row = _row(job_id, owner_only=True)
    if row["status"] not in {"failed", "cancelled", "interrupted"}:
        raise HTTPException(409, "只有失败、中断或已取消任务可以重试")
    _membership(row["paper_id"], row["project_id"])
    if row["kind"] == "translate":
        core.require_membership(row["paper_id"], row["project_id"], write=True)
        core.get_paper(row["paper_id"], write=True)
    cfg = _configuration(row["kind"])
    try:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict) or not {"source_sha", "provider", "input_limits"} <= payload.keys():
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(409, "任务输入快照损坏，请重新提交。") from None
    paper = core.get_paper(row["paper_id"])
    if paper["sha256"] != payload["source_sha"]:
        raise HTTPException(409, "原文版本已变化，请新建任务")
    if row["kind"] == "translate":
        _check_translation_baseline(payload, paper)
        translations.validate_run(payload["translation_run_id"], job_id, running=False)
    # A retry is the same immutable material/skill snapshot. Changed model or
    # context budgets should be submitted as a new job, not change history.
    if payload["provider"] != {"base_url": cfg["base_url"], "model": cfg["model"]} or payload["input_limits"] != {"max_input_chars": cfg["max_input_chars"], "max_output_tokens": cfg["max_output_tokens"]}:
        raise HTTPException(409, "模型或上下文配置已变化，请新建任务以保存新的执行配置；原任务快照保留。")
    with _STATE_LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        current = con.execute("SELECT * FROM jobs WHERE id=? AND owner_id=?", (job_id, auth.current_user_id())).fetchone()
        if current["status"] not in {"failed", "cancelled", "interrupted"}:
            if current["status"] in {"queued", "running"}:
                result = public_job(dict(current))
                result["reused"] = True
                return result
            raise HTTPException(409, "任务状态已变化，请刷新后再操作")
        duplicate = con.execute("SELECT * FROM jobs WHERE dedupe_key=? AND id<>? AND status IN ('queued','running','succeeded') AND owner_id=? LIMIT 1", (row["dedupe_key"], job_id, auth.current_user_id())).fetchone()
        if duplicate and row["kind"] == "translate" and json.loads(duplicate["payload"]).get("translation_run_id") != paper.get("active_translation_run_id"):
            duplicate = None
        if duplicate:
            result = public_job(dict(duplicate))
            result["reused"] = True
            return result
        if row["kind"] == "translate":
            translations.resume_run(con, payload["translation_run_id"], job_id)
        con.execute("UPDATE jobs SET status='queued',stage='等待重试',error=NULL,result_markdown=NULL,coverage='{}',updated_at=? WHERE id=? AND status IN ('failed','cancelled','interrupted')", (core.now(), job_id))
    _WAKE.set()
    return get_job(job_id)


def _cancelled(job_id: str) -> bool:
    return _STOP.is_set() or _row(job_id)["status"] != "running"


def _progress(job_id: str, stage: str, coverage: dict):
    with core.db() as con:
        con.execute("UPDATE jobs SET stage=?,coverage=?,updated_at=? WHERE id=? AND status='running'", (stage, json.dumps(coverage, ensure_ascii=False), core.now(), job_id))


def _claim(*, paragraphs_only: bool = False, channel: str | None = None) -> dict | None:
    with _STATE_LOCK, core.db() as con:
        con.execute("BEGIN IMMEDIATE")
        condition = " AND kind='paragraph-translate'" if paragraphs_only or channel == "paragraph" else (" AND kind='translate'" if channel == "translation" else (" AND kind NOT IN ('translate','paragraph-translate')" if channel == "text" else ""))
        row = con.execute("SELECT * FROM jobs WHERE status='queued'" + condition + " ORDER BY CASE WHEN kind='paragraph-translate' THEN 0 ELSE 1 END,created_at,rowid LIMIT 1").fetchone()
        if not row:
            return None
        con.execute("UPDATE jobs SET status='running',stage='准备输入证据',attempt=attempt+1,updated_at=? WHERE id=?", (core.now(), row["id"]))
        return dict(row)


def _check_translation_baseline(payload: dict, paper: dict):
    if "translation_base_sha" not in payload:
        raise HTTPException(409, "旧翻译任务没有译文版本快照，请重新提交翻译任务。")
    if paper["translated_sha256"] != payload["translation_base_sha"]:
        raise HTTPException(409, "译文已在任务提交后更新，保留现有译文；请确认后重新提交翻译任务。")
    if not payload.get("translation_run_id"):
        raise HTTPException(409, "旧翻译任务没有逐页版本快照，请重新提交翻译任务。")
    if paper.get("active_translation_run_id") != payload["translation_run_id"]:
        raise HTTPException(409, "译文已在任务提交后更新，保留现有译文；请确认后重新提交翻译任务。")


def run_one(*, paragraphs_only: bool = False, channel: str | None = None) -> bool:
    """Execute one queued job; also used by deterministic integration tests."""
    row = _claim(paragraphs_only=paragraphs_only, channel=channel)
    if row is None:
        return False
    with auth.as_user(row["owner_id"]):
        return _execute(row)


def _execute(row):
    job_id = row["id"]
    try:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise ai.ExecutionError("任务输入快照损坏，请重新提交。")
        if core.get_paper(payload["paper_id"])["sha256"] != payload["source_sha"]:
            raise ai.ExecutionError("原文版本已变化，未执行旧输入任务；请新建处理任务。")
        core.require_membership(row["paper_id"], row["project_id"], write=row["kind"] == "translate")
        if row["kind"] == "translate":
            core.get_paper(row["paper_id"], write=True)
        cfg = ai.settings()
        if payload["provider"] != {"base_url": cfg["base_url"], "model": cfg["model"]} or payload["input_limits"] != {"max_input_chars": cfg["max_input_chars"], "max_output_tokens": cfg["max_output_tokens"]}:
            raise ai.ExecutionError("排队后模型或上下文配置发生变化，未执行。请重新提交以固定新的执行配置。")
        if _cancelled(job_id):
            raise ai.Cancelled()
        progress = lambda stage, coverage: _progress(job_id, stage, coverage)
        if row["kind"] == "translate":
            # Avoid spending a provider call on an already obsolete task. The
            # final attach repeats this check atomically for updates made while
            # BabelDOC is running.
            _check_translation_baseline(payload, core.get_paper(payload["paper_id"]))
            _run_translation_pages(job_id, payload, progress)
        elif row["kind"] == "paragraph-translate":
            from . import reading
            answer, coverage = reading.run_translation(payload, lambda: _cancelled(job_id), progress)
            with _STATE_LOCK:
                if _cancelled(job_id):
                    raise ai.Cancelled()
                _finish(job_id, answer, coverage)
        else:
            answer, coverage = ai.run_text(payload, lambda: _cancelled(job_id), progress)
            with _STATE_LOCK:
                if _cancelled(job_id):
                    raise ai.Cancelled()
                _finish(job_id, answer, coverage)
    except (ai.Cancelled, translations.Stopped):
        with core.db() as con:
            status = "interrupted" if _STOP.is_set() else "cancelled"
            changed = con.execute("UPDATE jobs SET status=?,stage=?,updated_at=? WHERE id=? AND status='running'", (status, "服务停止，需手动重试" if _STOP.is_set() else "已取消", core.now(), job_id)).rowcount
            if changed:
                translations.stop_run(con, job_id, status)
    except (ai.ConfigurationError, ai.ExecutionError) as exc:
        _fail(job_id, str(exc))
    except HTTPException as exc:
        _fail(job_id, str(exc.detail))
    except Exception:
        # Never echo provider secrets, subprocess stdout, or filesystem details.
        _fail(job_id, "任务执行失败。输入可能无效或服务不可用；请检查服务端配置并手动重试。")
    return True


def _run_translation_pages(job_id: str, payload: dict, progress):
    run_id = payload["translation_run_id"]
    pages = translations.pending_pages(run_id, job_id, payload.get("start_page", 1))
    for page in pages:
        if _cancelled(job_id):
            raise ai.Cancelled()
        # A full book must not monopolize the only worker. Service a bounded
        # number of explicitly requested paragraphs at each page boundary.
        for _ in range(2):
            if _THREADS and any(t.is_alive() for t in _THREADS.values()):
                break
            if _cancelled(job_id) or not run_one(paragraphs_only=True):
                break
        if _cancelled(job_id):
            raise ai.Cancelled()
        translations.start_page(run_id, job_id, page)
        progress(f"正在翻译第 {page} / {payload['page_count']} 页 · 已完成页可先阅读", translations.counts(run_id))
        try:
            translated = ai.run_translation_page(payload, page, lambda: _cancelled(job_id), lambda stage, _: progress(f"第 {page} 页 · {stage}", translations.counts(run_id)))
            content = translated.content if isinstance(translated, ai.PageTranslation) else translated
            alignment = translated.alignment if isinstance(translated, ai.PageTranslation) else None
            with _STATE_LOCK:
                if _cancelled(job_id):
                    raise ai.Cancelled()
                translations.publish_page(run_id, job_id, page, content, alignment=alignment)
        except (ai.Cancelled, translations.Stopped):
            raise
        except (ai.ConfigurationError, ai.ExecutionError, HTTPException) as exc:
            if _cancelled(job_id):
                raise ai.Cancelled()
            # The active-run check inside fail_page propagates manual overrides;
            # a bad individual output only fails its page and the loop continues.
            message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            translations.fail_page(run_id, job_id, page, message)
        except Exception:
            if _cancelled(job_id):
                raise ai.Cancelled()
            translations.fail_page(run_id, job_id, page, "这一页翻译失败；其他页会继续，完成后可重试失败页。")
        progress(f"已就绪 {translations.counts(run_id)['ready_pages']} / {payload['page_count']} 页", translations.counts(run_id))
    with _STATE_LOCK:
        if _cancelled(job_id):
            raise ai.Cancelled()
        coverage = translations.finish_run(run_id, job_id)
        if coverage["ready_pages"] != coverage["total_pages"]:
            _progress(job_id, "部分页面未完成 · 已完成页可阅读", coverage)
            _fail(job_id, f"{coverage['failed_pages']} 页翻译失败；已完成 {coverage['ready_pages']} 页保留。重试只处理未就绪页面。")
        else:
            _finish(job_id, "译文页面已全部生成，并可下载合并后的单语 PDF。页面完成即开放矢量阅读；已有批注不因后续页面完成而失效。\n\n本版逐页独立翻译，跨页段落与术语一致性可能弱于整文处理；请核对术语、公式与版式。", coverage)


def _finish(job_id: str, answer: str, coverage: dict):
    with core.db() as con:
        con.execute("UPDATE jobs SET status='succeeded',stage='已完成',error=NULL,result_markdown=?,coverage=?,updated_at=? WHERE id=? AND status='running'", (answer, json.dumps(coverage, ensure_ascii=False), core.now(), job_id))


def _fail(job_id: str, message: str):
    with _STATE_LOCK, core.db() as con:
        changed = con.execute("UPDATE jobs SET status='failed',stage='执行失败',error=?,updated_at=? WHERE id=? AND status='running'", (message, core.now(), job_id)).rowcount
        # Keep completed page assets readable. Superseded runs remain superseded.
        if changed:
            con.execute("UPDATE translation_runs SET status='failed',updated_at=? WHERE job_id=? AND status IN ('queued','running')", (core.now(), job_id))


def _loop(channel):
    while not _STOP.is_set():
        try:
            if not run_one(channel=channel):
                _WAKE.wait(timeout=.5)
                _WAKE.clear()
        except Exception:
            # Keep a transient database or legacy-row error from killing a
            # queue. Never include material, provider responses or secrets.
            logger.exception("Background %s worker iteration failed", channel)
            _STOP.wait(.5)


def start_worker():
    global _THREAD
    with _STATE_LOCK:
        if any(thread.is_alive() for thread in _THREADS.values()):
            return
        with core.db() as con:
            interrupted = con.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            con.execute("UPDATE jobs SET status='interrupted',stage='上次服务中断，需手动重试',error='服务在执行中停止。没有自动重复外部模型调用。',updated_at=? WHERE status='running'", (core.now(),))
            for row in interrupted:
                translations.stop_run(con, row["id"], "interrupted")
        _STOP.clear()
        _THREADS.clear()
        for channel in ("text", "translation", "paragraph"):
            thread = threading.Thread(target=_loop, args=(channel,), daemon=True, name="paperstudio-" + channel)
            _THREADS[channel] = thread
            thread.start()
        _THREAD = _THREADS["text"]


def stop_worker():
    _STOP.set()
    _WAKE.set()
    for thread in list(_THREADS.values()):
        thread.join(timeout=2)
    with _STATE_LOCK, core.db() as con:
        interrupted = con.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
        con.execute("UPDATE jobs SET status='interrupted',stage='服务停止，需手动重试',updated_at=? WHERE status='running'", (core.now(),))
        for row in interrupted:
            translations.stop_run(con, row["id"], "interrupted")
