import importlib.util
import json
from pathlib import Path

import pytest
from app.data_files import read_data_bytes

spec = importlib.util.spec_from_file_location('chunk_packer', Path(__file__).parents[1] / 'scripts/chunk_data_files.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


def make_bundle(tmp_path):
    path = tmp_path / 'fixture.json'
    raw = json.dumps({'papers': ['论文🙂\\n'] * 30, 'scores': [0, None, .45]}, ensure_ascii=False).encode() + b'\n'
    path.write_bytes(raw)
    assert packer.chunk_file(path, threshold=20, chunk_chars=17) > 1
    return path, raw, json.loads(path.read_text())


def test_chunked_bundle_preserves_exact_utf8_bytes_and_repacking(tmp_path):
    path, raw, manifest = make_bundle(tmp_path)
    assert read_data_bytes(path) == raw
    assert packer.chunk_file(path, threshold=20, chunk_chars=17) == 0
    assert read_data_bytes(path) == raw
    assert all((tmp_path / part['file']).read_bytes().decode('utf-8') for part in manifest['parts'])
    flat = tmp_path / 'flat.json'
    flat.write_bytes(raw)
    assert packer.chunk_file(flat, threshold=len(raw)) == 0
    assert read_data_bytes(flat) == raw


@pytest.mark.parametrize('damage', ['corrupt', 'missing', 'reverse', 'omit', 'duplicate', 'escape', 'symlink'])
def test_chunk_damage_and_unsafe_paths_are_rejected(tmp_path, damage):
    path, raw, manifest = make_bundle(tmp_path)
    part = tmp_path / manifest['parts'][0]['file']
    if damage == 'corrupt':
        part.write_bytes(part.read_bytes() + b'x')
    elif damage == 'missing':
        part.unlink()
    elif damage == 'reverse':
        manifest['parts'].reverse()
    elif damage == 'omit':
        manifest['parts'].pop()
    elif damage == 'duplicate':
        manifest['parts'].append(manifest['parts'][0])
    elif damage == 'escape':
        manifest['parts'][0]['file'] = '../outside.json'
    elif damage == 'symlink':
        target = tmp_path / 'other'
        part.rename(target)
        part.symlink_to(target)
    path.write_text(json.dumps(manifest, separators=(',', ':')))
    with pytest.raises((ValueError, FileNotFoundError)):
        read_data_bytes(path)
