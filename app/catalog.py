"""A provenance-preserving paper index, independent of downloaded personal PDFs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException

from . import core, discovery

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 25000
ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z][a-zA-Z.\-]+/\d{7})(?:v\d+)?\Z")
DOI = re.compile(r"10\.\d{4,9}/\S+\Z", re.I)
SEED_DIR = core.ROOT / "config" / "catalog"


def init_catalog(seed=True):
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_papers (
            id TEXT PRIMARY KEY, data TEXT NOT NULL, search_text TEXT NOT NULL,
            title TEXT NOT NULL, year INTEGER, published_at TEXT, hf_upvotes INTEGER,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_aliases (
            alias TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS catalog_sources (
            paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL, PRIMARY KEY(paper_id,source_id));
        CREATE TABLE IF NOT EXISTS catalog_institutions (
            paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            name TEXT NOT NULL, PRIMARY KEY(paper_id,name));
        CREATE TABLE IF NOT EXISTS catalog_datasets (
            fingerprint TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_imports (
            id TEXT PRIMARY KEY, fingerprint TEXT, name TEXT NOT NULL, status TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0, inserted INTEGER NOT NULL DEFAULT 0,
            updated INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
            error TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS catalog_date ON catalog_papers(year,published_at);
        CREATE INDEX IF NOT EXISTS catalog_votes ON catalog_papers(hf_upvotes);
        CREATE INDEX IF NOT EXISTS catalog_source_lookup ON catalog_sources(source_id,paper_id);
        CREATE INDEX IF NOT EXISTS catalog_institution_lookup ON catalog_institutions(name,paper_id);
        """)
    if seed:
        for path in sorted(SEED_DIR.glob("*.json")):
            try:
                if path.stat().st_size > MAX_BYTES:
                    raise HTTPException(413, "索引文件超过 20 MB")
                import_bytes(path.read_bytes(), path.name, skip_existing=True)
            except HTTPException:
                # One bad seed must not make existing papers or reading unavailable.
                continue


def _text(value, limit=2000):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("文本字段格式无效")
    value = str(value).strip()
    if len(value) > limit or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError("文本字段过长或含控制字符")
    return value


def _url(value):
    if not value:
        return None
    result = discovery.safe_url(_text(value, 4096))
    if not result:
        raise ValueError("链接必须为有效 HTTP(S) URL")
    return result


def _strings(value, limit=100):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        if value.strip().startswith("["):
            value = json.loads(value)
        else:
            value = re.split(r"[;；|]", value)
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("列表字段无效或超过数量限制")
    return list(dict.fromkeys(_text(x, 1024) for x in value if _text(x, 1024)))


def _number(value, limit=1000000000):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise ValueError("计数字段必须为非负整数或 null")
    result = int(value)
    if result > limit:
        raise ValueError("计数字段超过范围")
    return result


def _date(value):
    if value is None or value == "":
        return None
    value = _text(value, 100)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.date().isoformat()
    except ValueError:
        raise ValueError("日期须为 ISO 日期或时间") from None


def _arxiv(value):
    value = _text(value, 500)
    if not value:
        return None
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.hostname not in {"arxiv.org", "export.arxiv.org", "www.arxiv.org", "huggingface.co"}:
            raise ValueError("arXiv 标识链接的域名不支持")
        value = re.sub(r"^/(abs|pdf|html|papers)/", "", parsed.path).removesuffix(".pdf")
    if not ARXIV.fullmatch(value):
        raise ValueError("arXiv 标识无效")
    return re.sub(r"v\d+$", "", value).lower()


def _doi(value):
    value = _text(value, 500).strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    value = unquote(value).removeprefix("doi:").strip().lower()
    if value and not DOI.fullmatch(value):
        raise ValueError("DOI 无效")
    return value or None


def _source(value):
    value = _text(value or "import", 80).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", value):
        raise ValueError("source_id 格式无效")
    return value


def _provenance(value, defaults):
    if value is None:
        value = []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or len(value) > 500:
        raise ValueError("来源记录须为列表且不超过 500 条")
    if not value:
        value = [defaults]
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("来源记录须为对象")
        record = {"source_id": _source(item.get("source_id", item.get("source", defaults.get("source_id")))),
                  "source_url": _url(item.get("source_url", defaults.get("source_url"))),
                  "observed_at": _text(item.get("observed_at", defaults.get("observed_at")), 100) or None}
        for key in ("ranking_week", "week", "sort", "institution_evidence", "dataset_id", "notes", "note"):
            if item.get(key) is not None:
                record[key] = _text(item[key], 2000)
        for key in ("rank", "hf_upvotes", "github_stars"):
            if key in item:
                record[key] = _number(item[key])
        if "institutions" in item:
            record["institutions"] = _strings(item["institutions"])
        if "github_repositories" in item:
            repositories = item["github_repositories"]
            if not isinstance(repositories, list) or len(repositories) > 50:
                raise ValueError("GitHub 仓库来源记录无效")
            record["github_repositories"] = [{"url": _url(repo.get("url")), "stars": _number(repo.get("stars"))} for repo in repositories if isinstance(repo, dict)]
        result.append(record)
    return result


def _canonical_url(url):
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(sorted(query)), ""))


def normalize_row(row, defaults=None):
    if not isinstance(row, dict):
        raise ValueError("每条论文须为对象")
    defaults = defaults or {}
    title = _text(row.get("title", row.get("论文标题")), 2000)
    if not title:
        raise ValueError("缺少论文标题")
    url = _url(row.get("url", row.get("链接")))
    pdf_url = _url(row.get("pdf_url"))
    arxiv_ids = {_arxiv(row.get("arxiv_id"))}
    for link in (url, pdf_url):
        candidate = discovery._arxiv_id(link) if link else None
        if candidate:
            arxiv_ids.add(_arxiv(candidate))
    arxiv_ids.discard(None)
    if len(arxiv_ids) > 1:
        raise ValueError("同一条论文的 arXiv ID 与链接相互冲突")
    arxiv_id = next(iter(arxiv_ids), None)
    doi_ids = {_doi(row.get("doi"))}
    for link in (url, pdf_url):
        if link and urlsplit(link).hostname in {"doi.org", "dx.doi.org"}:
            doi_ids.add(_doi(link))
    doi_ids.discard(None)
    if len(doi_ids) > 1:
        raise ValueError("同一条论文的 DOI 与链接相互冲突")
    doi = next(iter(doi_ids), None)
    if not url:
        url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else f"https://doi.org/{doi}" if doi else None
    if not url:
        raise ValueError("缺少论文链接、arXiv ID 或 DOI")
    published = _date(row.get("published_at", row.get("date")))
    year = _number(row.get("year"), 2100)
    if year is not None and year < 1900:
        raise ValueError("年份无效")
    if published:
        published_year = int(published[:4])
        if year and year != published_year:
            raise ValueError("年份与发表日期冲突")
        year = published_year
    if not year and arxiv_id and re.match(r"\d{4}\.", arxiv_id):
        year = 2000 + int(arxiv_id[:2])
    source = row.get("source_id", row.get("source", defaults.get("source_id", "import")))
    provenance = _provenance(row.get("provenance"), {**defaults, "source_id": source})
    result = {
        "title": title, "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else url,
        "pdf_url": pdf_url or (f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else None),
        "arxiv_id": arxiv_id, "doi": doi, "published_at": published, "year": year,
        "authors": _strings(row.get("authors")), "institutions": _strings(row.get("institutions", row.get("单位"))),
        "abstract": _text(row.get("abstract", row.get("summary")), 30000),
        "hf_upvotes": _number(row.get("hf_upvotes", row.get("点赞量"))),
        "github_url": _url(row.get("github_url")), "github_stars": _number(row.get("github_stars")),
        "provenance": provenance, "source_ids": sorted({p["source_id"] for p in provenance}),
    }
    result["metrics_observed_at"] = {
        key: _metric_observed(provenance, key, result[key])
        for key in ("hf_upvotes", "github_stars") if result[key] is not None
    }
    aliases = ["url:" + _canonical_url(url)]
    if arxiv_id:
        aliases.append("arxiv:" + arxiv_id)
    if doi:
        aliases.append("doi:" + doi)
    return result, aliases


def _dataset(item, name):
    if not isinstance(item, dict):
        raise ValueError("dataset 须为对象")
    result = {key: _text(item.get(key), 3000) for key in ("id", "name", "note", "observed_at", "ranking_week") if item.get(key) is not None}
    result.setdefault("name", name)
    result["source_id"] = _source(item.get("source_id", item.get("source", "import")))
    result["source_url"] = _url(item.get("source_url", item.get("url")))
    result["coverage_start"] = _date(item.get("coverage_start"))
    result["coverage_end"] = _date(item.get("coverage_end"))
    # Completeness must be explicitly documented upstream; never infer from count.
    result["complete"] = item.get("complete") is True
    return result


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _metric_observed(provenance, key, value):
    stamps = []
    for item in provenance:
        if item.get(key) not in (None, value):
            continue
        stamp = item.get("observed_at")
        if not stamp:
            continue
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            stamps.append(parsed.astimezone(timezone.utc).isoformat())
        except (ValueError, TypeError):
            continue
    return max(stamps, default=None)


def _merge(old, new):
    result = dict(old)
    old_stamps, new_stamps = old.get("metrics_observed_at", {}), new.get("metrics_observed_at", {})
    stamps = dict(old_stamps)
    def newer(key):
        return not old_stamps.get(key) or bool(new_stamps.get(key) and new_stamps[key] >= old_stamps[key])
    for key, value in new.items():
        if key == "metrics_observed_at":
            continue
        if key in {"hf_upvotes", "github_stars"}:
            if value is not None and (old.get(key) is None or newer(key)):
                result[key] = value
                stamps[key] = new_stamps.get(key)
            continue
        if key == "github_url":
            if value and (not old.get(key) or newer("github_stars")):
                result[key] = value
            continue
        if key in {"authors", "institutions", "source_ids"}:
            result[key] = list(dict.fromkeys(old.get(key, []) + value))
        elif key == "provenance":
            unique = {_fingerprint(p): p for p in old.get(key, []) + value}
            result[key] = list(unique.values())
        elif value not in (None, "", []):
            # Imports are additive. A newer nonempty metadata value may correct
            # an old value; older snapshots remain inspectable in provenance.
            result[key] = value
    # A count from another repository cannot travel to a replacement URL.
    if result.get("github_url") != old.get("github_url"):
        result["github_stars"] = new.get("github_stars")
        stamps["github_stars"] = new_stamps.get("github_stars")
    result["metrics_observed_at"] = stamps
    return result


def _save_paper(con, identifier, data, now):
    data["id"] = identifier
    search_text = " ".join([data["title"], data["abstract"], *data["authors"], *data["institutions"], data.get("arxiv_id") or "", data.get("doi") or ""]).casefold()
    con.execute("""INSERT INTO catalog_papers(id,data,search_text,title,year,published_at,hf_upvotes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,search_text=excluded.search_text,
        title=excluded.title,year=excluded.year,published_at=excluded.published_at,hf_upvotes=excluded.hf_upvotes,updated_at=excluded.updated_at""",
        (identifier, json.dumps(data, ensure_ascii=False), search_text, data["title"], data["year"], data["published_at"], data["hf_upvotes"], now, now))
    con.execute("DELETE FROM catalog_sources WHERE paper_id=?", (identifier,))
    con.execute("DELETE FROM catalog_institutions WHERE paper_id=?", (identifier,))
    con.executemany("INSERT INTO catalog_sources VALUES(?,?)", [(identifier, s) for s in data["source_ids"]])
    con.executemany("INSERT INTO catalog_institutions VALUES(?,?)", [(identifier, s) for s in data["institutions"]])


def _record_failure(name, message, fingerprint=None):
    with core.db() as con:
        con.execute("INSERT INTO catalog_imports(id,fingerprint,name,status,error,created_at) VALUES(?,?,?,'failed',?,?)", (core.uid(), fingerprint, name, message[:2000], core.now()))


def import_bytes(content, filename="catalog.json", *, skip_existing=False):
    name = Path(filename).name[:200] or "catalog.json"
    digest = hashlib.sha256(content).hexdigest()
    if skip_existing:
        with core.db() as con:
            old = con.execute("SELECT * FROM catalog_imports WHERE fingerprint=? AND status='succeeded' LIMIT 1", (digest,)).fetchone()
            if old:
                return {**dict(old), "reused": True}
    try:
        if not content or len(content) > MAX_BYTES:
            raise ValueError("索引文件为空或超过 20 MB")
        text = content.decode("utf-8-sig")
        if name.lower().endswith(".csv"):
            reader = csv.DictReader(io.StringIO(text))
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) > MAX_ROWS:
                    raise ValueError("单次索引最多 25000 条，请分批导入")
            datasets = [_dataset({}, name)]
        elif name.lower().endswith(".json"):
            data = json.loads(text)
            if isinstance(data, list):
                rows, datasets = data, [_dataset({}, name)]
            elif isinstance(data, dict):
                rows = data.get("papers", data.get("items"))
                raw_datasets = data.get("datasets", [data.get("dataset", {})])
                if not isinstance(raw_datasets, list) or len(raw_datasets) > 1000:
                    raise ValueError("datasets 格式无效")
                datasets = [_dataset(d, name) for d in raw_datasets]
            else:
                raise ValueError("JSON 须为论文数组或包含 papers 的对象")
        else:
            raise ValueError("索引仅支持 UTF-8 JSON 或 CSV")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
            raise ValueError("单次索引须含 1 至 25000 条论文")
        defaults = datasets[0] if len(datasets) == 1 else {}
        normalized = []
        for number, row in enumerate(rows, 1):
            try:
                normalized.append(normalize_row(row, defaults))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"第 {number} 条：{exc}") from None
    except (UnicodeError, ValueError, TypeError, csv.Error, RecursionError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "索引编码或结构无效"
        _record_failure(name, message, digest)
        raise HTTPException(422, message) from None
    now, event_id = core.now(), core.uid()
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    try:
        with core.db() as con:
            con.execute("BEGIN IMMEDIATE")
            for incoming, aliases in normalized:
                found = con.execute("SELECT DISTINCT p.id,p.data FROM catalog_papers p JOIN catalog_aliases a ON a.paper_id=p.id WHERE a.alias IN (" + ",".join("?" for _ in aliases) + ") ORDER BY p.created_at,p.id", aliases).fetchall()
                for key in ("arxiv_id", "doi"):
                    known = {value for value in [incoming.get(key), *[json.loads(r["data"]).get(key) for r in found]] if value}
                    if len(known) > 1:
                        raise HTTPException(422, "同一来源链接存在冲突的论文标识，未导入；请核对该行的 DOI/arXiv ID")
                identifier = found[0]["id"] if found else core.uid()
                old = json.loads(found[0]["data"]) if found else None
                merged = dict(old) if old else {}
                for duplicate in found[1:]:
                    merged = _merge(merged, json.loads(duplicate["data"]))
                    con.execute("UPDATE catalog_aliases SET paper_id=? WHERE paper_id=?", (identifier, duplicate["id"]))
                    con.execute("DELETE FROM catalog_papers WHERE id=?", (duplicate["id"],))
                merged = _merge(merged, incoming) if old else incoming
                merged["id"] = identifier
                if old == merged and len(found) <= 1:
                    counts["unchanged"] += 1
                else:
                    _save_paper(con, identifier, merged, now)
                    counts["updated" if old else "inserted"] += 1
                con.executemany("INSERT OR IGNORE INTO catalog_aliases(alias,paper_id) VALUES(?,?)", [(a, identifier) for a in aliases])
            for dataset in datasets:
                con.execute("INSERT OR IGNORE INTO catalog_datasets(fingerprint,data) VALUES(?,?)", (_fingerprint(dataset), json.dumps(dataset, ensure_ascii=False)))
            con.execute("INSERT INTO catalog_imports(id,fingerprint,name,status,row_count,inserted,updated,unchanged,created_at) VALUES(?,?,?,'succeeded',?,?,?,?,?)", (event_id, digest, name, len(rows), counts["inserted"], counts["updated"], counts["unchanged"], now))
    except HTTPException as exc:
        _record_failure(name, str(exc.detail), digest)
        raise
    return {"id": event_id, "name": name, "status": "succeeded", "row_count": len(rows), **counts, "created_at": now, "datasets": datasets}


def search(q="", year=None, source="all", institution="", sort="votes", page=1, page_size=24):
    where, args = [], []
    for term in q.strip().casefold().split()[:20]:
        where.append("p.search_text LIKE ? ESCAPE '\\'")
        args.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if year is not None:
        where.append("p.year=?")
        args.append(year)
    if source and source != "all":
        where.append("EXISTS(SELECT 1 FROM catalog_sources s WHERE s.paper_id=p.id AND s.source_id=?)")
        args.append(source)
    if institution:
        where.append("EXISTS(SELECT 1 FROM catalog_institutions i WHERE i.paper_id=p.id AND i.name=?)")
        args.append(institution)
    condition = " WHERE " + " AND ".join(where) if where else ""
    orders = {"votes": "p.hf_upvotes IS NULL,p.hf_upvotes DESC,p.published_at DESC,p.id", "published": "p.published_at IS NULL,p.published_at DESC,p.year DESC,p.id", "title": "p.title COLLATE NOCASE,p.id"}
    if sort not in orders:
        raise HTTPException(422, "未知排序")
    with core.db() as con:
        total = con.execute("SELECT count(*) FROM catalog_papers p" + condition, args).fetchone()[0]
        rows = con.execute("SELECT p.data FROM catalog_papers p" + condition + " ORDER BY " + orders[sort] + " LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size]).fetchall()
    return {"items": [json.loads(r[0]) for r in rows], "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


def stats():
    with core.db() as con:
        total = con.execute("SELECT count(*) FROM catalog_papers").fetchone()[0]
        years = [dict(r) for r in con.execute("SELECT year,count(*) count FROM catalog_papers GROUP BY year ORDER BY year DESC")]
        sources = [dict(r) for r in con.execute("SELECT source_id id,count(*) count FROM catalog_sources GROUP BY source_id ORDER BY count DESC,source_id")]
        institutions = [dict(r) for r in con.execute("SELECT name,count(*) count FROM catalog_institutions GROUP BY name ORDER BY count DESC,name")]
        coverage = [json.loads(r[0]) for r in con.execute("SELECT data FROM catalog_datasets ORDER BY rowid DESC LIMIT 1000")]
        last = con.execute("SELECT * FROM catalog_imports ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
        errors = [dict(r) for r in con.execute("SELECT * FROM catalog_imports WHERE status='failed' ORDER BY created_at DESC,rowid DESC LIMIT 10")]
    return {"total": total, "by_year": years, "sources": sources, "institutions": institutions, "coverage": coverage, "last_import": dict(last) if last else None, "import_errors": errors}


def observed_hf_weeks():
    """Return provider weeks with any recorded HF observation, not completeness.

    Bundled indexes may describe many weeks in one dataset. Their per-paper
    provenance is authoritative for which weeks were observed. A partial manual
    import also counts as observed: callers must not call this a full-week list.
    """
    weeks = set()
    with core.db() as con:
        rows = con.execute("SELECT p.data FROM catalog_papers p JOIN catalog_sources s ON s.paper_id=p.id WHERE s.source_id='hf'").fetchall()
        datasets = con.execute("SELECT data FROM catalog_datasets").fetchall()
    observations = [p for row in rows for p in json.loads(row[0]).get("provenance", [])]
    observations.extend(json.loads(row[0]) for row in datasets)
    for item in observations:
        if item.get("source_id") != "hf":
            continue
        week = item.get("ranking_week", item.get("week"))
        if not isinstance(week, str):
            continue
        try:
            weeks.add(validate_week(week))
        except HTTPException:
            continue
    return sorted(weeks)


def validate_week(week):
    if not re.fullmatch(r"202[56]-W\d{2}", week):
        raise HTTPException(422, "周键格式须为 2025-W01 或 2026-W01")
    try:
        date.fromisocalendar(int(week[:4]), int(week[-2:]), 1)
    except ValueError:
        raise HTTPException(422, "周键无效") from None
    return week


async def refresh_hf(week):
    week = validate_week(week)
    source_url = f"https://huggingface.co/papers/week/{week}"
    try:
        rows = await discovery._json("https://huggingface.co/api/daily_papers", params={"week": week, "sort": "trending", "limit": 50, "p": 0})
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise discovery.ProviderError("HF 返回结构发生变化")
        observed = core.now()
        papers = []
        for rank, row in enumerate(rows[:50], 1):
            paper = row.get("paper", row)
            if not isinstance(paper, dict):
                raise discovery.ProviderError("HF 返回结构发生变化")
            identifier = _arxiv(paper.get("id"))
            if not identifier:
                raise discovery.ProviderError("HF 返回论文缺少标识")
            authors = [a.get("name", "") if isinstance(a, dict) else a for a in (paper.get("authors") or [])]
            organization = row.get("organization")
            institution = (organization.get("fullname") or organization.get("name")) if isinstance(organization, dict) else organization if isinstance(organization, str) else None
            institutions = [institution] if institution else []
            votes = paper.get("upvotes", row.get("upvotes"))
            stars = row.get("githubStars", paper.get("githubStars"))
            provenance = {"source_id": "hf", "source_url": source_url, "observed_at": observed, "ranking_week": week, "rank": rank, "sort": "trending", "hf_upvotes": votes, "github_stars": stars, "institutions": institutions, "institution_evidence": "hf-associated-organization" if institutions else "unknown", "notes": "抓取时的周榜排序与指标快照，不是历史冻结点赞量。"}
            papers.append({"title": paper.get("title", row.get("title")), "arxiv_id": identifier, "authors": authors, "abstract": paper.get("summary", ""), "published_at": paper.get("publishedAt"), "institutions": institutions, "hf_upvotes": votes, "github_url": row.get("githubRepo", paper.get("githubRepo")), "github_stars": stars, "provenance": [provenance]})
        if not papers:
            raise discovery.ProviderError("该周没有返回论文；现有索引保持不变")
        data = {"dataset": {"name": "HF 周榜 " + week, "id": "hf:" + week, "source_id": "hf", "source_url": source_url, "ranking_week": week, "observed_at": observed, "complete": False, "note": "最多 50 条 trending 快照；周范围采用 HF 周键，不推断为完整论文全集。"}, "papers": papers}
        return import_bytes(json.dumps(data, ensure_ascii=False).encode(), "hf-" + week + ".json")
    except discovery.ProviderError as exc:
        _record_failure("hf-" + week, str(exc))
        raise HTTPException(502, str(exc)) from None
    except (ValueError, TypeError) as exc:
        _record_failure("hf-" + week, "HF 元数据格式无效，未更新索引")
        raise HTTPException(502, "HF 元数据格式无效，未更新索引") from None
