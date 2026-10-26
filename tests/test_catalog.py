"""Real SQLite catalog imports, provider contract and canonical deduplication."""
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import catalog, core, discovery
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
    assert catalog.stats()['total'] == 6465
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
