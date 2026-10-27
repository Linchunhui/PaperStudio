"""Discovery API: whole-web search is explicitly separate from paper indexes."""
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from . import discovery

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("/config")
def config():
    return discovery.public_config()


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=500), scope: Literal["web", "arxiv", "hf"] = "web", page: int = Query(default=1, ge=1, le=10), direction: str = Query(default="", max_length=100), topic: str = Query(default="", max_length=100)):
    query = q.strip()
    if not query:
        raise HTTPException(422, "请输入搜索词")
    return await discovery.search(query, scope, page, direction, topic)


@router.get("/feed")
async def feed(sources: str = Query(default="hf,qbitai,jiqizhixin", max_length=100), interests: str = Query(default="", max_length=1000), limit: int = Query(default=30, ge=1, le=100), refresh: bool = False, direction: str = Query(default="", max_length=100), topic: str = Query(default="", max_length=100)):
    selected = list(dict.fromkeys(s.strip() for s in sources.split(",") if s.strip()))
    if not selected or any(s not in discovery.SOURCES for s in selected):
        raise HTTPException(422, "来源必须为 hf、qbitai 或 jiqizhixin")
    return await discovery.feed(selected, interests.split(","), limit, refresh, direction, topic)

