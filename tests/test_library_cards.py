"""Library card enrichment preserves local identities and access boundaries."""
import json
import pytest
from fastapi import HTTPException
from app import auth, catalog, core, paper_figures, reading_cards
from app.recommendation_content import library_cards
from test_auth_groups import workspace, setup, upload, new_group, invite_user


def catalog_paper(title='Public title', **extra):
    catalog.import_bytes(json.dumps({'papers': [{'title': title, 'arxiv_id': '2610.12345', 'abstract': 'Public abstract', **extra}]}).encode(), 'cards.json')
    with core.db() as con:
        return json.loads(con.execute('SELECT data FROM catalog_papers LIMIT 1').fetchone()[0])


def link(local, public, method='verified-source'):
    with core.db() as con:
        con.execute('INSERT OR REPLACE INTO paper_catalog_links VALUES(?,?,?,?)', (local['id'], public['id'], method, core.now()))


def test_card_opt_in_preserves_title_identity_and_project_progress(workspace, monkeypatch):
    user = setup(workspace)
    local = upload(workspace, user['inbox_id'], 'My chosen title')
    public = catalog_paper()
    link(local, public)
    monkeypatch.setattr(paper_figures, 'presentation', lambda item: {'paper_figure': {'caption': 'Figure 1'}})
    ordinary = workspace.get('/api/papers', params={'project_id': user['inbox_id']}).json()[0]
    assert 'abstract' not in ordinary and 'catalog_paper_id' not in ordinary
    enriched = workspace.get('/api/papers', params={'project_id': user['inbox_id'], 'include_card': True}).json()[0]
    assert enriched['id'] == enriched['paper_id'] == local['id']
    assert enriched['title'] == 'My chosen title'
    assert enriched['catalog_paper_id'] == public['id']
    assert enriched['abstract'] == 'Public abstract'
    assert enriched['paper_figure']['caption'] == 'Figure 1'
    for key in ('owner_id', 'can_edit', 'memberships', 'membership', 'sha256', 'filename'):
        assert enriched[key] == ordinary[key]


def test_title_and_unverified_link_do_not_match(workspace):
    user = setup(workspace)
    local = upload(workspace, user['inbox_id'], 'Public title')
    public = catalog_paper()
    for method in (None, 'title-only'):
        if method:
            link(local, public, method)
        result = workspace.get('/api/papers?include_card=true').json()[0]
        assert 'catalog_paper_id' not in result
        assert 'abstract' not in result
        assert result['id'] == local['id']


def test_pdf_mismatched_reading_card_is_not_reused(workspace, monkeypatch):
    user = setup(workspace)
    local = upload(workspace, user['inbox_id'])
    link(local, catalog_paper())
    monkeypatch.setattr(reading_cards, 'presentation', lambda paper: {'reading_card': {'source_sha256': '0'*64, 'abstract': 'Wrong version'}})
    assert 'reading_card' not in workspace.get('/api/papers?include_card=true').json()[0]
    monkeypatch.setattr(reading_cards, 'presentation', lambda paper: {'reading_card': {'source_sha256': local['sha256'], 'abstract': 'Exact version'}})
    assert workspace.get('/api/papers?include_card=true').json()[0]['reading_card']['abstract'] == 'Exact version'


def test_private_library_not_exposed_via_enrichment(workspace):
    owner = setup(workspace)
    project = workspace.post('/api/projects', json={'name': 'Owner private'}).json()['id']
    local = upload(workspace, project)
    link(local, catalog_paper())
    member, user, _ = invite_user(workspace, new_group(workspace), 'reader', 'viewer')
    assert member.get('/api/papers?include_card=true').json() == []
    assert member.get('/api/papers', params={'include_card': True, 'project_id': project}).status_code == 404
    with auth.as_user(user['id']), pytest.raises(HTTPException):
        library_cards([local])


def test_private_starred_row_enriched_without_losing_local_id(workspace):
    owner = setup(workspace)
    local = upload(workspace, owner['inbox_id'], 'Private custom title')
    response = workspace.patch('/api/paper-feedback', json={'reference': {'paper_id': local['id']}, 'starred': True})
    assert response.status_code == 200
    item = workspace.get('/api/paper-feedback/starred').json()['items'][0]
    assert item['id'] == item['paper_id'] == local['id']
    assert item['title'] == 'Private custom title'
    assert item['reader_href'].startswith('/read/'+local['id'])
    assert item['feedback']['starred'] is True


def test_hash_fallback_requires_unique_authorized_link(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(paper_figures, 'presentation', lambda paper: {})
    monkeypatch.setattr(reading_cards, 'presentation', lambda paper: {})
    digest = 'a'*64
    local = {'id': 'mine', 'title': 'Custom', 'sha256': digest, 'can_edit': False}
    with core.db() as con:
        con.executescript('CREATE TABLE papers(id TEXT,sha256 TEXT); CREATE TABLE catalog_papers(id TEXT,data TEXT); CREATE TABLE paper_catalog_links(paper_id TEXT,catalog_paper_id TEXT,match_method TEXT);')
        con.executemany('INSERT INTO papers VALUES(?,?)', [('mine',digest),('linked',digest),('ambiguous',digest)])
        con.executemany('INSERT INTO catalog_papers VALUES(?,?)', [('c1',json.dumps({'id':'c1','title':'One','abstract':'One'})),('c2',json.dumps({'id':'c2','title':'Two','abstract':'Two'}))])
        con.execute("INSERT INTO paper_catalog_links VALUES('linked','c1','verified-source')")
    def inaccessible(identifier):
        if identifier != 'mine':
            raise HTTPException(404, 'hidden')
        return local
    monkeypatch.setattr(core, 'get_paper', inaccessible)
    assert 'catalog_paper_id' not in library_cards([local])[0]
    monkeypatch.setattr(core, 'get_paper', lambda identifier: local)
    assert library_cards([local])[0]['catalog_paper_id'] == 'c1'
    with core.db() as con:
        con.execute("INSERT INTO paper_catalog_links VALUES('ambiguous','c2','verified-source')")
    assert 'catalog_paper_id' not in library_cards([local])[0]


def test_library_cards_work_without_catalog_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    local = {'id':'mine','title':'My title','sha256':'a'*64}
    monkeypatch.setattr(core, 'get_paper', lambda identifier: local)
    assert library_cards([local]) == [{**local, 'paper_id':'mine'}]
    assert library_cards([]) == []


def test_library_image_does_not_use_other_pdf_revision(workspace, monkeypatch):
    user = setup(workspace)
    local = upload(workspace, user['inbox_id'])
    link(local, catalog_paper())
    monkeypatch.setattr(paper_figures, '_linked', lambda identifier: {'sha256': '0'*64})
    monkeypatch.setattr(paper_figures, 'presentation', lambda item: pytest.fail('wrong PDF image selected'))
    assert 'paper_figure' not in workspace.get('/api/papers?include_card=true').json()[0]
