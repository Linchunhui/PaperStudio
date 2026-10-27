"""Exercise the real SQLite pipeline, including versioned semantic retrieval."""
import json
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, catalog, core, imports, jobs, recommendations as rec
from app import recommendation_profiles as profiles, recommendation_vectors as vectors
from app.recommendation_content import embedding_document, RetrievalContext, RetrievedPaper
from app.recommendation_routes import router, StructuredInput, VectorBatch
from app.import_routes import router as imports_router
from test_auth_groups import workspace, setup, invite_user, new_group
from test_imports import fake_network, drain


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(core, 'now', lambda: '2026-10-27T12:00:00+00:00')
    core.init_db()
    catalog.init_catalog(seed=False)
    profiles.init_recommendations()
    vectors.init_vectors()
    jobs.init_jobs()
    imports.init_imports()
    imports._STOP.clear()
    jobs._STOP.clear()
    app = FastAPI()
    app.include_router(router)
    app.include_router(imports_router)
    with TestClient(app) as session:
        yield session


def put_papers(*papers):
    catalog.import_bytes(json.dumps({'papers': papers}).encode(), 'recommendation-fixtures.json')
    with core.db() as con:
        return {p['title']: p for r in con.execute('SELECT data FROM catalog_papers') for p in [json.loads(r[0])]}


def preference(**kwargs):
    current = profiles.get_profile()
    return profiles.save_profile({**current, **kwargs}, current['version'])


def vector_item(entity, vector, kind='paper'):
    if kind == 'paper':
        identifier, digest = entity['id'], embedding_document(entity)['text_hash']
    else:
        identifier, digest = entity, vectors.interest_document(kind, entity)['input_hash']
    return {'entity_kind': kind, 'entity_id': identifier, 'input_hash': digest, 'vector': vector}


def ingest(items, **kwargs):
    return vectors.import_vectors(VectorBatch(**{'space_id': 'fixture-v1', 'model': 'test-only-encoder',
        'model_revision': 'fixed-fixture-revision', 'dimensions': 3, 'activate': True, 'items': items, **kwargs}))


def test_empty_catalog_and_no_preference_fallback(client):
    assert client.get('/api/recommendations').json()['items'] == []
    papers = put_papers({'title': 'Recent research', 'published_at': '2026-10-26'},
                        {'title': 'Older research', 'published_at': '2020-01-01', 'hf_upvotes': 1000})
    result = client.get('/api/recommendations').json()
    assert result['fallback'] == 'no_preferences'
    assert result['mode'] == 'rules' and not result['embedding_enabled']
    assert result['items'][0]['id'] == papers['Recent research']['id']
    assert 'raw' not in result['items'][0] and 'provenance' not in result['items'][0]


def test_child_interest_does_not_subscribe_to_siblings_and_freshness_matters(client):
    put_papers({'title': 'OCR for small images', 'published_at': '2026-10-26'},
               {'title': 'OCR for large images', 'published_at': '2020-01-01', 'hf_upvotes': 1000},
               {'title': 'Table structure recognition', 'published_at': '2026-10-26'},
               {'title': 'Language models', 'published_at': '2026-10-26', 'hf_upvotes': 999999})
    preference(topic_ids=['document.ocr'])
    result = rec.recommend()
    assert [i['title'] for i in result['items']] == ['OCR for small images', 'OCR for large images']
    assert result['fallback'] is None
    assert any('关注方向' in r for r in result['items'][0]['reasons'])
    preference(institutions=['Unknown Institute'], topic_ids=[])
    result = rec.recommend(category_id='document', topic_id='document.table')
    assert result['fallback'] == 'no_matches'
    assert [p['title'] for p in result['items']] == ['Table structure recognition']
    assert client.get('/api/recommendations?category_id=document&topic_id=nlp.language').status_code == 422


def test_institution_and_structured_signals_do_not_require_user_history(client):
    papers = put_papers({'title': 'A generic experiment', 'institutions': ['Example Lab']},
                        {'title': 'A separate experiment', 'abstract': 'An experimental setup.'})
    second = papers['A separate experiment']
    body = {'source_version': 'extractor-v1', 'source_document_hash': embedding_document(second)['text_hash'],
            'research_problem': 'Measure reliable optical character recognition', 'methods': ['contrastive alignment']}
    assert client.put('/api/admin/recommendations/content/' + second['id'], json=body).status_code == 200
    preference(institutions=['example lab'], keywords=['contrastive alignment'])
    items = rec.recommend()['items']
    assert len(items) == 2
    assert any('关注机构标签' in reason for item in items for reason in item['reasons'])
    assert any('研究问题或方法' in reason for item in items for reason in item['reasons'])
    # Evidence/limitations are stored, but cannot masquerade as method relevance.
    vectors.store_structured(second['id'], StructuredInput(**{**body, 'methods': [], 'evidence': ['contrastive alignment']}))
    assert not any('研究问题或方法' in reason for item in rec.recommend()['items'] for reason in item['reasons'])


@pytest.mark.parametrize('date,extra', [('', {}), ('2035-01-01', {}), ('invalid', {}),
    ('2026-10-26', {'provenance': [{'source_id': 'institution', 'notes': '日期可能为会议日期'}]})])
def test_unknown_or_uncertain_dates_are_not_called_new(date, extra):
    at = datetime(2026, 10, 27, tzinfo=timezone.utc)
    assert rec._freshness({'published_at': date, **extra}, at, 30) == (0.0, None)


def test_profile_validation_optimistic_updates_and_follow_preservation(client):
    bad = client.put('/api/me/recommendation-profile', json={'expected_version': 0, 'topic_ids': ['unknown']})
    assert bad.status_code == 422
    result = client.put('/api/me/recommendation-profile', json={'expected_version': 0, 'topic_ids': ['document.ocr'], 'keywords': [' OCR ', 'OCR']})
    assert result.status_code == 200 and result.json()['keywords'] == ['OCR']
    assert client.put('/api/me/recommendation-profile', json={'expected_version': 0}).status_code == 409
    follow = client.patch('/api/me/recommendation-profile/follow', json={'kind': 'institution', 'value': 'Example Lab', 'enabled': True})
    assert follow.status_code == 200
    assert follow.json()['topic_ids'] == ['document.ocr'] and follow.json()['institutions'] == ['Example Lab']
    assert client.patch('/api/me/recommendation-profile/follow', json={'kind': 'institution', 'value': 'Example Lab', 'enabled': False}).json()['institutions'] == []
    assert client.put('/api/me/recommendation-profile', json={'expected_version': 3, 'user_id': 'victim'}).status_code == 422


def test_snapshot_paging_stays_stable_and_dismissals_are_rechecked(client):
    put_papers(*[{'title': f'OCR experiment {i}', 'arxiv_id': f'2601.{10000+i}', 'published_at': '2026-10-25'} for i in range(9)])
    preference(topic_ids=['document.ocr'])
    first = rec.recommend(limit=2)
    original = rec.recommend(limit=60)['items']
    dismissed = original[2]['id']
    profiles.dismiss(dismissed)
    put_papers({'title': 'OCR brand new', 'published_at': '2026-10-27'})
    rest = rec.recommend(limit=60, cursor=first['next_cursor'])
    assert first['request_id'] == rest['request_id']
    assert [p['id'] for p in first['items'] + rest['items']] == [p['id'] for p in original if p['id'] != dismissed]
    with auth.as_user('another-user'):
        with pytest.raises(HTTPException) as error:
            rec.recommend(cursor=first['next_cursor'])
        assert error.value.status_code == 409
        assert dismissed in [p['id'] for p in rec.recommend()['items']]
    with pytest.raises(HTTPException) as error:
        rec.recommend(cursor=first['next_cursor'], category_id='document')
    assert error.value.status_code == 422
    preference(keywords=['alignment'])
    assert client.get('/api/recommendations', params={'cursor': first['next_cursor']}).status_code == 409
    assert client.delete('/api/me/recommendation-dismissals').status_code == 200


def test_expired_snapshots_and_per_user_retention(client):
    put_papers({'title': 'OCR one'}, {'title': 'OCR two'})
    cursor = rec.recommend(limit=1)['next_cursor']
    with core.db() as con:
        con.execute("UPDATE recommendation_snapshots SET expires_at='2000-01-01'")
    assert client.get('/api/recommendations', params={'cursor': cursor}).status_code == 409
    for _ in range(7):
        rec.recommend()
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM recommendation_snapshots').fetchone()[0] == 5


def test_semantic_retrieval_and_model_space_isolation(client):
    papers = put_papers({'title': 'Unclassified but semantically relevant'},
                        {'title': 'Unclassified orthogonal experiment'},
                        {'title': 'Unclassified opposite experiment'})
    a, b, c = papers.values()
    preference(topic_ids=['document.ocr'])
    ingest([vector_item(a, [1, 0, 0]), vector_item(b, [0, 1, 0]), vector_item(c, [-1, 0, 0]), vector_item('document.ocr', [1, 0, 0], 'topic')])
    result = rec.recommend()
    assert result['mode'] == 'hybrid'
    assert [p['id'] for p in result['items']] == [a['id']]
    assert result['items'][0]['score_signals']['semantic'] == 1.0
    ingest([vector_item(a, [0, 1, 0]), vector_item(b, [1, 0, 0]), vector_item('document.ocr', [1, 0, 0], 'topic')], space_id='fixture-v2', activate=False)
    assert rec.recommend()['items'][0]['id'] == a['id']
    ingest([vector_item('document.ocr', [1, 0, 0], 'topic')], space_id='fixture-v2')
    assert rec.recommend()['items'][0]['id'] == b['id']
    with pytest.raises(HTTPException) as error:
        ingest([vector_item(a, [1, 0, 0])], model='other-model')
    assert error.value.status_code == 409


def test_semantic_filters_before_topk_and_preserves_multiple_interests(client):
    papers = put_papers({'title': 'OCR test A'}, {'title': 'OCR test B'}, {'title': 'Table structure recognition C'},
                        {'title': 'Unclassified nearest neighbor'})
    a, b, c, outside = papers.values()
    preference(topic_ids=['document.ocr', 'document.table'])
    ingest([vector_item(a, [0.9, 0, 0.1]), vector_item(b, [0.7, 0, 0.3]),
            vector_item(c, [0, 1, 0]), vector_item(outside, [1, 0, 0]),
            vector_item('document.ocr', [1, 0, 0], 'topic'), vector_item('document.table', [0, 1, 0], 'topic')])
    context = RetrievalContext(profiles.get_profile(), 'document')
    found = vectors.EmbeddingProvider().retrieve(context, 2)
    assert {p.catalog_paper_id for p in found} == {a['id'], c['id']}
    result = rec.recommend(category_id='document', topic_id='document.ocr')
    assert [p['id'] for p in result['items']] == [a['id'], b['id']]
    profiles.dismiss(a['id'])
    assert a['id'] not in [p['id'] for p in rec.recommend()['items']]


def test_vector_validation_is_atomic_and_rejects_stale_inputs(client):
    paper = next(iter(put_papers({'title': 'OCR vector', 'arxiv_id': '2601.12345'}).values()))
    good = vector_item(paper, [1, 0, 0])
    for bad in [{**good, 'vector': [1, 0]}, {**good, 'vector': [0, 0, 0]}, {**good, 'input_hash': '0' * 64}, {**good, 'entity_id': 'unknown'}]:
        with pytest.raises(HTTPException):
            ingest([vector_item('document.ocr', [1, 0, 0], 'topic'), bad])
        assert vectors.status()['vectors'] == []
    with pytest.raises(HTTPException):
        ingest([good, good])
    ingest([good, vector_item('document.ocr', [1, 0, 0], 'topic')])
    with core.db() as con:
        blob = con.execute("SELECT vector FROM recommendation_vectors WHERE entity_kind='paper'").fetchone()[0]
        assert np.linalg.norm(np.frombuffer(blob, dtype='<f4')) == pytest.approx(1)
    vectors.store_structured(paper['id'], StructuredInput(source_version='v1', source_document_hash=good['input_hash'], methods=['contrastive']))
    preference(topic_ids=['document.ocr'])
    assert rec.recommend()['embedding_enabled']
    put_papers({'title': 'OCR vector', 'arxiv_id': '2601.12345', 'abstract': 'Newly supplied abstract.'})
    assert not rec.recommend()['embedding_enabled']
    assert vectors.status()['structured_papers'] == 0
    with pytest.raises(HTTPException) as error:
        ingest([good])
    assert error.value.status_code == 409
    updated = client.get('/api/admin/recommendations/embedding-inputs').json()['items'][0]
    assert updated['input_type'] == 'title_abstract' and 'Newly supplied' in updated['text']
    assert updated['input_hash'] != good['input_hash']


def test_local_data_cli_roundtrip_and_explicit_activation(client, tmp_path, capsys):
    from scripts import recommendation_data as cli
    paper = next(iter(put_papers({'title': 'OCR CLI input', 'abstract': 'Recognize printed text.'},
                               {'title': 'No abstract yet'}).values()))
    output = tmp_path / 'inputs.jsonl'
    assert cli.main(['export-inputs', '--output', str(output), '--require-abstract']) == 0
    inputs = list(cli.records(output))
    documents = [p for p in inputs if p['entity_kind'] == 'paper']
    assert len(documents) == 1 and documents[0]['entity_id'] == paper['id']
    assert len([p for p in inputs if p['entity_kind'] == 'topic']) == len(vectors.taxonomy.TOPICS)
    assert cli.main(['export-inputs', '--output', str(output)]) == 1
    # The failed exclusive export leaves the completed input file intact.
    assert list(cli.records(output)) == inputs
    data = tmp_path / 'vectors.jsonl'
    data.write_text(json.dumps(vector_item(paper, [2, 0, 0])) + '\n')
    spec = tmp_path / 'space.json'
    spec.write_text(json.dumps({'space_id': 'cli-v1', 'model': 'fixture', 'model_revision': 'v1', 'dimensions': 3}))
    assert cli.main(['import-vectors', str(data), '--space', str(spec)]) == 0
    assert vectors.status()['active_space'] is None
    assert cli.main(['activate', 'cli-v1']) == 1  # Query vectors still missing.
    data.write_text(json.dumps(vector_item('document.ocr', [1, 0, 0], 'topic')) + '\n')
    assert cli.main(['import-vectors', str(data), '--space', str(spec)]) == 0
    assert cli.main(['activate', 'cli-v1']) == 0
    assert vectors.status()['active_space'] == 'cli-v1'
    content = tmp_path / 'content.jsonl'
    content.write_text(json.dumps({'catalog_paper_id': paper['id'], 'source_document_hash': documents[0]['input_hash'],
                                   'source_version': 'v1', 'methods': ['contrastive alignment']}) + '\n')
    assert cli.main(['import-content', str(content)]) == 0
    assert vectors.status()['structured_papers'] == 1
    assert cli.main(['status']) == 0


def test_admin_export_keyset_and_activation_api(client):
    put_papers({'title': 'Input A'}, {'title': 'Input B'}, {'title': 'Input C'})
    first = client.get('/api/admin/recommendations/embedding-inputs?limit=2').json()
    second = client.get('/api/admin/recommendations/embedding-inputs', params={'limit': 2, 'after': first['next_after']}).json()
    assert len(first['items']) == 2 and len(second['items']) == 1 and second['next_after'] is None
    assert len({p['entity_id'] for p in first['items'] + second['items']}) == 3
    assert client.post('/api/admin/recommendations/spaces/missing/activate').status_code == 404


def test_provider_failures_invalid_ids_and_hidden_private_papers(client):
    put_papers({'title': 'OCR fallback'})
    preference(topic_ids=['document.ocr'])
    class Broken:
        name = 'unavailable'
        def retrieve(self, context, limit):
            raise TimeoutError('sensitive connection details must not reach clients')
    class Invalid:
        name = 'bad-index'
        def retrieve(self, context, limit):
            return [RetrievedPaper('private-pdf-id', .99), RetrievedPaper('missing', float('nan'))]
    result = rec.recommend(providers=(Broken(), Invalid()))
    assert len(result['items']) == 1
    assert result['degraded_sources'] == ['unavailable']
    assert 'sensitive' not in json.dumps(result)
    assert not result['embedding_enabled']


def test_metadata_merge_transfers_dismissals_and_library_identity(client, fake_network):
    papers = put_papers({'title': 'Catalog first', 'doi': '10.1234/fixture'},
                        {'title': 'Catalog second', 'arxiv_id': '2601.12456'})
    a, b = papers.values()
    profiles.dismiss(b['id'])
    plan = client.post('/api/imports', json={'url': 'https://arxiv.org/abs/2601.12456', 'catalog_paper_id': b['id'], 'processing': {'parse': False}})
    assert plan.status_code == 202, plan.text
    drain()
    saved = imports.get_import(plan.json()['id'])['paper_id']
    with core.db() as con:
        assert con.execute('SELECT catalog_paper_id FROM paper_catalog_links WHERE paper_id=?', (saved,)).fetchone()[0] == b['id']
    # Bridge the two stable aliases; metadata and recommendation identities coalesce.
    merged = put_papers({'title': 'Catalog combined', 'doi': '10.1234/fixture', 'arxiv_id': '2601.12456'})
    assert len(merged) == 1
    survivor = next(iter(merged.values()))['id']
    with core.db() as con:
        assert con.execute('SELECT catalog_paper_id FROM paper_catalog_links WHERE paper_id=?', (saved,)).fetchone()[0] == survivor
        assert con.execute('SELECT catalog_paper_id FROM recommendation_dismissals').fetchone()[0] == survivor
    assert rec.recommend()['items'] == []


def test_catalog_identity_is_verified_not_asserted_by_client(client, fake_network):
    papers = put_papers({'title': 'Real identity', 'arxiv_id': '2601.12345'})
    identifier = papers['Real identity']['id']
    wrong = {'url': 'https://arxiv.org/abs/2601.22222', 'catalog_paper_id': identifier, 'title': 'Real identity'}
    assert client.post('/api/imports', json=wrong).status_code == 422
    assert client.post('/api/resolve', json=wrong).status_code == 422
    assert not fake_network
    correct = {**wrong, 'url': 'https://arxiv.org/pdf/2601.12345v2'}
    plan = client.post('/api/imports', json={**correct, 'processing': {'parse': False}}).json()
    drain()
    resolved = client.post('/api/resolve', json=correct)
    assert resolved.status_code == 200 and resolved.json()['paper_id'] == imports.get_import(plan['id'])['paper_id']


def test_sessions_csrf_admin_ingestion_and_cross_account_profiles(workspace):
    owner = workspace
    owner_user = setup(owner)
    member, member_user, _ = invite_user(owner, new_group(owner), 'rec-reader', 'editor')
    assert owner.put('/api/me/recommendation-profile', json={'expected_version': 0, 'topic_ids': ['document.ocr']}).status_code == 200
    assert member.get('/api/me/recommendation-profile').json()['topic_ids'] == []
    assert member.get('/api/admin/recommendations/embedding-inputs').status_code == 403
    token = member.headers.pop('X-CSRF-Token')
    assert member.put('/api/me/recommendation-profile', json={'expected_version': 0}).status_code == 403
    member.headers['X-CSRF-Token'] = token
    assert member.put('/api/me/recommendation-profile', json={'expected_version': 0, 'institutions': ['My lab']}).status_code == 200
    assert owner.get('/api/me/recommendation-profile').json()['institutions'] == []
    with auth.as_user(owner_user['id']):
        put_papers({'title': 'OCR session one'}, {'title': 'OCR session two'})
    result = owner.get('/api/recommendations?limit=1').json()
    assert member.get('/api/recommendations', params={'cursor': result['next_cursor']}).status_code == 409
    assert owner.post('/api/recommendations/' + result['items'][0]['id'] + '/dismiss').status_code == 200
    assert len(member.get('/api/recommendations').json()['items']) == 2
    from app.main import app
    anonymous = TestClient(app)
    assert anonymous.get('/api/recommendations').status_code == 401
