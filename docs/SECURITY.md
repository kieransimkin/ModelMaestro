# Security and operating assumptions

## Intended deployment

This is a single-operator, self-hosted development/research workbench. It is not a hardened multi-tenant inference service. A token grants broad control over jobs, local inference and installation of approved MCP packages. Do not share it with untrusted users or place the service on the public internet without additional access controls.

The default public binding is loopback. The backend is always private loopback. The Node server validates Host before forwarding API and WebSocket traffic; the Python HTTP layer also validates Host. Socket.IO validates Origin and authentication. Browser POSTs require an allowed Origin and a custom anti-CSRF header. Cookie sessions are signed, HttpOnly, SameSite Strict and Secure when APP_ORIGIN is HTTPS. They expire after seven days. Bearer clients are supported for non-browser automation.

Changing APP_TOKEN and restarting revokes sessions. Logging out clears the current browser cookie; this is not a global session-revocation service. Existing independently authenticated clients retain their authority until disconnection/expiry or token rotation with restart.

## MCP trust boundary

Installing a package and executing an MCP server are privileged actions. A virtual environment separates dependencies, **not OS permissions**. A malicious package can read files accessible to the service user, open network connections, or act outside its declared tools. Per-call approval protects against unintended model actions, not malicious package initialization or install-time code.

Install only reviewed exact versions from publishers you trust. npm scripts are opt-in; PyPI builds can execute code. Transitive dependencies may also be malicious. The official MCP registry is discovery metadata, not a safety certificate. npm integrity and model checksums verify downloaded bytes against publisher metadata, not the trustworthiness of that publisher.

Run under a dedicated OS account. Use absolute, narrow filesystem roots and minimally scoped API keys. For genuinely untrusted packages, an OS-level sandbox/container/VM and network policy must be supplied outside this app. Do not claim that an enabled server’s `readOnlyHint` makes it safe; the implementation requires approval regardless of annotations.

MCP children receive a restricted inherited environment and isolated HOME/working directory. Credentials are supplied only through operator-configured environment references. This reduces accidental leakage; it does not stop code running as your user from reading your `.env` or other files. Persistent CLI config contains references; diagnostic jobs write a short-lived resolved config with mode 0600 and remove it during cleanup. A machine/power crash can leave a resolved file behind: startup removes stale `cli-*.json` files before accepting work.

MCP stdio stderr is stored in private local log files and not broadcast to the browser, because it can contain secrets. These logs can still contain credentials. The primary job logger redacts the application token and common token/password/API-key environment values, but this is best-effort redaction, not a data-loss prevention system.

## Model/tool separation

Model output cannot install packages, select arbitrary command lines or define new endpoints. Native tool calls must be complete and structurally parsed, with arguments validated against discovered JSON schemas. Generic text checkpoints must emit a complete, whole-response JSON envelope; fenced examples and prose snippets are inert. Non-finite values, duplicate JSON keys and incomplete calls are rejected. Remote schema references are rejected. Calls still require explicit user approval and are bounded by round/call/time/result limits.

Tool responses are untrusted content. Prompt injection remains possible: review the exact arguments and their consequences before approving. Denial does not rewind earlier calls. Cancellation cannot undo a completed file write, email, HTTP request or other side effect. Retrying an interrupted job may repeat an action, so stopped work is never silently replayed.

Resources/prompts are inventoried but not automatically consumed. HTML from Markdown/model text is not executed, external Markdown images are suppressed, and MCP Apps/binary media are not rendered. External links still lead to untrusted websites.

## Processes and persistence

Each loaded model owns a process group; termination/reaping precedes replacement. The scheduler and engine share a single inference lock. Owned subprocess identities are recorded with creation times so startup recovery does not kill an unrelated reused PID. This is best-effort recovery across process crashes, not a guarantee against arbitrary daemonisation, OS crashes or intentionally hostile children. Inspect the process list after a machine-level failure.

Exactly one Python backend process may use a state directory. Never use Uvicorn `--workers > 1`, backend autoreload, shared writable state between containers, or filesystem sync tools on a live database. The launcher and supplied service example supervise one frontend and one backend. Web UI actions do not mutate your source GitHub repository.

State includes prompts, tool arguments/results, partial outputs, selected model paths, package metadata and event logs. Filesystem permissions are private; data is not encrypted. Back up with the service stopped or with SQLite’s online-backup facilities. Logs are not automatically pruned. Securely retire tokens and state when decommissioning a machine.

## Remote access checklist

Keep backend and llama-server ports private. Put the Next.js port behind a trusted HTTPS reverse proxy or authenticated tunnel, configure the exact APP_ORIGIN, forward WebSocket upgrade headers, preserve Host/Origin correctly, and use external user/access controls. Do not configure wildcard origins. Remote MCP hosts must be explicitly listed in MCP_ALLOWED_HOSTS; non-loopback HTTP is rejected by the SDK integration. OAuth and remote endpoint authorization discovery are not implemented.

An approved endpoint’s DNS/hosting and SDK behavior remain external trust dependencies. The CLI is a separate trusted client; its own networking and filesystem behavior follows its upstream implementation and is not an OS sandbox. App-level host allowlisting is not a firewall for arbitrary subprocess traffic.
