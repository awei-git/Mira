# Mira API Control Plane Refactor

- **Date**: 2026-04-30
- **Last revised**: 2026-04-30 (added §24 Phase Checkpoints, §25 Critical Risks, §26 Offline Draft Queue, §27 iOS Background / Push, §28 Process Model, §29 Apple Notes Scope, §30 Compatibility Sunset Schedule; reviewer concerns folded into existing sections)
- **Status**: implementation in progress — core API, Postgres control schema, API-first MiraBridge reads/writes, DB runtime mirror, SSE skeleton, and local write retry queue implemented behind rollout flags
- **Owner**: Ang + Mira
- **Scope**: Mira, MiraBridge, MiraApp (Apple Notes inbox/outbox explicitly **out of scope** — see §29)
- **Primary goal**: remove iCloud Drive from the active task command/status path and replace it with a canonical API-backed control plane.

> **Reviewer-flagged blockers (must resolve before Phase 1 schema lands or Phase 3 ships).** Detail in §25:
> 1. Mac-asleep submission path (offline draft queue) — §25.1, design in §26
> 2. iOS background regression vs. current iCloud cache delivery — §25.2, push design in §27
> 3. Feed items schema: same `tasks` table or separate? — §25.3
>
> Phase Checkpoints (§24) define explicit verification gates between phases. Do not advance a phase until its gate passes.

> **Implementation checkpoint (2026-04-30 night).** The first implementation pass landed the additive control plane without deleting or migrating existing Mira memory/skill/artifact data. Defaults remain conservative: API writes, DB runtime dispatch, and DB SSE are flag-gated; iCloud command export remains available as compatibility fallback until the runtime has run cleanly against Postgres.

## 1. Executive Summary

Mira currently uses iCloud Drive as the active message bus between MiraApp and the local Mira runtime. This works for slow artifact sync, but it is the wrong substrate for task control. Task control needs strong identity, ordering, acknowledgements, current liveness, terminal state propagation, and explicit error semantics. iCloud gives eventual file sync, placeholder files, partial availability, and weak observability.

The proposed refactor is:

1. Keep Mira running locally on the Mac.
2. Make the local FastAPI service the primary API for MiraApp.
3. Store task/control state in Postgres under a dedicated `mira_control` schema.
4. Stream task events to the app with Server-Sent Events, with polling fallback.
5. Keep iCloud only for generated artifacts, optional export, and temporary compatibility.

The critical design rule:

**The phone must never infer active task truth from an eventually synced file cache again.**

## 2. Current System Trace

### 2.1 Current Repos

| Repo | Role | Current relevant modules |
|---|---|---|
| `Mira` | Mac runtime, task dispatch, FastAPI mirror | `agents/super/talk.py`, `agents/super/task_manager.py`, `agents/super/task_worker.py`, `web/server.py`, `lib/bridge.py` |
| `MiraBridge` | Shared Python/Swift bridge protocol package | `python/mira_bridge.py`, `swift/Sources/MiraBridge/*` |
| `MiraApp` | SwiftUI iPhone app | `MiraApp.swift`, `HomeView.swift`, `ItemDetailView.swift`, `TodoStore.swift`, `HealthDataProvider.swift`, `BackgroundRefreshManager.swift` |

### 2.2 Current Task Submission Path

```
MiraApp CommandWriter.createRequest()
  -> writes users/{user}/commands/cmd_*.json in iCloud
  -> creates optimistic local MiraItem in ItemStore

MiraBridge/Python Bridge.poll_commands()
  -> reads command files
  -> writes command_ledger.json
  -> deletes command files

Mira agents/super/talk.py do_talk()
  -> converts command to Message
  -> creates users/{user}/items/{item_id}.json
  -> TaskManager.dispatch()

TaskManager
  -> writes data/tasks/status.json
  -> starts task_worker.py subprocess

task_worker.py
  -> writes result.json/output.md in data/tasks/<workspace>

do_talk() next cycle
  -> TaskManager.check_tasks()
  -> reads result.json
  -> bridge.update_status(item_id, terminal status)
  -> writes item JSON + manifest JSON to iCloud

MiraApp SyncEngine
  -> polls LAN mirror or iCloud manifest/items
  -> updates ItemStore
```

### 2.3 Current FastAPI Server

`web/server.py` already exposes:

| Endpoint | Current behavior |
|---|---|
| `GET /api/heartbeat` | Reads `heartbeat.json` from bridge |
| `GET /api/{user}/manifest` | Reads bridge `manifest.json` |
| `GET /api/{user}/items/{id}` | Reads bridge item JSON |
| `POST /api/{user}/request` | Writes command JSON and optimistic item JSON |
| `POST /api/{user}/items/{id}/reply` | Writes command JSON and appends to item JSON |
| `GET /api/{user}/events` | Polls bridge manifest every 10 seconds and streams item JSON |
| artifact endpoints | Serve files from iCloud artifact directory |

This is not yet a real control plane. It is a faster facade over the same iCloud-backed item protocol.

### 2.4 Current State Stores

| Store | Path | Current truth level |
|---|---|---|
| Bridge items | `Mira-Bridge/users/{user}/items/*.json` | App-facing projection, not reliable execution truth |
| Manifest | `Mira-Bridge/users/{user}/manifest.json` | App sync index, vulnerable to stale reads |
| Commands | `Mira-Bridge/users/{user}/commands/*.json` | iOS to agent input queue |
| Ledger | `Mira-Bridge/users/{user}/command_ledger.json` | At-most-once-ish command processing |
| Task records | `data/tasks/status.json` | Runtime task truth for TaskManager |
| Worker output | `data/tasks/<workspace>/result.json` | Worker terminal result |
| Artifacts | `Mira-Artifacts/{user}/...` | Durable generated content |

The refactor should elevate task records and events into a single canonical control database.

## 3. Problems To Fix

### 3.1 iCloud Is Eventual, Task Status Needs Current Truth

iCloud can delay or skip local materialization of files. A phone may see a stale item while the Mac has already failed the task. A manifest update and item file update are not a transaction from the phone's perspective.

### 3.2 App Cache Can Permanently Miss Updates

`SyncEngine` currently records an item manifest timestamp before it successfully fetches the corresponding item. If item fetch fails, the app may never retry until another update changes the timestamp.

### 3.3 ID Drift Between Optimistic App Items And Runtime Items

Swift `CommandWriter.createRequest()` creates local `req_<cmd_id>`, but command payload may not include that `item_id`. Mira then creates a different request id. This can leave the user watching an item that is not the runtime task.

### 3.4 Completion Mapping Is Partial

Mira runtime statuses include values that are not mapped to app-visible terminal states. Example: `paused_horizon_limit` can be written by `task_worker.py`, collected by `TaskManager`, and then ignored by `do_talk()` because it only maps a subset of statuses.

### 3.5 No Durable Event Log For User-Facing State Changes

Current item JSON is mutable state. There is no append-only event stream saying:

1. task accepted
2. queued
3. dispatched
4. worker heartbeat
5. progress status card
6. terminal result

Without an event log, recovery and app resync are ad hoc.

### 3.6 Worker Liveness Is Split

There is a heartbeat supervisor in `lib/supervisor/worker_supervisor.py`, but task workers are not consistently writing heartbeats into a user-facing state path. The phone should see a difference between:

1. Mac API unreachable
2. Mira online but idle
3. task queued
4. task running with fresh heartbeat
5. task running but stale
6. task failed/timeout

### 3.7 Security Defaults Are Too Loose For Write API

The current web GUI can allow LAN access without a token depending on config. That may be acceptable for read-only local mirror use. It is not acceptable for a write-capable task control API.

## 4. Non-Goals

This plan does not attempt to:

1. turn Mira into a multi-tenant SaaS service
2. host Mira in the cloud
3. remove all iCloud artifact usage immediately
4. redesign all agents or workflows
5. replace local subprocess workers with a full distributed job queue
6. build push notifications in phase 1 (but APNs push is required for Phase 5.5 — see §27)
7. rewrite the app UI
8. migrate Apple Notes inbox/outbox to the API (separate transport, separate failure profile — see §29)
9. solve "Mac-is-off, phone wants to do work" via push-to-wake (out of scope; phone-side draft queue addresses this — see §26)

## 5. Target Architecture

### 5.1 High-Level Shape

```
MiraApp
  - APIClient
  - SSE event stream
  - local cache for offline display only

Mira API
  - FastAPI
  - token auth
  - task/todo/health/artifact endpoints
  - event stream from DB

Mira Control Store
  - Postgres
  - tasks
  - messages
  - task_events
  - commands
  - todos
  - health_imports

Mira Runtime
  - TaskManager
  - task_worker
  - background jobs
  - worker heartbeat

Artifacts
  - local or iCloud file directories
  - served through API
```

### 5.2 Source Of Truth Rules

| Domain | New source of truth | iCloud role |
|---|---|---|
| active task status | Postgres `mira_control.tasks` + `mira_control.task_events` | none |
| task messages | Postgres `mira_control.messages` | compatibility export only |
| task progress | Postgres `mira_control.task_events` | none |
| worker liveness | Postgres `mira_control.tasks.heartbeat_at` + events | none |
| todos | Postgres `mira_control.todos` | compatibility export only during migration |
| health summary | API backed by local health store/files | optional artifact source |
| health export from phone | API upload | fallback file upload only if needed |
| generated writings/audio/photos | artifact files served by API | optional storage location |
| app local cache | Swift local cache | display only, never canonical |

### 5.3 Network Model

Recommended initial deployment:

1. Mac runs FastAPI on a Tailscale interface or LAN address.
2. iPhone connects to the Mac over Tailscale.
3. Every write endpoint requires bearer token auth.
4. Loopback may remain tokenless for local development only.

Do not expose write endpoints on trusted LAN without a token.

## 6. Technology Choices

### 6.1 Postgres First

Use Postgres for the control plane. This supersedes the earlier SQLite-first draft because the local environment already has Postgres running for market data and related services.

Pros:

1. already deployed locally for adjacent systems
2. transactional and concurrency-safe
3. better fit for multiple Mira processes sharing state
4. supports `LISTEN/NOTIFY` later if SSE needs lower-latency wakeups
5. easier operator queries across task, event, health, and future worker tables
6. clearer migration path if remote workers or hosted relay are added later

Cons:

1. Mira API now depends on the Postgres service being up
2. local dev/test needs a configured database or explicit mocked/gated tests
3. schema isolation matters because the database may also hold market data
4. backup/restore must include the `mira_control` schema
5. credentials and connection pool limits become operational concerns

Schema isolation rules:

1. use `CONTROL_DATABASE_URL` when set
2. otherwise fall back to existing `DATABASE_URL`
3. create all control-plane tables under schema `mira_control`
4. do not write control-plane tables into `public`
5. never share table names with market/Tetra data

### 6.2 Server-Sent Events First

Use SSE for live task updates.

Pros:

1. simpler than WebSocket
2. native enough on iOS through `URLSession.bytes`
3. one-way server-to-phone is enough for status events
4. reconnect can use `Last-Event-ID`
5. easy to debug with `curl`

Cons:

1. no bidirectional channel
2. iOS background behavior is limited
3. needs polling fallback

WebSocket can come later if the app needs true interactive streaming.

### 6.3 API-First With Polling Fallback

The app should:

1. fetch initial snapshot from `GET /tasks`
2. connect to `GET /events`
3. apply events incrementally
4. periodically refresh full snapshot as drift correction
5. keep local cache only for offline display

## 7. Canonical Status Model

### 7.1 App-Visible Statuses

Use this set for all app-visible task/request statuses:

| Status | Meaning | Terminal |
|---|---|---|
| `queued` | accepted but not dispatched | no |
| `working` | worker dispatched and not terminal | no |
| `needs-input` | Mira needs user clarification/approval | no |
| `blocked` | cannot proceed until system/user unblock | yes or pause-state, depending on UI |
| `done` | verified success | yes |
| `failed` | failed execution or verification | yes |
| `timeout` | exceeded runtime budget or stale heartbeat | yes |
| `cancelled` | user cancelled | yes |
| `archived` | hidden from active views | yes |

Decision: `blocked` should be treated as terminal for dispatch slots, but visible in UI as an actionable state. It should not show an endless spinner.

### 7.2 Internal Status Mapping

All internal statuses must normalize before reaching the API:

| Internal | API status | Notes |
|---|---|---|
| `completed` | `done` | existing alias |
| `error` | `failed` | existing alias |
| `needs_input` | `needs-input` | existing alias |
| `paused_horizon_limit` | `needs-input` or `blocked` | prefer `needs-input` if user can resume |
| `preflight_blocked` | `blocked` | include failure_class |
| unknown terminal | `failed` | fail closed |

### 7.3 Terminal State Rule

A task may enter `done` only if:

1. worker completed
2. result exists
3. verification says the expected output exists or the task is explicitly conversational
4. DB update and event append happen in one transaction

A worker crash, missing output, unknown status, stale heartbeat, or result parse failure must not leave `working`.

## 8. Database Design

### 8.1 Database Location

Postgres schema: `mira_control`

Connection URL:

1. `CONTROL_DATABASE_URL` if present
2. otherwise `DATABASE_URL` from existing Mira config

Rationale:

1. stays off iCloud
2. reuses the existing local database stack
3. supports concurrent API and agent writers
4. allows future `LISTEN/NOTIFY` event wakeups
5. keeps control-plane data isolated from market data by schema

### 8.2 Connection Setup

On connection setup:

```sql
CREATE SCHEMA IF NOT EXISTS mira_control;
SET search_path TO mira_control, public;
```

### 8.3 Schema

```sql
CREATE TABLE IF NOT EXISTS mira_control.control_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mira_control.tasks (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('request', 'discussion', 'feed')),
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('user', 'agent')),
  quick INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  parent_id TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  queued_at TEXT,
  started_at TEXT,
  heartbeat_at TEXT,
  completed_at TEXT,
  worker_pid INTEGER,
  workspace TEXT,
  workflow_id TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  failure_class TEXT,
  error_code TEXT,
  error_message TEXT,
  retryable INTEGER NOT NULL DEFAULT 0,
  result_path TEXT,
  result_summary TEXT,
  archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_updated ON mira_control.tasks(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON mira_control.tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_worker_pid ON mira_control.tasks(worker_pid);
CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat ON mira_control.tasks(status, heartbeat_at);

CREATE TABLE IF NOT EXISTS mira_control.messages (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  sender TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL,
  image_path TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_task_created ON mira_control.messages(task_id, created_at);

CREATE TABLE IF NOT EXISTS mira_control.task_events (
  id BIGSERIAL PRIMARY KEY,
  task_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_user_id ON mira_control.task_events(user_id, id);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON mira_control.task_events(task_id, id);

CREATE TABLE IF NOT EXISTS mira_control.inbound_commands (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  received_at TEXT NOT NULL,
  handled_at TEXT,
  task_id TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS mira_control.todos (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  priority TEXT NOT NULL,
  status TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_todos_user_status ON mira_control.todos(user_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS mira_control.todo_followups (
  id TEXT PRIMARY KEY,
  todo_id TEXT NOT NULL REFERENCES todos(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('user', 'agent')),
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

### 8.4 Event Payload Examples

Task accepted:

```json
{
  "task": {
    "id": "req_abcd1234",
    "status": "queued",
    "title": "Do the thing"
  }
}
```

Worker dispatched:

```json
{
  "pid": 12345,
  "workspace": "/Users/angwei/Sandbox/Mira/data/tasks/...",
  "attempt_count": 1
}
```

Progress:

```json
{
  "message": {
    "id": "status_123",
    "sender": "agent",
    "kind": "status_card",
    "content": "{\"type\":\"status\",\"text\":\"Planning...\",\"icon\":\"list.bullet.clipboard\"}"
  }
}
```

Terminal failure:

```json
{
  "failure_class": "worker_crash",
  "error": {
    "code": "failed",
    "message": "Worker exited without producing output",
    "retryable": true
  }
}
```

## 9. API Design

### 9.1 Authentication

Use bearer token:

`Authorization: Bearer <token>`

Config:

```yaml
services:
  webgui_host: "0.0.0.0"
  webgui_port: 8384
  webgui_token: "<long random token>"
  webgui_allow_loopback_without_token: true
  webgui_allow_lan_without_token: false
```

Rules:

1. token required for every write endpoint
2. token required for non-loopback reads
3. token stored in iOS Keychain
4. settings screen must display whether token auth is configured

### 9.2 Profiles

`GET /api/profiles`

Returns:

```json
{
  "profiles": [
    {"id": "ang", "display_name": "Ang", "agent_name": "Mira"}
  ]
}
```

No iCloud profile file dependency after migration.

### 9.3 Heartbeat

`GET /api/heartbeat`

Returns:

```json
{
  "timestamp": "2026-04-30T20:30:00Z",
  "status": "online",
  "busy": true,
  "active_count": 1,
  "active_tasks": [
    {
      "id": "req_abcd1234",
      "title": "Do the thing",
      "status": "working",
      "heartbeat_at": "2026-04-30T20:29:58Z"
    }
  ]
}
```

### 9.4 Task Snapshot

`GET /api/{user_id}/tasks?include_archived=false&limit=200&updated_after=<iso>`

Returns:

```json
{
  "items": [MiraItem],
  "server_time": "2026-04-30T20:30:00Z",
  "last_event_id": 12345
}
```

The `MiraItem` response should stay compatible with current Swift models.

### 9.5 Create Task

`POST /api/{user_id}/tasks`

Request:

```json
{
  "type": "request",
  "title": "Summarize this",
  "content": "long request",
  "quick": false,
  "tags": ["writing"],
  "client_request_id": "optional-idempotency-key"
}
```

Response:

```json
{
  "item": MiraItem,
  "event_id": 123,
  "dispatch": {
    "status": "queued",
    "reason": "accepted"
  }
}
```

Idempotency:

1. if `client_request_id` repeats within 24 hours, return existing task
2. never create duplicate task for network retry

### 9.6 Reply

`POST /api/{user_id}/tasks/{task_id}/reply`

Request:

```json
{
  "content": "continue",
  "client_request_id": "optional-idempotency-key"
}
```

Behavior:

1. append user message in DB
2. reopen task if terminal and retry is allowed
3. dispatch follow-up using same `task_id`
4. emit `message_added` and `status_changed` events

### 9.7 Cancel

`POST /api/{user_id}/tasks/{task_id}/cancel`

Behavior:

1. mark `cancelled`
2. if worker_pid is active, signal process group
3. append event with signal result
4. do not silently leave task `working` if signal fails

**Prerequisite (must land before this endpoint is shipped):** workers must be spawned in their own process group. Today `task_manager.py` spawns workers via `subprocess.Popen` — verify it passes `start_new_session=True` (POSIX `setsid`). Without this, sending `SIGTERM` to `-pid` either fails or risks signaling `core.py`. Add a startup assertion that the spawned worker's `os.getpgid()` differs from the agent's. Cancel logic must:

1. send `SIGTERM` to `-worker_pid` (process group)
2. wait up to `cancel_grace_seconds` (default 10s) for clean exit
3. send `SIGKILL` to `-worker_pid` if still alive
4. verify exit; only then mark `cancelled`. If exit cannot be verified (PID reused, kernel weirdness), mark `failed` with `failure_class=cancel_unverified` rather than lying.

### 9.8 Retry

`POST /api/{user_id}/tasks/{task_id}/retry`

Behavior:

1. validate retry ceiling
2. increment attempt count
3. preserve previous result/error history in events
4. dispatch new worker

### 9.9 Pin/Archive/Share

Replace current command-file behavior:

| Endpoint | Behavior |
|---|---|
| `POST /api/{user}/tasks/{id}/pin` | toggle or set pinned in DB |
| `POST /api/{user}/tasks/{id}/archive` | set `archived` in DB |
| `POST /api/{user}/tasks/{id}/share` | create shared artifact/feed entry or compatibility export |

### 9.10 Events

`GET /api/{user_id}/events?since=<event_id>`

SSE event types:

| Event | Payload |
|---|---|
| `snapshot_required` | app should refetch full task snapshot |
| `task_created` | full `MiraItem` |
| `task_updated` | full `MiraItem` |
| `message_added` | task id + message |
| `status_changed` | task id + old/new status |
| `heartbeat` | heartbeat payload |
| `deleted` | item id archived/deleted |

SSE line format:

```text
id: 12345
event: task_updated
data: {"item": {...}}
```

Reconnect rules:

1. app stores last event id
2. reconnect passes `since`
3. server replays events newer than `since` if retained
4. if event gap is too old, send `snapshot_required`

### 9.11 Todos

Move `TodoStore` off direct file I/O.

| Endpoint | Behavior |
|---|---|
| `GET /api/{user}/todos` | list todos from DB |
| `POST /api/{user}/todos` | create todo |
| `PATCH /api/{user}/todos/{id}` | update title/priority/status |
| `POST /api/{user}/todos/{id}/followup` | append followup and optionally create task |
| `DELETE /api/{user}/todos/{id}` | delete |

### 9.12 Health

Health should be migrated in two layers.

Read:

| Endpoint | Behavior |
|---|---|
| `GET /api/{user}/health/summary` | serve current health summary |
| `GET /api/{user}/health/checkups` | list uploaded checkup files |

Write:

| Endpoint | Behavior |
|---|---|
| `POST /api/{user}/health/export` | upload Apple Health JSON payload |
| `POST /api/{user}/health/checkups` | multipart upload checkup files |

Keep direct HealthKit reads local in the app. Only bridge-summary and export/checkup paths move.

### 9.13 Artifacts

Keep existing artifact endpoints and gradually remove direct iCloud artifact reads from the app:

| Current app area | New path |
|---|---|
| `LibraryView` direct iCloud browsing | `GET /api/{user}/artifacts` |
| image loading in item details | API URL first, iCloud fallback |
| generated docs/audio | API streaming/download |

## 10. Python Implementation Plan

### 10.1 New Module Layout

Add:

```
lib/control/
  __init__.py
  db.py
  migrations.py
  models.py
  repository.py
  events.py
  projection.py
  idempotency.py

web/
  api_models.py
  control_api.py
```

Keep `web/server.py` as app bootstrap and route registration.

### 10.2 `lib/control/db.py`

Responsibilities:

1. open Postgres connection
2. set/control `mira_control` schema
3. run migrations
4. provide transaction context manager
5. avoid global long-lived write transactions

Suggested interface:

```python
def connect() -> psycopg2.extensions.connection: ...

@contextmanager
def transaction() -> Iterator[psycopg2.extensions.connection]: ...
```

### 10.3 `lib/control/repository.py`

Responsibilities:

1. create task
2. append message
3. update status
4. attach worker pid/workspace
5. update heartbeat
6. mark terminal result
7. query tasks for API
8. append events transactionally

Important invariant:

Every user-visible task mutation writes both the current state row and an append-only event in one DB transaction.

### 10.4 `lib/control/projection.py`

Convert DB rows to current `MiraItem` JSON shape.

This keeps Swift UI churn low. The app should be able to decode API items with the existing `MiraItem` model.

### 10.5 `web/control_api.py`

Implement the new endpoints.

Do not make the new API write command files. During migration, if legacy dispatch is still needed, call a Python dispatch function directly or write to a DB-backed pending queue consumed by `do_talk()`.

### 10.6 TaskManager Changes

Modify `agents/super/task_manager.py`:

1. accept canonical task id from API-created task
2. write DB state on dispatch
3. write DB worker pid and workspace
4. on result collection, write terminal DB state
5. write failure state when process exits without result
6. expose active tasks from DB or keep JSON status as internal mirror during transition

Phase transition rule:

During early migration, `data/tasks/status.json` can remain for compatibility. After DB runtime is stable, status JSON becomes diagnostic export only.

### 10.7 `talk.py` Changes

Current `do_talk()` does three jobs:

1. collect completed tasks
2. poll inbound bridge commands
3. update bridge items

Target:

1. collection remains, but writes DB/API events
2. bridge command polling becomes compatibility-only
3. bridge item updates become compatibility export only

Suggested split:

```
collect_task_results()
process_api_pending_tasks()
process_legacy_bridge_commands()
export_bridge_compat()
```

### 10.8 Worker Changes

Modify `task_worker.py`:

1. wrap `main()` body with top-level exception handling
2. start heartbeat thread that updates DB every 15-30 seconds
3. progress/status card writes go to DB events
4. result writes still write `result.json` for audit
5. on clean exit, mark heartbeat stopped

Do not let an unhandled exception leave only stderr as the source of truth.

**Heartbeat-write design choice (resolve before Phase 4).** Two options:

- **Option A — direct DB write from worker.** Worker thread holds a connection and writes `tasks.heartbeat_at`. Simple, but couples the worker to Postgres at runtime. New failure mode: database/network hiccups inside worker during long agent inference.
- **Option B — append-only heartbeat file, ingested by `core.py`.** Worker writes `data/tasks/<workspace>/heartbeat.jsonl` every 15-30s. The next `core.py` tick (or a small heartbeat-ingest thread in `mira-api`) reads new lines and updates `tasks.heartbeat_at` + emits `heartbeat` event. Worker stays DB-free; heartbeat freshness is bounded by the agent tick (worst case 30s — acceptable for the "stale heartbeat → timeout" rule, which uses a longer threshold anyway).

**Recommendation: Option B.** It preserves today's worker isolation property (worker only writes its workspace files; nothing else can break it). The `mira-api` process (§28) ingests heartbeat files into the DB on a 5s interval. Reconsider Option A only if heartbeat latency under 30s becomes user-visible (it shouldn't — heartbeats only matter for stuck-detection at minute scale).

If Option A is chosen anyway, the worker must use a short-lived connection per write, a low statement timeout, and never hold the connection across agent inference calls.

### 10.9 Background Jobs

Scheduled background jobs currently create feed items directly through bridge or artifact files. Do not migrate all at once.

Phase 1:

1. keep background feed items mirrored into DB for app reading
2. keep original file writes

Phase 2:

1. background jobs write feed items to DB
2. optional compatibility export writes item JSON for old app versions

## 11. Swift Implementation Plan

### 11.1 New `MiraAPIClient`

Add in `MiraBridge/swift/Sources/MiraBridge/Services/MiraAPIClient.swift`.

Responsibilities:

1. authenticated requests
2. JSON encode/decode
3. retry for transient network errors
4. idempotency key injection
5. SSE stream
6. clear error typing for UI

Core methods:

```swift
func fetchProfiles() async throws -> [MiraProfile]
func fetchHeartbeat() async throws -> MiraHeartbeat
func fetchTasks(updatedAfter: String? = nil) async throws -> TaskSnapshot
func createTask(title: String, content: String, quick: Bool, tags: [String]) async throws -> MiraItem
func reply(to itemId: String, content: String) async throws -> MiraItem
func cancel(itemId: String) async throws -> MiraItem
func retry(itemId: String) async throws -> MiraItem
func setPinned(itemId: String, pinned: Bool) async throws -> MiraItem
func archive(itemId: String) async throws
func streamEvents(since: Int?) -> AsyncThrowingStream<MiraEvent, Error>
```

### 11.2 `BridgeConfig` Refactor

Current `BridgeConfig` is folder-first. Target is server-first.

New config fields:

1. `serverURL`
2. `apiToken`
3. `profile`
4. `connectionMode`: `api`, `icloudFallback`, `offline`
5. optional `artifactsFallbackURL`

Keep folder selection as optional:

1. local artifact fallback
2. emergency iCloud compatibility
3. migration rollback

Settings UI should show:

1. server URL
2. auth configured yes/no
3. last API heartbeat
4. Tailscale/LAN reachability debug
5. iCloud fallback enabled yes/no

### 11.3 Replace `CommandWriter`

Option A: create `APICommandWriter` and keep `CommandWriter` for fallback.

Option B: refactor `CommandWriter` to call API first and fall back to file writes.

Recommended:

1. add `MiraCommandService`
2. keep `CommandWriter` as `ICloudCommandWriter`
3. inject command service into views

This makes fallback explicit and avoids hiding file writes in a class named as if it is transport-agnostic.

### 11.4 Replace `SyncEngine`

Current `SyncEngine`:

1. loads heartbeat from LAN then iCloud
2. loads manifest from LAN then iCloud
3. fetches changed items
4. confirms command ledger

Target `APISyncEngine`:

1. fetch heartbeat from API
2. fetch task snapshot from API
3. open SSE event stream
4. apply events to `ItemStore`
5. periodically full-refresh as drift correction
6. no manifest timestamp logic
7. no command ledger logic

Polling fallback:

1. if SSE fails, poll `GET /tasks` every 20s while active
2. if offline, show cached state with explicit offline banner

### 11.5 `ItemStore`

Current `ItemStore` can mostly stay.

Changes:

1. add `apply(event:)`
2. track `lastEventId`
3. track `lastFullRefreshAt`
4. local cache remains display cache only
5. never treat local cache as command delivery truth

### 11.6 `TodoStore`

Replace direct `todos.json` file I/O with API methods.

API-backed behavior:

1. `refresh()` calls `GET /todos`
2. `add()` calls `POST /todos`
3. `complete()` calls `PATCH /todos/{id}`
4. `addFollowup()` calls `POST /todos/{id}/followup`
5. optimistic UI is allowed, but server response reconciles canonical row

### 11.7 Health

Current app has three bridge-dependent health paths:

1. read `health_summary.json`
2. export Apple Health JSON to iCloud
3. upload checkup files to iCloud

Target:

1. read summary through `GET /health/summary`
2. export through `POST /health/export`
3. checkups through multipart `POST /health/checkups`
4. keep HealthKit local queries unchanged

### 11.8 Background Refresh

iOS background refresh cannot keep a long SSE stream alive. It should:

1. fetch heartbeat
2. fetch changed task snapshot
3. process notifications
4. upload health export if due
5. exit quickly

Do not depend on background SSE.

## 12. Migration Phases

### Phase 0: Stabilize Current Bridge Before Migration

**Ship as a standalone PR, independent of the rest of this refactor.** These five fixes resolve ~80% of user-visible pain documented in §3 and require no schema, no API, no Swift architecture change. They should not be bundled with the DB work and should not wait for it. If the DB refactor is later abandoned or deferred, Phase 0 still stands on its own.

Goal: prevent additional confusing failures while building the new path.

Tasks:

1. fix Swift item timestamp bug: update manifest timestamp only after item fetch succeeds
2. send `item_id` in Swift `createRequest` and `createDiscussion`
3. add default fail-closed status mapping in `talk.py`
4. map `paused_horizon_limit`
5. improve stuck sweeper to repair terminal TaskRecord drift
6. make API token mandatory for LAN writes

Files:

1. `MiraBridge/swift/Sources/MiraBridge/Services/SyncEngine.swift`
2. `MiraBridge/swift/Sources/MiraBridge/Services/CommandWriter.swift`
3. `Mira/agents/super/talk.py`
4. `Mira/web/server.py`

Success criteria:

1. phone and Mac use same task id for new submissions
2. failed task is visible as failed within one polling cycle
3. unknown internal terminal status never leaves item `working`

Rollback:

1. revert these patches
2. no schema migration involved

### Phase 1: DB Mirror And Read-Only API

Goal: introduce Postgres as a mirror without changing app behavior.

**Important: in Phases 1-3, DB is a derived projection of JSON files, not the source of truth.** The read API must project from JSON files (or from a freshly-rebuilt DB), not trust the DB as canonical. This avoids dual-truth drift bugs during the window where neither writer (legacy or new) is exclusive. DB becomes canonical only at Phase 4 when `TaskManager` writes through it transactionally.

Tasks:

1. add `lib/control/db.py`
2. add migration runner
3. add repository/projection
4. import existing bridge items into DB
5. import existing `data/tasks/status.json` into DB
6. add read endpoints under `/api/{user}/tasks`
7. keep existing manifest/items endpoints untouched
8. **resolve §25.3 (feed table decision) before writing schema migration**

Success criteria:

1. `GET /api/ang/tasks` returns same visible items as current manifest path
2. terminal statuses match `data/tasks/status.json`
3. migration can run repeatedly without duplicates
4. DB can be deleted and rebuilt from existing files during this phase

Tests:

1. migration test with sample item JSON
2. projection test to `MiraItem` schema
3. read API test

Rollback:

1. disable new route usage
2. drop or ignore the `mira_control` schema

### Phase 2: App API Read Path

Goal: app reads tasks from API, not iCloud manifest/items.

Tasks:

1. add `MiraAPIClient`
2. add API config/token UI
3. add API-backed sync engine
4. initial snapshot from `GET /tasks`
5. polling fallback
6. keep iCloud read fallback behind explicit setting

Success criteria:

1. app home view renders from API snapshot
2. app shows offline/API unreachable distinctly
3. iCloud folder selection is not required for task list
4. existing cached display still works offline

Tests:

1. Swift unit tests for API decoding
2. mocked API snapshot into `ItemStore`
3. manual test over LAN/Tailscale

Rollback:

1. switch connection mode back to iCloud fallback
2. no runtime migration dependency

### Phase 3: API Write Path With Legacy Runtime

Goal: phone writes to API, server still may feed legacy dispatch internally.

Tasks:

1. `POST /tasks` creates DB task/message
2. server dispatches directly through `TaskManager.dispatch()` or writes a DB pending queue
3. `POST /reply`, `cancel`, `retry`, `pin`, `archive`
4. Swift command service calls API
5. stop writing iCloud command files from the app
6. keep server-side compatibility export to bridge item JSON

Important design choice:

Do not implement API writes by writing command JSON files and waiting for `poll_commands()`. That preserves the broken queue semantics. API writes should create canonical DB rows immediately.

Success criteria:

1. create task returns canonical item id
2. app does not create divergent optimistic ids
3. command retry is idempotent
4. task appears in DB and UI before worker dispatch
5. cancellation updates DB and UI

Tests:

1. API create/reply/cancel tests
2. idempotent retry test
3. dispatch failure test returns `failed`, not silent queued

Rollback:

1. app toggles back to iCloud command writer
2. server compatibility export keeps old app usable

### Phase 4: DB-Backed Runtime State

Goal: TaskManager and worker state transitions are canonical DB mutations.

Tasks:

1. `TaskManager.dispatch()` writes `queued/working` transitions to DB
2. store worker pid/workspace in DB
3. `TaskManager.check_tasks()` writes terminal states to DB
4. worker progress cards write DB messages/events
5. worker heartbeat updates DB
6. stale heartbeat monitor marks `timeout`
7. `data/tasks/status.json` becomes diagnostic export

Success criteria:

1. killing a worker marks task failed/timeout in API
2. missing `result.json` marks failed
3. failed DB update aborts terminal projection rather than lying to UI
4. app sees progress without iCloud

Tests:

1. fake worker success
2. fake worker crash
3. fake stale heartbeat
4. unknown result status
5. concurrent task updates

Rollback:

1. feature flag `CONTROL_DB_RUNTIME=false`
2. restore `TaskManager` JSON status as canonical

### Phase 5: API Events And SSE

Goal: live updates without manifest polling.

Tasks:

1. append events transactionally for every task mutation
2. implement `/events?since=`
3. support replay by event id
4. send `snapshot_required` when event gap is too old
5. app connects SSE while foregrounded
6. app falls back to polling on SSE failure

Success criteria:

1. status changes appear in app within seconds
2. reconnect after app sleep catches up
3. full refresh repairs event gap
4. background refresh uses snapshot, not SSE

Tests:

1. SSE endpoint yields ordered event ids
2. reconnect with `since`
3. event gap response
4. Swift parser for SSE frames

Rollback:

1. disable SSE
2. polling snapshot remains sufficient

### Phase 6: Todos, Health, Artifacts

Goal: remove remaining app-critical iCloud file reads/writes.

Tasks:

1. migrate `TodoStore` to API
2. migrate health summary reads to API
3. migrate health exports/checkup uploads to API
4. migrate Library/artifact browsing to API
5. keep direct iCloud artifacts as optional fallback

Success criteria:

1. app can be configured with only server URL/token/profile
2. iCloud folder is not required for normal operation
3. health export succeeds through API
4. artifact browsing works through API

Rollback:

1. keep iCloud fallback for health/artifacts until stable

### Phase 7: Decommission Active iCloud Bridge

Goal: make iCloud inactive for task control.

Tasks:

1. disable app command file writes by default
2. disable app manifest/item reads by default
3. make `Bridge.poll_commands()` compatibility-only
4. export bridge item JSON only for old app versions if configured
5. update docs and operations handbook
6. add self-audit checks that flag active iCloud task usage

Success criteria:

1. new task can be created, run, fail, retry, cancel with iCloud Drive disabled
2. no active control path depends on `Mira-Bridge/users/*/commands`
3. no active status path depends on `manifest.json`

## 13. Feature Flags

Add config flags:

```yaml
control_plane:
  enabled: false
  database_url: postgresql://localhost:5432/ai_system
  schema: mira_control
  api_writes_enabled: false
  runtime_db_enabled: false
  sse_enabled: false
  bridge_compat_export: true       # default true; sunset per §30
  icloud_command_fallback: true    # default true; sunset per §30
  push_notifications_enabled: false  # see §27
  offline_draft_queue_enabled: false # phone-side; see §26
  cancel_grace_seconds: 10
```

**All `*_compat_*` and `*_fallback_*` flags must have an explicit sunset in §30.** Do not add a fallback flag without a removal date.

Suggested Python constants:

1. `CONTROL_PLANE_ENABLED`
2. `CONTROL_API_WRITES_ENABLED`
3. `CONTROL_RUNTIME_DB_ENABLED`
4. `CONTROL_SSE_ENABLED`
5. `BRIDGE_COMPAT_EXPORT_ENABLED`
6. `ICLOUD_COMMAND_FALLBACK_ENABLED`

## 14. Compatibility Strategy

### 14.1 Old App Compatibility

During migration, server/runtime should optionally export DB tasks back to bridge item JSON:

```
DB task update
  -> update bridge item JSON
  -> rebuild manifest
```

This lets old app builds continue to work.

### 14.2 Old Runtime Compatibility

During early migration, DB importer mirrors:

1. bridge item JSON
2. `data/tasks/status.json`
3. `result.json` files

This gives read-only API before runtime writes directly to DB.

### 14.3 Cutover Rule

Only remove fallback after:

1. app API read path stable for 7 days
2. app API write path stable for 7 days
3. DB runtime state stable for 7 days
4. no stuck `working` items for 7 days
5. manual rollback has been tested

## 15. Testing Plan

### 15.1 Python Unit Tests

Add tests:

```
tests/control/test_db_migrations.py
tests/control/test_repository.py
tests/control/test_projection.py
tests/control/test_api_tasks.py
tests/control/test_events.py
tests/control/test_idempotency.py
```

Required cases:

1. migration creates schema
2. migration is idempotent
3. create task appends task and event in one transaction
4. append message preserves order
5. terminal failure clears active worker fields
6. stale heartbeat maps to timeout
7. unknown status maps to failed
8. projection matches Swift `MiraItem` shape
9. idempotency key returns existing task
10. unauthorized write is rejected

### 15.2 Python Integration Tests

Add:

```
tests/integration/test_api_task_lifecycle.py
tests/integration/test_taskmanager_control_db.py
tests/integration/test_sse_events.py
```

Lifecycle cases:

1. create -> queued -> working -> done
2. create -> worker crash -> failed
3. create -> worker stale heartbeat -> timeout
4. needs-input -> reply -> working -> done
5. cancel running task
6. retry failed task

### 15.3 Swift Tests

Add to MiraBridge/MiraApp tests:

1. decode task snapshot
2. decode event stream frames
3. apply events to `ItemStore`
4. create request idempotency key generation
5. token auth header present
6. offline cache does not imply task is still working

### 15.4 Manual Verification

Manual checklist:

1. start Mira API on Mac
2. connect phone on Tailscale
3. submit task from phone
4. observe canonical id in response
5. observe status changes without iCloud Drive
6. kill worker process
7. app shows failed/timeout
8. retry task
9. cancel task
10. background app and reopen
11. app catches up through snapshot/event id
12. disable network and verify offline banner

## 16. Observability

### 16.1 Operator Dashboard

Add DB-backed operator fields:

1. active tasks
2. stale running tasks
3. tasks by status last 24h
4. failure classes last 24h
5. API reachability
6. SSE connected clients
7. last event id
8. DB migration version

### 16.2 Logs

Structured logs for:

1. API task created
2. dispatch started
3. worker heartbeat stale
4. terminal result written
5. SSE reconnect
6. auth failure
7. idempotency replay
8. DB write failure

### 16.3 Self-Audit Rules

Add self-audit checks:

1. any task `working` with stale heartbeat
2. any terminal task missing terminal event
3. any app-visible task id absent from DB
4. any iCloud command files processed while fallback disabled
5. DB migration version behind code version

## 17. Failure Modes And Mitigations

| Failure | Mitigation |
|---|---|
| Mac asleep | app shows API unreachable; optional iCloud fallback only for queued offline drafts |
| API server down | launchd restarts server; app cached view marked stale |
| Tailscale unavailable | LAN fallback if configured, or offline display |
| DB unavailable or saturated | API returns explicit 503, app shows offline/stale state, runtime keeps legacy path until Phase 4 |
| DB corruption | backup DB, rebuild from event/log/artifact compatibility exports where possible |
| worker crash before result | TaskManager collector marks failed; heartbeat monitor marks timeout |
| app misses SSE event | reconnect with last event id; periodic full snapshot |
| duplicate create request | idempotency key |
| token leaked | rotate token in config and app settings |
| old app still writes iCloud commands | compatibility poll remains until decommission |
| API writes accepted but dispatch fails | task becomes `failed` with `dispatch_failed`, never stays queued forever |

## 18. Pros And Cons

### 18.1 Pros

1. truthful status: phone reads runtime state, not file-sync projection
2. canonical ids: no optimistic ID drift
3. clear failure semantics: failed/timeout/blocked are explicit
4. better recovery: event log can replay state
5. faster UX: LAN/Tailscale API beats iCloud sync
6. better debugging: every transition is logged
7. real cancel/retry: API can signal worker and update DB
8. less iOS file-provider weirdness
9. easier operator dashboard
10. transport extensibility: later web, CLI, Telegram, etc. can use same API

### 18.2 Cons

1. phone requires network route to Mac for live control
2. Tailscale or equivalent becomes an operational dependency
3. API security matters much more
4. background updates still constrained by iOS background limits
5. more backend code to maintain
6. Postgres migrations and backup discipline are required
7. old iCloud fallback must be maintained during transition
8. if Mac is off, submissions cannot be processed immediately

### 18.3 Net Assessment

The tradeoff is worth it because Mira is a control system, not just a document sync system. The current system optimizes for zero infrastructure but pays with false task state, delayed failure visibility, and hard-to-debug drift. The API approach introduces network/auth complexity, but those are explicit and observable. iCloud's failure modes are implicit and often invisible.

## 19. Deployment Plan

### 19.1 Mac

1. add launchd service for API if not already stable
2. bind to Tailscale IP or `0.0.0.0` with token required
3. configure token in `config.yml`
4. verify `GET /api/heartbeat` from iPhone
5. verify logs and restart behavior

### 19.2 iPhone

1. settings screen accepts server URL
2. settings screen accepts token
3. test connection button
4. select profile from API
5. enable API read mode
6. enable API write mode
7. keep iCloud fallback toggle visible during migration

### 19.3 Rollout Sequence

1. Mac API read-only mode
2. app API read mode on one device
3. app API write mode for low-risk tasks
4. all task writes API-first
5. runtime DB canonical mode
6. disable iCloud active command path

## 20. Rollback Plan

Rollback must be feature-flag based, not a git panic.

Levels:

1. **SSE rollback**: disable SSE, use polling API.
2. **API write rollback**: app returns to iCloud command writer.
3. **API read rollback**: app returns to iCloud manifest/items.
4. **runtime DB rollback**: TaskManager JSON status becomes canonical again.
5. **full rollback**: old iCloud bridge path restored.

Data rollback:

1. DB is additive during early phases
2. compatibility export preserves bridge items
3. `result.json` and artifacts remain unchanged
4. never delete bridge files until Phase 7 is stable

## 21. Open Questions

1. Should `blocked` be terminal in UI or grouped with `needs-input`?
2. ~~Should feed/background items live in the same `tasks` table or a separate `items` table?~~ → **Promoted to §25.3 (must resolve before Phase 1).**
3. Should health export uploads be stored in DB as blobs or files referenced by DB?
4. ~~Should app support offline draft queue, and if yes, should it sync through API only when reachable?~~ → **Promoted to §25.1 (must resolve before Phase 3); design in §26.**
5. Should API bind only to Tailscale IP instead of all LAN interfaces?
6. How long should `task_events` be retained before compaction? (suggested initial: keep 30 days, compact older to one row per task with summary)
7. ~~Should old bridge item JSON export be always-on until an app version gate says safe?~~ → **Resolved: see sunset schedule in §30.**
8. Does the iOS background-update regression need APNs push, or is "fresh on foreground only" acceptable? → **See §25.2 / §27.**

## 22. Suggested Implementation Order

The shortest useful sequence:

1. Phase 0 bridge stabilizers
2. Postgres schema + repository
3. read-only `/tasks` endpoint
4. Swift `MiraAPIClient`
5. API snapshot read mode
6. API create/reply write mode
7. TaskManager DB writes
8. worker heartbeat DB writes
9. SSE events
10. todo/health/artifact cleanup

Do not begin with SSE. It is not the source-of-truth fix. The source-of-truth fix is canonical DB state and direct API writes.

## 23. Acceptance Criteria

The refactor is complete when all are true:

1. A new task submitted from the phone returns a canonical server task id.
2. The app can show task status with iCloud Drive disabled.
3. Worker crash appears as `failed` or `timeout` within the configured detection window.
4. Unknown worker status cannot leave an item `working`.
5. Reply, retry, cancel, pin, archive work through API.
6. App reconnect after sleep catches up from DB events or snapshot.
7. Operator dashboard shows active/stale/failed tasks from DB.
8. iCloud command folder is not used in normal operation.
9. Artifact browsing still works.
10. Rollback to iCloud fallback has been tested before decommission.
11. Phone-submitted tasks while Mac is asleep arrive intact when Mac wakes (offline draft queue, §26).
12. APNs push delivers terminal-state updates to backgrounded app, OR the regression is accepted with explicit user-visible "loading…" on cold launch (§27).
13. All `*_compat_*` / `*_fallback_*` flags have hit their sunset dates per §30, or have explicit time-bounded extensions logged.

## 24. Phase Checkpoints (Verification Gates)

Each phase has a hard gate. Do not advance until every box is checked. Gates are stricter than success criteria — they include drift, regression, and operational signals, not just feature work.

### 24.0 Phase 0 → Phase 1

- [ ] All five §12.0 stabilizers shipped in production for ≥3 days
- [ ] No new "stuck working" incidents in `logs/` for 72h
- [ ] Phone optimistic id matches server task id for 100% of new submissions (verify by sampling 20 tasks)
- [ ] `paused_horizon_limit` task transitions to `needs-input` and is resumable via reply
- [ ] Synthetic unknown internal status verified: app surfaces `failed`, never `working`
- [ ] LAN write without token returns 401 (and loopback-without-token still works if configured)
- [ ] Self-audit (§16.3) shows zero "stuck working with no active task" rows for 24h

### 24.1 Phase 1 → Phase 2

- [ ] §25.3 (feed table decision) resolved and reflected in schema
- [ ] Migration runner is idempotent: run twice on same files → byte-identical DB
- [ ] DB import matches existing JSON state row-for-row for 1 week of historical data
- [ ] `GET /api/{user}/tasks` returns the same id set as the existing manifest endpoint for 24h with no diff
- [ ] DB can be deleted and rebuilt from files; result identical
- [ ] All §15.1 unit tests pass
- [ ] **DB has no writers other than the importer** — verified by code search for `INSERT`/`UPDATE` outside `lib/control/repository.py` importer module
- [ ] No SSE, no API writes — read endpoints only

### 24.2 Phase 2 → Phase 3

- [ ] App connects via API on Tailscale and LAN (separate manual tests)
- [ ] App shows distinct error states: "no network", "API unreachable", "API auth failed", "iCloud fallback active"
- [ ] Token UI works in Settings; Keychain storage verified by deleting app and reinstalling
- [ ] iCloud read fallback toggle works and is visibly indicated in UI
- [ ] **§25.1 offline draft queue spec approved and implemented** — without this, do not proceed
- [ ] Manual test: phone on cellular, Mac asleep → submit task → wake Mac → task arrives without duplicate
- [ ] Snapshot fetch latency under 1s on Tailscale, under 300ms on LAN
- [ ] App home view renders entirely from API; iCloud manifest read disabled in this mode

### 24.3 Phase 3 → Phase 4

- [ ] API create returns canonical id; phone reconciles optimistic id within 1s
- [ ] Idempotency stress test: 100 retries of same `client_request_id` → exactly 1 task
- [ ] Cancel actually terminates worker process (verify by `ps` showing PID gone within `cancel_grace_seconds + 2`)
- [ ] Cancel grace path: SIGTERM ignored → SIGKILL within window
- [ ] Cancel-unverified path produces `failed` with `failure_class=cancel_unverified`, not `cancelled`
- [ ] Reply on terminal task reopens correctly with same task id
- [ ] Compat export still writes bridge JSON; old app build still functional
- [ ] No regression in stuck-task counts vs. Phase 0 baseline

### 24.4 Phase 4 → Phase 5

- [ ] Killing a worker process (`kill -9`) shows `failed` in app within 60s
- [ ] Stale heartbeat (no update for `stale_threshold_seconds`) shows `timeout` within window
- [ ] Worker exits without `result.json` → app shows `failed` with `failure_class=missing_result`
- [ ] DB write failure on terminal aborts projection (test by making DB read-only mid-task)
- [ ] `data/tasks/status.json` and DB agree for 24h continuous operation
- [ ] Concurrent task transitions (5+ workers) do not exhaust the connection pool or block long transactions
- [ ] Heartbeat ingest path (Option B from §10.8, if chosen) keeps heartbeat lag under 30s p95
- [ ] Self-audit shows zero "terminal task missing terminal event" rows

### 24.5 Phase 5 → Phase 6

- [ ] SSE delivers events within 2s p95 of DB write under normal load
- [ ] Reconnect with `since=N` replays without gaps; verified by simulating disconnect mid-burst
- [ ] Polling fallback kicks in within 30s of SSE failure; user-visible only as a small badge
- [ ] Background app refresh uses snapshot only (no SSE attempted) — verified by network trace
- [ ] Event gap response (`snapshot_required`) triggers full refresh and recovers
- [ ] **§25.2 push notification path either prototyped (Phase 5.5 active) or regression formally accepted in writing** — pick one, document the choice

### 24.6 Phase 6 → Phase 7

- [ ] All `TodoStore` writes go through API (search for `todos.json` writes outside the API → none)
- [ ] Health summary read works without iCloud
- [ ] Health export upload via API succeeds for 1 week of daily exports
- [ ] Library/artifact browsing works through API
- [ ] iCloud fallback toggleable but app functions fully without it for 7 days
- [ ] No regressions in artifact rendering (images, audio playback)

### 24.7 Phase 7 (Decommission)

- [ ] Self-audit reports zero active iCloud command processing for 7 days
- [ ] Manifest no longer written by runtime
- [ ] Bridge file directory `Mira-Bridge/users/*/commands` can be moved to a holding location; agent unaffected for 24h
- [ ] Operator dashboard shows clean DB-only path
- [ ] Compat export sunset countdown started (§30)

## 25. Critical Risks To Resolve Before Phase 1 / Phase 3

Promoted from §21 Open Questions. These are not "later" — they are blockers.

### 25.1 Mac-Asleep Submission Path

**Status: must resolve before Phase 3 ships, otherwise UX regresses.**

Today, when the Mac is asleep, the iPhone can submit a task: it writes a command file to iCloud, the file syncs eventually, and the Mac processes it on wake. The new API design breaks this flow because `POST /tasks` requires a reachable server. WA's usage pattern frequently includes Mac-asleep periods (mobile, traveling, overnight). Losing this property without replacement is a real UX regression, not an edge case.

**Decision required (before Phase 3):**

- **Option A — Phone-side draft queue (recommended, designed in §26).** Phone keeps unsent tasks in local store, retries on reachability. Idempotency key prevents duplicates. UI shows "queued — Mac offline" badge.
- **Option B — Continue iCloud command file write as fallback.** Phone tries API, on failure writes iCloud command file. Server reconciles iCloud commands as before. Cost: keeps iCloud command path alive longer; defeats the point of the refactor.
- **Option C — Push-to-wake.** Phone POST → APNs push wakes Mac via wake-on-network. Requires Mac plugged in / WoN configured. Out of scope for this refactor.

**Recommendation: Option A.** Spec must be approved before Phase 3 ships; see §26 for the design.

### 25.2 iOS Background Update Regression

**Status: accept the regression OR design APNs push (Phase 5.5).**

Current behavior: iCloud delivers item updates into the iPhone's local cache while the app is backgrounded or killed. User opens the app, sees current state instantly with no loading.

New behavior: SSE only works while app is foregrounded. iOS BackgroundAppRefresh runs briefly at iOS-controlled intervals (typically tens of minutes to hours, not deterministic). Without push notifications, users may see stale state when opening the app and wait for a snapshot fetch.

**Decision required (before Phase 5):**

- Accept regression: document explicitly that "open app → brief loading state → fresh data" is the new norm. Keep an offline cached display while snapshot fetches.
- OR add APNs push for state changes: on every terminal event, server pushes silent notification → iOS triggers background refresh → snapshot fetched. Design in §27.

**Recommendation: APNs push as Phase 5.5.** Without it, the app feels less alive than today's iCloud-backed version. Design and prototype before §24.5 gate.

### 25.3 Feed Items Schema Decision

**Status: blocks Phase 1 schema migration.**

The schema in §8.3 has `tasks.type IN ('request', 'discussion', 'feed')`, implying feed items live in the same table. But feed items have a different lifecycle:

- no worker, no dispatch, no heartbeat
- no terminal status — they are read-or-archived
- generated in batches by background jobs (explore, journal, sparks)
- much higher volume — could be 10x tasks
- no `attempt_count`, `max_attempts`, `worker_pid`, `workspace`, `failure_class`, `error_code`, `retryable` (most of `tasks` columns are dead)

**Decision required (before Phase 1 migration is written):**

- **Option A — Single `tasks` table** (current schema as drafted)
  - Pro: one event stream, one query path, one Swift model
  - Con: ~13 columns are NULL for every feed row
  - Con: indexes mix high-cardinality (worker_pid) and absent (feed) values
  - Con: "active tasks" query has to filter by type
  - Con: feed retention/compaction policy will differ from task policy and the schema doesn't model it
- **Option B — Separate `feed_items` table with shared `messages` and `events` shape**
  - Pro: each table has clean columns
  - Pro: retention policy distinct (compact feed_items aggressively; keep tasks long)
  - Con: API and event stream must handle two tables
  - Con: Swift `ItemStore` needs to reconcile two snapshot sources
  - Con: more code, more migrations later

**Recommendation: Option B.** Feed items are read-stream content, not control-plane state. Mixing them masks task table operational metrics. The "extra code" cost is small relative to the schema-evolution cost of the wrong choice.

If Option A wins, document why, mark the dead columns as nullable with comments, and add a check constraint that feed-type rows do not populate worker fields.

### 25.4 Process Model Change (See §28)

The control plane requires a long-lived API server, which is a process model change from today's launchd-every-30s pattern. This is a real operational shift (new plist, new restart semantics, new logs to monitor). Worth calling out as a risk because:

- KeepAlive=true processes can hot-loop on bad config and burn battery
- API process and agent process now share Postgres — schema isolation, transaction scope, and connection limits matter
- Crash diagnostics need StandardErrorPath; today's launchd-tick errors land in a different log

Mitigation: see §28.

### 25.5 Compatibility Code Becomes Permanent (See §30)

`bridge_compat_export` and `icloud_command_fallback` default to `true`. Without an explicit sunset, they live forever and the iCloud writes never go away. Mitigation: §30 sunset schedule with hard dates.

### 25.6 Worker Process-Group Hygiene (Cancel Correctness)

Documented in §9.7 prerequisite. If workers aren't spawned with `start_new_session=True`, cancel either fails or accidentally signals the agent. This is easy to miss and a startup assertion is required.

## 26. Offline Draft Queue Design (Phone-Side)

Resolves §25.1. Required for Phase 3.

### 26.1 Phone-side storage

```swift
// MiraApp local SQLite (separate from iCloud-backed cache)
struct OfflineDraft {
    let id: UUID                  // becomes client_request_id on submit
    let payload: CreateTaskBody   // type, title, content, quick, tags
    let createdAt: Date
    let attemptCount: Int
    let lastAttemptAt: Date?
    let lastError: String?
    let state: DraftState         // pending | syncing | synced | failed
}
```

Storage location: app sandbox SQLite. Not iCloud. Survives app restart but not device reset.

### 26.2 Sync flow

1. User submits task → write to local drafts table → optimistic UI insert with the draft id
2. Reachability monitor watches API endpoint (HEAD `/api/heartbeat` with short timeout)
3. On reachability change OR every 60s while drafts pending:
   - Drain pending drafts in `createdAt` order
   - `POST /tasks` with `client_request_id = draft.id`
   - on 2xx → mark synced, replace optimistic item with server response (preserve scroll/UI position)
   - on 5xx / network → increment `attemptCount`, exponential backoff (max 1h)
   - on 4xx auth → mark failed, surface "auth error — open Settings"
   - on 4xx validation → mark failed, surface error inline on the optimistic item
4. UI shows draft items with a clear "Mac offline — will send when reachable" badge
5. Once synced, drafts table row can be retained for 24h then purged

### 26.3 Server side

- Idempotency key already in §9.5 — must store key for ≥24h regardless of whether `client_request_id` arrived from foreground or background queue
- Returned canonical id maps phone draft → server task
- Phone replaces local optimistic id with server id once synced; SSE events / snapshot will use server id thereafter

### 26.4 Edge cases

- Phone restarts mid-sync: drafts table persists, retry on launch
- Multiple drafts queued: flush in `createdAt` order — preserves user intent
- Server reset between submission and sync: idempotency key ensures no duplicate
- User cancels a draft before it syncs: delete from local drafts table; do not POST cancel
- User edits a draft before it syncs: allowed only while `state == pending`; updates `payload` and `createdAt`
- Auth token rotated mid-queue: pause draining, prompt for new token, resume

### 26.5 What this is NOT

- Not a general-purpose offline mode. Reads still require API reachability (or iCloud read fallback in Phases 1-6).
- Not a write-anywhere queue — only `POST /tasks` and `POST /tasks/{id}/reply` are queueable. Cancel, retry, archive require a reachable server (they affect server-side state and should not be optimistic).

## 27. iOS Background Update Strategy and Push Notifications

Resolves §25.2. Required for Phase 5.5 OR explicit acceptance of regression.

### 27.1 Today's baseline (what we lose)

- iCloud sync delivers item JSON updates to phone storage even when app is killed
- User opens app → reads local cache → sees current state immediately
- Latency: minutes (iCloud-bound) but invisible to user since it happens in background

### 27.2 Phase 1-5 behavior (without push)

- App foreground: SSE delivers updates within 2s
- App background: iOS BackgroundAppRefresh runs briefly, calls snapshot fetch, exits
  - iOS controls cadence — typically 1-4 hours, may be much longer
- App opened from cold: last cached state shown → snapshot fetch in flight → UI updates when fetch completes
- Net result: regression on cold-launch freshness vs current iCloud delivery

### 27.3 Phase 5.5 — APNs Push (recommended)

Add a wake-up push channel:

1. Server side
   - APNs key configured in `config.yml` (production + sandbox)
   - New endpoint: `POST /api/{user}/push/register {device_token, environment}`
   - Stored in DB table `push_tokens` (one row per device)
   - On terminal events (`done`, `failed`, `needs-input`, `timeout`, `cancelled`), enqueue silent push
   - Throttle: max 1 push per task per 30s; coalesce bursts (multiple terminal events within 30s → one push)
   - Push payload: `{aps: {content-available: 1}, task_id, status, last_event_id}`
2. Phone side
   - `UNUserNotificationCenter` registration on app launch (after token stored)
   - `application(_:didReceiveRemoteNotification:fetchCompletionHandler:)` handler
   - In handler: snapshot fetch via background URLSession (≤30s window)
   - ItemStore updates from snapshot
   - Optional: foreground rich notification if user enabled "notify when tasks finish"
3. Settings UI
   - Toggle: "Notify me when Mira tasks finish" (default off; explicit opt-in for noise reasons)
   - Per-status filter (advanced): notify only on `failed` / `needs-input`?

### 27.4 What this does not solve

- Push-to-wake-Mac (a different problem; see §25.1 / §26)
- Foreground rich content (would require non-silent push and a different opt-in)
- iOS guarantees delivery — APNs is best-effort. Snapshot pull on app open remains the canonical recovery path.

### 27.5 Decision point at §24.5 gate

Before Phase 5 → Phase 6 advance, formally choose:

- **A — Phase 5.5 APNs push delivered** → §24.5 gate satisfied
- **B — Regression accepted** → document in §23 acceptance, add user-facing "loading…" affordance on cold launch, monitor app-open feedback

Do not silently skip the choice.

## 28. Process Model

Resolves §25.4.

### 28.1 Two-process model

**`mira-api`** (new LaunchAgent, KeepAlive=true)
- FastAPI server bound to Tailscale interface or `127.0.0.1`
- SSE event publisher
- Postgres reader/writer (via `lib/control/`)
- Heartbeat-file ingest thread (if §10.8 Option B chosen)
- Push notification dispatcher (Phase 5.5+)
- No agent logic, no LLM calls, no file dispatch

**`mira-agent`** (existing LaunchAgent, run-on-interval, every 30s)
- Today's `core.py` mostly unchanged in shape
- Reads pending tasks from DB (Phase 4+) or bridge files (Phase ≤3)
- Dispatches workers
- Collects results, writes terminal DB transitions
- Background jobs (explore, journal, etc.)

Communication: shared Postgres schema `mira_control`. Both processes use short transactions; the API should keep a small pool and the agent should avoid holding connections across LLM/tool execution.

### 28.2 Configuration

New plist: `~/Library/LaunchAgents/com.angwei.mira-api.plist`

```xml
<key>Label</key><string>com.angwei.mira-api</string>
<key>ProgramArguments</key>
<array>
  <string>/Users/angwei/Sandbox/Mira/.venv/bin/python</string>
  <string>-m</string><string>uvicorn</string>
  <string>web.server:app</string>
  <string>--host</string><string>127.0.0.1</string>  <!-- or Tailscale IP -->
  <string>--port</string><string>8384</string>
</array>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>/Users/angwei/Sandbox/Mira/logs/mira-api.out.log</string>
<key>StandardErrorPath</key><string>/Users/angwei/Sandbox/Mira/logs/mira-api.err.log</string>
<key>WorkingDirectory</key><string>/Users/angwei/Sandbox/Mira</string>
```

ThrottleInterval=10 prevents hot-loop on bind failures (e.g., port collision).

### 28.3 Why two processes (not one always-on agent)

- **Crash isolation**: API crash doesn't kill task processing; agent crash doesn't kill the SSE stream
- **Different restart semantics**: API auto-restarts on bind/runtime failure; agent runs on schedule
- **Easier rollback**: disable API plist, agent continues with legacy bridge in Phase 0-2 mode
- **Resource shape**: API is small, low-CPU, long-lived; agent is bursty, runs heavy LLM calls, exits

### 28.4 Worker subprocess discipline

Workers spawned by `task_manager.py`. Required prerequisite (also noted in §9.7):

- `subprocess.Popen(..., start_new_session=True)` — POSIX `setsid`
- Cancel sends `SIGTERM` to `-pid` (negative PID = process group)
- Startup assertion in `task_manager`: log a warning and refuse cancel-by-pgrp if any spawned worker shares pgid with parent
- Track exit; only mark `cancelled` after `os.waitpid` confirms exit
- Grace period `cancel_grace_seconds` (default 10s) before SIGKILL escalation

### 28.5 Operational checks

- `launchctl list | grep mira` shows both agents
- `lsof -iTCP:8384` confirms api binding
- API log rotation (logrotate or built-in) prevents log file growth
- Control-plane data lives in Postgres schema `mira_control` — include it in the existing database backup/restore path

## 29. Apple Notes Bridge Scope

Resolves a gap in the original plan. `agents/super/notes_inbox/` and `notes_outbox/` use the same Apple-Notes-as-bridge pattern as iCloud commands but a different transport (AppleScript / EventKit). This refactor does **not** migrate them.

### 29.1 Rationale

- Different transport (AppleScript / EventKit, not file sync) — different failure modes
- Different user surface (Apple Notes app, not MiraApp)
- Lower failure rate in practice — no observed user complaints comparable to iCloud bridge issues
- Refactoring would 2x scope without addressing the named pain

### 29.2 Constraint during this refactor

Existing Notes inbox/outbox paths must continue to work through Phase 7. Do not break them with shared module changes (e.g., if `lib/bridge.py` is refactored, ensure `lib/notes_bridge.py` is unaffected or updated in lockstep).

### 29.3 Future work (out of scope here)

Notes inbox could route through `POST /api/{user}/tasks` once API is stable, with the AppleScript hook acting as an API client (curl from a launchd job or in-process Python). This becomes attractive once:

- API is canonical (Phase 4+)
- Idempotency keys are reliable
- Notes bridge starts showing similar drift symptoms (it currently doesn't)

If this work happens, it should be a separate plan document, not folded into this refactor.

## 30. Compatibility Sunset Schedule

Resolves §25.5. Without explicit sunset dates, "temporary" compat code becomes permanent and the iCloud writes never go away — defeating the refactor.

### 30.1 Hard rules

- Every `*_compat_*` and `*_fallback_*` flag has a sunset date logged here
- 30 days minimum between Phase 7 ship and default-flip
- 60 days minimum between Phase 7 ship and code removal
- During the sunset window, monitor the relevant log lines daily; rate must reach zero before each step

### 30.2 Schedule (dates set when Phase 7 ships)

Let `T = Phase 7 production-ship date`.

| Flag | Default at T | Default flip | Code removal | Monitoring signal |
|---|---|---|---|---|
| `bridge_compat_export` | true | T+30d (false) | T+60d | "compat export wrote bridge JSON" log rate |
| `icloud_command_fallback` | true | T+30d (false) | T+60d | "iCloud command processed (compat)" log rate |
| `manifest_compat_export` | true | T+30d (false) | T+60d | "manifest written (compat)" log rate |
| iCloud manifest read in Swift | enabled | T+45d (off) | T+90d (Swift code) | app analytics: `BridgeConfig.connectionMode == icloudFallback` count |
| `BridgeConfig.folderURL` UI | shown | T+30d (hidden behind dev flag) | T+90d | settings telemetry |

### 30.3 Conditions to advance each step

Default flip (T+30d) requires all of:

- Compat write-rate is zero for 7 consecutive days
- Self-audit (§16.3) reports zero "iCloud command files processed while fallback disabled" anomalies (test with default off in dev for 24h before production flip)
- Manual rollback drill performed and documented (revert to fallback, verify, re-enable)

Code removal (T+60d) requires all of:

- 30 days post-flip with no rollback events
- No support requests citing iCloud-related issues during the window
- App-version min-gate set: only app builds ≥ post-Phase-7 build are supported. Older app builds get a "please update" wall, not silent breakage.

### 30.4 Escalation rule

If a sunset condition fails, the flag's removal slips by 30 days and the cause is logged here as an extension entry:

```
| flag | original removal | extension reason | new removal |
```

Only two extensions allowed before the refactor is declared incomplete and re-planned.
