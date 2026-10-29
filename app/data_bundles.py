"""Checksummed optional data packages. Existing personal data is never replaced."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import threading
import urllib.request

from fastapi import HTTPException
from . import core

VERSION='0.5.0'
MAX_ARCHIVE=100*1024*1024
MAX_EXPANDED=180*1024*1024
COMPONENTS={'catalog','relations','reading-cards'}
_LOCK=threading.Lock()
_THREAD=None


def component_path(name, fallback):
    marker=core.DATA_DIR/'active-bundle.json'
    if marker.is_file():
        try:
            key=json.loads(marker.read_text())['sha256']
            if len(key)!=64 or any(c not in '0123456789abcdef' for c in key): raise ValueError()
            candidate=core.DATA_DIR/'bundles'/key/name
            if candidate.is_dir(): return candidate
        except (OSError,ValueError,KeyError,TypeError):
            pass
    return fallback


def _status(**values):
    from .model_settings import _write
    _write('data-bundle-status.json',values|{'updated_at':core.now()})


def status():
    from .model_settings import _read
    value=_read('data-bundle-status.json') or {'state':'idle','stage':'尚未加载可选数据包'}
    if value['state']=='running' and not (_THREAD and _THREAD.is_alive()):
        value={**value,'state':'interrupted','stage':'上次加载已中断，可手动重试；已入库的个人资料不受影响。'}
    from . import catalog
    with core.db() as con:
        count=con.execute('SELECT count(*) FROM catalog_papers').fetchone()[0]
    return {**value,'catalog_records':count,'bundled':(catalog.SEED_DIR/'manifest.json').is_file(),
            'data_version':VERSION,'archive_limit_mb':MAX_ARCHIVE//1048576}


def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def install_archive(path, expected_sha):
    """Extract only known data paths, then verify every byte before activation."""
    path=Path(path)
    if path.stat().st_size>MAX_ARCHIVE or file_hash(path)!=expected_sha:
        raise ValueError('数据包 SHA256 不符或文件过大。')
    parent=core.DATA_DIR/'bundles';parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.verify-',dir=parent) as temporary:
        staging=Path(temporary);seen=set();size=0
        with tarfile.open(path,'r:gz') as archive:
            for member in archive:
                name=PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or '..' in name.parts
                        or '\\' in member.name or member.name in seen or len(seen)>=1024
                        or (member.name!='bundle.json' and (len(name.parts)!=2 or name.parts[0] not in COMPONENTS))):
                    raise ValueError('数据包含不允许的路径、重复文件或链接。')
                size+=member.size
                if size>MAX_EXPANDED or member.size>25*1024*1024:
                    raise ValueError('数据包解压体积超过限制。')
                destination=staging/member.name;destination.parent.mkdir(parents=True,exist_ok=True)
                source=archive.extractfile(member)
                if source is None: raise ValueError('数据包条目无法读取。')
                with source, destination.open('wb') as output: shutil.copyfileobj(source,output)
                seen.add(member.name)
        manifest=json.loads((staging/'bundle.json').read_text())
        files=manifest['files']
        if manifest.get('schema')!=1 or set(files)!=seen-{'bundle.json'}:
            raise ValueError('数据包文件清单不完整。')
        for name,entry in files.items():
            target=staging/name
            if target.stat().st_size!=entry['bytes'] or file_hash(target)!=entry['sha256']:
                raise ValueError('数据包内部校验失败。')
        if any(not (staging/name/'manifest.json').is_file() for name in COMPONENTS):
            raise ValueError('完整数据包缺少目录、关系或阅读卡片清单。')
        destination=parent/expected_sha
        if destination.exists():
            # Previously installed content is checked too, not silently trusted.
            for name,entry in files.items():
                if not (destination/name).is_file() or file_hash(destination/name)!=entry['sha256']:
                    raise ValueError('已安装的同版本数据包损坏，请由管理员移走该包后重试。')
        else:
            # Same-filesystem rename keeps a killed process from leaving a
            # half-installed package that would block a later retry.
            os.replace(staging,destination)
        from .model_settings import _write
        _write('active-bundle.json',{'sha256':expected_sha,'version':manifest.get('version')})
    return manifest


def _download():
    base=f'https://github.com/Linchunhui/PaperStudio/releases/download/v{VERSION}/'
    with urllib.request.urlopen(base+'data-pack.json',timeout=30) as response:
        content=response.read(65537)
    if len(content)>65536: raise ValueError('数据清单过大。')
    manifest=json.loads(content)
    if manifest.get('filename')!='paperstudio-data.tar.gz' or not 0<manifest['bytes']<=MAX_ARCHIVE:
        raise ValueError('数据清单无效。')
    with tempfile.TemporaryDirectory(prefix='download-',dir=core.DATA_DIR) as folder:
        path=Path(folder)/'data.tar.gz';downloaded=0;last=0
        with urllib.request.urlopen(base+manifest['filename'],timeout=30) as source,path.open('wb') as output:
            while True:
                chunk=source.read(1024*1024)
                if not chunk: break
                downloaded+=len(chunk)
                if downloaded>manifest['bytes']: raise ValueError('下载大小超过数据清单。')
                output.write(chunk)
                if downloaded-last>=1024*1024:
                    _status(state='running',stage='正在下载数据包',downloaded_bytes=downloaded,total_bytes=manifest['bytes']);last=downloaded
        if downloaded!=manifest['bytes']: raise ValueError('下载中断，数据包不完整。')
        _status(state='running',stage='正在校验完整数据包',downloaded_bytes=downloaded,total_bytes=downloaded)
        install_archive(path,manifest['sha256'])


def _load():
    try:
        from . import catalog, reading_cards, paper_graph
        if not (component_path('catalog',catalog.SEED_DIR)/'manifest.json').is_file():
            _download()
        reading_cards.index.cache_clear();reading_cards.shard.cache_clear()
        paper_graph.bundle.cache_clear();paper_graph.scores.cache_clear()
        _status(state='running',stage='正在载入完整目录；个人论文、项目和笔记保持不变')
        with core.db() as con:
            failures_before=con.execute("SELECT count(*) FROM catalog_imports WHERE status='failed'").fetchone()[0]
        (core.DATA_DIR/'full-catalog-enabled').touch(mode=0o600)
        catalog.init_catalog(seed=True,force=True)
        with core.db() as con:
            count=con.execute('SELECT count(*) FROM catalog_papers').fetchone()[0]
            failures_after=con.execute("SELECT count(*) FROM catalog_imports WHERE status='failed'").fetchone()[0]
        expected=json.loads((component_path('catalog',catalog.SEED_DIR)/'manifest.json').read_text()).get('counts',{}).get('unique_papers',1)
        if failures_after>failures_before or count<expected:
            raise ValueError('目录载入未完成。')
        _status(state='succeeded',stage=f'已载入 {count:,} 条目录记录')
    except Exception as error:
        # URLs and upstream responses may include signed credentials. No raw exception is exposed.
        import logging
        logging.getLogger(__name__).warning('Optional data load failed (%s); existing user data retained.',type(error).__name__)
        _status(state='failed',stage='加载未完成。请检查 GitHub Release 下载连通性、磁盘空间或服务日志；已有个人资料保留。')


def start():
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive(): raise HTTPException(409,'数据包正在加载。')
        _status(state='running',stage='准备加载完整目录')
        _THREAD=threading.Thread(target=_load,daemon=True,name='paperstudio-data-bundle')
        _THREAD.start()
    return status()
