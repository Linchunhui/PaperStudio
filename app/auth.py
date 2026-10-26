"""Per-request identities, password sessions and additive multi-user migration."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException

from . import core

_USER: ContextVar[str] = ContextVar("paperstudio_user", default="local")
COOKIE = "paperstudio_session"
SESSION_SECONDS = 7 * 24 * 3600


def current_user_id() -> str:
    return _USER.get()


@contextmanager
def as_user(user_id: str):
    """Use only for a verified session or a durable job's recorded owner."""
    token = _USER.set(user_id)
    try:
        yield
    finally:
        _USER.reset(token)


def init_auth() -> None:
    with core.db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user', inbox_id TEXT NOT NULL,
            created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_attempts (
            identity_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT NOT NULL REFERENCES groups(id), user_id TEXT NOT NULL REFERENCES users(id),
            role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),
            joined_at TEXT NOT NULL, PRIMARY KEY(group_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS group_invitations (
            token_hash TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id),
            role TEXT NOT NULL, created_by TEXT NOT NULL,
            expires_at INTEGER NOT NULL, used_by TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_shares (
            project_id TEXT NOT NULL REFERENCES projects(id), group_id TEXT NOT NULL REFERENCES groups(id),
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id,group_id)
        );
        CREATE TABLE IF NOT EXISTS reading_progress (
            user_id TEXT NOT NULL, paper_id TEXT NOT NULL, project_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unread', last_page INTEGER NOT NULL DEFAULT 1,
            page_offset REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id,paper_id,project_id),
            FOREIGN KEY(paper_id,project_id) REFERENCES project_papers(paper_id,project_id)
        );
        CREATE TABLE IF NOT EXISTS annotation_versions (
            id TEXT PRIMARY KEY, annotation_id TEXT NOT NULL, version INTEGER NOT NULL,
            editor_id TEXT NOT NULL, visibility TEXT NOT NULL, content_json TEXT NOT NULL,
            action TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(annotation_id,version)
        );
        """)
        for column, declaration in [
            ("owner_id", "TEXT NOT NULL DEFAULT 'local'"),
            ("visibility", "TEXT NOT NULL DEFAULT 'private'"),
            ("version", "INTEGER NOT NULL DEFAULT 1"),
        ]:
            columns = {r['name'] for r in con.execute('PRAGMA table_info(annotations)')}
            if column not in columns:
                con.execute(f'ALTER TABLE annotations ADD COLUMN {column} {declaration}')
        con.execute("CREATE INDEX IF NOT EXISTS annotations_visibility ON annotations(project_id,owner_id,visibility)")
        con.execute("CREATE INDEX IF NOT EXISTS group_members_user ON group_members(user_id)")
        has_users = con.execute('SELECT 1 FROM users LIMIT 1').fetchone()
    if not has_users and not os.environ.get('PAPERSTUDIO_SETUP_TOKEN'):
        token_path = core.DATA_DIR / 'bootstrap-token'
        try:
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32) + '\n')


def setup_required() -> bool:
    with core.db() as con:
        return con.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None


def public_user(row) -> dict:
    return {k: row[k] for k in ('id', 'username', 'display_name', 'role', 'inbox_id')} | {'is_admin': row['role'] == 'admin'}


def current_user() -> dict:
    identifier = current_user_id()
    if identifier == 'local':
        # Legacy internal scripts/tests run without HTTP. The HTTP middleware
        # never accepts this identity as an anonymous authentication fallback.
        return {'id': 'local', 'username': 'local', 'display_name': '本地所有者', 'role': 'admin', 'is_admin': True, 'inbox_id': 'inbox'}
    with core.db() as con:
        row = con.execute('SELECT * FROM users WHERE id=? AND disabled=0', (identifier,)).fetchone()
    if not row:
        raise HTTPException(401, '请重新登录')
    return public_user(row)


def require_admin() -> dict:
    user = current_user()
    if not user['is_admin']:
        raise HTTPException(403, '此操作仅管理员可用')
    return user


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    rounds = 600_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), rounds).hex()
    return f'pbkdf2_sha256${rounds}${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session_from_token(token: str | None) -> dict | None:
    if not token or len(token) > 256:
        return None
    with core.db() as con:
        row = con.execute('''SELECT s.csrf_token,s.expires_at,u.* FROM auth_sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0''',
            (token_hash(token), int(time.time()))).fetchone()
    if not row:
        return None
    return {'user': public_user(row), 'csrf_token': row['csrf_token'], 'expires_at': row['expires_at']}


def new_session(user_id: str) -> tuple[str, dict]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with core.db() as con:
        con.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (int(time.time()),))
        con.execute('INSERT INTO auth_sessions VALUES(?,?,?,?,?)',
                    (token_hash(token), user_id, csrf, int(time.time()) + SESSION_SECONDS, core.now()))
    return token, session_from_token(token)


def rate_limit(identity: str, *, failed: bool = False, clear: bool = False):
    key, timestamp = token_hash(identity), int(time.time())
    with core.db() as con:
        row = con.execute('SELECT * FROM auth_attempts WHERE identity_hash=?', (key,)).fetchone()
        if clear:
            con.execute('DELETE FROM auth_attempts WHERE identity_hash=?', (key,))
            return
        if row and timestamp - row['window_start'] < 900 and row['failures'] >= 8:
            raise HTTPException(429, '尝试次数过多，请 15 分钟后重试')
        if failed:
            count = row['failures'] + 1 if row and timestamp - row['window_start'] < 900 else 1
            start = row['window_start'] if count > 1 else timestamp
            con.execute('INSERT OR REPLACE INTO auth_attempts VALUES(?,?,?)', (key, count, start))


def expected_setup_token() -> str:
    value = os.environ.get('PAPERSTUDIO_SETUP_TOKEN')
    if value:
        return value
    try:
        return (core.DATA_DIR / 'bootstrap-token').read_text().strip()
    except OSError:
        raise HTTPException(409, '管理员初始化口令尚未创建，请重启服务') from None


def create_account(con, username: str, password_hash: str, display_name: str, *, administrator=False) -> dict:
    user_id = core.uid()
    inbox_id = 'inbox' if administrator else f'inbox_{user_id}'
    timestamp = core.now()
    con.execute('INSERT INTO users(id,username,display_name,password_hash,role,inbox_id,created_at) VALUES(?,?,?,?,?,?,?)',
                (user_id, username.lower(), display_name or username, password_hash, 'admin' if administrator else 'user', inbox_id, timestamp))
    if administrator:
        # Only the inaugural admin claims legacy local rows. Never reassign
        # another real account's data or globally grant admins private access.
        tables = [r['name'] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if not table.replace('_', '').isalnum():
                continue
            columns = {r['name'] for r in con.execute(f'PRAGMA table_info({table})')}
            if 'owner_id' in columns:
                con.execute(f"UPDATE {table} SET owner_id=? WHERE owner_id='local'", (user_id,))
        con.execute("UPDATE annotation_versions SET editor_id=? WHERE editor_id='local'", (user_id,))
        con.execute("UPDATE reading_progress SET user_id=? WHERE user_id='local'", (user_id,))
        con.execute('''INSERT OR IGNORE INTO reading_progress(user_id,paper_id,project_id,status,last_page,page_offset,updated_at)
            SELECT ?,pp.paper_id,pp.project_id,pp.status,pp.last_page,pp.page_offset,pp.updated_at FROM project_papers pp
            JOIN projects p ON p.id=pp.project_id WHERE p.owner_id=?''', (user_id, user_id))
    con.execute('INSERT OR IGNORE INTO projects(id,name,parent_id,created_at,owner_id) VALUES(?,?,NULL,?,?)',
                (inbox_id, '待读收件箱', timestamp, user_id))
    return {'id': user_id, 'inbox_id': inbox_id}


def consume_invitation(con, token: str, user_id: str) -> str:
    row = con.execute('SELECT * FROM group_invitations WHERE token_hash=?', (token_hash(token),)).fetchone()
    if not row or row['used_by'] or row['expires_at'] <= int(time.time()):
        raise HTTPException(410, '邀请已失效或已使用')
    issuer = con.execute('SELECT role FROM group_members WHERE group_id=? AND user_id=?',
                         (row['group_id'], row['created_by'])).fetchone()
    if not issuer or issuer['role'] != 'admin':
        raise HTTPException(410, '邀请创建者已无管理员权限，请索取新的邀请')
    con.execute('INSERT OR IGNORE INTO group_members(group_id,user_id,role,joined_at) VALUES(?,?,?,?)',
                (row['group_id'], user_id, row['role'], core.now()))
    con.execute('UPDATE group_invitations SET used_by=? WHERE token_hash=?', (user_id, token_hash(token)))
    return row['group_id']

