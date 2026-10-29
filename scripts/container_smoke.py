"""CI smoke test of an actual non-root container, including a persistent restart."""
import http.cookiejar
import json
import secrets
import subprocess
import sys
import time
import urllib.request

image=sys.argv[1]
name='paperstudio-smoke-'+secrets.token_hex(5)
setup_token=secrets.token_urlsafe(24)
password=secrets.token_urlsafe(24)

def docker(*args):
    return subprocess.check_output(['docker',*args],text=True).strip()

try:
    docker('run','-d','--name',name,'-p','127.0.0.1::8000',
           '-e','PAPERSTUDIO_CATALOG_MODE=light','-e','AI_PROVIDER=none',
           '-e','PAPERSTUDIO_SETUP_TOKEN='+setup_token,image)
    port=docker('port',name,'8000/tcp').rsplit(':',1)[1]
    base='http://127.0.0.1:'+port
    client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf=''
    def request(path,body=None):
        headers={'Content-Type':'application/json','X-CSRF-Token':csrf}
        raw=None if body is None else json.dumps(body).encode()
        with client.open(urllib.request.Request(base+path,data=raw,headers=headers),timeout=15) as response:
            content=response.read()
            return json.loads(content) if 'application/json' in response.headers.get('Content-Type','') else content
    def ready():
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                request('/api/health');return
            except (OSError,ValueError): time.sleep(1)
        raise AssertionError('Container did not become healthy within 120 seconds')
    ready()
    assert docker('exec',name,'id','-u')=='10001'
    account=request('/api/auth/setup',{'setup_token':setup_token,'username':'smoke','password':password})
    csrf=account['csrf_token']
    assert request('/api/admin/model-settings')['provider']=='none'
    paper=request('/api/onboarding/sample',{})
    assert not paper['model_called']
    assert request('/api/papers/'+paper['paper_id']+'/file').startswith(b'%PDF-')
    assert b'PaperStudio' in request('/demo/')
    assert request('/api/jobs')==[]
    docker('restart',name);ready()
    assert request('/api/onboarding/sample',{})['paper_id']==paper['paper_id']
    assert request('/api/papers/'+paper['paper_id']+'/file').startswith(b'%PDF-')
    print('Container startup, non-root user, setup, model-free PDF and persistent restart passed.')
finally:
    subprocess.run(['docker','rm','-f','-v',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
