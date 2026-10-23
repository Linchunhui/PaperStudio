"""Conservative paragraph links from BabelDOC's actual translation trace.

The trace supplies the bilingual relationship, not page order or proximity.
Both texts must then match a unique range in their real PDF text layers. No
model, fuzzy semantic guess, or geometry-only fallback is used here.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from pathlib import Path

import fitz

from . import pdf

MAX_TRACKING_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 2000
MAX_PARAGRAPH_CHARS = 32000
MAX_PAGE_CHARS = 200000
MAX_RECTS = 1000
_ZERO_WIDTH = {'\u00ad', '\u200b', '\u200c', '\u200d', '\ufeff'}
_STYLE_OPEN = re.compile(r"<style\s+id\s*=\s*(['\"])(\d+)\1\s*>", re.I)
_STYLE_CLOSE = re.compile(r"</style\s*>", re.I)
_FORMULA = re.compile(r"\{\s*v\s*(\d+)\s*\}")
_TOKEN = re.compile(r"\{\s*v\s*\d+\s*\}|</style\s*>|<style\b[^>]*>", re.I)


def _normalise(text: str) -> str:
    return ''.join(
        c for original in text
        for c in unicodedata.normalize('NFKC', original).casefold()
        if not c.isspace() and c not in _ZERO_WIDTH
    )


def _without_line_hyphens(text: str) -> str:
    return re.sub(r'(?<=[^\W\d_])[-\u2010]\s*\n\s*(?=[^\W\d_])', '', text)


def _translated_text(record: dict) -> str | None:
    """Restore known formulas and remove style wrappers, never trace regexes."""
    output = record.get('output')
    if not isinstance(output, str) or not output.strip() or len(output) > MAX_PARAGRAPH_CHARS:
        return None
    placeholders = record.get('placeholders', [])
    if placeholders is None:
        placeholders = []
    if not isinstance(placeholders, list) or len(placeholders) > MAX_RECORDS:
        return None
    formulas, style_ids = {}, set()
    for item in placeholders:
        if not isinstance(item, dict):
            return None
        if item.get('type') == 'formula':
            chars, token = item.get('formula_chars'), item.get('placeholder')
            match = _FORMULA.fullmatch(token) if isinstance(token, str) and len(token) <= 200 else None
            if not isinstance(chars, str) or not chars or len(chars) > MAX_PARAGRAPH_CHARS or not match:
                return None
            index = match.group(1)
            if index in formulas and formulas[index] != chars:
                return None
            formulas[index] = chars
        elif item.get('type') == 'rich_text':
            left, right = item.get('left_placeholder'), item.get('right_placeholder')
            match = _STYLE_OPEN.fullmatch(left) if isinstance(left, str) and len(left) <= 200 else None
            if not match or not isinstance(right, str) or not _STYLE_CLOSE.fullmatch(right):
                return None
            style_ids.add(match.group(2))
        else:
            return None
    unknown, style_stack = False, []
    def restore(match):
        nonlocal unknown
        token = match.group(0)
        formula = _FORMULA.fullmatch(token)
        if formula:
            value = formulas.get(formula.group(1))
            if value is None:
                unknown = True
                return token
            # re.sub scans only the original output. A literal {vN} inside
            # formula_chars must never be recursively interpreted as a token.
            return value
        opening = _STYLE_OPEN.fullmatch(token)
        if token.lower().startswith('<style'):
            # Unknown/no-id/non-numeric style openings still need a stack
            # frame so their closing tag cannot close a known outer wrapper.
            known = bool(opening and opening.group(2) in style_ids)
            style_stack.append(known)
            return '' if known else token
        if _STYLE_CLOSE.fullmatch(token):
            return '' if style_stack and style_stack.pop() else token
        return token
    # Only our bounded grammar is evaluated, never regexes supplied by the trace.
    # Undeclared <style> markup may be ordinary paper text and stays verbatim.
    output = _TOKEN.sub(restore, output)
    if unknown or any(style_stack) or len(output) > MAX_PARAGRAPH_CHARS:
        return None
    return output.strip()


def _records(tracking: dict) -> tuple[list[dict], bool]:
    """Read only public paragraph fields; prompts and provider data are ignored."""
    records = []
    if not isinstance(tracking, dict):
        return records, False
    for name in ('page', 'cross_column', 'cross_page'):
        groups = tracking.get(name, [])
        if not isinstance(groups, list) or len(groups) > MAX_RECORDS:
            return [], False
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('paragraph'), list):
                return [], False
            for record in group['paragraph']:
                if not isinstance(record, dict):
                    return [], False
                records.append(record)
                if len(records) > MAX_RECORDS:
                    return [], False
    return records, True


def _normalised_rect(quad, rotation, width, height):
    points = [point * rotation for point in (quad.ul, quad.ur, quad.ll, quad.lr)]
    if not all(math.isfinite(point.x) and math.isfinite(point.y) for point in points):
        return None
    x0 = max(0.0, min(point.x for point in points))
    y0 = max(0.0, min(point.y for point in points))
    x1 = min(width, max(point.x for point in points))
    y1 = min(height, max(point.y for point in points))
    if x1 <= x0 or y1 <= y0:
        return None
    return {'x': x0 / width, 'y': y0 / height, 'width': (x1 - x0) / width, 'height': (y1 - y0) / height}


def _page_index(path: Path, number: int) -> dict:
    # Character quads use exactly the same unrotated -> visual-page transform as
    # pdf.page_info, but avoid highlighting a whole span for a partial text range.
    with pdf.PDF_LOCK, fitz.open(path) as document:
        if number < 1 or number > len(document):
            raise ValueError('Invalid page')
        page = document[number - 1]
        width, height = page.rect.width, page.rect.height
        units = []
        flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
        for block_id, block in enumerate(page.get_text('rawdict', flags=flags, sort=False).get('blocks', [])):
            for line_id, line in enumerate(block.get('lines', [])):
                for span in line.get('spans', []):
                    for char in span.get('chars', []):
                        value = char.get('c', '')
                        if not value:
                            continue
                        rect = _normalised_rect(fitz.recover_char_quad(line.get('dir', (1, 0)), span, char), page.rotation_matrix, width, height)
                        if rect is not None:
                            units.append({'text': value, 'rect': rect, 'line': (block_id, line_id)})
                            if len(units) > MAX_PAGE_CHARS:
                                raise ValueError('Page text limit')
                units.append({'text': '\n', 'rect': None, 'line': (block_id, line_id)})
        variants = []
        for dehyphenate in (False, True):
            chars, positions = [], []
            for i, unit in enumerate(units):
                value = unit['text']
                if dehyphenate and value in ('-', '\u2010') and i and units[i - 1]['text'].isalpha():
                    following = i + 1
                    newline = False
                    while following < len(units) and units[following]['text'].isspace():
                        newline |= '\n' in units[following]['text']
                        following += 1
                    if newline and following < len(units) and units[following]['text'].isalpha():
                        continue
                normalised = _normalise(value)
                chars.append(normalised)
                positions.extend([i] * len(normalised))
            text = ''.join(chars)
            if not variants or variants[0][0] != text:
                variants.append((text, positions))
    return {'units': units, 'variants': variants}


def _ascii_word(character: str) -> bool:
    return bool(character) and character.isascii() and character.isalnum()


def _rects(units, indices) -> list[dict]:
    groups = {}
    for index in indices:
        unit = units[index]
        rect = unit['rect']
        if not rect:
            continue
        # Keep separate physical text lines / PDF blocks separate even if two
        # columns happen to have the same y coordinate.
        key = unit['line']
        if key not in groups:
            groups[key] = dict(rect)
        else:
            old = groups[key]
            x1, y1 = max(old['x'] + old['width'], rect['x'] + rect['width']), max(old['y'] + old['height'], rect['y'] + rect['height'])
            old['x'], old['y'] = min(old['x'], rect['x']), min(old['y'], rect['y'])
            old['width'], old['height'] = x1 - old['x'], y1 - old['y']
    return list(groups.values())


def _locate(text: str, index: dict) -> dict | None:
    queries = {_normalise(text), _normalise(_without_line_hyphens(text))}
    queries.discard('')
    # Tiny fragments (page numbers, single glyphs) do not form useful paragraphs.
    if not queries or min(map(len, queries)) < 3:
        return None
    found = {}
    units = index['units']
    for query in queries:
        for haystack, positions in index['variants']:
            start = haystack.find(query)
            while start >= 0:
                end = start + len(query)
                indices = tuple(dict.fromkeys(positions[start:end]))
                first, last = indices[0], indices[-1]
                before = units[first - 1]['text'][-1:] if first else ''
                after = units[last + 1]['text'][:1] if last + 1 < len(units) else ''
                # Don't match a short tracked word inside a longer English word.
                if not ((_ascii_word(before) and _ascii_word(query[:1])) or (_ascii_word(after) and _ascii_word(query[-1:]))):
                    # Raw and dehyphenated variants can describe the same
                    # physical occurrence with/without its line-end hyphen.
                    # Uniqueness belongs to the first/last glyph range.
                    location = (first, last)
                    found[location] = tuple(sorted(set(found.get(location, ())) | set(indices)))
                    if len(found) > 1:
                        return None  # Repeated text is ambiguous, never pick first.
                start = haystack.find(query, start + 1)
    if len(found) != 1:
        return None
    indices = next(iter(found.values()))
    rectangles = _rects(units, indices)
    if not rectangles or len(rectangles) > MAX_RECTS:
        return None
    return {'indices': frozenset(indices), 'rects': rectangles}


def build_alignment(original_path: Path, original_page: int, translated_path: Path, tracking: dict) -> dict:
    """Return only uniquely located paragraphs from a real translation trace."""
    records, valid = _records(tracking)
    empty = {'status': 'unavailable', 'paragraphs': [], 'method': 'babeldoc-tracking', 'schema_version': 1}
    if not valid or not records:
        return {**empty, 'reason': 'no_tracking' if valid else 'invalid_tracking'}
    try:
        original = _page_index(original_path, original_page)
        translated = _page_index(translated_path, 1)
    except Exception:
        # Translation itself remains readable if its text layer cannot be mapped.
        return {**empty, 'reason': 'text_unavailable'}
    if not original['variants'][0][0] or not translated['variants'][0][0]:
        return {**empty, 'reason': 'no_text'}
    candidates, seen, skipped = [], set(), 0
    for record in records:
        source = record.get('pdf_unicode')
        target = _translated_text(record)
        if not isinstance(source, str) or not source.strip() or len(source) > MAX_PARAGRAPH_CHARS or not target:
            skipped += 1
            continue
        source = source.strip()
        identity = (_normalise(source), _normalise(target))
        if identity in seen:
            continue
        seen.add(identity)
        left, right = _locate(source, original), _locate(target, translated)
        if not left or not right:
            skipped += 1
            continue
        candidates.append({'id': 'p-' + hashlib.sha256(('\0'.join(identity)).encode()).hexdigest()[:24],
                           'original': {'text': source, 'rects': left['rects']},
                           'translation': {'text': target, 'rects': right['rects']},
                           'method': 'babeldoc-tracking', '_left': left['indices'], '_right': right['indices']})
    # Overlapping, contradictory records can occur in grouped translation logs.
    # Refuse them rather than mapping one visible passage to multiple passages.
    conflicts = set()
    for i, left in enumerate(candidates):
        for j in range(i + 1, len(candidates)):
            right = candidates[j]
            if left['_left'] & right['_left'] or left['_right'] & right['_right']:
                conflicts.update((i, j))
    paragraphs = [{k: v for k, v in row.items() if not k.startswith('_')} for i, row in enumerate(candidates) if i not in conflicts]
    skipped += len(conflicts)
    if not paragraphs:
        return {**empty, 'reason': 'unmatched', 'unmatched_count': skipped}
    return {'status': 'partial' if skipped else 'ready', 'paragraphs': paragraphs,
            'method': 'babeldoc-tracking', 'schema_version': 1,
            'matched_count': len(paragraphs), 'unmatched_count': skipped}
