"""Produce the complete optional data archive without changing source shards."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]


def package(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    files={}
    for component in ('catalog','relations','reading-cards'):
        for path in sorted((ROOT/'config'/component).iterdir()):
            if path.is_file():
                files[component+'/'+path.name]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest={'schema':1,'version':'0.5.0','files':files}
    target=output/'paperstudio-data.tar.gz'
    with tarfile.open(target,'w:gz') as archive:
        for name in files:
            archive.add(ROOT/'config'/name,arcname=name,recursive=False)
        raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True).encode()
        info=tarfile.TarInfo('bundle.json');info.size=len(raw)
        archive.addfile(info,io.BytesIO(raw))
    public={'schema':1,'version':'0.5.0','filename':target.name,'bytes':target.stat().st_size,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'file_count':len(files)}
    (output/'data-pack.json').write_text(json.dumps(public,indent=2)+'\n')
    print(json.dumps(public))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    package(parser.parse_args().output)
