"""On-demand reference PDFs, kept outside the user's project paper list.

Only references extracted and verified in a completed lineage task may start
this flow. Assets are scoped to an owner and project; source bytes are immutable.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata

from fastapi import HTTPException

from . import ai, auth, core, evidence, pdf, reading, safe_download
from .source_resolver import resolve_source


def init_tables():
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS reference_assets (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
            title TEXT NOT NULL, source_url TEXT NOT NULL, resolved_url TEXT NOT NULL,
            filename TEXT NOT NULL, sha256 TEXT NOT NULL, page_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(owner_id,project_id,source_url,sha256));
        CREATE INDEX IF NOT EXISTS reference_asset_owner ON reference_assets(owner_id,project_id,source_url);
        """)


def _title_key(value):
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def _local_paper(reference: dict, project_id: str):
    key = _title_key(reference["reference"])
    with core.db() as con:
        rows = con.execute("SELECT p.* FROM papers p JOIN project_papers pp ON pp.paper_id=p.id WHERE pp.project_id=?", (project_id,)).fetchall()
    matches = [dict(row) for row in rows if len(_title_key(row["title"])) >= 15 and _title_key(row["title"]) in key]
    if len(matches) == 1:
        return core.get_paper(matches[0]["id"])
    return None


def _link(reference: dict):
    text = reference["reference"]
    arxiv = set(re.findall(r"(?:arxiv\s*:?\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)", text, re.I))
    if len(arxiv) > 1:
        raise HTTPException(422, "这条引用含有多个论文编号，无法唯一定位；请重新生成或选择单篇参考文献")
    if arxiv:
        return resolve_source("https://arxiv.org/abs/" + next(iter(arxiv)))
    # URL must literally occur in the verified bibliography excerpt. Never use
    # a model-created paper_url or arbitrary client URL to launch a download.
    links = {}
    for url in re.findall(r"https?://[^\s<>]+", text):
        url = url.rstrip('.,;)]}"')
        try:
            resolved = resolve_source(url)
        except HTTPException:
            continue
        if resolved["kind"] != "pdf-url" or re.search(r"\.pdf(?:$|\?)", resolved["download_url"], re.I):
            links[resolved["canonical_url"]] = resolved
    if len(links) == 1:
        return next(iter(links.values()))
    if len(links) > 1:
        raise HTTPException(422, "这条引用含有多个独立全文链接，暂不自动选择；请缩小到单篇参考文献")
    # A catalog hit is accepted only when its complete normalized title occurs
    # in the authentic reference. This avoids weak fuzzy matches and shared
    # conference/schedule URLs being mistaken for paper full text.
    ref_key = _title_key(text)
    with core.db() as con:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_papers'").fetchone()
        rows = con.execute("SELECT title,data FROM catalog_papers").fetchall() if exists else []
    matches = [json.loads(row["data"]) for row in rows if len(_title_key(row["title"])) >= 15 and _title_key(row["title"]) in ref_key]
    if len(matches) == 1:
        item = matches[0]
        for url in (item.get("pdf_url"), item.get("url")):
            if not url:
                continue
            try:
                resolved = resolve_source(url)
            except HTTPException:
                continue
            if resolved["kind"] != "pdf-url" or re.search(r"\.pdf(?:$|\?)", resolved["download_url"], re.I):
                return resolved
    raise HTTPException(422, "这条参考文献尚未定位到可核对的独立论文全文。请用标题查找公开 PDF 或将目标论文导入项目后再展开；DOI/会议日程不能当作论文全文。")


def prepare(reference: dict, project_id: str, cancelled, progress):
    project_id = core.get_project(project_id)["id"]
    local = _local_paper(reference, project_id)
    if local:
        return local, None, {"paper_id": local["id"], "asset_id": None, "title": local["title"],
                             "url": f"/read/{local['id']}", "added_to_project": True, "source": "project"}
    source = _link(reference)
    owner = auth.current_user_id()
    with core.db() as con:
        cached = con.execute("SELECT * FROM reference_assets WHERE owner_id=? AND project_id=? AND source_url=? ORDER BY created_at DESC LIMIT 1", (owner, project_id, source["canonical_url"])).fetchone()
    asset = dict(cached) if cached else None
    if asset:
        try:
            asset_path(asset)
        except HTTPException:
            asset = None
    if not asset:
        progress("按需获取这篇前序论文的原文", {"reference_url": source["canonical_url"], "scope": "reference"})
        folder = core.DATA_DIR / "reference-assets" / evidence.digest([owner, project_id])[:32]
        try:
            download = safe_download.download_pdf(source["download_url"], folder, cancelled=cancelled)
        except safe_download.DownloadCancelled:
            raise ai.Cancelled() from None
        except safe_download.DownloadError as exc:
            raise ai.ExecutionError(str(exc)) from None
        try:
            if cancelled():
                raise ai.Cancelled()
            content = download["path"].read_bytes()
            info = pdf.inspect_pdf(content)
            sha = hashlib.sha256(content).hexdigest()
            target = folder / (sha + ".pdf")
            if not target.exists():
                os.replace(download["path"], target)
            identifier = core.uid()
            title = info.get("title") or reference["label"]
            with core.db() as con:
                con.execute("""INSERT OR IGNORE INTO reference_assets(id,owner_id,project_id,title,source_url,resolved_url,filename,sha256,page_count,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (identifier, owner, project_id, title[:500], source["canonical_url"], download["resolved_url"],
                             target.relative_to(core.DATA_DIR).as_posix(), sha, info["page_count"], core.now()))
                asset = dict(con.execute("SELECT * FROM reference_assets WHERE owner_id=? AND project_id=? AND source_url=? AND sha256=?", (owner, project_id, source["canonical_url"], sha)).fetchone())
        finally:
            download["path"].unlink(missing_ok=True)
    document = dict(asset) | {"id": "reference-" + asset["id"], "active_translation_run_id": None,
                            "_parse_cache_namespace": f"reference:{owner}:{project_id}"}
    return document, asset, {"asset_id": asset["id"], "paper_id": None, "title": asset["title"],
                            "url": asset["source_url"], "source_sha": asset["sha256"], "observed_at": asset["created_at"],
                            "added_to_project": False, "source": "temporary-reference"}


def collect(reference: dict, project_id: str, job_id: str, cancelled, progress):
    paper, asset, material = prepare(reference, project_id, cancelled, progress)
    from . import parsing
    parsed = parsing.ensure_parsed(paper, cancelled=cancelled)
    if parsed.get("source_sha") != paper["sha256"]:
        raise ai.ExecutionError("前序论文解析版本不一致")
    sources, empty = [], []
    for page in range(1, paper["page_count"] + 1):
        if cancelled():
            raise ai.Cancelled()
        blocks = reading._extract(paper, page)
        if not blocks:
            empty.append(page)
        for block in blocks:
            for index, part in enumerate(evidence.chunks(block["text"])):
                source = evidence._paper_source(paper, project_id, page, block, part, index)
                if asset:
                    source.update(type="reference", paper_id=None, reference_asset_id=asset["id"],
                                  reference_job_id=job_id, visibility="private", owner_id=asset["owner_id"])
                sources.append(source)
    if asset:
        with core.db() as con:
            row = con.execute("SELECT payload FROM research_jobs WHERE id=?", (job_id,)).fetchone()
            payload = json.loads(row["payload"])
            payload["reference_asset_id"] = asset["id"]
            con.execute("UPDATE research_jobs SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), job_id))
    coverage = {"scope": "reference", "total_papers": 1, "scanned_papers": 1,
                "total_pages": paper["page_count"], "scanned_pages": paper["page_count"],
                "empty_text_pages": empty, "failed_pages": [], "available_chunks": len(sources),
                "source_types": {"reference" if asset else "paper": len(sources)},
                "context_fingerprint": evidence.digest([paper["sha256"], reference, auth.current_user_id(), project_id]),
                "reference_source": material, "visual_evidence_verified": False,
                "reference_added_to_project": False if asset else True}
    return sources, coverage, material


def asset_path(asset: dict) -> Path:
    path = (core.DATA_DIR / asset["filename"]).resolve()
    base = (core.DATA_DIR / "reference-assets").resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "前序原文文件不可用")
    return path


def require_asset(asset_id: str):
    with core.db() as con:
        row = con.execute("SELECT * FROM reference_assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "前序原文不存在")
    asset = dict(row)
    core.get_project(asset["project_id"])
    if asset["owner_id"] != auth.current_user_id():
        # Do not recurse through research._access, which itself validates these
        # sources. A reference answer has only this public-paper source; explicit
        # sharing of that completed answer grants access to the associated bytes.
        with core.db() as con:
            shared = con.execute("""SELECT 1 FROM research_jobs WHERE project_id=? AND visibility='shared'
                                    AND status='succeeded' AND json_extract(payload,'$.reference_asset_id')=? LIMIT 1""",
                                 (asset["project_id"], asset_id)).fetchone()
        if not shared:
            raise HTTPException(404, "前序原文不存在")
    return asset


def import_asset(asset_id: str, project_id: str):
    asset = require_asset(asset_id)
    project = core.get_project(project_id, write=True)
    # The caller has explicitly chosen to add a material they can already read.
    content = asset_path(asset).read_bytes()
    return core.add_paper(content, project["id"], asset["title"], "reference.pdf")

