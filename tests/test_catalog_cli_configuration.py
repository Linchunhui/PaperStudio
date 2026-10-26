"""The maintenance CLI must target the same configured database as run.py."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_cli_loads_env_before_core_and_resolves_relative_data_from_repo(tmp_path):
    repo = tmp_path / 'project'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'app').mkdir()
    (repo / 'run.py').write_bytes((ROOT / 'run.py').read_bytes())
    (repo / 'scripts/update_catalog.py').write_bytes((ROOT / 'scripts/update_catalog.py').read_bytes())
    (repo / '.env').write_text('PAPERSTUDIO_DATA_DIR=relative-state\n')
    (repo / 'app/__init__.py').write_text('')
    (repo / 'app/catalog.py').write_text('')
    (repo / 'app/core.py').write_text('from pathlib import Path\nimport os\nDATA_DIR=Path(os.environ["PAPERSTUDIO_DATA_DIR"]).resolve()\n')
    script = 'import runpy,json; scope=runpy.run_path(' + repr(str(repo / 'scripts/update_catalog.py')) + '); print(json.dumps(str(scope["core"].DATA_DIR)))'
    environ = {k: v for k, v in os.environ.items() if k != 'PAPERSTUDIO_DATA_DIR'}
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=environ, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == str(repo / 'relative-state')
    environ['PAPERSTUDIO_DATA_DIR'] = str(tmp_path / 'process-override')
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=environ, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == str(tmp_path / 'process-override')
