"""Account-owned votes and stars, shared consistently across paper surfaces."""
import json
import re
from fastapi import HTTPException
from . import auth, catalog, core


def init_feedback():
    with core.db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS paper_feedback (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subject TEXT NOT NULL, vote INTEGER NOT NULL DEFAULT 0 CHECK(vote IN(-1,0,1)),
            starred INTEGER NOT NULL DEFAULT 0 CHECK(starred IN(0,1)),
            metadata TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id,subject));
        CREATE INDEX IF NOT EXISTS feedback_subject ON paper_feedback(subject);
        ''')


def catalog_subject(item):
    return 'arxiv:'+item['arxiv_id'] if item.get('arxiv_id') else 'catalog:'+item['id']


def merge_identity(con, old_subject, new_subject, catalog_id):
    """Preserve the latest explicit user choice when metadata identities merge."""
    if old_subject == new_subject:
        return
    for old in con.execute('SELECT * FROM paper_feedback WHERE subject=?',(old_subject,)).fetchall():
        newer = con.execute('SELECT * FROM paper_feedback WHERE user_id=? AND subject=?',(old['user_id'],new_subject)).fetchone()
        chosen = newer if newer and newer['updated_at']>=old['updated_at'] else old
        meta = json.loads(chosen['metadata']);meta['catalog_paper_id']=catalog_id;meta['id']=catalog_id
        con.execute('INSERT OR REPLACE INTO paper_feedback VALUES(?,?,?,?,?,?)',
                    (old['user_id'],new_subject,chosen['vote'],chosen['starred'],json.dumps(meta,ensure_ascii=False),chosen['updated_at']))
    con.execute('DELETE FROM paper_feedback WHERE subject=?',(old_subject,))


def identity(ref):
    fields = [k for k in ('catalog_id','paper_id','arxiv_id','graph_id') if ref.get(k)]
    if len(fields) != 1:
        raise HTTPException(422, '请指定一个论文标识')
    kind = fields[0]
    value = ref[kind]
    item, private = None, None
    with core.db() as con:
        if kind == 'paper_id':
            private = core.get_paper(value)
            link = con.execute('SELECT catalog_paper_id FROM paper_catalog_links WHERE paper_id=?', (value,)).fetchone()
            if link:
                value, kind = link[0], 'catalog_id'
        if kind == 'catalog_id':
            row = con.execute('SELECT data FROM catalog_papers WHERE id=?', (value,)).fetchone()
            if not row:
                raise HTTPException(404, '索引论文不存在')
            item = json.loads(row[0])
        elif kind == 'graph_id':
            from . import paper_graph
            node = paper_graph.resolve(paper=value)
            item = paper_graph.catalog_match(con, node)
            if item is None:
                arxiv = paper_graph.arxiv_id(node)
                return ('arxiv:'+arxiv if arxiv else 'graph:'+value), paper_graph.decorate(con, node)
        elif kind == 'arxiv_id':
            if not re.fullmatch(r'\d{4}\.\d{4,5}(?:v\d+)?', value):
                raise HTTPException(422, 'arXiv 标识无效')
            arxiv = re.sub(r'v\d+$', '', value)
            row = con.execute('SELECT p.data FROM catalog_aliases a JOIN catalog_papers p ON p.id=a.paper_id WHERE a.alias=?', ('arxiv:'+arxiv,)).fetchone()
            if row:
                item = json.loads(row[0])
            else:
                return 'arxiv:'+arxiv, {'arxiv_id':arxiv, 'title':'arXiv '+arxiv, 'url':'https://arxiv.org/abs/'+arxiv}
    if item:
        return catalog_subject(item), {**{k:item.get(k) for k in ('id','title','arxiv_id','url','pdf_url','classification','authors','abstract')}, 'catalog_paper_id':item['id']}
    return 'paper:'+private['id'], {'paper_id':private['id'], 'title':private['title']}


def state(con, subject):
    rows = con.execute('SELECT user_id,vote,starred FROM paper_feedback WHERE subject=?', (subject,)).fetchall()
    own = next((r for r in rows if r['user_id'] == auth.current_user_id()), None)
    return {'subject':subject, 'vote':own['vote'] if own else 0, 'starred':bool(own['starred']) if own else False,
            'likes':sum(r['vote']==1 for r in rows), 'dislikes':sum(r['vote']==-1 for r in rows),
            'stars':sum(r['starred'] for r in rows)}


def batch(refs):
    resolved = [identity(ref)[0] for ref in refs]
    with core.db() as con:
        return {'items':[state(con, subject) for subject in resolved]}


def update(ref, vote=None, starred=None):
    subject, metadata = identity(ref)
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        before = state(con, subject)
        con.execute('''INSERT INTO paper_feedback VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id,subject) DO UPDATE SET vote=excluded.vote,starred=excluded.starred,
            metadata=excluded.metadata,updated_at=excluded.updated_at''',
            (auth.current_user_id(),subject,before['vote'] if vote is None else vote,
             before['starred'] if starred is None else starred,json.dumps(metadata,ensure_ascii=False),core.now()))
        con.execute('DELETE FROM recommendation_snapshots WHERE user_id=?', (auth.current_user_id(),))
        return state(con, subject)


def starred(page=1, limit=24):
    with core.db() as con:
        rows = con.execute('SELECT subject,metadata FROM paper_feedback WHERE user_id=? AND starred=1 ORDER BY updated_at DESC', (auth.current_user_id(),)).fetchall()
        items = []
        for row in rows:
            item = json.loads(row['metadata'])
            if item.get('catalog_paper_id'):
                paper = con.execute('SELECT data FROM catalog_papers WHERE id=?',(item['catalog_paper_id'],)).fetchone()
                if paper:
                    from .recommendation_content import presentation
                    public = json.loads(paper[0]);item={**item,**public,**presentation(con,public)}
            if row['subject'].startswith('paper:'):
                try:
                    local = core.get_paper(row['subject'][6:])
                except HTTPException:
                    continue
                if not local['memberships']:
                    continue
                from urllib.parse import urlencode
                item['reader_href'] = '/read/'+local['id']+'?'+urlencode({'project':local['memberships'][0]['project_id']})
            items.append({**item, 'id':item.get('id') or row['subject'], 'feedback':state(con,row['subject']), 'reasons':['你收藏的论文']})
    return {'items':items[(page-1)*limit:page*limit], 'total':len(items), 'page':page, 'pages':(len(items)+limit-1)//limit}
