"""Generate self-authored vector assets for the public, model-free demo."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import fitz
from app.sample import sample_pdf


def build():
    folder=ROOT/'demo'/'assets';folder.mkdir(parents=True,exist_ok=True)
    for name,chinese in [('original',False),('translation',True)]:
        data=sample_pdf(chinese)
        (folder/('sample.pdf' if not chinese else 'sample.zh.pdf')).write_bytes(data)
        with fitz.open(stream=data,filetype='pdf') as document:
            for number,page in enumerate(document,1):
                (folder/f'{name}-{number}.svg').write_text(page.get_svg_image(text_as_path=False))
    (folder/'example-notes.md').write_text('# 阅读与证据 · 自编示例笔记\n\n> A highlight is an anchor, not an explanation.\n\n来源：自编阅读文档，第 1 页。\n\n我的理解：划线只标出值得回来的位置，还需要写清它对当前研究问题意味着什么。\n\n这是一份预制示例，不是对已发表论文的解读。\n')


if __name__=='__main__': build()
