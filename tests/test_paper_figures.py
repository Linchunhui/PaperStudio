import hashlib
import fitz
import pytest
from fastapi import HTTPException
from app import auth, core, paper_figures
from test_auth_groups import workspace, setup, new_group, invite_user


def test_caption_selection():
    page={'page':1,'width':600,'height':800,'blocks':[
        {'label':'image','bbox':[50,100,500,300],'text':''},
        {'label':'figure_caption','bbox':[50,310,500,335],'text':'Figure 1: Architecture overview.'}]}
    result=paper_figures.page_figures(page)
    assert result[0]['number']==1 and result[0]['selection']=='overview-caption'
    page['blocks'][1]['text']='Unnumbered decorative image'
    assert paper_figures.page_figures(page)==[]


def test_linked_figure_permission_and_source(workspace, monkeypatch, tmp_path):
    client=workspace; owner=setup(client)
    with fitz.open() as doc:
        doc.new_page(); content=doc.tobytes()
    with auth.as_user(owner['id']):
        paper=core.add_paper(content, owner['inbox_id'], 'Example')
    with core.db() as con:
        con.execute("INSERT INTO catalog_papers(id,title,search_text,year,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('cat_test','Example','example',2026,'{}',core.now(),core.now()))
        con.execute("INSERT INTO paper_catalog_links(paper_id,catalog_paper_id,match_method,created_at) VALUES(?,?,?,?)",(paper['id'],'cat_test','source',core.now()))
    selected={'page':1,'number':1,'caption':'Figure 1: Example','clip':(.1,.1,.5,.3),'selection':'first-numbered-figure','aspect_ratio':1.5}
    monkeypatch.setattr(paper_figures,'_native_selection',lambda *a:selected)
    with auth.as_user(owner['id']):
        figure=paper_figures.locate('cat_test')
    assert figure
    response=client.get('/api/catalog/cat_test/figure.png',params={'v':figure['version']})
    assert response.status_code==200 and response.content.startswith(b'\x89PNG')
    assert client.get('/api/catalog/cat_test/figure.png',params={'v':'0'*24}).status_code==409
    assert client.get('/api/catalog/cat_test/figure.png',params={'v':figure['version'],'width':99999}).status_code==422
    outsider,_,_=invite_user(client,new_group(client),'figureoutsider','viewer')
    assert outsider.get('/api/catalog/cat_test/figure.png',params={'v':figure['version']}).status_code==404


def test_native_selection_rejects_changed_source(tmp_path):
    source=tmp_path/'source.pdf'
    with fitz.open() as doc:
        doc.new_page(); content=doc.tobytes()
    source.write_bytes(content)
    stat=source.stat()
    assert paper_figures._native_selection(str(source),stat.st_mtime_ns,stat.st_size,'0'*64) is None


def test_native_raster_caption_pdf(tmp_path):
    source=tmp_path/'figure.pdf'
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=800)
        pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,450,200));pix.clear_with(127)
        page.insert_image(fitz.Rect(50,100,500,300),stream=pix.tobytes('png'))
        page.insert_text((50,330),'Figure 1: Architecture overview.',fontsize=12)
        content=doc.tobytes()
    source.write_bytes(content); stat=source.stat()
    result=paper_figures._native_selection(str(source),stat.st_mtime_ns,stat.st_size,hashlib.sha256(content).hexdigest())
    assert result['number']==1 and result['page']==1
    assert result['selection']=='overview-caption'
