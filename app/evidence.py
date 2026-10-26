"""Located, permission-scoped source material for research answers.

Model supplied locations are never accepted. A citation is a server-issued
source ID plus a verbatim excerpt which is checked against that source.
"""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import json
import math
import re
import sqlite3
from urllib.parse import urlencode

import fitz
from fastapi import HTTPException

from . import ai, core, pdf, reading

CHUNK_CHARS = 2400
VERSION = "located-evidence-v1"


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def normal(text: str) -> str:
    return " ".join(text.split())


def original_excerpt(source: str, quote: str) -> str | None:
    """Allow PDF whitespace normalization, never invented words or case changes."""
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 4000:
        return None
    escaped = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(escaped, source)
    return match.group(0) if match else None


def chunks(text: str):
    offset = 0
    while offset < len(text):
        end = min(offset + CHUNK_CHARS, len(text))
        if end < len(text):
            split = max(text.rfind(". ", offset + 600, end), text.rfind("\n", offset + 600, end))
            if split > offset:
                end = split + 1
        part = text[offset:end].strip()
        if part:
            yield part
        offset = end


def _paper_source(paper: dict, project_id: str, page: int, block: dict, text: str, part=0) -> dict:
    identifier = "p-" + digest([paper["id"], paper["sha256"], page, block["id"], part])[:24]
    return {"id": identifier, "type": "paper", "paper_id": paper["id"],
            "paper_title": paper["title"], "project_id": project_id,
            "source_sha": paper["sha256"], "page": page, "block_id": block["id"],
            "rects": block.get("rects", []), "text": text, "source_version": paper["sha256"],
            "visibility": "shared", "owner_id": paper.get("owner_id")}


def validate_rects(rects) -> list[dict]:
    if not isinstance(rects, list) or not 1 <= len(rects) <= 1000:
        raise HTTPException(422, "请选择论文中的有效区域")
    result = []
    for rect in rects:
        if not isinstance(rect, dict):
            raise HTTPException(422, "选区坐标格式无效")
        values = [rect.get(key) for key in ("x", "y", "width", "height")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise HTTPException(422, "选区坐标须为有限数值")
        x, y, width, height = values
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.001 or y + height > 1.001:
            raise HTTPException(422, "选区须位于论文页面内")
        result.append(dict(zip(("x", "y", "width", "height"), values)))
    return result


def _overlap(a, b):
    return (min(a["x"] + a["width"], b["x"] + b["width"]) > max(a["x"], b["x"])
            and min(a["y"] + a["height"], b["y"] + b["height"]) > max(a["y"], b["y"]))


def selected_sources(paper: dict, project_id: str, selection: dict, kind: str) -> list[dict]:
    if selection.get("source_sha") != paper["sha256"]:
        raise HTTPException(409, "原文版本已变化，请重新选择论文内容")
    page = selection.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= paper["page_count"]:
        raise HTTPException(422, "请选择有效的原文页码")
    blocks = reading._extract(paper, page)
    block_id, quote = selection.get("block_id"), selection.get("quote", "")
    if block_id:
        blocks = [block for block in blocks if block["id"] == block_id]
        if not blocks:
            raise HTTPException(409, "段落定位已变化，请重新选择")
    rects = validate_rects(selection["rects"]) if selection.get("rects") else []
    if kind in {"figure", "formula"} and rects:
        nearby = [b for b in blocks if any(_overlap(a, r) for a in b.get("rects", []) for r in rects)]
        text = "\n".join(b["text"] for b in nearby)
        visual = _paper_source(paper, project_id, page,
                               {"id": "region-" + digest(rects)[:16], "rects": rects}, text)
        visual["id"] = "v-" + visual["id"][2:]
        visual["type"] = "figure"
        return [visual] + [_paper_source(paper, project_id, page, b, part, index)
                           for b in nearby for index, part in enumerate(chunks(b["text"]))]
    if quote:
        # Selection can span adjacent extracted blocks, but it must still be a
        # contiguous excerpt of this page's authentic reading-order text.
        text = "\n".join(b["text"] for b in blocks)
        verified = original_excerpt(text, quote)
        if verified is None:
            raise HTTPException(422, "选中文字与原文不一致，请在原 PDF 中重新选择")
        matching = [b for b in blocks if normal(b["text"]) in normal(verified)
                    or normal(verified) in normal(b["text"])
                    or any(_overlap(a, r) for a in b.get("rects", []) for r in rects)]
        anchor = {"id": block_id or "selection-" + digest(verified)[:16],
                  "rects": [r for b in matching for r in b.get("rects", [])]}
        return [_paper_source(paper, project_id, page, anchor, part, index)
                for index, part in enumerate(chunks(verified))]
    if block_id:
        return [_paper_source(paper, project_id, page, b, part, index)
                for b in blocks for index, part in enumerate(chunks(b["text"]))]
    raise HTTPException(422, "请选择一段原文，或框选图表/公式区域")


def crop_image(source: dict) -> bytes:
    paper = core.get_paper(source["paper_id"])
    core.require_membership(paper["id"], source["project_id"])
    if paper["sha256"] != source["source_sha"]:
        raise HTTPException(409, "引用所绑定的原文版本不可用")
    rects = validate_rects(source["rects"])
    with pdf.PDF_LOCK, fitz.open(core.paper_asset_path(paper)) as doc:
        page = doc[source["page"] - 1]
        # Rasterize in the same rotated page coordinate system as the reader.
        scale = min(2.0, 2000 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        x0 = max(0, math.floor(min(r["x"] for r in rects) * pix.width))
        y0 = max(0, math.floor(min(r["y"] for r in rects) * pix.height))
        x1 = min(pix.width, math.ceil(max(r["x"] + r["width"] for r in rects) * pix.width))
        y1 = min(pix.height, math.ceil(max(r["y"] + r["height"] for r in rects) * pix.height))
        # A small output PDF clipped to the requested rectangle avoids depending
        # on Pillow and preserves both image-only and vector formulas.
        display = fitz.open()
        out = display.new_page(width=x1 - x0, height=y1 - y0)
        out.insert_image(fitz.Rect(-x0, -y0, pix.width - x0, pix.height - y0), pixmap=pix)
        content = out.get_pixmap(alpha=False).tobytes("png")
        display.close()
        return content


def image_data(source):
    return "data:image/png;base64," + base64.b64encode(crop_image(source)).decode()


def _rows_if_table(con, table: str, query: str, args=()):
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return [dict(row) for row in con.execute(query, args)]
    return []


def collect(project_id: str, paper_id: str | None, selection: dict | None, kind: str,
            cancelled=lambda: False, progress=lambda *args: None):
    """Scan all permitted project papers before selecting model context."""
    from . import auth
    project_id = core.get_project(project_id)["id"]
    user_id = auth.current_user_id()
    if paper_id:
        core.require_membership(paper_id, project_id)
        papers = [core.get_paper(paper_id)]
    else:
        with core.db() as con:
            ids = [r[0] for r in con.execute("SELECT paper_id FROM project_papers WHERE project_id=? ORDER BY paper_id", (project_id,))]
        papers = [core.get_paper(identifier) for identifier in ids]
    sources, failures, manifest = [], [], []
    total_pages = sum(p["page_count"] for p in papers)
    scanned_pages, empty_pages = 0, []
    for number, paper in enumerate(papers):
        if cancelled():
            raise ai.Cancelled()
        manifest.append({"paper_id": paper["id"], "source_sha": paper["sha256"], "pages": paper["page_count"]})
        progress(f"读取项目材料 {number + 1}/{len(papers)}", {"total_papers": len(papers), "scanned_papers": number, "total_pages": total_pages})
        # One explicit background parsing operation per PDF; page reads only
        # reuse the resulting source-bound blocks and never launch translations.
        try:
            from . import parsing
            parsed = parsing.ensure_parsed(paper, cancelled=cancelled)
            if parsed.get("source_sha") != paper["sha256"]:
                raise ai.ExecutionError("解析结果与原文版本不一致")
        except ImportError:
            pass
        if selection:
            sources.extend(selected_sources(paper, project_id, selection, kind))
            scanned_pages += 1
            continue
        in_references = False
        for page in range(1, paper["page_count"] + 1):
            if cancelled():
                raise ai.Cancelled()
            try:
                blocks = reading._extract(paper, page)
                scanned_pages += 1
                if not blocks:
                    empty_pages.append({"paper_id": paper["id"], "page": page})
                for block in blocks:
                    if re.match(r"^(?:\d+[. ]+)?(?:references|bibliography|参考文献)\b", block["text"].strip(), re.I):
                        in_references = True
                    elif in_references and re.match(r"^(?:appendix|appendices|supplementary|附录)\b", block["text"].strip(), re.I):
                        in_references = False
                    block_sources = [_paper_source(paper, project_id, page, block, part, index)
                                     for index, part in enumerate(chunks(block["text"]))]
                    for source in block_sources:
                        source["section"] = "references" if in_references else "body"
                    sources.extend(block_sources)
            except HTTPException as exc:
                failures.append({"paper_id": paper["id"], "page": page, "reason": str(exc.detail)})
        for note in core.annotation_list(paper["id"], project_id):
            if not note.get("is_current", True):
                continue
            body = note.get("note", "").strip()
            if not body:
                continue
            version = digest([note["id"], note.get("updated_at"), body, note.get("visibility", "private")])
            for index, part in enumerate(chunks(body)):
                sources.append({"id": "n-" + digest([version, index])[:24], "type": "note",
                                "paper_id": paper["id"], "paper_title": paper["title"], "project_id": project_id,
                                "source_sha": note["source_sha"], "page": note["page"], "side": note.get("side", "original"),
                                "block_id": None, "rects": note.get("rects", []), "text": part,
                                "annotation_id": note["id"], "source_version": version,
                                "visibility": note.get("visibility", "private"),
                                "owner_id": note.get("owner_id", note.get("author_id", paper.get("owner_id")))})
    if not selection:
        with core.db() as con:
            rows = _rows_if_table(con, "jobs", "SELECT * FROM jobs WHERE project_id=? AND status='succeeded'", (project_id,))
            research_rows = _rows_if_table(con, "research_jobs", "SELECT * FROM research_jobs WHERE project_id=? AND status='succeeded'", (project_id,))
        for row in rows + research_rows:
            if row.get("owner_id") != user_id and row.get("visibility", "private") != "shared":
                continue
            if paper_id and row.get("paper_id") != paper_id:
                continue
            body = row.get("result_markdown")
            origin = "jobs"
            if "result_json" not in row:
                from . import jobs
                if hasattr(jobs, "can_view") and not jobs.can_view(row):
                    continue
            if "result_json" in row:
                origin = "research_jobs"
                result = json.loads(row.get("result_json") or "{}")
                body = "\n".join(c.get("text", "") for c in result.get("claims", []))
                body += "\n" + "\n".join(c.get("description", "") for c in result.get("knowledge", []))
                # Do not feed a result whose formerly shared sources were revoked.
                try:
                    assert_sources_visible(json.loads(row.get("evidence_json") or "[]"))
                except HTTPException:
                    continue
            if not body or not body.strip():
                continue
            linked_paper = next((p for p in papers if p["id"] == row.get("paper_id")), None)
            version = digest([row["id"], body, row.get("visibility", "private")])
            for index, part in enumerate(chunks(body)):
                sources.append({"id": "g-" + digest([origin, version, index])[:24], "type": "generated",
                                "paper_id": row.get("paper_id"), "paper_title": linked_paper["title"] if linked_paper else "项目解读",
                                "project_id": project_id, "source_sha": linked_paper["sha256"] if linked_paper else None,
                                "page": None, "block_id": None, "rects": [], "text": part,
                                "generated_job_id": row["id"], "generated_table": origin,
                                "source_version": version, "visibility": row.get("visibility", "private"),
                                "owner_id": row.get("owner_id")})
    coverage = {"scope": "selection" if selection else "paper" if paper_id else "project",
                "total_papers": len(papers), "scanned_papers": len(papers), "total_pages": total_pages,
                "scanned_pages": scanned_pages, "empty_text_pages": empty_pages, "failed_pages": failures,
                "available_chunks": len(sources), "paper_manifest": manifest,
                "source_types": dict(Counter(s["type"] for s in sources)),
                "visual_evidence_verified": any(s["type"] == "figure" for s in sources)}
    coverage["context_fingerprint"] = digest([VERSION, user_id, project_id, manifest,
                                             [(s["id"], s["source_version"], s["visibility"]) for s in sources]])
    return sources, coverage


def terms(text):
    lowered = text.casefold()
    words = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", lowered)
    for run in re.findall(r"[\u3400-\u9fff]+", lowered):
        words.extend(run[i:i + 2] for i in range(len(run) - 1))
    return words


def retrieve(sources: list[dict], question: str, budget: int, kind: str):
    """BM25 over every collected chunk; bounded context has explicit coverage."""
    if not sources:
        return [], {"selected_chunks": 0, "selected_papers": 0, "omitted_paper_ids": []}
    aliases = {"方法": "method approach architecture", "实验": "experiment results evaluation",
               "数据集": "dataset benchmark data", "局限": "limitation failure discussion",
               "问题": "problem motivation challenge", "公式": "equation loss objective",
               "背景": "background related work", "脉络": "references related work prior"}
    query = question + " " + " ".join(v for k, v in aliases.items() if k in question)
    if kind == "knowledge":
        query += " abstract introduction method experiment dataset limitation"
    if kind == "lineage":
        query += " references bibliography related work previous prior"
    tokens = [Counter(terms(s["text"])) for s in sources]
    document_frequency = Counter(term for token in tokens for term in token)
    average = max(1, sum(sum(token.values()) for token in tokens) / len(tokens))
    query_terms = set(terms(query))
    scores = []
    for index, token in enumerate(tokens):
        length = sum(token.values())
        score = sum(math.log(1 + (len(sources) - document_frequency[t] + .5) / (document_frequency[t] + .5))
                    * token[t] * 2.2 / (token[t] + 1.2 * (.25 + .75 * length / average))
                    for t in query_terms if token[t])
        if sources[index]["type"] in {"paper", "reference"}:
            score *= 1.25
        if sources[index]["type"] == "figure":
            score += 1000
        if kind == "lineage" and sources[index].get("section") == "references":
            score += 4
        scores.append(score)
    ranked = sorted(range(len(sources)), key=lambda i: (-scores[i], i))
    # Broad project questions retain at least the strongest available chunk from
    # each paper when the context budget permits, then fill by relevance.
    document_key = lambda s: s.get("paper_id") or ("reference:" + s["reference_asset_id"] if s.get("reference_asset_id") else None)
    first, seen = [], set()
    for index in ranked:
        key = document_key(sources[index])
        if key not in seen and sources[index]["type"] in {"paper", "figure", "reference"}:
            first.append(index)
            seen.add(key)
    order = list(dict.fromkeys(first + ranked))
    picked, used = [], 0
    for index in order:
        cost = len(json.dumps(sources[index], ensure_ascii=False)) + 50
        if used + cost <= budget:
            picked.append(sources[index])
            used += cost
    all_papers = {document_key(s) for s in sources if document_key(s)}
    selected_papers = {document_key(s) for s in picked if document_key(s)}
    return picked, {"selected_chunks": len(picked), "selected_papers": len(selected_papers),
                    "omitted_paper_ids": sorted(all_papers - selected_papers),
                    "context_chars": used, "context_budget_chars": budget,
                    "retrieval": "BM25 over all accessible chunks; per-paper coverage before relevance fill",
                    "all_chunks_fit": len(picked) == len(sources)}


def citation(source: dict, quote: str, job_id: str) -> dict | None:
    actual = original_excerpt(source["text"], quote)
    if actual is None and not (source["type"] == "figure" and not quote):
        return None
    result = {key: value for key, value in source.items() if key not in {"text", "visibility", "owner_id"}}
    result["quote"] = actual or ""
    result["evidence_label"] = {"paper": "论文原文", "reference": "前序论文原文", "note": "用户笔记", "generated": "生成解读", "figure": "原文图像"}[source["type"]]
    if source.get("paper_id"):
        params = {"project": source["project_id"]}
        if source.get("page"):
            params["page"] = source["page"]
        result["href"] = "/read/" + source["paper_id"] + "?" + urlencode(params)
    if source["type"] == "figure":
        result["image_url"] = f"/api/research/jobs/{job_id}/evidence/{source['id']}/image"
    if source["type"] == "reference":
        result["href"] = f"/api/research/reference-assets/{source['reference_asset_id']}/file#page={source['page']}"
    return result


def assert_sources_visible(sources: list[dict], *, sharing=False, depth=0, _visited=None):
    from . import auth
    if depth > 500:
        raise HTTPException(409, "引用链超过当前安全核验容量，请重新生成仅据论文原文的结果")
    visited = set() if _visited is None else _visited
    user_id = auth.current_user_id()
    for source in sources:
        core.get_project(source["project_id"])
        if source["type"] == "reference":
            from . import reference_material
            asset = reference_material.require_asset(source["reference_asset_id"])
            if asset["sha256"] != source["source_sha"]:
                raise HTTPException(409, "前序原文版本已变化")
        if source.get("paper_id"):
            core.require_membership(source["paper_id"], source["project_id"])
        if source["type"] == "note":
            note = core.get_annotation(source["annotation_id"])
            if sharing and note.get("visibility", "private") != "shared":
                raise HTTPException(409, "结果引用了私有笔记，请先共享相关笔记，或重新生成仅据论文原文的结果")
            if (sharing or source.get("owner_id") != user_id) and source["text"] not in note.get("note", ""):
                raise HTTPException(409, "引用笔记已修改，不能使用旧内容对外共享；请重新生成结果")
        elif source["type"] == "generated":
            table = source.get("generated_table")
            if table not in {"jobs", "research_jobs"}:
                raise HTTPException(409, "生成材料来源无效")
            with core.db() as con:
                row = con.execute(f"SELECT * FROM {table} WHERE id=?", (source["generated_job_id"],)).fetchone()
            if not row:
                raise HTTPException(409, "引用的生成材料已不可用")
            row = dict(row)
            if row.get("owner_id") != user_id and row.get("visibility", "private") != "shared":
                raise HTTPException(403, "引用的生成材料当前不可见")
            if sharing and row.get("visibility", "private") != "shared":
                raise HTTPException(409, "结果引用了私有解读，请先共享该解读")
            if sharing or source.get("owner_id") != user_id:
                if table == "research_jobs":
                    current = json.loads(row.get("result_json") or "{}")
                    body = "\n".join(c.get("text", "") for c in current.get("claims", []))
                    body += "\n" + "\n".join(c.get("description", "") for c in current.get("knowledge", []))
                else:
                    body = row.get("result_markdown") or ""
                if source["text"] not in body:
                    raise HTTPException(409, "引用的解读版本已变化，请重新生成结果")
            if table == "research_jobs":
                key = (table, row["id"])
                if key not in visited:
                    visited.add(key)
                    assert_sources_visible(json.loads(row.get("evidence_json") or "[]"), sharing=sharing, depth=depth + 1, _visited=visited)
            else:
                from . import jobs
                if hasattr(jobs, "can_view") and not jobs.can_view(row):
                    raise HTTPException(403, "引用的生成材料权限已变化")
                if sharing and hasattr(jobs, "_shared_sources_valid") and not jobs._shared_sources_valid(row):
                    raise HTTPException(409, "引用的解读包含已撤回或修改的笔记，不能继续共享")

