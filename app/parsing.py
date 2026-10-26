"""Immutable, reusable BabelDOC paragraph parsing with a labelled fallback.

Upstream CLI contract: --skip-translation --no-auto-extract-glossary --debug
produces styles_and_formulas.json before translation. Debug PDFs are discarded;
only normalized paragraph data bound to the original SHA256 enters the cache.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

import fitz

from . import core, pdf
from .watermarks import strip_boilerplate

VERSION = "babeldoc-structure-v1"
_LOCKS = {}
_LOCK = threading.Lock()


def _cache_path(source_sha, namespace=None):
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha or ""):
        raise ValueError("Invalid source identity")
    directory = core.DATA_DIR / "cache" / "parsed"
    if namespace is not None:
        if not isinstance(namespace, str) or not 1 <= len(namespace) <= 1024:
            raise ValueError("Invalid parse cache namespace")
        directory = directory / "namespaces" / hashlib.sha256(namespace.encode()).hexdigest()
    return directory / (source_sha + ".json")


@lru_cache(maxsize=4)
def _read_cache(path: str, mtime_ns: int, size: int):
    # Parsing artifacts contain source text only. Treat this shared object as
    # immutable; reading._extract copies a single page before adding user data.
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def _legacy_cache(path: str, mtime_ns: int, size: int):
    return _legacy_result(_read_cache(path, mtime_ns, size))


def load_cached(source_sha, namespace=None):
    path = _cache_path(source_sha, namespace)
    try:
        if not path.is_file() or path.is_symlink():
            return None
        stat = path.stat()
        if stat.st_size > 64 * 1024 * 1024:
            return None
        result = _read_cache(str(path), stat.st_mtime_ns, stat.st_size)
        if not isinstance(result, dict) or result.get("source_sha") != source_sha or not isinstance(result.get("pages"), list):
            return None
        if result.get("version") == VERSION:
            return result
        # Previous releases stored page text only. Trust only their known
        # parser format with the exact immutable source identity. Preserve that
        # text rather than spending another BabelDOC run to rediscover it.
        if result.get("parser_version") != "pymupdf-text-v1" or namespace is not None:
            return None
        return _legacy_cache(str(path), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, TypeError):
        return None



def _legacy_result(result):
    pages = result.get("pages")
    if not isinstance(result.get("fulltext"), str) or not isinstance(result.get("coverage"), dict) or not 1 <= len(pages) <= pdf.MAX_PAGES:
        return None
    normalized = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("page") != index + 1 or not isinstance(page.get("text"), str) or len(page["text"]) > 200000:
            return None
        text = strip_boilerplate(page["text"])
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        blocks = []
        for position, paragraph in enumerate(paragraphs):
            identity = ["legacy-pymupdf-text-v1", result["source_sha"], index + 1, position, paragraph]
            blocks.append({"id": hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32],
                           "type": "paragraph", "text": paragraph, "rects": [], "translation": None})
        normalized.append({"page": index + 1, "text": text, "blocks": blocks})
    coverage = {**result["coverage"], "parser": "legacy-parser", "parser_version": "pymupdf-text-v1",
                "warnings": ["复用已有 PDF 文本解析；旧缓存没有段落坐标，引用可跳转到对应页。"]}
    return {**result, "version": VERSION, "provider": "legacy-parser", "legacy_parser_version": "pymupdf-text-v1",
            "pages": normalized, "fulltext": strip_boilerplate(result["fulltext"]), "coverage": coverage}

def _native(paper, cancelled):
    from . import ai, reading
    pages = []
    for page in range(1, paper["page_count"] + 1):
        if cancelled():
            raise ai.Cancelled()
        pages.append({"page": page, "blocks": reading._extract_native(paper, page)})
    return pages


def normalize_il(data, paper):
    """BabelDOC dataclass JSON uses PDF-space boxes and zero-based page numbers."""
    from .ai import ExecutionError
    raw_pages = data.get("page") if isinstance(data, dict) else None
    if not isinstance(raw_pages, list):
        raise ExecutionError("BabelDOC 解析结果缺少 page 列表。")
    normalized = {}
    with pdf.PDF_LOCK, fitz.open(core.paper_asset_path(paper)) as doc:
        for position, raw in enumerate(raw_pages):
            number = raw.get("page_number", position)
            if not isinstance(number, int) or not 0 <= number < len(doc):
                raise ExecutionError("BabelDOC 解析页码与原文不一致。")
            page = doc[number]
            blocks = []
            for index, paragraph in enumerate(raw.get("pdf_paragraph", [])):
                text = strip_boilerplate(str(paragraph.get("unicode") or ""))
                if not text:
                    continue
                if len(text) > 200000 or len(blocks) >= 1000:
                    raise ExecutionError("BabelDOC 单页解析量过大。")
                box = paragraph.get("box") or {}
                rects = []
                try:
                    coords = [float(box[k]) for k in ("x", "y", "x2", "y2")]
                    if not all(math.isfinite(v) for v in coords):
                        raise ValueError()
                    rect = fitz.Rect(coords) * page.transformation_matrix * page.rotation_matrix
                    rect &= page.rect
                    if not rect.is_empty:
                        rects = [{"x": rect.x0 / page.rect.width, "y": rect.y0 / page.rect.height,
                                  "width": rect.width / page.rect.width, "height": rect.height / page.rect.height}]
                except (KeyError, TypeError, ValueError):
                    pass
                # Missing/unsupported geometry is represented honestly; callers
                # can still jump to the page without a fabricated highlight.
                block_id = hashlib.sha256(json.dumps([VERSION, paper["sha256"], number + 1, index, text, rects], ensure_ascii=False).encode()).hexdigest()[:32]
                label = str(paragraph.get("layout_label") or "").casefold()
                blocks.append({"id": block_id, "type": "heading" if "title" in label or "heading" in label else "paragraph",
                               "text": text, "rects": rects, "translation": None})
            normalized[number + 1] = {"page": number + 1, "blocks": blocks}
    if not any(p["blocks"] for p in normalized.values()):
        raise ExecutionError("BabelDOC 未提取到可用段落。")
    return [normalized.get(n, {"page": n, "blocks": []}) for n in range(1, paper["page_count"] + 1)]


def _babeldoc(paper, executable, cancelled):
    from . import ai, providers
    root = core.DATA_DIR / "parse-work"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parse-", dir=root) as temp:
        folder = Path(temp)
        output, working = folder / "out", folder / "working"
        output.mkdir()
        working.mkdir()
        # The upstream CLI requires a translator configuration even when the
        # translation stage is skipped. Dummy loopback credentials cannot reach
        # a model; automatic glossary extraction is explicitly disabled as well.
        command = [executable, "--files", str(core.paper_asset_path(paper)), "--output", str(output),
                   "--working-dir", str(working), "--openai", "--openai-model", "parse-only",
                   "--openai-base-url", "http://127.0.0.1:9/v1", "--openai-api-key", "parse-only-unused",
                   "--skip-translation", "--no-auto-extract-glossary", "--debug", "--no-dual",
                   "--watermark-output-mode", "no_watermark"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   shell=False, start_new_session=os.name == "posix")
        providers.wait_process(process, ai._positive_int("BABELDOC_PARSE_TIMEOUT_SECONDS", 900, 10, 14400), cancelled, "BabelDOC 解析")
        for name in ("styles_and_formulas.json", "paragraph_finder.json"):
            candidates = [p for p in working.rglob(name) if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(working.resolve())]
            if len(candidates) == 1 and candidates[0].stat().st_size <= 128 * 1024 * 1024:
                return normalize_il(json.loads(candidates[0].read_text(encoding="utf-8")), paper)
        raise ai.ExecutionError("BabelDOC 未返回支持的结构化段落文件。")


def ensure_parsed(paper, cancelled=lambda: False):
    from . import ai
    namespace = paper.get("_parse_cache_namespace")
    cached = load_cached(paper["sha256"], namespace)
    if cached:
        return cached
    with _LOCK:
        lock = _LOCKS.setdefault((namespace, paper["sha256"]), threading.Lock())
    while not lock.acquire(timeout=.2):
        if cancelled():
            raise ai.Cancelled()
    try:
        cached = load_cached(paper["sha256"], namespace)
        if cached:
            return cached
        if cancelled():
            raise ai.Cancelled()
        executable = ai.translation_executable()
        warning = None
        if executable and os.environ.get("PAPERSTUDIO_PARSER", "babeldoc") != "pymupdf":
            try:
                pages = _babeldoc(paper, executable, cancelled)
                provider = "babeldoc"
            except ai.Cancelled:
                raise
            except Exception:
                pages = _native(paper, cancelled)
                provider, warning = "pymupdf", "BabelDOC 解析失败或版本不兼容，已使用 PDF 文字层；图表和复杂版式仍需核验。"
        else:
            pages = _native(paper, cancelled)
            provider, warning = "pymupdf", "BabelDOC 未安装或解析器已设为 PyMuPDF，当前使用 PDF 文字层。"
        empty = [p["page"] for p in pages if not p["blocks"]]
        coverage = {"total_pages": len(pages), "text_pages": len(pages) - len(empty), "empty_text_pages": empty,
                    "parser": provider, "parser_version": VERSION, "visual_evidence_verified": False,
                    "warnings": [warning] if warning else []}
        fulltext = "\n".join(f"[PDF 第 {p['page']} 页]\n" + "\n\n".join(b["text"] for b in p["blocks"]) for p in pages)
        result = {"source_sha": paper["sha256"], "version": VERSION, "provider": provider, "pages": pages,
                  "fulltext": fulltext, "coverage": coverage}
        path = _cache_path(paper["sha256"], namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".parse-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(result, file, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return result
    finally:
        lock.release()
