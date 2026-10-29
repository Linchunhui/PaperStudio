"""First-run helpers and explicit administrator model configuration."""
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from . import auth, core, model_settings

router = APIRouter(prefix='/api', tags=['onboarding'])


@router.get('/admin/model-settings')
def status():
    auth.require_admin()
    return model_settings.public_status()


@router.put('/admin/model-settings')
def save(body: model_settings.ModelInput):
    auth.require_admin()
    return model_settings.save(body)


@router.delete('/admin/model-settings')
def reset():
    auth.require_admin()
    return model_settings.reset()


class TestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['connection', 'completion'] = 'connection'


@router.post('/admin/model-settings/test')
def test(body: TestInput):
    auth.require_admin()
    return model_settings.test_model(body.kind)


@router.get('/onboarding')
def onboarding():
    with core.db() as con:
        count = con.execute('SELECT count(*) FROM papers WHERE owner_id=?', (auth.current_user_id(),)).fetchone()[0]
    return {'has_papers': bool(count), 'is_admin': auth.current_user()['is_admin'],
            'sample_label': '自编阅读示例，含预制译文，不调用模型'}


@router.post('/onboarding/sample', status_code=201)
def sample():
    from .sample import create_personal_sample
    return create_personal_sample()


@router.get('/admin/data-bundle')
def bundle_status():
    auth.require_admin()
    from .data_bundles import status
    return status()


@router.post('/admin/data-bundle',status_code=202)
def load_bundle():
    auth.require_admin()
    from .data_bundles import start
    return start()


@router.get('/onboarding/templates')
def templates():
    import json
    return json.loads((core.ROOT/'config'/'project-templates.json').read_text())


@router.post('/onboarding/templates/{template_id}',status_code=201)
def create_template(template_id:str):
    from fastapi import HTTPException
    template=next((t for t in templates()['templates'] if t['id']==template_id),None)
    if template is None: raise HTTPException(404,'项目模板不存在。')
    user=auth.current_user_id()
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        existing=con.execute('SELECT id FROM projects WHERE owner_id=? AND name=? AND parent_id IS NULL ORDER BY created_at LIMIT 1',(user,template['name'])).fetchone()
        if existing:
            identifier=existing['id']
        else:
            identifier=core.uid()
            con.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(identifier,template['name'],None,core.now(),user))
            for name in template['folders']:
                con.execute('INSERT INTO projects VALUES(?,?,?,?,?)',(core.uid(),name,identifier,core.now(),user))
    return {'project_id':identifier,'template':template,'created':not bool(existing)}
