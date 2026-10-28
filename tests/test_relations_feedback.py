import hashlib
import json
import base64
import gzip
import importlib.util
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import auth, catalog, core, paper_graph, paper_feedback, recommendations, recommendation_graph
from app import recommendation_profiles as profiles
from app.feedback_routes import router as feedback_router
from app.relations_routes import router as relations_router
from app.data_files import read_data_bytes
from test_auth_groups import workspace, setup, new_group, invite_user, upload as upload_pdf


@pytest.fixture
def relation_bundle(tmp_path, monkeypatch):
    titles = ['Document parsing base','Document parsing neighbor','Independent model','Unknown evidence']
    group = {'id':'document','name':'文档智能','node_count':4,'known_pair_count':3,'unknown_pair_count':3,
             'papers':[{'index':i,'id':f'arxiv_2607_0000{i+1}','title':title,'has_embedding':i<3,'direction_basis':'title_rule'} for i,title in enumerate(titles)],
             'similarity_upper':[[.91,0,None],[.42,None],[None],[]]}
    source = {'node_count':4,'primary_direction_count':1,'pairs_including_unknown':6,'pairs_with_known_score':3,
              'weights':{'overview':.3,'topic':.15,'problem':.15,'method':.15,'key_results':.1,'citation':.15},
              'score_meaning':'Supplied combined score','directions':[group]}
    path = tmp_path/'source.json';path.write_text(json.dumps(source))
    spec = importlib.util.spec_from_file_location('packer', core.ROOT/'scripts'/'pack_relations.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.pack(path,tmp_path/'relations')
    monkeypatch.setattr(paper_graph,'GRAPH_DIR',tmp_path/'relations')
    paper_graph.bundle.cache_clear();paper_graph.scores.cache_clear()
    yield group
    paper_graph.bundle.cache_clear();paper_graph.scores.cache_clear()


def add_catalog(group):
    records = [{'title':p['title'],'arxiv_id':'2607.0000'+str(i+1)} for i,p in enumerate(group['papers'])]
    catalog.import_bytes(json.dumps({'papers':records}).encode())
    return {item['arxiv_id']:item for item in catalog.search(page_size=100)['items']}


@pytest.fixture
def client(tmp_path, monkeypatch, relation_bundle):
    monkeypatch.setattr(core,'DATA_DIR',tmp_path/'data')
    core.init_db();catalog.init_catalog(seed=False);profiles.init_recommendations();paper_feedback.init_feedback()
    with core.db() as con:
        con.execute("INSERT INTO users(id,username,display_name,password_hash,role,inbox_id,created_at) VALUES('local','test-user','Test','unused','admin','inbox',?)",(core.now(),))
    app = FastAPI();app.include_router(feedback_router);app.include_router(relations_router)
    with TestClient(app) as c:
        yield c, add_catalog(relation_bundle)


def test_graph_preserves_zero_unknown_symmetry_limits_and_provenance(client):
    c, papers = client
    r=c.get('/api/relations/graph',params={'paper':'arxiv_2607_00001'}).json()
    assert [i['score'] for i in r['related']]==[.91,0]
    assert r['unknown_in_group']==1 and r['references']['status']=='not_provided' and r['cited_by']['total'] is None
    assert all(e['type']=='similarity' for e in r['edges'])
    assert r['center']['catalog_paper_id']==papers['2607.00001']['id']
    assert paper_graph.pair_score('document',0,1)==paper_graph.pair_score('document',1,0)==.91
    assert paper_graph.pair_score('document',0,2)==0
    assert paper_graph.pair_score('document',0,3) is None
    small=c.get('/api/relations/graph',params={'catalog_id':papers['2607.00001']['id'],'limit':1}).json()
    assert small['truncated'] and len(small['related'])==1 and small['center']==r['center']
    filtered=c.get('/api/relations/graph',params={'paper':'arxiv_2607_00001','min_score':.92}).json()
    assert filtered['related']==r['related'] and filtered['edges']==[]
    weak=c.get('/api/relations/graph',params={'paper':'arxiv_2607_00001','min_score':.5,'include_weak':True}).json()
    assert len(weak['edges'])==3 and sum(e['weak'] for e in weak['edges'])==2
    assert any(e['source']!='arxiv_2607_00001' and e['target']!='arxiv_2607_00001' for e in weak['edges'])
    assert weak['known_edges']==3 and weak['hidden_edges']==0
    assert c.get('/api/relations/graph?limit=101').status_code==422
    assert c.get('/api/relations/search?q=parsing').json()['total']==2
    assert c.get('/api/relations/search?direction=vision').json()['items']==[]


def test_feedback_cross_surface_identity_mutual_exclusion_and_star_persistence(client):
    c,p=client
    reference={'catalog_id':p['2607.00001']['id']}
    first=c.patch('/api/paper-feedback',json={'reference':reference,'vote':1,'starred':True}).json()
    assert first['likes']==1 and first['starred'] and first['stars']==1
    again=c.patch('/api/paper-feedback',json={'reference':{'graph_id':'arxiv_2607_00001'},'vote':1}).json()
    assert again==first
    down=c.patch('/api/paper-feedback',json={'reference':{'arxiv_id':'2607.00001v2'},'vote':-1}).json()
    assert down['likes']==0 and down['dislikes']==1 and down['starred']
    clear=c.patch('/api/paper-feedback',json={'reference':reference,'vote':0}).json()
    assert clear['vote']==0 and clear['starred']
    assert c.get('/api/paper-feedback/starred').json()['items'][0]['catalog_paper_id']==reference['catalog_id']
    paper_feedback.init_feedback()
    assert c.post('/api/paper-feedback/batch',json={'references':[reference]}).json()['items'][0]==clear
    assert c.patch('/api/paper-feedback',json={'reference':reference,'vote':True}).status_code==422
    assert c.patch('/api/paper-feedback',json={'reference':{'catalog_id':reference['catalog_id'],'arxiv_id':'2607.00001'},'vote':1}).status_code==422
    assert c.post('/api/paper-feedback/batch',json={'references':[reference]*101}).status_code==422


def test_library_links_match_same_paper_and_unknown_paper_stays_empty(client):
    c,p=client
    with fitz.open() as doc:
        doc.new_page().insert_text((30,30),'Research paper')
        local=core.add_paper(doc.tobytes(),'inbox','Locally renamed title')
    profiles.link_paper(local['id'],p['2607.00001']['id'])
    r=c.get('/api/relations/graph',params={'paper_id':local['id'],'project_id':'inbox'}).json()
    assert r['center']['id']=='arxiv_2607_00001'
    assert r['center']['reader_href'].startswith('/read/'+local['id'])
    a=paper_feedback.update({'paper_id':local['id']},vote=1)
    b=paper_feedback.batch([{'catalog_id':p['2607.00001']['id']}])['items'][0]
    assert a==b
    with core.db() as con:
        con.execute('DELETE FROM paper_catalog_links')
    unknown=c.get('/api/relations/graph',params={'paper_id':local['id'],'project_id':'inbox'}).json()
    assert unknown['status']=='not_covered' and not unknown['related']


def test_relations_drive_recommendations_and_votes_invalidate_old_paging(client,monkeypatch):
    c,p=client
    seed={'catalog_id':p['2607.00001']['id']}
    paper_feedback.update(seed,starred=True)
    rec=recommendations.recommend(providers=())
    assert rec['relations_enabled'] and rec['mode']=='hybrid'
    assert rec['items'][0]['id']==p['2607.00002']['id']
    assert '收藏' in rec['items'][0]['reasons'][0] and '0.91' in rec['items'][0]['reasons'][0]
    assert rec['items'][0]['score_signals']['paper_similarity']==.91
    # Unknown and known-zero pairs are not similarity-based recommendation seeds.
    assert len(rec['items'])==1
    paper_feedback.update({'catalog_id':p['2607.00002']['id']},vote=-1)
    with pytest.raises(HTTPException) as error:
        recommendations.recommend(cursor=rec['request_id']+':1',providers=())
    assert error.value.status_code==409
    assert all(i['id']!=p['2607.00002']['id'] for i in recommendations.recommend(providers=())['items'])
    monkeypatch.setattr(recommendation_graph,'candidates',lambda *a: (_ for _ in ()).throw(ValueError('unavailable')))
    fallback=recommendations.recommend(providers=())
    assert 'paper-relations' in fallback['degraded_sources']
    assert all(i['id']!=p['2607.00002']['id'] for i in fallback['items'])


def test_recent_reading_is_a_weaker_account_scoped_seed(client):
    c,p=client
    with fitz.open() as doc:
        doc.new_page().insert_text((30,30),'Recent reading')
        local=core.add_paper(doc.tobytes(),'inbox','Read paper')
    profiles.link_paper(local['id'],p['2607.00001']['id'])
    with core.db() as con:
        con.execute('INSERT INTO reading_progress(user_id,paper_id,project_id,status,last_page,page_offset,updated_at) VALUES(?,?,?,?,?,?,?)',('local',local['id'],'inbox','reading',1,.2,core.now()))
    r=recommendations.recommend(providers=())
    assert '最近阅读' in r['items'][0]['reasons'][0]
    assert r['items'][0]['score_signals']['relevance']==pytest.approx(.91*.75)


def test_authenticated_feedback_and_graph_never_expose_private_papers(workspace,relation_bundle):
    owner=workspace;setup(owner);p=add_catalog(relation_bundle)
    group=new_group(owner);member,user,_=invite_user(owner,group,'graph-reader','viewer')
    local=upload_pdf(owner,'inbox','Private paper')
    ref={'catalog_id':p['2607.00001']['id']}
    assert owner.patch('/api/paper-feedback',json={'reference':ref,'vote':1}).status_code==200
    read=member.post('/api/paper-feedback/batch',json={'references':[ref]}).json()['items'][0]
    assert read['vote']==0 and read['likes']==1 and not read['starred']
    assert member.post('/api/paper-feedback/batch',json={'references':[{'paper_id':local['id']}]}).status_code==404
    assert member.get('/api/relations/graph',params={'paper_id':local['id'],'project_id':'inbox'}).status_code in (403,404)
    assert member.get('/api/paper-feedback/starred').json()['total']==0
    assert member.get('/api/recommendations').json()['relations_enabled'] is False
    csrf=member.headers.pop('X-CSRF-Token')
    assert member.patch('/api/paper-feedback',json={'reference':ref,'vote':-1}).status_code==403
    member.headers['X-CSRF-Token']=csrf
    member.close()


def test_bundled_relation_artifacts_retain_every_pair():
    directory=core.ROOT/'config'/'relations'
    manifest=json.loads((directory/'manifest.json').read_text())
    nodes=known=unknown=embedded=0;ids=set()
    for entry in manifest['groups']:
        raw=read_data_bytes(directory/entry['file']);assert hashlib.sha256(raw).hexdigest()==entry['sha256']
        group=json.loads(raw);data=gzip.decompress(base64.b64decode(group['scores']))
        n=group['node_count'];assert len(data)==n*(n-1)//2
        assert hashlib.sha256(data).hexdigest()==group['packed_sha256']
        assert all(value<=100 or value==255 for value in set(data))
        assert data.count(255)==group['unknown_pair_count']
        assert len(data)-data.count(255)==group['known_pair_count']
        nodes+=n;known+=group['known_pair_count'];unknown+=group['unknown_pair_count']
        embedded+=sum(p['has_embedding'] for p in group['papers'])
        for i,p in enumerate(group['papers']):assert p['index']==i and p['id'] not in ids;ids.add(p['id'])
    assert (nodes,embedded,known,known+unknown)==(12601,8879,5980731,10360703)
    assert manifest['source_sha256']=='7f4cef03bce0fc5819d970b49b728733b4e27618c415eba81e31192b65ebe11d'


def test_feedback_survives_metadata_upgrade_and_verified_pdf_link(client):
    c,p=client
    catalog.import_bytes(json.dumps({'papers':[{'title':'Unidentified source'}]}).encode())
    item=catalog.search(q='Unidentified')['items'][0]
    paper_feedback.update({'catalog_id':item['id']},starred=True,vote=1)
    catalog.import_bytes(json.dumps({'papers':[{'title':'Unidentified source','arxiv_id':'2608.11111'}]}).encode())
    state=paper_feedback.batch([{'arxiv_id':'2608.11111'}])['items'][0]
    assert state['starred'] and state['vote']==1 and state['stars']==1
    with fitz.open() as doc:
        doc.new_page().insert_text((30,30),'Imported research')
        local=core.add_paper(doc.tobytes(),'inbox','Unlinked source')
    paper_feedback.update({'paper_id':local['id']},vote=-1,starred=True)
    profiles.link_paper(local['id'],p['2607.00001']['id'])
    canonical=paper_feedback.batch([{'catalog_id':p['2607.00001']['id']}])['items'][0]
    assert canonical['starred'] and canonical['vote']==-1
    assert paper_feedback.batch([{'paper_id':local['id']}])['items'][0]==canonical
