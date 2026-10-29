import io
import json
import zipfile

from test_onboarding import client
from test_auth_groups import new_group, invite_user, upload, annotate


def test_bibtex_ris_and_zotero_preview_do_not_download(client):
    examples=[('library.bib',b'@article{key,title={A {Nested} Title},author={First, Alice and Second, Bob},year={2025},doi={10.1234/example},file={/private/attachment.pdf}}'),
              ('library.ris',b'TY  - JOUR\nTI  - A nested title\nAU  - First, Alice\nPY  - 2025\nDO  - 10.1234/example\nER  -\n'),
              ('zotero.csv',b'Title,Author,Publication Year,DOI,File Attachments\nA nested title,"First, Alice",2025,10.1234/example,/private/attachment.pdf\n')]
    for filename,content in examples:
        response=client.post('/api/bibliography/preview',files={'file':(filename,content)})
        assert response.status_code==200,response.text
        data=response.json();assert len(data['entries'])==1
        assert data['entries'][0]['doi']=='10.1234/example'
        assert '/private/' not in response.text
    assert client.get('/api/papers').json()==[]
    assert client.get('/api/imports').json()==[]
    assert client.get('/api/jobs').json()==[]


def test_reference_save_duplicate_and_export_roundtrip(client):
    entry={'title':'Research <script>alert(1)</script>', 'authors':['Alice'], 'year':'2025',
           'doi':'10.1234/test', 'url':'javascript:alert(1)'}
    first=client.post('/api/projects/inbox/bibliography',json={'entries':[entry]}).json()
    assert first['inserted']==1 and first['entries'][0]['url']=='https://doi.org/10.1234/test'
    second=client.post('/api/projects/inbox/bibliography',json={'entries':[entry]}).json()
    assert second['inserted']==0 and second['duplicates']==1
    response=client.get('/api/projects/inbox/export')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist())=={'notes.md','reading.html','references.bib','project.json'}
        assert b'<script>alert(1)</script>' not in archive.read('reading.html')
        assert b'&lt;script&gt;' in archive.read('reading.html')
        preview=client.post('/api/bibliography/preview',files={'file':('roundtrip.bib',archive.read('references.bib'))})
        assert preview.status_code==200
        assert preview.json()['entries'][0]['doi']=='10.1234/test'


def test_shared_exports_exclude_other_private_notes(client):
    group=new_group(client)
    member,user,_=invite_user(client,group,'reader','editor')
    project=client.post('/api/projects',json={'name':'Export project'}).json()['id']
    assert client.post(f'/api/projects/{project}/shares',json={'group_id':group}).status_code in {200,201}
    paper=upload(client,project)
    assert annotate(client,paper,project,note='OWNER PRIVATE').status_code==201
    assert annotate(member,paper,project,note='MEMBER PRIVATE').status_code==201
    assert annotate(member,paper,project,note='MEMBER SHARED',visibility='shared').status_code==201
    default=client.get(f'/api/projects/{project}/export?format=json').text
    assert 'OWNER PRIVATE' in default and 'MEMBER PRIVATE' not in default and 'MEMBER SHARED' not in default
    shared=client.get(f'/api/projects/{project}/export?format=json&include_shared=true').text
    assert 'MEMBER SHARED' in shared and 'MEMBER PRIVATE' not in shared
    outsider=client.post('/api/projects',json={'name':'Not shared'}).json()['id']
    assert member.get(f'/api/projects/{outsider}/export').status_code==404
    assert member.get(f'/api/projects/{outsider}/bibliography').status_code==404


def test_bad_imports_are_explicit(client):
    for name,content in [('unknown.txt',b'anything'),('bad.ris',b'TY  - JOUR\nTI  - missing end'),('bad.bib',b'not bibtex')]:
        assert client.post('/api/bibliography/preview',files={'file':(name,content)}).status_code==422
    assert client.post('/api/projects/inbox/bibliography',json={'entries':[]}).status_code==422
