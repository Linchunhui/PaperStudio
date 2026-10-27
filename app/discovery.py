"""Real discovery providers; source failures never become invented news.

Remote requests are server-side, bounded, and limited to fixed source URLs or
the administrator-configured search endpoint. Search results are data only.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from fastapi import HTTPException

from . import core, taxonomy

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CACHE_SECONDS = 1800
SOURCES = ("hf", "qbitai", "jiqizhixin")
SOURCE_NAMES = {"hf": "Hugging Face", "qbitai": "量子位", "jiqizhixin": "机器之心", "arxiv": "arXiv", "web": "全网搜索"}
ARXIV_RE = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]+/\d{7})(?:v\d+)?\Z")


# These labels are retrieval aids, never a claim of a full-paper review.
DIRECTION_KEYWORDS = {
    "大语言模型与智能体": ("large language", "language model", "llm", "agent", "reasoning", "prompt", "retrieval augmented"),
    "多模态与视觉语言": ("multimodal", "multi-modal", "vision-language", "vision language", "vlm", "visual question", "image caption"),
    "计算机视觉": ("image", "video", "visual", "segmentation", "object detection", "3d", "gaussian splatting", "optical flow"),
    "生成模型": ("diffusion", "generative", "generation", "flow matching", "autoregressive", "synthesis"),
    "机器人与具身智能": ("robot", "robotic", "manipulation", "grasp", "embodied", "locomotion", "navigation", "vla"),
    "强化学习与决策": ("reinforcement learning", "policy optimization", "policy gradient", "bandit", "planning", "decision making"),
    "自然语言处理": ("language", "translation", "text", "linguistic", "sentiment", "named entity", "dialogue"),
    "机器学习理论与优化": ("optimization", "convergence", "generalization", "generalisation", "learning theory", "statistical", "gradient", "kernel"),
    "模型效率与系统": ("efficient", "efficiency", "quantization", "compression", "pruning", "acceleration", "serving", "distributed training"),
    "安全、对齐与隐私": ("safety", "alignment", "privacy", "adversarial", "jailbreak", "fairness", "unlearning", "backdoor"),
    "数据、评测与基准": ("dataset", "benchmark", "evaluation", "evaluating", "data curation", "data selection"),
    "科学与医疗AI": ("medical", "clinical", "molecule", "molecular", "protein", "drug", "scientific", "material", "healthcare"),
    "语音与音频": ("speech", "audio", "acoustic", "music", "sound"),
    "图学习与推荐": ("graph neural", "graph learning", "recommendation", "recommender", "knowledge graph"),
    "时间序列与预测": ("time series", "time-series", "forecasting", "temporal prediction"),
}


def classify_directions(title, abstract=""):
    text = (str(title or "") + " " + str(abstract or "")).casefold()
    matched = {label: [word for word in words if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text)] for label, words in DIRECTION_KEYWORDS.items()}
    matched = {label: words for label, words in matched.items() if words}
    return {"directions": list(matched) or ["未分类"], "direction_evidence": {
        "method": "keyword-v1", "status": "heuristic-unverified", "input": "title-and-abstract" if abstract else "title",
        "matched_keywords": sorted({word for words in matched.values() for word in words}),
        "note": "依据标题/已有摘要的关键词初标，待人工确认；未按方向移除论文。"}}


def direction_view(items, direction="", topic=""):
    enriched = [{**item, **classify_directions(item.get("title"), item.get("snippet", item.get("abstract", "")))} for item in items]
    for item in enriched:
        item["classification"] = taxonomy.classify(item.get("title"), item.get("snippet", item.get("abstract", "")), item["directions"])
    counts = {}
    for item in enriched:
        for label in item["directions"]:
            counts[label] = counts.get(label, 0) + 1
    return [item for item in enriched if taxonomy.matches(item, direction, topic)], [{"name": label, "count": count} for label, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]


class ProviderError(Exception):
    """Deliberately contains no response body, URL parameters or API key."""


def init_discovery() -> None:
    with core.db() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS discovery_cache (
            cache_key TEXT PRIMARY KEY, items TEXT NOT NULL,
            fetched_at TEXT NOT NULL, expires_at REAL NOT NULL
        )""")


def _search_settings() -> tuple[str, str, str]:
    provider = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    base = os.environ.get("SEARXNG_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if provider == "searxng":
        try:
            parsed = urlsplit(base)
            _ = parsed.port
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise HTTPException(409, "请在服务端设置有效的 SEARXNG_BASE_URL，并在 SearXNG 中启用 JSON 格式。")
    elif provider == "brave":
        if not key:
            raise HTTPException(409, "请在服务端设置 BRAVE_API_KEY 后使用全网搜索。")
    else:
        raise HTTPException(409, "全网搜索尚未配置：设置 SEARCH_PROVIDER=searxng 或 brave 及对应服务配置。arXiv 和 HF 搜索仍可使用。")
    return provider, base, key


def public_config() -> dict:
    provider = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    try:
        _search_settings()
        configured = True
    except (HTTPException, ValueError):
        configured = False
    return {
        "web_search": {"configured": configured, "provider": provider if provider in {"searxng", "brave"} else None},
        "scopes": ["web", "arxiv", "hf"],
        "sources": [
            {"id": "hf", "name": "Hugging Face", "available": True, "mode": "official-api"},
            {"id": "qbitai", "name": "量子位", "available": True, "mode": "public-rss"},
            {"id": "jiqizhixin", "name": "机器之心", "available": configured, "mode": "site-search", "note": "通过搜索服务检索公开网站文章，非完整微信公众号消息流"},
        ],
    }


def safe_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return None
    try:
        p = urlsplit(value.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        _ = p.port
        return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))
    except ValueError:
        return None


def _text(value, limit=2000) -> str:
    if not isinstance(value, (str, int, float)):
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(value)))).strip()[:limit]


def _date(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat()


def _arxiv_id(url: str) -> str | None:
    try:
        p = urlsplit(url)
        if p.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org", "huggingface.co"}:
            return None
        raw = re.sub(r"^/(abs|pdf|html|papers)/", "", p.path).removesuffix(".pdf")
        return raw if ARXIV_RE.fullmatch(raw) else None
    except ValueError:
        return None


def _item(source: str, title, url, *, kind="article", snippet="", authors=None, published=None, arxiv_id=None, votes=0, paper_urls=None) -> dict | None:
    url, title = safe_url(url), _text(title, 500)
    if not url or not title:
        return None
    arxiv_id = arxiv_id if isinstance(arxiv_id, str) and ARXIV_RE.fullmatch(arxiv_id) else _arxiv_id(url)
    links = [link for value in (paper_urls or []) if (link := safe_url(value))]
    if arxiv_id:
        kind = "paper"
        links.insert(0, f"https://arxiv.org/abs/{arxiv_id}")
    try:
        votes = max(0, min(10000000, int(votes)))
    except (ValueError, TypeError, OverflowError):
        votes = 0
    return {
        "id": hashlib.sha256(f"{source}:{url}".encode()).hexdigest()[:24],
        "kind": kind, "title": title, "url": url, "source_id": source,
        "source_ids": [source], "published_at": _date(published),
        "snippet": _text(snippet), "authors": [_text(a, 100) for a in (authors or []) if _text(a, 100)][:30],
        "arxiv_id": arxiv_id, "paper_urls": list(dict.fromkeys(links))[:20],
        "score_signals": {"hf_upvotes": votes}, "reasons": [],
    }


async def _request_bytes(url: str, *, params=None, headers=None) -> bytes:
    async def request():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=5), follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, params=params, headers={"User-Agent": "PaperStudio/0.2 (+personal research reader)", **(headers or {})}) as response:
                    if response.status_code >= 300:
                        raise ProviderError(f"来源服务返回 HTTP {response.status_code}")
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                        raise ProviderError("来源响应超过大小限制")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ProviderError("来源响应超过大小限制")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise ProviderError("来源请求超时，请稍后刷新") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接来源服务") from exc
    try:
        return await asyncio.wait_for(request(), timeout=20)
    except TimeoutError as exc:
        raise ProviderError("来源请求超时，请稍后刷新") from exc


async def _json(url, **kwargs):
    try:
        return json.loads(await _request_bytes(url, **kwargs))
    except (ValueError, UnicodeError) as exc:
        raise ProviderError("来源返回了无效 JSON") from exc


def _xml(content: bytes):
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ProviderError("不支持含实体声明的来源 XML")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ProviderError("来源返回了无效 RSS/Atom") from exc


def _hf_items(data) -> list[dict]:
    if not isinstance(data, list):
        raise ProviderError("HF 返回结构发生变化")
    result = []
    for row in data[:100]:
        if not isinstance(row, dict):
            continue
        paper = row.get("paper", row)
        if not isinstance(paper, dict):
            continue
        identifier = str(paper.get("id", ""))
        if not ARXIV_RE.fullmatch(identifier):
            continue
        authors = [a.get("name", "") if isinstance(a, dict) else a for a in paper.get("authors", []) or []]
        item = _item("hf", paper.get("title", row.get("title")), f"https://huggingface.co/papers/{identifier}", kind="paper", snippet=paper.get("summary", ""), authors=authors, published=paper.get("publishedAt", row.get("publishedAt")), arxiv_id=identifier, votes=paper.get("upvotes", row.get("upvotes", 0)))
        if item:
            result.append(item)
    return result


async def _hf(query=None, page=1):
    if query:
        # HF search currently exposes limit, not pagination. Retrieve a bounded
        # prefix and slice locally; do not pretend it supports arbitrary pages.
        if page > 5:
            return []
        data = await _json("https://huggingface.co/api/papers/search", params={"q": query, "limit": page * 20})
        return _hf_items(data)[(page - 1) * 20:page * 20]
    return _hf_items(await _json("https://huggingface.co/api/daily_papers", params={"sort": "trending", "limit": 50}))


async def _arxiv(query, page):
    content = await _request_bytes("https://export.arxiv.org/api/query", params={"search_query": "all:" + query, "start": (page - 1) * 20, "max_results": 20, "sortBy": "relevance"})
    root = _xml(content)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ProviderError("arXiv 未返回有效 Atom")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("a:entry", ns):
        url = entry.findtext("a:id", default="", namespaces=ns)
        if "arxiv.org/api/errors" in url:
            raise ProviderError("arXiv 无法处理本次查询")
        item = _item("arxiv", entry.findtext("a:title", default="", namespaces=ns), url, kind="paper", snippet=entry.findtext("a:summary", default="", namespaces=ns), authors=[a.text or "" for a in entry.findall("a:author/a:name", ns)], published=entry.findtext("a:published", namespaces=ns))
        if item:
            items.append(item)
    return items


async def _web(query, page=1, *, fresh=False, source="web"):
    provider, base, key = _search_settings()
    if provider == "searxng":
        data = await _json(base + "/search", params={"q": query, "format": "json", "pageno": page, **({"time_range": "month"} if fresh else {})})
        rows = data.get("results") if isinstance(data, dict) else None
        if isinstance(data, dict) and not rows and data.get("unresponsive_engines"):
            raise ProviderError("上游搜索引擎暂时不可用")
    else:
        if page > 10:
            return []
        data = await _json("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 20, "offset": page - 1, **({"freshness": "pm"} if fresh else {})}, headers={"X-Subscription-Token": key})
        if isinstance(data, dict) and data.get("error"):
            raise ProviderError("搜索服务未能完成本次查询")
        rows = data.get("web", {}).get("results", []) if isinstance(data, dict) and isinstance(data.get("web", {}), dict) else None
    if not isinstance(rows, list):
        raise ProviderError("搜索服务返回结构发生变化")
    items = []
    for row in rows[:50]:
        if not isinstance(row, dict):
            continue
        item = _item(source, row.get("title"), row.get("url"), snippet=row.get("content", row.get("description", "")), published=row.get("publishedDate", row.get("page_age")))
        if not item:
            continue
        if source == "jiqizhixin":
            parsed = urlsplit(item["url"])
            if parsed.hostname not in {"jiqizhixin.com", "www.jiqizhixin.com"} or not parsed.path.startswith("/articles/"):
                continue
            # Search-result crawl dates are not publication dates. Historical
            # article URLs carry a date; use it instead when present.
            match = re.match(r"/articles/(\d{4}-\d{2}-\d{2})(?:-|$)", parsed.path)
            item["published_at"] = _date(match.group(1)) if match else None
        items.append(item)
    return items


async def _qbitai():
    root = _xml(await _request_bytes("https://www.qbitai.com/feed"))
    if root.tag not in {"rss", "{http://www.w3.org/2005/Atom}feed"}:
        raise ProviderError("量子位未返回有效 RSS")
    items = []
    for row in root.findall("./channel/item")[:60]:
        text = row.findtext("description", "")
        content = row.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", text)
        links = re.findall(r'''href\s*=\s*["'](https?://[^"']+)["']''', content or "", flags=re.I)
        paper_urls = [html.unescape(u) for u in links if _arxiv_id(html.unescape(u))]
        item = _item("qbitai", row.findtext("title", ""), row.findtext("link", ""), snippet=text, published=row.findtext("pubDate"), authors=[row.findtext("{http://purl.org/dc/elements/1.1/}creator", "量子位")], paper_urls=paper_urls)
        if item:
            items.append(item)
    return items


def _cache_key(source, query="", page=1):
    # Bind web caches to configuration without storing credentials or full URLs.
    settings = ""
    if source in {"web", "jiqizhixin"}:
        settings = "|".join(_search_settings())
    return hashlib.sha256(json.dumps([source, query, page, settings], ensure_ascii=False).encode()).hexdigest()


def _cached(key):
    with core.db() as connection:
        row = connection.execute("SELECT * FROM discovery_cache WHERE cache_key=?", (key,)).fetchone()
    if not row:
        return None
    try:
        return {**dict(row), "items": json.loads(row["items"])}
    except (ValueError, TypeError):
        return None


async def _load(source, fetcher, *, query="", page=1, refresh=False):
    try:
        key = _cache_key(source, query, page)
    except (HTTPException, ValueError):
        return [], {"source_id": source, "status": "unconfigured", "fetched_at": None, "count": 0, "error": "此来源需要已配置的全网搜索服务。"}
    cached = _cached(key)
    if cached and not refresh and cached["expires_at"] > datetime.now(timezone.utc).timestamp():
        return cached["items"], {"source_id": source, "status": "cached", "fetched_at": cached["fetched_at"], "count": len(cached["items"])}
    try:
        items = await fetcher()
        stamp = core.now()
        with core.db() as connection:
            connection.execute("INSERT OR REPLACE INTO discovery_cache(cache_key,items,fetched_at,expires_at) VALUES(?,?,?,?)", (key, json.dumps(items, ensure_ascii=False), stamp, datetime.now(timezone.utc).timestamp() + CACHE_SECONDS))
        return items, {"source_id": source, "status": "ok", "fetched_at": stamp, "count": len(items)}
    except (ProviderError, HTTPException, ValueError, TypeError, KeyError):
        # The response body and provider error may contain credentials or query
        # data; return a fixed user-safe error and retain last successful data.
        items = cached["items"] if cached else []
        return items, {"source_id": source, "status": "stale" if cached else "error", "fetched_at": cached["fetched_at"] if cached else None, "count": len(items), "error": "来源更新失败，显示上次成功缓存。" if cached else "来源暂时不可用，请稍后刷新。"}


def _deduplicate(items):
    result = {}
    for value in items:
        item = {**value, "source_ids": list(value.get("source_ids", [value["source_id"]])), "paper_urls": list(value.get("paper_urls", [])), "score_signals": dict(value.get("score_signals", {})), "reasons": list(value.get("reasons", []))}
        key = "arxiv:" + re.sub(r"v\d+$", "", item["arxiv_id"]) if item.get("arxiv_id") else item["url"].rstrip("/")
        if key not in result:
            result[key] = item
        else:
            old = result[key]
            old["source_ids"] = list(dict.fromkeys(old["source_ids"] + item["source_ids"]))
            old["paper_urls"] = list(dict.fromkeys(old["paper_urls"] + item["paper_urls"]))
            old["score_signals"]["hf_upvotes"] = max(old["score_signals"].get("hf_upvotes", 0), item["score_signals"].get("hf_upvotes", 0))
    return list(result.values())


def _rank(items, interests):
    now = datetime.now(timezone.utc)
    terms = list(dict.fromkeys(t.strip()[:80] for t in interests if t.strip()))[:20]
    for item in items:
        haystack = (item["title"] + " " + item["snippet"]).casefold()
        matched = [term for term in terms if term.casefold() in haystack]
        votes = item["score_signals"].get("hf_upvotes", 0)
        freshness = 0
        if item.get("published_at"):
            age = max(0, (now - datetime.fromisoformat(item["published_at"])).total_seconds() / 86400)
            freshness = max(0, 1 - age / 30)
        score = len(matched) * 10 + freshness * 2 + math.log1p(votes) * .2
        item["score_signals"].update({"matched_interests": matched, "freshness_30d": round(freshness, 3), "rule_score": round(score, 3)})
        item["reasons"] = (["匹配关注：" + "、".join(matched)] if matched else []) + ([f"HF 社区 {votes} 次赞"] if votes else []) + (["近 30 天发布"] if freshness > 0 else [])
        if not item["reasons"]:
            item["reasons"] = ["来自关注来源：" + SOURCE_NAMES.get(item["source_id"], item["source_id"])]
    return sorted(items, key=lambda x: (x["score_signals"]["rule_score"], x.get("published_at") or ""), reverse=True)


async def search(query: str, scope: str, page: int, direction: str = "", topic: str = "") -> dict:
    if scope == "web":
        _search_settings()  # Missing configuration is an actionable HTTP 409.
        fetcher = lambda: _web(query, page)
    elif scope == "hf":
        fetcher = lambda: _hf(query, page)
    else:
        fetcher = lambda: _arxiv(query, page)
    items, status = await _load(scope, fetcher, query=query, page=page)
    items, facets = direction_view(_deduplicate(items))
    hierarchy = taxonomy.item_snapshot(items)
    has_more = bool(items)
    items = [item for item in items if taxonomy.matches(item, direction, topic)]
    return {"items": items, "directions": facets, "direction_taxonomy": hierarchy, "direction_filter_scope": "当前返回页", "statuses": [status], "query": query, "page": page, "has_more": has_more}


async def feed(sources: list[str], interests: list[str], limit: int, refresh: bool, direction: str = "", topic: str = "") -> dict:
    async def one(source):
        fetcher = {"hf": _hf, "qbitai": _qbitai, "jiqizhixin": lambda: _web("site:jiqizhixin.com/articles 论文", fresh=True, source="jiqizhixin")}[source]
        return await _load(source, fetcher, refresh=refresh)
    results = await asyncio.gather(*(one(source) for source in dict.fromkeys(sources)))
    items = _deduplicate([item for rows, _ in results for item in rows])
    items, facets = direction_view(items)
    hierarchy = taxonomy.item_snapshot(items)
    items = [item for item in items if taxonomy.matches(item, direction, topic)]
    return {"items": _rank(items, interests)[:limit], "directions": facets, "direction_taxonomy": hierarchy, "direction_filter_scope": "当前来源返回集合", "statuses": [status for _, status in results], "fetched_at": core.now()}

