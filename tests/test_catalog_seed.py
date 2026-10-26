"""Audit real bundled rows, provenance, identity and safe import targets."""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import runpy
import pytest

ROOT = Path(__file__).resolve().parents[1]
converter = runpy.run_path(str(ROOT / "scripts" / "normalize_catalog_sources.py"))

@pytest.fixture(scope="module")
def seed():
    folder = ROOT / "config/catalog"
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    bundles = []
    if manifest is not None:
        for shard in manifest["shards"]:
            content = (folder / shard["file"]).read_bytes()
            assert len(content) == shard["bytes"] < 1000000
            assert hashlib.sha256(content).hexdigest() == shard["sha256"]
            bundles.append(json.loads(content))
    else:
        # Remote source-only releases retain the original 14 data shards.
        # Validate them, rather than skipping the entire catalog suite.
        paths = sorted(folder.glob("*.json"))
        assert len(paths) == 14
        assert len([p for p in paths if p.name.startswith("hf-weekly-")]) == 10
        assert len([p for p in paths if p.name.startswith("institutions-reviewed-")]) == 4
        bundles = [json.loads(path.read_text()) for path in paths]
    return manifest, bundles, [paper for bundle in bundles for paper in bundle["papers"]]


@pytest.fixture(scope="module")
def full_seed(seed):
    if seed[0] is None:
        pytest.skip("Full v2 dataset is not bundled in this source-only release; legacy HF/institution coverage is tested separately.")
    return seed


def test_actual_catalog_snapshots_and_lossless_source_coverage(seed):
    manifest, bundles, papers = seed
    if manifest is None:
        assert len(papers) == 6465
        assert Counter(p["provenance"][0]["source_id"] for p in papers) == {"hf": 4748, "institution": 1717}
        assert len({converter["identity"](p) for p in papers}) == 6465
        assert all(not d["complete"] for b in bundles for d in b["datasets"])
        assert len({d["id"] for b in bundles for d in b["datasets"]}) == 2
        assert sum(p["year"] == 2024 for p in papers) == 39
        weeks = Counter(p["provenance"][0]["ranking_week"] for p in papers if p["provenance"][0]["source_id"] == "hf")
        assert len(weeks) == 95 and weeks["2025-W01"] == 48
        assert all(n == 50 for week, n in weeks.items() if week != "2025-W01")
        return
    assert len(papers) == 28919
    provenance = [p for row in papers for p in row["provenance"]]
    assert len(provenance) == 32162
    assert Counter(p["source_id"] for p in provenance) == {"hf": 4748, "institution": 8820, "conference": 18594}
    assert len({p["source_row_id"] for p in provenance}) == len(provenance)
    assert all(p["raw_record"] for p in provenance)
    assert all(not d["complete"] for b in bundles for d in b["datasets"])
    assert len({d["id"] for b in bundles for d in b["datasets"]}) == 3
    assert manifest["counts"]["direction_removed"] == manifest["counts"]["year_removed"] == 0
    institution = [p for p in papers if "institution" in p["source_ids"]]
    assert len(institution) == 6134
    assert Counter(p["year"] for p in institution) == {2023: 277, 2024: 1985, 2025: 1932, 2026: 1940}
    weeks = Counter(p["ranking_week"] for p in provenance if p["source_id"] == "hf")
    assert len(weeks) == 95 and weeks["2025-W01"] == 48
    assert all(n == 50 for week, n in weeks.items() if week != "2025-W01")


def test_conference_labels_and_pending_are_distinct(full_seed):
    _, _, papers = full_seed
    meetings = [c for p in papers for c in p["conferences"]]
    assert len(meetings) == 18594
    assert Counter(c["verification_status"] for c in meetings) == {"confirmed": 14570, "pending": 4024}
    assert len({c["conference"] for c in meetings}) == 21
    assert sum(c["presentation"] == "Outstanding Paper Award" for c in meetings) == 4
    assert all(c["selectivity"] == "regular-presentation" for c in meetings if c["conference"] == "RSS")
    for paper in papers:
        if paper["source_ids"] == ["conference"]:
            assert paper["published_at"] is None and paper["year"] is None
        for meeting in paper["conferences"]:
            assert meeting["year"] in {2025, 2026}


def test_missing_metrics_dates_and_safe_targets_are_preserved(seed):
    manifest, _, papers = seed
    if manifest is None:
        from app import catalog
        papers = [catalog.normalize_row(paper)[0] for paper in papers]
    assert any(p["hf_upvotes"] is None for p in papers)
    assert any(p["github_stars"] is None for p in papers)
    assert any(p["github_stars"] == 0 for p in papers)
    assert any(p["directions"] == ["未分类"] for p in papers)
    for paper in papers:
        assert paper["direction_evidence"]["status"] == "heuristic-unverified"
        assert paper["title"]
        if paper["published_at"]:
            assert date.fromisoformat(paper["published_at"]).year == paper["year"]
        target = paper["resolve"]["url"]
        if target:
            assert "docs.google.com/spreadsheets" not in target
            assert "ras.papercept.net" not in target
            assert "aaai.org/wp-content" not in target
        else:
            assert paper["resolve"]["action"] == "search"
        for source in paper["provenance"]:
            if source["source_id"] == "institution":
                assert source["observed_at"] == "2026-10-24"
    if manifest is not None:
        assert all(not p.get("url_is_identity") for p in papers)
        # Obsolete files may remain on disk during a staged upgrade; the
        # manifest selects active files, so they must not be listed as seeds.
        assert not any(s["file"].startswith("institutions-reviewed-") for s in manifest["shards"])


def test_disputed_renamed_institution_pair_is_not_silently_merged(full_seed):
    _, _, papers = full_seed
    wanted = {1196, 2896}
    matches = [p for p in papers if any(x["source_id"] == "institution" and x["raw_record"]["paper_index"] in wanted for x in p["provenance"])]
    assert len(matches) == 2


def test_normalizer_chooses_matching_repository_metric_and_missing_values():
    choose = converter["github_choice"]
    assert converter["number"]("") is None
    assert converter["number"]("0") == 0
    row = {"GitHub链接": "https://github.com/a/first; https://github.com/b/second", "GitHub Star最高值（10月24日）": "300", "逐仓库Star": "a/first: 10; b/second: 300"}
    assert choose(row)[:2] == ("https://github.com/b/second", 300)
    row["逐仓库Star"] = ""
    assert choose(row)[:2] == ("https://github.com/a/first", None)


def test_shared_directory_link_is_never_a_unique_paper_identity():
    base = {"title": "Paper one", "url": "https://example.org/research", "institutions": [], "provenance": []}
    assert len(converter["merge_records"]([base, {**base, "title": "Different paper"}])) == 2


def test_csv_conversion_does_not_remove_2023_or_2024_records():
    row = {"论文标题": "Older work", "论文链接": "https://example.org/research/papers", "原文日期": "2023-12-01", "目标单位": "Lab", "论文总表序号": "1"}
    converted = converter["institution_record"](row)
    assert converted["year"] == 2023 and converted["url"] is None
    assert converted["provenance"][0]["raw_record"] == row
