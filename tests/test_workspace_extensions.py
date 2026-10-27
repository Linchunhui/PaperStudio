"""User-visible index/follow/search and evidence-bound multi-paper comparison."""
import json
import pytest
from fastapi import HTTPException
from app import catalog, core, ai, research, evidence
from app import recommendation_profiles as profiles, recommendations
from app.catalog_entities import browse
from test_catalog import client, upload
from test_research import env, pdf_bytes


def test_author_migration_and_intersected_entity_counts(client):
    upload(client, [
        {'title':'OCR method one','authors':['Alice','Bob'],'institutions':['Lab A'],'published_at':'2025-01-01'},
        {'title':'OCR method two','authors':['Alice'],'institutions':['Lab B'],'published_at':'2026-01-01'},
        {'title':'Speech recognition method','authors':['Bob'],'institutions':['Lab A'],'published_at':'2024-01-01'},
    ])
    r=browse('author','','document','document.ocr','Lab A','','',1,24)
    assert {(v['value'],v['count']) for v in r['items']}=={('Alice',1),('Bob',1)}
    result=client.get('/api/catalog',params={'topic':'document.ocr','institution':'Lab A','author':'Alice','sort':'oldest'}).json()
    assert [p['title'] for p in result['items']]==['OCR method one']
    assert client.get('/api/catalog',params={'author':'Alice','sort':'published'}).json()['items'][0]['title']=='OCR method two'
    assert client.get('/api/catalog/entities',params={'kind':'author','q':'Alice','page_size':1}).json()['total']==1
    with core.db() as con:
        original=[tuple(r) for r in con.execute('SELECT id,data FROM catalog_papers ORDER BY id')]
        con.execute("DELETE FROM catalog_facets WHERE kind='author'")
        con.execute("DELETE FROM catalog_migrations WHERE id='author-facets-v1'")
    catalog.init_catalog(seed=False);catalog.init_catalog(seed=False)
    assert catalog.search(author='Alice')['total']==2
    with core.db() as con:
        assert [tuple(r) for r in con.execute('SELECT id,data FROM catalog_papers ORDER BY id')]==original


def test_search_reasons_match_real_sort_and_literal_queries(client):
    upload(client,[{'title':'OCR title match','abstract':'A technique','published_at':'2024-01-01'},
                   {'title':'Abstract match','abstract':'OCR technique','published_at':'2026-01-01'},
                   {'title':'Only metadata','authors':['OCR'],'published_at':'2026-01-02'}])
    r=catalog.search('OCR',sort='relevance')
    assert [p['title'] for p in r['items']]==['OCR title match','Abstract match','Only metadata']
    assert '标题匹配：OCR' in r['items'][0]['reasons']
    assert '摘要匹配：OCR' in r['items'][1]['reasons']
    assert '作者匹配：OCR' in r['items'][2]['reasons']
    assert catalog.search('%',sort='relevance')['total']==0
    assert catalog.search('OCR',sort='oldest')['items'][0]['title']=='OCR title match'


def test_follow_all_entity_types_preserves_profile_and_changes_ranking(client):
    profiles.init_recommendations()
    upload(client,[{'title':'OCR method','authors':['Alice'],'institutions':['Lab A']}, {'title':'OCR unrelated','authors':['Bob'],'institutions':['Lab B']}])
    for kind,value in [('author','Alice'),('institution','Lab A'),('category','document'),('topic','document.ocr'),('conference','ICLR')]:
        profiles.set_follow(kind,value,True)
    p=profiles.get_profile()
    assert p['authors']==['Alice'] and p['institutions']==['Lab A'] and p['topic_ids']==['document.ocr']
    ranked=recommendations.recommend(providers=())
    assert ranked['items'][0]['title']=='OCR method'
    assert '来自关注作者：Alice' in ranked['items'][0]['reasons']
    profiles.set_follow('category','document',False)
    assert profiles.get_profile()['topic_ids']==['document.ocr']
    profiles.set_follow('author','Alice',False)
    assert profiles.get_profile()['institutions']==['Lab A']
    with pytest.raises(HTTPException): profiles.set_follow('topic','missing',True)


def test_compare_only_selected_papers_with_verified_multi_paper_citations(env,monkeypatch):
    client,a=env
    b=core.add_paper(pdf_bytes('Our method improves accuracy on Dataset B.'),'study','Paper B')
    c=core.add_paper(pdf_bytes('PRIVATE EXCLUDED MATERIAL'),'study','Paper C')
    calls=[]
    def model(system,source,cfg):
        value=json.loads(source)
        if system==research.VERIFY_SYSTEM:
            return json.dumps({'supported':[v['index'] for v in value['statements']]})
        calls.append(value)
        mats=value['materials'];assert not any('PRIVATE EXCLUDED' in m['text'] for m in mats)
        first=next(m for m in mats if m['paper_title']=='Paper A' and '20 percent' in m['text'])
        second=next(m for m in mats if m['paper_title']=='Paper B')
        return json.dumps({'claims':[{'text':'【差异·实验】两篇论文评估不同数据集，不能直接排名。','citations':[{'source_id':m['id'],'quote':m['text']} for m in [first,second]]},
                                     {'text':'单篇证据不足以比较','citations':[{'source_id':first['id'],'quote':first['text']}]}]})
    monkeypatch.setattr(ai,'complete',model)
    response=client.post('/api/research/jobs',json={'project_id':'study','kind':'compare','paper_ids':[a['id'],b['id']]})
    assert response.status_code==202,response.text
    job=response.json();assert sorted(job['paper_ids'])==sorted([a['id'],b['id']]);assert not calls
    assert research.run_once()
    out=research.get_job(job['id']);assert out['status']=='succeeded',out
    assert out['coverage']['total_papers']==2
    assert len(out['result']['claims'])==1
    citations=out['result']['claims'][0]['citations']
    assert {v['paper_id'] for v in citations}=={a['id'],b['id']}
    assert all(v['quote'] and v['page']==1 and v['source_sha'] and v['href'].startswith('/read/') for v in citations)
    assert out['coverage']['rejected_citations_or_items']==1
    assert c['id'] not in json.dumps(out['coverage'])


def test_compare_rejects_invalid_scope_and_missing_source(env,monkeypatch):
    client,a=env
    b=core.add_paper(pdf_bytes(''),'study','Scanned PDF')
    assert client.post('/api/research/jobs',json={'project_id':'study','kind':'compare','paper_ids':[a['id']]}).status_code==422
    assert client.post('/api/research/jobs',json={'project_id':'study','kind':'compare','paper_ids':[a['id'],a['id']]}).status_code==422
    assert client.post('/api/research/jobs',json={'project_id':'study','kind':'compare','paper_ids':[a['id'],'outside']}).status_code==404
    job=research.enqueue('study','compare',paper_ids=[a['id'],b['id']])
    monkeypatch.setattr(ai,'complete',lambda *args:pytest.fail('Missing paper must not be compared by the model'))
    research.run_once();assert research.get_job(job['id'])['status']=='failed'
    # Membership is rechecked for history, not merely at enqueue time.
    with core.db() as con: con.execute('DELETE FROM project_papers WHERE project_id=? AND paper_id=?',('study',b['id']))
    assert client.get('/api/research/jobs/'+job['id']).status_code==409


def test_external_search_reuses_only_verified_catalog_content(client):
    from app.discovery import _catalog_presentation
    from app import recommendation_vectors as vectors
    from app.recommendation_routes import StructuredInput
    from app.recommendation_content import embedding_document
    profiles.init_recommendations(); vectors.init_vectors()
    upload(client,[{'title':'Canonical OCR','arxiv_id':'2601.12345','abstract':'$x^2$ matters'}])
    paper=catalog.search()['items'][0]
    vectors.store_structured(paper['id'],StructuredInput(source_version='test',source_document_hash=embedding_document(paper)['text_hash'],research_problem='Saved analysis'))
    linked,unlinked=_catalog_presentation([{'id':'external','arxiv_id':'2601.12345v2','snippet':'Remote abstract'}, {'id':'different','title':'Canonical OCR','snippet':'Same title is not identity'}])
    assert linked['structured']['research_problem']=='Saved analysis'
    assert linked['catalog_paper_id']==paper['id']
    assert 'structured' not in unlinked and 'catalog_paper_id' not in unlinked
