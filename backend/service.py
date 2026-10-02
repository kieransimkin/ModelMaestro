from __future__ import annotations
import asyncio
import contextlib
import json
import time
import uuid
from .catalog import Catalog
from .engines import ModelManager
from .mcp_tools import MCPService,MCPHub,bounded_json
from .runtimes import Runtimes
from .scheduler import Scheduler
from .schemas import validated
from .store import Store,ACTIVE

class Service:
    def __init__(self,settings,publish):
        self.settings=settings
        self.store=Store(settings.state/'console.sqlite3')
        self.scheduler=Scheduler(self.store,publish,settings.redact)
        self.runtimes=Runtimes(settings,self.store)
        self.catalog=Catalog(settings,self.store,self.runtimes)
        self.models=ModelManager(settings,self.store,self.runtimes,self.catalog,self.scheduler.emit)
        self.mcp=MCPService(settings,self.store,self.runtimes)
        self.switch_lock=asyncio.Lock()
        for kind,lane,handler,timeout in [
            ('runtime.install','maintenance',self.install_runtime,14400),
            ('model.download','download',self.catalog.download,7*86400),
            ('model.load','inference',self.load_model,14400),
            ('chat','inference',self.chat,7200),
            ('mcp.install','maintenance',self.mcp.install,3600),
            ('mcp.probe','maintenance',self.mcp.probe,180),
            ('mcp.cli','maintenance',self.mcp.cli,180),
        ]: self.scheduler.register(kind,lane,handler,timeout)
    async def start(self):
        from .processes import configure_registry,recover_processes
        await recover_processes(self.store)
        configure_registry(self.store)
        await self.models.reap_previous()
        # Stale turn caches belong only to this service and no worker is alive.
        import shutil
        for path in (self.settings.state/'mcp').glob('cli-*.json'):
            path.unlink(missing_ok=True)
        for directory in (self.settings.state/'cache'/'ollm').glob('turn-*'):
            if directory.is_dir(): shutil.rmtree(directory)
        await self.scheduler.start()
        if self.settings.auto_install:
            for name in ('ollm','llama','mcp-cli'): await self.ensure_runtime(name)
        desired=self.store.object('settings','desired')
        if desired and self.catalog.get(desired['model_id']):
            if not any(j['kind']=='model.load' for j in self.store.active()):
                await self.scheduler.submit('model.load',desired,priority=10)
    async def stop(self):
        await self.scheduler.stop()
        async with self.models.lock: await self.models.unload()
        from .processes import configure_registry
        configure_registry(None)
        self.store.close()
    async def ensure_runtime(self,name):
        if self.runtimes.record(name)['status']=='ready': return
        if any(j['kind']=='runtime.install' and j['payload']['runtime']==name for j in self.store.active()): return
        await self.scheduler.submit('runtime.install',{'runtime':name,'force':False},priority=2)
    async def install_runtime(self,ctx,payload):
        name=payload['runtime']
        if name=='mcp-cli': return await self.runtimes.install(ctx,payload)
        # Updating an active environment may invalidate loaded extensions. The
        # same lock used by inference/model switches serializes this maintenance.
        async with self.models.lock:
            if self.models.active and (name==self.models.active['backend'] or
                (self.models.active['backend']=='ollm' and name in ('flash-attn','flash-linear-attention','kvikio'))):
                await self.models.unload()
            return await self.runtimes.install(ctx,payload)
    async def select(self,payload,request_key=None):
        async with self.switch_lock:
            model=self.catalog.get(payload['model_id'])
            if not model or not model.get('installed'): raise ValueError('Model is not installed')
            if model['backend']!='demo':
                await self.ensure_runtime(model['backend'])
                for name in model.get('modules',[]): await self.ensure_runtime(name)
            revision=uuid.uuid4().hex
            payload={**payload,'selection_revision':revision}
            self.store.put('settings','desired',payload)
            # A selection change cancels old queued work rather than silently
            # answering it with a different model. Reaping precedes the new load.
            for job in self.store.active():
                if job['lane']=='inference': await self.scheduler.cancel(job['id'])
            job=await self.scheduler.submit('model.load',payload,priority=10,request_key=request_key)
            await self.models.state_event()
            return job
    async def load_model(self,ctx,payload):
        desired=self.store.object('settings','desired')
        if desired and payload.get('selection_revision')!=desired.get('selection_revision'):
            raise ValueError('This model selection was superseded')
        model=self.catalog.get(payload['model_id'])
        if not model: raise ValueError('Model no longer exists in the catalogue')
        names=[] if model['backend']=='demo' else [model['backend'],*model.get('modules',[])]
        while names:
            pending=[n for n in names if self.runtimes.record(n)['status']!='ready']
            if not pending: break
            active_installs={j['payload']['runtime'] for j in self.store.active() if j['kind']=='runtime.install'}
            if any(n not in active_installs for n in pending):
                raise ValueError('Required runtime installation failed or is missing: '+', '.join(pending))
            await ctx.progress(.01,'Waiting for runtime installation: '+', '.join(pending))
            await asyncio.sleep(2)
        return await self.models.load(ctx,payload)
    async def submit(self,request):
        kind=request['kind']; payload=validated(kind,request['payload'])
        key=request.get('request_key')
        if key:
            row=self.store.db.execute('SELECT id FROM jobs WHERE request_key=?',(key,)).fetchone()
            if row:
                prior=self.store.get(row['id'])
                original={k:v for k,v in prior['payload'].items() if k!='selection_revision'}
                if prior['kind']!=kind or original!=payload: raise ValueError('Idempotency key belongs to a different request')
                return prior
        if kind=='model.load': return await self.select(payload,key)
        if kind=='chat':
            state=self.models.snapshot(); desired=self.store.object('settings','desired')
            if state['status']!='ready' or not state['active'] or state['active']['id']!=payload['model_id']:
                raise ValueError('Select this model and wait until it is ready before queuing a chat')
            if desired and desired['model_id']!=payload['model_id']: raise ValueError('A model switch is pending')
            if any(j['kind']=='chat' and j['payload']['chat_id']==payload['chat_id'] for j in self.store.active()):
                raise ValueError('This conversation already has a pending turn. Use a new conversation for an independent job.')
            payload['selection_revision']=(desired or {}).get('selection_revision')
            if not self.store.object('chats',payload['chat_id']):
                self.store.put('chats',payload['chat_id'],{'id':payload['chat_id'],'title':payload['prompt'][:64],'created':time.time()})
        if kind=='model.download':
            for job in self.store.active():
                if job['kind']==kind and job['payload']==payload: return job
                if job['kind']==kind and payload['backend']=='ollm' and job['payload'].get('model_id')==payload['model_id']:
                    raise ValueError('A download for this oLLM alias is already active; wait or cancel it before choosing another revision')
        return await self.scheduler.submit(kind,payload,priority=request.get('priority',0),
            run_after=request.get('run_after',0),request_key=key)
    async def chat(self,ctx,payload):
        async with self.models.lock:
            active=self.models.active
            desired=self.store.object('settings','desired') or {}
            if not active or active['id']!=payload['model_id'] or payload.get('selection_revision')!=desired.get('selection_revision'):
                raise ValueError('Model selection changed; this job will not run against another model')
            history=[{'role':'system','content':payload['system']}]
            jobs=sorted(self.store.jobs(limit=1000,chat_id=payload['chat_id']),key=lambda j:j['created'])
            for prior in jobs:
                if prior['id']==ctx.id: break
                if prior['status']=='completed' and prior['result']:
                    history.extend([{'role':'user','content':prior['payload']['prompt']},
                                    {'role':'assistant','content':prior['result'].get('content','')}])
            history.append({'role':'user','content':payload['prompt']})
            if len(json.dumps(history))>2_000_000: raise ValueError('Conversation exceeds the 2 MB history limit; start a new chat')
            servers=[s for s in self.mcp.all() if s['enabled']] if payload['tools'] else []
            try:
                async with MCPHub(self.mcp,servers,ctx) as hub:
                    calls_used=0; usage=[]
                    for round_index in range(8):
                        await ctx.output(round=round_index)
                        answer=await self.models.generate(ctx,history,hub.tools,payload['settings'])
                        usage.append(answer.get('usage',{}))
                        calls=answer.get('tool_calls',[])
                        await ctx.output(content=answer.get('content',''),thinking=answer.get('thinking',''))
                        await ctx.emit('chat.turn',{'round':round_index,'content':answer.get('content',''),
                            'thinking':answer.get('thinking',''),'tool_calls':calls,'finish_reason':answer['finish_reason']})
                        if not calls:
                            result={**answer,'usage_rounds':usage,'tool_calls_used':calls_used,'model_id':active['id']}
                            return result
                        if answer['finish_reason'] in ('length','content_filter'):
                            raise ValueError('Incomplete generation cannot execute tools')
                        calls_used+=len(calls)
                        if calls_used>16: raise ValueError('Maximum 16 tool calls per job exceeded')
                        history.append({'role':'assistant','content':answer.get('content',''),'tool_calls':calls,
                                        'thinking':answer.get('thinking','')})
                        for call in calls:
                            fn=call['function']
                            try:
                                result=await hub.call(fn['name'],fn['arguments'])
                            except Exception as exc:
                                # Do not automatically retry side-effecting tools.
                                result=bounded_json({'isError':True,'error':self.settings.redact(str(exc))})
                                await ctx.emit('tool.error',{'tool':fn['name'],'error':result})
                            history.append({'role':'tool','tool_call_id':call['id'],'name':fn['name'],'content':result})
                    raise ValueError('Agent stopped at the eight-round limit; review tool activity before continuing')
            except asyncio.CancelledError:
                # Covers cancellation while awaiting approval or executing a tool,
                # not just cancellation while tokens are being generated.
                await self.models.unload(); raise
    def snapshot(self):
        import shutil
        import psutil
        memory=psutil.virtual_memory(); disk=shutil.disk_usage(self.settings.state)
        return {'jobs':self.store.jobs(),'models':self.catalog.all(),'model_state':self.models.snapshot(),
                'runtimes':self.runtimes.all(),'mcp_servers':self.mcp.all(),
                'approvals':[a for a in self.store.objects('approvals') if a['status']=='pending'],
                'chats':self.store.objects('chats'),'cursor':self.store.cursor(),'demo':self.settings.demo,
                'system':{'memory_used':memory.used,'memory_total':memory.total,'disk_free':disk.free,'disk_total':disk.total}}
