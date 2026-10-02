# Primary sources and inspected revisions

Checked during implementation on 2 October 2026. These document intended integration contracts, not hardware validation. Dependency versions and API availability can change; installed Git commits and pip freezes are recorded in `.state/runtimes/`.

## User fork, read through the GitHub connector

Inspected repository tree commit: `b395639e7397573728c7c4a1f82565ef1cb542c4`.

- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/README.md — offloading workflow and runtime prerequisites.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/pyproject.toml — runtime and optional tool dependencies.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/src/ollm/inference.py — exact catalogue aliases, checkpoint repositories, load_model and disk-cache behavior.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/src/ollm/tools/adapters.py — Qwen and GPT-OSS/Harmony preparation and strict final-turn parsing.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/src/ollm/tools/backend.py — existing buffered generation and isolated per-request cache conventions.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/src/ollm/tools/mcp.py — MCP SDK 1.x integration and same-task transport lifetimes.
- https://github.com/kieransimkin/ollm/blob/b395639e7397573728c7c4a1f82565ef1cb542c4/src/ollm/tools/types.py — assistant/tool-turn structure and message pairing.

## Framework and transport

- https://socket.io/how-to/use-with-nextjs — persistent Next.js/Socket.IO deployment pattern.
- https://nextjs.org/docs/app/guides/custom-server — custom server and self-hosting implications.
- https://python-socketio.readthedocs.io/en/stable/server.html — AsyncServer and ASGIApp protocol integration.
- https://registry.npmjs.org/next/latest — Next.js 16.3.8 metadata observed during implementation.
- https://registry.npmjs.org/react/latest — React 19.3.0 metadata observed during implementation.

## llama.cpp and model downloads

- https://github.com/ggml-org/llama.cpp/tree/master/tools/server — native llama-server API, streaming completions and options.
- https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md — Jinja templates and model-dependent tool calling.
- https://huggingface.co/docs/hub/api — repository/model discovery API.
- https://huggingface.co/docs/huggingface_hub/guides/download — snapshot downloads and pinned revisions.

## MCP discovery, client and SDK

- https://registry.modelcontextprotocol.io/docs — official registry API.
- https://registry.modelcontextprotocol.io/v0.1/servers — published server records and package metadata.
- https://github.com/modelcontextprotocol/python-sdk — official client sessions and transports.
- https://pypi.org/project/mcp/ — MCP SDK 1.30.0 release availability checked; this application restricts the SDK to 1.x.
- https://github.com/IBM/mcp-cli — current upstream project for the client formerly hosted under chrishayuk.
- https://github.com/IBM/mcp-cli/blob/main/README.md — `tools`, `ping`, `resources`, global config and filesystem-disable options.
- https://pypi.org/project/mcp-cli/0.20.1/ — current exact client release selected for the isolated CLI environment.

The console does not claim to implement all features of these upstream projects. See README scope and validation notes.
