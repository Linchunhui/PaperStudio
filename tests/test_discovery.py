"""Provider contracts, truthful caching and safe failures without live keys."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import core, discovery
from app.discovery_routes import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    for key in ("SEARCH_PROVIDER", "SEARXNG_BASE_URL", "BRAVE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    core.init_db()
    discovery.init_discovery()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


HF = [{"paper": {"id": "2601.12345", "title": "OCR Evidence Reader", "summary": "Document OCR and agents", "authors": [{"name": "Example Author"}], "publishedAt": "2026-01-10T12:00:00Z", "upvotes": 12}}]
RSS = b'''<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>QbitAI</title><item><title>New OCR paper</title><link>https://www.qbitai.com/2026/01/123.html</link><description>&lt;b&gt;New&lt;/b&gt; document evidence</description><pubDate>Fri, 23 Oct 2026 08:00:00 +0800</pubDate><dc:creator>Example</dc:creator><content:encoded><![CDATA[<a href="https://arxiv.org/abs/2601.12345">Paper</a><a href="javascript:alert(1)">bad</a>]]></content:encoded></item><item><title>bad link</title><link>javascript:alert(1)</link></item></channel></rss>'''


def network(monkeypatch, handler):
    """Exercise the real bounded HTTP path with an in-memory transport."""
    original = httpx.AsyncClient
    def factory(**kwargs):
        return original(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(discovery.httpx, "AsyncClient", factory)


def test_web_is_not_silently_replaced_by_arxiv(client):
    r = client.get("/api/discovery/search", params={"q": "OCR", "scope": "web"})
    assert r.status_code == 409 and "全网搜索" in r.json()["detail"]
    config = client.get("/api/discovery/config").json()
    assert config["web_search"] == {"configured": False, "provider": None}
    assert config["sources"][2]["available"] is False


def test_hf_search_and_feed_use_real_contract_and_safe_metadata(client, monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=HF + [{"paper": {"id": "../../bad", "title": "Invalid"}}])
    network(monkeypatch, handler)
    search = client.get("/api/discovery/search", params={"q": "OCR", "scope": "hf"}).json()
    assert seen[0].url.path == "/api/papers/search" and seen[0].url.params["q"] == "OCR"
    assert len(search["items"]) == 1
    item = search["items"][0]
    assert item["arxiv_id"] == "2601.12345" and item["authors"] == ["Example Author"]
    assert item["paper_urls"] == ["https://arxiv.org/abs/2601.12345"]
    assert item["score_signals"]["hf_upvotes"] == 12
    feed = client.get("/api/discovery/feed", params={"sources": "hf", "interests": "OCR"}).json()
    assert seen[1].url.path == "/api/daily_papers"
    assert seen[1].url.params["sort"] == "trending"
    assert feed["items"][0]["score_signals"]["matched_interests"] == ["OCR"]
    assert feed["statuses"][0]["status"] == "ok"


def test_feed_failure_preserves_cache_and_independent_sources(client, monkeypatch):
    failure = False
    def handler(request):
        if request.url.host == "huggingface.co":
            if failure:
                return httpx.Response(503, text="upstream unavailable secret-value")
            return httpx.Response(200, json=HF)
        return httpx.Response(500)
    network(monkeypatch, handler)
    first = client.get("/api/discovery/feed").json()
    statuses = {s["source_id"]: s for s in first["statuses"]}
    assert statuses["hf"]["status"] == "ok"
    assert statuses["qbitai"]["status"] == "error"
    assert statuses["jiqizhixin"]["status"] == "unconfigured"
    failure = True
    second = client.get("/api/discovery/feed", params={"refresh": True}).json()
    stale = next(s for s in second["statuses"] if s["source_id"] == "hf")
    assert stale["status"] == "stale" and stale["fetched_at"] == statuses["hf"]["fetched_at"]
    assert second["items"][0]["id"] == first["items"][0]["id"]
    assert "secret-value" not in json.dumps(second)


def test_cached_request_does_not_repeat_network(client, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=HF)
    network(monkeypatch, handler)
    client.get("/api/discovery/feed", params={"sources": "hf"})
    response = client.get("/api/discovery/feed", params={"sources": "hf"}).json()
    assert len(calls) == 1 and response["statuses"][0]["status"] == "cached"


def test_qbit_rss_keeps_article_distinct_from_paper(client, monkeypatch):
    network(monkeypatch, lambda r: httpx.Response(200, content=RSS))
    data = client.get("/api/discovery/feed", params={"sources": "qbitai"}).json()
    assert len(data["items"]) == 1
    article = data["items"][0]
    assert article["kind"] == "article" and article["arxiv_id"] is None
    assert article["paper_urls"] == ["https://arxiv.org/abs/2601.12345"]
    assert article["snippet"] == "New document evidence"
    assert article["published_at"] == "2026-10-23T00:00:00+00:00"


def test_brave_search_key_stays_server_side_and_paginates(client, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_API_KEY", "not-for-browser")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"web": {"results": [{"title": "Real outside result", "url": "https://example.org/paper", "description": "A result"}, {"title": "XSS", "url": "javascript:alert(1)"}]}})
    network(monkeypatch, handler)
    result = client.get("/api/discovery/search", params={"q": "paper", "page": 2}).json()
    assert len(result["items"]) == 1
    assert seen[0].headers["X-Subscription-Token"] == "not-for-browser"
    assert seen[0].url.params["offset"] == "1"
    assert "not-for-browser" not in json.dumps(result)
    assert "not-for-browser" not in client.get("/api/discovery/config").text
    with core.db() as c:
        cache = " ".join(str(dict(row)) for row in c.execute("SELECT * FROM discovery_cache"))
    assert "not-for-browser" not in cache


def test_searx_and_jiqizhixin_filter_domains_and_publication_date(client, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:8888")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [
            {"title": "Actual article", "url": "https://www.jiqizhixin.com/articles/2020-01-01-1", "publishedDate": "2026-10-23"},
            {"title": "Wrong site", "url": "https://jiqizhixin.com.attacker.test/articles/2026-01-01"},
            {"title": "Membership", "url": "https://www.jiqizhixin.com/data-service"},
        ]})
    network(monkeypatch, handler)
    result = client.get("/api/discovery/feed", params={"sources": "jiqizhixin"}).json()
    assert len(result["items"]) == 1
    assert result["items"][0]["published_at"].startswith("2020-01-01")
    assert result["items"][0]["score_signals"]["freshness_30d"] == 0
    assert seen[0].url.params["format"] == "json"
    assert seen[0].url.params["time_range"] == "month"


def test_arxiv_atom_and_error_are_not_fake_success(client, monkeypatch):
    payload = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2601.12345v2</id><title>Paper title</title><summary>Mechanism</summary><published>2026-01-02T00:00:00Z</published><author><name>A</name></author></entry></feed>'''
    network(monkeypatch, lambda r: httpx.Response(200, content=payload))
    good = client.get("/api/discovery/search", params={"q": "attention", "scope": "arxiv"}).json()
    assert good["items"][0]["arxiv_id"] == "2601.12345v2"
    payload = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad_query</id><title>Error</title></entry></feed>'''
    bad = client.get("/api/discovery/search", params={"q": "bad-other-query", "scope": "arxiv"}).json()
    assert bad["items"] == [] and bad["statuses"][0]["status"] == "error"


@pytest.mark.parametrize("payload", [b"<!DOCTYPE rss [<!ENTITY boom 'text'>]><rss/>", b"<html>Sign in</html>"])
def test_feed_rejects_xml_entities_and_html(client, monkeypatch, payload):
    network(monkeypatch, lambda r: httpx.Response(200, content=payload))
    result = client.get("/api/discovery/feed", params={"sources": "qbitai"}).json()
    assert result["items"] == [] and result["statuses"][0]["status"] == "error"


def test_http_size_limit_and_redirects_are_not_followed(client, monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.host == "www.qbitai.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(200, headers={"content-length": str(discovery.MAX_RESPONSE_BYTES + 1)}, content=b"[]")
    network(monkeypatch, handler)
    result = client.get("/api/discovery/feed", params={"sources": "hf,qbitai"}).json()
    assert len(seen) == 2 and all(s["status"] == "error" for s in result["statuses"])
    assert not any(r.url.host == "127.0.0.1" for r in seen)


def test_dedup_and_ranking_are_transparent_and_leave_cached_rows_unchanged(client):
    a = discovery._item("hf", "OCR paper", "https://huggingface.co/papers/2601.12345", arxiv_id="2601.12345", votes=3)
    b = discovery._item("arxiv", "OCR paper", "https://arxiv.org/abs/2601.12345v2", arxiv_id="2601.12345v2")
    c = discovery._item("qbitai", "Other item", "https://www.qbitai.com/2026/01/1.html")
    rows = discovery._rank(discovery._deduplicate([a, b, c]), ["OCR"])
    assert len(rows) == 2 and rows[0]["source_ids"] == ["hf", "arxiv"]
    assert "匹配关注：OCR" in rows[0]["reasons"]
    assert a["reasons"] == [] and "matched_interests" not in a["score_signals"]


def test_validation_and_invalid_search_configuration_are_safe(client, monkeypatch):
    assert client.get("/api/discovery/search", params={"q": "  "}).status_code == 422
    assert client.get("/api/discovery/feed", params={"sources": "http://localhost/private"}).status_code == 422
    assert client.get("/api/discovery/search", params={"q": "x", "scope": "unknown"}).status_code == 422
    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://host:invalid")
    assert client.get("/api/discovery/search", params={"q": "x"}).status_code == 409
    assert client.get("/api/discovery/config").json()["web_search"]["configured"] is False


def test_failed_searx_engines_are_not_reported_as_zero_matches(client, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://localhost:8888")
    network(monkeypatch, lambda r: httpx.Response(200, json={"results": [], "unresponsive_engines": [["engine", "upstream timeout"]]}))
    result = client.get("/api/discovery/search", params={"q": "OCR"}).json()
    assert result["items"] == [] and result["statuses"][0]["status"] == "error"



def test_direction_filter_marks_heuristics_and_retains_unknown_topic():
    a = discovery._item('hf', 'Robot planning with language model reasoning', 'https://arxiv.org/abs/2601.00001')
    b = discovery._item('web', 'Untitled mechanisms', 'https://example.org/a')
    rows, facets = discovery.direction_view([a, b])
    assert rows[1]['directions'] == ['未分类']
    assert all(p['direction_evidence']['status'] == 'heuristic-unverified' for p in rows)
    selected, _ = discovery.direction_view([a, b], '机器人与具身智能')
    assert [p['id'] for p in selected] == [a['id']]
    assert {f['name'] for f in facets} >= {'未分类', '机器人与具身智能'}


def test_discovery_topics_filter_real_results_and_keep_unfiltered_facets(client, monkeypatch):
    network(monkeypatch, lambda request: httpx.Response(200, json=HF))
    params = {"q": "OCR", "scope": "hf", "direction": "document", "topic": "document.ocr"}
    result = client.get("/api/discovery/search", params=params).json()
    assert len(result["items"]) == 1
    assert result["items"][0]["classification"]["primary"]["topic_id"] == "document.ocr"
    parent = next(c for c in result["direction_taxonomy"]["categories"] if c["id"] == "document")
    assert parent["count"] == 1
    empty = client.get("/api/discovery/search", params={**params, "topic": "document.layout"}).json()
    assert empty["items"] == [] and empty["has_more"]
    assert empty["direction_taxonomy"] == result["direction_taxonomy"]
    assert result["direction_filter_scope"] == "当前返回页"
    feed = client.get("/api/discovery/feed", params={"sources": "hf", "direction": "document", "topic": "document.ocr"}).json()
    assert len(feed["items"]) == 1 and feed["direction_taxonomy"]["version"]
