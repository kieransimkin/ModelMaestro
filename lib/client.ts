'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {io, Socket} from 'socket.io-client';
import type {Dict, Job, LiveEvent, Snapshot} from './types';

export class APIError extends Error {constructor(public status: number, message: string) {super(message);}}
export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const response = await fetch('/api' + path, {
    method: method || (body === undefined ? 'GET' : 'POST'),
    credentials: 'same-origin', cache: 'no-store',
    headers: {'Content-Type': 'application/json', 'X-Console-Request': '1'},
    ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
  });
  const result = await response.json().catch(() => ({detail: response.statusText}));
  if (!response.ok) throw new APIError(response.status, typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result as T;
}
export function useModelMaestro() {
  const [state, setState] = useState<Snapshot | null>(null);
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const socket = useRef<Socket | null>(null);
  const cursor = useRef(0);
  const refresh = useCallback(async () => {
    try {
      const next = await api<Snapshot>('/state');
      cursor.current = Math.max(cursor.current, next.cursor);
      setState(previous => {
        if (!previous) return next;
        // A snapshot taken just before an arriving token must not roll that
        // token back. Per-job timestamps make reconciliation idempotent.
        const newer = new Map(previous.jobs.map(j => [j.id, j]));
        return {...next, jobs: next.jobs.map(j => (newer.get(j.id)?.updated || 0) > j.updated ? newer.get(j.id)! : j)};
      });
      setAuthenticated(true);
    } catch (e) {
      if (e instanceof APIError && e.status === 401) {setAuthenticated(false); setState(null);}
      else setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {void refresh();}, [refresh]);
  useEffect(() => {
    if (!authenticated) return;
    const client = io({path: '/socket.io/', autoConnect: false, withCredentials: true,
      transports: ['websocket', 'polling'], reconnection: true});
    socket.current = client;
    client.on('connect', () => {
      setConnected(true);
      client.emit('sync', {after: cursor.current}, (reply: {ok: boolean; state: Snapshot; events: LiveEvent[]}) => {
        if (reply.ok) {
          setEvents(old => Array.from(new Map([...old, ...reply.events].map(e => [e.seq, e])).values()).sort((a,b) => a.seq-b.seq).slice(-160));
          void refresh();
        }
      });
    });
    client.on('disconnect', () => setConnected(false));
    client.on('connect_error', () => setConnected(false));
    client.on('event', (event: LiveEvent) => {
      cursor.current = Math.max(cursor.current, event.seq);
      setEvents(old => [...old.filter(e => e.seq !== event.seq), event].slice(-160));
      setState(old => {
        if (!old) return old;
        const data = event.data;
        if (event.type === 'model.state') return {...old, model_state: data as unknown as Snapshot['model_state']};
        if (event.type === 'job.updated') {
          const job = data.job as Job;
          const previous = old.jobs.find(j => j.id === job.id);
          if (previous && previous.updated > job.updated) return old;
          return {...old, jobs: [job, ...old.jobs.filter(j => j.id !== job.id)].sort((a,b) => b.created-a.created).slice(0,100)};
        }
        if (event.type === 'chat.delta' || event.type === 'chat.round' || event.type === 'job.progress') {
          return {...old, jobs: old.jobs.map(job => {
            if (job.id !== event.job_id) return job;
            if (event.type === 'job.progress') return {...job, progress: Number(data.progress)};
            if (event.type === 'chat.round') return {...job, output: {...job.output, raw: '', thinking: '', round: Number(data.round)}};
            const channel = String(data.channel) as 'raw' | 'thinking' | 'content';
            if (!['raw','thinking','content'].includes(channel)) return job;
            const previous = job.output[channel] || '';
            if (Number(data.offset) !== Array.from(previous).length || Number(data.round) !== (job.output.round || 0)) return job;
            return {...job, updated: event.ts, output: {...job.output, [channel]: previous + String(data.text)}};
          })};
        }
        if (event.type === 'tool.approval') return {...old, approvals: [...old.approvals.filter(a => a.id !== data.id), data as unknown as Snapshot['approvals'][number]]};
        if (event.type === 'tool.approval_resolved') return {...old, approvals: old.approvals.filter(a => a.id !== data.id)};
        return old;
      });
      if (['runtime.updated','mcp.updated','model.downloaded','chat.turn'].includes(event.type)) void refresh();
    });
    client.connect();
    const poll = window.setInterval(() => {void refresh();}, 4000);
    return () => {window.clearInterval(poll); client.disconnect(); socket.current = null;};
  }, [authenticated, refresh]);
  const submit = useCallback(async (kind: string, payload: Dict, options: Dict = {}) => {
    const job = await api<Job>('/jobs', {kind, payload, request_key: crypto.randomUUID(), ...options});
    await refresh(); return job;
  }, [refresh]);
  return {state, authenticated, connected, error, setError, events, refresh, submit};
}
export function bytes(value = 0) {
  if (value >= 1024 ** 3) return (value / 1024 ** 3).toFixed(1) + ' GiB';
  if (value >= 1024 ** 2) return (value / 1024 ** 2).toFixed(0) + ' MiB';
  return value.toLocaleString() + ' B';
}
export const active = (job: Job) => ['queued','running','waiting_approval'].includes(job.status);
