"""Read the supplied similarity matrix without inferring directed citations."""
import base64
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
import re
from urllib.parse import urlencode

from fastapi import HTTPException
from . import catalog, core
from .data_files import read_data_bytes

GRAPH_DIR = core.ROOT / 'config' / 'relations'


@lru_cache(maxsize=1)
def bundle():
    from .data_bundles import component_path
    directory=component_path('relations',GRAPH_DIR)
    if not (directory/'manifest.json').is_file():
        raise HTTPException(503,'尚未安装关系数据包，请在管理员设置中加载完整数据。')
    manifest = json.loads((directory/'manifest.json').read_text())
    nodes, titles, groups = {}, defaultdict(list), {}
    for entry in manifest['groups']:
        raw = read_data_bytes(directory/entry['file'])
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise HTTPException(503, '论文关系数据校验失败')
        group = json.loads(raw)
        groups[group['id']] = group
        for paper in group['papers']:
            node = {**paper, 'direction':group['id'], 'direction_name':group['name']}
            nodes[node['id']] = node
            titles[catalog.title_key(node['title'])].append(node['id'])
    return manifest, nodes, titles, groups


@lru_cache(maxsize=4)
def scores(direction):
    group = bundle()[3][direction]
    data = gzip.decompress(base64.b64decode(group['scores'], validate=True))
    n = len(group['papers'])
    if len(data) != n*(n-1)//2 or hashlib.sha256(data).hexdigest() != group['packed_sha256']:
        raise HTTPException(503, '论文相似度矩阵校验失败')
    return data


def pair_score(direction, i, j):
    if i == j:
        return None
    if i > j:
        i, j = j, i
    n = len(bundle()[3][direction]['papers'])
    value = scores(direction)[i*(2*n-i-1)//2+j-i-1]
    return None if value == 255 else value/100


def arxiv_id(node):
    match = re.fullmatch(r'arxiv_(\d{4})_(\d{4,5})(?:v\d+)?', node.get('id', ''))
    return '.'.join(match.groups()) if match else None


def catalog_data(con, identifier):
    row = con.execute('SELECT data FROM catalog_papers WHERE id=?', (identifier,)).fetchone()
    return json.loads(row[0]) if row else None


def catalog_match(con, node):
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='reading_card_catalog'").fetchone():
        row = con.execute('SELECT p.data FROM reading_card_catalog c JOIN catalog_papers p ON p.id=c.catalog_paper_id WHERE c.graph_id=?',(node['id'],)).fetchone()
        if row:
            return json.loads(row[0])
    arxiv = arxiv_id(node)
    alias = 'arxiv:'+arxiv if arxiv else 'title:'+catalog.title_key(node['title'])
    row = con.execute('SELECT p.data FROM catalog_aliases a JOIN catalog_papers p ON p.id=a.paper_id WHERE a.alias=?', (alias,)).fetchone()
    return json.loads(row[0]) if row else None


def resolve(*, paper='', catalog_id='', paper_id='', project_id=''):
    _, nodes, titles, _ = bundle()
    if paper_id:
        local = core.get_paper(paper_id)
        if project_id:
            core.require_membership(paper_id, project_id)
        with core.db() as con:
            row = con.execute('SELECT catalog_paper_id FROM paper_catalog_links WHERE paper_id=?', (paper_id,)).fetchone()
        catalog_id = row[0] if row else ''
        title = local['title']
    else:
        title = ''
    if catalog_id:
        with core.db() as con:
            item = catalog_data(con, catalog_id)
        if not item:
            raise HTTPException(404, '索引论文不存在')
        from .reading_cards import for_paper
        card = for_paper(item)
        if card and card['paper_id'] in nodes:
            return nodes[card['paper_id']]
        arxiv = item.get('arxiv_id')
        if arxiv:
            key = 'arxiv_'+arxiv.replace('.', '_')
            if key in nodes:
                return nodes[key]
        title = item['title']
    elif paper:
        if paper not in nodes:
            raise HTTPException(404, '关系数据尚未收录这篇论文')
        return nodes[paper]
    matches = titles.get(catalog.title_key(title), []) if title else []
    return nodes[matches[0]] if len(matches) == 1 else None


def decorate(con, node, project_id=''):
    item = catalog_match(con, node)
    arxiv = arxiv_id(node)
    result = {**node, 'graph_id':node['id'], 'catalog_paper_id':item['id'] if item else None,
              'arxiv_id':arxiv or (item or {}).get('arxiv_id'),
              'url':f'https://arxiv.org/abs/{arxiv}' if arxiv else (item or {}).get('url'),
              'pdf_url':f'https://arxiv.org/pdf/{arxiv}' if arxiv else (item or {}).get('pdf_url')}
    if item:
        result['classification'] = item.get('classification')
    from .reading_cards import presentation
    result.update(presentation(result))
    if project_id and item:
        row = con.execute('SELECT l.paper_id FROM paper_catalog_links l JOIN project_papers p ON p.paper_id=l.paper_id WHERE l.catalog_paper_id=? AND p.project_id=? ORDER BY p.updated_at DESC LIMIT 1', (item['id'], project_id)).fetchone()
        if row:
            result['reader_href'] = '/read/'+row[0]+'?'+urlencode({'project':project_id})
    return result


def search(query='', direction='', limit=20):
    manifest, nodes, _, _ = bundle()
    terms = query.casefold().split()
    matches = [n for n in nodes.values() if (not direction or n['direction'] == direction)
               and all(t in (n['title']+' '+n['id']).casefold() for t in terms)]
    matches.sort(key=lambda n: (n['id'] != query, not n['has_embedding'], n['id']), reverse=False)
    return {'items':matches[:limit], 'total':len(matches), 'groups':manifest['groups'],
            'node_count':manifest['node_count'], 'embedding_node_count':manifest['embedding_node_count']}


def graph(*, paper='', catalog_id='', paper_id='', project_id='', limit=50, min_score=0, include_weak=False):
    if project_id:
        core.get_project(project_id)
    manifest, nodes, _, groups = bundle()
    node = resolve(paper=paper, catalog_id=catalog_id, paper_id=paper_id, project_id=project_id)
    missing = {'status':'not_provided', 'items':[], 'total':None}
    result = {'center':None, 'nodes':[], 'edges':[], 'related':[], 'references':missing,
              'cited_by':missing, 'coverage':{'node_count':manifest['node_count'],
              'known_pairs':manifest['pairs_with_known_score'], 'scope':'within_primary_direction',
              'source_sha256':manifest['source_sha256'], 'weights':manifest['weights'],
              'score_meaning':manifest['score_meaning'], 'components_status':'not_provided'}}
    if node is None:
        return {**result, 'status':'not_covered', 'message':'当前数据尚未收录这篇论文，暂无可核对的相关论文。'}
    candidates, unknown = [], 0
    for other in groups[node['direction']]['papers']:
        if other['id'] == node['id']:
            continue
        score = pair_score(node['direction'], node['index'], other['index'])
        if score is None:
            unknown += 1
        else:
            candidates.append((score, other['id']))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    with core.db() as con:
        center = decorate(con, node, project_id)
        related = [{**decorate(con, nodes[key], project_id), 'score':score,
                    'reason':f"同属{node['direction_name']} · 综合相似度 {score:.2f}"} for score, key in candidates[:limit]]
    result.update(status='ready', center=center, nodes=[center, *related], related=related,
                  total_related=len(candidates), unknown_in_group=unknown,
                  truncated=len(candidates)>limit)
    # An induced network: retain every known pair among the selected nodes.
    # Thresholds affect edges only, so changing them does not remove papers.
    edges, known_edges, weak_edges = [], 0, 0
    for i, a in enumerate(result['nodes']):
        for b in result['nodes'][i+1:]:
            score = pair_score(node['direction'], a['index'], b['index'])
            if score is None:
                continue
            known_edges += 1
            weak = score < min_score
            weak_edges += weak
            if not weak or include_weak:
                edges.append({'source':a['id'], 'target':b['id'], 'score':score,
                              'type':'similarity', 'weak':weak})
    result.update(edges=edges, edge_threshold=min_score, include_weak=include_weak,
                  known_edges=known_edges, weak_edges=weak_edges,
                  hidden_edges=0 if include_weak else weak_edges)
    return result
