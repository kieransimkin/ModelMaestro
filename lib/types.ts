export type Dict = Record<string, unknown>;
export interface Model {
  id: string; backend: 'ollm' | 'llama' | 'demo'; name: string; installed: boolean;
  alias?: string; repo_id?: string; filename?: string; adapter?: string;
  tool_mode?: string; modules: string[]; bytes?: number; revision?: string;
}
export interface Job {
  id: string; kind: string; lane: string; status: string; progress: number;
  payload: Dict; output: {raw?: string; content?: string; thinking?: string; round?: number};
  result: Dict | null; error: string | null; created: number; updated: number;
  run_after: number; priority: number;
}
export interface Runtime {
  id: string; status: string; version?: string; commit?: string; accelerator?: string;
  error?: string; note?: string; demo?: boolean;
}
export interface MCPServer {
  id: string; name: string; transport: string; enabled: boolean;
  package?: string; version?: string; url?: string; env_refs?: Record<string, string>;
}
export interface Approval {
  id: string; job_id: string; tool: string; arguments: Dict; status: string; expires: number;
}
export interface ModelState {
  status: string; active: Model | null; context: number; detail: string;
  pid: number | null; desired?: {model_id: string} | null;
}
export interface Snapshot {
  jobs: Job[]; models: Model[]; model_state: ModelState; runtimes: Runtime[];
  mcp_servers: MCPServer[]; approvals: Approval[];
  chats: {id: string; title: string; created: number}[]; cursor: number; demo: boolean;
  system: {memory_used: number; memory_total: number; disk_free: number; disk_total: number};
}
export interface LiveEvent {seq: number; job_id: string | null; type: string; data: Dict; ts: number}
export interface Sampling {max_tokens: number; temperature: number; top_p: number; context: number; thinking: boolean; reasoning_effort: string}
