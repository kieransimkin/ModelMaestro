from __future__ import annotations
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote
import httpx
from .config import ROOT, atomic_json
from .processes import spawn, terminate, drain

SEED={
 'llama3-1B-chat':('unsloth/Llama-3.2-1B-Instruct','generic',[]),
 'llama3-3B-chat':('unsloth/Llama-3.2-3B-Instruct','generic',[]),
 'llama3-8B-chat':('unsloth/Meta-Llama-3.1-8B-Instruct','generic',[]),
 'gpt-oss-20B':('AnuarSh/gpt-oss-20B','harmony',[]),
 'qwen3-next-80B':('Qwen/Qwen3-Next-80B-A3B-Instruct','qwen',['flash-linear-attention']),
 'gemma3-12B':('google/gemma-3-12b-it','generic',[]),
}

def extract_fork_catalog(source: str):
    """Read literal published aliases without importing Torch or executing code."""
    tree=ast.parse(source); mapping={}; supported=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.FunctionDef) and node.name=='hf_download':
            for child in ast.walk(node):
                if isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='urls' for t in child.targets):
                    try: mapping=ast.literal_eval(child.value)
                    except (ValueError,TypeError): pass
        if isinstance(node,ast.FunctionDef) and node.name=='ini_model':
            for child in ast.walk(node):
                if isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='models_list' for t in child.targets):
                    try: supported=set(ast.literal_eval(child.value))
                    except (ValueError,TypeError): pass
    return {k:v for k,v in mapping.items() if k in supported and isinstance(k,str) and isinstance(v,str)}

class Catalog:
    def __init__(self,settings,store,runtimes): self.settings,self.store,self.runtimes=settings,store,runtimes
    def ollm(self):
        entries=dict(SEED)
        src=self.runtimes.root('ollm')/'source'/'src'/'ollm'/'inference.py'
        if src.exists():
            try:
                actual=extract_fork_catalog(src.read_text())
                # Only aliases explicitly supported by the installed code are added.
                for alias,repo in actual.items():
                    if alias.startswith('voxtral'): continue # this console is deliberately text-only
                    old=SEED.get(alias,(repo,'generic',[]))
                    entries[alias]=(repo,old[1],old[2])
                if actual: entries={k:v for k,v in entries.items() if k in actual}
            except (OSError,SyntaxError): pass
        result=[]
        for alias,(repo,adapter,modules) in entries.items():
            ident='ollm:'+alias
            installed=self.store.object('models',ident)
            result.append({'id':ident,'backend':'ollm','alias':alias,'name':alias,'repo_id':repo,
                'adapter':adapter,'modules':modules,'installed':bool(installed and Path(installed['path']).is_dir()),
                'tool_mode':'native' if adapter!='generic' else 'strict JSON fallback', **(installed or {}),
                'installed':bool(installed and Path(installed['path']).is_dir())})
        return result
    def all(self):
        rows=self.ollm()+[{**r,'installed':Path(r.get('path','')).is_file()}
            for r in self.store.objects('models') if r['backend']=='llama']
        if self.settings.demo:
            rows += [{'id':'demo:echo','backend':'demo','name':'Demo · deterministic echo (not an LLM)',
                      'installed':True,'tool_mode':'test only','modules':[]}]
        return rows
    def get(self,ident):
        return next((m for m in self.all() if m['id']==ident),None)
    async def search(self,query):
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.get('https://huggingface.co/api/models',params={
                'search':query,'filter':'gguf','sort':'downloads','direction':-1,'limit':30})
            r.raise_for_status()
            return [{'id':m['id'],'downloads':m.get('downloads',0),'likes':m.get('likes',0),
                'gated':m.get('gated',False)} for m in r.json()]
    async def files(self,repo,revision='main'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('Invalid repository ID')
        import os
        headers={'Authorization':'Bearer '+os.environ['HF_TOKEN']} if os.getenv('HF_TOKEN') else {}
        async with httpx.AsyncClient(timeout=30) as client:
            url=f'https://huggingface.co/api/models/{quote(repo,safe="/")}/revision/{quote(revision,safe="")}'
            r=await client.get(url,params={'blobs':'true'},headers=headers)
            r.raise_for_status(); body=r.json()
            return {'repo_id':repo,'revision':body['sha'],'files':[
                {'name':f['rfilename'],'size':f.get('size',0),'sha256':(f.get('lfs') or {}).get('sha256')}
                for f in body.get('siblings',[]) if f['rfilename'].lower().endswith('.gguf') and 'mmproj' not in f['rfilename'].lower()]}
    async def download(self,ctx,payload):
        if payload['backend']=='ollm':
            entry=self.get(payload['model_id'])
            if not entry or entry['backend']!='ollm': raise ValueError('Unknown oLLM catalogue entry')
            repo=entry['repo_id']; ident=entry['id']
        else:
            repo=payload['repo_id']
            digest=hashlib.sha256(f'{repo}:{payload["filename"]}:{payload["revision"]}'.encode()).hexdigest()[:24]
            ident='llama:'+digest
            entry={'id':ident,'backend':'llama','name':f'{repo.split("/")[-1]} / {payload["filename"]}',
                'repo_id':repo,'filename':payload['filename'],'modules':[],'tool_mode':'llama.cpp native'}
        prior=self.store.object('models',ident)
        if prior and Path(prior['path']).exists():
            await ctx.log('Model already installed; keeping the verified local copy.')
            return prior
        key=hashlib.sha256(ident.encode()).hexdigest()[:32]
        staging=self.settings.state/'models'/('.partial-'+key)
        destination=self.settings.state/'models'/key
        plan={**payload,'repo_id':repo,'staging':str(staging),'destination':str(destination)}
        proc=await spawn([sys.executable,str(ROOT/'backend/workers/download_worker.py')],
            stdin=__import__('asyncio').subprocess.PIPE)
        import asyncio
        pump=asyncio.create_task(drain(proc.stderr,ctx.log))
        manifest=None
        try:
            proc.stdin.write((json.dumps(plan)+'\n').encode()); await proc.stdin.drain(); proc.stdin.close()
            while line:=await proc.stdout.readline():
                try: event=json.loads(line)
                except (json.JSONDecodeError,UnicodeDecodeError):
                    await ctx.log(line.decode(errors='replace')); continue
                if event.get('type')=='progress':
                    await ctx.progress(event['value'],event.get('message',''))
                elif event.get('type')=='manifest': manifest=event['manifest']
                elif event.get('type')=='error': raise RuntimeError(event['message'])
                else: await ctx.log(event.get('message',str(event)))
            await proc.wait(); await pump
            if proc.returncode or not manifest: raise RuntimeError('Download worker failed; see job log')
            localpath=destination/manifest['entry_file'] if entry['backend']=='llama' else destination
            installed={**entry,'path':str(localpath),'installed':True,'revision':manifest['revision'],
                'bytes':manifest['bytes'],'files':manifest['files']}
            self.store.put('models',ident,installed)
            await ctx.emit('model.downloaded',installed)
            return installed
        finally:
            await terminate(proc)
            if not pump.done(): pump.cancel()
            await asyncio.gather(pump,return_exceptions=True)
