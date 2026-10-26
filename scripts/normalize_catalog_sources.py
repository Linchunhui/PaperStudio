"""Normalize supplied CSV snapshots without direction or year exclusions into the bundled paper discovery index.

Usage: python scripts/normalize_catalog_sources.py --hf-csv HF.csv \
    --institution-csv institutions.csv --output config/catalog

This is a deterministic conversion, not a new affiliation audit or web crawl.
Source publication dates and ranking weeks remain separate. All institution rows are retained. Unknown metrics remain null, never zero.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import html
import unicodedata
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, urlunsplit


def clean(value: str | None) -> str:
    return (value or "").strip()


def number(value: str | None) -> int | None:
    value = clean(value).replace(",", "")
    if not value:
        return None
    if not value.isdigit():
        raise ValueError(f"Invalid metric: {value!r}")
    return int(value)


def url(value: str | None) -> str | None:
    value = clean(value)
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Invalid source URL: {value!r}")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def names(value: str | None) -> list[str]:
    return list(dict.fromkeys(n for part in re.split(r"[;；]", value or "")
                              if (n := clean(part)) and n not in {"未获取", "未找到", "未知", "—", "-"}))


def arxiv(value: str | None) -> str | None:
    value = re.sub(r"v\d+$", "", clean(value))
    if not value:
        return None
    if not re.fullmatch(r"\d{4}\.\d{4,5}", value):
        raise ValueError(f"Invalid arXiv identifier: {value!r}")
    return value


def github_choice(row: dict) -> tuple[str | None, int | None, list[str]]:
    links = [url(v) for v in names(row.get("GitHub链接"))]
    metric = number(row.get("GitHub Star最高值（10月24日）"))
    scores = {}
    for repo, count in re.findall(r"([\w.\-]+/[\w.\-]+):\s*([\d,]+)", row.get("逐仓库Star", "")):
        scores[repo.casefold()] = number(count)
    if scores:
        known = [(link, scores.get(urlsplit(link).path.strip("/").removesuffix(".git").casefold())) for link in links]
        known = [(link, count) for link, count in known if count is not None]
        if known:
            link, count = max(known, key=lambda pair: pair[1])
            return link, count, links
    # A multi-repository maximum cannot truthfully be attached to one arbitrary
    # repository if the source lacks a matching per-repository observation.
    return (links[0] if links else None), (metric if len(links) == 1 else None), links


def hf_record(row: dict) -> dict | None:
    week = clean(row["HF周榜"])
    match = re.fullmatch(r"(20\d{2})-W(\d{2})", week)
    if not match:
        raise ValueError("Invalid HF ranking week")
    date.fromisocalendar(int(match[1]), int(match[2]), 1)
    published = clean(row["论文发表日期"])
    if published:
        date.fromisoformat(published)
    institutions = names(row["单位"])
    return {
        "title": clean(row["论文标题"]), "url": url(row["论文链接"]),
        "arxiv_id": arxiv(row["arXiv ID"]),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv(row['arXiv ID'])}",
        "published_at": published or None, "year": int(published[:4]) if published else None,
        "institutions": institutions, "hf_upvotes": number(row["HF累计点赞"]),
        "github_url": url(row["GitHub链接"]), "github_stars": number(row["GitHub Star（HF记录）"]),
        "provenance": [{
            "source_id": "hf", "source_url": url(row["周榜来源"]),
            "observed_at": clean(row["抓取时间UTC"]), "ranking_week": week,
            "rank": number(row["周内排名"]), "institutions": institutions,
            "institution_evidence": f"{row['单位来源类型']}；{row['单位获取状态']}；{row['单位来源链接']}",
            "notes": f"HF周榜CSV快照；点赞为累计值，Star为HF记录值，均非上榜当周值。HF收录日期：{row['HF收录日期']}。接口：{row['HF接口来源']}。机构为来源标签/自动提取，未重新人工核实。",
        }],
    }


def institution_record(row: dict) -> dict:
    def pick(*keys):
        return next((row[key] for key in keys if key in row), "")
    published = clean(pick("原文日期", "原文发表日期"))
    year = int(published) if re.fullmatch(r"\d{4}", published) else date.fromisoformat(published).year if published else number(pick("日期年份"))
    source_url, pdf_url = url(row.get("论文链接")), url(row.get("PDF链接"))
    directory = source_url and urlsplit(source_url).path.rstrip("/") in {"/research/papers", "/research/publications", "/research", "/publications"}
    canonical_url = pdf_url if directory else source_url
    institutions = names(row.get("目标单位"))
    metric_row = {**row, "GitHub Star最高值（10月24日）": pick("Star最高值（10月24日快照）", "GitHub Star最高值（10月24日）")}
    github, stars, links = github_choice(metric_row)
    return {
        "title": clean(row["论文标题"]), "url": canonical_url, "pdf_url": pdf_url,
        "url_is_identity": False,
        "arxiv_id": arxiv(pick("arXiv ID", "可提取arXiv ID")), "doi": clean(pick("DOI", "可提取DOI")) or None,
        "published_at": published if len(published) == 10 else None, "year": year, "institutions": institutions,
        "hf_upvotes": number(pick("HF点赞（10月24日快照）", "HF累计点赞（10月24日）")),
        "github_url": github, "github_stars": stars,
        "provenance": [{
            "source_id": "institution", "source_url": source_url,
            "source_row_id": "institution-csv:" + pick("论文总表序号", "序号"),
            "observed_at": "2026-10-24", "institutions": institutions,
            "institution_evidence": "用户清单中的目标机构标签，并非完整作者署名。",
            "notes": "全量保留；原有方向/复筛标签只作备注，不用于删除。指标沿用2026-10-24采集快照。",
            "raw_record": row,
        }],
    }


def identity(record: dict) -> str:
    if record.get("arxiv_id"):
        return "arxiv:" + record["arxiv_id"]
    if record.get("doi"):
        return "doi:" + unquote(record["doi"]).casefold()
    title = unicodedata.normalize("NFKC", html.unescape(record["title"])).casefold()
    return "title:" + "".join(char for char in title if char.isalnum())


def merge_records(records: list[dict]) -> list[dict]:
    unique = {}
    for record in records:
        key = identity(record)
        if key not in unique:
            unique[key] = record
            continue
        existing = unique[key]
        if existing["title"].casefold() != record["title"].casefold():
            raise ValueError(f"Canonical identifier has conflicting titles: {key}")
        existing["institutions"] = list(dict.fromkeys(existing["institutions"] + record["institutions"]))
        existing["provenance"].extend(p for p in record["provenance"] if p not in existing["provenance"])
    return list(unique.values())


def load(path: Path, convert) -> tuple[list[dict], int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        source = list(csv.DictReader(handle))
    return merge_records([paper for row in source if (paper := convert(row))]), len(source)


def write_shards(output: Path, prefix: str, records: list[dict], dataset: dict, size=500):
    output.mkdir(parents=True, exist_ok=True)
    # Only replace this converter's own named outputs.
    for old in output.glob(prefix + "-*.json"):
        old.unlink()
    for index in range(0, len(records), size):
        path = output / f"{prefix}-{index // size + 1:02d}.json"
        payload = {"schema_version": 1, "datasets": [dataset], "papers": records[index:index+size]}
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hf-csv", required=True, type=Path)
    parser.add_argument("--institution-csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    hf, hf_input = load(args.hf_csv, hf_record)
    institutions, institution_input = load(args.institution_csv, institution_record)
    overlap = set(map(identity, hf)) & set(map(identity, institutions))
    hf_dataset = {
        "id": "hf-weekly-2025-2026-snapshot-20261023", "name": "HF 周榜 2025–2026 · 2026-10-23 快照",
        "source_id": "hf", "source_url": "https://huggingface.co/papers",
        "observed_at": "2026-10-23", "coverage_start": "2025-01-01", "coverage_end": "2026-10-23", "complete": False,
        "note": f"来源HF周榜_2025-2026.csv（SHA256 {hashlib.sha256(args.hf_csv.read_bytes()).hexdigest()}）。{hf_input}行，规范化{len(hf)}篇；95周，2025-W01为48条，其余94周各50条；2026-W43为周中快照。按榜单周入选，含39篇发表于2024年的论文；发表年份保留原值。点赞/Star均为采集快照，不是历史周内值。机构沿用HF标签或自动提取，676篇未取得机构。不是2026全年完整榜单。",
    }
    institution_dataset = {
        "id": "institutions-all-snapshot-20261024", "name": "重点机构全量论文 · 2026-10-24 指标快照",
        "source_id": "institution", "observed_at": "2026-10-24",
        "coverage_start": "2023-10-21", "coverage_end": "2026-10-21", "complete": False,
        "note": f"输入文件SHA256 {hashlib.sha256(args.institution_csv.read_bytes()).hexdigest()}；原{institution_input}行，保留{len(institutions)}条。无方向或年份删除。机构是目标标签并非完整署名；日期可能为会议/期刊日期；指标是2026-10-24快照。不是机构完整产出统计。",
    }
    write_shards(args.output, "hf-weekly", hf, hf_dataset)
    write_shards(args.output, "institutions-all", institutions, institution_dataset)
    print(json.dumps({"hf_input_rows": hf_input, "hf_papers": len(hf), "institution_input_rows": institution_input,
                      "institution_papers": len(institutions), "canonical_overlap": len(overlap),
                      "unique_papers": len(set(map(identity, hf + institutions)))}, ensure_ascii=False))


if __name__ == "__main__":
    main()

