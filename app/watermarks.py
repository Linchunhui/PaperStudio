"""Recognize only BabelDOC's generated promotional footer, not paper content."""
from __future__ import annotations

import re

_PATTERNS = [
    r"本\s*文\s*档\s*由[\s\S]{0,500}?BabelDOC[\s\S]{0,500}?欢迎\s*star\s*和\s*关注[。.!！]?",
    r"This\s+document\s+(?:was|is)\s+translated[\s\S]{0,800}?(?:welcome\s+to\s+star(?:\s+and\s+follow)?|star\s+and\s+follow)[.!]?",
]


def is_boilerplate(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    chinese = "本文档由" in compact and "翻译" in compact and "欢迎star和关注" in compact
    english = ("thisdocumentwastranslated" in compact or "thisdocumentistranslated" in compact) and ("welcometostar" in compact or "starandfollow" in compact)
    return "babeldoc" in compact and "funstory" in compact and (chinese or english)


def strip_boilerplate(text: str) -> str:
    # Keep adjacent original paragraphs. A generic mention of BabelDOC/funstory
    # in references or related work does not satisfy these exact boilerplates.
    for pattern in _PATTERNS:
        text = re.sub(pattern, lambda match: "" if is_boilerplate(match.group()) else match.group(), text, flags=re.I)
    return text.strip()


def redact_page(page):
    """Clean the in-memory presentation only; immutable source bytes stay intact."""
    import fitz
    text, characters = [], []
    line_id = 0
    # A footer can span several PDF blocks. Match the complete standardized
    # sentence across lines, then redact only the characters in that match.
    # Whole-block rectangles could erase adjacent scholarly prose.
    for block in page.get_text("rawdict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    value = char.get("c", "")
                    for value_char in value:
                        text.append(value_char)
                        characters.append((line_id, char["bbox"]))
            text.append("\n")
            characters.append(None)
            line_id += 1
    joined = "".join(text)
    matched = set()
    for pattern in _PATTERNS:
        for match in re.finditer(pattern, joined, re.I):
            if is_boilerplate(match.group()):
                matched.update(range(match.start(), match.end()))
    rects = {}
    for index in sorted(matched):
        item = characters[index]
        if item is None:
            continue
        line, bbox = item
        rects[line] = rects.get(line, fitz.Rect()) | fitz.Rect(bbox)
    for rect in rects.values():
        page.add_redact_annot(rect, fill=(1, 1, 1))
    found = bool(rects)
    if found:
        page.apply_redactions(images=0, graphics=0)
    return found
