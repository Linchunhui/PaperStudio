"""POST-only explicit ranking; mounted inside the normal session/CSRF boundary."""
import hmac
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import recommendation_rerank

router = APIRouter(prefix='/api/recommendations', tags=['optional model ranking'])


class RerankInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    candidate_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=2, max_length=50)
    interests: str = Field(min_length=1, max_length=1000)
    # Explicit disclosure belongs beside the UI button, not an automatic feed load.
    confirm_model_call: Literal[True]

    @model_validator(mode='after')
    def unique_candidates(self):
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError('Candidate IDs must be unique')
        return self


@router.post('/rerank')
def rerank(body: RerankInput, request: Request):
    session = getattr(request.state, 'session', None)
    if not session:
        raise HTTPException(401, '请先登录')
    if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf_token']):
        raise HTTPException(403, '请求校验已失效，请刷新页面后重试')
    expected = request.headers.get('x-reader-account')
    if expected and expected != session['user']['id']:
        raise HTTPException(409, '阅读用户已切换，请刷新页面。')
    return recommendation_rerank.rerank(body.candidate_ids, body.interests)
