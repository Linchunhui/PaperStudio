"""Checks against the actual bundled source snapshots, not synthetic coverage."""
from collections import Counter
from datetime import date
import json
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
converter = runpy.run_path(str(ROOT / "scripts" / "normalize_catalog_sources.py"))


@pytest.fixture(scope="module")
def seed():
    bundles = [json.loads(p.read_text()) for p in sorted((ROOT / "config" / "catalog").glob("*.json"))]
    return bundles, [paper for bundle in bundles for paper in bundle["papers"]]


def test_actual_catalog_snapshots_and_coverage(seed):
    bundles, papers = seed
    assert len(papers) == 6465
    counts = Counter(p["provenance"][0]["source_id"] for p in papers)
    assert counts == {"hf": 4748, "institution": 1717}
    assert len({converter["identity"](p) for p in papers}) == len(papers)
    assert all(not d["complete"] for b in bundles for d in b["datasets"])
    assert len({d["id"] for b in bundles for d in b["datasets"]}) == 2
    assert all(p["year"] in {2025, 2026} for p in papers if p["provenance"][0]["source_id"] == "institution")
    # Ranking year is not publication year: preserve genuinely older papers
    # that appeared in the 2025 ranking instead of falsifying their date.
    assert sum(p["year"] == 2024 for p in papers) == 39
    weeks = Counter(p["provenance"][0]["ranking_week"] for p in papers if p["provenance"][0]["source_id"] == "hf")
    assert len(weeks) == 95
    assert weeks["2025-W01"] == 48
    assert all(count == 50 for week, count in weeks.items() if week != "2025-W01")


def test_missing_metrics_and_source_evidence_are_preserved(seed):
    _, papers = seed
    assert any(p["hf_upvotes"] is None for p in papers)
    assert any(p["github_stars"] is None for p in papers)
    assert any(p["github_stars"] == 0 for p in papers)
    assert sum(not p["institutions"] for p in papers if p["provenance"][0]["source_id"] == "hf") == 676
    assert all("未获取" not in p["institutions"] for p in papers)
    assert any(p["published_at"] is None and p["year"] == 2025 for p in papers)
    for paper in papers:
        assert paper["title"] and paper["url"].startswith("https://")
        assert paper["provenance"][0]["source_url"].startswith("https://")
        assert paper["provenance"][0]["institution_evidence"]
        if paper["published_at"]:
            assert date.fromisoformat(paper["published_at"]).year == paper["year"]
    text = json.dumps(papers, ensure_ascii=False)
    assert "/Users/" not in text and "libfile_" not in text


def test_normalizer_chooses_matching_repository_metric_and_missing_values():
    choose = converter["github_choice"]
    assert converter["number"]("") is None
    assert converter["number"]("0") == 0
    row = {"GitHub链接": "https://github.com/a/first; https://github.com/b/second",
           "GitHub Star最高值（10月24日）": "300", "逐仓库Star": "a/first: 10; b/second: 300"}
    assert choose(row)[:2] == ("https://github.com/b/second", 300)
    row["逐仓库Star"] = ""
    assert choose(row)[:2] == ("https://github.com/a/first", None)
    assert converter["names"]("未获取; Lab A; Lab B; Lab A") == ["Lab A", "Lab B"]


def test_conflicting_generic_link_is_never_silently_deduplicated():
    base = {"title": "Paper one", "url": "https://example.org/research", "institutions": [], "provenance": []}
    with pytest.raises(ValueError, match="conflicting titles"):
        converter["merge_records"]([base, {**base, "title": "Different paper"}])
