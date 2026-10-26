"""Invite-only groups and explicit project sharing."""
from __future__ import annotations

import secrets
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import auth, core

router = APIRouter(prefix='/api', tags=['groups'])
Role = Literal['admin', 'editor', 'viewer']


class GroupInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('小组名称不能为空')
        return value.strip()


class InvitationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Role = 'viewer'
    expires_days: int = Field(default=7, ge=1, le=30)


class JoinInput(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class RoleInput(BaseModel):
    role: Role


class ShareInput(BaseModel):
    group_id: str = Field(min_length=1, max_length=128)


def get_group(group_id: str, *, administer=False) -> dict:
    with core.db() as con:
        row = con.execute('''SELECT g.*,m.role FROM groups g JOIN group_members m ON g.id=m.group_id
            WHERE g.id=? AND m.user_id=?''', (group_id,auth.current_user_id())).fetchone()
    if not row:
        raise HTTPException(404, '小组不存在')
    if administer and row['role'] != 'admin':
        raise HTTPException(403, '需要小组管理员权限')
    return dict(row)


@router.get('/groups')
def groups():
    with core.db() as con:
        rows = con.execute('''SELECT g.*,m.role,(SELECT COUNT(*) FROM group_members other WHERE other.group_id=g.id) AS member_count
            FROM groups g JOIN group_members m ON g.id=m.group_id WHERE m.user_id=? ORDER BY g.created_at''',
            (auth.current_user_id(),)).fetchall()
    return [dict(row) for row in rows]


@router.post('/groups', status_code=201)
def create_group(body: GroupInput):
    identifier, user_id = core.uid(), auth.current_user_id()
    with core.db() as con:
        con.execute('INSERT INTO groups VALUES(?,?,?,?)', (identifier,body.name,user_id,core.now()))
        con.execute('INSERT INTO group_members VALUES(?,?,?,?)', (identifier,user_id,'admin',core.now()))
    return get_group(identifier)


@router.get('/groups/{group_id}')
def detail(group_id: str):
    group = get_group(group_id)
    with core.db() as con:
        group['members'] = [dict(row) for row in con.execute('''SELECT gm.user_id,gm.role,gm.joined_at,u.username,u.display_name
            FROM group_members gm JOIN users u ON u.id=gm.user_id WHERE gm.group_id=? ORDER BY gm.joined_at''', (group_id,))]
        ids = [row['project_id'] for row in con.execute('SELECT project_id FROM project_shares WHERE group_id=?', (group_id,))]
    group['projects'] = [core.get_project(identifier) for identifier in ids]
    return group


@router.post('/groups/{group_id}/invitations', status_code=201)
def invitation(group_id: str, body: InvitationInput):
    get_group(group_id, administer=True)
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + body.expires_days * 86400
    with core.db() as con:
        con.execute('INSERT INTO group_invitations(token_hash,group_id,role,created_by,expires_at,created_at) VALUES(?,?,?,?,?,?)',
            (auth.token_hash(token),group_id,body.role,auth.current_user_id(),expires,core.now()))
    return {'token': token, 'invite_url': f'/?invite={token}', 'expires_at': expires, 'role': body.role}


@router.post('/groups/join')
def join(body: JoinInput):
    with core.db() as con:
        con.execute('BEGIN IMMEDIATE')
        group_id = auth.consume_invitation(con, body.token, auth.current_user_id())
    return get_group(group_id)


@router.patch('/groups/{group_id}/members/{user_id}')
def change_role(group_id: str, user_id: str, body: RoleInput):
    group = get_group(group_id, administer=True)
    if user_id == group['owner_id']:
        raise HTTPException(409, '不能改变小组创建者的管理员权限')
    with core.db() as con:
        changed = con.execute('UPDATE group_members SET role=? WHERE group_id=? AND user_id=?', (body.role,group_id,user_id)).rowcount
        if not changed:
            raise HTTPException(404, '成员不存在')
    return detail(group_id)


@router.delete('/groups/{group_id}/members/{user_id}', status_code=204)
def remove_member(group_id: str, user_id: str):
    group = get_group(group_id, administer=True)
    if user_id == group['owner_id']:
        raise HTTPException(409, '不能移除小组创建者')
    with core.db() as con:
        con.execute('DELETE FROM group_members WHERE group_id=? AND user_id=?', (group_id,user_id))
    return Response(status_code=204)


@router.get('/projects/{project_id}/shares')
def project_shares(project_id: str):
    project = core.get_project(project_id)
    if project['owner_id'] != auth.current_user_id():
        raise HTTPException(403, '只有项目所有者可管理共享范围')
    with core.db() as con:
        return [dict(row) for row in con.execute('SELECT ps.*,g.name FROM project_shares ps JOIN groups g ON g.id=ps.group_id WHERE ps.project_id=?', (project['id'],))]


@router.post('/projects/{project_id}/shares', status_code=201)
def share_project(project_id: str, body: ShareInput):
    project = core.get_project(project_id, write=True)
    if project['owner_id'] != auth.current_user_id():
        raise HTTPException(403, '只有项目所有者可扩大共享范围')
    group = get_group(body.group_id)
    if group['role'] == 'viewer':
        raise HTTPException(403, '只读成员不能向小组添加共享项目')
    with core.db() as con:
        con.execute('INSERT OR IGNORE INTO project_shares VALUES(?,?,?,?)', (project['id'],body.group_id,auth.current_user_id(),core.now()))
    return project_shares(project['id'])


@router.delete('/projects/{project_id}/shares/{group_id}', status_code=204)
def unshare_project(project_id: str, group_id: str):
    project = core.get_project(project_id)
    if project['owner_id'] != auth.current_user_id():
        get_group(group_id, administer=True)
    with core.db() as con:
        con.execute('DELETE FROM project_shares WHERE project_id=? AND group_id=?', (project['id'],group_id))
    return Response(status_code=204)

