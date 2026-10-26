"""Search an offline catalog and explicitly import/update its metadata."""
from typing import Literal
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from . import catalog

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("")
def list_catalog(q: str = Query(default="", max_length=500), year: int | None = Query(default=None, ge=1900, le=2100), source: str = Query(default="all", max_length=80), institution: str = Query(default="", max_length=1024), sort: Literal["votes", "published", "title"] = "votes", page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=24, ge=1, le=100)):
    return catalog.search(q, year, source, institution, sort, page, page_size)


@router.get("/stats")
def catalog_stats():
    return catalog.stats()


@router.post("/import")
def import_catalog(file: UploadFile = File(...)):
    try:
        content = file.file.read(catalog.MAX_BYTES + 1)
    finally:
        file.file.close()
    if len(content) > catalog.MAX_BYTES:
        raise HTTPException(413, "索引文件超过 20 MB")
    return catalog.import_bytes(content, file.filename or "catalog.json")


class HFRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: str = Field(min_length=8, max_length=8)


@router.post("/refresh-hf")
async def refresh_hf(body: HFRefresh):
    return await catalog.refresh_hf(body.week)
