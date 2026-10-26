"""Authentication, cross-user isolation and explicit sharing regressions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from app import auth, catalog, core, imports, jobs
from app.main import app

PASSWORD = 'example-long-password-for-tests'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO', '0')
    monkeypatch.delenv('PAPERSTUDIO_SETUP_TOKEN', raising=False)
    original_catalog = catalog.init_catalog
    monkeypatch.setattr(catalog, 'init_catalog', lambda: original_catalog(seed=False))
    monkeypatch.setattr(jobs, 'start_worker', lambda: None)
    monkeypatch.setattr(imports, 'start_worker', lambda: None)
    with TestClient(app) as client:
        yield client


def setup(client):
    response = client.post('/api/auth/setup', json={
        'setup_token': (core.DATA_DIR / 'bootstrap-token').read_text().strip(),
        'username': 'admin', 'password': PASSWORD, 'display_name': 'Owner',
    })
    assert response.status_code == 201, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return response.json()['user']


def new_group(client):
    response = client.post('/api/groups', json={'name': 'Research group'})
    assert response.status_code == 201, response.text
    return response.json()['id']


def invite_user(owner, group_id, username, role):
    invitation = owner.post(f'/api/groups/{group_id}/invitations', json={'role': role})
    assert invitation.status_code == 201, invitation.text
    member = TestClient(app)
    response = member.post('/api/auth/register', json={
        'invite_token': invitation.json()['token'], 'username': username,
        'password': PASSWORD, 'display_name': username.title(),
    })
    assert response.status_code == 201, response.text
    member.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return member, response.json()['user'], invitation.json()['token']


def pdf_bytes():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((70, 90), 'Evidence: the method improves accuracy on this benchmark.')
        page = doc.new_page()
        page.insert_text((70, 90), 'Second page: limitations and future work.')
        return doc.tobytes()


def upload(client, project_id, title='Shared evidence'):
    response = client.post('/api/papers', files={'file': ('paper.pdf',pdf_bytes(),'application/pdf')},
                           data={'project_id': project_id, 'title': title})
    assert response.status_code == 201, response.text
    return response.json()


def annotate(client, paper, project, *, note, visibility='private'):
    return client.post(f"/api/papers/{paper['id']}/annotations", json={
        'project_id': project, 'side': 'original', 'page': 1,
        'quote': 'Evidence: the method improves accuracy on this benchmark.',
        'note': note, 'rects': [{'x': .1, 'y': .1, 'width': .6, 'height': .04}],
        'source_sha': paper['sha256'], 'visibility': visibility,
    })


def test_setup_sessions_csrf_and_admin_metadata(workspace):
    client = workspace
    assert client.get('/api/auth/session').json()['setup_required']
    assert client.get('/api/papers').status_code == 401
    assert client.get('/api/skills').status_code == 401
    assert client.get('/api/health').status_code == 200
    assert client.post('/api/auth/setup', json={'username':'admin','password':PASSWORD,'setup_token':'wrong'}).status_code == 403
    user = setup(client)
    assert user['is_admin'] and user['inbox_id'] == 'inbox'
    assert not (core.DATA_DIR / 'bootstrap-token').exists()
    assert client.post('/api/auth/setup', json={'username':'another','password':PASSWORD,'setup_token':'wrong'}).status_code == 409
    assert client.get('/api/papers').status_code == 200
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.post('/api/projects',json={'name':'No CSRF'}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    assert client.post('/api/projects',json={'name':'Foreign origin'},headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/api/projects',json={'name':'Valid project'}).status_code == 201
    assert client.get('/api/skills').status_code == 200
    session = client.get('/api/auth/session').json()
    assert set(session['features']) == {'ai_configured','babeldoc_installed'}
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/papers').status_code == 401
    response = client.post('/api/auth/login',json={'username':'admin','password':PASSWORD})
    assert response.status_code == 200 and response.json()['csrf_token'] != csrf


def test_project_roles_private_notes_and_revision_visibility(workspace):
    owner = workspace
    setup(owner)
    group = new_group(owner)
    project = owner.post('/api/projects',json={'name':'Shared project'}).json()['id']
    paper = upload(owner,project)
    private_project = owner.post('/api/projects',json={'name':'Owner private project'}).json()['id']
    private_paper = upload(owner,private_project,'Secret title')
    # Distinct source bytes are required to test paper asset isolation, since
    # owner uploads of identical PDFs intentionally reuse one paper identity.
    with auth.as_user(owner.get('/api/auth/session').json()['user']['id']):
        with fitz.open() as doc:
            page = doc.new_page(); page.insert_text((40,40),'Private research document')
            private_paper = core.add_paper(doc.tobytes(),private_project,'Secret unique PDF')
    assert owner.post(f'/api/projects/{project}/shares',json={'group_id':group}).status_code == 201
    editor, editor_user, _ = invite_user(owner,group,'editor','editor')
    viewer, viewer_user, _ = invite_user(owner,group,'viewer','viewer')
    for member in (editor,viewer):
        visible = member.get('/api/projects').json()
        assert project in {p['id'] for p in visible} and private_project not in {p['id'] for p in visible}
        assert member.get(f"/api/papers/{paper['id']}/file").status_code == 200
        assert member.get(f"/api/papers/{private_paper['id']}/file").status_code == 404
        for path in ('/api/skills','/api/capabilities','/api/sources','/api/discovery/config'):
            assert member.get(path).status_code == 403
    assert viewer.patch(f'/api/projects/{project}',json={'name':'Forbidden'}).status_code == 403
    assert editor.patch(f'/api/projects/{project}',json={'name':'Edited project'}).status_code == 200
    assert viewer.post('/api/papers',files={'file':('x.pdf',pdf_bytes(),'application/pdf')},data={'project_id':project}).status_code == 403
    assert viewer.post(f"/api/papers/{paper['id']}/translation",files={'file':('x.pdf',pdf_bytes(),'application/pdf')}).status_code == 403
    assert viewer.post(f"/api/papers/{paper['id']}/projects",json={'project_id':viewer_user['inbox_id']}).status_code == 201
    assert viewer.post(f"/api/papers/{paper['id']}/translation",files={'file':('x.pdf',pdf_bytes(),'application/pdf')}).status_code == 403
    assert viewer.post('/api/jobs',json={'paper_id':paper['id'],'project_id':viewer_user['inbox_id'],'kind':'translate'}).status_code == 403
    private_note = annotate(owner,paper,project,note='Private draft').json()
    assert editor.get(f"/api/papers/{paper['id']}/annotations",params={'project_id':project}).json() == []
    viewer_note = annotate(viewer,paper,project,note='Viewer personal thought')
    assert viewer_note.status_code == 201
    assert annotate(viewer,paper,project,note='Forbidden shared thought',visibility='shared').status_code == 403
    assert owner.get(f"/api/papers/{paper['id']}/annotations",params={'project_id':project}).json()[0]['id'] == private_note['id']
    shared = owner.patch(f"/api/annotations/{private_note['id']}",json={'visibility':'shared','note':'Published note','expected_version':1})
    assert shared.status_code == 200 and shared.json()['version'] == 2
    versions = editor.get(f"/api/annotations/{private_note['id']}/versions").json()
    assert len(versions) == 1 and versions[0]['content']['note'] == 'Published note'
    assert editor.patch(f"/api/annotations/{private_note['id']}",json={'note':'Collaborative edit','expected_version':2}).status_code == 200
    assert owner.patch(f"/api/annotations/{private_note['id']}",json={'note':'Stale edit','expected_version':2}).status_code == 409
    assert editor.patch(f"/api/annotations/{private_note['id']}",json={'visibility':'private'}).status_code == 403
    assert viewer.patch(f"/api/annotations/{private_note['id']}",json={'note':'Read only'}).status_code == 403
    assert owner.patch(f"/api/annotations/{private_note['id']}",json={'visibility':'private'}).status_code == 200
    assert editor.get(f"/api/annotations/{private_note['id']}/versions").status_code == 404
    # Each person has independent reading progress in a shared project.
    assert viewer.patch(f"/api/papers/{paper['id']}/reading",json={'project_id':project,'status':'reading','last_page':2}).status_code == 200
    owner_paper = owner.get(f"/api/papers/{paper['id']}").json()
    assert next(m for m in owner_paper['memberships'] if m['project_id']==project)['last_page'] == 1
    viewer_paper = viewer.get(f"/api/papers/{paper['id']}").json()
    assert next(m for m in viewer_paper['memberships'] if m['project_id']==project)['last_page'] == 2
    assert owner.delete(f"/api/groups/{group}/members/{viewer_user['id']}").status_code == 204
    # The reader deliberately saved a reference into their private inbox before
    # removal; that copy remains, but the group's private notes/project revoke.
    assert viewer.get(f"/api/papers/{paper['id']}/annotations",params={'project_id':project}).status_code == 404
    assert viewer.get(f'/api/groups/{group}').status_code == 404


def test_invites_single_use_and_private_inbox_scope(workspace):
    owner = workspace
    setup(owner)
    group = new_group(owner)
    member, user, token = invite_user(owner,group,'member','editor')
    assert user['inbox_id'] != 'inbox'
    paper = upload(member,'inbox','Member private paper')
    assert paper['owner_id'] == user['id']
    assert paper['memberships'][0]['project_id'] == user['inbox_id']
    assert owner.get(f"/api/papers/{paper['id']}").status_code == 404  # site admin does not bypass privacy
    duplicate = TestClient(app).post('/api/auth/register',json={'invite_token':token,'username':'reuse','password':PASSWORD})
    assert duplicate.status_code == 410
    with core.db() as con:
        assert con.execute("SELECT 1 FROM users WHERE username='reuse'").fetchone() is None
    assert member.post(f'/api/groups/{group}/invitations',json={'role':'admin'}).status_code == 403
    assert member.post(f'/api/projects/{user["inbox_id"]}/shares',json={'group_id':group}).status_code == 201
    assert owner.get(f"/api/papers/{paper['id']}/file").status_code == 200


def test_context_identity_does_not_bleed_across_async_tasks():
    async def identity(identifier):
        with auth.as_user(identifier):
            await asyncio.sleep(0)
            return auth.current_user_id()
    async def run():
        return await asyncio.gather(identity('alice'),identity('bob'))
    assert asyncio.run(run()) == ['alice','bob']
    assert auth.current_user_id() == 'local'


def test_simultaneous_http_requests_keep_owners_isolated(workspace):
    owner = workspace
    owner_user = setup(owner)
    group = new_group(owner)
    member, member_user, _ = invite_user(owner,group,'parallel','editor')
    def create(client, label):
        identifiers = []
        for index in range(6):
            response = client.post('/api/projects',json={'name':f'{label}-{index}'})
            assert response.status_code == 201, response.text
            identifiers.append(response.json())
        return identifiers
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(create,owner,'owner')
        second = pool.submit(create,member,'member')
        owned, member_owned = first.result(), second.result()
    assert {p['owner_id'] for p in owned} == {owner_user['id']}
    assert {p['owner_id'] for p in member_owned} == {member_user['id']}
    assert not {p['id'] for p in owned} & {p['id'] for p in member.get('/api/projects').json()}
    assert not {p['id'] for p in member_owned} & {p['id'] for p in owner.get('/api/projects').json()}
    assert auth.current_user_id() == 'local'


def test_shared_generated_results_follow_private_note_revocation(workspace):
    owner = workspace
    owner_user = setup(owner)
    group = new_group(owner)
    project = owner.post('/api/projects',json={'name':'Evidence permissions'}).json()['id']
    paper = upload(owner,project)
    owner.post(f'/api/projects/{project}/shares',json={'group_id':group})
    viewer, _, _ = invite_user(owner,group,'evidence-reader','viewer')
    note = annotate(owner,paper,project,note='The private experiment used extra training data.').json()
    job_id, research_id = core.uid(), core.uid()
    text = 'The reading note says the experiment used extra training data.'
    payload = {'source_sha':paper['sha256'],'skill':{'id':'reading-notes-summary','sha256':'fixture'},
               'kind':'notes-summary','annotations':[note]}
    source = {'id':'generated-fixture','type':'generated','project_id':project,'paper_id':paper['id'],
              'owner_id':owner_user['id'],'generated_table':'jobs','generated_job_id':job_id,
              'text':text,'visibility':'shared','source_version':'fixture'}
    with core.db() as con:
        con.execute('''INSERT INTO jobs(id,paper_id,project_id,kind,mode,status,stage,result_markdown,payload,dedupe_key,created_at,updated_at,owner_id)
            VALUES(?,?,?,'notes-summary','cards','succeeded','completed',?,?,?,?,?,?)''',
            (job_id,paper['id'],project,text,json.dumps(payload),'fixture',core.now(),core.now(),owner_user['id']))
        con.execute('''INSERT INTO research_jobs(id,project_id,paper_id,owner_id,kind,status,stage,question,payload,dedupe_key,result_json,evidence_json,created_at,updated_at)
            VALUES(?,?,?,?,'qa','succeeded','completed','What did my note say?','{}','fixture',?,?,?,?)''',
            (research_id,project,paper['id'],owner_user['id'],json.dumps({'kind':'qa','claims':[{'text':text,'citations':[]}]}),json.dumps([source]),core.now(),core.now()))
    assert owner.patch(f'/api/jobs/{job_id}/visibility',json={'visibility':'shared'}).status_code == 409
    assert viewer.get(f'/api/jobs/{job_id}').status_code == 404
    owner.patch(f"/api/annotations/{note['id']}",json={'visibility':'shared'})
    assert owner.patch(f'/api/jobs/{job_id}/visibility',json={'visibility':'shared'}).status_code == 200
    assert owner.patch(f'/api/research/jobs/{research_id}/visibility',json={'visibility':'shared'}).status_code == 200
    assert viewer.get(f'/api/jobs/{job_id}').status_code == 200
    assert viewer.get(f'/api/research/jobs/{research_id}').status_code == 200
    owner.patch(f"/api/annotations/{note['id']}",json={'visibility':'private'})
    assert viewer.get(f'/api/jobs/{job_id}').status_code == 404
    assert viewer.get(f'/api/research/jobs/{research_id}').status_code in {403,404,409}
    assert viewer.get('/api/research/jobs',params={'project_id':project}).json() == []
    assert owner.patch(f'/api/research/jobs/{research_id}/visibility',json={'visibility':'shared'}).status_code == 409


def test_bootstrap_and_data_files_are_not_static_resources(workspace):
    client = workspace
    token = (core.DATA_DIR / 'bootstrap-token').read_text().strip()
    for path in ('/bootstrap-token','/data/bootstrap-token','/static/../data/bootstrap-token',
                 '/static/%2e%2e/data/bootstrap-token','/.env','/config/sources.json'):
        response = client.get(path)
        assert response.status_code == 404
        assert token not in response.text


def test_resolve_import_cache_and_worker_identity_are_user_scoped(workspace,monkeypatch):
    from app import parsing
    from app.source_resolver import resolve_source
    owner = workspace
    owner_user = setup(owner)
    group = new_group(owner)
    member, member_user, _ = invite_user(owner,group,'importer','editor')
    paper = upload(owner,'inbox','A private source title')
    source = resolve_source('https://arxiv.org/abs/2501.12345')
    with core.db() as con:
        con.execute('INSERT INTO paper_sources(id,paper_id,source_json,source_sha,resolved_url,created_at,owner_id) VALUES(?,?,?,?,?,?,?)',
                    (core.uid(),paper['id'],json.dumps(source),paper['sha256'],source['download_url'],core.now(),owner_user['id']))
    resolved = owner.post('/api/resolve',json={'url':source['canonical_url'],'title':paper['title']}).json()
    assert resolved['paper_id'] == paper['id'] and resolved['reused']
    unresolved = member.post('/api/resolve',json={'url':source['canonical_url'],'title':paper['title']}).json()
    assert unresolved['paper_id'] is None and not unresolved['reused']
    assert member.get(f"/api/papers/{paper['id']}/parsed").status_code == 404
    other_paper = upload(member,'inbox','Another private source')
    seen = []
    def fake_parse(paper,**kwargs):
        seen.append((auth.current_user_id(),paper['owner_id']))
        return {'source_sha':paper['sha256'],'provider':'test-parser','pages':[{'text':'Page one.'},{'text':'Page two.'}],'coverage':{}}
    monkeypatch.setattr(parsing,'ensure_parsed',fake_parse)
    imports._STOP.clear()
    first = owner.post('/api/imports',json={'paper_id':paper['id'],'project_id':'inbox'}).json()
    second = member.post('/api/imports',json={'paper_id':other_paper['id'],'project_id':'inbox'}).json()
    assert member.get(f"/api/imports/{first['id']}").status_code == 404
    assert owner.get(f"/api/imports/{second['id']}").status_code == 404
    assert imports.run_once() and imports.run_once()
    assert all(actor == source_owner for actor,source_owner in seen)
    assert {actor for actor,_ in seen} == {owner_user['id'],member_user['id']}
    assert auth.current_user_id() == 'local'
    assert owner.get(f"/api/imports/{first['id']}").json()['status'] == 'succeeded'
    assert member.get(f"/api/imports/{second['id']}").json()['status'] == 'succeeded'
    assert member.get(f"/api/papers/{paper['id']}/parsed").status_code == 404


def test_restart_keeps_claimed_data_and_does_not_reseed(tmp_path, monkeypatch):
    monkeypatch.setattr(core,'DATA_DIR',tmp_path)
    monkeypatch.setenv('PAPERSTUDIO_SEED_DEMO','1')
    monkeypatch.delenv('PAPERSTUDIO_SETUP_TOKEN',raising=False)
    original_catalog = catalog.init_catalog
    monkeypatch.setattr(catalog,'init_catalog',lambda:original_catalog(seed=False))
    monkeypatch.setattr(jobs,'start_worker',lambda:None)
    monkeypatch.setattr(imports,'start_worker',lambda:None)
    with TestClient(app) as client:
        user = setup(client)
        papers = client.get('/api/papers').json()
        assert len(papers) == 1
        identifier = papers[0]['id']
    with TestClient(app) as client:
        response = client.post('/api/auth/login',json={'username':'admin','password':PASSWORD})
        assert response.status_code == 200
        assert response.json()['user']['id'] == user['id']
        papers = client.get('/api/papers').json()
        assert len(papers) == 1 and papers[0]['id'] == identifier
        assert papers[0]['owner_id'] == user['id']
        assert not (tmp_path/'bootstrap-token').exists()

