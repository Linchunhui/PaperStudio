"""Self-authored PDF fixture: real vector pages, never presented as a research paper."""
from pathlib import Path
import fitz
from . import core

INK = (0.16, 0.25, 0.21)
GREEN = (0.18, 0.42, 0.34)
PALE = (0.94, 0.96, 0.93)


def _text(page, text, rect, size=11, chinese=False, color=INK):
    spare = page.insert_textbox(fitz.Rect(rect), text, fontname='china-s' if chinese else 'helv', fontsize=size, color=color, lineheight=1.6)
    if spare < 0:
        raise RuntimeError('Sample PDF text overflow')


def sample_pdf(chinese=False):
    doc = fitz.open()
    for n in range(2):
        page = doc.new_page(width=612, height=792)
        page.draw_rect(fitz.Rect(46, 42, 566, 47), fill=GREEN, color=GREEN)
        _text(page, 'PAPERSTUDIO  /  READING WORKBENCH  /  SAMPLE DOCUMENT', (46, 61, 566, 89), 8, color=GREEN)
        if n == 0:
            _text(page, 'Reading with evidence' if not chinese else '让阅读与证据保持连接', (46, 105, 566, 160), 26, chinese)
            _text(page, 'Self-authored interface fixture. Not a published research paper.' if not chinese else '自行编写的界面验收文档，非已发表研究论文。', (46, 170, 566, 211), 10, chinese)
            _text(page, '1. Start with a question' if not chinese else '1. 从研究问题出发', (46, 235, 566, 273), 16, chinese)
            _text(page,
                'A paper becomes useful when its mechanism answers a question you care about. Before collecting conclusions, write down the problem, the earlier approach, and the gap the authors try to close.\n\nA highlight is an anchor, not an explanation. Keep the quoted sentence and your interpretation separate. A note should say why the passage matters to the current project.' if not chinese else
                '当论文中的机制回应了你关心的问题，阅读才会变得有用。收集结论之前，先写下问题是什么、前人如何处理，以及作者试图弥补的缺口。\n\n高亮只是定位，不能代替解释。把引用原句与自己的理解分开保存，笔记应说明这一段为什么对当前项目有价值。',
                (46, 282, 566, 453), 12, chinese)
            page.draw_rect(fitz.Rect(46, 482, 566, 606), fill=PALE, color=(0.78, 0.83, 0.78))
            _text(page, 'TRY IT' if not chinese else '试着操作', (65, 501, 547, 531), 10, chinese, GREEN)
            _text(page, 'Select a word or sentence above. Add a note in the right panel, save it, and refresh the page. The quote, page number and highlight should still be there.' if not chinese else '选中上方的词语或句子，在右侧填写笔记并保存。刷新页面后，原句、页码和高亮位置都应保留。', (65, 539, 547, 595), 11, chinese)
        else:
            _text(page, 'From notes to understanding' if not chinese else '从阅读笔记形成自己的理解', (46, 105, 566, 170), 24, chinese)
            _text(page, '2. Keep claims and evidence connected' if not chinese else '2. 让主张与证据保持对应', (46, 206, 566, 247), 16, chinese)
            _text(page, 'A useful reading summary connects the question, the mechanism and the evidence. It preserves conditions: which data were used, what changed in an ablation, and which metric moved.\n\nFinishing a paper does not resolve every question. Record uncertainty and possible follow-up experiments. A note summary should organize your selected evidence rather than pretend that notes cover the whole paper.' if not chinese else '有用的读后总结会连接研究问题、方法机制和实验证据，同时保留实验条件：使用了什么数据、消融改变了什么，以及哪个指标发生了变化。\n\n读完论文不意味着所有问题都已解决。记录仍然不确定的地方与后续验证想法。笔记总结应整理你选取的证据，不能假装这些笔记覆盖了论文全文。', (46, 266, 566, 468), 12, chinese)
            _text(page, '3. Preserve the source version' if not chinese else '3. 保留来源版本', (46, 506, 566, 547), 16, chinese)
            _text(page, 'Notes bind to the PDF hash, page and normalized selection rectangles. If the translated PDF changes, older annotations remain in the notebook and are marked as belonging to the older version.' if not chinese else '笔记绑定 PDF 哈希、页码和归一化选区坐标。替换译文 PDF 后，旧批注仍保留在笔记本中，但会标记为旧版本，避免高亮落到错误文字上。', (46, 558, 566, 668), 12, chinese)
        page.draw_line(fitz.Point(46, 728), fitz.Point(566, 728), color=(0.8, 0.84, 0.8))
        _text(page, ('Original English' if not chinese else '对应中文译文') + f'  |  {n+1} / 2', (46, 742, 566, 770), 9, chinese)
    doc.set_metadata({'title': 'PaperStudio reading fixture / 自编示例', 'author': 'PaperStudio', 'subject': 'Interface validation fixture, not a research paper'})
    payload = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return payload


def seed_sample():
    with core.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        if con.execute("SELECT value FROM app_settings WHERE key='sample-seeded'").fetchone():
            return
    paper = core.add_paper(sample_pdf(False), 'inbox', '阅读与证据 · 自编 PDF 示例（非论文）', 'reading-sample.pdf')
    core.attach_translation(paper['id'], sample_pdf(True), 'reading-sample.zh.pdf')
    with core.db() as con:
        con.execute("INSERT OR REPLACE INTO app_settings VALUES('sample-seeded',?)", (paper['id'],))


def create_personal_sample():
    """The same real reading surface, with clearly self-authored sample assets."""
    from fastapi import HTTPException
    key='personal-sample:'+core.current_user_id()
    with core.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        previous=con.execute('SELECT value FROM app_settings WHERE key=?',(key,)).fetchone()
    if previous:
        try:
            paper=core.get_paper(previous['value'])
            core.link_paper(paper['id'],'inbox')
            return {'paper_id':paper['id'],'project_id':core.resolve_project_id('inbox'),
                    'url':f"/read/{paper['id']}?project={core.resolve_project_id('inbox')}",
                    'sample':True,'model_called':False}
        except HTTPException:
            pass
    paper = core.add_paper(sample_pdf(False), 'inbox', '阅读与证据 · 自编 PDF 示例（非论文）', 'reading-sample.pdf')
    if not paper.get('translated_sha256'):
        core.attach_translation(paper['id'], sample_pdf(True), 'reading-sample.zh.pdf')
    with core.db() as con:
        con.execute('INSERT OR REPLACE INTO app_settings VALUES(?,?)',(key,paper['id']))
    return {'paper_id': paper['id'], 'project_id': core.resolve_project_id('inbox'),
            'url': f"/read/{paper['id']}?project={core.resolve_project_id('inbox')}",
            'sample': True, 'model_called': False}
