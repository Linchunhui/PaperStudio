"""HTTP contract for the local library and anchored reading notes."""
from __future__ import annotations

import json
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import core, pdf, auth

router = APIRouter(prefix="/api")
Side = Literal["original", "translation"]
Status = Literal["unread", "reading", "read"]


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("项目名不能为空")
        return value.strip()


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("项目名不能为空")
        return value.strip() if value else value


class ProjectLink(BaseModel):
    project_id: str


class ReadingInput(ProjectLink):
    status: Status
    last_page: int | None = Field(default=None, ge=1)
    page_offset: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RectInput(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def in_page(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("选区必须在页面范围内")
        return self


class AnnotationInput(ProjectLink):
    side: Side = "original"
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=16000)
    note: str = Field(default="", max_length=50000)
    color: Literal["yellow", "green", "blue", "pink"] = "yellow"
    rects: list[RectInput] = Field(min_length=1, max_length=1000)
    source_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    translation_run_id: str | None = Field(default=None, max_length=128)
    visibility: Literal["private", "shared"] = "private"

    @field_validator("quote")
    @classmethod
    def quote_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请先选择原文词句")
        return value


class AnnotationPatch(BaseModel):
    visibility: Literal["private", "shared"] | None = None
    expected_version: int | None = Field(default=None, ge=1)
    note: str | None = Field(default=None, max_length=50000)
    color: Literal["yellow", "green", "blue", "pink"] | None = None


def _read_upload(file: UploadFile) -> bytes:
    try:
        content = file.file.read(pdf.MAX_PDF_BYTES + 1)
    finally:
        file.file.close()
    if len(content) > pdf.MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 超过 50 MB 限制")
    return content


@router.get("/health")
def health():
    return {"status": "ok", "mode": "multi-user", "storage": "sqlite"}


@router.get("/projects")
def projects():
    ids = core.visible_project_ids()
    result = []
    with core.db() as connection:
        for identifier in ids:
            entry = core.get_project(identifier)
            entry["paper_count"] = connection.execute("SELECT COUNT(*) FROM project_papers WHERE project_id=?", (identifier,)).fetchone()[0]
            result.append(entry)
    return sorted(result, key=lambda p: (p["created_at"], p["id"]))


@router.post("/projects", status_code=201)
def create_project(body: ProjectInput):
    parent_id = body.parent_id
    if parent_id:
        parent = core.get_project(parent_id, write=True)
        if parent["owner_id"] != auth.current_user_id():
            raise HTTPException(403, "请在自己的项目中创建子项目，再按需共享")
        parent_id = parent["id"]
    project_id = core.uid()
    with core.db() as connection:
        connection.execute("INSERT INTO projects(id,name,parent_id,created_at,owner_id) VALUES(?,?,?,?,?)", (project_id, body.name, parent_id, core.now(), auth.current_user_id()))
    return core.get_project(project_id)


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch):
    current = core.get_project(project_id, write=True)
    project_id = current["id"]
    if "name" in body.model_fields_set and body.name is None:
        raise HTTPException(422, "项目名不能为空")
    parent_id = body.parent_id if "parent_id" in body.model_fields_set else current["parent_id"]
    if parent_id:
        parent_id = core.get_project(parent_id, write=True)["id"]
    with core.db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        ancestor = parent_id
        seen = {project_id}
        while ancestor:
            if ancestor in seen:
                raise HTTPException(422, "不能把项目移动到自身或其子项目中")
            seen.add(ancestor)
            row = connection.execute("SELECT parent_id FROM projects WHERE id=? AND owner_id=?", (ancestor, current["owner_id"])).fetchone()
            if not row:
                raise HTTPException(404, "父项目不存在或不属于同一所有者")
            ancestor = row["parent_id"]
        connection.execute("UPDATE projects SET name=?,parent_id=? WHERE id=?", (body.name if body.name is not None else current["name"], parent_id, project_id))
    return core.get_project(project_id)


@router.get("/papers")
def papers(project_id: str | None = None, q: str = Query(default="", max_length=500), include_children: bool = False):
    visible = core.visible_project_ids()
    selected = list(visible)
    if project_id:
        project_id = core.get_project(project_id)["id"]
        selected = [project_id]
        if include_children:
            with core.db() as connection:
                projects = [dict(r) for r in connection.execute("SELECT id,parent_id FROM projects") if r["id"] in visible]
            changed = True
            while changed:
                additions = [p["id"] for p in projects if p["parent_id"] in selected and p["id"] not in selected]
                selected.extend(additions)
                changed = bool(additions)
    placeholders = ','.join('?' for _ in selected) or 'NULL'
    conditions = [f"p.id IN (SELECT paper_id FROM project_papers WHERE project_id IN ({placeholders}))"]
    params = list(selected)
    if not project_id:
        conditions[0] = f"({conditions[0]} OR p.owner_id=?)"
        params.append(auth.current_user_id())
    if q.strip():
        conditions.append("p.title LIKE ? ESCAPE '\\'")
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    with core.db() as connection:
        ids = [r["id"] for r in connection.execute("SELECT p.id FROM papers p WHERE " + " AND ".join(conditions) + " ORDER BY p.created_at DESC,p.id", params)]
    result = [core.get_paper(paper_id) for paper_id in ids]
    if project_id:
        for paper in result:
            paper["membership"] = next((m for m in paper["memberships"] if m["project_id"] == project_id), None)
    return result


@router.post("/papers", status_code=201)
def upload_paper(file: UploadFile = File(...), project_id: str = Form("inbox"), title: str = Form("")):
    content = _read_upload(file)
    return core.add_paper(content, project_id, title, file.filename or "paper.pdf")


@router.get("/papers/{paper_id}")
def paper_detail(paper_id: str):
    return core.get_paper(paper_id)


@router.post("/papers/{paper_id}/projects", status_code=201)
def add_to_project(paper_id: str, body: ProjectLink):
    return core.link_paper(paper_id, body.project_id)


@router.patch("/papers/{paper_id}/reading")
def set_reading(paper_id: str, body: ReadingInput):
    project_id = core.get_project(body.project_id)["id"]
    membership = core.require_membership(paper_id, project_id)
    paper = core.get_paper(paper_id)
    page = body.last_page if body.last_page is not None else membership["last_page"]
    if page > paper["page_count"]:
        raise HTTPException(422, "阅读位置超出论文页数")
    offset = body.page_offset if body.page_offset is not None else (membership["page_offset"] if page == membership["last_page"] else 0)
    with core.db() as connection:
        connection.execute("INSERT INTO reading_progress(user_id,paper_id,project_id,status,last_page,page_offset,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,paper_id,project_id) DO UPDATE SET status=excluded.status,last_page=excluded.last_page,page_offset=excluded.page_offset,updated_at=excluded.updated_at", (auth.current_user_id(),paper_id,project_id,body.status,page,offset,core.now()))
        # Preserve the original single-user progress fields for existing scripts.
        if core.get_project(project_id)["owner_id"] == auth.current_user_id():
            connection.execute("UPDATE project_papers SET status=?,last_page=?,page_offset=?,updated_at=? WHERE paper_id=? AND project_id=?", (body.status,page,offset,core.now(),paper_id,project_id))
    return core.require_membership(paper_id, project_id)


@router.post("/papers/{paper_id}/translation")
def upload_translation(paper_id: str, file: UploadFile = File(...)):
    return core.attach_translation(paper_id, _read_upload(file), file.filename or "translated.pdf")


@router.get("/papers/{paper_id}/file")
def file_download(paper_id: str, side: Side = "original"):
    paper = core.get_paper(paper_id)
    if side == "translation" and paper.get("active_translation_run_id"):
        from . import translations
        path, sha = translations.merged_asset(paper), core.source_sha(paper, side)
    else:
        path, sha = core.paper_asset_path(paper, side), core.source_sha(paper, side)
    name = f"{paper['title']}{'.zh' if side == 'translation' else ''}.pdf"
    # FileResponse handles UTF-8 Content-Disposition and byte-range requests.
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache", "ETag": f'"{sha}"'})


@router.get("/papers/{paper_id}/page-manifest")
def page_manifest(paper_id: str):
    paper = core.get_paper(paper_id)
    return {"paper_id": paper_id, "source_sha": paper["sha256"], "page_count": paper["page_count"],
            "pages": pdf.page_manifest(core.paper_asset_path(paper))}


@router.get("/papers/{paper_id}/translation-pages")
def translation_pages(paper_id: str):
    from . import translations
    return translations.manifest(paper_id)


@router.get("/papers/{paper_id}/pages/{page_number}/alignment")
def paragraph_alignment(paper_id: str, page_number: int):
    from . import translations
    return translations.page_alignment(paper_id, page_number)


def _page_asset(paper: dict, page_number: int, side: Side, version: str | None = None) -> dict:
    if page_number < 1 or page_number > paper["page_count"]:
        raise HTTPException(404, "论文页码不存在")
    # A versioned progressive asset remains accessible after another page finishes
    # or a manual replacement: the lookup is scoped to this paper and page.
    if side == "translation" and (paper.get("active_translation_run_id") or version):
        from . import translations
        if paper.get("active_translation_run_id") or version != paper.get("translated_sha256"):
            try:
                return translations.page_asset(paper, page_number, v=version)
            except HTTPException as exc:
                # Keep the legacy whole-document version contract for manual PDFs.
                if paper.get("active_translation_run_id") or exc.status_code not in {404, 409}:
                    raise
                raise HTTPException(409, "PDF 版本已更新，请刷新页面") from exc
    sha = core.source_sha(paper, side)
    if version and version != sha:
        raise HTTPException(409, "PDF 版本已更新，请刷新页面")
    return {"path": core.paper_asset_path(paper, side), "pdf_page": page_number,
            "source_sha": sha, "translation_run_id": None}


@router.get("/papers/{paper_id}/pages/{page_number}.svg")
def svg_page(paper_id: str, page_number: int, side: Side = "original", v: str | None = None):
    paper = core.get_paper(paper_id)
    asset = _page_asset(paper, page_number, side, v)
    sha = asset["source_sha"]
    svg = pdf.page_svg(asset["path"], asset["pdf_page"])
    return Response(svg, media_type="image/svg+xml", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache",
        "ETag": f'"{sha}-page-{page_number}"',
        "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox",
    })


@router.get("/papers/{paper_id}/pages/{page_number}")
def page_metadata(paper_id: str, page_number: int, side: Side = "original"):
    paper = core.get_paper(paper_id)
    asset = _page_asset(paper, page_number, side)
    sha = asset["source_sha"]
    result = pdf.page_info(asset["path"], asset["pdf_page"])
    result.update(page=page_number, source_sha=sha, translation_run_id=asset["translation_run_id"],
                  svg_url=f"/api/papers/{paper_id}/pages/{page_number}.svg?side={side}&v={sha}")
    return result


@router.get("/papers/{paper_id}/annotations")
def list_annotations(paper_id: str, project_id: str):
    return core.annotation_list(paper_id, project_id)


@router.post("/papers/{paper_id}/annotations", status_code=201)
def create_annotation(paper_id: str, body: AnnotationInput):
    project_id = core.get_project(body.project_id, write=body.visibility == "shared")["id"]
    core.require_membership(paper_id, project_id)
    paper = core.get_paper(paper_id)
    if body.page > paper["page_count"]:
        raise HTTPException(422, "批注页码超出 PDF 范围")
    if body.side == "original" and body.translation_run_id:
        raise HTTPException(422, "原文批注不能携带译文任务版本")
    annotation_id = core.uid()
    timestamp = core.now()
    with core.db() as connection:
        # Validate the active page asset and write the note in one transaction.
        # A page becoming ready later cannot invalidate an already-ready page.
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT sha256,translated_sha256,active_translation_run_id FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not current:
            raise HTTPException(404, "论文不存在")
        run_id = None
        if body.side == "original":
            sha = current["sha256"]
        elif current["active_translation_run_id"]:
            run_id = current["active_translation_run_id"]
            if body.translation_run_id != run_id:
                raise HTTPException(409, "译文任务版本已改变，请重新选择词句")
            asset = connection.execute("SELECT asset_sha FROM translation_pages WHERE run_id=? AND page_no=? AND status='ready'", (run_id, body.page)).fetchone()
            if not asset:
                raise HTTPException(409, "这一页译文尚未就绪")
            sha = asset["asset_sha"]
        else:
            if body.translation_run_id:
                raise HTTPException(409, "译文任务版本已改变，请重新选择词句")
            sha = current["translated_sha256"]
        if not sha or body.source_sha != sha:
            raise HTTPException(409, "PDF 版本已改变，刷新原页后重新选择词句")
        connection.execute("INSERT INTO annotations(id,paper_id,project_id,side,page,quote,note,color,rects,source_sha,translation_run_id,created_at,updated_at,owner_id,visibility,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)", (annotation_id, paper_id, project_id, body.side, body.page, body.quote, body.note, body.color, json.dumps([r.model_dump() for r in body.rects]), sha, run_id, timestamp, timestamp,auth.current_user_id(),body.visibility))
        created = dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
        core.record_annotation_version(connection, created, "created")
    result = core.get_annotation(annotation_id)
    result["is_current"] = True
    return result


@router.patch("/annotations/{annotation_id}")
def edit_annotation(annotation_id: str, body: AnnotationPatch):
    old = core.get_annotation(annotation_id, write=True)
    if body.visibility is not None and body.visibility != old["visibility"]:
        if old["owner_id"] != auth.current_user_id():
            raise HTTPException(403, "只有作者可以改变笔记共享范围")
        if body.visibility == "shared":
            core.get_project(old["project_id"], write=True)
    with core.db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
        if current["owner_id"] != auth.current_user_id() and current["visibility"] != "shared":
            raise HTTPException(403, "笔记共享范围已改变，请刷新后重试")
        if body.expected_version is not None and body.expected_version != current["version"]:
            raise HTTPException(409, "笔记已被修改，请刷新后合并")
        existing = connection.execute("SELECT 1 FROM annotation_versions WHERE annotation_id=? AND version=?", (annotation_id,current["version"])).fetchone()
        if not existing:
            core.record_annotation_version(connection,current,"migrated")
        connection.execute("UPDATE annotations SET note=?,color=?,visibility=?,version=version+1,updated_at=? WHERE id=?", (body.note if body.note is not None else current["note"], body.color if body.color is not None else current["color"],body.visibility or current["visibility"],core.now(),annotation_id))
        updated = dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
        core.record_annotation_version(connection,updated,"updated")
    return core.get_annotation(annotation_id)


@router.get("/annotations/{annotation_id}/versions")
def annotation_versions(annotation_id: str):
    note = core.get_annotation(annotation_id)
    with core.db() as connection:
        rows = connection.execute("SELECT v.*,u.display_name AS editor FROM annotation_versions v LEFT JOIN users u ON u.id=v.editor_id WHERE v.annotation_id=? ORDER BY v.version DESC", (annotation_id,)).fetchall()
    result = []
    for row in rows:
        if note["owner_id"] != auth.current_user_id() and row["visibility"] != "shared":
            continue
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        if isinstance(item["content"].get("rects"),str):
            item["content"]["rects"] = json.loads(item["content"]["rects"])
        item["editor"] = item["editor"] or "本地所有者"
        result.append(item)
    return result


@router.delete("/annotations/{annotation_id}", status_code=204)
def remove_annotation(annotation_id: str):
    old = core.get_annotation(annotation_id, write=True)
    with core.db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = dict(connection.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
        if current["owner_id"] != auth.current_user_id() and current["visibility"] != "shared":
            raise HTTPException(403, "笔记共享范围已改变，请刷新后重试")
        current["version"] += 1
        core.record_annotation_version(connection,current,"deleted")
        connection.execute("DELETE FROM annotations WHERE id=?", (annotation_id,))
    return Response(status_code=204)


@router.get("/papers/{paper_id}/notes.md")
def export_notes(paper_id: str, project_id: str):
    paper = core.get_paper(paper_id)
    project = core.get_project(project_id)
    notes = core.annotation_list(paper_id, project_id)
    lines = [f"# {paper['title']} · 阅读笔记", "", f"项目：{project['name']}", f"原文 SHA-256：`{paper['sha256']}`", "", "这是个人笔记导出，不是 AI 生成的论文总结。", ""]
    for note in notes:
        side = "原文" if note["side"] == "original" else "译文"
        stale = " · 旧版本" if not note["is_current"] else ""
        lines.extend([f"## {side}第 {note['page']} 页{stale}", "", *["> " + line for line in note["quote"].splitlines()], "", note["note"] or "（仅高亮，尚未写下个人笔记）", "", f"来源版本：`{note['source_sha']}`", ""])
    if not notes:
        lines.append("尚无批注。")
    return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=notes.md; filename*=UTF-8''{quote(paper['title'] + '.notes.md')}", "X-Content-Type-Options": "nosniff"})

