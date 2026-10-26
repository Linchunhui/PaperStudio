"""A provenance-preserving paper index, independent of downloaded personal PDFs."""
from __future__ import annotations

import csv
import hashlib
import html
import unicodedata
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
        CREATE TABLE IF NOT EXISTS catalog_facets (
            paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(paper_id,kind,value));
        CREATE TABLE IF NOT EXISTS catalog_conferences (
            paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            name TEXT NOT NULL, year INTEGER NOT NULL, presentation TEXT NOT NULL,
            verification_status TEXT NOT NULL, selectivity TEXT NOT NULL,
            PRIMARY KEY(paper_id,name,year,presentation,verification_status));
        CREATE INDEX IF NOT EXISTS catalog_facet_lookup ON catalog_facets(kind,value,paper_id);
        CREATE INDEX IF NOT EXISTS catalog_conference_lookup ON catalog_conferences(name,year,presentation,verification_status,paper_id);
        CREATE TABLE IF NOT EXISTS catalog_migrations (
            id TEXT PRIMARY KEY, details TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS catalog_legacy_archive (
            migration_id TEXT NOT NULL, paper_id TEXT NOT NULL, data TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(migration_id,paper_id));
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
        # Old installations did not have title aliases. Backfill once so new
        # conference observations attach to existing papers without duplicates.
        if not con.execute("SELECT 1 FROM catalog_aliases WHERE alias LIKE 'title:%' LIMIT 1").fetchone():
            for existing in con.execute("SELECT id,title FROM catalog_papers").fetchall():
                con.execute("INSERT OR IGNORE INTO catalog_aliases VALUES(?,?)", ("title:" + title_key(existing["title"]), existing["id"]))
    if seed:
        manifest = SEED_DIR / "manifest.json"
        paths = []
        if manifest.exists():
            try:
                listing = json.loads(manifest.read_text())
                entries = listing.get("shards", [])
                paths = [SEED_DIR / entry["file"] for entry in entries if Path(entry["file"]).name == entry["file"]]
                if len(paths) != len(entries):
                    raise ValueError("种子文件路径无效")
                # Verify the complete replacement before retiring legacy seed
                # observations. User PDFs/projects/notes are never touched.
                verified = all(path.is_file() and path.stat().st_size <= MAX_BYTES and hashlib.sha256(path.read_bytes()).hexdigest() == entry.get("sha256") for path, entry in zip(paths, entries))
                if listing.get("schema_version", 1) >= 2 and paths and verified:
                    _migrate_legacy_seed()
                elif listing.get("schema_version", 1) >= 2:
                    _record_failure("manifest.json", "种子清单校验失败，保留现有目录")
                    return
            except (ValueError, TypeError, KeyError, OSError):
                _record_failure("manifest.json", "种子清单格式无效")
                return
        else:
            # A source-only deployment still ships the existing 14 v1 shards.
            # Keep all of them active until a complete, verified v2 manifest
            # explicitly replaces them; no institution exclusions on upgrade.
            paths = sorted(SEED_DIR.glob("*.json"))
        for path in paths:
            try:
                if path.stat().st_size > MAX_BYTES:
                    raise HTTPException(413, "索引文件超过 20 MB")
                import_bytes(path.read_bytes(), path.name, skip_existing=True)
            except (HTTPException, OSError):
                # One bad seed must not make existing reading unavailable.
                continue


def _migrate_legacy_seed():
    """Retire identifiable obsolete seed observations, archive first.

    The old index merged common institution URLs. Keeping those aliases would
    carry that error into the replacement. We remove only old converter
    observations; manual uploads and later HF refreshes remain active.
    """
    migration = "full-catalog-v2-retire-reviewed-seed"
    with core.db() as con:
        if con.execute("SELECT 1 FROM catalog_migrations WHERE id=?", (migration,)).fetchone():
            return
        datasets = [(row["fingerprint"], json.loads(row["data"])) for row in con.execute("SELECT * FROM catalog_datasets")]
        legacy = [(key, value) for key, value in datasets if str(value.get("id", "")).startswith(("institutions-reviewed-", "hf-weekly-2025-2026-snapshot-"))]
        if not legacy:
            return
        def obsolete(observation):
            note = str(observation.get("notes", ""))
            return not observation.get("source_row_id") and ((observation.get("source_id") == "institution" and note.startswith("最新复筛CSV第")) or (observation.get("source_id") == "hf" and note.startswith("HF周榜CSV快照")))
        retired = retained = 0
        for row in con.execute("SELECT id,data FROM catalog_papers").fetchall():
            old = json.loads(row["data"])
            keep = [p for p in old.get("provenance", []) if not obsolete(p)]
            if len(keep) == len(old.get("provenance", [])):
                continue
            con.execute("INSERT INTO catalog_legacy_archive VALUES(?,?,?,?)", (migration, row["id"], row["data"], core.now()))
            con.execute("DELETE FROM catalog_papers WHERE id=?", (row["id"],))
            retired += 1
            if keep:
                # Metadata survives for observations that the old converter did
                # not own; archive retains the complete pre-migration object.
                data, aliases = normalize_row({**old, "provenance": keep, "url_is_identity": False})
                _save_paper(con, row["id"], data, core.now())
                con.executemany("INSERT OR IGNORE INTO catalog_aliases VALUES(?,?)", [(alias, row["id"]) for alias in aliases])
                retained += 1
        con.executemany("DELETE FROM catalog_datasets WHERE fingerprint=?", [(key,) for key, _ in legacy])
        con.execute("UPDATE catalog_imports SET status='superseded' WHERE name LIKE 'institutions-reviewed-%' OR name LIKE 'hf-weekly-%'")
        details = {"archived_papers": retired, "papers_with_other_observations_preserved": retained, "legacy_datasets": [value for _, value in legacy], "note": "Archived obsolete converter observations only; personal reading tables and files unchanged."}
        con.execute("INSERT INTO catalog_migrations VALUES(?,?,?)", (migration, json.dumps(details, ensure_ascii=False), core.now()))


def title_key(value):
    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    return "".join(char for char in value if char.isalnum())


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
        for key in ("ranking_week", "week", "sort", "institution_evidence", "dataset_id", "notes", "note", "source_row_id", "verification_status", "conference", "presentation", "selectivity", "track", "source_title", "metrics_observed_at"):
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
        if "raw_record" in item:
            raw = item["raw_record"]
            if not isinstance(raw, dict) or len(json.dumps(raw, ensure_ascii=False)) > 50000:
                raise ValueError("原始来源记录无效或过大")
            record["raw_record"] = raw
        for key in ("conference_year", "source_line"):
            if key in item:
                record[key] = _number(item[key])
        result.append(record)
    return result


def _conferences(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("会议展示记录格式无效")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("会议展示记录须为对象")
        status = item.get("verification_status", "pending")
        if status not in {"confirmed", "pending"}:
            raise ValueError("会议核验状态无效")
        year = _number(item.get("year"), 2100)
        if not year or year < 1900:
            raise ValueError("会议年份无效")
        record = {key: _text(item.get(key), 2000) for key in ("conference", "presentation", "track", "selectivity")}
        if not record["conference"]:
            raise ValueError("会议名称不能为空")
        record.update(year=year, verification_status=status, source_url=_url(item.get("source_url")))
        result.append(record)
    return result


def _canonical_url(url):
    parsed = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(sorted(query)), ""))


def _source_only_url(url):
    if not url:
        return False
    parsed = urlsplit(url)
    path = parsed.path.casefold().rstrip("/")
    return parsed.hostname in {"docs.google.com", "ras.papercept.net"} or (parsed.hostname in {"aaai.org", "www.aaai.org"} and ("oral" in path or "schedule" in path)) or path in {"/research/papers", "/research/publications", "/publications", "/research"}


def _reading_target(data):
    pdf = data.get("pdf_url")
    if pdf and not _source_only_url(pdf):
        return pdf
    url = data.get("url")
    if not url or _source_only_url(url):
        return None
    parsed = urlsplit(url)
    if parsed.hostname in {"arxiv.org", "export.arxiv.org", "www.arxiv.org", "huggingface.co", "openreview.net", "aclanthology.org", "openaccess.thecvf.com"} or parsed.path.casefold().endswith(".pdf"):
        return url
    # An individual landing page remains a useful source link, but an unknown
    # provider is searched by title rather than downloaded as a PDF.
    return None


def normalize_row(row, defaults=None):
    if not isinstance(row, dict):
        raise ValueError("每条论文须为对象")
    defaults = defaults or {}
    title = _text(row.get("title", row.get("论文标题")), 2000)
    if not title:
        raise ValueError("缺少论文标题")
    url = _url(row.get("url", row.get("链接")))
    pdf_url = _url(row.get("pdf_url"))
    if _source_only_url(pdf_url):
        pdf_url = None
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
    conferences = _conferences(row.get("conferences"))
    directions = discovery.classify_directions(title, row.get("abstract", row.get("summary", "")))
    if row.get("directions"):
        directions["directions"] = _strings(row["directions"])
        if isinstance(row.get("direction_evidence"), dict):
            directions["direction_evidence"] = row["direction_evidence"]
    url_role = "source-only" if _source_only_url(url) else _text(row.get("url_role", "paper" if url else "missing"), 40)
    if url_role not in {"paper", "source-only", "missing"}:
        raise ValueError("链接角色无效")
    # Source schedules/directories are evidence, never PDF import targets.
    if url_role == "source-only":
        url = None
    result = {
        **directions, "conferences": conferences, "url_role": url_role,
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
    result["source_record_count"] = len(provenance)
    result["url_role"] = "paper" if result.get("url") or result.get("pdf_url") else "missing"
    target = _reading_target(result)
    result["resolve"] = {"action": "resolve" if target else "search", "url": target, "query": title}
    result["title_aliases"] = list(dict.fromkeys([title, *_strings(row.get("title_aliases"))]))
    result["url_is_identity"] = row.get("url_is_identity", True) is True
    result["doi_is_identity"] = row.get("doi_is_identity", True) is True
    aliases = ["title:" + title_key(value) for value in result["title_aliases"]]
    if url and row.get("url_is_identity", True):
        aliases.append("url:" + _canonical_url(url))
    if arxiv_id:
        aliases.append("arxiv:" + arxiv_id)
    if doi and row.get("doi_is_identity", True):
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
    # A later conference observation cannot re-date an older popularity metric.
    explicit = [item for item in provenance if key in item and item[key] == value and value is not None]
    candidates = explicit or [item for item in provenance if key not in item and item.get("source_id") in {"hf", "institution", "import"}]
    stamps = []
    for item in candidates:
        stamp = item.get("metrics_observed_at") or item.get("observed_at")
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
        if key in {"authors", "institutions", "source_ids", "directions", "title_aliases"}:
            result[key] = list(dict.fromkeys(old.get(key, []) + value))
        elif key in {"provenance", "conferences"}:
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
    result["source_record_count"] = len(result.get("provenance", []))
    result["url_role"] = "paper" if result.get("url") or result.get("pdf_url") else "missing"
    target = _reading_target(result)
    result["resolve"] = {"action": "resolve" if target else "search", "url": target, "query": result.get("title", "")}
    return result


def _save_paper(con, identifier, data, now):
    data["id"] = identifier
    search_text = " ".join([data["title"], data["abstract"], *data["authors"], *data["institutions"], data.get("arxiv_id") or "", data.get("doi") or "", *data.get("directions", []), *[c["conference"] for c in data.get("conferences", [])]]).casefold()
    con.execute("""INSERT INTO catalog_papers(id,data,search_text,title,year,published_at,hf_upvotes,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,search_text=excluded.search_text,
        title=excluded.title,year=excluded.year,published_at=excluded.published_at,hf_upvotes=excluded.hf_upvotes,updated_at=excluded.updated_at""",
        (identifier, json.dumps(data, ensure_ascii=False), search_text, data["title"], data["year"], data["published_at"], data["hf_upvotes"], now, now))
    con.execute("DELETE FROM catalog_sources WHERE paper_id=?", (identifier,))
    con.execute("DELETE FROM catalog_institutions WHERE paper_id=?", (identifier,))
    con.executemany("INSERT INTO catalog_sources VALUES(?,?)", [(identifier, s) for s in data["source_ids"]])
    con.executemany("INSERT INTO catalog_institutions VALUES(?,?)", [(identifier, s) for s in data["institutions"]])
    con.execute("DELETE FROM catalog_facets WHERE paper_id=?", (identifier,))
    con.execute("DELETE FROM catalog_conferences WHERE paper_id=?", (identifier,))
    facets = [(identifier, "direction", label) for label in data.get("directions", [])]
    con.executemany("INSERT OR IGNORE INTO catalog_facets VALUES(?,?,?)", facets)
    con.executemany("INSERT OR IGNORE INTO catalog_conferences VALUES(?,?,?,?,?,?)", [(identifier, c["conference"], c["year"], c["presentation"], c["verification_status"], c["selectivity"]) for c in data.get("conferences", [])])


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
                stable_key = next((a for prefix in ("arxiv:", "doi:", "title:") for a in aliases if a.startswith(prefix)), aliases[0])
                identifier = found[0]["id"] if found else "cat_" + hashlib.sha256(stable_key.encode()).hexdigest()[:24]
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


def search(q="", year=None, source="all", institution="", sort="votes", page=1, page_size=24, *, conference="", conference_year=None, presentation="", verification_status="all", direction=""):
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
    conference_terms, conference_args = [], []
    for column, value in (("name", conference), ("year", conference_year), ("presentation", presentation), ("verification_status", verification_status if verification_status != "all" else "")):
        if value:
            conference_terms.append("c." + column + "=?")
            conference_args.append(value)
    if conference_terms:
        where.append("EXISTS(SELECT 1 FROM catalog_conferences c WHERE c.paper_id=p.id AND " + " AND ".join(conference_terms) + ")")
        args.extend(conference_args)
    if direction and direction != "all":
        where.append("EXISTS(SELECT 1 FROM catalog_facets f WHERE f.paper_id=p.id AND f.kind='direction' AND f.value=?)")
        args.append(direction)
    condition = " WHERE " + " AND ".join(where) if where else ""
    orders = {"votes": "p.hf_upvotes IS NULL,p.hf_upvotes DESC,p.published_at DESC,p.id", "published": "p.published_at IS NULL,p.published_at DESC,p.year DESC,p.id", "title": "p.title COLLATE NOCASE,p.id", "stars": "json_extract(p.data,'$.github_stars') IS NULL,json_extract(p.data,'$.github_stars') DESC,p.id"}
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
        directions = [dict(r) for r in con.execute("SELECT value name,count(*) count FROM catalog_facets WHERE kind='direction' GROUP BY value ORDER BY count DESC,value")]
        conference_facets = {}
        for label, column in (("conferences", "name"), ("conference_years", "year"), ("presentations", "presentation"), ("verification_statuses", "verification_status"), ("selectivities", "selectivity")):
            conference_facets[label] = [dict(r) for r in con.execute(f"SELECT {column} name,count(DISTINCT paper_id) count FROM catalog_conferences GROUP BY {column} ORDER BY count DESC,{column}")]
        source_records = con.execute("SELECT COALESCE(sum(json_array_length(data,'$.provenance')),0) FROM catalog_papers").fetchone()[0]
        last = con.execute("SELECT * FROM catalog_imports ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
        errors = [dict(r) for r in con.execute("SELECT * FROM catalog_imports WHERE status='failed' ORDER BY created_at DESC,rowid DESC LIMIT 10")]
    return {"total": total, "source_records": source_records, "by_year": years, "sources": sources, "institutions": institutions, "directions": directions, **conference_facets, "coverage": coverage, "last_import": dict(last) if last else None, "import_errors": errors}


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

