import hashlib
import io
import json
import tarfile

import pytest
from app import core, data_bundles


def make_bundle(path, extras=None):
    files={f'{name}/manifest.json':b'{}' for name in data_bundles.COMPONENTS}
    manifest={'schema':1,'version':'test','files':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()}}
    files['bundle.json']=json.dumps(manifest).encode()
    with tarfile.open(path,'w:gz') as archive:
        for name,raw in files.items():
            item=tarfile.TarInfo(name);item.size=len(raw);archive.addfile(item,io.BytesIO(raw))
        for item,raw in extras or []:
            archive.addfile(item,io.BytesIO(raw))
    return data_bundles.file_hash(path)


def test_verified_archive_activation_preserves_personal_files(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA_DIR',tmp_path/'user-data')
    core.DATA_DIR.mkdir();(core.DATA_DIR/'personal.pdf').write_bytes(b'untouched')
    path=tmp_path/'pack.tar.gz';sha=make_bundle(path)
    data_bundles.install_archive(path,sha)
    assert data_bundles.component_path('catalog',tmp_path/'missing').is_dir()
    assert (core.DATA_DIR/'personal.pdf').read_bytes()==b'untouched'
    data_bundles.install_archive(path,sha)
    with pytest.raises(ValueError): data_bundles.install_archive(path,'0'*64)


@pytest.mark.parametrize('kind',['traversal','symlink','duplicate'])
def test_archive_rejects_unsafe_members(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(core,'DATA_DIR',tmp_path/'user-data')
    item=tarfile.TarInfo('../escape' if kind=='traversal' else 'catalog/manifest.json')
    if kind=='symlink': item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd'
    path=tmp_path/'bad.tar.gz';sha=make_bundle(path,[(item,b'')])
    with pytest.raises(ValueError): data_bundles.install_archive(path,sha)
    assert not (core.DATA_DIR/'active-bundle.json').exists()
    assert not (tmp_path/'escape').exists()
