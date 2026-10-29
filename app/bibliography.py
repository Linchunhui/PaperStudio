"""Private project reference lists; importing metadata never downloads a PDF."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from urllib.parse import urlsplit

import bibtexparser
from bibtexparser.bparser import BibTexParser
from fastapi import HTTPException

from . import auth, core

MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 500


def init_db():
    with core.db() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS bibliography_entries (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            owner_id TEXT NOT NULL, identity_key TEXT NOT NULL, data TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(project_id,identity_key))''')


def safe_url(value):
    value = str(value or '').strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password:
            return value[:4096]
    except ValueError:
        pass
    return ''


def normalize(row):
    if not isinstance(row, dict):
        raise HTTPException(422, '文献条目必须是对象。')
    text = lambda key, limit: str(row.get(key) or '').strip()[:limit]
    title = text('title', 1000)
    if not title:
        raise HTTPException(422, '文献缺少标题，无法导入。')
    doi = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', text('doi', 300), flags=re.I)
    if doi and not re.fullmatch(r'10\.\d{4,9}/\S+', doi, re.I):
        doi = ''
    url = safe_url(row.get('url'))
    if not url and doi:
        url = 'https://doi.org/' + doi
    authors = row.get('authors', [])
    if isinstance(authors, str):
        authors = re.split(r'\s+and\s+|;', authors)
    if not isinstance(authors, list):
        authors = []
    return {'title':title, 'authors':[str(a).strip()[:300] for a in authors[:100] if str(a).strip()],
            'year':text('year', 4) if re.fullmatch(r'\d{4}',text('year',4)) else '',
            'doi':doi, 'url':url, 'abstract':text('abstract',12000),
            'venue':text('venue',500), 'source':'user-imported-bibliography'}


def _ris(text):
    result, current, previous = [], {}, None
    for line in text.splitlines():
        match = re.match(r'^([A-Z0-9]{2})  -\s?(.*)$',line)
        if match:
            key, value = match.groups()
            if key == 'TY':
                if current:
                    raise HTTPException(422, 'RIS 条目缺少 ER 结束标记。')
                current = {'TY':[value]}
            elif key == 'ER':
                if current: result.append(current)
                current, previous = {}, None
                continue
            else:
                current.setdefault(key,[]).append(value)
            previous = key
        elif line.strip() and previous and current:
            current[previous][-1] += ' ' + line.strip()
    if current:
        raise HTTPException(422, 'RIS 文件末尾缺少 ER 结束标记。')
    rows=[]
    for item in result:
        first=lambda *keys: next((item[k][0] for k in keys if item.get(k)), '')
        rows.append({'title':first('TI','T1','CT'), 'authors':item.get('AU',item.get('A1',[])),
            'year':first('PY','Y1')[:4], 'url':first('UR','L1'), 'doi':first('DO'),
            'abstract':first('AB','N2'), 'venue':first('JO','JF','T2')})
    return rows


def parse(content, filename):
    if len(content)>MAX_BYTES:
        raise HTTPException(413, '文献列表超过 2 MiB，请拆分后导入。')
    try:
        text=content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(422, '请从文献管理器导出 UTF-8 文件。') from None
    suffix=filename.lower().rsplit('.',1)[-1]
    try:
        if suffix in {'bib','bibtex'}:
            parser=BibTexParser(common_strings=True)
            parser.ignore_nonstandard_types=False
            database=bibtexparser.loads(text,parser=parser)
            rows=[{'title':e.get('title'),'authors':e.get('author',''),'year':e.get('year',''),
                   'doi':e.get('doi',''),'url':e.get('url') or ('https://arxiv.org/abs/'+e['eprint'] if e.get('archiveprefix','').lower()=='arxiv' and e.get('eprint') else ''),
                   'abstract':e.get('abstract',''),'venue':e.get('journal',e.get('booktitle',''))} for e in database.entries]
        elif suffix=='ris':
            rows=_ris(text)
        elif suffix=='csv':
            reader=csv.DictReader(io.StringIO(text))
            rows=[]
            for e in reader:
                rows.append({'title':e.get('Title',e.get('title','')),'authors':e.get('Author',e.get('authors','')),
                    'year':e.get('Publication Year',e.get('year','')),'url':e.get('Url',e.get('URL',e.get('url',''))),
                    'doi':e.get('DOI',e.get('doi','')),'abstract':e.get('Abstract Note',e.get('abstract','')),
                    'venue':e.get('Publication Title',e.get('venue',''))})
                if len(rows)>MAX_ENTRIES: break
        else:
            raise HTTPException(422,'支持 BibTeX (.bib)、RIS 和 Zotero CSV。')
    except HTTPException:
        raise
    except Exception:
        # Parser exception text can contain private local attachment paths.
        raise HTTPException(422, '文献列表无法解析，请检查格式或重新导出。') from None
    if not rows:
        raise HTTPException(422,'没有识别到文献，请检查文件格式。')
    if len(rows)>MAX_ENTRIES:
        raise HTTPException(413,'一次最多导入 500 条文献，请拆分文件。')
    entries, warnings=[],[]
    for index,row in enumerate(rows,1):
        try: entries.append(normalize(row))
        except HTTPException: warnings.append(f'第 {index} 条缺少标题，已跳过。')
    return {'entries':entries,'warnings':warnings,
            'notice':'仅导入文献元数据；附件路径、个人笔记和密钥不会导入，不会自动下载 PDF 或调用模型。'}


def entries(project_id):
    project_id=core.get_project(project_id)['id']
    with core.db() as con:
        return [json.loads(r['data'])|{'id':r['id']} for r in con.execute(
            'SELECT id,data FROM bibliography_entries WHERE project_id=? ORDER BY created_at,id',(project_id,))]


def save(project_id, rows):
    project_id=core.get_project(project_id,write=True)['id']
    cleaned=[normalize(row) for row in rows]
    inserted=0
    with core.db() as con:
        for row in cleaned:
            identity = row['doi'].lower() or row['url'] or re.sub(r'\W','',row['title'].casefold())
            identity = hashlib.sha256(identity.encode()).hexdigest()
            cursor=con.execute('INSERT OR IGNORE INTO bibliography_entries VALUES(?,?,?,?,?,?)',
                (core.uid(),project_id,auth.current_user_id(),identity,json.dumps(row,ensure_ascii=False),core.now()))
            inserted+=cursor.rowcount
    return {'inserted':inserted,'duplicates':len(cleaned)-inserted,'entries':entries(project_id)}


def bibtex(rows):
    database=bibtexparser.bibdatabase.BibDatabase()
    database.entries=[]
    for number,row in enumerate(rows,1):
        item={'ENTRYTYPE':'misc','ID':f'paperstudio{number}','title':row['title']}
        for key in ('year','doi','url'):
            if row.get(key): item[key]=str(row[key])
        if row.get('authors'): item['author']=' and '.join(row['authors'])
        database.entries.append(item)
    return bibtexparser.dumps(database)
