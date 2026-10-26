"""Explicit, paragraph-scoped translations; reading endpoints never call AI."""
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from . import jobs, reading

router = APIRouter(prefix="/api", tags=["reading"])


class ParagraphTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=100)
    page: int = Field(ge=1, le=1000, strict=True)
    block_id: str = Field(min_length=1, max_length=100)
    source_sha: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/papers/{paper_id}/pages/{page_number}/reading")
def page_reading(paper_id: str, page_number: int, project_id: str | None = Query(default=None, min_length=1, max_length=100)):
    return reading.page_reading(paper_id, page_number, project_id)


@router.post("/papers/{paper_id}/reading/translate", status_code=202)
def translate_paragraph(paper_id: str, request: ParagraphTranslationRequest):
    return jobs.enqueue(paper_id=paper_id, kind="paragraph-translate", **request.model_dump())
