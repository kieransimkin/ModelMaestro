from __future__ import annotations
import ast
import asyncio
import json
import os
import platform
import re
import shutil
import sys
import time
import tomllib
from pathlib import Path
import httpx
from .config import atomic_json
from .processes import run_logged, spawn, terminate, venv_python

RUNTIME_NAMES=('ollm','llama','mcp-cli','flash-attn','flash-linear-attention','kvikio')

class Runtimes:
    def __init__(self,settings,store): self.settings,self.store=settings,store
    def root(self,name): return self.settings.state/'runtimes'/name
    def python(self,name): return venv_python(self.root(name)/'venv')
    def record(self,name):
        row=self.store.object('runtimes',name,{'id':name,'status':'missing'})
        if row.get('status')=='ready' and not self.settings.demo:
            path=Path(row.get('executable',''))
            if not path.is_file(): return {**row,'status':'missing','error':'Managed executable is missing'}
        return row
    def all(self): return [self.record(n) for n in RUNTIME_NAMES]
    async def _capture(self,argv,cwd=None):
        p=await spawn(argv,cwd=cwd,merge=True)
        try:
            data,_=await asyncio.wait_for(p.communicate(),120)
            if p.returncode: raise RuntimeError(data.decode(errors='replace')[-4000:])
            return data.decode().strip()
        finally: await terminate(p)
    async def _checkout(self,url,ref,dest,ctx):
        if not shutil.which('git'): raise RuntimeError('Git is required. Install it using your OS package manager.')
        if not re.fullmatch(r'[A-Za-z0-9_.\-/]+',ref) or ref.startswith('-'):
            raise ValueError('Invalid operator-configured Git ref')
        dest.mkdir(parents=True,exist_ok=True)
        if not (dest/'.git').exists():
            await run_logged(['git','init',str(dest)],ctx)
            await run_logged(['git','-C',str(dest),'remote','add','origin',url],ctx)
        await run_logged(['git','-C',str(dest),'remote','set-url','origin',url],ctx)
        await run_logged(['git','-C',str(dest),'fetch','--depth','1','origin',ref],ctx)
        await run_logged(['git','-C',str(dest),'checkout','--detach','--force','FETCH_HEAD'],ctx)
        return await self._capture(['git','-C',str(dest),'rev-parse','HEAD'])
    async def install(self,ctx,payload):
        name=payload['runtime']
        existing=self.record(name)
        if existing['status']=='ready' and not payload.get('force'): return existing
        self.store.put('runtimes',name,{'id':name,'status':'installing','job_id':ctx.id})
        await ctx.emit('runtime.updated',{'id':name,'status':'installing'})
        try:
            if self.settings.demo:
                await asyncio.sleep(.1)
                result={'id':name,'status':'ready','version':'DEMO — no runtime installed','demo':True}
            elif name=='llama': result=await self._llama(ctx)
            elif name=='ollm': result=await self._ollm(ctx)
            elif name=='mcp-cli': result=await self._mcp_cli(ctx)
            else: result=await self._optional(name,ctx)
            result.update(id=name,status='ready',installed=time.time())
            self.store.put('runtimes',name,result)
            atomic_json(self.root(name)/'installation.json',result)
            await ctx.emit('runtime.updated',result)
            return result
        except BaseException as exc:
            record={'id':name,'status':'failed','error':self.settings.redact(str(exc) or 'Installation cancelled'),'job_id':ctx.id}
            self.store.put('runtimes',name,record)
            await ctx.emit('runtime.updated',record)
            raise
    async def _venv(self,name,ctx):
        root=self.root(name); root.mkdir(parents=True,exist_ok=True)
        py=self.python(name)
        if not py.exists(): await run_logged([sys.executable,'-m','venv',str(root/'venv')],ctx)
        await run_logged([str(py),'-m','pip','install','--upgrade','pip','setuptools','wheel','packaging'],ctx)
        return py
    async def _ollm(self,ctx):
        py=await self._venv('ollm',ctx)
        src=self.root('ollm')/'source'
        ref=os.getenv('OLLM_REF','b395639e7397573728c7c4a1f82565ef1cb542c4')
        commit=await self._checkout(os.getenv('OLLM_REPO','https://github.com/kieransimkin/ollm.git'),ref,src,ctx)
        project=tomllib.loads((src/'pyproject.toml').read_text())['project']
        # The inspected fork declares flash-attn mandatory despite a runtime fallback.
        # Install its remaining declared dependencies, then the fork itself without
        # dependency resolution. Optional accelerators have explicit install jobs.
        deps=[]; torch_req=None
        for dep in project.get('dependencies',[]):
            key=re.split(r'[<>=!~\[; ]',dep,1)[0].lower().replace('_','-')
            if key in ('flash-attn','flash-linear-attention'): continue
            if key=='torch':
                torch_req=os.getenv('OLLM_TORCH_REQUIREMENT', 'torch==2.8.0' if commit=='b395639e7397573728c7c4a1f82565ef1cb542c4' else dep)
            else: deps.append(dep)
        if torch_req:
            cmd=[str(py),'-m','pip','install',torch_req]
            if os.getenv('OLLM_TORCH_INDEX_URL'): cmd += ['--index-url',os.environ['OLLM_TORCH_INDEX_URL']]
            await run_logged(cmd,ctx)
        await run_logged([str(py),'-m','pip','install',*deps,'psutil','safetensors','pillow','sentencepiece','protobuf','jsonschema>=4.23,<5','openai-harmony>=0.0.8,<1'],ctx)
        await run_logged([str(py),'-m','pip','install','--no-deps','--no-build-isolation','-e',str(src)],ctx)
        await run_logged([str(py),'-c','from ollm import Inference; print("oLLM import OK")'],ctx)
        freeze=await self._capture([str(py),'-m','pip','freeze'])
        (self.root('ollm')/'requirements.installed.txt').write_text(freeze+'\n')
        return {'executable':str(py),'version':project.get('version'),'commit':commit,'ref':ref,'source':str(src),
                'note':'FlashAttention / linear attention are separately installable modules.'}
    async def _mcp_cli(self,ctx):
        py=await self._venv('mcp-cli',ctx)
        await run_logged([str(py),'-m','pip','install','mcp-cli==0.20.1'],ctx)
        exe=py.parent/('mcp-cli.exe' if os.name=='nt' else 'mcp-cli')
        await run_logged([str(exe),'--help'],ctx,timeout=90)
        version=await self._capture([str(py),'-c','from importlib.metadata import version; print(version("mcp-cli"))'])
        freeze=await self._capture([str(py),'-m','pip','freeze'])
        (self.root('mcp-cli')/'requirements.installed.txt').write_text(freeze+'\n')
        return {'executable':str(exe),'version':version}
    async def _llama(self,ctx):
        for tool in ('cmake','git'):
            if not shutil.which(tool): raise RuntimeError(f'{tool} is required; install OS build prerequisites first')
        if not any(shutil.which(t) for t in ('c++','g++','clang++','cl')):
            raise RuntimeError('A C++ compiler is required (build-essential / Xcode Command Line Tools)')
        ref=os.getenv('LLAMA_REF','latest')
        if ref=='latest':
            async with httpx.AsyncClient(timeout=30) as client:
                response=await client.get('https://api.github.com/repos/ggml-org/llama.cpp/releases/latest')
                response.raise_for_status(); ref=response.json()['tag_name']
        src=self.root('llama')/'source'
        commit=await self._checkout('https://github.com/ggml-org/llama.cpp.git',ref,src,ctx)
        accelerator=os.getenv('LLAMA_ACCELERATOR','auto')
        if accelerator=='auto':
            accelerator='cuda' if shutil.which('nvcc') else ('metal' if platform.system()=='Darwin' and platform.machine()=='arm64' else 'cpu')
        flags={'cpu':[], 'cuda':['-DGGML_CUDA=ON'], 'metal':['-DGGML_METAL=ON'], 'vulkan':['-DGGML_VULKAN=ON']}
        if accelerator not in flags: raise ValueError('LLAMA_ACCELERATOR must be auto/cpu/cuda/metal/vulkan')
        jobs=str(max(1,min(16,int(os.getenv('LLAMA_BUILD_JOBS','2')))))
        build=src/'build'
        await run_logged(['cmake','-S',str(src),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release',
            '-DLLAMA_CURL=OFF','-DGGML_NATIVE=OFF','-DGGML_CUDA=OFF','-DGGML_METAL=OFF',
            '-DGGML_VULKAN=OFF',*flags[accelerator]],ctx)
        await run_logged(['cmake','--build',str(build),'--config','Release','--target','llama-server','-j',jobs],ctx)
        exe=build/'bin'/'llama-server'
        if not exe.exists(): exe=build/'bin'/'Release'/'llama-server.exe'
        if not exe.exists(): raise RuntimeError('Build completed but llama-server executable was not found')
        await run_logged([str(exe),'--version'],ctx,timeout=60)
        return {'executable':str(exe),'version':ref,'ref':ref,'commit':commit,'accelerator':accelerator}
    async def _optional(self,name,ctx):
        if self.record('ollm')['status']!='ready': raise ValueError('Install the oLLM runtime first')
        py=self.python('ollm')
        package={'flash-attn':'flash-attn','flash-linear-attention':'flash-linear-attention','kvikio':'kvikio-cu12'}[name]
        await run_logged([str(py),'-m','pip','install','--no-build-isolation',package],ctx)
        module={'flash-attn':'flash_attn','flash-linear-attention':'fla','kvikio':'kvikio'}[name]
        await run_logged([str(py),'-c',f'import {module}; print("Module import OK")'],ctx)
        freeze=await self._capture([str(py),'-m','pip','freeze'])
        (self.root('ollm')/'requirements.installed.txt').write_text(freeze+'\n')
        return {'executable':str(py),'version':await self._capture([str(py),'-c',f'from importlib.metadata import version; print(version({package!r}))'])}
