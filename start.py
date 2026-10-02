#!/usr/bin/env python3
"""Bootstrap and supervise ModelMaestro. No shell scripts or root required."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent

def load_env(path:Path):
    """A deliberately small .env reader: literal values, never command evaluation."""
    if not path.exists(): return
    for n,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'): continue
        if line.startswith('export '): line=line[7:].strip()
        if '=' not in line: raise ValueError(f'{path.name}:{n}: expected NAME=value')
        key,value=line.split('=',1); key=key.strip(); value=value.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key): raise ValueError(f'{path.name}:{n}: invalid variable name')
        if len(value)>1 and value[0]==value[-1] and value[0] in ('"',"'"): value=value[1:-1]
        os.environ.setdefault(key,value)

def fingerprint(paths):
    digest=hashlib.sha256()
    for path in sorted(paths):
        if path.is_file(): digest.update(str(path.relative_to(ROOT)).encode()); digest.update(path.read_bytes())
    return digest.hexdigest()

def run(argv):
    print('+ '+' '.join(map(str,argv)),flush=True)
    subprocess.run(list(map(str,argv)),cwd=ROOT,check=True)

def stop(proc):
    if proc is None: return
    if proc.poll() is None:
        try: os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError: return
        try: proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
    # The app's inference workers have separate process groups. Its shutdown
    # handler reaps those; persisted identities also support crash recovery.

def wait_for_backend(proc,port):
    deadline=time.monotonic()+90
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic()<deadline:
        if proc.poll() is not None: raise RuntimeError('The backend exited during startup. Inspect the error above.')
        try:
            with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:
                if json.load(response).get('service')=='modelmaestro': return
        except (OSError,ValueError): pass
        time.sleep(.25)
    raise RuntimeError('Backend did not become ready. Inspect the logs and port configuration.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev',action='store_true',help='Run Next.js in development mode (backend is not auto-reloaded)')
    parser.add_argument('--demo',action='store_true',help='Use a clearly labelled deterministic echo runtime; do not install LLM runtimes')
    parser.add_argument('--no-install-runtimes',action='store_true',help='Disable automatic first-start ollm, llama.cpp and mcp-cli installation')
    parser.add_argument('--skip-app-install',action='store_true',help='Use already installed app dependencies; do not invoke pip/npm')
    args=parser.parse_args()
    if os.name!='posix' or sys.version_info<(3,11): raise RuntimeError('Use Python 3.11+ on Linux, macOS, or WSL2. Python 3.11/3.12 are recommended for GPU package compatibility.')
    os.chdir(ROOT); load_env(ROOT/'.env')
    os.environ.setdefault('NEXT_TELEMETRY_DISABLED','1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
    os.environ.setdefault('PORT','3000'); os.environ.setdefault('BACKEND_PORT','8765')
    os.environ.setdefault('HOST','127.0.0.1'); os.environ.setdefault('APP_ORIGIN','http://127.0.0.1:'+os.environ['PORT'])
    if args.demo: os.environ.update(DEMO_MODE='1',AUTO_INSTALL='0')
    if args.no_install_runtimes: os.environ['AUTO_INSTALL']='0'
    # Subprocesses inherit a private umask for databases, prompts and logs.
    os.umask(0o077)
    node=shutil.which('node'); npm=shutil.which('npm')
    if not node or not npm: raise RuntimeError('Install Node.js 22+ and npm before starting ModelMaestro.')
    version=subprocess.check_output([node,'--version'],text=True).strip()
    if int(version.lstrip('v').split('.')[0])<22: raise RuntimeError('Node.js 22+ is required; found '+version)
    state=Path(os.getenv('APP_STATE_DIR',str(ROOT/'.state'))).expanduser().resolve()
    os.environ['APP_STATE_DIR']=str(state); state.mkdir(parents=True,exist_ok=True); state.chmod(0o700)
    # Initialise credentials before package installation; never print the token itself.
    from backend.config import Settings
    settings=Settings(state=state)
    venv=state/'backend-venv'; py=venv/'bin'/'python'
    requirement_hash=fingerprint([ROOT/'requirements.txt'])
    stamp=state/'backend-requirements.sha256'
    if not args.skip_app_install:
        if not py.exists(): run([sys.executable,'-m','venv',venv])
        if not stamp.exists() or stamp.read_text()!=requirement_hash:
            run([py,'-m','pip','install','--upgrade','pip','wheel','setuptools'])
            run([py,'-m','pip','install','-r',ROOT/'requirements.txt'])
            stamp.write_text(requirement_hash)
        frontend_hash=fingerprint([ROOT/'package.json'])
        npm_stamp=state/'frontend-package.sha256'
        if not (ROOT/'node_modules'/'next'/'package.json').is_file() or not npm_stamp.exists() or npm_stamp.read_text()!=frontend_hash:
            run([npm,'ci' if (ROOT/'package-lock.json').exists() else 'install','--no-audit','--no-fund'])
            npm_stamp.write_text(frontend_hash)
    if not py.exists(): raise RuntimeError('Backend virtualenv is missing; omit --skip-app-install on first launch.')
    if not args.dev:
        paths=[ROOT/'package.json',ROOT/'next.config.mjs',ROOT/'tsconfig.json']
        for directory in ('app','components','lib'): paths.extend((ROOT/directory).rglob('*'))
        build_hash=fingerprint(paths); build_stamp=state/'frontend-build.sha256'
        if not (ROOT/'.next'/'BUILD_ID').exists() or not build_stamp.exists() or build_stamp.read_text()!=build_hash:
            run([npm,'run','build']); build_stamp.write_text(build_hash)
    if not os.getenv('APP_TOKEN'):
        import shlex
        print('\nAccess token: read this private file and paste its contents into the login form:\n  cat '+shlex.quote(str(state/'access-token')),flush=True)
    else: print('\nSign in with the APP_TOKEN supplied in your environment.',flush=True)
    print('Workspace: '+settings.origin,flush=True)
    if os.environ.get('AUTO_INSTALL','1')=='1':
        print('First-start runtime installations will appear as background jobs in Runtimes / Tasks.',flush=True)
    children=[]
    def requested_stop(signum,frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,requested_stop)
    try:
        # Check the private port before spawning: never accidentally proxy to a
        # second console with a different state directory and token.
        import socket
        with socket.socket() as test: test.bind(('127.0.0.1',int(os.environ['BACKEND_PORT'])))
        backend=subprocess.Popen([str(py),'-m','uvicorn','backend.app:app','--host','127.0.0.1',
            '--port',os.environ['BACKEND_PORT'],'--workers','1','--no-access-log'],cwd=ROOT,start_new_session=True)
        children.append(backend); wait_for_backend(backend,int(os.environ['BACKEND_PORT']))
        frontend=subprocess.Popen([node,'server.mjs',*(['--dev'] if args.dev else [])],cwd=ROOT,start_new_session=True)
        children.append(frontend)
        while all(p.poll() is None for p in children): time.sleep(.5)
        failed=next(p for p in children if p.poll() is not None)
        if failed.returncode: raise RuntimeError(f'A ModelMaestro service exited with status {failed.returncode}.')
    except KeyboardInterrupt: print('\nStopping frontend and background workers…',flush=True)
    finally:
        signal.signal(signal.SIGINT,signal.SIG_IGN); signal.signal(signal.SIGTERM,signal.SIG_IGN)
        for proc in reversed(children): stop(proc)

if __name__=='__main__':
    try: main()
    except (RuntimeError,ValueError,OSError,subprocess.CalledProcessError) as exc:
        print('Startup failed: '+str(exc),file=sys.stderr); sys.exit(1)
