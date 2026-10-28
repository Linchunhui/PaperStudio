"""Application composition with cookie sessions and explicit project permissions."""
from contextlib import asynccontextmanager
import json
import hmac
import os
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import core, jobs, imports, discovery, catalog, auth, research
from .core_routes import router as core_router
from .jobs_routes import router as jobs_router
from .import_routes import router as import_router
from .discovery_routes import router as discovery_router
from .catalog_routes import router as catalog_router
from .reading_routes import router as reading_router
from .auth_routes import router as auth_router
from .group_routes import router as group_router
from .research_routes import router as research_router
from .recommendation_routes import router as recommendation_router
from .recommendation_profiles import init_recommendations
from .recommendation_vectors import init_vectors
from .paper_feedback import init_feedback
from .feedback_routes import router as feedback_router
from .relations_routes import router as relations_router


@asynccontextmanager
async def lifespan(app):
    core.init_db()
    jobs.init_jobs()
    imports.init_imports()
    discovery.init_discovery()
    catalog.init_catalog()
    init_recommendations()
    init_vectors()
    init_feedback()
    research.init_research()
    if auth.setup_required() and os.environ.get('PAPERSTUDIO_SEED_DEMO', '1') == '1':
        from .sample import seed_sample
        seed_sample()
    jobs.start_worker()
    imports.start_worker()
    research.start_worker()
    try:
        yield
    finally:
        research.stop_worker()
        imports.stop_worker()
        jobs.stop_worker()


app = FastAPI(title='PaperStudio', version='0.4.0', lifespan=lifespan)
allowed_hosts = [v.strip() for v in os.environ.get('PAPERSTUDIO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver,terminal.local').split(',') if v.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


@app.middleware('http')
async def session_boundary(request: Request, call_next):
    path = request.url.path
    mutation = request.method not in {'GET', 'HEAD', 'OPTIONS'}
    origin = request.headers.get('origin')
    if mutation and origin:
        parsed = urlsplit(origin)
        if parsed.netloc != request.headers.get('host') or parsed.scheme not in {'http', 'https'}:
            return JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, status_code=403)
    if mutation and request.headers.get('sec-fetch-site') == 'cross-site':
        return JSONResponse({'detail': 'Cross-site writes are not allowed.'}, status_code=403)
    public = path in {'/api/health', '/api/auth/session', '/api/auth/setup', '/api/auth/login', '/api/auth/register'}
    session = auth.session_from_token(request.cookies.get(auth.COOKIE)) if path.startswith('/api/') else None
    request.state.session = session
    if path.startswith('/api/') and not public:
        if not session:
            return JSONResponse({'detail': '请先登录', 'setup_required': auth.setup_required()}, status_code=401)
        if mutation and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf_token']):
            return JSONResponse({'detail': '请求校验已失效，请刷新页面后重试'}, status_code=403)
        admin_only = path in {'/api/skills', '/api/capabilities', '/api/sources', '/api/discovery/config', '/api/catalog/import', '/api/catalog/refresh-hf'} or path.startswith('/api/admin/')
        if admin_only and not session['user']['is_admin']:
            return JSONResponse({'detail': '此页面仅管理员可用'}, status_code=403)
    if session:
        with auth.as_user(session['user']['id']):
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    if path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


app.include_router(auth_router)
app.include_router(group_router)
app.include_router(core_router)
app.include_router(jobs_router)
app.include_router(import_router)
app.include_router(discovery_router)
app.include_router(catalog_router)
app.include_router(reading_router)
app.include_router(research_router)
app.include_router(recommendation_router)
app.include_router(feedback_router)
app.include_router(relations_router)


@app.get('/api/sources')
def sources():
    auth.require_admin()
    return json.loads((core.ROOT / 'config' / 'sources.json').read_text())


@app.get('/')
@app.get('/read/{paper_id}')
def index(paper_id: str | None = None):
    return FileResponse(core.ROOT / 'web' / 'index.html')


app.mount('/static', StaticFiles(directory=core.ROOT / 'web'), name='static')


