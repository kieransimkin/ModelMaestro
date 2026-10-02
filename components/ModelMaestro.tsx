'use client';
import {useEffect, useRef, useState} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {active, api, bytes, useModelMaestro} from '@/lib/client';
import type {Dict, Job, LiveEvent, Model, Sampling, Snapshot} from '@/lib/types';
import Models from './Models';
import MCP from './MCP';

type Tab = 'Chat' | 'Models' | 'Tasks' | 'MCP tools' | 'Runtimes';
const icons: Record<Tab, string> = {'Chat':'◫','Models':'▦','Tasks':'≋','MCP tools':'⌘','Runtimes':'◈'};
export function Pill({status}: {status: string}) {return <span className={'pill status-'+status}>{status.replaceAll('_',' ')}</span>;}
export function Markdown({text}: {text: string}) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
    a: ({children, href}) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({alt}) => <span className="muted">[External image omitted: {alt}]</span>,
  }}>{text}</ReactMarkdown>;
}
export default function ModelMaestro() {
  const control = useModelMaestro();
  const {state, authenticated, connected, error, setError, refresh, submit, events} = control;
  const [tab,setTab] = useState<Tab>('Chat');
  const [token,setToken] = useState('');
  const [loggingIn,setLoggingIn] = useState(false);
  const [chatId,setChatId] = useState('');
  const [prompt,setPrompt] = useState('');
  const [sending,setSending] = useState(false);
  const [settings,setSettings] = useState<Sampling>({max_tokens:512, temperature:.7, top_p:.95, context:8192, thinking:false, reasoning_effort:'low'});
  const [system,setSystem] = useState('You are a helpful assistant.');
  const [tools,setTools] = useState(true);
  const [schedule,setSchedule] = useState('');
  const [history,setHistory] = useState<Job[]>([]);
  const [selectedJob,setSelectedJob] = useState<Job|null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {setChatId(localStorage.getItem('console-chat') || crypto.randomUUID());}, []);
  useEffect(() => {if(chatId) localStorage.setItem('console-chat',chatId);}, [chatId]);
  useEffect(() => {
    if(!chatId || !authenticated)return;
    let stopped=false; setHistory([]);
    const load=async()=>{try{const rows=await api<Job[]>('/jobs?limit=1000&chat_id='+encodeURIComponent(chatId));if(!stopped)setHistory(rows);}catch(e){if(!stopped)setError(String(e));}};
    void load();const timer=window.setInterval(()=>void load(),5000);
    return()=>{stopped=true;window.clearInterval(timer);};
  },[chatId,authenticated,setError]);
  const merged=new Map(history.map(j=>[j.id,j]));
  for(const j of state?.jobs||[]){const prior=merged.get(j.id);if(!prior||prior.updated<=j.updated)merged.set(j.id,j);}
  const jobs = [...merged.values()].filter(j => j.kind === 'chat' && j.payload.chat_id === chatId).sort((a,b)=>a.created-b.created);
  const pending = jobs.find(active);
  const selected = state?.model_state.active;
  const ready = state?.model_state.status === 'ready' && selected && (!state.model_state.desired || state.model_state.desired.model_id === selected.id);
  useEffect(() => {end.current?.scrollIntoView({behavior:'smooth', block:'end'});}, [jobs.length,jobs.at(-1)?.output.raw]);
  const act = async (fn: () => Promise<unknown>) => {try {setError(''); await fn(); await refresh();} catch (e) {setError(e instanceof Error ? e.message : String(e));}};
  async function send() {
    if(!prompt.trim() || !selected || !ready || sending || pending) return;
    setSending(true);
    try {
      await submit('chat',{chat_id:chatId, prompt:prompt.trim(), model_id:selected.id, system, tools, settings},
        {run_after:schedule ? new Date(schedule).getTime()/1000 : 0});
      setPrompt(''); setSchedule('');
    } catch(e) {setError(e instanceof Error ? e.message : String(e));} finally {setSending(false);}
  }
  if (authenticated === null) return <main className="login"><div className="brand-mark" aria-hidden="true">M</div><h1>Opening ModelMaestro</h1><p className="muted">Connecting to the local control plane…</p>{error && <div className="notice danger">{error}</div>}</main>;
  if (!authenticated) return <main className="login"><div className="login-card">
    <div className="brand"><div className="brand-mark" aria-hidden="true">M</div><div><strong>Model<em>Maestro</em></strong><small>DANCEFLOW · LOCAL AI</small></div></div>
    <h1>Your models.<br/><span className="accent">Your machine.</span></h1><p className="muted">Connect to your private inference workspace.</p>
    <form onSubmit={e => {e.preventDefault(); setLoggingIn(true); void act(async () => {await api('/login',{token}); setToken('');}).finally(()=>setLoggingIn(false));}}>
      <label>Access token<input type="password" autoComplete="current-password" value={token} onChange={e=>setToken(e.target.value)} placeholder="Paste the token from your terminal" required /></label>
      <button className="primary wide" disabled={loggingIn}>{loggingIn?'Connecting…':'Open workbench →'}</button>
    </form><p className="footnote">The launcher prints a token-file command. Credentials stay in an HttpOnly session cookie, not browser storage.</p>{error && <div className="notice danger">{error}</div>}
  </div></main>;
  if (!state) return null;
  const queued = state.jobs.filter(active);
  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark" aria-hidden="true">M</div><div><strong>Model<em>Maestro</em></strong><small>DANCEFLOW · LOCAL AI</small></div></div>
      <div className="nav-caption">WORKSPACE</div>
      <nav>{(Object.keys(icons) as Tab[]).map(t=><button key={t} className={'nav-item '+(tab===t?'chosen':'')} onClick={()=>setTab(t)}><span>{icons[t]}</span>{t}{t==='Tasks'&&queued.length>0&&<b>{queued.length}</b>}</button>)}</nav>
      <div className="sidebar-chats"><div className="nav-caption flex">CONVERSATIONS<button aria-label="New conversation" onClick={()=>{setChatId(crypto.randomUUID());setTab('Chat');}}>＋</button></div>
        {[...state.chats].sort((a,b)=>b.created-a.created).slice(0,16).map(chat=><button key={chat.id} className={'chat-link '+(chatId===chat.id?'chosen':'')} onClick={()=>{setChatId(chat.id);setTab('Chat');}}>{chat.title}</button>)}
        {!state.chats.length&&<p className="footnote">Your conversations will appear here.</p>}
      </div>
      <div className="sidebar-footer"><div className="connection"><span className={'dot '+(connected?'on':'')} />{connected?'Live connection':'Reconnecting…'}</div><p>Single-operator workspace</p><button className="text-button" onClick={()=>void act(async()=>{await api('/logout',{});window.location.reload();})}>Lock workspace ↗</button></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>{tab}</strong></div><div className="top-meta"><span className="mono">{bytes(state.system.disk_free)} free</span><span className="local-badge">● LOCAL FIRST</span></div></header>
      {state.demo&&<div className="demo-banner">DEMO MODE · Deterministic test responses only. No LLM runtimes or model weights are installed.</div>}
      {error&&<div role="alert" className="notice danger global-error"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error">×</button></div>}
      <section className="model-strip"><div className="model-icon">◈</div><div><label htmlFor="current-model">CURRENT MODEL</label><select id="current-model" value={selected?.id || ''} onChange={e=>{if(e.target.value)void act(()=>submit('model.load',{model_id:e.target.value,context:settings.context,gpu_layers:-1}));}}><option value="">Select an installed model</option>{state.models.filter(m=>m.installed).map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></div><Pill status={state.model_state.status}/><div className="model-strip-detail">{selected?.backend || 'No runtime selected'}<span> · </span>{state.model_state.context.toLocaleString()} context</div>{selected&&<button className="secondary small reload-button" onClick={()=>void act(()=>submit('model.load',{model_id:selected.id,context:settings.context,gpu_layers:-1}))}>Reload</button>}<button className="secondary small" onClick={()=>setTab('Models')}>Browse models ↗</button></section>
      {tab==='Chat'&&<div className="chat-layout"><main className="chat-main">
        <div className="section-heading"><div><span className="eyebrow">INFERENCE STUDIO</span><h1>Let’s work on something.</h1></div><button className="secondary" onClick={()=>setChatId(crypto.randomUUID())}>＋ New chat</button></div>
        <div className="messages" aria-live="polite">
          {!jobs.length&&<div className="chat-empty"><div className="orb">✳</div><h2>A conversation, without the cloud.</h2><p>Choose a model and send a message. You can leave this page;<br className="desktop"/> the scheduler keeps your job running.</p><div className="starter-grid">{['Explain a tricky idea','Review a piece of code','Explore my MCP tools'].map((s,i)=><button key={s} onClick={()=>setPrompt(['Explain the trade-offs between memory usage and inference speed for local language models.','Help me review a piece of code. First, ask me to paste it and describe what it should do.','Which tools are available to you? Describe their capabilities without executing any tools.'][i])}>{s}<span>↗</span></button>)}</div></div>}
          {jobs.map(job=><article className="turn" key={job.id}><div className="message-label">YOU <time>{new Date(job.created*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</time></div><div className="user-message">{String(job.payload.prompt)}</div><div className="message-label assistant-label"><span className="mini-logo" aria-hidden="true">M</span> ASSISTANT <Pill status={job.status}/><button className="text-button" onClick={()=>setSelectedJob(job)}>Job details ↗</button></div><div className="assistant-message">
            {job.status==='queued'?<p className="muted">{job.run_after>Date.now()/1000?'Scheduled for '+new Date(job.run_after*1000).toLocaleString():'Waiting for the inference lane…'}</p>:job.status==='completed'?<Markdown text={String(job.result?.content || job.output.content || '')}/>:<><pre className={'stream-text '+(active(job)?'streaming':'')}>{job.output.raw || job.output.content || (job.status==='waiting_approval'?'Waiting for tool approval…':'Preparing response…')}</pre>{job.error&&<div className="notice danger">{job.error}</div>}</>}
            {job.output.thinking&&<details className="reasoning"><summary>Model reasoning</summary><pre>{job.output.thinking}</pre></details>}
          </div></article>)}<div ref={end}/>
        </div>
        <div className="composer-area">
          {state.approvals.filter(a=>jobs.some(j=>j.id===a.job_id)).map(a=><div key={a.id} className="approval"><div><span className="eyebrow">APPROVAL REQUIRED</span><h3>{a.tool}</h3><pre>{JSON.stringify(a.arguments,null,2)}</pre></div><div className="button-row"><button className="secondary" onClick={()=>void act(()=>api('/approvals/'+a.id,{allow:false}))}>Deny</button><button className="primary" onClick={()=>void act(()=>api('/approvals/'+a.id,{allow:true}))}>Approve this call</button></div></div>)}
          <div className="composer"><textarea aria-label="Message" placeholder={ready?'Message your local model…':'Select and load a model to start…'} value={prompt} onChange={e=>setPrompt(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><div className="composer-footer"><label className="check"><input type="checkbox" checked={tools} onChange={e=>setTools(e.target.checked)}/> MCP tools <span className="count">{state.mcp_servers.filter(s=>s.enabled).length}</span></label><span className="footnote">Enter to send · Shift + Enter for a new line</span>{pending?<button className="stop" onClick={()=>void act(()=>api('/jobs/'+pending.id+'/cancel',{}))}>■ Stop job</button>:<button className="primary" disabled={!ready||!prompt.trim()||sending} onClick={()=>void send()}>{sending?'Queuing…':'Send message ↑'}</button>}</div></div>
          <details className="generation-settings"><summary>Generation & scheduling <span>{settings.max_tokens} max tokens · {settings.temperature} temperature</span></summary><div className="form-grid"><label>Max output tokens<input type="number" min={1} max={32768} value={settings.max_tokens} onChange={e=>setSettings({...settings,max_tokens:Number(e.target.value)})}/></label><label>Temperature<input type="number" min={0} max={2} step={.1} value={settings.temperature} onChange={e=>setSettings({...settings,temperature:Number(e.target.value)})}/></label><label>Context on next load<input type="number" min={256} max={131072} value={settings.context} onChange={e=>setSettings({...settings,context:Number(e.target.value)})}/></label><label>Run after (optional)<input type="datetime-local" value={schedule} onChange={e=>setSchedule(e.target.value)}/></label><label>Reasoning effort<select value={settings.reasoning_effort} onChange={e=>setSettings({...settings,reasoning_effort:e.target.value})}><option>low</option><option>medium</option><option>high</option></select></label><label className="check"><input type="checkbox" checked={settings.thinking} onChange={e=>setSettings({...settings,thinking:e.target.checked})}/>Enable Qwen thinking</label><label className="span-2">System instructions<textarea rows={2} value={system} onChange={e=>setSystem(e.target.value)}/></label></div><p className="footnote">Context changes require reloading the model. Thinking controls apply to supported oLLM adapters; llama.cpp follows the model’s chat template.</p></details>
          <p className="composer-note">Model output can be wrong. Review tool calls before approving access to files or services.</p>
        </div>
      </main><Activity state={state} events={events} onJob={setSelectedJob}/></div>}
      {tab==='Models'&&<Models state={state} submit={submit} report={setError} refresh={refresh} context={settings.context}/>}
      {tab==='MCP tools'&&<MCP state={state} submit={submit} report={setError} refresh={refresh}/>}
      {tab==='Tasks'&&<main className="page-content"><div className="section-heading"><div><span className="eyebrow">PERSISTENT SCHEDULER</span><h1>Work, in progress.</h1><p className="muted">One inference lane. Two download workers. Independent maintenance.</p></div><span className="stat-number">{queued.length}<small>active jobs</small></span></div>
        {state.approvals.map(a=><div className="approval" key={a.id}><div><span className="eyebrow">TOOL APPROVAL</span><h3>{a.tool}</h3><pre>{JSON.stringify(a.arguments,null,2)}</pre></div><div className="button-row"><button onClick={()=>void act(()=>api('/approvals/'+a.id,{allow:false}))}>Deny</button><button className="primary" onClick={()=>void act(()=>api('/approvals/'+a.id,{allow:true}))}>Approve once</button></div></div>)}
        <div className="table-wrap"><table><thead><tr><th>Task</th><th>Lane</th><th>Status</th><th>Progress</th><th>Created</th><th/></tr></thead><tbody>{state.jobs.map(job=><tr key={job.id}><td><button className="text-button task-name" onClick={()=>setSelectedJob(job)}>{job.kind}<small>{job.id.slice(0,12)}</small></button></td><td>{job.lane}</td><td><Pill status={job.status}/></td><td><progress value={job.progress} max={1}/><span className="footnote"> {Math.round(job.progress*100)}%</span></td><td className="footnote">{new Date(job.created*1000).toLocaleString()}</td><td>{active(job)?<button className="secondary small" onClick={()=>void act(()=>api('/jobs/'+job.id+'/cancel',{}))}>Cancel</button>:['failed','cancelled','interrupted'].includes(job.status)&&<button className="secondary small" onClick={()=>{if(window.confirm('Retry as a new job? Any previous tool side effects may be repeated.'))void act(()=>api('/jobs/'+job.id+'/retry',{}));}}>Retry</button>}</td></tr>)}</tbody></table>{!state.jobs.length&&<div className="empty-panel">No jobs yet.</div>}</div>
        <p className="footnote">Closing this tab does not cancel a job. Server restarts preserve queued jobs and mark interrupted work for review rather than repeating tool side effects.</p>
      </main>}
      {tab==='Runtimes'&&<main className="page-content"><div className="section-heading"><div><span className="eyebrow">MANAGED INFRASTRUCTURE</span><h1>The engines underneath.</h1><p className="muted">Isolated environments. Recorded revisions. No administrator privileges.</p></div></div><div className="notice">oLLM, llama.cpp and mcp-cli install automatically on first startup. Optional GPU modules are separate jobs; drivers and system build tools remain host prerequisites.</div><div className="runtime-grid">{state.runtimes.map(runtime=><article className="runtime-card" key={runtime.id}><div className="flex"><div className="runtime-symbol">◈</div><Pill status={runtime.status}/></div><h2>{runtime.id}</h2><p className="muted">{({ollm:'Your fork, with SSD weight streaming and disk-backed KV caches.',llama:'Native llama-server for GGUF models and streaming tool calls.','mcp-cli':'Isolated CLI with access to your configured MCP servers.','flash-attn':'Optional GPU attention kernels. Needs compatible CUDA and a compiler.','flash-linear-attention':'Linear attention kernels required by the inspected Qwen3-Next backend.',kvikio:'Optional CUDA 12 accelerated disk I/O for oLLM.'} as Record<string,string>)[runtime.id]}</p>{runtime.version&&<div className="mono runtime-version">{runtime.version}{runtime.accelerator&&' · '+runtime.accelerator}</div>}{runtime.commit&&<div className="footnote mono">commit {runtime.commit.slice(0,12)}</div>}{runtime.error&&<div className="notice danger">{runtime.error}</div>}<button className="secondary wide" disabled={runtime.status==='installing'} onClick={()=>void act(()=>submit('runtime.install',{runtime:runtime.id,force:runtime.status==='ready'}))}>{runtime.status==='installing'?'Installation queued / running…':runtime.status==='ready'?'Reinstall / update pinned runtime':'Install runtime'}</button></article>)}</div><p className="footnote">Updates use the operator’s OLLM_REF and LLAMA_REF configuration. A repair of an active inference environment unloads its model; select it again afterwards.</p></main>}
    </div>
    {selectedJob&&<JobDetail job={state.jobs.find(j=>j.id===selectedJob.id)||selectedJob} onClose={()=>setSelectedJob(null)}/>}
  </div>;
}
function Activity({state,events,onJob}:{state:Snapshot;events:LiveEvent[];onJob:(job:Job)=>void}) {
  const queued=state.jobs.filter(active);
  const recent=events.filter(e=>!['chat.delta','job.progress','job.updated'].includes(e.type)).slice(-7).reverse();
  return <aside className="activity"><div className="activity-title"><span>LIVE ACTIVITY</span><span className="dot on"/></div><div className="metric"><span>Inference</span><strong>{state.jobs.some(j=>j.kind==='chat'&&j.status==='running')?'Working':'Idle'}</strong></div><div className="metric"><span>Queued & running</span><strong>{queued.length}</strong></div><div className="memory-card"><div className="flex"><span>System memory</span><span>{Math.round(state.system.memory_used/state.system.memory_total*100)}%</span></div><progress value={state.system.memory_used} max={state.system.memory_total}/><small>{bytes(state.system.memory_used)} / {bytes(state.system.memory_total)}</small></div><div className="nav-caption">WORK QUEUE</div>{queued.slice(0,5).map(job=><button className="activity-job" key={job.id} onClick={()=>onJob(job)}><span>{job.kind}</span><Pill status={job.status}/><progress value={job.progress} max={1}/></button>)}{!queued.length&&<div className="quiet-state">✓<p>All clear.<br/><span>No pending jobs.</span></p></div>}<div className="nav-caption">EVENT STREAM</div><div className="event-stream">{recent.map(e=><div key={e.seq}><span className="event-dot"/><div><strong>{e.type.replaceAll('.',' / ')}</strong><small>{new Date(e.ts*1000).toLocaleTimeString()} {e.job_id&&'· '+e.job_id.slice(0,6)}</small></div></div>)}{!recent.length&&<p className="footnote">Events appear here as jobs run.</p>}</div><div className="local-note"><span>◇</span><p>Inference stays on this machine. Downloads and enabled MCP services can use the network.</p></div></aside>;
}
function JobDetail({job,onClose}:{job:Job;onClose:()=>void}) {
  const [events,setEvents]=useState<LiveEvent[]>([]); const [error,setError]=useState('');
  const cursor=useRef(0);
  useEffect(()=>{const close=(e:KeyboardEvent)=>{if(e.key==='Escape')onClose();};window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);},[onClose]);
  useEffect(()=>{
    let stopped=false; cursor.current=0; setEvents([]);
    async function load() {
      try {
        const data=await api<{events:LiveEvent[];next:number}>('/events?job_id='+job.id+'&after='+cursor.current+'&limit=1000');
        if(stopped)return;
        cursor.current=data.next;
        setEvents(old=>[...old,...data.events].slice(-5000));
      }catch(e){if(!stopped)setError(String(e));}
    }
    void load();const timer=window.setInterval(()=>void load(),1000);
    return()=>{stopped=true;window.clearInterval(timer);};
  },[job.id]);
  return <div className="modal-backdrop" onClick={onClose}><section className="job-modal" role="dialog" aria-modal="true" aria-label="Job details" onClick={e=>e.stopPropagation()}><div className="flex"><div><span className="eyebrow">JOB DETAILS</span><h2>{job.kind}</h2></div><button aria-label="Close job details" onClick={onClose}>×</button></div><div className="button-row"><Pill status={job.status}/><code>{job.id}</code></div>{job.error&&<div className="notice danger">{job.error}</div>}{error&&<p>{error}</p>}<h3>Event log</h3><pre className="log-view">{events.filter(e=>e.type!=='chat.delta').map(e=>'['+new Date(e.ts*1000).toLocaleTimeString()+'] '+e.type+' '+(e.type==='job.log'?String(e.data.text):JSON.stringify(e.data))+'\n').join('')||'No events yet.'}</pre><details><summary>Request payload</summary><pre>{JSON.stringify(job.payload,null,2)}</pre></details><details><summary>Result</summary><pre>{JSON.stringify(job.result||job.output,null,2)}</pre></details><button className="secondary" onClick={()=>{const blob=new Blob([JSON.stringify({job,events},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='job-'+job.id+'.json';a.click();URL.revokeObjectURL(url);}}>Export job & loaded events</button></section></div>;
}
