from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from . import ai, jobs

router = APIRouter(prefix="/api", tags=["processing"])


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    kind: Literal["guide", "brief", "selection-summary", "notes-summary", "translate"]
    mode: Literal["abstract", "cards", "summary", "all"] = "cards"
    annotation_id: str | None = Field(default=None, max_length=100)
    force: bool = Field(default=False, strict=True)


@router.get("/capabilities")
def capabilities():
    return ai.capabilities()


@router.get("/skills")
def skills():
    return ai.skills_list()


@router.post("/jobs", status_code=202)
def create_job(request: JobRequest):
    return jobs.enqueue(**request.model_dump())


@router.get("/jobs")
def list_jobs(paper_id: str | None = None, project_id: str | None = None):
    return jobs.list_jobs(paper_id, project_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get_job(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    return jobs.cancel(job_id)


@router.post("/jobs/{job_id}/retry")
def retry(job_id: str):
    return jobs.retry(job_id)
