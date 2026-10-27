"""User-supplied hierarchy, honest classification and stable taxonomy IDs."""
import pytest

from app import taxonomy


def test_supplied_taxonomy_is_complete_and_has_unique_stable_ids():
    assert len(taxonomy.CATEGORIES) == 13
    assert len(taxonomy.TOPICS) == 76
    assert all(key.startswith(category["id"] + ".") for key, (category, _) in taxonomy.TOPICS.items())
    assert taxonomy.TOPICS["document.layout"][1]["name"] == "版面分析与文档解析"
    assert taxonomy.snapshot()["count_scope"] == "exclusive-primary"


@pytest.mark.parametrize("title,topic", [
    ("Optical character recognition with OCR", "document.ocr"),
    ("High resolution PDF parsing", "document.layout"),
    ("Visual question answering", "multimodal.vqa"),
    ("Vision-language-action policies", "robotics.vla"),
    ("On-policy distillation for language models", "efficiency.adapt"),
    ("Diffusion language models", "generation.discrete"),
    ("基于视觉的文档解析", "document.layout"),
])
def test_specific_topics_include_acronyms_hyphens_plurals_and_chinese(title, topic):
    result = taxonomy.classify(title)
    assert result["primary"]["topic_id"] == topic
    assert result["status"] == "heuristic-unverified" and result["needs_review"]
    assert result["primary"]["evidence"][0]["field"] == "title"


def test_unknowns_broad_parents_and_false_substrings_are_not_forced_into_topics():
    result = taxonomy.classify("Journal of organic systems")
    assert result["primary"]["category_id"] == "unclassified"  # RNA/GAN are not substrings.
    assert result["primary"]["topic_id"] is None
    broad = taxonomy.classify("Large language models")
    assert broad["primary"]["category_id"] == "nlp" and broad["unassigned_topic"]
    assert taxonomy.classify("Untitled work", legacy=["OCR"])["primary"]["category_id"] == "document"


def test_primary_is_exclusive_secondary_is_bounded_and_cache_is_not_mutable():
    title = "Document parsing for visual question answering with retrieval augmented generation and knowledge distillation"
    first = taxonomy.classify(title)
    assert len(first["secondary"]) == 2
    assert len({x["topic_id"] for x in [first["primary"], *first["secondary"]]}) == 3
    original = first["primary"]["topic_name"]
    first["primary"]["topic_name"] = "changed by caller"
    assert taxonomy.classify(title)["primary"]["topic_name"] == original
    item = {"classification": taxonomy.classify(title)}
    counts = taxonomy.item_snapshot([item])
    assert sum(c["count"] for c in counts["categories"]) == 1
    assert sum(c["match_count"] for c in counts["categories"]) >= 1
    assert not taxonomy.matches(item, "document", "multimodal.vqa")
