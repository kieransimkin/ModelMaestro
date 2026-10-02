from __future__ import annotations
import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.parse import quote,urlsplit
import httpx
import jsonschema
from .config import atomic_json,within
from .processes import run_logged,venv_python,clean_env


def alias(server,name):
    raw=server+'__'+name
    safe=re.sub(r'[^A-Za-z0-9_]','_',raw)
    return safe if raw==safe and len(safe)<=64 else safe[:47]+'_'+hashlib.sha256(raw.encode()).hexdigest()[:16]

def bounded_json(value,limit=32000):
    raw=json.dumps(value,ensure_ascii=False,default=str)
    if len(raw)<=limit: return raw
    # The wrapper itself remains JSON even when nested strings are escaped.
    size=limit//2
    result=json.dumps({'truncated':True,'original_chars':len(raw),'preview':raw[:size]})
    while len(result)>limit:
        size//=2; result=json.dumps({'truncated':True,'preview':raw[:size]})
    return result

def validate_remote(url):
    parsed=urlsplit(url)
    hosts={h.strip().lower() for h in os.getenv('MCP_ALLOWED_HOSTS','127.0.0.1,localhost,::1').split(',') if h.strip()}
    if parsed.hostname not in hosts:
        raise ValueError('Add this endpoint hostname to MCP_ALLOWED_HOSTS in .env before connecting')
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Invalid MCP endpoint; URL credentials and fragments are forbidden')
    if parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Non-loopback MCP endpoints require HTTPS')
    return parsed

class MCPService:
    def __init__(self,settings,store,runtimes):
        self.settings,self.store,self.runtimes=settings,store,runtimes
        self.cache={}
    def all(self): return self.store.objects('mcp')
    def env(self,server):
        values={}
        for key,ref in server.get('env_refs',{}).items():
            if not os.getenv(ref): raise ValueError(f'Missing host environment variable {ref} for MCP server {server["id"]}')
            values[key]=os.environ[ref]
        return values
    async def search(self,query,cursor=None):
        key=(query,cursor)
        if key in self.cache and time.monotonic()-self.cache[key][0]<3600: return self.cache[key][1]
        params={'search':query,'limit':25,'version':'latest'}
        if cursor: params['cursor']=cursor
        async with httpx.AsyncClient(timeout=30) as client:
            r=await client.get('https://registry.modelcontextprotocol.io/v0.1/servers',params=params)
            r.raise_for_status(); data=r.json()
        result={'servers':[{'server':r.get('server',r)} for r in data.get('servers',[])],
                'metadata':{'nextCursor':data.get('metadata',{}).get('nextCursor')}}
        self.cache[key]=(time.monotonic(),result)
        return result
    async def package_info(self,registry,package):
        # Only registry metadata, never a caller-supplied URL.
        if registry=='npm':
            if not re.fullmatch(r'(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*',package): raise ValueError('Invalid npm name')
            url='https://registry.npmjs.org/'+quote(package,safe='')+'/latest'
        elif registry=='pypi':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',package): raise ValueError('Invalid PyPI name')
            url='https://pypi.org/pypi/'+quote(package,safe='')+'/json'
        else: raise ValueError('Registry must be npm or pypi')
        async with httpx.AsyncClient(timeout=30) as client:
            response=await client.get(url); response.raise_for_status(); data=response.json()
        if registry=='npm': return {'package':data['name'],'version':data['version'],'entrypoints':list(data.get('bin',{})) if isinstance(data.get('bin'),dict) else []}
        return {'package':data['info']['name'],'version':data['info']['version'],'entrypoints':[]}
    async def install(self,ctx,payload):
        name=payload['name']
        if self.store.object('mcp',name): raise ValueError('An MCP server with this name is already registered; use a distinct name')
        root=self.settings.state/'mcp'/name
        root.mkdir(parents=True,exist_ok=True)
        registry=payload['registry']; package=payload['package']; version=payload['version']
        if registry=='npm':
            npm=shutil.which('npm')
            if not npm: raise ValueError('npm is required to install npm MCP servers')
            atomic_json(root/'package.json',{'private':True,'name':'modelmaestro-mcp-'+name.lower(),'version':'1.0.0'})
            cmd=[npm,'install','--prefix',str(root),'--save-exact','--no-audit','--no-fund']
            if not payload['allow_scripts']: cmd+=['--ignore-scripts']
            cmd += [package+'@'+version]
            await run_logged(cmd,ctx)
            metadata=json.loads((root/'node_modules'/package/'package.json').read_text())
            bins=metadata.get('bin',{})
            if isinstance(bins,str): bins={package.split('/')[-1]:bins}
            entry=payload['entrypoint'] or (next(iter(bins)) if len(bins)==1 else '')
            if entry not in bins: raise ValueError('Choose a package entrypoint: '+', '.join(bins))
            script=within(root/'node_modules'/package,bins[entry])
            if not script.is_file(): raise ValueError('Package entrypoint is missing')
            # npm MCP packages conventionally expose Node scripts; .bin symlinks
            # are not used, so startup cannot re-resolve/install a newer package.
            command=shutil.which('node')
            args=[str(script),*payload['args']]
            integrity=json.loads((root/'package-lock.json').read_text()).get('packages',{}).get('node_modules/'+package,{}).get('integrity')
        else:
            py=venv_python(root/'venv')
            if not py.exists(): await run_logged([sys.executable,'-m','venv',str(root/'venv')],ctx)
            await run_logged([str(py),'-m','pip','install',package+'=='+version],ctx)
            # Inspect the selected distribution's console_scripts, not every
            # executable in the environment. This cannot select pip/python.
            from .processes import spawn,terminate
            code='import json,importlib.metadata as m; d=m.distribution('+repr(package)+'); print(json.dumps([e.name for e in d.entry_points if e.group=="console_scripts"]))'
            proc=await spawn([str(py),'-c',code],merge=True)
            try:
                out,_=await asyncio.wait_for(proc.communicate(),30)
                entries=json.loads(out)
            finally: await terminate(proc)
            if payload['entrypoint'] not in entries: raise ValueError('Entrypoint not declared by this distribution: '+', '.join(entries))
            command=str(py.parent/payload['entrypoint']); args=payload['args']; integrity=None
        server={'id':name,'name':name,'transport':'stdio','command':command,'args':args,
                'env_refs':payload['env_refs'],'enabled':False,'package':package,'version':version,
                'registry':registry,'integrity':integrity,'root':str(root),'allow_scripts':payload['allow_scripts']}
        self.store.put('mcp',name,server); self.export_config()
        await ctx.emit('mcp.updated',{'server':server})
        return server
    def remote(self,payload):
        if not payload['trust_server']: raise ValueError('Explicit server trust acknowledgement required')
        validate_remote(payload['url'])
        name=payload['name']
        if self.store.object('mcp',name): raise ValueError('Server name already registered')
        server={'id':name,'name':name,'transport':'streamable-http','url':payload['url'],
                'bearer_env':payload.get('bearer_env'),'enabled':False,'env_refs':{}}
        self.store.put('mcp',name,server); self.export_config(); return server
    def enable(self,name,enabled):
        server=self.store.object('mcp',name)
        if not server: raise KeyError('MCP server not found')
        server['enabled']=enabled; self.store.put('mcp',name,server); self.export_config(); return server
    def export_config(self,*,resolved=False,target=None,only=None):
        servers={}
        for s in self.all():
            if only and s['id']!=only: continue
            if not only and not s['enabled']: continue
            if s['transport']=='stdio':
                env=self.env(s) if resolved else {k:'${'+v+'}' for k,v in s.get('env_refs',{}).items()}
                servers[s['id']]={'command':s['command'],'args':s['args'],'env':env}
            else:
                # mcp-cli's HTTP configuration accepts url + headers.
                config={'url':s['url']}
                if s.get('bearer_env'):
                    ref=s['bearer_env']
                    if resolved and not os.getenv(ref): raise ValueError('Missing host environment variable '+ref)
                    config['headers']={'Authorization':'Bearer '+(os.environ[ref] if resolved else '${'+ref+'}')}
                servers[s['id']]=config
        path=target or self.settings.state/'mcp'/'server_config.json'
        atomic_json(path,{'mcpServers':servers})
        return path
    async def cli(self,ctx,payload):
        runtime=self.runtimes.record('mcp-cli')
        if runtime['status']!='ready': raise ValueError('mcp-cli is not installed yet')
        if not self.store.object('mcp',payload['server_id']): raise KeyError('Unknown MCP server')
        path=self.settings.state/'mcp'/('cli-'+ctx.id+'.json')
        try:
            self.export_config(resolved=True,target=path,only=payload['server_id'])
            env=clean_env(self.settings.state/'mcp'/'cli-home')
            await run_logged([runtime['executable'],'--config-file',str(path),'--disable-filesystem',
                payload['action'],'--server',payload['server_id']],ctx,env=env,timeout=120)
            return {'action':payload['action'],'server_id':payload['server_id'],'message':'Output is in the job log'}
        finally: path.unlink(missing_ok=True)
    async def probe(self,ctx,payload):
        server=self.store.object('mcp',payload['server_id'])
        if not server: raise KeyError('Unknown MCP server')
        async with MCPHub(self,[server],ctx) as hub:
            data={'tools':hub.tools,'servers':hub.info}
            await ctx.emit('mcp.discovered',data)
            self.store.put('mcp_inventory',server['id'],data)
            return data

class MCPHub:
    """MCP SDK session contexts stay in the SAME task for their entire lifetime."""
    def __init__(self,service,servers,ctx):
        self.service,self.servers,self.ctx=service,servers,ctx
        self.stack=AsyncExitStack(); self.tools=[]; self.routes={}; self.info=[]
    async def __aenter__(self):
        await self.stack.__aenter__()
        try:
            for server in self.servers: await self._connect(server)
            return self
        except BaseException:
            await self.stack.aclose(); raise
    async def __aexit__(self,*args): return await self.stack.__aexit__(*args)
    async def _pages(self,method,key):
        values=[]; cursor=None; seen=set()
        for _ in range(20):
            page=await method(cursor=cursor) if cursor else await method()
            values.extend(getattr(page,key))
            cursor=getattr(page,'nextCursor',None)
            if not cursor: return values
            if cursor in seen: raise ValueError('MCP server returned a repeated pagination cursor')
            seen.add(cursor)
        raise ValueError('MCP discovery exceeded page limit')
    async def _connect(self,server):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client
        await self.ctx.emit('mcp.connecting',{'server':server['id']})
        if server['transport']=='stdio':
            home=Path(server['root'])/'work'
            env=clean_env(home,self.service.env(server))
            params=StdioServerParameters(command=server['command'],args=server['args'],env=env,cwd=str(home))
            # SDK subprocess stderr can contain secrets. Keep it in a private
            # per-server file, not the browser or shared control-plane logs.
            log=(self.service.settings.state/'logs'/('mcp-'+server['id']+'.log')).open('a')
            os.chmod(log.name,0o600); self.stack.callback(log.close)
            read,write=await self.stack.enter_async_context(stdio_client(params,errlog=log))
        else:
            validate_remote(server['url']); headers={}
            if server.get('bearer_env'):
                ref=server['bearer_env']
                if not os.getenv(ref): raise ValueError('Missing MCP bearer environment variable '+ref)
                headers['Authorization']='Bearer '+os.environ[ref]
            def client_factory(headers=None,timeout=None,auth=None,**kwargs):
                return httpx.AsyncClient(headers=headers,timeout=timeout,auth=auth,follow_redirects=False)
            read,write,_=await self.stack.enter_async_context(streamablehttp_client(server['url'],
                headers=headers,timeout=timedelta(seconds=30),sse_read_timeout=timedelta(seconds=120),
                httpx_client_factory=client_factory))
        session=await self.stack.enter_async_context(ClientSession(read,write,read_timeout_seconds=timedelta(seconds=120)))
        init=await session.initialize()
        native=await self._pages(session.list_tools,'tools') if init.capabilities.tools else []
        for tool in native:
            if len(self.tools)>=128: raise ValueError('Too many tools: limit is 128 across enabled MCP servers')
            schema=tool.inputSchema or {'type':'object','properties':{}}
            raw=json.dumps(schema)
            if len(raw)>32768: raise ValueError('Tool schema too large')
            # Do not fetch remote JSON Schema refs during validation.
            if re.search(r'"\$(?:ref|dynamicRef|recursiveRef)"\s*:\s*"(?!#)',raw): raise ValueError('Remote JSON Schema references are not supported')
            jsonschema.Draft202012Validator.check_schema(schema)
            name=alias(server['id'],tool.name)
            if name in self.routes: raise ValueError('Tool alias collision')
            self.tools.append({'type':'function','function':{'name':name,'description':(tool.description or '')[:4000],'parameters':schema}})
            self.routes[name]=(session,tool.name,schema)
        resources=await self._pages(session.list_resources,'resources') if init.capabilities.resources else []
        prompts=await self._pages(session.list_prompts,'prompts') if init.capabilities.prompts else []
        self.info.append({'id':server['id'],'tools':len(native),
            'resources':[r.model_dump(mode='json') for r in resources[:100]],
            'prompts':[p.model_dump(mode='json') for p in prompts[:100]]})
        await self.ctx.emit('mcp.connected',{'server':server['id'],'tools':len(native)})
    async def call(self,name,args):
        if name not in self.routes: raise ValueError('Unknown or disabled tool: '+name)
        session,remote,schema=self.routes[name]
        jsonschema.Draft202012Validator(schema).validate(args)
        # Every call is approved individually. readOnlyHint/destructiveHint from
        # untrusted package metadata do not confer approval.
        allowed=await self.ctx.approve(name,args)
        if not allowed: return bounded_json({'isError':True,'error':'Operator declined or approval expired'})
        await self.ctx.emit('tool.started',{'tool':name,'arguments':args})
        async def progress(value,total=None,message=None):
            await self.ctx.emit('tool.progress',{'tool':name,'progress':value,'total':total,'message':message})
        result=await session.call_tool(remote,args,progress_callback=progress)
        data=result.model_dump(mode='json',exclude_none=True)
        # The frontend and models are text-only; do not expose executable HTML
        # apps or send unbounded base64 blobs into the inference context.
        for block in data.get('content',[]):
            if block.get('type') in ('image','audio'):
                block.pop('data',None); block['omitted']='Binary media omitted in this text-only console'
            if block.get('type')=='resource': block.get('resource',{}).pop('blob',None)
        text=bounded_json(data)
        await self.ctx.emit('tool.result',{'tool':name,'result':text,'is_error':bool(data.get('isError'))})
        return text
