from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Sampling(Strict):
    max_tokens:int=Field(512,ge=1,le=32768)
    temperature:float=Field(.7,ge=0,le=2)
    top_p:float=Field(.95,gt=0,le=1)
    context:int=Field(8192,ge=256,le=131072)
    thinking:bool=False
    reasoning_effort:Literal['low','medium','high']='low'

class Chat(Strict):
    chat_id:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    prompt:str=Field(min_length=1,max_length=200000)
    model_id:str=Field(min_length=1,max_length=160)
    system:str=Field('You are a helpful assistant.',max_length=16000)
    tools:bool=True
    settings:Sampling=Field(default_factory=Sampling)

class Selection(Strict):
    model_id:str=Field(min_length=1,max_length=160)
    context:int=Field(8192,ge=256,le=131072)
    gpu_layers:int=Field(-1,ge=-1,le=1000)

class Download(Strict):
    backend:Literal['ollm','llama']
    model_id:str|None=Field(None,max_length=160)
    repo_id:str|None=Field(None,max_length=160)
    filename:str|None=Field(None,max_length=240)
    revision:str=Field('main',max_length=100,pattern=r'^[A-Za-z0-9_.\-/]+$')
    @model_validator(mode='after')
    def validate_source(self):
        if self.backend=='ollm' and not self.model_id: raise ValueError('Select an oLLM catalogue entry')
        if self.backend=='llama':
            if not self.repo_id or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',self.repo_id):
                raise ValueError('Expected a Hugging Face owner/repository')
            if not self.filename or not self.filename.lower().endswith('.gguf'):
                raise ValueError('Select a GGUF file')
            if any(x in self.filename for x in ('..','\\','\x00')) or self.filename.startswith('/'):
                raise ValueError('Invalid model filename')
        return self

class InstallRuntime(Strict):
    runtime:Literal['ollm','llama','mcp-cli','flash-attn','flash-linear-attention','kvikio']
    force:bool=False

class MCPInstall(Strict):
    name:str=Field(min_length=1,max_length=40,pattern=r'^[A-Za-z][A-Za-z0-9_-]*$')
    registry:Literal['npm','pypi']
    package:str=Field(min_length=1,max_length=200)
    version:str=Field(min_length=1,max_length=80)
    entrypoint:str=Field('',max_length=100,pattern=r'^[A-Za-z0-9_.-]*$')
    args:list[str]=Field(default_factory=list,max_length=40)
    env_refs:dict[str,str]=Field(default_factory=dict)
    trust_package:bool=False
    allow_scripts:bool=False
    @model_validator(mode='after')
    def check(self):
        pattern = r'(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*' if self.registry=='npm' else r'[A-Za-z0-9][A-Za-z0-9._-]*'
        if not re.fullmatch(pattern,self.package): raise ValueError('Registry package names only; no URLs, paths or shell fragments')
        if not re.fullmatch(r'[0-9][A-Za-z0-9.\-+_]*',self.version): raise ValueError('Pin an exact package version, not latest or a range')
        if self.registry=='npm' and not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?',self.version):
            raise ValueError('npm requires a complete major.minor.patch version; partial versions are ranges')
        if not self.trust_package: raise ValueError('Explicit package-code trust acknowledgement required')
        if self.registry=='pypi' and not self.entrypoint: raise ValueError('PyPI packages need a console-script entrypoint')
        if any(len(a)>4096 or '\x00' in a for a in self.args): raise ValueError('Invalid package arguments')
        for key,value in self.env_refs.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value):
                raise ValueError('Environment mapping must contain variable names, not secret values')
        return self

class MCPRemote(Strict):
    name:str=Field(min_length=1,max_length=40,pattern=r'^[A-Za-z][A-Za-z0-9_-]*$')
    url:str=Field(max_length=2048)
    bearer_env:str|None=Field(None,max_length=100,pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    trust_server:bool=False

class MCPAction(Strict):
    server_id:str=Field(min_length=1,max_length=40)
    action:Literal['tools','resources','ping']='tools'

class Submit(Strict):
    kind:Literal['chat','model.download','model.load','runtime.install','mcp.install','mcp.probe','mcp.cli']
    payload:dict
    priority:int=Field(0,ge=-10,le=10)
    run_after:float=Field(0,ge=0)
    request_key:str|None=Field(None,max_length=128)

PAYLOADS={'chat':Chat,'model.download':Download,'model.load':Selection,
    'runtime.install':InstallRuntime,'mcp.install':MCPInstall,'mcp.probe':MCPAction,'mcp.cli':MCPAction}

def validated(kind,payload):
    return PAYLOADS[kind].model_validate(payload).model_dump()
