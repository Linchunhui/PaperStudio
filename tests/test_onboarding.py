import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app import auth, catalog, core, imports, jobs, model_settings, providers
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO', '0')
    for name in ('AI_PROVIDER','AI_MODEL','AI_BASE_URL','AI_API_KEY','PAPERSTUDIO_MODEL_CONFIG'):
        monkeypatch.delenv(name, raising=False)
    original = catalog.init_catalog
    monkeypatch.setattr(catalog, 'init_catalog', lambda: original(seed=False))
    monkeypatch.setattr(imports, 'start_worker', lambda: None)
    monkeypatch.setattr(jobs, 'start_worker', lambda: None)
    with TestClient(app) as client:
        setup = client.post('/api/auth/setup', json={'setup_token':(tmp_path/'bootstrap-token').read_text().strip(),
            'username':'admin', 'password':'long-test-password'}).json()
        client.headers['X-CSRF-Token'] = setup['csrf_token']
        yield client


def configuration(**overrides):
    return {'provider':'openai','base_url':'https://models.example/v1','model':'test-model',
            'api_key':'private-test-key', **overrides}


def test_saved_secret_is_private_and_endpoint_changes_clear_it(client):
    assert client.get('/api/admin/model-settings').json()['provider'] == 'none'
    result = client.put('/api/admin/model-settings', json=configuration())
    assert result.status_code == 200
    assert 'private-test-key' not in result.text and result.json()['has_api_key']
    assert result.json()['connection'] is None and result.json()['completion'] is None
    path = core.DATA_DIR/'model-settings.json'
    if os.name == 'posix':
        assert path.stat().st_mode & 0o777 == 0o600
    assert providers.settings()['api_key'] == 'private-test-key'
    assert client.put('/api/admin/model-settings', json=configuration(model='another',api_key=None)).status_code == 200
    assert providers.settings()['api_key'] == 'private-test-key'
    bad = client.put('/api/admin/model-settings', json=configuration(base_url='https://other.example/v1',api_key=None))
    assert bad.status_code == 422
    assert providers.settings()['base_url'] == 'https://models.example/v1'
    invalid = client.put('/api/admin/model-settings', json=configuration(base_url='file:///secret'))
    assert invalid.status_code == 422 and 'private-test-key' not in invalid.text


def test_real_call_is_explicit_and_status_invalidates(client, monkeypatch):
    calls=[]
    monkeypatch.setattr(providers,'complete',lambda *a,**k: calls.append(a) or 'OK')
    client.put('/api/admin/model-settings',json=configuration())
    assert client.get('/api/admin/model-settings').status_code == 200
    assert calls == []
    response=client.post('/api/admin/model-settings/test',json={'kind':'completion'})
    assert response.json()['completion'] is True and len(calls)==1
    assert calls[0][2]['max_output_tokens']==32
    assert 'private-test-key' not in response.text
    client.put('/api/admin/model-settings',json=configuration(model='changed'))
    assert client.get('/api/admin/model-settings').json()['completion'] is None


def test_failed_call_does_not_claim_success(client, monkeypatch):
    from app.ai import ExecutionError
    def fail(*a,**k): raise ExecutionError('模型服务返回 HTTP 401；检查模型权限与上下文容量。')
    monkeypatch.setattr(providers,'complete',fail)
    client.put('/api/admin/model-settings',json=configuration())
    result=client.post('/api/admin/model-settings/test',json={'kind':'completion'}).json()
    assert result['completion'] is False and result['connection'] is None


def test_environment_fallback_and_local_model(client, monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','openai');monkeypatch.setenv('AI_BASE_URL','https://legacy.example/v1')
    monkeypatch.setenv('AI_MODEL','legacy');monkeypatch.setenv('AI_API_KEY','environment-secret')
    assert providers.settings()['model']=='legacy'
    response=client.put('/api/admin/model-settings',json=configuration(provider='local',base_url='http://host.docker.internal:11434/v1',api_key=None))
    assert response.status_code==200 and providers.settings()['api_key']==''
    assert client.delete('/api/admin/model-settings').json()['source']=='environment'
    assert providers.settings()['model']=='legacy'
    monkeypatch.setenv('PAPERSTUDIO_MODEL_CONFIG','environment')
    assert client.put('/api/admin/model-settings',json=configuration()).status_code==409


def test_non_admin_cannot_read_or_change_settings(client):
    group=client.post('/api/groups',json={'name':'Team'}).json()
    token=client.post(f"/api/groups/{group['id']}/invitations",json={'role':'viewer'}).json()['token']
    member=TestClient(app)
    session=member.post('/api/auth/register',json={'invite_token':token,'username':'member','password':'long-test-password'}).json()
    member.headers['X-CSRF-Token']=session['csrf_token']
    assert member.get('/api/admin/model-settings').status_code==403
    assert member.put('/api/admin/model-settings',json=configuration()).status_code==403
    assert member.post('/api/admin/model-settings/test',json={'kind':'completion'}).status_code==403
    assert client.put('/api/admin/model-settings',json=configuration(),headers={'Origin':'https://evil.example'}).status_code==403


def test_sample_uses_real_pdf_without_model(client, monkeypatch):
    def unexpected(*a,**k): raise AssertionError('An example must never invoke a model')
    monkeypatch.setattr(providers,'complete',unexpected)
    assert not client.get('/api/onboarding').json()['has_papers']
    result=client.post('/api/onboarding/sample').json()
    assert result['model_called'] is False
    pdf=client.get(f"/api/papers/{result['paper_id']}/file")
    assert pdf.content.startswith(b'%PDF-')
    assert client.get('/api/onboarding').json()['has_papers']
    assert client.get('/api/jobs').json()==[]
    assert client.post('/api/onboarding/sample').json()['paper_id']==result['paper_id']


def test_templates_create_owned_nested_projects_idempotently(client):
    templates=client.get('/api/onboarding/templates').json()['templates']
    assert {t['id'] for t in templates}=={'document','dllm','vlm'}
    made=client.post('/api/onboarding/templates/document').json()
    assert made['created']
    duplicate=client.post('/api/onboarding/templates/document').json()
    assert duplicate['project_id']==made['project_id'] and not duplicate['created']
    with core.db() as con:
        children=con.execute('SELECT name,owner_id FROM projects WHERE parent_id=?',(made['project_id'],)).fetchall()
        parent=con.execute('SELECT owner_id FROM projects WHERE id=?',(made['project_id'],)).fetchone()
    assert [r['name'] for r in children]==templates[0]['folders']
    assert all(r['owner_id']==parent['owner_id'] for r in children)
    assert client.post('/api/onboarding/templates/unknown').status_code==404
