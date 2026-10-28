"""Losslessly pack the supplied two-decimal upper-triangle similarity export.

Usage: python scripts/pack_relations.py source.json config/relations
All pairs are retained: 0..100 represent hundredths; 255 represents unknown.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path


def pack(source, target):
    raw = Path(source).read_bytes()
    data = json.loads(raw)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    manifest = {k: v for k, v in data.items() if k != 'directions'}
    manifest.update(format='upper-cents-gzip-base64-v1', source_sha256=hashlib.sha256(raw).hexdigest(),
                    citation_status='not_provided', components_status='not_provided', groups=[])
    count, pairs, known, embedded, identifiers = 0, 0, 0, 0, set()
    for group in data['directions']:
        name = group['id']
        if not name.replace('_', '').isalnum():
            raise ValueError('Invalid group identifier')
        papers = group['papers']
        size = len(papers)
        assert size == group['node_count'] and len(group['similarity_upper']) == size
        packed = bytearray()
        group_known = 0
        for i, row in enumerate(group['similarity_upper']):
            assert papers[i]['index'] == i and len(row) == size-i-1
            assert papers[i]['id'] not in identifiers
            identifiers.add(papers[i]['id'])
            for score in row:
                if score is None:
                    packed.append(255)
                else:
                    assert type(score) in (float, int) and 0 <= score <= 1
                    value = round(score*100)
                    assert abs(value/100-score) < 1e-9, 'More than two decimal places'
                    packed.append(value)
                    group_known += 1
        assert group_known == group['known_pair_count']
        assert len(packed)-group_known == group['unknown_pair_count']
        artifact = {k: v for k, v in group.items() if k != 'similarity_upper'}
        artifact.update(encoding=manifest['format'], packed_sha256=hashlib.sha256(packed).hexdigest(),
                        scores=base64.b64encode(gzip.compress(packed, compresslevel=9, mtime=0)).decode())
        body = json.dumps(artifact, ensure_ascii=False, separators=(',', ':')).encode()
        filename = name+'.json'
        (target/filename).write_bytes(body)
        manifest['groups'].append({'id':name, 'name':group['name'], 'file':filename,
                                   'node_count':size, 'known_pair_count':group_known,
                                   'sha256':hashlib.sha256(body).hexdigest(), 'bytes':len(body)})
        count += size
        pairs += len(packed)
        known += group_known
        embedded += sum(p['has_embedding'] for p in papers)
    assert count == data['node_count'] and pairs == data['pairs_including_unknown'] and known == data['pairs_with_known_score']
    manifest['embedding_node_count'] = embedded
    (target/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'nodes':count, 'embedding_nodes':embedded, 'pairs':pairs, 'known':known,
                      'packed_bytes':sum(g['bytes'] for g in manifest['groups'])}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    pack(args.source, args.target)
