"""Use observed paper relationships as an account-scoped recommendation signal."""
import json
from fastapi import HTTPException
from . import auth, core, paper_feedback, paper_graph


def candidates(con, limit=120):
    user = auth.current_user_id()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'paper_feedback' not in tables:
        return {}, set()
    rows = con.execute('SELECT * FROM paper_feedback WHERE user_id=? ORDER BY starred DESC,updated_at DESC', (user,)).fetchall()
    excluded = {r['subject'] for r in rows if r['vote'] == -1}
    seeds, seen = [], set()
    for row in rows:
        if row['vote'] == -1 or not (row['starred'] or row['vote'] == 1):
            continue
        item = json.loads(row['metadata'])
        try:
            node = paper_graph.resolve(catalog_id=item.get('catalog_paper_id',''), paper_id=item.get('paper_id',''),
                                       paper=item.get('graph_id') or ('arxiv_'+item['arxiv_id'].replace('.','_') if item.get('arxiv_id') else ''))
        except HTTPException:
            continue
        if node and node['id'] not in seen:
            seeds.append((node, 1.0 if row['starred'] else .95, '收藏' if row['starred'] else '点赞'))
            seen.add(node['id'])
        if len(seeds) >= 8:
            break
    if len(seeds) < 8 and 'reading_progress' in tables:
        recent = con.execute('SELECT paper_id FROM reading_progress WHERE user_id=? ORDER BY updated_at DESC LIMIT 12', (user,)).fetchall()
        for row in recent:
            try:
                subject, _ = paper_feedback.identity({'paper_id':row[0]})
                if subject in excluded:
                    continue
                node = paper_graph.resolve(paper_id=row[0])
            except HTTPException:
                continue
            if node and node['id'] not in seen:
                seeds.append((node,.75,'最近阅读'))
                seen.add(node['id'])
            if len(seeds) >= 8:
                break
    result = {}
    for seed, weight, action in seeds:
        nearby = []
        for other in paper_graph.bundle()[3][seed['direction']]['papers']:
            if other['id'] in seen:
                continue
            score = paper_graph.pair_score(seed['direction'],seed['index'],other['index'])
            if score is not None and score > 0:
                nearby.append((score,other))
        nearby.sort(key=lambda pair:(-pair[0],pair[1]['id']))
        for score, other in nearby[:60]:
            item = paper_graph.catalog_match(con,other)
            if not item or paper_feedback.catalog_subject(item) in excluded:
                continue
            identifier = item['id']
            value = score*weight
            if identifier not in result or value > result[identifier]['relevance']:
                result[identifier] = {'relevance':value, 'score':score, 'seed_id':seed['id'],
                                      'reason':f"与你{action}的《{seed['title']}》相关 · 综合相似度 {score:.2f}"}
    return dict(sorted(result.items(),key=lambda pair:(-pair[1]['relevance'],pair[0]))[:limit]), excluded
