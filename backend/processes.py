from __future__ import annotations
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

_registry = None

def configure_registry(store):
    global _registry
    _registry = store

async def recover_processes(store):
    """Reap recorded live child identities after an unclean control-plane exit."""
    import psutil
    pending=[]
    for row in store.objects("processes"):
        try:
            proc=psutil.Process(row["pid"])
            if abs(proc.create_time()-row["created"])<.02:
                os.killpg(proc.pid,signal.SIGKILL)
                pending.append(proc)
        except (psutil.NoSuchProcess,ProcessLookupError): pass
        store.delete("processes",str(row["pid"]))
    if pending:
        await asyncio.to_thread(psutil.wait_procs,pending,timeout=3)

async def terminate(proc, grace=5):
    """Kill and reap the entire owned process group, not just its shell/parent."""
    if proc is None: return
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            if os.name=='posix': os.killpg(proc.pid,signal.SIGTERM)
            else: proc.terminate()
        try: await asyncio.wait_for(proc.wait(),grace)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                if os.name=='posix': os.killpg(proc.pid,signal.SIGKILL)
                else: proc.kill()
            await proc.wait()
    if os.name=='posix':
        # The group leader may have exited before a stubborn descendant.
        with contextlib.suppress(ProcessLookupError): os.killpg(proc.pid,signal.SIGKILL)
    if _registry is not None:
        _registry.delete('processes',str(proc.pid))

async def spawn(argv, *, cwd=None, env=None, stdin=asyncio.subprocess.DEVNULL, merge=False):
    if not argv or not all(isinstance(x,str) and '\x00' not in x for x in argv):
        raise ValueError('Invalid subprocess argument vector')
    proc = await asyncio.create_subprocess_exec(*argv,cwd=cwd,env=env,stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT if merge else asyncio.subprocess.PIPE,
        start_new_session=os.name=='posix',limit=4*1024*1024)
    if _registry is not None:
        import psutil
        try:
            _registry.put('processes',str(proc.pid),{'pid':proc.pid,'created':psutil.Process(proc.pid).create_time()})
        except psutil.NoSuchProcess: pass
    return proc

async def drain(stream, callback):
    buffer=''
    while chunk:=await stream.read(4096):
        buffer += chunk.decode('utf-8',errors='replace').replace('\r','\n')
        while '\n' in buffer:
            line,buffer=buffer.split('\n',1)
            if line: await callback(line)
        if len(buffer)>8192:
            await callback(buffer[:8192]); buffer=buffer[8192:]
    if buffer: await callback(buffer)

async def run_logged(argv, ctx, *, cwd=None, env=None, timeout=7200):
    # Deliberately no shell=True and no model-generated command strings.
    await ctx.log('Running: ' + ' '.join(argv))
    proc=await spawn(argv,cwd=cwd,env=env,merge=True)
    pump=asyncio.create_task(drain(proc.stdout,ctx.log))
    try:
        async with asyncio.timeout(timeout):
            await proc.wait(); await pump
        if proc.returncode:
            raise RuntimeError(f'{Path(argv[0]).name} exited with status {proc.returncode}; see job log')
    finally:
        await terminate(proc)
        if not pump.done(): pump.cancel()
        await asyncio.gather(pump,return_exceptions=True)

def venv_python(path:Path)->Path:
    return path / ('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def clean_env(home:Path, extras:dict|None=None):
    # Tool processes do not inherit host credentials by default.
    env={k:v for k,v in os.environ.items() if k in {
        'PATH','SYSTEMROOT','WINDIR','LANG','LC_ALL','TMPDIR','TEMP','TMP',
        'CUDA_VISIBLE_DEVICES','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'}}
    home.mkdir(parents=True,exist_ok=True)
    env.update({'HOME':str(home),'USERPROFILE':str(home),'PYTHONUNBUFFERED':'1',
                'PYTHONNOUSERSITE':'1','NO_COLOR':'1','TERM':'dumb'})
    env.update(extras or {})
    return env
