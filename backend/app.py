from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import socketio
import httpx
from fastapi import FastAPI,Request,HTTPException,Query
from fastapi.responses import JSONResponse
from pydantic import Field,ValidationError
from .auth import Auth
from .http_limit import BodyLimit
from .config import Settings
from .service import Service
from .schemas import Strict,Submit,MCPRemote

class Login(Strict): token:str=Field(min_length=1,max_length=512)
class Enable(Strict): enabled:bool
class Decision(Strict): allow:bool
class SyncRequest(Strict): after:int=Field(0,ge=0)
class SocketCancel(Strict): job_id:str=Field(min_length=1,max_length=80)
class SocketDecision(Strict): approval_id:str=Field(min_length=1,max_length=80); allow:bool

def create_app(settings=None):
    settings=settings or Settings(); auth=Auth(settings)
    sio=socketio.AsyncServer(async_mode='asgi',cors_allowed_origins=settings.origins,
        async_handlers=True,max_http_buffer_size=1024*1024,logger=False,engineio_logger=False)
    async def publish(event): await sio.emit('event',event,room='operator')
    service=Service(settings,publish)
    @asynccontextmanager
    async def lifespan(api):
        if os.name!='posix': raise RuntimeError('Use Linux, macOS, or Windows via WSL2; native Windows is not qualified')
        import fcntl
        lock=(settings.state/'process.lock').open('a')
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close(); raise RuntimeError('Another console process owns this state directory. Use exactly one Uvicorn worker.')
        try:
            await service.start()
            print(f'ModelMaestro backend ready. Access token file: {settings.state / "access-token"}',flush=True)
            yield
        finally:
            await service.stop(); lock.close()
    api=FastAPI(title='ModelMaestro',version='0.1.0',lifespan=lifespan,
                docs_url=None,redoc_url=None,openapi_url=None)
    api.state.service=service
    @api.middleware('http')
    async def secure(request:Request,call_next):
        if request.url.path=='/api/health': return await call_next(request)
        # Check Host even when somebody accesses the control-plane port directly.
        allowed={urlsplit(o).netloc for o in settings.origins}
        allowed.update({'127.0.0.1:'+os.getenv('BACKEND_PORT','8765'),'localhost:'+os.getenv('BACKEND_PORT','8765')})
        if request.headers.get('host','') not in allowed:
            return JSONResponse({'detail':'Unrecognised Host'},status_code=403)
        try: content_length=int(request.headers.get('content-length','0'))
        except ValueError: return JSONResponse({'detail':'Invalid Content-Length'},status_code=400)
        if content_length>1_048_576:
            return JSONResponse({'detail':'Request body too large'},status_code=413)
        if request.method not in ('GET','HEAD','OPTIONS') and not auth.csrf_ok(request.headers):
            return JSONResponse({'detail':'Origin/CSRF check failed'},status_code=403)
        if request.url.path!='/api/login' and not auth.authorised(request.headers):
            return JSONResponse({'detail':'Authentication required'},status_code=401)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
    @api.exception_handler(ValueError)
    async def value_error(request,exc): return JSONResponse({'detail':settings.redact(str(exc))},status_code=400)
    @api.exception_handler(httpx.HTTPError)
    async def external_error(request,exc): return JSONResponse({'detail':settings.redact('External service request failed: '+str(exc))},status_code=502)
    @api.exception_handler(KeyError)
    async def key_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=404)
    @api.get('/api/health')
    async def health(): return {'status':'ok','service':'modelmaestro'}
    @api.post('/api/login')
    async def login(body:Login,request:Request):
        peer=request.client.host if request.client else 'unknown'
        if not auth.rate_limit(peer): raise HTTPException(429,'Too many failed attempts; retry after the rate-limit window')
        if not auth.valid_token(body.token): auth.failed(peer); raise HTTPException(401,'Incorrect access token')
        response=JSONResponse({'ok':True})
        response.set_cookie('console_session',auth.session(),httponly=True,samesite='strict',
            secure=settings.origin.startswith('https:'),max_age=7*86400,path='/')
        return response
    @api.post('/api/logout')
    async def logout():
        response=JSONResponse({'ok':True}); response.delete_cookie('console_session'); return response
    @api.get('/api/state')
    async def state(): return service.snapshot()
    @api.get('/api/schema')
    async def schema(): return api.openapi()
    @api.get('/api/jobs')
    async def jobs(limit:int=Query(100,ge=1,le=1000),chat_id:str|None=None): return service.store.jobs(limit,chat_id)
    @api.get('/api/events')
    async def events(after:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=1000),job_id:str|None=None):
        rows=service.store.events(after,limit,job_id)
        return {'events':rows,'next':rows[-1]['seq'] if rows else after,'cursor':service.store.cursor()}
    @api.get('/api/jobs/{ident}')
    async def job(ident:str):
        result=service.store.get(ident)
        if not result: raise HTTPException(404,'Job not found')
        return result
    @api.post('/api/jobs')
    async def submit(body:Submit): return await service.submit(body.model_dump())
    @api.post('/api/jobs/{ident}/cancel')
    async def cancel(ident:str): return await service.scheduler.cancel(ident)
    @api.post('/api/jobs/{ident}/retry')
    async def retry(ident:str):
        job=service.store.get(ident)
        if not job: raise HTTPException(404,'Job not found')
        if job['status'] not in ('failed','cancelled','interrupted'): raise ValueError('Only stopped/failed jobs can be retried')
        payload={k:v for k,v in job['payload'].items() if k!='selection_revision'}
        return await service.submit({'kind':job['kind'],'payload':payload})
    @api.post('/api/approvals/{ident}')
    async def approval(ident:str,body:Decision): service.scheduler.resolve(ident,body.allow); return {'ok':True}
    @api.get('/api/models/search')
    async def search(q:str=Query('',max_length=120)): return await service.catalog.search(q)
    @api.get('/api/models/files')
    async def files(repo_id:str=Query(max_length=160),revision:str=Query('main',max_length=100)):
        return await service.catalog.files(repo_id,revision)
    @api.get('/api/mcp/search')
    async def mcp_search(q:str=Query('',max_length=120),cursor:str|None=Query(None,max_length=512)):
        return await service.mcp.search(q,cursor)
    @api.get('/api/mcp/package')
    async def package(registry:str=Query(max_length=10),package:str=Query(max_length=200)):
        return await service.mcp.package_info(registry,package)
    @api.get('/api/mcp/inventory/{ident}')
    async def inventory(ident:str): return service.store.object('mcp_inventory',ident,{'tools':[],'servers':[]})
    @api.post('/api/mcp/remote')
    async def remote(body:MCPRemote): return service.mcp.remote(body.model_dump())
    @api.post('/api/mcp/{ident}/enabled')
    async def enable(ident:str,body:Enable): return service.mcp.enable(ident,body.enabled)
    @sio.event
    async def connect(sid,environ,auth_data=None):
        headers={k[5:].lower().replace('_','-'):v for k,v in environ.items() if k.startswith('HTTP_')}
        origin=headers.get('origin')
        if origin and origin not in settings.origins: return False
        if not (auth.authorised(headers) or isinstance(auth_data,dict) and auth.valid_token(auth_data.get('token'))): return False
        await sio.enter_room(sid,'operator')
    @sio.on('sync')
    async def sync(sid,data=None):
        try: after=SyncRequest.model_validate(data or {}).after
        except ValidationError: return {'ok':False,'error':'Invalid cursor'}
        # The snapshot cursor and persisted event log let reconnecting clients
        # reconcile live events and catch up without re-running jobs.
        return {'ok':True,'state':service.snapshot(),'events':service.store.events(after,500)}
    @sio.on('job:submit')
    async def ws_submit(sid,data):
        try: return {'ok':True,'job':await service.submit(Submit.model_validate(data).model_dump())}
        except (ValueError,KeyError,ValidationError) as exc: return {'ok':False,'error':settings.redact(str(exc))}
    @sio.on('job:cancel')
    async def ws_cancel(sid,data):
        try:
            body=SocketCancel.model_validate(data)
            return {'ok':True,'job':await service.scheduler.cancel(body.job_id)}
        except (ValueError,KeyError,ValidationError) as exc: return {'ok':False,'error':str(exc)}
    @sio.on('approval:resolve')
    async def ws_approve(sid,data):
        try:
            body=SocketDecision.model_validate(data); service.scheduler.resolve(body.approval_id,body.allow)
            return {'ok':True}
        except (ValueError,ValidationError) as exc: return {'ok':False,'error':str(exc)}
    wrapped=socketio.ASGIApp(sio,other_asgi_app=BodyLimit(api),socketio_path='socket.io')
    wrapped.api=api; wrapped.service=service; wrapped.sio=sio
    return wrapped

app=create_app()
