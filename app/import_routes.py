"""URL/file entry points with one shared background processing plan."""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import core, imports, pdf

router = APIRouter(prefix="/api", tags=["imports"])


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, max_length=4096)
    paper_id: str | None = Field(default=None, min_length=1, max_length=100)
    project_id: str = Field(default="inbox", min_length=1, max_length=100)
    title: str = Field(default="", max_length=500)
    processing: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_exactly_once(self):
        if bool(self.url) == bool(self.paper_id):
            raise ValueError("请提供一个链接或一个已入库论文 ID")
        return self


@router.post("/imports", status_code=202)
def create_import(body: ImportRequest):
    return imports.create_import(**body.model_dump())


@router.post("/imports/file", status_code=202)
def import_file(file: UploadFile = File(...), project_id: str = Form("inbox"), title: str = Form(""), processing: str = Form("{}")):
    try:
        options = imports.normalize_processing(json.loads(processing))
    except (ValueError, TypeError):
        raise HTTPException(422, "processing 必须是包含处理选项的 JSON 对象") from None
    try:
        content = file.file.read(pdf.MAX_PDF_BYTES + 1)
    finally:
        file.file.close()
    if len(content) > pdf.MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 超过 50 MB 限制")
    paper = core.add_paper(content, project_id, title, file.filename or "paper.pdf")
    return imports.create_import(project_id=project_id, paper_id=paper["id"], processing=options)


@router.get("/imports")
def list_imports(project_id: str | None = None, paper_id: str | None = None):
    return imports.list_imports(project_id, paper_id)


@router.get("/imports/{import_id}")
def get_import(import_id: str):
    return imports.get_import(import_id)


@router.post("/imports/{import_id}/cancel")
def cancel_import(import_id: str):
    return imports.cancel(import_id)


@router.post("/imports/{import_id}/retry")
def retry_import(import_id: str):
    return imports.retry(import_id)


@router.get("/papers/{paper_id}/parsed")
def parsed_paper(paper_id: str):
    value = imports.get_parse_artifact(paper_id)
    if value is None:
        raise HTTPException(404, "这篇论文尚未完成全文文字解析")
    return value
