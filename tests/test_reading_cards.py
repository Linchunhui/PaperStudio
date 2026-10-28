import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from app import catalog, core, reading_cards, recommendation_content, discovery
from app.data_files import read_data_bytes


@pytest.fixture
def cards(tmp_path, monkeypatch):
    rows=[]
    for key,title,url in [('arxiv_2601_00001','Same title','https://arxiv.org/abs/2601.00001'),('cvpr_2025_other','Same title','https://example.org/paper/other'),('inst80_missing','Only supplied','https://example.org/paper/supplied')]:
        rows.append({'paper_id':key,'title':title,'url':url,'abstract':'Generated $x^2$ abstract',
                     'reasons':[{'reason':'Useful method','who_benefits':'Readers','caveat':'Small sample','caveat_basis':'inference'}],
                     'provenance':{'source_file':'/private/source.json','source_sha256':'abc','source_coverage':'selected_pages',
                                   'abstract':{'kind':'existing_generated_abstract'},'reasons':{'kind':'existing_reading_assessment'}}})
    source=tmp_path/'cards.json';source.write_text(json.dumps({'count':3,'export_date':'2026-10-28','papers':rows}))
    spec=importlib.util.spec_from_file_location('cards_packer',core.ROOT/'scripts/pack_reading_cards.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.pack(source,tmp_path/'cards')
    monkeypatch.setattr(reading_cards,'CARDS_DIR',tmp_path/'cards');reading_cards.index.cache_clear();reading_cards.shard.cache_clear()
    monkeypatch.setattr(core,'DATA_DIR',tmp_path/'data');core.init_db();catalog.init_catalog(seed=False)
    yield rows
    reading_cards.index.cache_clear();reading_cards.shard.cache_clear()


def test_lossless_cards_strict_identity_and_public_provenance(cards):
    assert reading_cards.get(cards[0]['paper_id'])==cards[0]
    assert reading_cards.for_paper({'arxiv_id':'2601.00001v2'})==cards[0]
    assert reading_cards.for_paper({'url':cards[1]['url']})==cards[1]
    assert reading_cards.for_paper({'title':'Same title'}) is None
    assert reading_cards.for_paper({'graph_id':cards[1]['paper_id'],'arxiv_id':'2601.00001'})==cards[1]
    assert reading_cards.for_paper({'arxiv_id':'2601.00001','url':cards[1]['url']}) is None
    public=reading_cards.public(cards[0]);assert public['reasons']==cards[0]['reasons']
    assert public['coverage']=='selected_pages' and 'source_file' not in json.dumps(public)
    assert '/private/' not in json.dumps(public)
    public['reasons']=[] # public container does not replace the cached source array
    assert reading_cards.get(cards[0]['paper_id'])['reasons']


def test_additive_card_metadata_preserves_author_content_and_all_records(cards):
    catalog.import_bytes(json.dumps({'papers':[{'title':'Same title','arxiv_id':'2601.00001','abstract':'Author abstract'}]}).encode())
    with core.db() as con: before=tuple(con.execute('SELECT id,data FROM catalog_papers').fetchone())
    reading_cards.init_catalog_cards();reading_cards.init_catalog_cards()
    with core.db() as con:
        assert tuple(con.execute('SELECT id,data FROM catalog_papers WHERE id=?',(before[0],)).fetchone())==before
        assert con.execute('SELECT count(*) FROM catalog_papers').fetchone()[0]==3
        assert con.execute('SELECT count(*) FROM reading_card_catalog').fetchone()[0]==3
        assert con.execute("SELECT count(*) FROM reading_card_catalog WHERE match_method='separate-source-record'").fetchone()[0]==2
        item=json.loads(before[1]);doc=recommendation_content.embedding_document(item)
        shown=recommendation_content.presentation(con,item)
        assert shown['abstract']=='Author abstract' and shown['reading_card']['abstract'].startswith('Generated')
        assert recommendation_content.embedding_document(item)==doc
    results=catalog.search(page_size=100)['items'];assert len(results)==3
    assert all(p.get('reading_card') for p in results)
    assert len({p['reading_card']['paper_id'] for p in results})==3
    external=discovery._catalog_presentation([{'arxiv_id':'2601.00001','snippet':'live'}])[0]
    assert external['reading_card']['paper_id']==cards[0]['paper_id']
    external=discovery._catalog_presentation([{'url':cards[1]['url'],'snippet':'live'}])[0]
    assert external['reading_card']['paper_id']==cards[1]['paper_id']


def test_checksum_failure_is_not_silent(cards):
    meta=reading_cards.index()[0]['shards'][0];path=reading_cards.CARDS_DIR/meta['file'];path.write_bytes(path.read_bytes()+b'x')
    reading_cards.shard.cache_clear()
    with pytest.raises(ValueError,match='checksum'):reading_cards.shard(meta['id'])


def test_real_bundle_retains_all_abstracts_reasons_and_caveats():
    directory=core.ROOT/'config/reading-cards';manifest=json.loads(read_data_bytes(directory/'manifest.json'))
    papers={};reasons=0
    for entry in manifest['shards']:
        raw=(directory/entry['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['sha256']
        content=gzip.decompress(base64.b64decode(raw,validate=True));assert hashlib.sha256(content).hexdigest()==entry['content_sha256']
        rows=json.loads(content);assert len(rows)==entry['count']
        for row in rows:
            assert row['paper_id'] not in papers and row['abstract']
            papers[row['paper_id']]=row;reasons+=len(row['reasons'])
            assert all('caveat' in reason and reason['caveat_basis'] in ('reported','inference','not_stated') for reason in row['reasons'])
    assert len(papers)==manifest['count']==8879 and reasons==24734
    assert set(papers)==set(manifest['papers'])
    assert manifest['source_sha256']=='8af2a044a15fb1a73361312dfc0da76681642631fb120e54b19db74bf1810004'
