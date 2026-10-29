"""Public generated reading cards, separate from an author's original abstract."""
import base64
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
import re
from . import catalog, core
from .data_files import read_data_bytes

CARDS_DIR=core.ROOT/'config'/'reading-cards'


@lru_cache(maxsize=1)
def index():
    from .data_bundles import component_path
    directory=component_path('reading-cards',CARDS_DIR)
    if not (directory/'manifest.json').is_file():
        return {'papers':{},'shards':[],'export_date':None},defaultdict(list)
    manifest=json.loads(read_data_bytes(directory/'manifest.json'))
    urls=defaultdict(list)
    for key,paper in manifest['papers'].items():
        if paper.get('url'):
            urls[catalog._canonical_url(paper['url'])].append(key)
    return manifest,urls


@lru_cache(maxsize=16)
def shard(identifier):
    entry=next(s for s in index()[0]['shards'] if s['id']==identifier)
    from .data_bundles import component_path
    raw=(component_path('reading-cards',CARDS_DIR)/entry['file']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
        raise ValueError('Reading-card checksum mismatch')
    content=gzip.decompress(base64.b64decode(raw,validate=True))
    if hashlib.sha256(content).hexdigest()!=entry['content_sha256']:
        raise ValueError('Reading-card content checksum mismatch')
    return {row['paper_id']:row for row in json.loads(content)}


def get(graph_id):
    meta=index()[0]['papers'].get(graph_id)
    return shard(meta['shard']).get(graph_id) if meta else None


def for_paper(paper):
    """Stable graph/arXiv ID or unique exact source URL; never title alone."""
    manifest,urls=index()
    # A server-resolved graph/source record selects that precise supplied card,
    # even if another source version carries the same canonical arXiv ID.
    for field in ('reading_card_id','graph_id'):
        if paper.get(field) in manifest['papers']:
            return get(paper[field])
    keys=[]
    if paper.get('arxiv_id'):
        keys.append('arxiv_'+catalog._arxiv(paper['arxiv_id']).replace('.','_'))
    for url in (paper.get('url'),paper.get('pdf_url')):
        if url:
            matches=urls.get(catalog._canonical_url(url),[])
            if len(matches)==1:
                keys.append(matches[0])
    found={k for k in keys if k in manifest['papers']}
    if len(found)!=1:
        return None
    return get(found.pop())


def public(card):
    if not card:
        return None
    provenance=card.get('provenance',{})
    return {'paper_id':card['paper_id'],'abstract':card['abstract'],'reasons':card['reasons'],
            'classification':card.get('classification'),'metadata_missing':card.get('metadata_missing',False),
            'coverage':provenance.get('source_coverage','partial'),'abstract_provenance':provenance.get('abstract',{}),
            'reasons_provenance':provenance.get('reasons',{}),'source_sha256':provenance.get('source_sha256'),
            'export_date':index()[0]['export_date'],'origin':'supplied-generated-reading-card'}


def presentation(paper):
    card=public(for_paper(paper))
    return {'reading_card':card} if card else {}


def init_catalog_cards():
    """Add missing source records without rewriting existing catalog material.

    Stable identifiers/unique paper URLs are the only joins. Same-title source
    versions remain separate until their identity can be verified.
    """
    manifest, urls = index()
    migration = 'reading-cards:' + manifest['source_sha256']
    with core.db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS reading_card_catalog (
            graph_id TEXT PRIMARY KEY,
            catalog_paper_id TEXT NOT NULL REFERENCES catalog_papers(id) ON DELETE CASCADE,
            match_method TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS reading_card_catalog_paper ON reading_card_catalog(catalog_paper_id);
        ''')
        if con.execute('SELECT 1 FROM catalog_migrations WHERE id=?',(migration,)).fetchone():
            return
        lookup = defaultdict(set)
        for row in con.execute('SELECT id,data FROM catalog_papers'):
            paper = json.loads(row['data'])
            if paper.get('arxiv_id'):
                lookup['arxiv:'+catalog._arxiv(paper['arxiv_id'])].add(row['id'])
            for url in (paper.get('url'),paper.get('pdf_url')):
                if url and not catalog._source_only_url(url):
                    lookup['url:'+catalog._canonical_url(url)].add(row['id'])
        con.execute('BEGIN IMMEDIATE')
        inserted = matched = 0
        stamp = core.now()
        for graph_id, meta in manifest['papers'].items():
            arxiv = graph_id[6:].replace('_','.') if graph_id.startswith('arxiv_') else None
            url = meta.get('url')
            unique_url = bool(url and not catalog._source_only_url(url) and len(urls[catalog._canonical_url(url)])==1)
            keys = (['arxiv:'+arxiv] if arxiv else []) + (['url:'+catalog._canonical_url(url)] if unique_url else [])
            found = set().union(*(lookup[k] for k in keys))
            if len(found)==1:
                identifier = found.pop(); method='stable-source'; matched+=1
            else:
                identifier = 'cat_' + hashlib.sha256(('reading-card:'+graph_id).encode()).hexdigest()[:24]
                year_match = re.match(r'(?:icml|iclr|neurips|cvpr|eccv|corl)_(\d{4})_',graph_id)
                data, aliases = catalog.normalize_row({'title':meta['title'], 'url':url,
                    'arxiv_id':arxiv, 'year':int(year_match[1]) if year_match else None,
                    'source_id':'reading-cards', 'url_is_identity':unique_url,
                    'provenance':[{'source_id':'reading-cards','source_url':url,
                                   'notes':'提供的摘要附件中的元数据；生成摘要与原始摘要分开保存。'}]}, {})
                data['reading_card_id'] = graph_id
                catalog._save_paper(con,identifier,data,stamp)
                con.executemany('INSERT OR IGNORE INTO catalog_aliases VALUES(?,?)',
                                [(a,identifier) for a in aliases if not a.startswith('title:')])
                method='separate-source-record'; inserted+=1
            con.execute('INSERT OR REPLACE INTO reading_card_catalog VALUES(?,?,?)',(graph_id,identifier,method))
        con.execute('INSERT INTO catalog_migrations VALUES(?,?,?)',
                    (migration,json.dumps({'matched':matched,'inserted':inserted,'total':manifest['count']}),stamp))
