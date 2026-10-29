"""Check the release archive against every original source file (stdlib only)."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def verify(folder):
    folder=Path(folder)
    public=json.loads((folder/'data-pack.json').read_text())
    path=folder/public['filename']
    assert path.stat().st_size==public['bytes']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==public['sha256']
    expected={component+'/'+p.name:p for component in ('catalog','relations','reading-cards')
              for p in (ROOT/'config'/component).iterdir() if p.is_file()}
    with tarfile.open(path) as archive:
        names=archive.getnames()
        assert len(names)==len(set(names))
        assert set(names)==set(expected)|{'bundle.json'}
        inner=json.load(archive.extractfile('bundle.json'))
        assert set(inner['files'])==set(expected)
        for name,source in expected.items():
            raw=archive.extractfile(name).read()
            assert raw==source.read_bytes(),name
            assert len(raw)==inner['files'][name]['bytes']
            assert hashlib.sha256(raw).hexdigest()==inner['files'][name]['sha256']
    assert len(expected)==public['file_count']
    print(f'Complete data package verified: {len(expected)} original files, no omitted or modified bytes.')

if __name__=='__main__': verify(sys.argv[1])
