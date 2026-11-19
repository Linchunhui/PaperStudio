"""Managed reader isolation, session safety and account-local activity."""
import pytest
from app import auth, core
from test_auth_groups import workspace, setup, new_group, invite_user, upload, PASSWORD

BODY = {'name': 'Research learner', 'persona': 'Researcher', 'goals': ['Read carefully'],
        'interests': [{'topic_id': 'document.ocr', 'level': 'beginner'},
                      {'custom': 'archives', 'level': 'experienced'}]}


def create(client):
    response = client.post('/api/me/reader-accounts', json=BODY)
    assert response.status_code == 201, response.text
    return response.json()['id']


def switch(client, identifier):
    response = client.post('/api/me/reader-accounts/switch', json={'user_id': identifier})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return client.get('/api/auth/session').json()


def test_managed_accounts_isolation_and_profile(workspace):
    client = workspace
    owner = setup(client)
    child = create(client)
    session = switch(client, child)
    assert session['user']['id'] == child
    assert session['account_owner']['id'] == owner['id']
    assert session['is_reader_account'] and not session['user']['is_admin']
    assert client.get('/api/me/recommendation-profile').json()['topic_ids'] == ['document.ocr']
    assert client.get('/api/admin/model-settings').status_code == 403
    assert client.post('/api/me/reader-accounts/events', json={'kind': 'search', 'value': 'handwriting'}).status_code == 200
    assert client.get('/api/projects', headers={'X-Reader-Account': owner['id']}).status_code == 409
    switch(client, owner['id'])
    assert client.get('/api/me/recommendation-profile').json()['topic_ids'] == []
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM reader_events WHERE user_id=?', (owner['id'],)).fetchone()[0] == 0
        assert con.execute('SELECT count(*) FROM reader_events WHERE user_id=?', (child,)).fetchone()[0] == 1
    outsider, other, _ = invite_user(client, new_group(client), 'outsider', 'viewer')
    assert outsider.get('/api/me/reader-accounts').json()['items'] == []
    assert outsider.post('/api/me/reader-accounts/switch', json={'user_id': child}).status_code == 404
    assert client.post('/api/me/reader-accounts', json={**BODY, 'interests': [{'topic_id': 'missing', 'level': 'expert'}]}).status_code == 422
    # No fixtures or simulated browsing histories are seeded through production routes.
    assert client.post('/api/me/reader-accounts/seed-examples').status_code == 404


def test_switch_invalidates_old_tab_writes_without_account_header(workspace):
    client = workspace
    owner = setup(client)
    child = create(client)
    old_csrf = client.headers['X-CSRF-Token']
    switch(client, child)
    assert old_csrf != client.headers['X-CSRF-Token']
    response = client.post('/api/me/reader-accounts/events', json={'kind': 'search', 'value': 'stale'},
                           headers={'X-CSRF-Token': old_csrf})
    assert response.status_code == 403
    with core.db() as con:
        assert con.execute('SELECT count(*) FROM reader_events').fetchone()[0] == 0


def test_disabled_reader_session_does_not_regain_owner_authority(workspace):
    client = workspace
    owner = setup(client)
    child = create(client)
    switch(client, child)
    with core.db() as con:
        con.execute('UPDATE users SET disabled=1 WHERE id=?', (child,))
    assert client.get('/api/auth/session').json()['user'] is None
    assert client.get('/api/admin/model-settings').status_code == 401
    # The owner can recover using their actual password, not a silent role escalation.
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD})
    assert response.status_code == 200
    assert response.json()['user']['id'] == owner['id']


def test_managed_account_cannot_log_in_with_placeholder_password(workspace):
    client = workspace
    setup(client)
    child = create(client)
    with core.db() as con:
        user = con.execute('SELECT username FROM users WHERE id=?', (child,)).fetchone()
    assert client.post('/api/auth/login', json={'username': user['username'], 'password': '!managed-reader-account'}).status_code == 401


def test_reader_private_library_isolation(workspace):
    client = workspace
    owner = setup(client)
    child = create(client)
    paper = upload(client, owner['inbox_id'])
    switch(client, child)
    assert client.get('/api/papers/' + paper['id']).status_code in (403, 404)
    assert all(p['id'] != owner['inbox_id'] for p in client.get('/api/projects').json())
    assert client.post('/api/me/reader-accounts/events', json={'kind': 'open', 'value': 'missing-paper'}).status_code == 404
