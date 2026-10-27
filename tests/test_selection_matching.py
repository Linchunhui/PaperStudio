import pytest

from app.evidence import original_excerpt
from app.selection_matching import selection_excerpt


@pytest.mark.parametrize("source,quote,expected", [
    ("Before. We learn across lines. After.", "Welearnacrosslines.", "We learn across lines."),
    ("Before. We learn across lines. After.", "W e learn ac ross lines.", "We learn across lines."),
    ("An ofﬁce uses eﬃcient ﬂows.", "office uses efficient flows", "ofﬁce uses eﬃcient ﬂows"),
    ("The ‘method’ is “reliable”.", "'method' is \"reliable\"", "‘method’ is “reliable”"),
    ("Use non\u00adlinear\u200b methods.", "nonlinear methods", "non\u00adlinear\u200b methods"),
    ("A café study.", "cafe\u0301 study", "café study"),
    ("Use ＡＢＣ models.", "ABCmodels", "ＡＢＣ models"),
    ("Self-improve-\nment needs evidence.", "Self-improvementneedsevidence.", "Self-improve-\nment needs evidence."),
    ("Self-improvement needs evidence.", "improve-ment needs evidence", "improvement needs evidence"),
    ("A model uses x − 1, then x + 1.", "x - 1", "x − 1"),
    ("中文\n跨行文字。后续内容。", "中文 跨 行 文字。", "中文\n跨行文字。"),
])
def test_pdf_copy_variants_return_authentic_minimal_excerpt(source, quote, expected):
    assert selection_excerpt(source, quote) == expected
    assert expected in source


@pytest.mark.parametrize("quote", [
    "first paragrapx with a line break.",
    "firs paragraph with a line break.",
    "first paraagraph with a line break.",
    "firstparagrapxwithalinebreak.",
])
def test_localized_small_edits_preserve_original_scope(quote):
    source = "Left first paragraph with a line break. A different paragraph."
    assert selection_excerpt(source, quote) is None
    assert selection_excerpt(source, quote, allow_edits=True) == "first paragraph with a line break."


def test_ambiguous_unrelated_or_unbounded_edits_are_not_guessed():
    assert selection_excerpt("An effective method; An affective method.", "An xffective method", allow_edits=True) is None
    assert selection_excerpt("Actual results support this claim.", "Invented words", allow_edits=True) is None
    assert selection_excerpt("ab " * 20000, "ab" * 10000 + "x", allow_edits=True) is None
    assert selection_excerpt("a" * 20000, "a" * 50 + "x", allow_edits=True) is None


def test_model_citation_verifier_remains_strict():
    source = "We measure latency with an efficient method."
    for quote in ("Wemeasure latency", "We measure latencx", "We measure LATENCY"):
        assert original_excerpt(source, quote) is None
        assert selection_excerpt(source, quote, allow_edits=True) is not None
    assert original_excerpt(source, "measure\nlatency") == "measure latency"
