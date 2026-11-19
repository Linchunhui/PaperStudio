"""Generic topic packages: provenance, access, audit and immutable releases."""
import hashlib
import json
from pathlib import Path
import pytest

from app import topics
from test_auth_groups import invite_user, new_group, setup, workspace

def package(root, slug='methods'):
    folder = root / slug
    folder.mkdir(parents=True)
    text = 'A reproducible example keeps its original evidence.'
    (folder/'source.txt').write_text(text)
    baseline = 'Synthetic example, not a research finding.'
    (folder/'baseline.txt').write_text(baseline)
    change = {'id':'evidence', 'title':'Follow the evidence', 'paragraphs':['Keep provenance.'],
              'practice':'Record the source version.', 'evidence':[{
                  'source_url':'https://example.org/paper', 'snapshot':'source.txt',
                  'source_sha256':hashlib.sha256(text.encode()).hexdigest(), 'start':0, 'end':len(text),
                  'quote':text, 'locator':'Example paragraph', 'document_version_id':'example-v1'}]}
    manifest = {'title':'Methods', 'description':'Synthetic topic fixture', 'baseline':'baseline.txt',
                'baseline_sha256':hashlib.sha256(baseline.encode()).hexdigest(),
                'versions':[{'version':1}, {'version':2, 'parent':1, 'changes':[change]}]}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    return manifest

@pytest.fixture
def topic_workspace(workspace, tmp_path, monkeypatch):
    root = tmp_path/'packages'
    monkeypatch.setattr(topics, 'ASSETS', root)
    package(root)
    topics.init_topics()
    yield workspace, root

def test_review_publish_and_history(topic_workspace):
    client, root = topic_workspace
    assert client.get('/topics/methods').status_code == 401
    setup(client)
    member, _, _ = invite_user(client, new_group(client), 'reader', 'viewer')
    read, admin = '/topics/methods', '/api/admin/topics/methods'
    assert member.get(read).status_code == 200
    assert member.get(read+'?version=2').status_code == 404
    assert member.get(read+'/review').status_code == 403
    assert member.post(admin+'/publish', json={'version':2}).status_code == 403
    assert client.post(admin+'/publish', json={'version':2}).status_code == 409
    body = {'version':2,'change_id':'evidence','decision':'approved','note':'Checked the source.'}
    assert client.post(admin+'/review',json=body,headers={'X-CSRF-Token':'bad'}).status_code == 403
    assert client.post(admin+'/review',json={**body,'note':' '}).status_code == 422
    assert client.post(admin+'/review',json=body).status_code == 204
    assert client.post(admin+'/review',json=body).status_code == 409
    assert 'Follow the evidence' not in member.get(read).text
    assert client.post(admin+'/publish',json={'version':2}).status_code == 204
    assert client.post(admin+'/publish',json={'version':2}).status_code == 409
    response = member.get(read)
    assert response.headers['cache-control'] == 'no-store'
    assert 'Follow the evidence' in response.text
    assert 'Follow the evidence' not in member.get(read+'?version=1').text
    topics.init_topics()
    assert 'Follow the evidence' in member.get(read).text

def test_tamper_and_imported_review_rejected(topic_workspace):
    _, root = topic_workspace
    manifest = json.loads((root/'methods/manifest.json').read_text())
    manifest['versions'][1]['changes'][0]['review'] = {'decision':'approved'}
    (root/'methods/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Reviews'):
        topics.init_topics()
    manifest['versions'][1]['changes'][0].pop('review')
    manifest['versions'][1]['changes'][0]['evidence'][0]['quote'] = 'forged'
    (root/'methods/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='quote'):
        topics.init_topics()

def test_multiple_topics_and_immutable_package(topic_workspace):
    client, root = topic_workspace
    package(root, 'another')
    topics.init_topics()
    setup(client)
    assert '/topics/another' in client.get('/topics').text
    manifest = json.loads((root/'methods/manifest.json').read_text())
    manifest['title'] = 'Changed history'
    (root/'methods/manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='immutable'):
        topics.init_topics()

def test_path_and_url_safety(topic_workspace):
    _, root = topic_workspace
    with pytest.raises(ValueError):
        topics.package_path('../escape', 'source.txt')
    with pytest.raises(ValueError):
        topics.package_path('methods', '../source.txt')
    manifest = json.loads((root/'methods/manifest.json').read_text())
    change = manifest['versions'][1]['changes'][0]
    change['evidence'][0]['source_url'] = 'javascript:alert(1)'
    with pytest.raises(ValueError, match='HTTPS'):
        topics.validate_change('methods',change)


def test_rejected_update_and_publish_source_recheck(topic_workspace):
    client, root = topic_workspace
    setup(client)
    admin = '/api/admin/topics/methods'
    body = {'version':2,'change_id':'evidence','decision':'approved','note':'Checked.'}
    assert client.post(admin+'/review',json=body).status_code == 204
    (root/'methods/source.txt').write_text('tampered')
    assert client.post(admin+'/publish',json={'version':2}).status_code == 409
    assert client.get('/topics/methods?version=2').status_code == 404


def test_all_rejected_cannot_publish(topic_workspace):
    client, _ = topic_workspace
    setup(client)
    admin = '/api/admin/topics/methods'
    assert client.post(admin+'/review',json={'version':2,'change_id':'evidence',
        'decision':'rejected','note':'Unsupported.'}).status_code == 204
    assert client.post(admin+'/publish',json={'version':2}).status_code == 409
