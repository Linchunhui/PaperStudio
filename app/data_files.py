"""Read flat or losslessly chunked UTF-8 data bundles."""
import hashlib
import json
from pathlib import Path

CHUNK_PREFIX = b'{"_paperstudio_chunks":1,'


def read_data_bytes(path):
    path = Path(path)
    raw = path.read_bytes()
    if not raw.startswith(CHUNK_PREFIX):
        return raw
    manifest = json.loads(raw)
    parts = manifest['parts']
    if not isinstance(parts, list) or not 1 <= len(parts) <= 1024:
        raise ValueError('Invalid data chunk manifest')
    chunks, seen = [], set()
    for entry in parts:
        name = entry['file']
        if not isinstance(name, str) or not name.startswith(path.name + '.part-') or '/' in name or '\\' in name or name in seen:
            raise ValueError('Invalid data chunk filename')
        seen.add(name)
        chunk_path = path.parent / name
        if chunk_path.is_symlink():
            raise ValueError('Data chunks must not be symlinks')
        chunk = chunk_path.read_bytes()
        if len(chunk) != entry['bytes'] or hashlib.sha256(chunk).hexdigest() != entry['sha256']:
            raise ValueError('Data chunk checksum mismatch')
        chunks.append(chunk)
    content = b''.join(chunks)
    if len(content) != manifest['bytes'] or hashlib.sha256(content).hexdigest() != manifest['sha256']:
        raise ValueError('Data bundle checksum mismatch')
    return content
