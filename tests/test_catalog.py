"""Real SQLite catalog imports, provider contract and canonical deduplication."""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import catalog, core, discovery
from app import taxonomy
from app.catalog_routes import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    core.init_db()
    catalog.init_catalog(seed=False)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as value:
        yield value


def upload(client, papers, dataset=None):
    data = {"papers": papers, "dataset": dataset or {"source_id": "hf", "name": "Fixture", "complete": False}}
    return client.post("/api/catalog/import", files={"file": ("index.json", json.dumps(data).encode(), "application/json")})


def record(**fields):
    return {"title": "OCR <script> is data", "arxiv_id": "2501.12345", "published_at": "2025-01-20", "hf_upvotes": 100, "institutions": ["Example Lab"], **fields}


def test_two_level_filters_include_secondary_and_preserve_unknown_records(client):
    records = [
        {"title": "OCR for multilingual documents", "institutions": ["Example Lab"], "year": 2024},
        {"title": "PDF parsing with visual question answering"},
        {"title": "Table structure recognition"},
        {"title": "Unspecified mechanisms"},
    ]
    assert upload(client, records).status_code == 200
    results = client.get("/api/catalog").json()
    assert results["total"] == 4
    query = lambda **params: client.get("/api/catalog", params=params).json()
    assert query(direction="document")["total"] == 3
    assert query(topic="document.layout")["items"][0]["title"] == records[1]["title"]
    assert query(direction="OCR / 文档智能", topic="文字识别与文字图像处理", institution="Example Lab", year=2024)["total"] == 1
    assert query(direction="document", topic="nlp.language")["total"] == 0
    assert query(topic="does-not-exist")["total"] == 0
    assert query(direction="unclassified")["total"] == 1
    stats = client.get("/api/catalog/stats").json()
    categories = stats["direction_taxonomy"]["categories"]
    assert sum(c["count"] for c in categories) == 4
    assert sum(t["count"] for c in categories for t in c["topics"]) == 3
    assert len([c for c in categories if c["id"] != "unclassified"]) == 13
    assert sum(len(c["topics"]) for c in categories) == 76
    assert next(c for c in categories if c["id"] == "document")["match_count"] == 3
    assert stats["source_records"] == 4


def test_existing_catalog_backfill_is_atomic_idempotent_and_preserves_sources(client, monkeypatch):
    rows = [{"title": "OCR evidence", "directions": ["Legacy source tag"], "year": 2023}, {"title": "PDF parsing evidence"}]
    upload(client, rows)
    with core.db() as con:
        con.execute("UPDATE catalog_papers SET data=json_remove(data,'$.classification')")
        con.execute("DELETE FROM catalog_facets WHERE kind != 'direction'")
        before = [dict(r) for r in con.execute("SELECT id,data,created_at,updated_at FROM catalog_papers ORDER BY id")]
    classify = taxonomy.classify
    calls = []
    def fail_second(*args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("interrupted migration")
        return classify(*args)
    monkeypatch.setattr(taxonomy, "classify", fail_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        catalog.init_catalog(seed=False)
    with core.db() as con:
        assert [dict(r) for r in con.execute("SELECT id,data,created_at,updated_at FROM catalog_papers ORDER BY id")] == before
    monkeypatch.setattr(taxonomy, "classify", classify)
    catalog.init_catalog(seed=False)
    after = client.get("/api/catalog").json()["items"]
    for original in before:
        new = next(p for p in after if p["id"] == original["id"])
        assert {k: v for k, v in new.items() if k != "classification"} == json.loads(original["data"])
    assert client.get("/api/catalog", params={"direction": "Legacy source tag"}).json()["total"] == 1
    monkeypatch.setattr(taxonomy, "classify", lambda *a: pytest.fail("already-current rows must not be reclassified"))
    catalog.init_catalog(seed=False)
    assert client.get("/api/catalog").json()["items"] == after
    with core.db() as con:
        assert con.execute("SELECT count(*) FROM papers").fetchone()[0] == 0


def test_new_metadata_reclassifies_without_stale_topic_facets(client):
    row = {"title": "A generic research model", "arxiv_id": "2601.33333"}
    upload(client, [row])
    assert client.get("/api/catalog", params={"direction": "unclassified"}).json()["total"] == 1
    upload(client, [{**row, "abstract": "We study document layout analysis and PDF parsing."}])
    assert client.get("/api/catalog", params={"direction": "unclassified"}).json()["total"] == 0
    assert client.get("/api/catalog", params={"topic": "document.layout"}).json()["total"] == 1
    assert upload(client, [{**row, "abstract": "We study document layout analysis and PDF parsing."}]).json()["unchanged"] == 1


def test_seed_and_upload_are_idempotent_without_touching_library(client, tmp_path, monkeypatch):
    content = json.dumps({"papers": [record()], "dataset": {"source_id": "hf", "name": "Seed", "complete": False}}).encode()
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "index.json").write_bytes(content)
    monkeypatch.setattr(catalog, "SEED_DIR", seeds)
    catalog.init_catalog()
    catalog.init_catalog()
    assert client.get("/api/catalog/stats").json()["total"] == 1
    with core.db() as con:
        assert con.execute("SELECT count(*) FROM papers").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM catalog_imports").fetchone()[0] == 1
    response = client.post("/api/catalog/import", files={"file": ("index.json", content)})
    assert response.json()["unchanged"] == 1
    assert response.json()["inserted"] == 0


def test_arxiv_version_merge_keeps_source_provenance_and_unknown_metrics(client):
    assert upload(client, [record(arxiv_id="2501.12345v1")]).status_code == 200
    data = record(arxiv_id="2501.12345v3", institutions=["Other Lab"], hf_upvotes=None,
                  provenance=[{"source_id": "institution", "source_url": "https://example.org/paper", "institution_evidence": "user-supplied-index"}])
    result = upload(client, [data]).json()
    assert result["updated"] == 1
    row = client.get("/api/catalog").json()["items"][0]
    assert row["arxiv_id"] == "2501.12345" and row["hf_upvotes"] == 100
    assert row["source_ids"] == ["hf", "institution"]
    assert row["institutions"] == ["Example Lab", "Other Lab"]
    assert len(row["provenance"]) == 2 and row["github_stars"] is None


def test_alias_bridge_merges_doi_and_arxiv_existing_rows(client):
    upload(client, [record(), {"title": "same work", "doi": "10.1234/AbC", "institutions": ["Other Lab"]}])
    assert client.get("/api/catalog").json()["total"] == 2
    result = upload(client, [record(doi="https://doi.org/10.1234/abc")])
    assert result.status_code == 200
    rows = client.get("/api/catalog").json()
    assert rows["total"] == 1 and "Other Lab" in rows["items"][0]["institutions"]
    assert upload(client, [{"title": "same work", "url": "https://doi.org/10.1234/ABC"}]).json()["inserted"] == 0


def test_query_identifiers_stay_distinct_but_tracking_deduplicates(client):
    upload(client, [{"title": "a", "url": "https://example.org/paper?id=1"}, {"title": "b", "url": "https://example.org/paper?id=2"}])
    response = upload(client, [{"title": "a", "url": "https://example.org/paper?utm_source=x&id=1"}]).json()
    assert response["inserted"] == 0
    assert client.get("/api/catalog").json()["total"] == 2


def test_search_filters_literal_wildcards_pagination_and_publication_year(client):
    upload(client, [record(title="OCR 100%_Evidence"), record(title="OCR plain", arxiv_id="2409.11111", published_at="2024-09-01", institutions=[], provenance=[{"source_id": "hf", "ranking_week": "2025-W01"}]), record(title="Other", arxiv_id="2603.12345", published_at="2026-03-02", hf_upvotes=None)])
    assert client.get("/api/catalog", params={"q": "%_"}).json()["total"] == 1
    assert client.get("/api/catalog", params={"year": 2024}).json()["items"][0]["title"] == "OCR plain"
    assert client.get("/api/catalog", params={"institution": "Example Lab", "source": "hf", "year": 2025}).json()["total"] == 1
    page = client.get("/api/catalog", params={"page": 2, "page_size": 2}).json()
    assert page["pages"] == 2 and len(page["items"]) == 1
    assert client.get("/api/catalog", params={"page": 0}).status_code == 422


def test_invalid_batch_is_atomic_and_records_failure(client):
    response = upload(client, [record(), record(arxiv_id="2501.11111", url="javascript:alert(1)")])
    assert response.status_code == 422 and "第 2 条" in response.json()["detail"]
    stats = client.get("/api/catalog/stats").json()
    assert stats["total"] == 0 and len(stats["import_errors"]) == 1
    assert stats["last_import"]["status"] == "failed"


def test_conflicting_known_identifiers_roll_back_all_rows(client):
    upload(client, [record(doi="10.1234/one")])
    response = upload(client, [record(arxiv_id="2602.00001", title="new unrelated"), record(arxiv_id="2501.99999", doi="10.1234/one")])
    assert response.status_code == 422
    stats = client.get('/api/catalog/stats').json()
    assert stats['total'] == 1 and stats['last_import']['status'] == 'failed'


def test_csv_unknown_metrics_and_repository_provenance(client):
    csv_data = 'title,url,year,institutions,hf_upvotes\nA,https://arxiv.org/abs/2601.00001,2026,Lab One;Lab Two,\n'
    result = client.post("/api/catalog/import", files={"file": ("papers.csv", csv_data)}).json()
    assert result["inserted"] == 1
    row = client.get("/api/catalog").json()["items"][0]
    assert row["hf_upvotes"] is None and row["institutions"] == ["Lab One", "Lab Two"]
    assert row["source_ids"] == ["import"]


def test_oversize_and_bad_structures_fail_without_partial_writes(client, monkeypatch):
    monkeypatch.setattr(catalog, "MAX_BYTES", 20)
    assert client.post("/api/catalog/import", files={"file": ("large.json", b"x" * 21)}).status_code == 413
    assert client.post("/api/catalog/import", files={"file": ("bad.json", b"{}")}).status_code == 422
    assert client.get("/api/catalog/stats").json()["total"] == 0


def network(monkeypatch, handler):
    original = httpx.AsyncClient
    def factory(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(discovery.httpx, "AsyncClient", factory)


def test_hf_week_refresh_has_verified_parameters_snapshot_and_truthful_coverage(client, monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"paper": {"id": "2409.11111", "title": "Old publication, current weekly list", "publishedAt": "2024-09-01", "upvotes": 23}, "publishedAt": "2025-01-07", "organization": {"name": "example"}, "githubRepo": "https://github.com/example/repo", "githubStars": 12}])
    network(monkeypatch, handler)
    response = client.post("/api/catalog/refresh-hf", json={"week": "2025-W01"})
    assert response.status_code == 200
    assert seen[0].url.path == "/api/daily_papers"
    assert dict(seen[0].url.params) == {"week": "2025-W01", "sort": "trending", "limit": "50", "p": "0"}
    row = client.get("/api/catalog").json()["items"][0]
    assert row["year"] == 2024 and row["hf_upvotes"] == 23
    assert row["provenance"][0]["ranking_week"] == "2025-W01"
    assert row["provenance"][0]["institution_evidence"] == "hf-associated-organization"
    coverage = client.get("/api/catalog/stats").json()["coverage"][0]
    assert coverage["complete"] is False and coverage["coverage_start"] is None


def test_hf_failure_preserves_existing_index_and_redacts_body(client, monkeypatch):
    upload(client, [record()])
    network(monkeypatch, lambda request: httpx.Response(503, text="secret body"))
    response = client.post("/api/catalog/refresh-hf", json={"week": "2026-W43"})
    assert response.status_code == 502 and "secret body" not in response.text
    assert client.get("/api/catalog").json()["total"] == 1
    assert client.post("/api/catalog/refresh-hf", json={"week": "2026-W99"}).status_code == 422
    assert client.post("/api/catalog/refresh-hf", json={"week": "2024-W01"}).status_code == 422


@pytest.mark.parametrize('fields', [
    {'url': 'https://arxiv.org/abs/2501.99999'},
    {'pdf_url': 'https://arxiv.org/pdf/2501.99999'},
    {'doi': '10.1234/a', 'url': 'https://doi.org/10.1234/b'},
])
def test_self_conflicting_identifiers_are_rejected_before_mutation(client, fields):
    response = upload(client, [record(**fields)])
    assert response.status_code == 422
    assert client.get('/api/catalog').json()['total'] == 0


def test_metrics_respect_observation_recency_and_repository_identity(client):
    old = record(hf_upvotes=80, github_url='https://github.com/example/old', github_stars=100,
                 provenance=[{'source_id': 'hf', 'observed_at': '2026-10-20', 'hf_upvotes': 80, 'github_stars': 100}])
    upload(client, [old])
    older = record(hf_upvotes=50, github_url='https://github.com/example/older', github_stars=90,
                   provenance=[{'source_id': 'hf', 'observed_at': '2026-10-01', 'hf_upvotes': 50, 'github_stars': 90}])
    upload(client, [older])
    row = client.get('/api/catalog').json()['items'][0]
    assert row['hf_upvotes'] == 80 and row['github_stars'] == 100
    assert row['github_url'].endswith('/old') and len(row['provenance']) == 2
    newer = record(hf_upvotes=70, github_url='https://github.com/example/new', github_stars=None,
                   provenance=[{'source_id': 'hf', 'observed_at': '2026-10-22', 'hf_upvotes': 70}])
    upload(client, [newer])
    row = client.get('/api/catalog').json()['items'][0]
    assert row['hf_upvotes'] == 70  # Latest count, not historical maximum.
    # Missing repository observation does not override an observed old snapshot.
    assert row['github_url'].endswith('/old') and row['github_stars'] == 100


def test_observed_weeks_include_partial_provenance_without_claiming_completeness(client):
    upload(client, [record(provenance=[{'source_id': 'hf', 'ranking_week': '2025-W03', 'rank': 7}])])
    assert catalog.observed_hf_weeks() == ['2025-W03']
    assert client.get('/api/catalog/stats').json()['coverage'][0]['complete'] is False


def test_cli_resume_skips_actual_seed_95_weeks_without_network(client, monkeypatch, capsys):
    import importlib.util
    import sys
    catalog.init_catalog()
    observed = catalog.observed_hf_weeks()
    assert len(observed) == 95
    assert observed[0] == '2025-W01' and observed[-1] == '2026-W43'
    expected_total = 28919 if (catalog.SEED_DIR / 'manifest.json').exists() else 6465
    assert catalog.stats()['total'] == expected_total
    async def unexpected_network(week):
        raise AssertionError('Resume should reuse the bundled weekly observations')
    monkeypatch.setattr(catalog, 'refresh_hf', unexpected_network)
    script = core.ROOT / 'scripts' / 'update_catalog.py'
    spec = importlib.util.spec_from_file_location('catalog_update_cli_test', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', [str(script), '--from-week', '2025-W01', '--to-week', '2026-W43', '--resume'])
    module.main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 95 and all(row['status'] == 'skipped' for row in lines)



def test_title_only_records_and_shared_directory_never_collapse(client):
    source = {'source_id': 'conference', 'source_url': 'https://example.org/program.pdf', 'source_row_id': 'one'}
    rows = [
        {'title': 'First distinct work', 'url': 'https://example.org/research/papers', 'url_is_identity': False, 'provenance': [source]},
        {'title': 'Second distinct work', 'url': None, 'provenance': [{**source, 'source_row_id': 'two'}]},
    ]
    assert upload(client, rows).status_code == 200
    items = client.get('/api/catalog').json()['items']
    assert len(items) == 2 and all(p['resolve']['action'] == 'search' for p in items)
    assert all(p['url'] is None for p in items)
    assert upload(client, rows).json()['unchanged'] == 2


def test_combined_conference_filters_use_same_observation(client):
    meetings = [
        {'conference': 'ICRA', 'year': 2025, 'presentation': '未保留分类', 'verification_status': 'pending'},
        {'conference': 'IROS', 'year': 2026, 'presentation': 'Oral', 'verification_status': 'confirmed'},
    ]
    response = upload(client, [{'title': 'Robot navigation with a language model', 'conferences': meetings}])
    assert response.status_code == 200
    assert client.get('/api/catalog', params={'conference': 'ICRA', 'verification_status': 'confirmed'}).json()['total'] == 0
    assert client.get('/api/catalog', params={'conference': 'ICRA', 'conference_year': 2025, 'verification_status': 'pending'}).json()['total'] == 1
    assert client.get('/api/catalog', params={'direction': '大语言模型与智能体'}).json()['total'] == 1
    stats = client.get('/api/catalog/stats').json()
    assert stats['source_records'] == 1
    assert {x['name'] for x in stats['conferences']} == {'ICRA', 'IROS'}
    assert {x['name'] for x in stats['verification_statuses']} == {'confirmed', 'pending'}


def test_conference_source_cannot_redate_metrics_or_be_downloaded(client):
    row = record(hf_upvotes=7, provenance=[
        {'source_id': 'hf', 'observed_at': '2026-10-23', 'hf_upvotes': 7},
        {'source_id': 'conference', 'observed_at': '2026-10-26', 'source_url': 'https://aaai.org/wp-content/uploads/2026/01/Main-track-oral-talks.pdf'},
    ])
    assert upload(client, [row, {'title': 'Program-only evidence', 'url': 'https://aaai.org/wp-content/uploads/2026/01/Main-track-oral-talks.pdf', 'pdf_url': 'https://aaai.org/wp-content/uploads/2026/01/Main-track-oral-talks.pdf'}]).status_code == 200
    items = client.get('/api/catalog').json()['items']
    observed = next(p for p in items if p.get('arxiv_id'))
    assert observed['metrics_observed_at']['hf_upvotes'].startswith('2026-10-23')
    unsafe = next(p for p in items if p['title'] == 'Program-only evidence')
    assert unsafe['pdf_url'] is None and unsafe['resolve']['action'] == 'search'


def test_title_identity_and_ids_stable_across_fresh_databases(client, tmp_path, monkeypatch):
    row = {'title': 'A Full Paper: Title!', 'provenance': [{'source_id': 'conference', 'source_row_id': 'c:1'}]}
    upload(client, [row])
    first = catalog.search()['items'][0]['id']
    assert upload(client, [{**row, 'title': 'a full paper title'}]).json()['inserted'] == 0
    monkeypatch.setattr(core, 'DATA_DIR', tmp_path / 'other-db')
    core.init_db()
    catalog.init_catalog(seed=False)
    catalog.import_bytes(json.dumps([row]).encode())
    assert catalog.search()['items'][0]['id'] == first


def test_upgrade_rebuilds_obsolete_seed_aliases_preserves_manual_and_reading(client, tmp_path, monkeypatch):
    import hashlib
    old_dataset = {'id': 'institutions-reviewed-2025-2026-snapshot-20261024', 'source_id': 'institution'}
    obsolete = {'source_id': 'institution', 'notes': '最新复筛CSV第1条；旧版', 'source_url': 'https://example.org/directory'}
    # Simulate a legacy index that merged two different papers through one URL.
    upload(client, [{'title': 'Legacy conflated title', 'url': 'https://example.org/directory', 'provenance': [obsolete, {**obsolete, 'notes': '最新复筛CSV第2条；旧版'}]}], old_dataset)
    upload(client, [{'title': 'User manually curated paper', 'url': 'https://arxiv.org/abs/2301.00001', 'provenance': [{'source_id': 'import', 'notes': 'personal collection'}]}])
    with core.db() as con:
        con.execute("INSERT INTO papers(id,title,filename,sha256,page_count,created_at) VALUES('owned-pdf','Personal PDF','files/existing.pdf','sha',1,?)", (core.now(),))
        con.execute("INSERT INTO project_papers(paper_id,project_id,updated_at) VALUES('owned-pdf','inbox',?)", (core.now(),))
    seeds = tmp_path / 'replacement'
    seeds.mkdir()
    payload = json.dumps({'papers': [
        {'title': 'Actual first paper', 'url': None, 'provenance': [{**obsolete, 'source_row_id': 'full:1'}]},
        {'title': 'Actual second paper', 'url': None, 'provenance': [{**obsolete, 'source_row_id': 'full:2'}]},
    ]}).encode()
    (seeds / 'full-catalog-001.json').write_bytes(payload)
    (seeds / 'manifest.json').write_text(json.dumps({'schema_version': 2, 'shards': [{'file': 'full-catalog-001.json', 'sha256': hashlib.sha256(payload).hexdigest()}]}))
    monkeypatch.setattr(catalog, 'SEED_DIR', seeds)
    catalog.init_catalog()
    titles = {paper['title'] for paper in catalog.search()['items']}
    assert titles == {'Actual first paper', 'Actual second paper', 'User manually curated paper'}
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM catalog_legacy_archive').fetchone()[0] == 1
        assert con.execute("SELECT title FROM papers WHERE id='owned-pdf'").fetchone()[0] == 'Personal PDF'
        assert con.execute("SELECT count(*) FROM project_papers WHERE paper_id='owned-pdf'").fetchone()[0] == 1
    catalog.init_catalog()
    assert catalog.stats()['total'] == 3


def test_corrupt_replacement_manifest_does_not_retire_legacy_catalog(client, tmp_path, monkeypatch):
    upload(client, [{'title': 'Legacy entry', 'provenance': [{'source_id': 'institution', 'notes': '最新复筛CSV第1条'}]}], {'id': 'institutions-reviewed-old'})
    seeds = tmp_path / 'corrupt-seeds'
    seeds.mkdir()
    (seeds / 'full.json').write_text('{"papers": []}')
    (seeds / 'manifest.json').write_text(json.dumps({'schema_version': 2, 'shards': [{'file': 'full.json', 'sha256': 'bad'}]}))
    monkeypatch.setattr(catalog, 'SEED_DIR', seeds)
    catalog.init_catalog()
    assert catalog.search()['items'][0]['title'] == 'Legacy entry'
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM catalog_legacy_archive').fetchone()[0] == 0


def test_no_manifest_keeps_both_legacy_hf_and_reviewed_institution_seeds(client, tmp_path, monkeypatch):
    seeds = tmp_path / 'legacy-seeds'
    seeds.mkdir()
    fixtures = [
        ('hf-weekly-01.json', 'hf-weekly-2025-2026-snapshot-20261023', {'title': 'Legacy HF paper', 'arxiv_id': '2501.01001', 'published_at': '2025-01-02', 'provenance': [{'source_id': 'hf', 'ranking_week': '2025-W01', 'notes': 'HF周榜CSV快照；旧版'}]}),
        ('institutions-reviewed-01.json', 'institutions-reviewed-2025-2026-snapshot-20261024', {'title': 'Legacy institution paper', 'url': 'https://example.org/research/paper-one', 'year': 2025, 'institutions': ['Legacy Lab'], 'provenance': [{'source_id': 'institution', 'notes': '最新复筛CSV第1条；旧版'}]}),
    ]
    for filename, dataset_id, paper in fixtures:
        (seeds / filename).write_text(json.dumps({'schema_version': 1, 'datasets': [{'id': dataset_id, 'source_id': paper['provenance'][0]['source_id'], 'complete': False}], 'papers': [paper]}))
    monkeypatch.setattr(catalog, 'SEED_DIR', seeds)
    catalog.init_catalog()
    catalog.init_catalog()
    stats = catalog.stats()
    assert stats['total'] == 2
    assert {item['id']: item['count'] for item in stats['sources']} == {'hf': 1, 'institution': 1}
    assert not stats['import_errors']
    assert catalog.search(institution='Legacy Lab')['total'] == 1
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM catalog_migrations').fetchone()[0] == 0
        assert con.execute('SELECT count(*) FROM catalog_legacy_archive').fetchone()[0] == 0


def test_malformed_manifest_keeps_existing_catalog_and_does_not_import_unverified_rows(client, tmp_path, monkeypatch):
    upload(client, [{'title': 'Keep existing legacy entry', 'provenance': [{'source_id': 'institution', 'notes': '最新复筛CSV第1条'}]}], {'id': 'institutions-reviewed-old'})
    seeds = tmp_path / 'malformed-manifest-seeds'
    seeds.mkdir()
    (seeds / 'new.json').write_text(json.dumps({'papers': [{'title': 'Unverified replacement'}]}))
    (seeds / 'manifest.json').write_text(json.dumps({'schema_version': 'invalid', 'shards': [{'file': 'new.json', 'sha256': 'bad'}]}))
    monkeypatch.setattr(catalog, 'SEED_DIR', seeds)
    catalog.init_catalog()
    assert [row['title'] for row in catalog.search()['items']] == ['Keep existing legacy entry']
    assert catalog.stats()['import_errors']
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM catalog_legacy_archive').fetchone()[0] == 0
