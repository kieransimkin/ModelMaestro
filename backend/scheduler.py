from __future__ import annotations
import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from .store import Store, ACTIVE

class JobContext:
    def __init__(self, scheduler: 'Scheduler', job: dict):
        self.scheduler, self.job = scheduler, job
        self.id = job['id']
    async def emit(self, kind: str, data: dict):
        return await self.scheduler.emit(self.id, kind, data)
    async def log(self, text: str):
        text = self.scheduler.redact(text)
        if text.strip(): await self.emit('job.log', {'text': text[-16384:]})
    async def progress(self, value: float, message: str = ''):
        job = self.scheduler.store.change(self.id, progress=max(0,min(1,value)))
        await self.emit('job.progress', {'progress':job['progress'],'message':message})
    async def output(self, **values):
        output = self.scheduler.store.get(self.id)['output']
        output.update(values)
        self.scheduler.store.change(self.id, output=output)
    async def delta(self, text: str, *, channel='content'):
        if not text: return
        output = self.scheduler.store.get(self.id)['output']
        old = output.get(channel, '')
        if len(old) + len(text) > 2_000_000:
            raise ValueError('Generation output exceeded the 2 MB per-channel limit')
        output[channel] = old + text
        self.scheduler.store.change(self.id, output=output)
        await self.emit('chat.delta', {'text':text, 'channel':channel, 'offset':len(old), 'round':output.get('round',0)})
    async def approve(self, tool: str, arguments: dict, *, timeout=600):
        ident = uuid.uuid4().hex
        approval = {'id':ident, 'job_id':self.id, 'tool':tool, 'arguments':arguments,
                    'status':'pending','created':time.time(),'expires':time.time()+timeout}
        self.scheduler.store.put('approvals',ident,approval)
        future = asyncio.get_running_loop().create_future()
        self.scheduler.approvals[ident] = future
        self.scheduler.store.change(self.id,status='waiting_approval')
        await self.emit('tool.approval',approval)
        try:
            decision = await asyncio.wait_for(future,timeout)
            approval['status'] = 'approved' if decision else 'denied'
            return decision
        except TimeoutError:
            approval['status'] = 'expired'
            return False
        except asyncio.CancelledError:
            approval['status'] = 'cancelled'
            raise
        finally:
            self.scheduler.approvals.pop(ident,None)
            self.scheduler.store.put('approvals',ident,approval)
            if self.scheduler.store.get(self.id)['status'] == 'waiting_approval':
                self.scheduler.store.change(self.id,status='running')
            await self.emit('tool.approval_resolved',approval)

class Scheduler:
    LANES = {'inference':1, 'download':2, 'maintenance':1}
    def __init__(self, store: Store, publish: Callable[[dict], Awaitable[None]], redact=lambda x:x):
        self.store, self.publish, self.redact = store, publish, redact
        self.handlers: dict[str,tuple[str,Callable,float]] = {}
        self.running: dict[str,asyncio.Task] = {}
        self.approvals: dict[str,asyncio.Future] = {}
        self.cancelling: set[str] = set()
        self.workers: list[asyncio.Task] = []
        self.stopping = False
    def register(self, kind, lane, handler, timeout=7200):
        if lane not in self.LANES: raise ValueError('Invalid lane')
        self.handlers[kind] = lane,handler,timeout
    async def emit(self, ident, kind, data):
        event = self.store.event(ident,kind,data)
        # Persistence is authoritative; a failed live delivery must not fail a job.
        try: await self.publish(event)
        except Exception: pass
        return event
    async def submit(self, kind, payload, *, priority=0, run_after=0, request_key=None):
        if kind not in self.handlers: raise ValueError('Unknown job kind')
        if len(self.store.active()) >= 2048: raise ValueError('Job queue is full')
        lane = self.handlers[kind][0]
        job = self.store.create(kind,lane,payload,priority=priority,run_after=run_after,request_key=request_key)
        await self.emit(job['id'],'job.updated',{'job':job})
        return job
    async def cancel(self, ident):
        job = self.store.get(ident)
        if not job: raise KeyError('Job not found')
        if job['status'] not in ACTIVE: return job
        task = self.running.get(ident)
        if task:
            if ident not in self.cancelling:
                self.cancelling.add(ident); task.cancel()
            # Cleanup/reaping finishes before the next job in this lane starts.
        else:
            job = self.store.change(ident,status='cancelled',error='Cancelled by operator')
            await self.emit(ident,'job.updated',{'job':job})
        return self.store.get(ident)
    def resolve(self, ident: str, allow: bool):
        future = self.approvals.get(ident)
        if not future or future.done(): raise ValueError('Approval is no longer pending')
        future.set_result(allow)
    async def start(self):
        for ident in self.store.recover():
            await self.emit(ident,'job.updated',{'job':self.store.get(ident)})
        for a in self.store.objects('approvals'):
            if a['status']=='pending':
                a['status']='interrupted'; self.store.put('approvals',a['id'],a)
        self.stopping=False
        for lane,count in self.LANES.items():
            for i in range(count): self.workers.append(asyncio.create_task(self._worker(lane),name=f'{lane}-{i}'))
    async def _worker(self,lane):
        while not self.stopping:
            job = self.store.claim(lane)
            if not job:
                await asyncio.sleep(.2); continue
            ctx = JobContext(self,job)
            _, handler, timeout = self.handlers[job['kind']]
            task = asyncio.create_task(handler(ctx,job['payload']),name=job['id'])
            self.running[job['id']]=task
            await self.emit(job['id'],'job.updated',{'job':job})
            try:
                result = await asyncio.wait_for(task,timeout)
                final=self.store.change(job['id'],status='completed',progress=1,result=result)
            except asyncio.CancelledError:
                final=self.store.change(job['id'],status='interrupted' if self.stopping else 'cancelled',
                    error='Server stopped' if self.stopping else 'Cancelled by operator')
            except TimeoutError:
                final=self.store.change(job['id'],status='failed',error='Job deadline exceeded; subprocesses stopped')
            except Exception as exc:
                error=self.redact(f'{type(exc).__name__}: {exc}')[-8000:]
                final=self.store.change(job['id'],status='failed',error=error)
                await ctx.log(error)
            finally:
                self.running.pop(job['id'],None)
                self.cancelling.discard(job['id'])
            await self.emit(job['id'],'job.updated',{'job':final})
    async def stop(self):
        self.stopping=True
        for ident,task in list(self.running.items()):
            if ident not in self.cancelling:
                self.cancelling.add(ident); task.cancel()
        # Idle workers wake promptly. Running workers own cancellation/cleanup.
        if self.workers: await asyncio.gather(*self.workers,return_exceptions=True)
        self.workers.clear()
