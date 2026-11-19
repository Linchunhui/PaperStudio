"""Account-local search/open events and actual reading progress; no outside history."""
import json
from . import auth, core


def init_behavior():
    with core.db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS reader_events (
            user_id TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
            data TEXT NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id,kind,subject));
        CREATE INDEX IF NOT EXISTS reader_events_recent ON reader_events(user_id,updated_at);
        CREATE TABLE IF NOT EXISTS recommendation_exposures (
            user_id TEXT NOT NULL, catalog_paper_id TEXT NOT NULL,
            shown_at TEXT NOT NULL, batch_id TEXT NOT NULL,
            PRIMARY KEY(user_id,catalog_paper_id));
        ''')


def record(kind, subject, data=None, *, source='observed', con=None):
    values=(auth.current_user_id(),kind,subject,json.dumps(data or {},ensure_ascii=False),source,core.now())
    sql='''INSERT INTO reader_events VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,kind,subject)
           DO UPDATE SET data=excluded.data,source=excluded.source,updated_at=excluded.updated_at'''
    if con is not None:
        con.execute(sql,values)
    else:
        with core.db() as connection:
            connection.execute(sql,values)


def history(con, user_id=None, limit=12):
    user_id=user_id or auth.current_user_id()
    found={}
    rows=con.execute('''SELECT l.catalog_paper_id, r.status, r.last_page, r.updated_at
        FROM reading_progress r JOIN paper_catalog_links l ON l.paper_id=r.paper_id
        WHERE r.user_id=? AND r.status IN ('reading','read') ORDER BY r.updated_at DESC LIMIT 30''',(user_id,)).fetchall()
    for row in rows:
        found.setdefault(row['catalog_paper_id'],dict(row)|{'source':'reading_progress','kind':'read'})
    for row in con.execute("SELECT * FROM reader_events WHERE user_id=? AND kind IN ('open','read') ORDER BY updated_at DESC LIMIT 30",(user_id,)):
        found.setdefault(row['subject'],dict(row)|{'catalog_paper_id':row['subject']})
    result=[]
    for identifier,event in sorted(found.items(),key=lambda pair:pair[1]['updated_at'],reverse=True):
        row=con.execute('SELECT data FROM catalog_papers WHERE id=?',(identifier,)).fetchone()
        if row:
            paper=json.loads(row[0])
            result.append({'id':identifier,'title':paper['title'],'abstract':(paper.get('abstract') or '')[:500],
                'classification':paper.get('classification',{}),'action':event['kind'],'source':event['source'],
                'status':event.get('status'),'last_page':event.get('last_page'),'updated_at':event['updated_at'],
                'paper':{key:paper.get(key) for key in ('id','title','url','pdf_url','resolve','arxiv_id')}})
        if len(result)>=limit:break
    return result


def context(con, profile):
    user=auth.current_user_id()
    row=con.execute('SELECT persona FROM reader_accounts WHERE user_id=?',(user,)).fetchone()
    persona=json.loads(row[0]) if row else {}
    searches=[{'query':r['subject'],'source':r['source']} for r in con.execute(
        "SELECT subject,source FROM reader_events WHERE user_id=? AND kind='search' ORDER BY updated_at DESC LIMIT 6",(user,))]
    reads=history(con,user)
    feedback=[]
    for r in con.execute('SELECT metadata,vote,starred FROM paper_feedback WHERE user_id=? ORDER BY updated_at DESC LIMIT 12',(user,)):
        meta=json.loads(r['metadata'])
        feedback.append({'title':meta.get('title',''),'vote':r['vote'],'starred':bool(r['starred'])})
    return {'as_of':core.now()[:10],'profile':persona,'followed_topics':profile['topic_ids'],
            'followed_categories':profile['category_ids'],'keywords':profile['keywords'],
            'recent_searches':searches,'reading_history':[{k:v for k,v in row.items() if k!='paper'} for row in reads],
            'feedback':feedback,'history_source':'PaperStudio account only'}
