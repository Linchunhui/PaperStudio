"""Recover browser selections from authentic PDF text, not model citations.

Normalization keeps an offset map: even tolerant matches return a substring of
the server's source. Typo recovery must only be used inside selected PDF blocks.
"""
from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "−": "-",
    "⁄": "/", "∕": "/",
})


def _normalized(text: str):
    chars, offsets = [], []
    # Normalize a base character and its combining marks together, so composed
    # accents, ligatures, full-width glyphs and their copied forms agree.
    index = 0
    while index < len(text):
        end = index + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        value = unicodedata.normalize("NFKC", text[index:end]).casefold().translate(_PUNCTUATION)
        for char in value:
            if not char.isspace() and unicodedata.category(char) != "Cf":
                chars.append(char)
                offsets.append((index, end))
        index = end
    # Browser spans may remove both newline and space after a line-break hyphen.
    # Only letter-to-letter hyphens qualify; numeric signs/formulas stay intact.
    keep = [i for i, c in enumerate(chars) if not
            (c == "-" and i and i + 1 < len(chars)
             and chars[i - 1].isalpha() and chars[i + 1].isalpha())]
    return "".join(chars[i] for i in keep), [offsets[i] for i in keep]


def _distance_prefix(query: str, source: str, limit: int):
    """Banded edit distance against a bounded prefix, with early rejection."""
    size = len(query)
    previous = {j: j for j in range(min(len(source), limit) + 1)}
    for i, char in enumerate(query, 1):
        row = {0: i} if i <= limit else {}
        for j in range(max(1, i - limit), min(len(source), i + limit) + 1):
            cost = min(previous.get(j, limit + 1) + 1,
                       row.get(j - 1, limit + 1) + 1,
                       previous.get(j - 1, limit + 1) + (char != source[j - 1]))
            if cost <= limit:
                row[j] = cost
        if not row:
            return []
        previous = row
    return [(cost, end) for end, cost in previous.items()
            if abs(end - size) <= limit and cost <= limit]


def selection_excerpt(source: str, quote: str, *, allow_edits=False) -> str | None:
    """Resolve PDF formatting differences, optionally small localized edits.

    This is deliberately separate from evidence.original_excerpt: generated
    quotations still require verbatim verification. Never return client text.
    """
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 24000:
        return None
    exact = re.search(r"\s+".join(re.escape(part) for part in quote.split()), source)
    if exact:
        return exact.group(0)
    target, _ = _normalized(quote)
    text, positions = _normalized(source)
    if not target or not text:
        return None
    start = text.find(target)
    if start >= 0:
        return source[positions[start][0]:positions[start + len(target) - 1][1]]
    if not allow_edits or not 8 <= len(target) <= 10000:
        return None
    limit = min(12, max(1, len(target) // 50 + 1))
    # A few unchanged substrings locate small candidate windows. Work is bounded
    # even for repetitive, long pages; no full-page fuzzy scan is performed.
    width = max(2, min(12, len(target) // (limit + 2)))
    last = len(target) - width
    anchors = sorted({0, last, *(last * n // 6 for n in range(1, 6))})
    starts = set()
    for anchor in anchors:
        cursor = 0
        for _ in range(24):
            found = text.find(target[anchor:anchor + width], cursor)
            if found < 0:
                break
            guess = found - anchor
            starts.update(range(max(0, guess - limit), min(len(text), guess + limit + 1)))
            cursor = found + width
    if len(starts) > 256:
        return None
    best, excerpts = None, set()
    for start in sorted(starts):
        window = text[start:start + len(target) + limit]
        if len(window) < len(target) - limit:
            continue
        for cost, length in _distance_prefix(target, window, limit):
            rank = (cost, abs(length - len(target)))
            excerpt = source[positions[start][0]:positions[start + length - 1][1]]
            if best is None or rank < best:
                best, excerpts = rank, {excerpt}
            elif rank == best:
                excerpts.add(excerpt)
    # If two different originals fit equally well, don't silently choose one.
    return excerpts.pop() if len(excerpts) == 1 else None
