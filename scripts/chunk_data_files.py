"""Split large UTF-8 JSON bundles without changing their reconstructed bytes.

Usage: python scripts/chunk_data_files.py config/relations config/reading-cards
Run after either packer when small transport payloads are required.
"""
import argparse
import hashlib
import json
from pathlib import Path


def chunk_file(path, threshold=550_000, chunk_chars=100_000):
    if threshold < 1 or chunk_chars < 1:
        raise ValueError('Chunk sizes must be positive')
    path = Path(path)
    raw = path.read_bytes()
    if raw.startswith(b'{"_paperstudio_chunks":1,') or len(raw) <= threshold:
        return 0
    text = raw.decode('utf-8')
    manifest = {'_paperstudio_chunks': 1, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest(), 'parts': []}
    for index, offset in enumerate(range(0, len(text), chunk_chars)):
        chunk = text[offset:offset + chunk_chars].encode('utf-8')
        name = path.name + f'.part-{index:04d}'
        (path.parent / name).write_bytes(chunk)
        manifest['parts'].append({'file': name, 'bytes': len(chunk),
                                  'sha256': hashlib.sha256(chunk).hexdigest()})
    # Publish the descriptor only after every complete part has been written.
    temporary = path.with_name(path.name + '.chunk-tmp')
    temporary.write_text(json.dumps(manifest, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(path)
    return len(manifest['parts'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', nargs='+')
    args = parser.parse_args()
    count = sum(chunk_file(path) for directory in args.directories
                for path in sorted(Path(directory).glob('*.json')))
    print(json.dumps({'new_parts': count}))
