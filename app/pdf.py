"""Read original PDF bytes and preserve native page graphics and geometry.

PyMuPDF's extraction coordinates are unrotated (relative to CropBox); images and
Page.rect are rotated. Quad corners are transformed with rotation_matrix so the
selectable text layer and the visible page share one coordinate system.
https://pymupdf.readthedocs.io/en/latest/page.html
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

import fitz
from fastapi import HTTPException
from .watermarks import redact_page, strip_boilerplate

PDF_LOCK = threading.RLock()
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 1000


def inspect_pdf(content: bytes) -> dict:
    if not content:
        raise HTTPException(422, "PDF 文件为空")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 超过 50 MB 限制")
    if not content[:1024].lstrip().startswith(b"%PDF-"):
        raise HTTPException(422, "请选择有效的 PDF 文件")
    try:
        with PDF_LOCK, fitz.open(stream=content, filetype="pdf") as doc:
            if doc.needs_pass:
                raise HTTPException(422, "暂不支持加密 PDF，请先提供已解密的文件")
            if doc.page_count < 1 or doc.page_count > MAX_PAGES:
                raise HTTPException(422, f"PDF 页数须为 1–{MAX_PAGES} 页")
            # Ensure page trees can actually be read, rather than trusting a header.
            for page in doc:
                if page.rect.width <= 0 or page.rect.height <= 0:
                    raise HTTPException(422, "PDF 包含无效页面尺寸")
            return {"page_count": doc.page_count, "title": (doc.metadata or {}).get("title", "").strip()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "PDF 无法读取或文件已损坏") from exc


def _get_page(doc, page_number: int):
    if page_number < 1 or page_number > doc.page_count:
        raise HTTPException(404, "论文页码不存在")
    return doc[page_number - 1]


def page_info(path: Path, page_number: int) -> dict:
    try:
        with PDF_LOCK, fitz.open(path) as doc:
            page = _get_page(doc, page_number)
            redact_page(page)
            spans = []
            flags = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES
            for block in page.get_text("dict", flags=flags, sort=False).get("blocks", []):
                for line in block.get("lines", []):
                    direction = line.get("dir", (1, 0))
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text:
                            continue
                        quad = fitz.recover_quad(direction, span)
                        ul = quad.ul * page.rotation_matrix
                        ur = quad.ur * page.rotation_matrix
                        ll = quad.ll * page.rotation_matrix
                        width = abs(ur - ul)
                        height = abs(ll - ul)
                        if not width or not height:
                            continue
                        spans.append({
                            "text": text, "x": ul.x, "y": ul.y,
                            "width": width, "height": height,
                            "font_size": span.get("size", height),
                            "font_family": span.get("font", "sans-serif"),
                            "rotation": math.degrees(math.atan2(ur.y - ul.y, ur.x - ul.x)),
                        })
            return {
                "page": page_number, "width": page.rect.width,
                "height": page.rect.height, "rotation": page.rotation,
                "text": spans, "has_text": any(s["text"].strip() for s in spans),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "无法读取这页 PDF") from exc


def page_png(path: Path, page_number: int, width: int = 1600,
             clip: tuple[float, float, float, float] | None = None) -> bytes:
    """Render native PDF graphics without the PDF-to-SVG text-stroke conversion.

    Both the crop and selectable text use rotated page coordinates. Limit the
    output pixel count as well as its width for unusually tall or large pages.
    """
    try:
        with PDF_LOCK, fitz.open(path) as doc:
            page = _get_page(doc, page_number)
            redact_page(page)
            region = page.rect
            if clip is not None:
                x, y, w, h = clip
                if (not all(math.isfinite(v) for v in clip) or min(x, y) < 0
                        or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001):
                    raise HTTPException(422, "裁剪范围须位于论文页面内")
                region = fitz.Rect(x * region.width, y * region.height,
                                   (x + w) * region.width, (y + h) * region.height)
            scale = min(max(256, min(4096, width)) / region.width,
                        math.sqrt(12_000_000 / region.get_area()), 8.0)
            return page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=region,
                                   colorspace=fitz.csRGB, alpha=False).tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "无法渲染这页 PDF") from exc


def page_svg(path: Path, page_number: int) -> str:
    try:
        with PDF_LOCK, fitz.open(path) as doc:
            page = _get_page(doc, page_number)
            redact_page(page)
            # Glyph outlines remain vectors. Existing raster images stay images;
            # this does not falsely promise to vectorize scanned pages.
            return page.get_svg_image(text_as_path=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "无法渲染这页 PDF") from exc


def extract_pages(path: Path) -> list[str]:
    """Expose a serialized MuPDF extraction path to background workers."""
    try:
        with PDF_LOCK, fitz.open(path) as doc:
            return [strip_boilerplate(page.get_text("text", sort=True)) for page in doc]
    except Exception as exc:
        raise HTTPException(422, "无法提取 PDF 正文") from exc


def page_manifest(path: Path) -> list[dict]:
    """Read dimensions only; do not decode every page's text or SVG."""
    try:
        with PDF_LOCK, fitz.open(path) as doc:
            return [
                {"page": index + 1, "width": page.rect.width,
                 "height": page.rect.height, "rotation": page.rotation}
                for index, page in enumerate(doc)
            ]
    except Exception as exc:
        raise HTTPException(422, "无法读取 PDF 页面清单") from exc

