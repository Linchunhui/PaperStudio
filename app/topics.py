"""Offline topic packages, evidence review and immutable version publication.

Packages under config/topics/<slug>/manifest.json are operator-managed source
assets, not uploads. No models, corpus access or private review records are bundled.
"""
import hashlib
import hmac
from html import escape
import json
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Literal

from . import auth, core

router = APIRouter()
ASSETS = core.ROOT / 'config' / 'topics'
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
SHA = re.compile(r'[0-9a-f]{64}')


def package_path(topic, filename):
    if not SLUG.fullmatch(topic) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', filename):
        raise ValueError('Invalid topic asset path')
    root = ASSETS.resolve()
    path = root / topic / filename
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError('Topic asset escapes package')
    return path


def checked_asset(topic, filename, digest):
    content = package_path(topic, filename).read_bytes()
    if not SHA.fullmatch(digest) or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('Topic asset checksum mismatch')
    return content


def validate_change(topic, change):
    if not SLUG.fullmatch(change['id']) or not change.get('evidence'):
        raise ValueError('An update needs an ID and source evidence')
    if 'review' in change:
        raise ValueError('Reviews must be created by a signed-in administrator')
    for evidence in change['evidence']:
        url = urlsplit(evidence['source_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Evidence needs a public HTTPS source URL')
        source = checked_asset(topic, evidence['snapshot'], evidence['source_sha256']).decode('utf-8')
        start, end = evidence['start'], evidence['end']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source)
                or source[start:end] != evidence['quote']):
            raise ValueError('Evidence quote does not match its source snapshot')


def init_topics():
    with core.db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS topic_versions (
            topic_id TEXT NOT NULL, version INTEGER NOT NULL, parent INTEGER,
            title TEXT NOT NULL, description TEXT NOT NULL, baseline TEXT NOT NULL,
            baseline_sha256 TEXT NOT NULL, changes TEXT NOT NULL,
            created_at TEXT NOT NULL, published_at TEXT, publisher TEXT,
            PRIMARY KEY(topic_id, version),
            FOREIGN KEY(topic_id,parent) REFERENCES topic_versions(topic_id,version))""")
    if not ASSETS.is_dir():
        return
    for path in sorted(ASSETS.glob('*/manifest.json')):
        topic = path.parent.name
        manifest = json.loads(package_path(topic, 'manifest.json').read_text())
        baseline = checked_asset(topic, manifest['baseline'], manifest['baseline_sha256']).decode('utf-8')
        for item in manifest['versions']:
            version, parent = item['version'], item.get('parent')
            if type(version) is not int or version < 1 or (version == 1) != (parent is None):
                raise ValueError('Invalid topic version lineage')
            if parent is not None and (type(parent) is not int or not 0 < parent < version):
                raise ValueError('Invalid parent version')
            changes = item.get('changes', [])
            if version == 1 and changes:
                raise ValueError('Baseline cannot contain unreviewed updates')
            if len({c['id'] for c in changes}) != len(changes):
                raise ValueError('Duplicate update IDs')
            for change in changes:
                validate_change(topic, change)
            with core.db() as con:
                existing = con.execute('SELECT * FROM topic_versions WHERE topic_id=? AND version=?',
                                       (topic, version)).fetchone()
                if existing:
                    original = [{k: v for k, v in c.items() if k != 'review'}
                                for c in json.loads(existing['changes'])]
                    if (original != changes or existing['baseline_sha256'] != manifest['baseline_sha256']
                            or existing['parent'] != parent or existing['title'] != manifest['title']
                            or existing['description'] != manifest.get('description', '')):
                        raise ValueError('Existing topic versions are immutable; add a new version')
                    continue
                stamp = core.now()
                con.execute('INSERT INTO topic_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (topic, version, parent, manifest['title'], manifest.get('description', ''), baseline,
                     manifest['baseline_sha256'], json.dumps(changes, ensure_ascii=False), stamp,
                     stamp if version == 1 else None, 'baseline-import' if version == 1 else None))


def session(request, admin=False):
    value = auth.session_from_token(request.cookies.get(auth.COOKIE))
    if not value:
        raise HTTPException(401, '请先登录')
    if admin and not value['user']['is_admin']:
        raise HTTPException(403, '仅管理员可审核与发布')
    if request.method == 'POST' and not hmac.compare_digest(
            request.headers.get('x-csrf-token', ''), value['csrf_token']):
        raise HTTPException(403, '请求校验已失效，请刷新页面')
    return value


def draft(con, topic, version):
    row = con.execute('SELECT * FROM topic_versions WHERE topic_id=? AND version=?', (topic, version)).fetchone()
    if row is None:
        raise HTTPException(404, '专题版本不存在')
    if row['published_at']:
        raise HTTPException(409, '该版本已发布')
    return row


class ReviewInput(BaseModel):
    version: int = Field(ge=1)
    change_id: str = Field(min_length=1, max_length=64)
    decision: Literal['approved', 'rejected']
    note: str = Field(min_length=1, max_length=1200)


class PublishInput(BaseModel):
    version: int = Field(ge=1)


@router.post('/api/admin/topics/{topic}/review', status_code=204)
def review(topic: str, body: ReviewInput, request: Request):
    user = session(request, admin=True)['user']
    if not body.note.strip():
        raise HTTPException(422, '请填写审核依据')
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = draft(con, topic, body.version)
        changes = json.loads(row['changes'])
        change = next((c for c in changes if c['id'] == body.change_id), None)
        if not change:
            raise HTTPException(404, '更新项不存在')
        if 'review' in change:
            raise HTTPException(409, '该更新项已有审核记录')
        change['review'] = dict(decision=body.decision, note=body.note.strip(),
                                reviewer=user['id'], reviewed_at=core.now())
        con.execute('UPDATE topic_versions SET changes=? WHERE topic_id=? AND version=?',
                    (json.dumps(changes, ensure_ascii=False), topic, body.version))


@router.post('/api/admin/topics/{topic}/publish', status_code=204)
def publish(topic: str, body: PublishInput, request: Request):
    user = session(request, admin=True)['user']
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = draft(con, topic, body.version)
        changes = json.loads(row['changes'])
        if not changes or any('review' not in c for c in changes) or not any(
                c['review']['decision'] == 'approved' for c in changes):
            raise HTTPException(409, '请审核所有更新项，并至少通过一项')
        latest = con.execute('SELECT max(version) FROM topic_versions WHERE topic_id=? AND published_at IS NOT NULL',
                             (topic,)).fetchone()[0]
        if row['parent'] != latest:
            raise HTTPException(409, '草稿基线已更新')
        # Recheck source bytes immediately before publication.
        try:
            for change in changes:
                validate_change(topic, {k: v for k, v in change.items() if k != 'review'})
        except (OSError, ValueError, KeyError):
            raise HTTPException(409, '原文证据发生变化，请检查专题包') from None
        con.execute('UPDATE topic_versions SET published_at=?,publisher=? WHERE topic_id=? AND version=?',
                    (core.now(), user['id'], topic, body.version))


def shell(title, body, *, csrf='', topic=''):
    metadata = (f'<meta name="csrf-token" content="{escape(csrf)}">'
                '<script src="/static/topic-review.js" defer></script>') if csrf else ''
    return HTMLResponse(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · PaperStudio</title>'
        f'<link rel="stylesheet" href="/static/topics.css">{metadata}</head>'
        f'<body class="topic-shell" data-topic="{escape(topic)}"><main>'
        f'<nav class="topic-nav"><a href="/">工作台</a><a href="/topics">全部专题</a></nav>{body}</main></body></html>',
        headers={'Cache-Control': 'no-store', 'Content-Security-Policy':
                 "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"})


def update_html(change):
    body = f'<h2>{escape(change["title"])}</h2>'
    body += ''.join(f'<p>{escape(p)}</p>' for p in change['paragraphs'])
    body += f'<p>{escape(change.get("practice", ""))}</p><details class="topic-evidence"><summary>原文依据</summary><ul>'
    for item in change['evidence']:
        body += (f'<li><blockquote>{escape(item["quote"])}</blockquote>'
                 f'<a href="{escape(item["source_url"])}" rel="noopener noreferrer" target="_blank">'
                 f'{escape(item["locator"])} · {escape(item["document_version_id"])}</a></li>')
    return body + '</ul></details>'


@router.get('/topics', response_class=HTMLResponse)
def index(request: Request):
    session(request)
    with core.db() as con:
        rows = con.execute('SELECT topic_id,title,description,max(version) AS version FROM topic_versions '
                           'WHERE published_at IS NOT NULL GROUP BY topic_id ORDER BY title').fetchall()
    body = '<h1>专题知识</h1><p>沿研究问题阅读方法演进，循证据返回原文。</p>'
    for row in rows:
        body += (f'<article class="topic-card"><h2><a href="/topics/{escape(row["topic_id"])}">'
                 f'{escape(row["title"])}</a></h2><p>{escape(row["description"])}</p><p>v{row["version"]}</p></article>')
    return shell('专题知识', body if rows else body + '<p>暂无专题。管理员可安装经过核验的专题包。</p>')


@router.get('/topics/{topic}', response_class=HTMLResponse)
def read(topic: str, request: Request, version: int | None = Query(None, ge=1)):
    current = session(request)
    with core.db() as con:
        history = con.execute('SELECT * FROM topic_versions WHERE topic_id=? AND published_at IS NOT NULL ORDER BY version',
                              (topic,)).fetchall()
    row = next((r for r in history if r['version'] == version), None) if version else (history[-1] if history else None)
    if not row:
        raise HTTPException(404, '专题版本尚未发布或不存在')
    body = f'<h1>{escape(row["title"])}</h1><p>{escape(row["description"])}</p><nav class="topic-version">'
    body += ''.join(f'<a href="/topics/{escape(topic)}?version={r["version"]}">v{r["version"]}</a>' for r in history) + '</nav>'
    if current['user']['is_admin']:
        body += f'<p><a href="/topics/{escape(topic)}/review">审核与发布</a></p>'
    body += '<article class="topic-card">' + ''.join(f'<p>{escape(p)}</p>' for p in row['baseline'].split('\n\n')) + '</article>'
    # Follow the actual parent chain, not all earlier version numbers.
    chain, cursor = [], row
    while cursor:
        chain.append(cursor)
        cursor = next((r for r in history if r['version'] == cursor['parent']), None)
    for ancestor in reversed(chain):
        for change in json.loads(ancestor['changes']):
            if change.get('review', {}).get('decision') == 'approved':
                body += f'<article class="topic-update"><p>v{ancestor["version"]}</p>{update_html(change)}</article>'
    return shell(row['title'], body)


@router.get('/topics/{topic}/review', response_class=HTMLResponse)
def review_page(topic: str, request: Request):
    current = session(request, admin=True)
    with core.db() as con:
        row = con.execute('SELECT * FROM topic_versions WHERE topic_id=? ORDER BY version DESC LIMIT 1',
                          (topic,)).fetchone()
    if not row:
        raise HTTPException(404, '专题不存在')
    body = f'<h1>{escape(row["title"])}：审核与发布</h1><p>v{row["version"]} · 基线 v{row["parent"]}</p>'
    for change in json.loads(row['changes']):
        body += f'<article class="topic-card">{update_html(change)}'
        decision = change.get('review')
        if decision:
            body += f'<p>{escape(decision["decision"])} · {escape(decision["note"])}</p><p class="topic-meta">{escape(decision["reviewed_at"])}</p>'
        elif not row['published_at']:
            body += f'<form data-topic-action="review" data-version="{row["version"]}" data-change="{escape(change["id"])}">' \
                    '<label>审核依据<textarea name="note" required maxlength="1200"></textarea></label>' \
                    '<div class="topic-actions"><button name="decision" value="approved">通过</button>' \
                    '<button name="decision" value="rejected">退回</button></div></form>'
        body += '</article>'
    if row['published_at']:
        body += '<p>已发布；历史版本不可修改。</p>'
    else:
        body += f'<form data-topic-action="publish" data-version="{row["version"]}"><button>发布已通过更新</button></form>'
    return shell('审核与发布', body + '<p id="topic-feedback" role="alert"></p>', csrf=current['csrf_token'], topic=topic)
