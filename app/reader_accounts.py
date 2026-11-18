"""Owner-managed reader personas with real, isolated library accounts."""
import json
import secrets
from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import auth, core, taxonomy, reader_behavior

router=APIRouter(prefix='/api/me/reader-accounts',tags=['reader accounts'])
Text=Annotated[str,Field(min_length=1,max_length=200)]


class Interest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    topic_id: str=Field(default='',max_length=100)
    category_id: str=Field(default='',max_length=100)
    custom: str=Field(default='',max_length=80)
    level: Text

    @model_validator(mode='after')
    def validate_interest(self):
        if sum(bool(x) for x in (self.topic_id,self.category_id,self.custom))!=1:
            raise ValueError('每个方向请选择一个分类或填写其他方向')
        if self.topic_id and self.topic_id not in taxonomy.TOPICS:raise ValueError('未知研究方向')
        if self.category_id and self.category_id not in taxonomy.CATEGORIES:raise ValueError('未知研究领域')
        return self


class ReaderInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name: str=Field(min_length=1,max_length=80)
    persona: Text
    goals: list[Text]=Field(min_length=1,max_length=8)
    interests: list[Interest]=Field(min_length=1,max_length=20)


class SwitchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id: str=Field(min_length=1,max_length=100)


def owner_id(request):
    return request.state.session['account_owner']['id']


def _public(con,row):
    return {'user':auth.public_user(row),'persona':json.loads(row['persona']),'simulated':bool(row['simulated']),
            'history':reader_behavior.history(con,row['id'],8)}


def create(con,owner,body):
    if con.execute('SELECT count(*) FROM reader_accounts WHERE owner_id=?',(owner,)).fetchone()[0]>=100:
        raise HTTPException(422,'最多创建 100 个阅读用户')
    user=auth.create_account(con,'reader_'+core.uid(),'!managed-reader-account',body.name)
    data=body.model_dump()
    for interest in data['interests']:
        interest['name']=(taxonomy.TOPICS[interest['topic_id']][1]['name'] if interest['topic_id'] else
            taxonomy.CATEGORIES[interest['category_id']]['name'] if interest['category_id'] else interest['custom'])
    con.execute('INSERT INTO reader_accounts VALUES(?,?,?,?,?,?)',
        (user['id'],owner,json.dumps(data,ensure_ascii=False),0,None,core.now()))
    profile={key:[] for key in ('category_ids','topic_ids','institutions','authors','conferences','keywords')}
    profile['freshness_days']=60
    for interest in body.interests:
        if interest.topic_id:profile['topic_ids'].append(interest.topic_id)
        elif interest.category_id:profile['category_ids'].append(interest.category_id)
        else:profile['keywords'].append(interest.custom)
    con.execute('INSERT INTO recommendation_profiles VALUES(?,?,1,?)',(user['id'],json.dumps(profile,ensure_ascii=False),core.now()))
    return user


@router.get('')
def accounts(request:Request):
    with core.db() as con:
        rows=con.execute('''SELECT u.*,a.persona,a.simulated FROM reader_accounts a JOIN users u ON u.id=a.user_id
            WHERE a.owner_id=? AND u.disabled=0 ORDER BY a.created_at,u.display_name''',(owner_id(request),)).fetchall()
        return {'owner':request.state.session['account_owner'],'active_user_id':auth.current_user_id(),
                'items':[_public(con,row) for row in rows],'taxonomy':taxonomy.snapshot()}


@router.post('',status_code=201)
def create_reader(body:ReaderInput,request:Request):
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        return create(con,owner_id(request),body)


@router.post('/switch')
def switch(body:SwitchInput,request:Request):
    owner=owner_id(request)
    with core.db() as con:
        if body.user_id!=owner and not con.execute('''SELECT 1 FROM reader_accounts a JOIN users u ON u.id=a.user_id
            WHERE a.owner_id=? AND a.user_id=? AND u.disabled=0''',(owner,body.user_id)).fetchone():
            raise HTTPException(404,'此测试用户不属于当前账号')
        # Invalidate requests from tabs holding the previous persona's CSRF token.
        csrf = secrets.token_urlsafe(32)
        con.execute('UPDATE auth_sessions SET active_user_id=?,csrf_token=? WHERE token_hash=? AND user_id=?',
            (None if body.user_id==owner else body.user_id,csrf,auth.token_hash(request.cookies[auth.COOKIE]),owner))
    return {'active_user_id':body.user_id,'csrf_token':csrf}


class EventInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    kind: Literal['search','open']
    value: str=Field(min_length=1,max_length=500)


@router.post('/events')
def event(body:EventInput):
    if body.kind=='open':
        with core.db() as con:
            if not con.execute('SELECT 1 FROM catalog_papers WHERE id=?',(body.value,)).fetchone():
                raise HTTPException(404,'论文不存在')
    reader_behavior.record(body.kind,body.value)
    return {'recorded':True}
