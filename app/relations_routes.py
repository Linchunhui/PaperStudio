from fastapi import APIRouter, Query
from . import paper_graph

router = APIRouter(prefix='/api/relations', tags=['relations'])


@router.get('/search')
def search(q: str = Query('', max_length=300), direction: str = Query('', max_length=100), limit: int = Query(20, ge=1, le=100)):
    return paper_graph.search(q, direction, limit)


@router.get('/graph')
def graph(paper: str = Query('', max_length=200), catalog_id: str = Query('', max_length=100), paper_id: str = Query('', max_length=100), project_id: str = Query('', max_length=100), limit: int = Query(50, ge=1, le=100), min_score: float = Query(0, ge=0, le=1), include_weak: bool = False):
    return paper_graph.graph(paper=paper, catalog_id=catalog_id, paper_id=paper_id, project_id=project_id, limit=limit, min_score=min_score, include_weak=include_weak)
