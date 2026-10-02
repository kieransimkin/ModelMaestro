# Control plane and Socket.IO protocol

## Topology and authentication

The browser uses the Next.js origin, normally `http://127.0.0.1:3000`. HTTP requests below are under `/api`; Socket.IO uses `/socket.io/`, the default namespace and Socket.IO v4 client / Python Socket.IO v5 server semantics. The Node proxy forwards both polling and WebSocket upgrade requests. Never expose llama-server’s private port or the backend port directly.

Browser login: `POST /api/login` with JSON `{"token":"..."}`, an allowed `Origin`, and `X-Console-Request: 1`. A signed HttpOnly session cookie is then sent with same-origin requests and the Socket.IO handshake. Bearer-token clients send `Authorization: Bearer TOKEN` for HTTP; Socket.IO clients send `auth: {token: TOKEN}`. An explicitly disallowed Origin is rejected even with a bearer token.

All asynchronous actions return a persisted job immediately, not the final model result. Reuse a `request_key` to safely retry a submission after a lost acknowledgement. Never substitute a new key blindly: the first submission may already exist. Retries of failed/interrupted *execution* create a new job and may repeat external side effects.

## HTTP routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Unauthenticated liveness only. |
| POST | `/login`, `/logout` | Browser session entry/exit. |
| GET | `/state` | Current models, selected state, jobs, runtime status, MCP servers, approvals, conversations, system memory/disk and event cursor. |
| GET | `/schema` | Authenticated OpenAPI schema. |
| GET | `/jobs?limit=100&chat_id=...` | Recent jobs; max 1,000. |
| GET | `/jobs/{id}` | Full persisted job, output and result. |
| POST | `/jobs` | Validated job submission. |
| POST | `/jobs/{id}/cancel` | Request real computation/process cancellation. |
| POST | `/jobs/{id}/retry` | Explicit new job for a failed/cancelled/interrupted execution. |
| GET | `/events?after=0&limit=500&job_id=...` | Sequenced event replay; max 1,000 per page. |
| POST | `/approvals/{id}` | `{"allow":true}` or `false`; approvals are single use. |
| GET | `/models/search?q=...` | Public Hugging Face GGUF discovery. |
| GET | `/models/files?repo_id=owner/repo&revision=main` | GGUF file list and resolved immutable revision. |
| GET | `/mcp/search?q=...&cursor=...` | Official registry search; hour-long metadata cache. |
| GET | `/mcp/package?registry=npm&package=...` | Exact current package version/entrypoint metadata; does not install. |
| GET | `/mcp/inventory/{id}` | Last saved capability inventory. |
| POST | `/mcp/remote` | Explicitly trusted, host-allowlisted Streamable HTTP endpoint. |
| POST | `/mcp/{id}/enabled` | `{"enabled":true}` / `false`. |

API bodies and Socket.IO frames are bounded to 1 MiB. Jobs, schemas and message settings have additional field limits. Commands are assembled from validated argument vectors; there is no general shell-execution endpoint.

## Submission envelope

```json
{
  "kind": "chat",
  "request_key": "a-stable-client-generated-id",
  "priority": 0,
  "run_after": 0,
  "payload": {
    "chat_id": "conversation-1",
    "model_id": "ollm:llama3-1B-chat",
    "prompt": "Explain offloading.",
    "system": "You are a helpful assistant.",
    "tools": true,
    "settings": {
      "max_tokens": 512,
      "temperature": 0.7,
      "top_p": 0.95,
      "context": 8192,
      "thinking": false,
      "reasoning_effort": "low"
    }
  }
}
```

`run_after` is a Unix timestamp in seconds; zero means immediately eligible. Priority is −10 through +10. Model selections are always immediate, high priority; their request scheduling fields are not used. The request must name the currently ready model. Changing the model cancels previous inference jobs rather than implicitly reloading a different model when an old queued chat reaches execution.

Other payloads:

```json
{"kind":"model.load","payload":{"model_id":"ollm:llama3-1B-chat","context":8192,"gpu_layers":-1}}
{"kind":"model.download","payload":{"backend":"ollm","model_id":"ollm:llama3-1B-chat"}}
{"kind":"model.download","payload":{"backend":"llama","repo_id":"owner/repo","filename":"model-Q4_K_M.gguf","revision":"COMMIT_SHA"}}
{"kind":"runtime.install","payload":{"runtime":"llama","force":false}}
{"kind":"mcp.probe","payload":{"server_id":"my-server"}}
{"kind":"mcp.cli","payload":{"server_id":"my-server","action":"tools"}}
```

Runtime identifiers: `ollm`, `llama`, `mcp-cli`, `flash-attn`, `flash-linear-attention`, `kvikio`. Runtime repairs and MCP package installation use the maintenance lane. Model loading and chat use the inference lane. Downloads use the download lane. A package-install payload is documented by `/api/schema` and the UI; explicit trust is mandatory.

## Socket.IO client-to-server messages

Every message uses an acknowledgement callback, or `socket.timeout(...).emitWithAck(...)` in a v4 JavaScript client.

| Event | Payload | Acknowledgement |
|---|---|---|
| `sync` | `{"after":sequence}` | `{ok,state,events}`; at most 500 replay events. |
| `job:submit` | Same submission envelope as HTTP. | `{ok,job}` or `{ok:false,error}`. |
| `job:cancel` | `{"job_id":"..."}` | `{ok,job}`. Cancellation is asynchronous; wait for a terminal job update. |
| `approval:resolve` | `{"approval_id":"...","allow":true}` | `{ok:true}` or a validation error. |

All operator clients receive the server event named **`event`**:

```json
{
  "seq": 42,
  "job_id": "job-id-or-null",
  "type": "chat.delta",
  "data": {"channel":"raw","text":"hello","offset":0,"round":0},
  "ts": 1790940000.0
}
```

Common `type` values:

| Type | Data |
|---|---|
| `job.updated` | `{job: ...}` including persisted status/output. |
| `job.progress` | `{progress:0..1,message:...}`. Installation output may be log-based rather than a precise percentage. |
| `job.log` | `{text:...}` with known credentials redacted. |
| `chat.round` | `{round:...}` resets the raw stream for an agent round. |
| `chat.delta` | `{channel,text,offset,round}`; channel `raw`, `thinking` or `content`. |
| `chat.turn` | Parsed content/thinking/tool calls/finish reason after generation completes. |
| `model.state` | Selected/runtime state, PID, loaded context and desired selection. |
| `model.downloaded` | Verified local model metadata. |
| `runtime.updated` | Runtime status and resolved installation information. |
| `mcp.updated` | Saved server configuration with environment references, not entered secrets. |
| `mcp.connecting`, `mcp.connected`, `mcp.discovered` | Connection and capability discovery. |
| `tool.approval` | Approval ID, job ID, tool, arguments and expiry. |
| `tool.approval_resolved` | Approved, denied, expired, cancelled or interrupted decision. |
| `tool.started`, `tool.progress`, `tool.result`, `tool.error` | Tool execution lifecycle and bounded text results. |

The raw oLLM stream can include template/reasoning/tool markers. It is **not executable**. Only the structurally validated final turn can dispatch tools. Use the parsed final result for the stored answer, not a concatenation of raw intermediate rounds. Incomplete or length-truncated tool calls are never executed.

## Reconnection and replay

`seq` is global and monotonically increasing. Keep a cursor per consumer and deduplicate by sequence. On reconnect, call `sync`; use its state snapshot as the authoritative current view. Event replay is for historical activity, not for resubmitting work. Page `/events?after=N` until its `next` reaches the relevant cursor when more than 500 events were missed.

When applying a delta to partial output, both the agent round and `offset` must match. Offsets count **Unicode code points**, as Python strings do, not UTF-16 code units. The bundled browser client handles this distinction. If a delta does not fit, refetch the job snapshot rather than guessing. Job timestamps prevent an older snapshot overwriting newer streamed output. The browser also refreshes state periodically to recover from missed events.

Statuses are `queued`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`, `interrupted`. Terminal results and events remain readable. Interrupted work is not auto-replayed; external side effects are not exactly-once transactional with SQLite.
