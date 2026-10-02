# ModelMaestro

**Your models, tools and tasks—in sync.** Part of the **DanceFlow** ecosystem.

A self-hosted **Next.js model workbench** for [Kieran Simkin’s oLLM fork](https://github.com/kieransimkin/ollm) and **llama.cpp**, with a persistent task scheduler, live Socket.IO events and approval-gated MCP tools.

This is a standalone companion application. It does not modify your oLLM repository, substitute Ollama for oLLM, or fall back to loading your offloaded models through vanilla Transformers.

## Renamed package

This is **ModelMaestro 0.1.0**, the renamed standalone application previously delivered as oLLM Console. The archive extracts to `modelmaestro/`; the npm package is `modelmaestro`, and the sample user service is `examples/modelmaestro.service`. This is a branding update, not a change to the inference engines or their pinned revisions.

Existing `.env` settings and saved-state formats are unchanged. To reuse an existing installation, stop its launcher and service first, then set `APP_STATE_DIR` to the absolute path of its existing `.state` directory before starting ModelMaestro. Do not run both applications against the same state directory. Install the new user service only after disabling the previous service.

The internal `console.sqlite3` filename, `console_session` cookie, `X-Console-Request` header and generic tool-call envelope are deliberately retained for compatibility. The example Socket.IO client now uses `MODELMAESTRO_TOKEN` and optional `MODELMAESTRO_URL`, with the previous `CONSOLE_TOKEN` and `CONSOLE_URL` accepted as fallbacks. `/api/health` now identifies the service as `modelmaestro`; the launcher has been updated to match.

## Start

Host prerequisites: **Python 3.11+**, **Node.js 22+ with npm**, **Git**, **CMake**, and a **C/C++ build toolchain**. Python 3.11 or 3.12 is recommended for GPU-wheel compatibility. Designed for Linux, Windows through WSL2, and macOS; the delivered core tests were run on Linux. Native Windows process management is deliberately not enabled.

```bash
# From the extracted modelmaestro/ directory:
cp .env.example .env
python3 start.py
```

Open **http://127.0.0.1:3000**. The launcher prints the location of a private token file. Read it and paste the token into the login form:

```bash
cat .state/access-token
```

The launcher creates the backend virtual environment, installs frontend dependencies and builds Next.js. Once the control plane starts, it queues separate installation jobs for **your oLLM fork**, **llama.cpp’s native llama-server**, and **mcp-cli**. Follow their logs in **Tasks** or their status in **Runtimes**. Installation failures remain visible and retryable; they do not take down the UI.

It installs into `.state/`, without `sudo`. It does **not** install OS drivers, change system Python, or download model weights without a model-download request. First startup needs internet access and substantial free disk space for Python/GPU packages. A compatible NVIDIA driver/toolkit or platform GPU stack is still your responsibility.

Development and clearly labelled, GPU-free demo modes:

```bash
python3 start.py --dev
python3 start.py --demo
python3 start.py --demo --dev
```

Demo mode returns deterministic echo text, **not an LLM response**. It neither installs inference runtimes nor downloads weights. Network catalogue searches remain real external requests.

`--no-install-runtimes` disables automatic runtime installation; missing runtimes can still be explicitly installed from the UI. `--skip-app-install` skips pip/npm bootstrap when you have already installed app dependencies. It does not disable a required Next.js production build.

## The interface

| Workspace | What it does |
|---|---|
| **Chat** | Persistent conversations, streamed model output, Markdown answers, reasoning display where available, system instructions, sampling controls, delayed execution, per-call MCP approval and cancellation. |
| **Models** | Inspect supported oLLM aliases, search Hugging Face GGUF repositories, select a quantisation, queue downloads, view local models and load/reload a selected model. |
| **Tasks** | Inspect inference, download, installation, discovery and CLI jobs; see progress and logs; cancel or explicitly retry stopped jobs; export a job with its loaded event history. |
| **MCP tools** | Search the official MCP Registry, look up npm/PyPI metadata, install exact package versions, configure argument/environment references, enable servers, discover capabilities and run mcp-cli diagnostics. |
| **Runtimes** | Inspect installation status, recorded revisions and failures; install/repair engines and optional GPU modules. |

### A first conversation

Wait for the desired runtime’s installation to complete. In **Models**, download either an oLLM catalogue checkpoint or a GGUF file. Downloads do not load weights onto the GPU. After download, choose **Load into memory** and wait for **ready**. Open **Chat** and send a message.

Switching models cancels older queued/running inference jobs, waits for their cleanup, terminates/reaps the previous model process, and only then starts the replacement. Reloading the same model follows the same policy. This avoids silently answering an old request with a different model and releases the previous process’s CUDA context and module-global loaders.

A chat job snapshots the selected model identity and selection revision. Independent conversations can queue behind one another; overlapping turns in the same conversation are rejected so history ordering is unambiguous. Changing the context setting requires reloading. For llama.cpp, `−1` GPU layers requests offload supported by that build; `0` explicitly selects CPU execution.

Closing a browser tab does **not** cancel a job. Stopping the server does. Run the launcher as a supervised service to keep jobs running after terminal logout; see `examples/modelmaestro.service`.

## Supported models and runtimes

### Your oLLM fork

The default source is pinned to the inspected commit:

```dotenv
OLLM_REPO=https://github.com/kieransimkin/ollm.git
OLLM_REF=b395639e7397573728c7c4a1f82565ef1cb542c4
```

At that revision, the text catalogue includes:

| Alias | Source | Tool route |
|---|---|---|
| `llama3-1B-chat` | `unsloth/Llama-3.2-1B-Instruct` | Strict whole-response JSON envelope |
| `llama3-3B-chat` | `unsloth/Llama-3.2-3B-Instruct` | Strict whole-response JSON envelope |
| `llama3-8B-chat` | `unsloth/Meta-Llama-3.1-8B-Instruct` | Strict whole-response JSON envelope |
| `gpt-oss-20B` | `AnuarSh/gpt-oss-20B` | Fork’s native Harmony adapter |
| `qwen3-next-80B` | `Qwen/Qwen3-Next-80B-A3B-Instruct` | Fork’s native Qwen adapter |
| `gemma3-12B` | `google/gemma-3-12b-it` | Strict whole-response JSON envelope; text only |

The adapter calls `Inference.load_model()` on the locally installed checkpoint. Generation streams directly through a Transformers streamer instead of pretending that the fork’s buffered agent wrapper is a streaming API. Each turn gets an isolated temporary disk cache where supported. GPT-OSS does not request disk KV caching because the inspected fork does not support it.

GPT-OSS requires the fork-compatible `gds_export` repack. Ordinary GPT-OSS safetensors are not treated as interchangeable. Voxtral/audio and image uploads are outside this text-only interface.

The catalogue reads literal aliases from the installed fork’s `hf_download`/`ini_model` definitions without executing them. It can expose new declared aliases, but this is **not architecture qualification**: unfamiliar formats use the strict generic tool route and still depend on your fork implementing the model correctly. Arbitrary DeepSeek/Qwen variants are not falsely advertised as supported. Updating the fork may require corresponding integration work.

The inspected `pyproject.toml` makes FlashAttention mandatory despite the documented fallback. Bootstrap installs the non-accelerator dependencies and then your fork with `--no-deps --no-build-isolation`. FlashAttention, flash-linear-attention and CUDA-12 KvikIO are explicit module-install jobs; Qwen3-Next selection requests its linear-attention module. The inspected revision defaults to its documented tested PyTorch 2.8.0. See `.env.example` to select a matching PyTorch index/device.

Compatibility of actual checkpoints, GPU extensions, device backends and low-VRAM budgets must be verified on your host. One active model does not mean every model fits in available RAM/VRAM. `OLLM_CUDA_MEMORY_FRACTION` limits the PyTorch allocator, not all possible external GPU allocations.

### llama.cpp

Bootstrap builds **llama-server from the official ggml-org/llama.cpp repository**. It is not llama-cpp-python. `LLAMA_REF=latest` resolves a release tag on first installation, records the tag and commit, and keeps the completed installation on normal restarts. Pin `LLAMA_REF` to a tag/commit for a reproducible deployment. Explicit reinstall/update resolves the configured ref again.

`LLAMA_ACCELERATOR=auto` chooses CUDA when `nvcc` is present, Metal on Apple Silicon, otherwise CPU. Explicit `cpu`, `cuda`, `metal`, and `vulkan` options are available; their system build prerequisites are not installed by the application. Builds use two jobs by default to avoid exhausting host memory.

The server binds to a private loopback port with a per-process API key. The backend waits for the expected server/model alias before declaring it ready. Chat uses the server’s streaming chat-completion API with `--jinja`. Tool support and template quality remain checkpoint-dependent; the UI does not claim that every GGUF supports native tool calls.

## Downloads

Downloads use their own scheduler lane, two workers, and no inference/GPU process. The worker resolves a Hugging Face revision to a commit SHA, checks available disk headroom, downloads only required assets, verifies file sizes and available LFS SHA-256 checksums, and atomically publishes a complete installation manifest. Split GGUF selections fetch and verify the entire shard set. Image projection GGUFs are excluded from text model selection.

Partial downloads are retained in private staging directories and can resume on **Retry**. Interrupted downloads are not silently restarted. Missing checksums are recorded as missing, not invented. A retry after the directory rename but before DB registration can recover the finished manifest. Set `HF_TOKEN` in `.env` for gated/private checkpoints and accept the model’s licence separately. Large oLLM checkpoints and disk KV caches can require far more disk than a quantised GGUF.

## MCP setup

**mcp-cli is a client, not an MCP server.** It is installed in its own environment, pinned to version **0.20.1**. The console’s models use the official MCP Python SDK against the **same managed server configurations**, so native tool calls do not need to scrape CLI terminal output. The **mcp-cli tools** button runs the actual CLI as a queued diagnostic job.

Search the official registry or enter a known npm/PyPI package. Look up its version, review the publisher and metadata, then pin an exact version and explicitly acknowledge code trust. Registry registration is not a security endorsement. npm lifecycle scripts are disabled unless you opt in. PyPI installation may execute build code. Choose a declared console entrypoint; arbitrary shell commands and Git/HTTP package URLs are not accepted through the installer.

A typical environment mapping is:

```json
{"API_KEY":"MY_SERVICE_TOKEN"}
```

Put the actual value in the host `.env`, not in the browser form:

```dotenv
MY_SERVICE_TOKEN=replace-with-your-secret
```

Restart the launcher after changing `.env`. Use absolute paths for filesystem servers’ allowed-root arguments; their working directory and HOME are deliberately private. Existing packages start **disabled**. Enable a server to make its tools available to chat, or click **Discover capabilities** to explicitly launch/probe it without enabling it for every conversation.

Every model-initiated tool call is validated against the discovered schema and requires **Approve this call**. Denials/timeouts go back to the model as tool errors. Calls are never automatically retried. Limits: eight model rounds, sixteen calls per chat, 128 exposed tools, ten-minute approval expiry, bounded schema/result sizes. MCP sessions stay in one asyncio task so AnyIO transport cleanup is well formed.

Tools are callable by models. Resources and prompts are discovered and displayed in inventory, but are not automatically read or inserted into conversations. This release does not implement OAuth login flows, legacy SSE MCP transports, MCP Apps, sampling/elicitation, binary tools, remote code sandboxing or automatic package trust. Streamable HTTP endpoints are supported; non-local hosts require operator allowlisting through `MCP_ALLOWED_HOSTS` and HTTPS. Endpoint redirects are not followed by the SDK transport.

**Virtual environments are not sandboxes.** A trusted MCP package runs with your user’s OS permissions. Use a dedicated OS account/container and narrowly scoped credentials for untrusted or sensitive services. Approval cannot make malicious package code safe.

## Scheduler and Socket.IO

The SQLite WAL store persists jobs, partial output, results, event sequences, approvals, installations and conversations. Scheduler lanes are:

- **Inference:** one worker, shared with model loading.
- **Downloads:** two workers.
- **Maintenance:** one worker for runtimes, packages, probes and CLI diagnostics.

Jobs support priority and `run_after` timestamps. Queued jobs survive restarts. Previously running/waiting jobs become **interrupted**, never auto-replayed, because tools may already have caused side effects. The selected model is restored before normal queued inference proceeds. Managed subprocess identities are recorded for crash recovery; a creation-time check avoids killing reused PIDs. Normal cancellation stops the actual process group, not just the browser stream.

This is an actual Socket.IO service, not a raw WebSocket approximation. It supports WebSocket and polling transports, authentication, acknowledgements, submissions, cancellation, approval decisions and sequenced live events. A reconnecting client can fetch a snapshot and replay persisted events; it must reconcile by sequence and per-job output offsets rather than execute jobs again.

See **[docs/PROTOCOL.md](docs/PROTOCOL.md)** and **[examples/socket-client.mjs](examples/socket-client.mjs)**.

```bash
# With the console running and a model loaded:
MODELMAESTRO_TOKEN="$(cat .state/access-token)" node examples/socket-client.mjs "Explain SSD weight offloading."
```

No Redis, external queue broker or database server is needed. Run exactly **one** backend worker per state directory; a process lock enforces ownership. Horizontal scaling, multiple users/tenants, exactly-once external side effects and recurring cron schedules are not implemented.

## Configuration, deployment and storage

Copy `.env.example` and adjust ports, state directory, Git refs, GPU settings and credentials. The backend binds only to `127.0.0.1`; the Next.js custom server proxies `/api/` and `/socket.io/` on the public app origin, including upgrade requests. This is a persistent self-hosted application, not a Vercel/serverless deployment.

Keep the default loopback binding for local use. For remote access, configure `HOST`, the exact HTTPS `APP_ORIGIN`, a trusted TLS reverse proxy with WebSocket support, and access controls. A strong access token is mandatory even on loopback. Browser sessions use signed HttpOnly/SameSite cookies; mutation routes enforce same-origin/CSRF checks. Programmatic clients use a bearer token or Socket.IO auth payload. See **[docs/SECURITY.md](docs/SECURITY.md)** before exposing it to a network.

`.state/` contains private credentials, SQLite data, runtime environments, model files, downloads, disk KV caches, MCP configuration and logs. Do not commit it. Stored conversations and tool results are plaintext protected by filesystem permissions, not encrypted. The main UI lists the latest 100 jobs; conversation history/API reads support up to 1,000 recent turns. Event logs are retained without automatic pruning; monitor disk usage. Stop the service before backing up its state.

## Tests and delivery status

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt aiohttp
python -m unittest discover -s tests -v
npm install
npm run typecheck
npm run build
```

The delivered verification run passed **55 tests**; **three real Socket.IO integration tests were explicitly skipped** because `python-socketio` could not be installed in the build environment. Python compile checks, Node server syntax checks, and TypeScript/TSX syntax/transpilation checks passed. Dependency-aware TypeScript checking, a production Next.js build, browser rendering, real Socket.IO/MCP SDK handshakes, runtime installations, downloads and GPU inference were **not verified here**. npm registry DNS failed and Python package installation was unavailable. See **[docs/VALIDATION.md](docs/VALIDATION.md)** for the exact evidence and remaining qualification steps.

CI installs real application dependencies, runs the integration tests, performs TypeScript checking and builds Next.js on Python 3.11/3.12 with Node 22. It does not install GPU runtimes or download model weights. No CI success is claimed before you run it. No fabricated npm lockfile is supplied: the first successful npm install resolves one; commit that lockfile after reviewing it. Runtime Git commits and pip freezes are recorded separately.

## Layout

```text
app/                 Next.js App Router pages and responsive styling
components/          Chat, models, tasks, MCP and runtime workspaces
lib/                 Typed API client and Socket.IO state reconciliation
server.mjs           Persistent Next.js server and same-origin WebSocket/API proxy
start.py             Bootstrap, credentials and process supervision
backend/app.py       FastAPI routes, auth, Socket.IO handlers and lifecycle
backend/scheduler.py SQLite-backed queues, cancellation and approvals
backend/engines.py   Exclusive model process lifecycle and streaming adapters
backend/workers/     Offloaded oLLM and verified/resumable download workers
backend/mcp_tools.py Registry, package installation, SDK sessions and CLI jobs
tests/               Core, process lifecycle and real Socket.IO integration tests
docs/                Protocol, security, validation and primary-source references
examples/            Socket.IO client and systemd service template
```

Released as source under MIT. Third-party runtime, model and package licences still apply. Primary sources and inspected revisions are recorded in **[docs/SOURCES.md](docs/SOURCES.md)**.
