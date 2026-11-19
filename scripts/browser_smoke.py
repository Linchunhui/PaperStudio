"""Real Chromium CI journey; all documents are self-authored and no model is called."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'test-results';OUTPUT.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='paperstudio-browser-') as data:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    environment={**os.environ,'PAPERSTUDIO_DATA_DIR':data,'PAPERSTUDIO_SEED_DEMO':'0',
                 'PAPERSTUDIO_CATALOG_MODE':'light','AI_PROVIDER':'none','AI_BASE_URL':'','AI_API_KEY':'',
                 'PAPERSTUDIO_PARSER':'pymupdf'}
    with (OUTPUT/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'run.py','--no-env','--no-demo','--port',str(port)],cwd=ROOT,env=environment,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+60
            while True:
                try:
                    urllib.request.urlopen(base+'/api/health',timeout=1).close();break
                except OSError:
                    if process.poll() is not None or time.monotonic()>deadline: raise RuntimeError('Test service did not start')
                    time.sleep(.25)
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch()
                context=browser.new_context(viewport={'width':1440,'height':1000})
                page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base)
                page.locator('[name=setup_token]').fill((Path(data)/'bootstrap-token').read_text().strip())
                page.locator('[name=username]').fill('browser-test')
                page.locator('[name=password]').fill(secrets.token_urlsafe(24))
                page.get_by_role('button',name='创建并进入',exact=True).click()
                expect(page.get_by_role('button',name='打开阅读示例',exact=True)).to_be_visible()
                page.goto(base+'/?view=readers')
                expect(page.get_by_role('heading',name='阅读用户',exact=True).first).to_be_visible()
                page.get_by_text('新建阅读用户',exact=True).click()
                page.locator('[data-reader-create] [name=name]').fill('Browser reader')
                page.locator('[name=persona]').fill('Researcher')
                page.locator('[name=goals]').fill('Read papers')
                page.locator('[name=custom]').fill('document understanding')
                page.get_by_role('button',name='创建阅读用户',exact=True).click()
                page.get_by_role('button',name='Browser reader',exact=True).click()
                expect(page.get_by_role('button',name='Browser reader · 当前',exact=True)).to_be_disabled()
                assert not page.locator('[data-view=skills]').count()
                page.locator('[data-reader-switch]').filter(has_text='主账号').click()
                expect(page.locator('[data-view=skills]')).to_be_visible()
                page.goto(base)
                page.get_by_role('button',name='打开阅读示例',exact=True).click()
                page.wait_for_url('**/read/**')
                sample_url=page.url
                layer=page.locator('.pdf-page[data-side=original] .text-layer').first
                expect(layer).to_be_visible(timeout=20000)
                page.locator('.pdf-page[data-side=original] img').first.evaluate('img => img.decode()')
                span=layer.locator('span').filter(has_text='A paper becomes useful').first
                expect(span).to_be_visible()
                layer.focus()
                span.evaluate('el => { const r=document.createRange(); r.selectNodeContents(el); const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); }')
                page.keyboard.press('Shift+F10')
                page.get_by_role('menuitem',name='笔记',exact=True).click()
                page.locator('#note-draft').fill('Browser regression: evidence stays with the selected original.')
                page.get_by_role('button',name='保存笔记',exact=True).click()
                expect(page.locator('#note-draft')).to_have_count(0)
                page.reload()
                page.locator('[data-reader-tab=notes]').click()
                expect(page.get_by_text('Browser regression: evidence stays with the selected original.',exact=True)).to_be_visible()
                page.locator('.pdf-page[data-side=original] img').first.evaluate('img => img.decode()')
                page.screenshot(path=str(OUTPUT/'reader-desktop.png'),full_page=True)
                assert page.request.get(base+'/api/jobs').json()==[]
                page.goto(base+'/?view=library')
                card=page.locator('.unified-paper').filter(has_text='自编 PDF 示例').first
                expect(card).to_be_visible()
                expect(card.locator('.paper-card-structured')).not_to_have_attribute('open','')
                card.locator('.paper-card-structured > summary').click()
                expect(card.locator('.card-structured-body')).to_be_visible()
                page.locator('[data-library-columns="2"]').click()
                expect(page.locator('.unified-paper-grid')).to_have_attribute('data-columns','2')
                page.locator('[data-library-columns="1"]').click()
                expect(page.locator('.unified-paper-grid')).to_have_attribute('data-columns','1')
                card.locator('.paper-card-management > summary').click()
                expect(card.locator('[data-process-paper]')).to_be_visible()
                card.locator('[data-paper-open]').click()
                page.wait_for_url('**/read/**')
                assert page.url==sample_url
                page.goto(base+'/?view=library')
                page.set_viewport_size({'width':390,'height':844})
                expect(page.locator('.unified-paper').first).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.screenshot(path=str(OUTPUT/'library-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.goto(base+'/?view=skills')
                expect(page.locator('#model-setup-form')).to_be_visible()
                expect(page.locator('#model-setup-form [name=provider]')).to_have_value('none')
                expect(page.get_by_role('button',name='测试实际调用',exact=True)).to_be_disabled()
                page.goto(base+'/?view=library&section=portability')
                expect(page.locator('[data-project-template]')).to_have_count(3)
                page.locator('[data-project-template=document]').click()
                expect(page.get_by_role('heading',name='OCR 与文档解析',exact=True)).to_be_visible()
                page.get_by_role('button',name='导出成果',exact=True).click()
                with page.expect_download() as download:
                    page.get_by_role('button',name='下载成果包',exact=True).click()
                download.value.save_as(OUTPUT/'project.zip')
                assert (OUTPUT/'project.zip').stat().st_size>100
                page.screenshot(path=str(OUTPUT/'project-desktop.png'),full_page=True)
                page.goto(base+'/demo/')
                page.get_by_role('tab',name='② 项目与成果').click()
                expect(page.locator('#scene-projects')).to_be_visible()
                page.get_by_role('tab',name='① 阅读与笔记').click()
                page.screenshot(path=str(OUTPUT/'demo-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(OUTPUT/'demo-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.goto(base+'/?view=library&section=portability')
                expect(page.locator('#portable-project')).to_be_visible()
                page.screenshot(path=str(OUTPUT/'workspace-mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                assert not errors,errors
                browser.close()
                print('Chromium setup → sample → anchored note → reload → template → export and responsive demo passed.')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill();process.wait()
