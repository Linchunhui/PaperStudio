from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import paper_feedback

router = APIRouter(prefix='/api/paper-feedback', tags=['paper-feedback'])


class Reference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    catalog_id: str = Field('', max_length=100)
    paper_id: str = Field('', max_length=100)
    arxiv_id: str = Field('', max_length=100)
    graph_id: str = Field('', max_length=200)


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    references: list[Reference] = Field(min_length=1, max_length=100)


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: Reference
    vote: StrictInt | None = Field(None, ge=-1, le=1)
    starred: StrictBool | None = None


@router.post('/batch')
def batch(body: Batch):
    return paper_feedback.batch([ref.model_dump() for ref in body.references])


@router.patch('')
def update(body: Update):
    return paper_feedback.update(body.reference.model_dump(), body.vote, body.starred)


@router.get('/starred')
def starred(page: int = Query(1, ge=1, le=100000), limit: int = Query(24, ge=1, le=100)):
    return paper_feedback.starred(page, limit)
