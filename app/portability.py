"""Explicit, permission-scoped exports. PDF assets and server paths are excluded."""
from __future__ import annotations
import html
import io
import json
import zipfile
from fastapi import HTTPException
from . import auth, bibliography, core, jobs, research


def snapshot(project_id, *, include_children=False, include_shared=False, include_generated=False):
    project=core.get_project(project_id)
    ids={project['id']}
    if include_children:
        visible=set(core.visible_project_ids())
        with core.db() as con:
            rows=con.execute('SELECT id,parent_id FROM projects').fetchall()
        changed=True
        while changed:
            before=len(ids)
            ids.update(r['id'] for r in rows if r['parent_id'] in ids and r['id'] in visible)
            changed=len(ids)!=before
    materials=[]
    for identifier in sorted(ids):
        current=core.get_project(identifier)
        with core.db() as con:
            papers=con.execute('SELECT paper_id FROM project_papers WHERE project_id=? ORDER BY paper_id',(identifier,)).fetchall()
        if len(papers)>2000:
            raise HTTPException(413,'单个项目超过 2000 篇，请按子项目分别导出。')
        for record in papers:
            paper=core.get_paper(record['paper_id'])
            notes=[{'quote':n['quote'],'note':n['note'],'page':n['page'],'side':n['side'],
                    'source_sha':n['source_sha'],'is_current':n['is_current'],'visibility':n.get('visibility','private'),
                    'author':n.get('author',''),'owner_id':n.get('owner_id')}
                   for n in core.annotation_list(paper['id'],identifier)
                   if include_shared or n.get('owner_id')==auth.current_user_id()]
            metadata={'title':paper['title'],'authors':[],'year':'','url':'','doi':''}
            with core.db() as con:
                linked=con.execute('SELECT c.data FROM paper_catalog_links l JOIN catalog_papers c ON c.id=l.catalog_paper_id WHERE l.paper_id=?',(paper['id'],)).fetchone()
                if linked:
                    data=json.loads(linked[0]);metadata.update({k:data.get(k) or metadata.get(k,'') for k in ('title','authors','year','url','doi')})
            materials.append({'paper_id':paper['id'],'project':current['name'],'title':paper['title'],
                'source_sha':paper['sha256'],'page_count':paper['page_count'],'metadata':metadata,'notes':notes})
    generated=[]
    if include_generated:
        for identifier in sorted(ids):
            with core.db() as con:
                simple=con.execute("SELECT id FROM jobs WHERE project_id=? AND status='succeeded' AND (owner_id=? OR ?=1)",(identifier,auth.current_user_id(),int(include_shared))).fetchall()
                advanced=con.execute("SELECT id FROM research_jobs WHERE project_id=? AND status='succeeded' AND (owner_id=? OR ?=1)",(identifier,auth.current_user_id(),int(include_shared))).fetchall()
            for record in simple:
                try: value=jobs.get_job(record['id'])
                except HTTPException: continue
                if value.get('result_markdown'):
                    generated.append({'kind':value['kind'],'paper_id':value.get('paper_id'),
                        'markdown':value['result_markdown'],'source_sha':value.get('source_sha'),
                        'coverage':value.get('coverage'), 'generated':True})
            for record in advanced:
                try: value=research.get_job(record['id'])
                except HTTPException: continue
                generated.append({'kind':value['kind'],'question':value['question'],'result':value['result'],
                                  'coverage':value['coverage'],'generated':True})
    refs=[]
    for identifier in sorted(ids): refs.extend(bibliography.entries(identifier))
    return {'format':'paperstudio-project-v1','exported_at':core.now(),'project':project['name'],
        'scope':{'include_children':include_children,'include_shared':include_shared,'include_generated':include_generated},
        'notice':'导出包含所选范围内的笔记和研究成果，请检查后再分享；不包含 PDF、服务配置或 API 密钥。生成内容不等同于作者原文。',
        'papers':materials,'references':refs,'generated':generated}


def markdown(data):
    lines=['# '+data['project'],'',data['notice'],'','导出时间：'+data['exported_at'],'']
    for paper in data['papers']:
        lines.extend(['## '+paper['title'],'','项目：'+paper['project'],f"原文 SHA256：`{paper['source_sha']}`",''])
        if paper['metadata'].get('url'): lines.extend(['来源：'+paper['metadata']['url'],''])
        for note in paper['notes']:
            lines.extend([f"### {'原文' if note['side']=='original' else '译文'}第 {note['page']} 页"+(' · 旧版本' if not note['is_current'] else ''),'',
                '笔记作者：'+note['author'], '', *['> '+line for line in note['quote'].splitlines()],'',note['note'] or '（仅高亮）',''])
    if data['references']:
        lines.extend(['## 文献清单',''])
        for ref in data['references']:
            lines.extend(['- '+ref['title']+(' — '+ref['url'] if ref['url'] else '')])
    for output in data['generated']:
        lines.extend(['','## AI 研究成果 · '+output['kind'],'',output.get('question','')])
        if output.get('markdown'): lines.append(output['markdown'])
        else:
            result=output.get('result') or {}
            for key in ('claims','knowledge','lineage'):
                for claim in result.get(key,[]):
                    lines.extend(['',claim.get('text') or claim.get('label') or '', ''])
                    for citation in claim.get('citations',[]):
                        lines.append('> '+str(citation.get('quote',citation.get('excerpt',''))))
                        lines.append('来源：'+str(citation.get('paper_title',citation.get('source_id','')))+'；页码：'+str(citation.get('page','未提供')))
            lines.extend(result.get('unanswered',[]))
        lines.extend(['','证据范围：'+json.dumps(output.get('coverage'),ensure_ascii=False),''])
    return '\n'.join(lines)


def html_document(data):
    title=html.escape(data['project'])
    text=html.escape(markdown(data))
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>{title} · PaperStudio</title><style>body{{max-width:960px;margin:40px auto;padding:0 24px;background:#f7f8f4;color:#203b30;font:16px/1.9 system-ui,sans-serif}}h1{{font-size:30px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;background:white;padding:30px;border:1px solid #dce5db;border-radius:16px}}footer{{font-size:13px;color:#53685a}}@media print{{body{{margin:0;background:white}}pre{{border:0;padding:0}}}}</style><h1>{title}</h1><pre>{text}</pre><footer>由 PaperStudio 导出 · 原文摘录、个人笔记与 AI 生成内容分别标记。</footer></html>'''


def archive(data):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as target:
        target.writestr('project.json',json.dumps(data,ensure_ascii=False,indent=2))
        target.writestr('notes.md',markdown(data))
        target.writestr('reading.html',html_document(data))
        target.writestr('references.bib',bibliography.bibtex([p['metadata'] for p in data['papers']]+data['references']))
    return buffer.getvalue()
