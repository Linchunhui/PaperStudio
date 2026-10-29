"""Admin-owned model configuration. Secrets never appear in public status payloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

from . import core

_LOCK = threading.RLock()
_TEST_LOCK = threading.Lock()


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['none', 'openai', 'local', 'codex'] = 'none'
    base_url: str = Field(default='', max_length=2048)
    model: str = Field(default='', max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_key: bool = False

    @model_validator(mode='after')
    def validate_endpoint(self):
        self.base_url = self.base_url.strip().rstrip('/')
        self.model = self.model.strip()
        if self.provider != 'none' and not self.model:
            raise ValueError('请选择或填写模型名称。')
        if self.provider in {'openai', 'local'}:
            url = urlsplit(self.base_url)
            if (url.scheme not in {'http', 'https'} or not url.hostname or url.username
                    or url.password or url.query or url.fragment):
                raise ValueError('模型地址须为 HTTP(S) API 基地址，不包含账号、查询参数或片段。')
        return self


def _read(name):
    path = core.DATA_DIR / name
    if not path.is_file():
        return None
    if path.is_symlink() or path.stat().st_size > 32768:
        raise HTTPException(409, '模型配置文件无效，请由部署管理员检查。')
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError):
        raise HTTPException(409, '模型配置无法读取，请由部署管理员检查。') from None


def _write(name, data):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=core.DATA_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).chmod(0o600)
        os.replace(temporary, core.DATA_DIR / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def environment_locked():
    return os.environ.get('PAPERSTUDIO_MODEL_CONFIG', '').lower() == 'environment'


def configured_values():
    """Existing .env deployments retain their configuration until an explicit save."""
    saved = None if environment_locked() else _read('model-settings.json')
    if saved is not None:
        return {**saved, 'source': 'workspace'}
    base = os.environ.get('AI_BASE_URL', '').strip().rstrip('/')
    return {'provider': os.environ.get('AI_PROVIDER', 'openai' if base else 'none').lower(),
            'base_url': base, 'model': os.environ.get('AI_MODEL', 'gpt-6-luna' if os.environ.get('AI_PROVIDER') or base else '').strip(),
            'api_key': os.environ.get('AI_API_KEY', '').strip(), 'source': 'environment'}


def _fingerprint(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def public_status():
    values = configured_values()
    configured = values.get('provider') not in {None, 'none'} and bool(values.get('model'))
    base_url = values.get('base_url', '')
    try:
        parsed = urlsplit(base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            base_url = ''
            configured = False
    except ValueError:
        base_url = ''
        configured = False
    if values.get('provider') in {'openai','local'}:
        configured = configured and bool(base_url) and (values['provider']=='local' or bool(values.get('api_key')))
    from . import ai
    installation = shutil.which(os.environ.get('CODEX_BIN', 'codex')) is not None
    runtime_available = configured and (values['provider'] != 'codex' or installation)
    test = _read('model-test.json') or {}
    if test.get('fingerprint') != _fingerprint(values):
        test = {}
    # No command output, provider responses or authentication material returned.
    return {'provider': values.get('provider', 'none'), 'base_url': base_url,
            'model': values.get('model', ''), 'has_api_key': bool(values.get('api_key')),
            'source': values['source'], 'locked': environment_locked(),
            'configured': configured, 'runtime_available': runtime_available,
            'codex_installed': installation, 'babeldoc_installed': ai.translation_executable() is not None,
            'connection': test.get('connection'), 'completion': test.get('completion'),
            'tested_at': test.get('tested_at'), 'message': test.get('message', '')}


def save(body: ModelInput):
    if environment_locked():
        raise HTTPException(409, '部署管理员已锁定环境变量配置。')
    with _LOCK:
        previous = configured_values()
        # Never silently send the old provider's credential to a new endpoint.
        same_endpoint = (previous.get('provider') == body.provider
                         and previous.get('base_url', '') == body.base_url)
        key = body.api_key.strip() if body.api_key else ''
        if not key and not body.clear_key and same_endpoint:
            key = previous.get('api_key', '')
        if body.provider == 'openai' and not key:
            raise HTTPException(422, '请填写 API Key；无密钥的可信本地服务请选择“本地模型”。')
        values = {'provider': body.provider, 'base_url': body.base_url,
                  'model': body.model, 'api_key': key}
        if body.provider in {'none', 'codex'}:
            values['base_url'] = values['api_key'] = ''
        _write('model-settings.json', values)
        return public_status()


def reset():
    if environment_locked():
        raise HTTPException(409, '部署管理员已锁定环境变量配置。')
    with _LOCK:
        (core.DATA_DIR / 'model-settings.json').unlink(missing_ok=True)
        (core.DATA_DIR / 'model-test.json').unlink(missing_ok=True)
    return public_status()


def test_model(kind='connection'):
    """Explicit, bounded tests. Merely opening settings never calls a model."""
    if not _TEST_LOCK.acquire(blocking=False):
        raise HTTPException(409, '已有模型测试正在执行，请稍后再试。')
    try:
        from . import providers
        from .ai import ConfigurationError, ExecutionError
        values = configured_values()
        fingerprint = _fingerprint(values)
        existing = _read('model-test.json') or {}
        result = existing if existing.get('fingerprint') == fingerprint else {'connection': None, 'completion': None}
        result = {**result, 'fingerprint': fingerprint, 'tested_at': core.now()}
        try:
            cfg = providers.settings()
            if kind == 'completion':
                reply = providers.complete('Reply with OK only.', 'Connection test.',
                    {**cfg, 'timeout': 25, 'max_output_tokens': 32})
                if not reply.strip():
                    raise ExecutionError('模型未返回文本。')
                result.update(connection=True, completion=True, message='模型已成功返回文本。此测试不验证视觉和 PDF 翻译质量。')
            elif cfg['provider'] == 'codex':
                check = subprocess.run([cfg['executable'], 'login', 'status'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                result.update(connection=check.returncode == 0,
                              message='Codex 登录状态有效；请继续测试实际调用。' if check.returncode == 0 else 'Codex 尚未登录，请在服务账号下执行 codex login --device-auth。')
            else:
                headers = {'Authorization': 'Bearer ' + cfg['api_key']} if cfg['api_key'] else {}
                with httpx.Client(timeout=10, follow_redirects=False, trust_env=values.get('provider') != 'local') as client:
                    response = client.get(cfg['base_url'] + '/models', headers=headers)
                ok = response.status_code == 200
                if ok:
                    try:
                        ok = isinstance(response.json().get('data'), list)
                    except (ValueError, AttributeError):
                        ok = False
                result.update(connection=ok, message='模型列表接口可访问；请继续测试实际调用。' if ok else
                    f'模型列表接口未通过（HTTP {response.status_code}）。部分服务不提供 /models，可直接测试实际调用。')
            if result.get('connection') is False:
                result['completion'] = None
        except (ConfigurationError, ExecutionError) as exc:
            result[kind] = False
            result['message'] = str(exc)  # Our adapters expose bounded, secret-free errors only.
        except (httpx.HTTPError, OSError, subprocess.TimeoutExpired):
            result[kind] = False
            result['message'] = '测试连接失败或超时，请检查服务地址、网络和登录状态。'
        with _LOCK:
            if _fingerprint(configured_values()) != fingerprint:
                raise HTTPException(409, '配置已改变，请对新配置重新测试。')
            _write('model-test.json', result)
        return public_status()
    finally:
        _TEST_LOCK.release()
