"""Real-PDF paragraph links: provenance, ambiguity and visible coordinates."""
import math

import fitz
import pytest

from app import alignment


def write_pdf(path, lines, *, rotate=0, crop=False):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        for x, y, text in lines:
            font = 'china-s' if any(ord(char) > 0x3000 for char in text) else 'helv'
            page.insert_text((x, y), text, fontsize=12, fontname=font)
        if crop:
            page.set_cropbox(fitz.Rect(20, 20, 580, 780))
        page.set_rotation(rotate)
        doc.save(path)
    return path


def tracking(*pairs):
    return {'page': [{'paragraph': [{'pdf_unicode': source, 'input': source, 'output': target,
             'placeholders': [], 'llm_translate_trackers': [{'prompt': 'PRIVATE PROMPT MUST NOT ESCAPE'}]}
            for source, target in pairs]}], 'cross_page': [], 'cross_column': []}


def test_true_trace_links_english_to_chinese_without_using_page_order(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'A retrieval method preserves useful evidence.'),
        (45, 220, 'The experiment measures relevant documents.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '实验衡量相关文档。'), (45, 220, '检索方法保留有用证据。')])
    result = alignment.build_alignment(source, 1, target, tracking(
        ('A retrieval method preserves useful evidence.', '检索方法保留有用证据。'),
        ('The experiment measures relevant documents.', '实验衡量相关文档。')))
    assert result['status'] == 'ready' and result['matched_count'] == 2
    first = result['paragraphs'][0]
    assert first['translation']['rects'][0]['y'] > first['original']['rects'][0]['y']
    assert 'PRIVATE' not in str(result) and 'llm_translate' not in str(result)
    assert first['method'] == 'babeldoc-tracking'


def test_line_wrap_and_line_end_hyphen_are_normalized(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'The inter-\nnational experiment measures\nretrieval quality.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '国际实验衡量\n检索质量。')])
    result = alignment.build_alignment(source, 1, target,
        tracking(('The international experiment measures retrieval quality.', '国际实验衡量检索质量。')))
    assert result['status'] == 'ready'
    assert len(result['paragraphs'][0]['original']['rects']) == 3
    assert len(result['paragraphs'][0]['translation']['rects']) == 2
    assert alignment._normalise('ofﬁce\u00ad\u200b') == 'office'


def test_duplicate_visible_text_is_ambiguous_not_first_match(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'Repeated passage.'), (45, 220, 'Repeated passage.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '重复段落。')])
    result = alignment.build_alignment(source, 1, target, tracking(('Repeated passage.', '重复段落。')))
    assert result['status'] == 'unavailable' and result['paragraphs'] == []
    assert result['reason'] == 'unmatched'


def test_partial_mapping_preserves_good_pairs_without_guessing_missing_ones(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'This paragraph has actual evidence.'), (45, 220, 'A missing translation paragraph.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '这段有实际证据。'), (45, 220, '这里完全不是对应的翻译。')])
    result = alignment.build_alignment(source, 1, target, tracking(
        ('This paragraph has actual evidence.', '这段有实际证据。'), ('A missing translation paragraph.', '未显示的译文。')))
    assert result['status'] == 'partial' and len(result['paragraphs']) == 1
    assert result['unmatched_count'] == 1


def test_actual_character_bounds_do_not_highlight_other_text_in_same_span(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'Prefix words. Accurate paragraph. Trailing.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '前言。对应段落。结尾。')])
    result = alignment.build_alignment(source, 1, target, tracking(('Accurate paragraph.', '对应段落。')))
    assert result['status'] == 'ready'
    rect = result['paragraphs'][0]['original']['rects'][0]
    with fitz.open(source) as doc:
        expected = doc[0].search_for('Accurate paragraph.')[0]
        assert rect['x'] * 600 == pytest.approx(expected.x0, abs=.1)
        assert (rect['x'] + rect['width']) * 600 == pytest.approx(expected.x1, abs=.1)


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_rotated_cropped_pages_use_visible_pdf_coordinates(tmp_path, rotation):
    source = write_pdf(tmp_path / 'source.pdf', [(80, 140, 'A rotated paragraph of evidence.')], rotate=rotation, crop=True)
    target = write_pdf(tmp_path / 'target.pdf', [(90, 150, 'Translated rotated evidence.')], rotate=rotation, crop=True)
    result = alignment.build_alignment(source, 1, target, tracking(('A rotated paragraph of evidence.', 'Translated rotated evidence.')))
    assert result['status'] == 'ready'
    for path, side, text in [(source, 'original', 'A rotated paragraph of evidence.'), (target, 'translation', 'Translated rotated evidence.')]:
        rect = result['paragraphs'][0][side]['rects'][0]
        with fitz.open(path) as doc:
            page = doc[0]
            expected = page.search_for(text)[0] * page.rotation_matrix
            assert rect['x'] * page.rect.width == pytest.approx(expected.x0, abs=.1)
            assert rect['y'] * page.rect.height == pytest.approx(expected.y0, abs=.1)
            assert rect['width'] * page.rect.width == pytest.approx(expected.width, abs=.1)
            assert rect['height'] * page.rect.height == pytest.approx(expected.height, abs=.1)
        assert all(math.isfinite(value) for value in rect.values())
        assert 0 <= rect['x'] < 1 and 0 < rect['width'] <= 1 - rect['x'] + 1e-9


def test_formula_and_style_placeholders_keep_comparison_symbols_and_ignore_regex(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'The condition is x < y and a+b.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, 'Condition: x < y and a+b.')])
    data = tracking(('The condition is x < y and a+b.', "<style id='1'>Condition:</style> x < y and { v 2 }."))
    record = data['page'][0]['paragraph'][0]
    record['placeholders'] = [
        {'type': 'rich_text', 'id': 1, 'left_placeholder': "<style id='1'>", 'right_placeholder': '</style>',
         'left_regex_pattern': '(a+)+$', 'right_regex_pattern': '.*', 'composition_chars': 'Condition:'},
        {'type': 'formula', 'id': 2, 'placeholder': '{v2}', 'regex_pattern': '.*', 'formula_chars': 'a+b'},
    ]
    result = alignment.build_alignment(source, 1, target, data)
    assert result['status'] == 'ready'
    assert result['paragraphs'][0]['translation']['text'] == 'Condition: x < y and a+b.'
    record['placeholders'][1]['formula_chars'] = None
    assert alignment.build_alignment(source, 1, target, data)['status'] == 'unavailable'


def test_cross_column_and_cross_page_records_and_duplicate_tracking_entries(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'Left column evidence.'), (330, 75, 'Right column evidence.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '左栏证据。'), (330, 75, '右栏证据。')])
    data = tracking(('Left column evidence.', '左栏证据。'))
    data['cross_column'] = tracking(('Right column evidence.', '右栏证据。'))['page']
    data['cross_page'] = data['page']
    result = alignment.build_alignment(source, 1, target, data)
    assert result['status'] == 'ready' and len(result['paragraphs']) == 2
    assert result['paragraphs'][0]['id'] != result['paragraphs'][1]['id']
    assert result['paragraphs'][0]['original']['rects'][0]['x'] < .5
    assert result['paragraphs'][1]['original']['rects'][0]['x'] > .5


def test_contradictory_trace_entries_are_not_published_as_two_links(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'Single source paragraph.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, 'First target paragraph.'), (45, 220, 'Second target paragraph.')])
    result = alignment.build_alignment(source, 1, target, tracking(
        ('Single source paragraph.', 'First target paragraph.'), ('Single source paragraph.', 'Second target paragraph.')))
    assert result['status'] == 'unavailable'
    assert result['paragraphs'] == []


def test_invalid_and_oversized_tracking_and_pdf_fail_closed(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'Actual evidence.')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, 'Actual translation.')])
    assert alignment.build_alignment(source, 1, target, {})['reason'] == 'no_tracking'
    assert alignment.build_alignment(source, 1, target, {'page': 'not a list'})['reason'] == 'invalid_tracking'
    data = tracking(('Actual evidence.', 'Actual translation.'))
    data['page'][0]['paragraph'] *= alignment.MAX_RECORDS + 1
    assert alignment.build_alignment(source, 1, target, data)['reason'] == 'invalid_tracking'
    assert alignment.build_alignment(source, 1, target, tracking(('x' * (alignment.MAX_PARAGRAPH_CHARS + 1), 'Actual translation.')))['status'] == 'unavailable'
    assert alignment.build_alignment(source, 3, target, tracking(('Actual evidence.', 'Actual translation.')))['reason'] == 'text_unavailable'
    empty = write_pdf(tmp_path / 'empty.pdf', [])
    assert alignment.build_alignment(empty, 1, target, tracking(('Actual evidence.', 'Actual translation.')))['reason'] == 'no_text'


def test_do_not_match_inside_a_longer_english_word(tmp_path):
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, 'paragraph')])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '图结构。')])
    result = alignment.build_alignment(source, 1, target, tracking(('graph', '图结构。')))
    assert result['status'] == 'unavailable'


def test_same_physical_dehyphenated_match_is_not_ambiguous(tmp_path):
    text = 'The inter-\nnational study measures evidence.'
    source = write_pdf(tmp_path / 'source.pdf', [(45, 75, text)])
    target = write_pdf(tmp_path / 'target.pdf', [(45, 75, '国际研究衡量证据。')])
    result = alignment.build_alignment(source, 1, target, tracking((text, '国际研究衡量证据。')))
    assert result['status'] == 'ready'
    assert len(result['paragraphs'][0]['original']['rects']) == 2


def test_undeclared_html_markup_stays_literal_and_arbitrary_style_tokens_rejected():
    literal = "Example <style id='7'>CSS</style> x < y."
    assert alignment._translated_text({'output': literal, 'placeholders': []}) == literal
    assert alignment._translated_text({'output': literal, 'placeholders': [
        {'type': 'rich_text', 'left_placeholder': 'Example', 'right_placeholder': 'CSS'}]}) is None
    # Unknown nested markup is retained even inside a known style wrapper.
    assert alignment._translated_text({'output': "<style id='1'><style id='7'>CSS</style></style>", 'placeholders': [
        {'type': 'rich_text', 'left_placeholder': "<style id='1'>", 'right_placeholder': '</style>'}]}) == "<style id='7'>CSS</style>"


def test_formula_restoration_is_single_pass():
    result = alignment._translated_text({'output': 'Literal formula: {v1}', 'placeholders': [
        {'type': 'formula', 'id': 1, 'placeholder': '{v1}', 'formula_chars': '{v2}'},
        {'type': 'formula', 'id': 2, 'placeholder': '{v2}', 'formula_chars': 'DO NOT SUBSTITUTE AGAIN'},
    ]})
    assert result == 'Literal formula: {v2}'


@pytest.mark.parametrize('literal', ["<style id='custom'>literal</style>", '<style>literal</style>', '<style media="screen">literal</style>'])
def test_unknown_style_openings_cannot_close_known_outer_wrapper(literal):
    assert alignment._translated_text({'output': "<style id='1'>" + literal + '</style>', 'placeholders': [
        {'type': 'rich_text', 'left_placeholder': "<style id='1'>", 'right_placeholder': '</style>'}]}) == literal
