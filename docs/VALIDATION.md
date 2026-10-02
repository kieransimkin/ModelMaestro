# Delivery validation

Date: **2 October 2026**. Source package: **ModelMaestro 0.1.0**. Rechecked after the naming update.

## Executed in this environment

| Check | Result | Meaning |
|---|---|---|
| Python unittest suite | **59 passed; 3 skipped** | 62 discovered tests, including four new branding/startup-contract checks. The three real Socket.IO integration tests were skipped because their dependencies are unavailable in this environment. |
| ModelMaestro naming regression tests | **4 passed** | Package/page identity, renamed React component, matching health/launcher identity, service paths, and Socket.IO environment-variable compatibility. |
| Real OS subprocess lifecycle tests | **Passed** | Cancellation/reaping and unload-before-replacement tested using real local Python processes, not an actual LLM. |
| SQLite persistence and scheduler | **Passed** | WAL persistence, priorities/delays, lane isolation, recovery, idempotency, repeated cancellation, deadline cleanup, output replay and approval behavior. |
| Tool parsing and policy tests | **Passed** | Strict envelopes, duplicate/non-finite JSON rejection, complete-turn gating, schema validation, per-call approval, denial, timeout and binary omission. |
| llama.cpp SSE adapter | **Passed against mocked HTTP streams** | Incremental native tool argument reconstruction and rejection of length-truncated calls. This does not validate a real llama-server build. |
| Model/download boundaries | **Passed** | Fork alias extraction, safe paths, split-GGUF completeness and selected checkpoint-file policy. No actual weights downloaded. |
| Registry/configuration contract | **Passed against mocked registry data** | Response shape matches the UI; exported credentials remain environment references. |
| HTTP body boundary | **Passed** | Chunked request limits and exact replay of accepted bodies tested without Socket.IO. |
| Python compileall | **Passed** | Backend, workers and launcher compile. |
| Node syntax checks | **Passed** | Custom server and standalone Socket.IO example parse. |
| TypeScript/TSX transpilation | **Passed: 7 files** | Global TypeScript compiler found no syntax/transpilation errors. This is **not** dependency-aware typechecking or a Next.js build. |

The raw unittest output is in `test-results.txt`. Tests use temporary state directories and deterministic or mocked model responses where stated. They do not substitute fake data into the production runtime path.

## Blocked or not executed

The original delivery recorded the following installation blockers. Dependency installation was not repeated for this branding-only update; its full-build, integration and GPU qualification limitations remain.

`npm install` failed with **EAI_AGAIN / DNS resolution failure for registry.npmjs.org**. Attempting to install `python-socketio` with pip returned **no available distributions in the restricted environment**. The package was not preinstalled. Consequently, the following claims are deliberately **not** made:

- Successful full Next.js production build, dependency-aware TypeScript checking, browser rendering, or visual/accessibility testing of the live UI.
- Successful live Socket.IO connection, authentication, reverse-proxy upgrade, disconnect/reconnect and replay tests. Their real integration tests are included for CI/your host.
- Live MCP SDK stdio/Streamable HTTP handshakes or operation of a third-party MCP package/mcp-cli executable.
- Successful fresh-host installation/build of oLLM, llama.cpp, mcp-cli or optional GPU extensions.
- Actual model download, GPT-OSS repack load, GPU inference, device memory usage, throughput, or any particular low-VRAM target.
- macOS, native Windows or WSL hardware qualification. Native Windows is explicitly rejected by the launcher; WSL uses the Linux code path.

No npm lockfile was fabricated. The exact observed direct framework versions are pinned, but the full transitive dependency graph must be resolved, reviewed and committed on a network-enabled host. Git refs and pip freezes are recorded by runtime installation. The supplied GitHub Actions workflow has **not** been run or pushed by this delivery.

## Host qualification sequence

Run the included CI workflow or the README’s isolated test-environment commands first. A successful full dependency install should make the three Socket.IO tests run rather than skip. Then run `npm run typecheck` and `npm run build`.

Start `python3 start.py --demo` and verify login, responsive layout, model selection, a streamed echo, job logs, browser-close persistence and reconnect. Demo mode is deliberately labelled and is not evidence of model quality or GPU operation.

For real inference, use a fresh or backed-up state directory. Confirm each runtime installation records its executable and Git/package revision. Download a small supported oLLM checkpoint and a GGUF independently; verify manifest hashes on disk. Cancel and retry a download to verify resumability under your network/storage conditions.

Load one model, generate a response, switch to the other, and inspect OS processes/GPU memory to confirm the prior process is gone before the replacement allocates. Check cancellation during generation, loading and tool approval. Test your intended context size, CPU-layer/GPU-layer settings and device backend; do not infer memory fit from a model’s parameter count or disk size.

Finally install a reviewed, narrowly scoped MCP package. Verify capability discovery and CLI diagnostics, deny a call, approve a harmless call, and inspect progress/results. Test interrupted jobs only against non-destructive fixture tools until you understand side-effect recovery. Do not use a successful unit suite as justification to expose the service publicly.
