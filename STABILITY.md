# STABILITY.md — Load-bearing Interfaces & Tech Stack

V2 commits both interfaces and tech stack for 12+ months.

Breaking either requires:

1. A migration ADR in `docs/architecture-decisions.md`
2. A contract test demonstrating the new shape
3. An upcaster for any persisted data
4. A strangler-fig migration plan for any plugin depending on the old shape
5. A 30-day observation window before deleting the legacy path

## Part A — Stable Interfaces

1. **Task**
   `{task_id, workflow_id, agent, payload, schema_version, created_at, parent_task_id?}`

2. **Handler**
   `handler(payload, ctx) -> Result{status, artifacts, verification, failure_class?}`

3. **LLMProvider**
   `complete(messages, model_class, max_tokens, ...) -> Response`

   Fixed adapter shape:
   - `anthropic_oauth` via `claude-code` CLI as primary
   - `anthropic_api`, `openai`, `gemini`, `minimax`, and `omlx` as fallbacks or specialized adapters
   - routed through `runtime/registry/llm_routing.yaml`
   - no third-party OAuth wrapper or `claude.ai` session scraping
   - local inference is oMLX only; primary local chat model is `gemma-4-31b-it-4bit`
   - oMLX model/cache root is `/Volumes/aw_swap/omlx-cache`

4. **ArtifactStore**
   `read(key) / write(key, payload, schema_version) / version(key) / list(prefix)`

5. **AuditEvent**
   `{event_id, ts, type, task_id?, workflow_id?, user_id?, payload, schema_version}`

6. **Heartbeat**
   `{ts, pid, last_dispatch_ts, last_workflow_ts, status}`

7. **Memory**
   `write / read / supersede / consolidate / list_recent`

   Fixed V2 semantics:
   - kinds: `fact`, `belief`, `episode`, `task`, `reflection`
   - bi-temporal supersede only; no hard deletion
   - Postgres + pgvector query layer
   - human-editable file mirror remains available through the port

## Part B — Stable Tech Stack

Fixed components:

- Python 3.12+
- LaunchAgent + local FastAPI server
- PostgreSQL 17
- DBOS Transact + Postgres backend for durable workflows
- LLMProvider port with six adapters: `anthropic_oauth`, `anthropic_api`, `openai`, `gemini`, `minimax`, `omlx`
- SwiftUI + SwiftData for iOS message/thread reliability
- mDNS/Bonjour + HTTPS API for app-to-Mac bridge
- local self-signed certificate with iOS pinned certificate fingerprint
- FastAPI for the existing web/API server

Explicitly not in the V2 stack:

- CKSyncEngine
- Cloudflare Workers or any cloud relay
- Sign in with Apple, StoreKit, IAP, TestFlight, or App Store release work
- Docker, Kubernetes, Redis, MongoDB, or a vector database as primary memory
- Ollama in Mira runtime, routing, or recovery paths

Everything outside these interfaces and fixed stack choices is plugin-layer work unless a future ADR says otherwise.
