"""Optional recommendation figures cropped from verified local paper originals.

Only numbered figures with explicit visual blocks are eligible. No network,
model, PDF import or private-library lookup occurs while browsing the feed.
"""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from . import core, pdf
import fitz

VERSION = "catalog-figure-v1"
FIGURE = re.compile(r"^\s*(?:Figure|Fig\.?|图)\s*(\d+)\s*[:.：\s]", re.I)
OVERVIEW = re.compile(r"\b(?:overview|architecture|framework|pipeline)\b|总览|总体架构|整体框架", re.I)
VISUALS = {"image", "chart", "figure"}
CAPTIONS = {"figure_title", "figure_caption", "caption"}
BARRIERS = {"text", "abstract", "paragraph_title", "table", "table_title", "algorithm", "display_formula"}


def _box(block, width, height):
    try:
        x0, y0, x1, y1 = map(float, block["bbox"])
    except (KeyError, TypeError, ValueError):
        return None
    if (not all(math.isfinite(v) for v in (x0, y0, x1, y1))
            or not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height):
        return None
    return x0, y0, x1, y1


def _overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0]))


def page_figures(page):
    """Associate graphics above a numbered caption without crossing body text."""
    width, height = float(page["width"]), float(page["height"])
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        return []
    blocks = [(b, _box(b, width, height)) for b in page.get("blocks", [])]
    blocks = [(b, r) for b, r in blocks if r]
    found = []
    for caption, cap in blocks:
        text = caption.get("text", "").strip()
        match = FIGURE.match(text)
        if caption.get("label") not in CAPTIONS or not match:
            continue
        barrier = max((r[3] for b, r in blocks
                       if (b.get("label") in BARRIERS or FIGURE.match(b.get("text", "")))
                       and r[3] <= cap[1] and _overlap(r, cap) >= min(r[2]-r[0], cap[2]-cap[0]) * .5), default=height * .05)
        graphics = [r for b, r in blocks if b.get("label") in VISUALS
                    and r[1] >= barrier and r[3] <= cap[1] + 2
                    and _overlap(r, cap) >= min(r[2]-r[0], cap[2]-cap[0]) * .5]
        if not graphics:
            continue
        box = [min(r[0] for r in graphics), min(r[1] for r in graphics),
               max(r[2] for r in graphics), max(r[3] for r in graphics)]
        # Keep subfigure labels and legends, including labels just above an image.
        for b, r in blocks:
            if (b is not caption and b.get("label") in CAPTIONS | {"vision_footnote"}
                    and not FIGURE.match(b.get("text", ""))
                    and max(barrier, box[1]-height*.025) <= r[1] < cap[1]
                    and r[3] <= cap[1] and _overlap(r, box) > 0):
                box = [min(box[0], r[0]), min(box[1], r[1]), max(box[2], r[2]), max(box[3], r[3])]
        x0, y0 = max(0, box[0]-4), max(0, box[1]-4)
        x1, y1 = min(width, box[2]+4), min(cap[1], box[3]+4)
        w, h = (x1-x0)/width, (y1-y0)/height
        if w < .15 or h < .045 or w*h > .72 or not .4 <= (x1-x0)/(y1-y0) <= 8:
            continue
        overview = bool(OVERVIEW.search(text[:180]))
        found.append({"page": int(page["page"]), "number": int(match[1]), "caption": text,
                      "clip": (x0/width, y0/height, w, h),
                      "selection": "overview-caption" if overview else "first-numbered-figure",
                      "overview": overview, "aspect_ratio": (x1-x0)/(y1-y0)})
    return found



def _linked(catalog_id):
    """Resolve an explicit catalog link, never a title or another user's assets."""
    with core.db() as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_catalog_links'").fetchone():
            return None
        rows = con.execute("SELECT paper_id FROM paper_catalog_links WHERE catalog_paper_id=? ORDER BY paper_id", (catalog_id,)).fetchall()
    for row in rows:
        try:
            return core.get_paper(row[0])
        except HTTPException:
            continue
    return None


@lru_cache(maxsize=128)
def _native_selection(path, mtime, size, source_sha):
    source = Path(path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
        return None
    candidates = []
    with pdf.PDF_LOCK, fitz.open(source) as doc:
        for i in range(min(5, len(doc))):
            page = doc[i]
            # Cropping rotated originals requires a different coordinate contract.
            if page.rotation:
                continue
            blocks = [{"label": "image", "bbox": item["bbox"], "text": ""}
                      for item in page.get_image_info()]
            for item in page.get_text("blocks"):
                if item[6] != 0:
                    continue
                text = item[4].strip()
                blocks.append({"label": "figure_caption" if FIGURE.match(text) else "text",
                               "bbox": item[:4], "text": text})
            candidates.extend(page_figures({"width": page.rect.width, "height": page.rect.height,
                                            "page": i+1, "blocks": blocks}))
    return min(candidates, key=lambda f: (not f["overview"], f["number"], f["page"])) if candidates else None


def locate(catalog_id):
    paper = _linked(catalog_id)
    if not paper:
        return None
    path = core.paper_asset_path(paper)
    if path.is_symlink() or not path.is_file():
        return None
    stat = path.stat()
    chosen = _native_selection(str(path), stat.st_mtime_ns, stat.st_size, paper["sha256"])
    if not chosen:
        return None
    identity = json.dumps([VERSION, paper["sha256"], chosen], sort_keys=True)
    return {**chosen, "source_path": path, "source_sha256": paper["sha256"],
            "version": hashlib.sha256(identity.encode()).hexdigest()[:24]}


def presentation(paper):
    try:
        figure = locate(paper["id"])
    except (HTTPException, OSError, ValueError, KeyError, TypeError, RuntimeError):
        return {}
    if not figure:
        return {}
    base = f"/api/catalog/{quote(paper['id'], safe='')}/figure.png"
    return {"paper_figure": {
        **{key: figure[key] for key in ("page", "number", "caption", "aspect_ratio", "selection")},
        "url": base + "?" + urlencode({"width": 720, "v": figure["version"]}),
        "full_url": base + "?" + urlencode({"width": 2200, "v": figure["version"]})}}


def image_bytes(catalog_id, width, version):
    figure = locate(catalog_id)
    if not figure:
        raise HTTPException(404, "暂无可用论文配图")
    if version != figure["version"]:
        raise HTTPException(409, "论文配图已更新，请刷新列表")
    # Authorization is repeated by locate on every request; no shared public cache.
    return pdf.page_png(figure["source_path"], figure["page"], width, clip=figure["clip"])
