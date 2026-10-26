"""Password sessions and invite-only account enrollment."""
from __future__ import annotations

import hmac
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import ai, auth, core

router = APIRouter(prefix='/api/auth', tags=['accounts'])


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9_.@-]+$')
    password: str = Field(min_length=12, max_length=256)

    @field_validator('username')
    @classmethod
    def username_lower(cls, value):
        return value.lower()


class SetupInput(Credentials):
    setup_token: str = Field(min_length=1, max_length=512)
    display_name: str = Field(default='', max_length=120)


class RegisterInput(Credentials):
    invite_token: str = Field(min_length=16, max_length=256)
    display_name: str = Field(default='', max_length=120)


def _features() -> dict:
    try:
        capabilities = ai.capabilities()
        return {'ai_configured': bool(capabilities.get('ai_configured')),
                'babeldoc_installed': bool(capabilities.get('babeldoc_installed'))}
    except (ValueError, OSError):
        return {'ai_configured': False, 'babeldoc_installed': False}


def session_payload(session: dict | None) -> dict:
    return {'setup_required': auth.setup_required(),
            'user': session['user'] if session else None,
            'csrf_token': session['csrf_token'] if session else None,
            'features': _features() if session else {}}


def _establish(response: Response, request: Request, user_id: str) -> dict:
    token, session = auth.new_session(user_id)
    old_token = request.cookies.get(auth.COOKIE)
    if old_token:
        with core.db() as con:
            con.execute('DELETE FROM auth_sessions WHERE token_hash=?', (auth.token_hash(old_token),))
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS,
                        httponly=True, samesite='lax', path='/',
                        secure=request.url.scheme == 'https' or os.environ.get('PAPERSTUDIO_COOKIE_SECURE') == '1')
    return session_payload(session)


@router.get('/session')
def session(request: Request):
    return session_payload(auth.session_from_token(request.cookies.get(auth.COOKIE)))


@router.post('/setup', status_code=201)
def setup(body: SetupInput, request: Request, response: Response):
    identity = 'setup:' + (request.client.host if request.client else 'unknown')
    auth.rate_limit(identity)
    if not auth.setup_required():
        raise HTTPException(409, '管理员已创建，请登录')
    if not hmac.compare_digest(body.setup_token, auth.expected_setup_token()):
        auth.rate_limit(identity, failed=True)
        raise HTTPException(403, '管理员初始化口令无效')
    encoded = auth.hash_password(body.password)
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise HTTPException(409, '管理员已创建，请登录')
        user = auth.create_account(con, body.username, encoded, body.display_name.strip(), administrator=True)
    (core.DATA_DIR / 'bootstrap-token').unlink(missing_ok=True)
    auth.rate_limit(identity, clear=True)
    return _establish(response, request, user['id'])


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    identity = 'login:' + (request.client.host if request.client else 'unknown') + ':' + body.username
    auth.rate_limit(identity)
    with core.db() as con:
        user = con.execute('SELECT * FROM users WHERE username=? AND disabled=0', (body.username,)).fetchone()
    dummy = 'pbkdf2_sha256$600000$' + '0' * 32 + '$' + '0' * 64
    valid = auth.verify_password(body.password, user['password_hash'] if user else dummy)
    if not valid:
        auth.rate_limit(identity, failed=True)
        raise HTTPException(401, '账号或密码错误')
    auth.rate_limit(identity, clear=True)
    return _establish(response, request, user['id'])


@router.post('/register', status_code=201)
def register(body: RegisterInput, request: Request, response: Response):
    identity = 'register:' + (request.client.host if request.client else 'unknown')
    auth.rate_limit(identity)
    encoded = auth.hash_password(body.password)
    try:
        with core.db() as con:
            con.execute('BEGIN IMMEDIATE')
            user = auth.create_account(con, body.username, encoded, body.display_name.strip())
            auth.consume_invitation(con, body.invite_token, user['id'])
    except sqlite3.IntegrityError:
        auth.rate_limit(identity, failed=True)
        raise HTTPException(409, '此账号名已被使用') from None
    except HTTPException:
        auth.rate_limit(identity, failed=True)
        raise
    auth.rate_limit(identity, clear=True)
    return _establish(response, request, user['id'])


@router.post('/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE)
    if token:
        with core.db() as con:
            con.execute('DELETE FROM auth_sessions WHERE token_hash=?', (auth.token_hash(token),))
    response.delete_cookie(auth.COOKIE, path='/')
    return {'ok': True}

