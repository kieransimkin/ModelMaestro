from __future__ import annotations
import asyncio
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import time
import uuid
import httpx
from .config import ROOT
from .processes import spawn,terminate,drain
from .tool_format import strict_object

async def sse_json(response):
    """Parse SSE framing, including multi-line data and final frames at EOF."""
    data=[]
    async for line in response.aiter_lines():
        if line=='':
            if data:
                text='\n'.join(data); data=[]
                if text=='[DONE]': return
                yield json.loads(text)
        elif line.startswith('data:'): data.append(line[5:].lstrip(' '))
    if data:
        text='\n'.join(data)
        if text!='[DONE]': yield json.loads(text)

class ModelManager:
    def __init__(self,settings,store,runtimes,catalog,publish):
        self.settings,self.store,self.runtimes,self.catalog,self.publish=settings,store,runtimes,catalog,publish
        self.lock=asyncio.Lock(); self.proc=None; self.pump=None
        self.active=None; self.context=8192; self.base_url=None; self.key=None
        self.status='unloaded'; self.detail='No model loaded'; self.loaded_at=None; self.log_context=None
    def snapshot(self):
        if self.status=='ready' and self.proc is not None and self.proc.returncode is not None:
            self.status='failed'; self.detail='Model process exited. Inspect its load-job log, then reload.'
        return {'status':self.status,'active':self.active,'context':self.context,'detail':self.detail,
                'pid':self.proc.pid if self.proc and self.proc.returncode is None else None,
                'loaded_at':self.loaded_at,'desired':self.store.object('settings','desired')}
    async def state_event(self): await self.publish(None,'model.state',self.snapshot())
    def remember_pid(self):
        import psutil
        if self.proc:
            self.store.put('settings','model_process',{'pid':self.proc.pid,'created':psutil.Process(self.proc.pid).create_time()})
    async def reap_previous(self):
        # On crash recovery, kill ONLY the process identity we launched, never a
        # reused PID. The process lock has already established sole ownership.
        import psutil,signal
        prior=self.store.object('settings','model_process')
        if prior:
            try:
                proc=psutil.Process(prior['pid'])
                if abs(proc.create_time()-prior['created'])<.02:
                    if os.name=='posix': os.killpg(proc.pid,signal.SIGKILL)
                    else:
                        for child in proc.children(recursive=True): child.kill()
                        proc.kill()
            except (psutil.NoSuchProcess,ProcessLookupError): pass
        self.store.delete('settings','model_process')
    async def unload(self):
        self.status='unloading'; await self.state_event()
        await terminate(self.proc)
        if self.pump:
            if not self.pump.done(): self.pump.cancel()
            await asyncio.gather(self.pump,return_exceptions=True)
        self.proc=self.pump=None
        self.active=None; self.base_url=self.key=None; self.loaded_at=None
        self.store.delete('settings','model_process')
        self.status='unloaded'; self.detail='No model loaded'
        await self.state_event()
    async def load(self,ctx,payload):
        async with self.lock:
            model=self.catalog.get(payload['model_id'])
            if not model or not model.get('installed'): raise ValueError('Download this model before selecting it')
            # Always stop and reap the previous process before starting another.
            await self.unload()
            self.status='loading'; self.detail=model['name']; self.context=payload['context']; self.log_context=ctx
            await self.state_event()
            try:
                if model['backend']=='demo':
                    await asyncio.sleep(.15)
                else:
                    runtime=self.runtimes.record(model['backend'])
                    if runtime['status']!='ready': raise ValueError(f'{model["backend"]} runtime is not ready; inspect its installation job')
                    missing=[m for m in model.get('modules',[]) if self.runtimes.record(m)['status']!='ready']
                    if missing: raise ValueError('Install required runtime modules first: '+', '.join(missing))
                    if model['backend']=='ollm': await self._load_ollm(model,ctx)
                    else: await self._load_llama(model,payload,ctx)
                self.active=model; self.status='ready'; self.detail='Ready for queued jobs'; self.loaded_at=time.time()
                await self.state_event(); return self.snapshot()
            except BaseException:
                await self.unload(); raise
    async def _load_ollm(self,model,ctx):
        env=dict(os.environ); env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1')
        self.proc=await spawn([str(self.runtimes.python('ollm')),str(ROOT/'backend/workers/ollm_worker.py')],
            stdin=asyncio.subprocess.PIPE,env=env)
        self.remember_pid()
        async def log(text): await (self.log_context or ctx).log(text)
        self.pump=asyncio.create_task(drain(self.proc.stderr,log))
        config={'model':model,'context':self.context,'cache':str(self.settings.state/'cache'/'ollm')}
        self.proc.stdin.write((json.dumps(config)+'\n').encode()); await self.proc.stdin.drain()
        async with asyncio.timeout(1200):
            event=await self._read_worker()
            if event['type']!='ready': raise RuntimeError(event.get('message','Model worker did not become ready'))
            await ctx.log('oLLM loaded on '+event['device'])
    async def _read_worker(self):
        if not self.proc: raise RuntimeError('No model worker')
        line=await self.proc.stdout.readline()
        if not line: raise RuntimeError('Model process exited; see runtime/job logs')
        return json.loads(line)
    async def _load_llama(self,model,payload,ctx):
        runtime=self.runtimes.record('llama')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0)); port=reservation.getsockname()[1]
        self.key=secrets.token_urlsafe(36)
        self.base_url=f'http://127.0.0.1:{port}'
        alias='console-'+uuid.uuid4().hex
        gpu_layers=payload['gpu_layers'] if runtime.get('accelerator')!='cpu' else 0
        argv=[runtime['executable'],'--model',model['path'],'--host','127.0.0.1','--port',str(port),
              '--ctx-size',str(self.context),'--parallel','1','--n-gpu-layers',str(gpu_layers),'--jinja',
              '--alias',alias,'--api-key',self.key]
        self.proc=await spawn(argv,merge=True)
        self.remember_pid()
        async def log(text): await (self.log_context or ctx).log(text.replace(self.key or '', '[PRIVATE SERVER KEY]') if self.key else text)
        self.pump=asyncio.create_task(drain(self.proc.stdout,log))
        async with httpx.AsyncClient(timeout=5,headers={'Authorization':'Bearer '+self.key}) as client:
            async with asyncio.timeout(1200):
                while True:
                    if self.proc.returncode is not None: raise RuntimeError('llama-server exited while loading; inspect its log')
                    try:
                        r=await client.get(self.base_url+'/v1/models')
                        if r.status_code==200 and any(m['id']==alias for m in r.json().get('data',[])): break
                    except (httpx.HTTPError,ValueError): pass
                    await asyncio.sleep(.5)
        self.server_alias=alias
    async def generate(self,ctx,messages,tools,settings):
        self.log_context=ctx
        if not self.active or self.status!='ready': raise ValueError('No model is ready')
        if settings['context']>self.context: raise ValueError('Requested context exceeds the loaded context; reload the model with a larger setting')
        await ctx.output(raw='',thinking='',round=ctx.scheduler.store.get(ctx.id)['output'].get('round',0))
        await ctx.emit('chat.round',{'round':ctx.scheduler.store.get(ctx.id)['output'].get('round',0)})
        try:
            if self.active['backend']=='demo':
                text='[DEMO — not an LLM] '+messages[-1]['content']
                for token in text.split(' '): await ctx.delta(token+' ',channel='raw'); await asyncio.sleep(.025)
                return {'content':text,'thinking':'','tool_calls':[],'finish_reason':'stop','usage':{'demo':True}}
            if self.active['backend']=='ollm':
                self.proc.stdin.write((json.dumps({'messages':messages,'tools':tools,'settings':settings})+'\n').encode())
                await self.proc.stdin.drain()
                while True:
                    event=await self._read_worker()
                    if event['type']=='delta': await ctx.delta(event['text'],channel=event.get('channel','raw'))
                    elif event['type']=='result': return event['result']
                    elif event['type']=='error': raise RuntimeError(event['message'])
            return await self._llama_generate(ctx,messages,tools,settings)
        except BaseException:
            # Cancellation must stop compute, not merely stop forwarding tokens.
            await self.unload(); raise
    async def _llama_generate(self,ctx,messages,tools,settings):
        converted=[]
        for message in messages:
            item=dict(message)
            thinking=item.pop('thinking',None)
            if thinking: item['reasoning_content']=thinking
            if item.get('tool_calls'):
                item['tool_calls']=[{**c,'function':{**c['function'],'arguments':json.dumps(c['function']['arguments'])
                    if isinstance(c['function']['arguments'],dict) else c['function']['arguments']}} for c in item['tool_calls']]
            converted.append(item)
        body={'model':self.server_alias,'messages':converted,'stream':True,
              'max_tokens':settings['max_tokens'],'temperature':settings['temperature'],'top_p':settings['top_p'],
              'stream_options':{'include_usage':True},'cache_prompt':False}
        if tools: body.update(tools=tools,tool_choice='auto')
        calls={}; content=''; thinking=''; finished=None; usage={}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120,read=600),headers={'Authorization':'Bearer '+self.key}) as client:
            async with client.stream('POST',self.base_url+'/v1/chat/completions',json=body) as response:
                if response.status_code!=200:
                    detail=(await response.aread()).decode(errors='replace')[:4000]
                    raise RuntimeError(f'llama-server returned {response.status_code}: {detail}')
                async for chunk in sse_json(response):
                    if chunk.get('error'): raise RuntimeError(str(chunk['error']))
                    if chunk.get('usage'): usage=chunk['usage']
                    for choice in chunk.get('choices',[]):
                        delta=choice.get('delta',{})
                        part=delta.get('content') or ''
                        if part: content+=part; await ctx.delta(part,channel='raw')
                        reasoning=delta.get('reasoning_content') or delta.get('reasoning') or ''
                        if reasoning: thinking+=reasoning; await ctx.delta(reasoning,channel='thinking')
                        for change in delta.get('tool_calls',[]):
                            index=change.get('index',0)
                            target=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                            if change.get('id'): target['id']=change['id']
                            fn=change.get('function',{})
                            if fn.get('name'): target['function']['name']+=fn['name']
                            args=fn.get('arguments','')
                            if isinstance(args,dict): target['function']['arguments']=json.dumps(args)
                            else: target['function']['arguments']+=args
                            if len(target['function']['arguments'])>65536: raise ValueError('Tool arguments exceeded limit')
                        if choice.get('finish_reason'): finished=choice['finish_reason']
        if finished is None: raise RuntimeError('llama-server stream ended without a finish reason')
        complete_calls=[]
        if calls and finished not in ('length','content_filter'):
            for _,call in sorted(calls.items()):
                if not call['id']: call['id']='call_'+uuid.uuid4().hex
                call['function']['arguments']=strict_object(call['function']['arguments'])
                complete_calls.append(call)
        return {'content':content,'thinking':thinking,'tool_calls':complete_calls,'finish_reason':finished,'usage':usage}
