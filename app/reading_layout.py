"""Loss-preserving visual content for reflow; never call a model on reads.

Text/translation block IDs stay unchanged. Figures, tables and display equations
are original-page crops, not a guessed reconstruction of their contents.
"""
from functools import lru_cache
import hashlib
import math
import re
from urllib.parse import urlencode, quote

import fitz

from . import core, pdf
from .watermarks import redact_page

VERSION = "native-visual-reflow-v1"


def _valid(rect, page):
    rect = fitz.Rect(rect) & page.rect
    return rect if not rect.is_empty and all(math.isfinite(v) for v in rect) else None


def _merge(regions):
    """Unite overlapping graphics, preserving a whole table or diagram."""
    result = []
    for kind, rect in regions:
        rect = fitz.Rect(rect)
        changed = True
        while changed:
            changed = False
            for index, (other_kind, other) in enumerate(result):
                padded = fitz.Rect(rect.x0 - 3, rect.y0 - 3, rect.x1 + 3, rect.y1 + 3)
                if padded.intersects(other):
                    rect |= other
                    kind = "table" if "table" in (kind, other_kind) else "figure" if "figure" in (kind, other_kind) else kind
                    result.pop(index)
                    changed = True
                    break
        result.append((kind, rect))
    return result


@lru_cache(maxsize=96)
def _regions(path: str, mtime_ns: int, size: int, number: int):
    with pdf.PDF_LOCK, fitz.open(path) as doc:
        page = pdf._get_page(doc, number)
        redact_page(page)
        regions, uncertain = [], False
        rotate = page.rotation_matrix
        for image in page.get_image_info():
            rect = _valid(fitz.Rect(image["bbox"]) * rotate, page)
            if rect:
                regions.append(("figure", rect))
        drawings = page.get_drawings()
        if len(drawings) > 12000:
            return (("page", tuple(page.rect)),), True, tuple(page.rect)
        try:
            tables = page.find_tables()
            for table in tables.tables:
                rect = _valid(fitz.Rect(table.bbox) * rotate, page)
                if rect:
                    regions.append(("table", rect))
        except Exception:
            uncertain = True
        if drawings:
            try:
                clusters = page.cluster_drawings(drawings=drawings, x_tolerance=12, y_tolerance=12, final_filter=False)
                for cluster in clusters:
                    rect = _valid(fitz.Rect(cluster) * rotate, page)
                    if rect:
                        # Keep fine rules too: they may belong to a borderless
                        # table or equation. Crops include neighbouring labels.
                        regions.append(("figure", rect))
            except Exception:
                return (("page", tuple(page.rect)),), True, tuple(page.rect)
        raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
        for block in raw.get("blocks", []):
            lines = block.get("lines", [])
            if not lines or len(lines) > 5:
                continue
            spans = [s for line in lines for s in line.get("spans", [])]
            text = " ".join(s.get("text", "") for s in spans)
            math_chars = sum(len(s.get("text", "")) for s in spans
                             if re.search(r"math|symbol|cmsy|cmex|cmmi|msam|msbm", s.get("font", ""), re.I))
            if math_chars >= max(2, len(text.strip()) * .4):
                rect = _valid(fitz.Rect(block["bbox"]) * rotate, page)
                if rect:
                    regions.append(("formula", rect))
        # A scanned page is itself a complete visual block, never an empty page.
        if not regions and not any(b.get("lines") for b in raw.get("blocks", [])):
            regions = [("page", page.rect)]
        if uncertain:
            return (("page", tuple(page.rect)),), True, tuple(page.rect)
        merged = _merge(regions)
        if len(merged) > 120:
            return (("page", tuple(page.rect)),), True, tuple(page.rect)
        padded = []
        for kind, rect in merged:
            padded.append((kind, tuple((rect + (-4, -4, 4, 4)) & page.rect)))
        return tuple(padded), uncertain, tuple(page.rect)


def compose(paper: dict, page_number: int, blocks: list[dict]) -> dict:
    from .reading import _reading_order
    path = core.paper_asset_path(paper)
    stat = path.stat()
    try:
        regions, uncertain, bounds = _regions(str(path), stat.st_mtime_ns, stat.st_size, page_number)
    except Exception:
        # Graphics classification is optional. A readable page must remain
        # available even when a malformed drawing defeats the layout detector.
        with pdf.PDF_LOCK, fitz.open(path) as doc:
            bounds = tuple(pdf._get_page(doc, page_number).rect)
        regions, uncertain = (("page", bounds),), True
    page_rect = fitz.Rect(bounds)
    width, height = page_rect.width, page_rect.height
    base = f'/api/papers/{quote(paper["id"], safe="")}/pages/{page_number}.png'
    full_url = base + "?" + urlencode({"side": "original", "v": paper["sha256"], "width": 2400})
    content, visuals, expanded = [], [], []
    for kind, coordinates in regions:
        rect = fitz.Rect(coordinates)
        # Widen a crop to retain complete text labels touching the graphic.
        for block in blocks:
            for r in block.get("rects", []):
                line = fitz.Rect(r["x"] * width, r["y"] * height,
                                 (r["x"] + r["width"]) * width, (r["y"] + r["height"]) * height)
                intersection = line & rect
                if not intersection.is_empty and intersection.get_area() >= line.get_area() * .35:
                    rect |= line
        rect = (rect + (-2, -2, 2, 2)) & page_rect
        expanded.append((kind, rect))
    for kind, rect in _merge(expanded):
        anchor = {"x": rect.x0 / width, "y": rect.y0 / height,
                  "width": rect.width / width, "height": rect.height / height}
        identifier = "visual-" + hashlib.sha256(f'{VERSION}:{paper["sha256"]}:{page_number}:{tuple(rect)}'.encode()).hexdigest()[:24]
        params = {"side": "original", "v": paper["sha256"], "width": 2000,
                  "x": anchor["x"], "y": anchor["y"], "w": anchor["width"], "h": anchor["height"]}
        visual = {"id": identifier, "type": kind, "rects": [anchor],
                  "image_url": base + "?" + urlencode(params), "width": rect.width, "height": rect.height,
                  "bbox": tuple(rect)}
        visuals.append(visual)
    for block in blocks:
        rects = block.get("rects", [])
        box = fitz.Rect()
        for r in rects:
            box |= fitz.Rect(r["x"] * width, r["y"] * height,
                             (r["x"] + r["width"]) * width, (r["y"] + r["height"]) * height)
        # Covered words remain available for search/QA/translation, but are not
        # repeated as disjoint labels below an intact diagram or table.
        covered = not box.is_empty and any(fitz.Rect(v["bbox"]).contains(box) for v in visuals)
        if not covered:
            content.append({"id": block["id"], "type": "text", "bbox": tuple(box) if not box.is_empty else (0, height, width, height)})
    content.extend(visuals)
    ordered = _reading_order(content)
    return {"layout_version": VERSION, "content": [{k: v for k, v in item.items() if k != "bbox"} for item in ordered],
            "page_image_url": full_url, "visual_fallback": uncertain}
