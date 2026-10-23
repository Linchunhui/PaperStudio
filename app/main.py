"""Application composition. Start with python run.py; local single-user MVP."""
from contextlib import asynccontextmanager
import json
import os
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import core, jobs, imports, discovery
from .core_routes import router as core_router
from .jobs_routes import router as jobs_router
from .import_routes import router as import_router
from .discovery_routes import router as discovery_router


@asynccontextmanager
async def lifespan(app):
    core.init_db()
    jobs.init_jobs()
    imports.init_imports()
    discovery.init_discovery()
    if os.environ.get('PAPERSTUDIO_SEED_DEMO', '1') == '1':
        from .sample import seed_sample
        seed_sample()
    jobs.start_worker()
    imports.start_worker()
    try:
        yield
    finally:
        imports.stop_worker()
        jobs.stop_worker()


app = FastAPI(title='PaperStudio', version='0.2.0', lifespan=lifespan)
allowed_hosts = [v.strip() for v in os.environ.get('PAPERSTUDIO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver,terminal.local').split(',') if v.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


@app.middleware('http')
async def local_boundary(request: Request, call_next):
    # No permissive CORS: other websites must not send state-changing requests to
    # a user's local workbench. Reverse proxies must preserve the Host header.
    origin = request.headers.get('origin')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.headers.get('host') or parsed.scheme not in {'http', 'https'}:
            return JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


app.include_router(core_router)
app.include_router(jobs_router)
app.include_router(import_router)
app.include_router(discovery_router)


@app.get('/api/sources')
def sources():
    return json.loads((core.ROOT / 'config' / 'sources.json').read_text())


@app.get('/')
@app.get('/read/{paper_id}')
def index(paper_id: str | None = None):
    return FileResponse(core.ROOT / 'web' / 'index.html')


app.mount('/static', StaticFiles(directory=core.ROOT / 'web'), name='static')
