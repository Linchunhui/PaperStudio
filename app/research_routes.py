"""Explicit research actions. Read-only routes never initiate model work."""
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import research, reference_material

router = APIRouter(prefix="/api/research", tags=["research"])


class Rect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_sha: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int = Field(ge=1, le=1000, strict=True)
    block_id: str | None = Field(default=None, max_length=100)
    quote: str = Field(default="", max_length=24000)
    rects: list[Rect] = Field(default_factory=list, max_length=1000)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=100)
    paper_id: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["qa", "explain", "summary", "figure", "formula", "lineage", "knowledge", "compare"]
    question: str = Field(default="", max_length=20000)
    selection: Selection | None = None
    paper_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, min_length=2, max_length=6)


class VisibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["private", "shared"]


class ReferenceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(default="", max_length=20000)


class ReferenceImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=100)


@router.post("/jobs", status_code=202)
def create_job(body: ResearchRequest):
    return research.enqueue(**body.model_dump())


@router.get("/jobs")
def list_jobs(project_id: str = Query(min_length=1, max_length=100), paper_id: str | None = Query(default=None, min_length=1, max_length=100)):
    return research.list_jobs(project_id, paper_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return research.get_job(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return research.cancel(job_id)


@router.patch("/jobs/{job_id}/visibility")
def set_visibility(job_id: str, body: VisibilityRequest):
    return research.set_visibility(job_id, body.visibility)


@router.post("/jobs/{job_id}/references/{index}/expand", status_code=202)
def expand_reference(job_id: str, index: int, body: ReferenceQuestion):
    return research.expand_reference(job_id, index, body.question)


@router.get("/reference-assets/{asset_id}/file")
def reference_file(asset_id: str):
    asset = reference_material.require_asset(asset_id)
    return FileResponse(reference_material.asset_path(asset), media_type="application/pdf",
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline; filename=reference.pdf"})


@router.post("/reference-assets/{asset_id}/import", status_code=201)
def import_reference(asset_id: str, body: ReferenceImport):
    return reference_material.import_asset(asset_id, body.project_id)


@router.get("/jobs/{job_id}/evidence/{evidence_id}/image")
def image(job_id: str, evidence_id: str):
    return Response(research.get_image(job_id, evidence_id), media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/knowledge")
def knowledge(project_id: str = Query(min_length=1, max_length=100), category: str = Query(default="", max_length=30), q: str = Query(default="", max_length=1000), label: str = Query(default="", max_length=300)):
    return research.knowledge(project_id, category, q, label)
