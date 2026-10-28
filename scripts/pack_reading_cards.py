"""Losslessly package supplied generated abstracts, reasons and caveats."""
import argparse
import base64
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path


def pack(source, target):
    raw=Path(source).read_bytes();data=json.loads(raw);target=Path(target);target.mkdir(parents=True,exist_ok=True)
    manifest={k:v for k,v in data.items() if k!='papers'}
    manifest.update(source_sha256=hashlib.sha256(raw).hexdigest(),format='reading-cards-gzip-base64-v1',shards=[],papers={})
    groups=defaultdict(list)
    for paper in data['papers']:
        key=paper['paper_id'];assert key not in manifest['papers']
        assert paper['abstract'] and isinstance(paper['reasons'],list)
        shard=f"{int(hashlib.sha256(key.encode()).hexdigest()[:2],16)%64:02x}"
        groups[shard].append(paper)
        manifest['papers'][key]={'shard':shard,'title':paper['title'],'url':paper.get('url'),
                                'metadata_missing':paper.get('metadata_missing',False)}
    for shard,rows in sorted(groups.items()):
        content=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()
        packed=base64.b64encode(gzip.compress(content,compresslevel=9,mtime=0))
        filename=shard+'.json.b64';(target/filename).write_bytes(packed)
        manifest['shards'].append({'id':shard,'file':filename,'count':len(rows),'sha256':hashlib.sha256(packed).hexdigest(),'content_sha256':hashlib.sha256(content).hexdigest()})
    assert len(manifest['papers'])==data['count']
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,separators=(',',':'))+'\n')
    print(json.dumps({'papers':len(manifest['papers']),'shards':len(groups),'bytes':sum(p.stat().st_size for p in target.iterdir())}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('target');args=parser.parse_args();pack(args.source,args.target)
