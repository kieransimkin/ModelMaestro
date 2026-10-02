"""Cancellable/resumable downloads. Never imports a model or occupies the GPU."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import threading
import time

def emit(kind,**kwargs): print(json.dumps({'type':kind,**kwargs}),flush=True)

def safe_file(root,name):
    rel=PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or '\\' in name: raise ValueError('Unsafe repository filename')
    path=(root/name).resolve()
    if root.resolve() not in path.parents: raise ValueError('File path escapes model directory')
    return path

def select_files(siblings,backend,filename=None):
    by_name={f.rfilename:f for f in siblings}
    if backend=='llama':
        if filename not in by_name: raise ValueError('Selected GGUF file is absent at this revision')
        match=re.match(r'^(.*)-(\d{5})-of-(\d{5})\.gguf$',filename,re.I)
        if match:
            total=int(match[3])
            names=[f'{match[1]}-{i:05d}-of-{total:05d}.gguf' for i in range(1,total+1)]
            if not all(n in by_name for n in names): raise ValueError('Incomplete split-GGUF shard set')
            return [by_name[n] for n in names],names[0]
        return [by_name[filename]],filename
    # Safetensors, configuration/tokenizer assets, and the fork's packed gds export.
    # No remote Python modules, arbitrary pickle model files, or duplicate GGUFs.
    names=[n for n in by_name if n.endswith(('.safetensors','.json','.model','.tiktoken','.txt','.jinja'))
           or n.startswith('gds_export/')]
    if 'config.json' not in names: raise ValueError('Checkpoint has no config.json')
    if not any(n.endswith('.safetensors') for n in names): raise ValueError('No safetensors checkpoint found')
    return [by_name[n] for n in names],''

def main(plan):
    from huggingface_hub import HfApi, snapshot_download
    token=os.getenv('HF_TOKEN') or None
    info=HfApi(token=token).model_info(plan['repo_id'],revision=plan.get('revision','main'),files_metadata=True)
    files,entry=select_files(info.siblings,plan['backend'],plan.get('filename'))
    if plan.get('model_id')=='ollm:gpt-oss-20B' and not any(f.rfilename.startswith('gds_export/') for f in files):
        raise ValueError('This oLLM GPT-OSS checkpoint needs gds_export weights; do not substitute an ordinary HF checkpoint')
    stage=Path(plan['staging']); dest=Path(plan['destination'])
    # A crash between the atomic directory rename and SQLite registration is recoverable.
    ready=dest/'console-manifest.json'
    if ready.exists():
        emit('manifest',manifest=json.loads(ready.read_text())); return
    stage.mkdir(parents=True,exist_ok=True)
    total=sum(f.size or 0 for f in files)
    present=sum(min(safe_file(stage,f.rfilename).stat().st_size,f.size or 0)
                for f in files if safe_file(stage,f.rfilename).exists())
    need=max(0,total-present)+max(256*1024**2,int(total*.03))
    if shutil.disk_usage(stage).free<need: raise ValueError(f'Insufficient free disk: need approximately {need/1024**3:.1f} GiB more including headroom')
    stop=threading.Event()
    def monitor():
        while not stop.wait(.75):
            try:
                # Includes HF's .incomplete files; percentages are bounded until verified.
                downloaded=sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())
                emit('progress',value=min(.89,.89*downloaded/max(1,total)),
                     message=f'Downloading {downloaded/1024**3:.2f} / {total/1024**3:.2f} GiB')
            except OSError: pass
    thread=threading.Thread(target=monitor,daemon=True); thread.start()
    try:
        snapshot_download(repo_id=plan['repo_id'],revision=info.sha,token=token,local_dir=str(stage),
            allow_patterns=[f.rfilename for f in files],max_workers=2)
    finally:
        stop.set(); thread.join()
    manifest_files=[]
    for i,f in enumerate(files):
        path=safe_file(stage,f.rfilename)
        if not path.is_file() or (f.size is not None and path.stat().st_size!=f.size):
            raise ValueError('Downloaded file failed size verification: '+f.rfilename)
        lfs=f.lfs
        expected=(lfs.get('sha256') if isinstance(lfs,dict) else getattr(lfs,'sha256',None)) if lfs else None
        if expected:
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(8*1024*1024),b''): digest.update(chunk)
            if digest.hexdigest()!=expected:
                path.unlink(missing_ok=True)
                raise ValueError('SHA-256 mismatch; invalid file removed: '+f.rfilename)
        manifest_files.append({'name':f.rfilename,'size':path.stat().st_size,'sha256':expected})
        emit('progress',value=.9+.09*(i+1)/len(files),message='Verifying '+f.rfilename)
    if plan['backend']=='llama':
        with safe_file(stage,entry).open('rb') as f:
            if f.read(4)!=b'GGUF': raise ValueError('File has no GGUF magic header')
    manifest={'repo_id':plan['repo_id'],'revision':info.sha,'files':manifest_files,'bytes':total,'entry_file':entry}
    (stage/'console-manifest.json').write_text(json.dumps(manifest,indent=2))
    if dest.exists(): raise ValueError('Destination exists without a complete manifest; inspect it before retrying')
    stage.rename(dest)
    emit('manifest',manifest=manifest)

if __name__=='__main__':
    try: main(json.loads(sys.stdin.readline()))
    except Exception as exc:
        emit('error',message=f'{type(exc).__name__}: {exc}'); sys.exit(1)
