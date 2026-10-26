#!/usr/bin/env python3
"""Build a lossless offline catalog from the three audited local snapshots.

No network calls, direction exclusions or year exclusions. Exact normalized
full-title / original arXiv-ID linkage only. Source rows remain inspectable.
Usage: python scripts/build_full_catalog.py --source-root ../ --output config/catalog
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import catalog, discovery

SNAPSHOT = "2026-10-26T14:49:27+08:00"
PENDING_SOURCES = {
    ("ICRA", 2025): "https://github.com/DoongLi/ICRA2025-Paper-List",
    ("IROS", 2026): "https://github.com/gisbi-kim/iros2026-explorer",
}

def clean(value):
    if not value or value in {"—", "-", "未提供", "未获取", "未知"}:
        return ""
    # Markdown backslash escapes and HTML entities are presentation syntax.
    return html.unescape(re.sub(r"\\([_!#*|`])", r"\1", str(value))).strip()


def names(value):
    return list(dict.fromkeys(text for raw in re.split(r"[;；]", value or "") if (text := clean(raw))))


def primary_url(url, shared):
    if not url or url in shared:
        return None
    p = urlsplit(url)
    # Group schedules, spreadsheet/PDF lists and institution directories do not
    # identify an individual paper, even when only one source row uses them.
    if p.hostname in {"docs.google.com", "ras.papercept.net", "aaai.org"}:
        return None
    if p.path.rstrip("/") in {"/research/papers", "/research/publications", "/publications", "/research"}:
        return None
    return url


def normalize(record):
    result, _ = catalog.normalize_row(record)
    # Do not use shared URLs or later inferred HF links as identity assertions.
    result["url_is_identity"] = False
    result["doi_is_identity"] = False
    return result


def merge_all(records):
    parent = list(range(len(records)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a
    known = {}
    for index, paper in enumerate(records):
        keys = ["title:" + catalog.title_key(paper["title"])]
        if paper.get("arxiv_id"):
            keys.append("arxiv:" + paper["arxiv_id"])
        for key in keys:
            if key in known:
                union(index, known[key])
            else:
                known[key] = index
    groups = defaultdict(list)
    for index, paper in enumerate(records):
        groups[find(index)].append(paper)
    result = []
    for group in groups.values():
        paper = group[0]
        for other in group[1:]:
            paper = catalog._merge(paper, other)
        # Retain the richer independent URL and date from earlier sources.
        # Conference source-only rows never erase them.
        paper["url_role"] = "paper" if paper.get("url") or paper.get("pdf_url") else "missing"
        paper["url_is_identity"] = False
        paper["doi_is_identity"] = False
        paper["directions"] = list(dict.fromkeys(paper["directions"]))
        if len(paper["directions"]) > 1:
            paper["directions"] = [label for label in paper["directions"] if label != "未分类"]
        paper.pop("id", None)
        result.append(paper)
    return result


def build(source_root):
    paths = {"hf": source_root / "hf_data/inventory.json", "institution": source_root / "all_data/inventory.json", "conference": source_root / "conference_audit/parsed.json"}
    sources = {key: json.loads(path.read_text()) for key, path in paths.items()}
    hf = sources["hf"]["rows"]
    institutions = sources["institution"]["rows"]
    source_rows = sources["institution"]["source_rows"]
    conferences = sources["conference"]
    urls = defaultdict(set)
    for row in institutions + conferences:
        value = row.get("url") or row.get("source_url")
        if value:
            urls[value].add(catalog.title_key(row["title"]))
    shared = {url for url, titles in urls.items() if len(titles) > 1}
    institution_sources = defaultdict(list)
    for row in source_rows:
        institution_sources[row["paper_index"]].append(row)
    repo_index = {row["repo"].casefold(): row for row in sources["institution"]["repo_rows"]}
    records = []
    for row in hf:
        units = names(row["unit"])
        records.append(normalize({
            "title": clean(row["title"]), "arxiv_id": row["id"], "url": row["arxiv"], "pdf_url": "https://arxiv.org/pdf/" + row["id"],
            "published_at": row["published"], "institutions": units, "hf_upvotes": row["upvotes"], "github_url": row["github"], "github_stars": row["stars"],
            "provenance": [{"source_id": "hf", "source_row_id": f"hf:{row['week']}:{row['rank']}", "source_url": row["weekly_url"], "observed_at": row["retrieved_at"], "ranking_week": row["week"], "rank": row["rank"], "hf_upvotes": row["upvotes"], "github_stars": row["stars"], "institutions": units, "institution_evidence": row["unit_source"] + "；" + row["unit_status"], "notes": "累计点赞与HF仓库Star为2026-10-23采集快照，并非历史周内冻结值。", "raw_record": row}],
        }))
    for row in institutions:
        repo_urls = names(row.get("github", ""))
        repos = []
        for url in repo_urls:
            identity = urlsplit(url).path.strip("/").removesuffix(".git").casefold()
            known = repo_index.get(identity, {})
            repos.append({"url": url, "stars": known.get("stars")})
        scored = [repo for repo in repos if repo["stars"] is not None]
        github = max(scored, key=lambda item: item["stars"]) if scored else (repos[0] if repos else {"url": None, "stars": None})
        url = primary_url(row["url"], shared)
        pdf = primary_url(row["pdf"], shared)
        arxiv_ids = row.get("arxiv_ids", [])
        provenance = []
        for source_row in institution_sources[row["index"]]:
            provenance.append({"source_id": "institution", "source_row_id": f"institution:{source_row['source']}:{source_row['source_index']}", "source_url": source_row["url"], "source_line": source_row["source_line"], "observed_at": "2026-10-24", "hf_upvotes": row["upvotes"], "github_stars": github["stars"], "institutions": row["units"], "github_repositories": repos, "institution_evidence": "原清单目标机构标签，非完整作者署名；未新增核实。", "notes": "保留全部原始记录及后续复筛备注，不按方向或年份删除；日期可能为会议/期刊日期。指标沿用2026-10-24快照，缺值不等于0。", "raw_record": source_row})
        date = row["date"]
        records.append(normalize({"title": clean(row["title"]), "url": url, "pdf_url": pdf,
            "arxiv_id": arxiv_ids[0] if len(arxiv_ids) == 1 else None, "doi": row.get("doi") or None,
            "published_at": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None,
            "year": row["year"], "institutions": row["units"], "hf_upvotes": row["upvotes"],
            "github_url": github["url"], "github_stars": github["stars"], "provenance": provenance}))
    for row in conferences:
        source_url = row["source_url"] or PENDING_SOURCES.get((row["conference"], row["year"]))
        # The source itself distinguishes all-accepted presentations from the
        # selected Oral/Spotlight labels used by other conferences.
        selectivity = "unverified" if row["status"] == "pending" else "regular-presentation" if row["conference"] in {"RSS", "IJCAI", "IROS"} else "award" if row["presentation"] == "Outstanding Paper Award" else "not-established"
        conference = {"conference": row["conference"], "year": row["year"], "presentation": row["presentation"], "track": clean(row["track_or_session"]), "verification_status": row["status"], "selectivity": selectivity, "source_url": source_url}
        records.append(normalize({"title": clean(row["title"]), "url": primary_url(row["source_url"], shared), "authors": names(row["authors"]), "conferences": [conference],
            "provenance": [{"source_id": "conference", "source_row_id": f"conference:{row['conference']}:{row['year']}:{row['status']}:{row['index']}", "source_url": source_url, "source_line": row["source_line"], "observed_at": SNAPSHOT, "conference": row["conference"], "conference_year": row["year"], "presentation": row["presentation"], "track": clean(row["track_or_session"]), "selectivity": selectivity, "verification_status": row["status"], "notes": "确认/候选状态沿用用户附件，未再次独立逐篇核验；来源可能为展示页或整份日程，不必然为全文。", "raw_record": row}]}))
    papers = merge_all(records)
    totals = {"hf_source_rows": len(hf), "institution_papers": len(institutions), "institution_source_rows": len(source_rows), "conference_confirmed_rows": sum(r["status"] == "confirmed" for r in conferences), "conference_pending_rows": sum(r["status"] == "pending" for r in conferences), "source_records": sum(len(p["provenance"]) for p in papers), "unique_papers": len(papers), "direction_removed": 0, "year_removed": 0}
    assert totals == {"hf_source_rows": 4748, "institution_papers": 6134, "institution_source_rows": 8820, "conference_confirmed_rows": 14570, "conference_pending_rows": 4024, "source_records": 32162, "unique_papers": 28919, "direction_removed": 0, "year_removed": 0}, totals
    datasets = [
        {"id": "hf-weekly-2025-2026-20261023", "name": "HF周榜 2025-W01—2026-W43", "source_id": "hf", "source_url": "https://huggingface.co/papers", "observed_at": "2026-10-23", "coverage_start": "2025-01-01", "coverage_end": "2026-10-23", "complete": False, "note": "95周共4748条；首周48条，其余各50。上榜年份与发表年份分开，含2024年发表论文；不声称覆盖2026全年。指标为采集时累计值。"},
        {"id": "institutions-all-20261024", "name": "80家重点机构全量清单及后续来源", "source_id": "institution", "observed_at": "2026-10-24", "coverage_start": "2023-10-21", "coverage_end": "2026-10-21", "complete": False, "note": "6134条原始论文+2686条重合后续来源=8820条来源；不按方向/年份删除。单位为目标机构标签，日期可能为会议/期刊日期。不是机构全部产出的完整统计。"},
        {"id": "conference-2025-2026-20261026", "name": "2025—2026会议展示清单（含待核验）", "source_id": "conference", "observed_at": SNAPSHOT, "complete": False, "note": "14570确认展示记录+4024待核验候选，21个会议系列。确认沿用用户附件，未再次逐篇核验；常规口头展示、精选展示及奖项保留不同标签；ICRA/IROS含期刊参会报告可能性。会议年份不充作发表日期。"},
    ]
    manifest = {"schema_version": 2, "snapshot": SNAPSHOT, "counts": totals, "source_files": [{"dataset": key, "filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for key, path in paths.items()], "coverage": datasets, "identity_policy": "Exact normalized full title or original arXiv ID; no fuzzy matching; no shared-source-URL identity; source observations retained.", "known_identity_review": [{"institution_indices": [1196, 2896], "reason": "Different original titles; later HF enrichment points to same ID 2505.19731. Kept separate pending version verification."}], "quality": {"conference_missing_authors": 815, "pending_without_individual_links": 4024, "directions": "keyword-v1 heuristic-unverified; never used to remove records", "metrics": {"hf_weekly": "2026-10-23", "institutions": "2026-10-24"}}, "shards": []}
    manifest["conference_counts"] = {
        "series": len({row["conference"] for row in conferences}),
        "confirmed_by_year": dict(Counter(str(row["year"]) for row in conferences if row["status"] == "confirmed")),
        "confirmed_labels": dict(Counter(row["presentation"] for row in conferences if row["status"] == "confirmed")),
        "groups": [{"conference": conference, "year": year, "status": status, "records": count}
            for (conference, year, status), count in sorted(Counter((row["conference"], row["year"], row["status"]) for row in conferences).items())],
    }
    manifest["quality"]["selectivity"] = "Only explicit source policy is encoded: RSS/IJCAI/IROS regular presentations; awards; pending unverified; others not-established. Original presentation labels are preserved."
    return papers, datasets, manifest


def write(output, papers, datasets, manifest, max_bytes=900000):
    output.mkdir(parents=True, exist_ok=True)
    # Replace only generated catalog shards. Reviewed-only seed snapshots are
    # superseded; leaving them active would resurrect the old exclusion policy.
    for pattern in ("full-catalog-*.json", "institutions-reviewed-*.json", "hf-weekly-*.json"):
        for old in output.glob(pattern):
            old.unlink()
    def encoded(batch):
        return (json.dumps({"schema_version": 2, "datasets": datasets, "papers": batch}, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    batch, estimate = [], len(encoded([]))
    def flush():
        nonlocal batch, estimate
        if not batch:
            return
        payload = encoded(batch)
        assert len(payload) <= max_bytes
        name = f"full-catalog-{len(manifest['shards'])+1:03d}.json"
        (output / name).write_bytes(payload)
        manifest["shards"].append({"file": name, "bytes": len(payload), "papers": len(batch), "source_records": sum(len(p["provenance"]) for p in batch), "sha256": hashlib.sha256(payload).hexdigest()})
        batch, estimate = [], len(encoded([]))
    for paper in papers:
        size = len(json.dumps(paper, ensure_ascii=False, separators=(",", ":")).encode()) + 1
        if batch and estimate + size > max_bytes:
            flush()
        batch.append(paper)
        estimate += size
    flush()
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT.parent)
    parser.add_argument("--output", type=Path, default=ROOT / "config/catalog")
    args = parser.parse_args()
    papers, datasets, manifest = build(args.source_root)
    write(args.output, papers, datasets, manifest)
    print(json.dumps({**manifest["counts"], "shards": len(manifest["shards"]), "largest_shard_bytes": max(s["bytes"] for s in manifest["shards"])}, ensure_ascii=False))

if __name__ == "__main__":
    main()
