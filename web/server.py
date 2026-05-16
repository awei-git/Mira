"""Mira Web GUI — lightweight FastAPI server reading from bridge files."""

import asyncio
import atexit
import collections
import json
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import compare_digest
from typing import Optional
from urllib.parse import quote

from enum import Enum

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Add Mira modules to path. launchd starts this process from web/, so keep both
# shared lib modules and supervisor runtime modules importable.
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "super"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from config import (
    BRIDGE_COMPAT_EXPORT_ENABLED,
    CONTROL_API_WRITES_ENABLED,
    CONTROL_RUNTIME_DB_ENABLED,
    CONTROL_SSE_ENABLED,
    ICLOUD_COMMAND_FALLBACK_ENABLED,
    MDNS_ADVERTISE_ENABLED,
    MIRA_DIR,
    TASKS_DIR,
    WEBGUI_ALLOW_LAN_WITHOUT_TOKEN,
    WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN,
    WEBGUI_HOST,
    WEBGUI_HTTPS_ENABLED,
    WEBGUI_PORT,
    WEBGUI_TLS_CERT_FILE,
    WEBGUI_TLS_KEY_FILE,
    WEBGUI_TOKEN,
    get_known_user_ids,
    get_user_config,
    is_known_user,
)

BRIDGE = MIRA_DIR
USERS_DIR = BRIDGE / "users"
WEB_DIR = Path(__file__).parent
WEB_ICON = WEB_DIR / "mira-icon.png"
BACKEND_DASHBOARD_ASSETS = WEB_DIR / "backend_dashboard"

app = FastAPI(title="Mira", docs_url=None, redoc_url=None)
_mdns_process: subprocess.Popen | None = None
_JSON_FILE_LOCKS: collections.defaultdict[str, threading.RLock] = collections.defaultdict(threading.RLock)
app.mount("/backend-assets", StaticFiles(directory=BACKEND_DASHBOARD_ASSETS), name="backend-dashboard-assets")

# ---------------------------------------------------------------------------
# CORS — allow the web GUI and local dev origins
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{WEBGUI_PORT}",
        f"http://127.0.0.1:{WEBGUI_PORT}",
        f"https://localhost:{WEBGUI_PORT}",
        f"https://127.0.0.1:{WEBGUI_PORT}",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "X-Mira-Token", "Content-Type"],
)


@app.on_event("startup")
def _verify_control_db_on_startup() -> None:
    """Fail fast when the canonical control DB is required but unavailable."""
    if not (CONTROL_API_WRITES_ENABLED or CONTROL_RUNTIME_DB_ENABLED or CONTROL_SSE_ENABLED):
        return
    try:
        from migrations.runner import apply_migrations

        apply_migrations()
    except Exception as exc:
        raise RuntimeError(f"Control DB unavailable at startup: {exc}") from exc
    _start_mdns_advertisement()


def _start_mdns_advertisement() -> None:
    """Advertise the local API as `_mira._tcp` for the iOS app."""
    global _mdns_process
    if not MDNS_ADVERTISE_ENABLED or _mdns_process is not None:
        return
    dns_sd = shutil.which("dns-sd")
    if not dns_sd:
        return
    try:
        subprocess.run(
            ["pkill", "-f", f"dns-sd -R Mira _mira._tcp local {WEBGUI_PORT}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        scheme_txt = "scheme=https" if WEBGUI_HTTPS_ENABLED else "scheme=http"
        _mdns_process = subprocess.Popen(
            [
                dns_sd,
                "-R",
                "Mira",
                "_mira._tcp",
                "local",
                str(WEBGUI_PORT),
                "path=/api/heartbeat",
                scheme_txt,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        _mdns_process = None


def _stop_mdns_advertisement() -> None:
    global _mdns_process
    if _mdns_process is None:
        return
    _mdns_process.terminate()
    _mdns_process = None


atexit.register(_stop_mdns_advertisement)

# ---------------------------------------------------------------------------
# Rate limiting — simple in-memory per-IP limiter.
#
# The app legitimately does bursty list/detail polling when it reconnects.
# Keep read flooding from consuming the write lane; otherwise a detail refresh
# can make a user reply disappear behind 429s.
# ---------------------------------------------------------------------------
_READ_RATE_LIMIT = 600  # requests per window
_WRITE_RATE_LIMIT = 120  # requests per window
_RATE_WINDOW = 60  # window in seconds
_rate_buckets: dict[str, collections.deque] = {}


def _check_rate_limit(bucket_key: str, *, limit: int, window: int = _RATE_WINDOW) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(bucket_key, collections.deque())
    # Purge old entries
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def _is_rate_limit_exempt(path: str) -> bool:
    """Keep app liveness/event polling available during a read storm."""
    if path == "/api/heartbeat":
        return True
    parts = [part for part in path.split("/") if part]
    return len(parts) == 3 and parts[0] == "api" and parts[2] in {"events", "manifest"}


def _rate_limit_lane(method: str) -> tuple[str, int]:
    if method.upper() in {"POST", "PATCH", "DELETE"}:
        return "write", _WRITE_RATE_LIMIT
    return "read", _READ_RATE_LIMIT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_after_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _json_file_lock(path: Path) -> threading.RLock:
    return _JSON_FILE_LOCKS[str(path.resolve())]


def _atomic_write(path: Path, data):
    with _json_file_lock(path):
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _user_dir(user_id: str) -> Path:
    return USERS_DIR / user_id


def _item_path(user_id: str, item_id: str) -> Path:
    return _user_dir(user_id) / "items" / f"{item_id}.json"


def _load_item_or_404(user_id: str, item_id: str) -> tuple[Path, dict]:
    item_path = _item_path(user_id, item_id)
    item = _read_json(item_path)
    if not item:
        raise HTTPException(404, "Item not found")
    return item_path, item


def _client_host(request: Request) -> str:
    return (request.client.host if request.client else "").strip()


def _is_loopback_client(host: str) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_lan_client(host: str) -> bool:
    """Check if host is a private/LAN IP (RFC 1918)."""
    import ipaddress

    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private and not addr.is_loopback
    except ValueError:
        return False


def _extract_webgui_token(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("x-mira-token", "").strip()
    if header:
        return header
    return request.query_params.get("token", "").strip()


def _require_api_access(request: Request):
    host = _client_host(request)
    if WEBGUI_TOKEN:
        token = _extract_webgui_token(request)
        if token and compare_digest(token, WEBGUI_TOKEN):
            return
        raise HTTPException(401, "Missing or invalid Mira Web token")
    if WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN and _is_loopback_client(host):
        return
    if WEBGUI_ALLOW_LAN_WITHOUT_TOKEN and _is_lan_client(host):
        return
    raise HTTPException(403, "Mira Web API is limited to loopback unless a token is configured")


def _require_user_access(request: Request, user_id: str):
    if not is_known_user(user_id):
        raise HTTPException(404, "Unknown user")
    _require_api_access(request)


@app.middleware("http")
async def _api_auth_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    # Rate limiting
    client_ip = _client_host(request) or "unknown"
    if not _is_rate_limit_exempt(path):
        lane, limit = _rate_limit_lane(request.method)
        if not _check_rate_limit(f"{client_ip}:{lane}", limit=limit):
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests", "lane": lane},
                headers={"Retry-After": str(_RATE_WINDOW)},
            )
    parts = [part for part in path.split("/") if part]
    try:
        if len(parts) >= 3:
            _require_user_access(request, parts[1])
        else:
            _require_api_access(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


# ---------------------------------------------------------------------------
# API — Read
# ---------------------------------------------------------------------------


@app.get("/api/profiles")
def get_profiles():
    data = _read_json(BRIDGE / "profiles.json")
    known = set(get_known_user_ids())
    if data and isinstance(data, dict):
        profiles = [p for p in data.get("profiles", []) if isinstance(p, dict) and p.get("id") in known]
        if profiles:
            return {"profiles": profiles}
    profiles = []
    for user_id in get_known_user_ids():
        cfg = get_user_config(user_id)
        profiles.append(
            {
                "id": user_id,
                "display_name": cfg.get("display_name", user_id),
                "agent_name": "Mira",
            }
        )
    return {"profiles": profiles}


@app.get("/api/heartbeat")
def get_heartbeat():
    data = _read_json(BRIDGE / "heartbeat.json")
    if not isinstance(data, dict):
        data = {"timestamp": "", "status": "offline"}

    agent_status = data.get("agent_status")
    try:
        agent_status = _task_manager().get_status_summary()
    except Exception:
        if not isinstance(agent_status, dict):
            agent_status = {}

    if agent_status:
        data["agent_status"] = agent_status
        data["busy"] = bool(agent_status.get("busy"))
        data["active_count"] = int(agent_status.get("active_count") or 0)
        if "active_tasks" in agent_status:
            data["active_tasks"] = agent_status["active_tasks"]
        if "last_completed" in agent_status:
            data["last_completed"] = agent_status["last_completed"]
    return data


# ---------------------------------------------------------------------------
# API — Todos
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TodoStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"


class NewTodo(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    priority: Priority = Priority.medium
    tags: list[str] = Field(default=[], max_length=20)


class UpdateTodo(BaseModel):
    status: TodoStatus | None = None
    priority: Priority | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)


class Followup(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    source: str = Field(default="user", pattern=r"^(user|agent)$")


class V2StatusCard(BaseModel):
    card_type: str = Field(..., pattern=r"^(daily_status|decision|sunday_gate|drift_alert|build_summary)$")
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=4000)
    reply_options: list[str] = Field(default=[], max_length=6)
    default_action: str = Field(default="WAIT", max_length=80)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class V2StatusReply(BaseModel):
    reply: str = Field(..., min_length=1, max_length=200)


class ModelAssignmentUpdate(BaseModel):
    model: str = Field(..., min_length=1, max_length=80)
    token_budget: int = Field(default=0, ge=0, le=1_000_000)


@app.get("/api/{user_id}/todos")
def get_todos(user_id: str):
    path = _user_dir(user_id) / "todos.json"
    todos = _read_json(path) or []
    # Migrate legacy 'response' → 'followups'
    for t in todos:
        if "followups" not in t:
            t["followups"] = []
            if t.get("response"):
                t["followups"].append(
                    {"content": t["response"], "source": "agent", "timestamp": t.get("updated_at", "")}
                )
        if "tags" not in t:
            t["tags"] = []
    return todos


@app.post("/api/{user_id}/todos")
def add_todo(user_id: str, todo: NewTodo):
    path = _user_dir(user_id) / "todos.json"
    with _json_file_lock(path):
        todos = _read_json(path) or []
        new = {
            "id": f"todo_{uuid.uuid4().hex[:8]}",
            "title": todo.title,
            "priority": todo.priority.value,
            "status": "pending",
            "tags": todo.tags,
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
            "followups": [],
        }
        todos.append(new)
        _atomic_write(path, todos)
        return new


@app.patch("/api/{user_id}/todos/{todo_id}")
def update_todo(user_id: str, todo_id: str, update: UpdateTodo):
    path = _user_dir(user_id) / "todos.json"
    with _json_file_lock(path):
        todos = _read_json(path) or []
        for t in todos:
            if t["id"] == todo_id:
                if update.status is not None:
                    t["status"] = update.status.value
                if update.priority is not None:
                    t["priority"] = update.priority.value
                if update.title is not None:
                    t["title"] = update.title
                t["updated_at"] = _utc_iso()
                _atomic_write(path, todos)
                return t
    raise HTTPException(404)


@app.post("/api/{user_id}/todos/{todo_id}/followup")
def add_followup(user_id: str, todo_id: str, fu: Followup):
    path = _user_dir(user_id) / "todos.json"
    with _json_file_lock(path):
        todos = _read_json(path) or []
        for t in todos:
            if t["id"] == todo_id:
                if "followups" not in t:
                    t["followups"] = []
                t["followups"].append({"content": fu.content, "source": fu.source, "timestamp": _utc_iso()})
                t["updated_at"] = _utc_iso()
                _atomic_write(path, todos)
                # Send command so Mira processes the followup
                if fu.source == "user":
                    cmd_id = uuid.uuid4().hex[:8]
                    cmd = {
                        "id": cmd_id,
                        "type": "todo_followup",
                        "timestamp": _utc_iso(),
                        "sender": user_id,
                        "todo_id": todo_id,
                        "content": fu.content,
                    }
                    cmd_dir = _user_dir(user_id) / "commands"
                    cmd_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)
                return t
    raise HTTPException(404)


@app.post("/api/{user_id}/todos/{todo_id}/done")
def complete_todo(user_id: str, todo_id: str):
    path = _user_dir(user_id) / "todos.json"
    with _json_file_lock(path):
        todos = _read_json(path) or []
        for t in todos:
            if t["id"] == todo_id:
                t["status"] = "done"
                t["updated_at"] = _utc_iso()
                _atomic_write(path, todos)
                return t
    raise HTTPException(404)


@app.delete("/api/{user_id}/todos/{todo_id}")
def delete_todo(user_id: str, todo_id: str):
    path = _user_dir(user_id) / "todos.json"
    with _json_file_lock(path):
        todos = [t for t in (_read_json(path) or []) if t["id"] != todo_id]
        _atomic_write(path, todos)
        return {"status": "deleted"}


@app.get("/api/{user_id}/manifest")
def get_manifest(user_id: str):
    data = _read_json(_user_dir(user_id) / "manifest.json")
    return data or {"updated_at": "", "items": []}


@app.get("/api/{user_id}/items")
def get_items(user_id: str):
    items_dir = _user_dir(user_id) / "items"
    if not items_dir.exists():
        return []
    items = []
    for path in sorted(items_dir.glob("*.json")):
        item = _read_json(path)
        if item:
            items.append(item)
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


@app.get("/api/{user_id}/items/{item_id}")
def get_item(user_id: str, item_id: str):
    item = _read_json(_user_dir(user_id) / "items" / f"{item_id}.json")
    if not item:
        raise HTTPException(404, "Item not found")
    return item


def _is_internal_liveness_item(item: dict) -> bool:
    item_id = str(item.get("id") or "")
    if item_id.startswith(("req_liveness_", "mira_liveness_", "output_stale_")):
        return True
    tags = {str(tag).lower() for tag in item.get("tags") or []}
    title = str(item.get("title") or "").lower()
    return "liveness" in tags and ("system" in tags or "stale" in title or "watchdog" in title)


@app.get("/api/{user_id}/tasks")
def get_tasks(
    user_id: str,
    include_archived: bool = False,
    include_internal: bool = False,
    limit: int = 200,
    messages_per_item: int = 20,
):
    """Return API-control-plane task projection.

    Phase 1 is intentionally read-only: it projects existing bridge item JSON
    and TaskManager status JSON into Postgres, then serves the app-compatible
    MiraItem shape from the control schema. Legacy files are read but not
    modified.
    """
    try:
        from control.db import transaction
        from control.repository import ControlRepository, sync_user_from_legacy

        sync_user_from_legacy(user_id, user_dir=_user_dir(user_id), task_status_file=TASKS_DIR / "status.json")
        with transaction() as conn:
            repo = ControlRepository(conn)
            items = repo.list_items(
                user_id,
                include_archived=include_archived,
                limit=max(1, min(limit, 500)),
                messages_per_item=max(1, min(messages_per_item, 50)),
            )
            if not include_internal:
                items = [item for item in items if not _is_internal_liveness_item(item)]
            last_event_id = repo.last_event_id(user_id)
        return {"items": items, "server_time": _utc_iso(), "last_event_id": last_event_id}
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc


@app.get("/api/{user_id}/tasks/{task_id}")
def get_task_detail(user_id: str, task_id: str, messages_per_item: int = 50):
    """Return one canonical task projection from the control plane."""
    try:
        from control.db import transaction
        from control.repository import ControlRepository, sync_user_from_legacy

        sync_user_from_legacy(user_id, user_dir=_user_dir(user_id), task_status_file=TASKS_DIR / "status.json")
        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.get_item(user_id, task_id, messages_per_item=max(1, min(messages_per_item, 100)))
        if not item:
            raise HTTPException(404, "Task not found")
        return {"item": item, "server_time": _utc_iso()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc


@app.get("/api/{user_id}/threads")
def get_threads(
    user_id: str,
    include_archived: bool = False,
    include_internal: bool = False,
    limit: int = 200,
    messages_per_item: int = 20,
):
    """Return app thread projections backed by canonical tasks/messages."""
    try:
        from control.db import transaction
        from control.repository import ControlRepository, sync_user_from_legacy

        sync_user_from_legacy(user_id, user_dir=_user_dir(user_id), task_status_file=TASKS_DIR / "status.json")
        with transaction() as conn:
            repo = ControlRepository(conn)
            threads = repo.list_items(
                user_id,
                include_archived=include_archived,
                limit=max(1, min(limit, 500)),
                messages_per_item=max(1, min(messages_per_item, 50)),
            )
            if not include_internal:
                threads = [item for item in threads if not _is_internal_liveness_item(item)]
        return {"threads": threads, "server_time": _utc_iso()}
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc


@app.get("/api/{user_id}/jobs")
def get_jobs_today(user_id: str):
    """Return today's scheduled job runs with status, model, token, cost details."""
    from datetime import date as _date

    today = _date.today().isoformat()

    # 1. Load job registry
    _agents_super = Path(__file__).resolve().parent.parent / "agents" / "super"
    for _p in [str(_agents_super), str(_agents_super / "runtime")]:
        if _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        from runtime.jobs import get_jobs as _get_jobs

        all_jobs = _get_jobs(enabled_only=False)
    except Exception:
        all_jobs = []

    # 2. Load agent state to check which jobs ran today
    from config import STATE_FILE, LOGS_DIR

    state = _read_json(STATE_FILE) or {}
    user_state = state.get("users", {}).get(user_id, {})

    # 3. Load today's usage log for per-agent token/cost breakdown
    logs_dir = LOGS_DIR
    usage_path = logs_dir / f"usage_{today}.jsonl"
    agent_usage: dict[str, dict] = {}
    if usage_path.exists():
        for line in usage_path.read_text(encoding="utf-8").strip().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            agent = r.get("agent", "unknown")
            if agent not in agent_usage:
                agent_usage[agent] = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "models": {}}
            agent_usage[agent]["calls"] += 1
            agent_usage[agent]["tokens"] += r.get("total_tokens", 0)
            agent_usage[agent]["cost_usd"] += r.get("cost_usd", 0.0)
            model = r.get("model", "unknown")
            if model not in agent_usage[agent]["models"]:
                agent_usage[agent]["models"][model] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            agent_usage[agent]["models"][model]["calls"] += 1
            agent_usage[agent]["models"][model]["tokens"] += r.get("total_tokens", 0)
            agent_usage[agent]["models"][model]["cost_usd"] += r.get("cost_usd", 0.0)

    # Round costs
    for v in agent_usage.values():
        v["cost_usd"] = round(v["cost_usd"], 4)
        for mv in v["models"].values():
            mv["cost_usd"] = round(mv["cost_usd"], 4)

    # 4. Parse today's dispatch log entries from main log
    main_log = logs_dir / f"{today}.log"
    dispatches: dict[str, list[str]] = {}  # job_name -> [timestamps]
    completions: dict[str, list[str]] = {}
    if main_log.exists():
        try:
            for line in main_log.read_text(encoding="utf-8", errors="replace").splitlines():
                if "dispatched (PID" in line:
                    # "Background 'explore-morning' dispatched (PID 12345)"
                    parts = line.split("'")
                    if len(parts) >= 2:
                        bg_name = parts[1]
                        ts = line[:19]  # "2026-04-10 07:00:01"
                        dispatches.setdefault(bg_name, []).append(ts)
                elif "complete" in line.lower() and ("[INFO]" in line):
                    for job in all_jobs:
                        if job.name in line.lower():
                            ts = line[:19]
                            completions.setdefault(job.name, []).append(ts)
                            break
        except OSError:
            pass

    # 5. Build per-job status
    jobs_out = []
    for job in all_jobs:
        # Determine state key for today
        state_key = job.state_key(today=today)
        # Check multiple patterns for "ran today" — search both global and per-user state
        ran_at = None
        candidates = [state_key, f"{job.name}_{today}"]
        # Cooldown-based jobs use "last_*" keys
        if job.state_key_pattern.startswith("last_"):
            candidates.append(job.state_key_pattern)
        for key in candidates:
            # Try per-user first, then global
            val = user_state.get(key) or state.get(key)
            if val and today in str(val):
                ran_at = val
                break
        # Special case: explore uses "explored_DATE_*" keys
        if not ran_at and job.name == "explore":
            for key, val in state.items():
                if key.startswith(f"explored_{today}"):
                    ran_at = val
                    break

        # Map job name to agent name(s) in usage log
        agent_map = {
            "explore": ["explore"],
            "journal": ["journal"],
            "reflect": ["reflect"],
            "research-cycle": ["research-cycle"],
            "research-log": ["research-log"],
            "analyst-pre": ["analyst"],
            "analyst-post": ["analyst"],
            "substack-comments": ["growth-cycle", "growth"],
            "substack-growth": ["growth-cycle", "growth"],
            "substack-notes": ["notes-cycle", "notes"],
            "writing-pipeline": ["writer", "writing-pipeline"],
            "autowrite-check": ["autowrite", "autowrite-check"],
            "skill-study": ["skill-study"],
            "idle-think": ["idle-think"],
            "spark-check": ["spark-check"],
            "daily-photo": ["daily-photo"],
            "daily-report": ["daily-report"],
            "zhesi": ["zhesi"],
            "soul-question": ["soul-question"],
            "daily-research": ["research", "daily-research"],
            "book-review": ["book-review"],
            "self-audit": ["self-audit"],
            "self-evolve": ["self-evolve"],
            "assessment": ["assess", "assessment"],
            "backlog-executor": ["backlog-executor"],
        }
        agent_keys = agent_map.get(job.name, [job.name])
        # Merge usage from all matching agent names
        usage: dict = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "models": {}}
        for ak in agent_keys:
            au = agent_usage.get(ak)
            if not au:
                continue
            usage["calls"] += au["calls"]
            usage["tokens"] += au["tokens"]
            usage["cost_usd"] += au["cost_usd"]
            for mk, mv in au["models"].items():
                if mk not in usage["models"]:
                    usage["models"][mk] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
                usage["models"][mk]["calls"] += mv["calls"]
                usage["models"][mk]["tokens"] += mv["tokens"]
                usage["models"][mk]["cost_usd"] += mv["cost_usd"]
        usage["cost_usd"] = round(usage["cost_usd"], 4)

        # Dispatch count from log — match bg_name_pattern to dispatched names
        dispatch_times = []
        # Build all possible prefixes to match
        job_prefixes = [job.name]
        # Handle bg_name_pattern like "analyst-{slot}" → match "analyst-"
        if job.bg_name_pattern != "{name}":
            base = job.bg_name_pattern.split("{")[0].rstrip("-")
            if base and base != job.name:
                job_prefixes.append(base)
        for bg_name, times in dispatches.items():
            for pfx in job_prefixes:
                if bg_name == pfx or bg_name.startswith(pfx + "-"):
                    dispatch_times.extend(times)
                    break

        # A job counts as "done" if it has a state key OR was dispatched today
        status = "done" if (ran_at or dispatch_times) else ("disabled" if not job.enabled else "pending")

        entry = {
            "name": job.name,
            "description": job.description,
            "trigger": job.trigger,
            "cooldown_hours": job.cooldown_hours,
            "window": f"{job.window_start or ''}:00-{job.window_end or ''}:00" if job.window_start is not None else "",
            "priority": job.priority,
            "enabled": job.enabled,
            "status": status,
            "ran_at": ran_at,
            "dispatch_count": len(dispatch_times),
            "dispatch_times": dispatch_times[-5:],  # last 5
            "usage": {
                "calls": usage.get("calls", 0),
                "tokens": usage.get("tokens", 0),
                "cost_usd": usage.get("cost_usd", 0.0),
                "models": usage.get("models", {}),
            },
        }
        jobs_out.append(entry)

    # 6. Usage summary totals
    total_cost = sum(v["cost_usd"] for v in agent_usage.values())
    total_tokens = sum(v["tokens"] for v in agent_usage.values())
    total_calls = sum(v["calls"] for v in agent_usage.values())

    return {
        "date": today,
        "jobs": jobs_out,
        "usage_totals": {
            "cost_usd": round(total_cost, 4),
            "tokens": total_tokens,
            "calls": total_calls,
        },
        "by_agent": {
            k: {"calls": v["calls"], "tokens": v["tokens"], "cost_usd": v["cost_usd"]}
            for k, v in sorted(agent_usage.items(), key=lambda x: -x[1]["cost_usd"])
        },
    }


@app.get("/api/{user_id}/operator")
def get_operator_dashboard(user_id: str):
    cached = _read_json(_user_dir(user_id) / "operator" / "dashboard.json")
    if isinstance(cached, dict) and cached:
        return cached

    import importlib.util

    dashboard_path = Path(__file__).resolve().parent.parent / "agents" / "super" / "operator_dashboard.py"
    spec = importlib.util.spec_from_file_location("mira_operator_dashboard", dashboard_path)
    if not spec or not spec.loader:
        raise HTTPException(500, "Operator dashboard unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_operator_summary(user_id=user_id)


@app.get("/api/{user_id}/v3")
def get_v3_dashboard(user_id: str):
    if not is_known_user(user_id):
        raise HTTPException(404, "Unknown profile")

    from mira.configuration import default_v3_config
    from mira.kernel.store import JsonKernelStore
    from mira.engine.risk_gate import ApprovalStore
    from mira.engine.effect_log import EffectLog
    from mira.kernel.commit import MemoryCommitLog
    from mira.runtime import default_ledger, default_v3_paths
    from mira.web.dashboard import build_dashboard_snapshot

    paths = default_v3_paths()
    kernel = JsonKernelStore(paths.kernel).load()
    dashboard = build_dashboard_snapshot(
        kernel,
        default_ledger(),
        MemoryCommitLog(paths.commits),
        EffectLog(paths.effect_log),
        ApprovalStore(paths.approvals),
    )
    return {
        "dashboard": dashboard.__dict__,
        "config": default_v3_config().to_dict(),
        "paths": {
            "kernel": str(paths.kernel),
            "ledger": str(paths.ledger),
            "commits": str(paths.commits),
            "effect_log": str(paths.effect_log),
            "eval_history": str(paths.eval_history),
            "approvals": str(paths.approvals),
            "quarantine": str(paths.quarantine),
        },
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _file_meta(path: Path) -> dict:
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False, "bytes": 0, "updated_at": ""}
    return {
        "path": str(path),
        "exists": True,
        "bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _date_range(values: list[str]) -> dict:
    dates = sorted(dt for dt in (_parse_datetime(v) for v in values) if dt is not None)
    return {
        "first": dates[0].strftime("%Y-%m-%dT%H:%M:%SZ") if dates else "",
        "last": dates[-1].strftime("%Y-%m-%dT%H:%M:%SZ") if dates else "",
        "count": len(dates),
    }


def _status_rank(status: str) -> int:
    return {"red": 4, "yellow": 3, "blue": 2, "green": 1, "gray": 0}.get(status, 0)


def _normalize_dashboard_status(status: str | None) -> str:
    value = str(status or "").strip().lower()
    if value in {"green", "ok", "done", "applied", "success", "succeeded", "completed", "verified"}:
        return "green"
    if value in {"red", "error", "failed", "failure", "rejected", "quarantined"}:
        return "red"
    if value in {"blue", "pending", "queued", "scheduled"}:
        return "blue"
    if value in {"yellow", "running", "started", "active", "requires_human", "attention"}:
        return "yellow"
    return "gray"


def _is_dashboard_security_alert(item: dict) -> bool:
    tags = {str(tag).lower() for tag in item.get("tags") or []}
    error = item.get("error") if isinstance(item.get("error"), dict) else {}
    error_code = str(error.get("code") or "").lower()
    return bool(tags & {"security", "skill_audit", "error"}) or "skill_audit" in error_code


def _dashboard_item_summary(user_id: str, item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "type": item.get("type", ""),
        "title": item.get("title", ""),
        "status": item.get("status", ""),
        "tags": item.get("tags", []),
        "updated_at": item.get("updated_at", ""),
        "href": f"/api/{user_id}/items/{item.get('id', '')}",
    }


def _empty_usage_bucket() -> dict:
    return {"calls": 0, "tokens": 0, "cost_usd": 0.0, "models": {}, "agents": {}}


_SUCCESS_OUTCOMES = {"green", "ok", "done", "applied", "success", "succeeded", "completed", "verified"}
_RUNNING_JOB_STATUSES = {"running", "started", "active"}
_QUEUED_JOB_STATUSES = {"pending", "queued", "scheduled"}
_MODEL_CATALOG_CHECKED_AT = "2026-05-15"
_MODEL_CATALOG_SOURCES = [
    {
        "provider": "Anthropic",
        "url": "https://platform.claude.com/docs/en/about-claude/models/overview",
    },
    {"provider": "OpenAI", "url": "https://platform.openai.com/docs/models"},
    {"provider": "Google", "url": "https://ai.google.dev/gemini-api/docs/models"},
    {"provider": "Google TTS", "url": "https://ai.google.dev/gemini-api/docs/speech-generation"},
    {"provider": "DeepSeek", "url": "https://api-docs.deepseek.com/api/list-models"},
    {"provider": "MiniMax", "url": "https://platform.minimax.io/docs/guides/models-intro"},
    {"provider": "MLX", "url": "https://huggingface.co/mlx-community/gemma-4-31b-4bit"},
]
_MODEL_CATALOG = [
    {
        "provider": "Claude",
        "models": [
            {"value": "claude", "label": "Claude Code subscription"},
            {"value": "claude-opus-4-7", "label": "Claude Opus 4.7"},
            {"value": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
            {"value": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
        ],
    },
    {
        "provider": "GPT / Codex",
        "models": [
            {"value": "codex", "label": "Codex code subscription"},
            {"value": "gpt-5.5", "label": "GPT-5.5"},
        ],
    },
    {
        "provider": "DeepSeek",
        "models": [
            {"value": "deepseek-v4-pro", "label": "DeepSeek V4-Pro"},
        ],
    },
    {
        "provider": "Gemini",
        "models": [
            {"value": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview"},
            {"value": "gemini-3.1-flash-tts-preview", "label": "Gemini 3.1 Flash TTS Preview"},
        ],
    },
    {
        "provider": "MiniMax",
        "models": [
            {"value": "speech-2.8-hd", "label": "MiniMax Speech 2.8 HD"},
        ],
    },
    {
        "provider": "Local oMLX",
        "models": [
            {"value": "omlx", "label": "Gemma 4 31B IT 4-bit"},
        ],
    },
    {
        "provider": "System",
        "models": [
            {"value": "none", "label": "No model"},
        ],
    },
]
_MODEL_OPTIONS = [
    str(model["value"]) for group in _MODEL_CATALOG for model in group.get("models", []) if model.get("value")
]
_PIPELINE_AGENT_HINTS: dict[str, list[str]] = {
    "article_creation": ["writer"],
    "podcast_production": ["podcast"],
    "book_reading_notes": ["reader"],
    "social_reactive": ["social"],
    "social_proactive": ["social"],
    "weekly_growth_report": ["social"],
    "intelligence_briefing": ["explorer"],
    "research_deep_dive": ["researcher"],
    "daily_thought_discussion": ["discussion"],
    "daily_journal": ["orchestrator"],
    "weekly_reflection": ["memory_organizer"],
    "market_monitor": ["analyst"],
    "communication": ["orchestrator"],
    "system_health": ["monitor"],
    "incident_response": ["coder"],
    "health_wellness": ["health"],
    "self_evolution": ["coder"],
    "skill_learning": ["memory_organizer"],
    "memory_maintenance": ["memory_organizer"],
    "deterministic_reference": ["policy_runner"],
}


def _parse_maybe_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _latest_timestamp(values: list[str]) -> str:
    parsed = [(dt, raw) for raw in values if (dt := _parse_maybe_datetime(raw))]
    if not parsed:
        return ""
    parsed.sort(key=lambda row: row[0])
    return parsed[-1][1]


def _job_event_times(job: dict) -> list[str]:
    times = []
    if job.get("ran_at"):
        times.append(str(job["ran_at"]))
    times.extend(str(value) for value in job.get("dispatch_times") or [] if value)
    return times


def _configured_model_for_pipeline(pipeline_name: str, pipeline, model_by_agent: dict[str, str]) -> tuple[str, str]:
    candidates = [*_PIPELINE_AGENT_HINTS.get(pipeline_name, []), *(pipeline.involved_skills or []), pipeline_name]
    for agent in candidates:
        model = model_by_agent.get(agent)
        if model:
            return agent, model
    return "", ""


def _configured_model_for_step(pipeline_name: str, step_name: str, default_model: str) -> str:
    if pipeline_name == "daily_thought_discussion":
        if "opus" in step_name:
            return "claude-opus"
        if "sonnet" in step_name:
            return "claude-sonnet"
        if "deepseek" in step_name or "gemini" in step_name:
            return "deepseek / gemini"
    return default_model


def _step_model_hint(step_name: str, configured_model: str) -> tuple[str, str]:
    name = step_name.lower()
    if "agent_a_opus" in name:
        return "claude-opus", "step policy"
    if "agent_b_sonnet" in name:
        return "claude-sonnet", "step policy"
    if "agent_c_deepseek" in name or "deepseek" in name or "gemini" in name:
        return "deepseek / gemini", "step policy"
    llm_keywords = (
        "agent_",
        "analysis",
        "briefing",
        "compile",
        "diagnostic",
        "draft",
        "generate",
        "insight",
        "novelty",
        "outline",
        "pick_topic",
        "quality_eval",
        "research",
        "root_cause",
        "script",
        "synthesis",
        "trend",
        "write_",
    )
    if configured_model and configured_model != "none" and any(keyword in name for keyword in llm_keywords):
        return configured_model, "agent policy"
    return "", "no LLM"


def _model_options() -> list[str]:
    return list(_MODEL_OPTIONS)


def _model_catalog() -> dict:
    return {
        "checked_at": _MODEL_CATALOG_CHECKED_AT,
        "sources": list(_MODEL_CATALOG_SOURCES),
        "groups": [
            {
                "provider": str(group.get("provider", "")),
                "models": [
                    {"value": str(model.get("value", "")), "label": str(model.get("label", model.get("value", "")))}
                    for model in group.get("models", [])
                    if model.get("value")
                ],
            }
            for group in _MODEL_CATALOG
        ],
    }


def _latest_timestamp_from_map(values: dict) -> str:
    return _latest_timestamp([str(value) for value in values.values() if value])


def _pipeline_outputs(user_id: str, pipeline_name: str) -> list[dict]:
    base = _artifacts_dir(user_id)
    outputs: list[dict] = []
    if pipeline_name == "article_creation":
        manifest = _read_json(base / "writings" / "publish_manifest.json")
        articles = manifest.get("articles", {}) if isinstance(manifest, dict) else {}
        rows = []
        for slug, article in articles.items():
            if not isinstance(article, dict):
                continue
            timestamps = article.get("timestamps") if isinstance(article.get("timestamps"), dict) else {}
            rows.append((_latest_timestamp_from_map(timestamps), str(slug), article))
        rows.sort(key=lambda row: row[0], reverse=True)
        for ts, slug, article in rows[:3]:
            status = str(article.get("status") or "")
            href = f"/api/{quote(user_id)}/artifacts/writings/{quote(slug)}/final.md"
            outputs.append(
                {
                    "title": article.get("title") or slug,
                    "status": status,
                    "updated_at": ts,
                    "href": href,
                    "error": article.get("error") or "",
                }
            )
    elif pipeline_name == "book_reading_notes":
        books_dir = base / "books"
        if books_dir.exists():
            for project in sorted(books_dir.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)[:3]:
                if not project.is_dir() or project.name.startswith("_"):
                    continue
                latest_file = next(
                    (
                        path
                        for path in sorted(project.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
                        if path.is_file()
                    ),
                    None,
                )
                if not latest_file:
                    continue
                outputs.append(
                    {
                        "title": project.name,
                        "status": "ready",
                        "updated_at": datetime.fromtimestamp(latest_file.stat().st_mtime, timezone.utc).isoformat(),
                        "href": f"/api/{quote(user_id)}/artifacts/books/{quote(project.name)}/{quote(latest_file.name)}",
                        "error": "",
                    }
                )
    return outputs


def _dashboard_model_assignments_path() -> Path:
    from mira.runtime import default_v3_paths

    return default_v3_paths().root / "model_assignments.json"


def _load_model_assignment_overrides() -> dict[str, dict]:
    data = _read_json(_dashboard_model_assignments_path())
    if not isinstance(data, dict):
        return {}
    return {str(agent): row for agent, row in data.items() if isinstance(row, dict)}


def _dashboard_config() -> dict:
    from mira.configuration import default_v3_config

    config = default_v3_config().to_dict()
    overrides = _load_model_assignment_overrides()
    by_agent = {str(row.get("agent")): row for row in config.get("models", []) if isinstance(row, dict)}
    for agent, override in overrides.items():
        row = by_agent.setdefault(agent, {"agent": agent, "model": "", "token_budget": 0})
        if override.get("model"):
            row["model"] = str(override["model"])
        if "token_budget" in override:
            row["token_budget"] = int(override.get("token_budget") or 0)
        row["override"] = True
    config["models"] = sorted(by_agent.values(), key=lambda row: str(row.get("agent", "")))
    config["token_budgets"] = {
        str(row.get("agent")): int(row.get("token_budget") or 0) for row in config["models"] if row.get("agent")
    }
    config["model_options"] = _model_options()
    config["model_catalog"] = _model_catalog()
    config["model_overrides"] = overrides
    return config


def _usage_history(days: int = 30) -> dict:
    from datetime import date as _date

    from config import LOGS_DIR

    today = _date.today()
    by_agent: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    for offset in range(days):
        day = (today - timedelta(days=offset)).isoformat()
        daily[day] = _empty_usage_bucket()
        usage_path = LOGS_DIR / f"usage_{day}.jsonl"
        if not usage_path.exists():
            continue
        for line in usage_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            agent = str(rec.get("agent") or "unknown")
            model = str(rec.get("model") or "unknown")
            tokens = int(rec.get("total_tokens") or rec.get("tokens") or 0)
            cost = float(rec.get("cost_usd") or 0)
            day_row = daily[day]
            row = by_agent.setdefault(
                agent,
                {"calls": 0, "tokens": 0, "cost_usd": 0.0, "models": {}, "days": set()},
            )
            row["calls"] += 1
            row["tokens"] += tokens
            row["cost_usd"] += cost
            row["days"].add(day)
            row["models"][model] = row["models"].get(model, 0) + 1
            day_row["calls"] += 1
            day_row["tokens"] += tokens
            day_row["cost_usd"] += cost
            day_model = day_row["models"].setdefault(model, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            day_model["calls"] += 1
            day_model["tokens"] += tokens
            day_model["cost_usd"] += cost
            day_agent = day_row["agents"].setdefault(agent, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
            day_agent["calls"] += 1
            day_agent["tokens"] += tokens
            day_agent["cost_usd"] += cost

    out = {}
    for agent, row in by_agent.items():
        active_days = max(1, len(row.pop("days")))
        out[agent] = {
            **row,
            "cost_usd": round(row["cost_usd"], 4),
            "avg_daily_calls": round(row["calls"] / active_days, 2),
            "avg_weekly_calls": round(row["calls"] / max(1, days / 7), 2),
            "avg_monthly_calls": round(row["calls"] / max(1, days / 30), 2),
            "top_model": max(row["models"], key=row["models"].get) if row["models"] else "",
        }
    daily_rows = []
    for day in sorted(daily):
        row = daily[day]
        for collection in (row["models"], row["agents"]):
            for stats in collection.values():
                stats["cost_usd"] = round(stats["cost_usd"], 4)
        daily_rows.append(
            {
                "date": day,
                "calls": row["calls"],
                "tokens": row["tokens"],
                "cost_usd": round(row["cost_usd"], 4),
                "models": row["models"],
                "agents": row["agents"],
            }
        )

    def range_total(window: int) -> dict:
        rows = daily_rows[-window:]
        total = _empty_usage_bucket()
        for row in rows:
            total["calls"] += row["calls"]
            total["tokens"] += row["tokens"]
            total["cost_usd"] += row["cost_usd"]
            for model, stats in row["models"].items():
                dest = total["models"].setdefault(model, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
                dest["calls"] += stats["calls"]
                dest["tokens"] += stats["tokens"]
                dest["cost_usd"] += stats["cost_usd"]
            for agent, stats in row["agents"].items():
                dest = total["agents"].setdefault(agent, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
                dest["calls"] += stats["calls"]
                dest["tokens"] += stats["tokens"]
                dest["cost_usd"] += stats["cost_usd"]
        total["cost_usd"] = round(total["cost_usd"], 4)
        for collection in (total["models"], total["agents"]):
            for stats in collection.values():
                stats["cost_usd"] = round(stats["cost_usd"], 4)
        return total

    return {
        "days": days,
        "by_agent": out,
        "daily": daily_rows,
        "totals": {
            "today": range_total(1),
            "last_7d": range_total(7),
            "last_30d": range_total(days),
        },
    }


def _job_agent_stats(jobs: dict, usage_history: dict) -> list[dict]:
    rows = []
    by_agent = usage_history.get("by_agent", {})
    for name, usage in by_agent.items():
        rows.append(
            {
                "agent": name,
                "calls_30d": usage.get("calls", 0),
                "tokens_30d": usage.get("tokens", 0),
                "cost_30d": usage.get("cost_usd", 0.0),
                "daily_avg": usage.get("avg_daily_calls", 0),
                "weekly_avg": usage.get("avg_weekly_calls", 0),
                "monthly_avg": usage.get("avg_monthly_calls", 0),
                "top_model": usage.get("top_model", ""),
            }
        )
    if not rows:
        for agent, usage in (jobs.get("by_agent") or {}).items():
            rows.append(
                {
                    "agent": agent,
                    "calls_30d": usage.get("calls", 0),
                    "tokens_30d": usage.get("tokens", 0),
                    "cost_30d": usage.get("cost_usd", 0.0),
                    "daily_avg": usage.get("calls", 0),
                    "weekly_avg": usage.get("calls", 0) * 7,
                    "monthly_avg": usage.get("calls", 0) * 30,
                    "top_model": "",
                }
            )
    if not rows:
        for job in jobs.get("jobs", []):
            calls = int(job.get("dispatch_count") or (1 if job.get("status") == "done" else 0))
            if not calls and not job.get("enabled", True):
                continue
            rows.append(
                {
                    "agent": job.get("agent") or job.get("name", "unknown"),
                    "calls_30d": calls,
                    "tokens_30d": int((job.get("usage") or {}).get("tokens") or 0),
                    "cost_30d": float((job.get("usage") or {}).get("cost_usd") or 0.0),
                    "daily_avg": calls,
                    "weekly_avg": calls * 7,
                    "monthly_avg": calls * 30,
                    "top_model": max(
                        ((job.get("usage") or {}).get("models") or {}),
                        key=lambda model: ((job.get("usage") or {}).get("models") or {}).get(model, {}).get("calls", 0),
                        default="",
                    ),
                }
            )
    return sorted(rows, key=lambda r: (r["cost_30d"], r["calls_30d"]), reverse=True)[:50]


def _memory_action_dict(action) -> dict:
    return {"type": action.type, "target": action.target, "detail": action.detail}


def _security_posture(request: Request | None = None) -> dict:
    checks = [
        {
            "name": "API token",
            "status": "green" if bool(WEBGUI_TOKEN) else "red",
            "detail": "Bearer/X-Mira-Token required" if WEBGUI_TOKEN else "No WEBGUI_TOKEN configured",
        },
        {
            "name": "Loopback bypass",
            "status": "yellow" if WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN else "green",
            "detail": (
                "Loopback can access without token"
                if WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN
                else "Loopback still requires token"
            ),
        },
        {
            "name": "LAN bypass",
            "status": "red" if WEBGUI_ALLOW_LAN_WITHOUT_TOKEN else "green",
            "detail": (
                "Private LAN clients can access without token"
                if WEBGUI_ALLOW_LAN_WITHOUT_TOKEN
                else "LAN clients require token"
            ),
        },
        {
            "name": "Transport",
            "status": "green" if WEBGUI_HTTPS_ENABLED else "yellow",
            "detail": "HTTPS enabled" if WEBGUI_HTTPS_ENABLED else "HTTP enabled; acceptable only on trusted loopback",
        },
        {
            "name": "Write API",
            "status": "yellow" if CONTROL_API_WRITES_ENABLED else "green",
            "detail": "Write endpoints enabled" if CONTROL_API_WRITES_ENABLED else "Read-only control API",
        },
        {
            "name": "Rate limits",
            "status": "green",
            "detail": f"read={_READ_RATE_LIMIT}/min write={_WRITE_RATE_LIMIT}/min per IP",
        },
    ]
    worst = max(checks, key=lambda item: _status_rank(item["status"]))["status"]
    recommendations = []
    if not WEBGUI_TOKEN:
        recommendations.append("Set WEBGUI_TOKEN and require it from the iOS/web clients.")
    if WEBGUI_ALLOW_LAN_WITHOUT_TOKEN:
        recommendations.append("Disable WEBGUI_ALLOW_LAN_WITHOUT_TOKEN before binding beyond localhost.")
    if not WEBGUI_HTTPS_ENABLED and WEBGUI_HOST not in {"127.0.0.1", "localhost", "::1"}:
        recommendations.append("Enable HTTPS or bind WEBGUI_HOST to loopback only.")
    if CONTROL_API_WRITES_ENABLED:
        recommendations.append("Keep write endpoints token-gated and prefer short-lived local tokens.")
    return {
        "status": worst,
        "checks": checks,
        "summary": (
            "Not secure enough for exposed networks" if worst in {"red", "yellow"} else "Local posture is acceptable"
        ),
        "recommendations": recommendations,
    }


def _pipeline_status_rows(user_id: str, pipelines, records, commits, effects, jobs: dict, config: dict) -> list[dict]:
    from mira.runtime import JOB_PIPELINE_MAP

    job_by_pipeline: dict[str, list[dict]] = {}
    for job in jobs.get("jobs", []):
        pipeline_name = JOB_PIPELINE_MAP.get(job.get("name", ""))
        if pipeline_name:
            job_by_pipeline.setdefault(pipeline_name, []).append(job)

    model_by_agent = {m.get("agent"): m.get("model") for m in config.get("models", []) if isinstance(m, dict)}
    rows = []
    for name, pipeline in sorted(pipelines.items()):
        recent_records = [record for record in records if record.pipeline == name]
        recent_commits = [commit for commit in commits if commit.pipeline == name]
        recent_effects = [effect for effect in effects if effect.pipeline == name]
        pipeline_jobs = job_by_pipeline.get(name, [])
        latest_record = recent_records[-1] if recent_records else None
        latest_commit = recent_commits[-1] if recent_commits else None
        successful_records = [
            record for record in recent_records if str(record.outcome).strip().lower() in _SUCCESS_OUTCOMES
        ]
        latest_success_record = successful_records[-1] if successful_records else None
        latest_done_job_at = _latest_timestamp(
            [
                ts
                for job in pipeline_jobs
                if job.get("enabled", True) and job.get("status") == "done"
                for ts in _job_event_times(job)
            ]
        )
        last_success_at = latest_success_record.timestamp.isoformat() if latest_success_record else latest_done_job_at
        running_jobs = [
            job.get("name", "")
            for job in pipeline_jobs
            if job.get("enabled", True) and job.get("status") in _RUNNING_JOB_STATUSES
        ]
        queued_jobs = [
            job.get("name", "")
            for job in pipeline_jobs
            if job.get("enabled", True) and job.get("status") in _QUEUED_JOB_STATUSES
        ]
        errors = []
        outputs = _pipeline_outputs(user_id, name)
        latest_output = outputs[0] if outputs else {}
        latest_output_status = str(latest_output.get("status") or "").lower()
        latest_output_blocker = (
            latest_output if latest_output_status.startswith("blocked") or latest_output.get("error") else {}
        )
        output_blockers = [latest_output_blocker] if latest_output_blocker else []
        if latest_output_status in {"approved", "published", "ready", "done", "success"} and latest_output.get(
            "updated_at"
        ):
            last_success_at = (
                max(last_success_at, str(latest_output["updated_at"]))
                if last_success_at
                else str(latest_output["updated_at"])
            )
        if latest_record and (latest_record.outcome == "failed" or latest_record.delta.what_failed):
            errors.extend(latest_record.delta.what_failed or [latest_record.outcome])
        if latest_commit and latest_commit.status in {"quarantined", "rejected", "requires_human"}:
            errors.extend(f.reason for f in latest_commit.findings)
        for effect in recent_effects[-3:]:
            if effect.status not in {"done", "applied", "ok", "success"}:
                errors.append(f"{effect.action} {effect.status}")
        if errors:
            status = "red" if any("failed" in e.lower() or "reject" in e.lower() for e in errors) else "yellow"
        elif latest_output_blocker:
            status = "yellow"
        elif name == "article_creation" and latest_output_status == "approved":
            status = "blue"
        elif running_jobs:
            status = "yellow"
        elif last_success_at:
            status = "green"
        elif queued_jobs:
            status = "blue"
        else:
            status = "gray"
        status_text = (
            errors[0]
            if errors
            else (
                str(latest_output_blocker.get("status") or "output blocked")
                if latest_output_blocker
                else (
                    "approved / publish queued"
                    if name == "article_creation" and latest_output_status == "approved"
                    else {
                        "green": "success",
                        "yellow": "running",
                        "blue": "scheduled",
                        "red": "needs attention",
                        "gray": "not observed",
                    }.get(status, status)
                )
            )
        )

        usage = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "models": {}}
        for job in pipeline_jobs:
            job_usage = job.get("usage") or {}
            usage["calls"] += int(job_usage.get("calls") or 0)
            usage["tokens"] += int(job_usage.get("tokens") or 0)
            usage["cost_usd"] += float(job_usage.get("cost_usd") or 0)
            for model, row in (job_usage.get("models") or {}).items():
                usage["models"].setdefault(model, {"calls": 0, "tokens": 0, "cost_usd": 0.0})
                usage["models"][model]["calls"] += row.get("calls", 0)
                usage["models"][model]["tokens"] += row.get("tokens", 0)
                usage["models"][model]["cost_usd"] += row.get("cost_usd", 0.0)
        top_model = max(usage["models"], key=lambda m: usage["models"][m]["calls"]) if usage["models"] else ""
        configured_agent, configured_model = _configured_model_for_pipeline(name, pipeline, model_by_agent)
        steps = []
        step_count = max(1, len(pipeline.steps))
        failed_step_index: int | None = None
        attention_step_index: int | None = None
        if errors:
            first_error = errors[0]
            for idx, step in enumerate(pipeline.steps):
                if first_error.startswith(f"{step.name}:"):
                    failed_step_index = idx
                    break
        if output_blockers:
            blocker_text = f"{output_blockers[0].get('status', '')} {output_blockers[0].get('error', '')}".lower()
            preferred_step = ""
            if "security" in blocker_text:
                preferred_step = "content_hard_policy"
            elif "quality gate" in blocker_text or "writer_gate" in blocker_text:
                preferred_step = "quality_eval"
            for idx, step in enumerate(pipeline.steps):
                if preferred_step and preferred_step == step.name:
                    attention_step_index = idx
                    break
            if attention_step_index is None and pipeline.steps:
                attention_step_index = len(pipeline.steps) - 1
        for idx, step in enumerate(pipeline.steps):
            if status == "red" and failed_step_index is not None:
                if idx < failed_step_index:
                    step_status = "green"
                elif idx == failed_step_index:
                    step_status = "red"
                else:
                    step_status = "gray"
            elif status == "red":
                step_status = "red" if idx == step_count - 1 else "gray"
            elif output_blockers and attention_step_index is not None:
                if idx < attention_step_index:
                    step_status = "green"
                elif idx == attention_step_index:
                    step_status = "yellow"
                else:
                    step_status = "gray"
            elif status == "yellow" and last_success_at:
                step_status = "green"
            elif status == "yellow":
                step_status = "yellow" if idx == 0 else "gray"
            else:
                step_status = status
            step_status = _normalize_dashboard_status(step_status)
            hinted_model, model_source = _step_model_hint(step.name, configured_model)
            step_model = top_model or hinted_model
            if top_model:
                model_source = "recorded"
            observed_at = latest_record.timestamp.isoformat() if latest_record else latest_done_job_at
            if step_status == "blue":
                observed_at = ""
            steps.append(
                {
                    "name": step.name,
                    "type": step.type,
                    "status": step_status,
                    "model": step_model,
                    "model_recorded": bool(top_model),
                    "model_source": model_source,
                    "configured_model": _configured_model_for_step(name, step.name, configured_model),
                    "configured_agent": configured_agent,
                    "usage_recorded": bool(usage["calls"] or usage["tokens"] or usage["cost_usd"]),
                    "cost_usd": round(usage["cost_usd"] / step_count, 4),
                    "tokens": int(usage["tokens"] / step_count),
                    "observed_at": observed_at,
                    "timestamp_source": "pipeline run" if observed_at else "",
                    "error": (
                        output_blockers[0].get("error") or output_blockers[0].get("status") or ""
                        if output_blockers and attention_step_index == idx
                        else (
                            errors[0]
                            if errors
                            and (
                                (failed_step_index is not None and idx == failed_step_index)
                                or (failed_step_index is None and idx == step_count - 1)
                            )
                            else ""
                        )
                    ),
                }
            )
        rows.append(
            {
                "name": name,
                "status": status,
                "status_text": status_text,
                "status_detail": (
                    errors[0]
                    if errors
                    else (
                        output_blockers[0].get("error")
                        if output_blockers
                        else (
                            f"Latest output approved: {latest_output.get('title', '')}; publish queued"
                            if name == "article_creation" and latest_output_status == "approved"
                            else (
                                f"Active job(s): {', '.join(running_jobs)}; last success {last_success_at or 'not observed'}"
                                if running_jobs
                                else (
                                    f"Scheduled but not observed today: {', '.join(queued_jobs)}"
                                    if status == "blue"
                                    else (
                                        "No run evidence in the current V3 ledger/job window."
                                        if status == "gray"
                                        else ""
                                    )
                                )
                            )
                        )
                    )
                ),
                "memory_class": pipeline.memory_class,
                "trigger": f"{pipeline.trigger.type}: {pipeline.trigger.detail}",
                "priority": pipeline.priority,
                "version": pipeline.version,
                "last_run": latest_record.timestamp.isoformat() if latest_record else latest_done_job_at,
                "last_success_at": last_success_at,
                "last_success_outcome": (
                    latest_success_record.outcome if latest_success_record else ("done" if latest_done_job_at else "")
                ),
                "current_jobs": running_jobs or queued_jobs,
                "outcome": latest_record.outcome if latest_record else "",
                "error": errors[0] if errors else (output_blockers[0].get("error") if output_blockers else ""),
                "usage": {**usage, "cost_usd": round(usage["cost_usd"], 4)},
                "configured_agent": configured_agent,
                "configured_model": configured_model,
                "outputs": outputs,
                "steps": steps,
                "skills": pipeline.involved_skills,
            }
        )
    return rows


@app.get("/api/{user_id}/backend-dashboard")
def get_backend_dashboard(user_id: str, request: Request):
    if not is_known_user(user_id):
        raise HTTPException(404, "Unknown profile")

    from mira.engine.effect_log import EffectLog
    from mira.engine.risk_gate import ApprovalStore
    from mira.kernel.commit import MemoryCommitLog
    from mira.kernel.store import JsonKernelStore
    from mira.pipelines import PIPELINE_CATALOG
    from mira.runtime import default_ledger, default_v3_paths
    from mira.web.dashboard import build_dashboard_snapshot

    paths = default_v3_paths()
    ledger = default_ledger()
    commit_log = MemoryCommitLog(paths.commits)
    effect_log = EffectLog(paths.effect_log)
    approval_store = ApprovalStore(paths.approvals)
    kernel = JsonKernelStore(paths.kernel).load()
    snapshot = build_dashboard_snapshot(kernel, ledger, commit_log, effect_log, approval_store)
    pipeline_records = ledger.list(limit=500)
    pipeline_commits = commit_log.list(limit=500)
    pipeline_effects = effect_log.list(limit=500)
    records = pipeline_records[-25:]
    commits = pipeline_commits[-25:]
    effects = pipeline_effects[-25:]
    heartbeat = get_heartbeat()
    jobs = get_jobs_today(user_id)
    all_items = get_items(user_id)
    items = all_items[:25]
    alert_items = [item for item in all_items if _is_dashboard_security_alert(item)][:25]
    artifacts = list_artifact_sections(user_id)
    config = _dashboard_config()
    usage_history = _usage_history(days=30)
    pipeline_rows = _pipeline_status_rows(
        user_id, PIPELINE_CATALOG, pipeline_records, pipeline_commits, pipeline_effects, jobs, config
    )
    memory_timestamps = [
        *(record.timestamp.isoformat() for record in records),
        *(commit.timestamp.isoformat() for commit in commits),
        *(effect.timestamp.isoformat() for effect in effects),
    ]
    item_timestamps = [item.get("updated_at", "") for item in items]
    memory_status = "green"
    memory_errors = []
    if snapshot.review_queues.get("memory_commit") or snapshot.review_queues.get("incident_dlq"):
        memory_status = "yellow"
        memory_errors.extend(
            row.get("reason") or row.get("status") or "" for row in snapshot.review_queues.get("memory_commit", [])
        )
        memory_errors.extend(row.get("status") or "" for row in snapshot.review_queues.get("incident_dlq", []))

    return {
        "server_time": _utc_iso(),
        "profile": {"id": user_id, **get_user_config(user_id)},
        "service": {
            "heartbeat": heartbeat,
            "web": {
                "host": WEBGUI_HOST,
                "port": WEBGUI_PORT,
                "https": WEBGUI_HTTPS_ENABLED,
                "cert": str(WEBGUI_TLS_CERT_FILE),
            },
            "control": {
                "api_writes": CONTROL_API_WRITES_ENABLED,
                "runtime_db": CONTROL_RUNTIME_DB_ENABLED,
                "sse": CONTROL_SSE_ENABLED,
            },
        },
        "security": _security_posture(request),
        "memory": {
            "status": {
                "overall": memory_status,
                "errors": [err for err in memory_errors if err][:10],
                "date_range": _date_range(memory_timestamps),
                "item_date_range": _date_range(item_timestamps),
                "files": {
                    "kernel": _file_meta(paths.kernel),
                    "ledger": _file_meta(paths.ledger),
                    "commits": _file_meta(paths.commits),
                    "effect_log": _file_meta(paths.effect_log),
                    "eval_history": _file_meta(paths.eval_history),
                },
                "counts": {
                    "ledger": len(records),
                    "commits": len(commits),
                    "effects": len(effects),
                    "queued": sum(len(rows) for rows in snapshot.review_queues.values()),
                    "items": len(items),
                },
            },
            "kernel": {
                "identity": kernel.identity.statement,
                "scars": [scar.scar_id for scar in kernel.scars],
                "failure_lessons": [
                    {
                        "id": scar.scar_id,
                        "incident": scar.incident,
                        "root_cause": scar.root_cause,
                        "behavioral_change": scar.behavioral_change,
                        "policy_created": scar.policy_created,
                        "reinforcement_count": scar.reinforcement_count,
                        "date": scar.date.isoformat(),
                    }
                    for scar in kernel.scars[-20:]
                ],
                "hypotheses": [h.hypothesis_id for h in kernel.pending_hypotheses],
                "skill_traces": {trace.skill_name: trace.success_rate for trace in kernel.skill_traces},
                "failure_signatures": [sig.pattern for sig in kernel.failure_signatures],
                "relationship_notes": kernel.relationship_model.notes[-10:],
            },
            "ledger": [
                {
                    "id": record.id,
                    "pipeline": record.pipeline,
                    "outcome": record.outcome,
                    "proposal_id": record.memory_delta_proposal_id,
                    "commit_id": record.memory_commit_id,
                    "timestamp": record.timestamp.isoformat(),
                }
                for record in records
            ],
            "commits": [
                {
                    "id": commit.commit_id,
                    "pipeline": commit.pipeline,
                    "status": commit.status,
                    "proposal_id": commit.proposal_id,
                    "findings": [finding.reason for finding in commit.findings],
                    "committed_actions": [_memory_action_dict(action) for action in commit.committed_actions],
                    "rejected_actions": [_memory_action_dict(action) for action in commit.rejected_actions],
                    "quarantined_actions": [_memory_action_dict(action) for action in commit.quarantined_actions],
                    "summary": "; ".join(action.detail for action in commit.committed_actions[:3])
                    or "; ".join(finding.reason for finding in commit.findings[:3])
                    or commit.status,
                    "timestamp": commit.timestamp.isoformat(),
                }
                for commit in commits
            ],
            "effects": [
                {
                    "id": effect.effect_id,
                    "pipeline": effect.pipeline,
                    "action": effect.action,
                    "target": effect.target,
                    "status": effect.status,
                    "idempotency_key": effect.idempotency_key,
                    "timestamp": effect.timestamp.isoformat(),
                }
                for effect in effects
            ],
            "queues": snapshot.review_queues,
        },
        "pipelines": pipeline_rows,
        "policies": {
            "hard": snapshot.hard_policy_count,
            "soft": snapshot.soft_policy_count,
            "config": config.get("policy_parameters", {}),
        },
        "models": config.get("models", []),
        "model_options": config.get("model_options", _MODEL_OPTIONS),
        "model_catalog": config.get("model_catalog", _model_catalog()),
        "outputs": {
            "artifacts": artifacts,
            "alert_count": len(alert_items),
            "alert_items": [_dashboard_item_summary(user_id, item) for item in alert_items],
            "recent_items": [_dashboard_item_summary(user_id, item) for item in items],
            "jobs": {
                "date": jobs.get("date"),
                "usage_totals": jobs.get("usage_totals", {}),
                "by_agent": jobs.get("by_agent", {}),
                "usage_history": usage_history,
                "agent_stats": _job_agent_stats(jobs, usage_history),
                "recent": [
                    {
                        "name": job.get("name", ""),
                        "status": job.get("status", ""),
                        "agent": job.get("agent", ""),
                        "usage": job.get("usage", {}),
                        "ran_at": job.get("ran_at", ""),
                        "dispatch_count": job.get("dispatch_count", 0),
                        "trigger": job.get("trigger", ""),
                        "enabled": job.get("enabled", True),
                    }
                    for job in jobs.get("jobs", [])[:20]
                ],
            },
        },
        "paths": {
            "kernel": str(paths.kernel),
            "ledger": str(paths.ledger),
            "commits": str(paths.commits),
            "effect_log": str(paths.effect_log),
            "eval_history": str(paths.eval_history),
            "snapshots": str(paths.snapshots),
            "artifacts": str(_artifacts_dir(user_id)),
        },
    }


@app.post("/api/{user_id}/backend-dashboard/models/{agent}")
def update_backend_dashboard_model(user_id: str, agent: str, assignment: ModelAssignmentUpdate):
    if not is_known_user(user_id):
        raise HTTPException(404, "Unknown profile")
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    clean_agent = agent.strip()
    clean_model = assignment.model.strip()
    if not clean_agent:
        raise HTTPException(400, "Agent is required")
    if not clean_model:
        raise HTTPException(400, "Model is required")
    if clean_model not in _MODEL_OPTIONS:
        raise HTTPException(400, "Unknown model option")
    overrides_path = _dashboard_model_assignments_path()
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    overrides = _load_model_assignment_overrides()
    overrides[clean_agent] = {
        "model": clean_model,
        "token_budget": int(assignment.token_budget or 0),
        "updated_at": _utc_iso(),
        "updated_by": user_id,
    }
    _atomic_write(overrides_path, overrides)
    return {"agent": clean_agent, "assignment": overrides[clean_agent], "config": _dashboard_config()}


# ---------------------------------------------------------------------------
# API — Write (commands)
# ---------------------------------------------------------------------------


class NewRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=50000)
    quick: bool = False
    tags: list[str] = Field(default=[], max_length=20)


class NewTask(NewRequest):
    client_request_id: str | None = Field(default=None, min_length=1, max_length=100)
    type: str = Field(default="request", pattern=r"^(request|discussion)$")


class Reply(BaseModel):
    content: str = Field(..., min_length=1, max_length=50000)


class HealthMetricIn(BaseModel):
    type: str = Field(..., min_length=1, max_length=100)
    value: float
    unit: str = Field(default="", max_length=50)
    date: str = Field(..., min_length=1, max_length=100)
    activity: str | None = Field(default=None, max_length=100)
    calories: float | None = None
    distance: float | None = None


class HealthExportIn(BaseModel):
    export_date: str | None = None
    person_id: str | None = None
    metrics: list[HealthMetricIn] = Field(default=[], max_length=1000)


class PinUpdate(BaseModel):
    pinned: bool


class RecallQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


def _command_id() -> str:
    return uuid.uuid4().hex[:8]


def _command_filename(command_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"cmd_{ts}_{command_id}.json"


def _write_command(user_id: str, cmd: dict) -> None:
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(cmd_dir / _command_filename(cmd["id"]), cmd)


def _optimistic_item(
    *,
    item_id: str,
    item_type: str,
    title: str,
    content: str,
    message_id: str,
    sender: str,
    quick: bool = False,
    tags: list[str] | None = None,
    created_at: str | None = None,
) -> dict:
    now = created_at or _utc_iso()
    return {
        "id": item_id,
        "type": item_type,
        "title": title,
        "status": "queued",
        "tags": tags or [],
        "origin": "user",
        "pinned": False,
        "quick": quick,
        "parent_id": None,
        "created_at": now,
        "updated_at": now,
        "messages": [{"id": message_id, "sender": sender, "content": content, "timestamp": now, "kind": "text"}],
        "error": None,
        "result_path": None,
    }


def _export_compat_item(user_id: str, item: dict) -> None:
    items_dir = _user_dir(user_id) / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(items_dir / f"{item['id']}.json", item)
    _rebuild_manifest(user_id)


def _append_item_message(user_id: str, item_id: str, *, sender: str, content: str, kind: str = "text") -> dict:
    item_path, item = _load_item_or_404(user_id, item_id)
    now = _utc_iso()
    item.setdefault("messages", []).append(
        {
            "id": f"msg_{uuid.uuid4().hex[:10]}",
            "sender": sender,
            "content": content,
            "timestamp": now,
            "kind": kind,
        }
    )
    item["updated_at"] = now
    _atomic_write(item_path, item)
    _rebuild_manifest(user_id)
    return item


def _archive_compat_item(user_id: str, item: dict) -> None:
    archive_dir = _user_dir(user_id) / "archive"
    archive_dir.mkdir(exist_ok=True)
    _atomic_write(archive_dir / f"{item['id']}.json", item)
    item_path = _item_path(user_id, item["id"])
    if item_path.exists():
        item_path.unlink()
    _rebuild_manifest(user_id)


def _task_manager():
    super_dir = Path(__file__).resolve().parent.parent / "agents" / "super"
    if str(super_dir) not in sys.path:
        sys.path.insert(0, str(super_dir))
    from task_manager import TaskManager

    return TaskManager()


@app.post("/api/{user_id}/v2-status/cards")
def create_v2_status_card(user_id: str, card: V2StatusCard):
    """Create an app-visible V2 status feed card.

    This is a narrow operator channel for plan/gate/build status. It writes a
    feed item, not a dispatch command, so it cannot accidentally enqueue work.
    """
    now = _utc_iso()
    card_id = f"v2_{card.card_type}_{uuid.uuid4().hex[:10]}"
    status = "needs-input" if card.reply_options else "done"
    item = {
        "id": card_id,
        "type": "v2_status",
        "title": card.title,
        "status": status,
        "tags": ["v2_status", card.card_type],
        "origin": "agent",
        "pinned": card.card_type in {"decision", "sunday_gate", "drift_alert"},
        "quick": False,
        "parent_id": None,
        "created_at": now,
        "updated_at": now,
        "messages": [
            {
                "id": f"{card_id}_body",
                "sender": "mira",
                "content": card.body,
                "timestamp": now,
                "kind": "v2_status_card",
            }
        ],
        "error": None,
        "result_path": None,
        "channel": "v2_status",
        "card_type": card.card_type,
        "reply_options": card.reply_options,
        "default_action": card.default_action,
        "expires_at": _iso_after_hours(card.ttl_hours),
    }
    _export_compat_item(user_id, item)
    return {"status": status, "item_id": card_id, "item": item}


@app.post("/api/{user_id}/v2-status/cards/{card_id}/reply")
def reply_to_v2_status_card(user_id: str, card_id: str, reply: V2StatusReply):
    """Record a WA reply to a V2 status card and emit a narrow operator command."""
    item_path, item = _load_item_or_404(user_id, card_id)
    if item.get("channel") != "v2_status":
        raise HTTPException(404, "V2 status card not found")
    options = item.get("reply_options") or []
    normalized = reply.reply.strip().upper()
    if options and normalized not in {str(opt).upper() for opt in options}:
        raise HTTPException(422, f"Reply must be one of: {', '.join(options)}")
    updated = _append_item_message(user_id, card_id, sender=user_id, content=normalized, kind="v2_status_reply")
    updated["status"] = "done"
    updated["updated_at"] = _utc_iso()
    _atomic_write(item_path, updated)
    _rebuild_manifest(user_id)
    _write_command(
        user_id,
        {
            "id": _command_id(),
            "type": "v2_status_reply",
            "timestamp": _utc_iso(),
            "sender": user_id,
            "item_id": card_id,
            "reply": normalized,
        },
    )
    return {"status": "recorded", "item": updated}


@app.post("/api/{user_id}/tasks")
def create_task_api(user_id: str, req: NewTask):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    cmd_id = req.client_request_id or _command_id()
    prefix = "disc" if req.type == "discussion" else "req"
    item_id = cmd_id if cmd_id.startswith(f"{prefix}_") else f"{prefix}_{cmd_id}"
    created_at = _utc_iso()
    item = _optimistic_item(
        item_id=item_id,
        item_type=req.type,
        title=req.title,
        content=req.content,
        message_id=cmd_id,
        sender=user_id,
        quick=req.quick,
        tags=req.tags,
        created_at=created_at,
    )
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.create_task(
                user_id=user_id,
                task_id=item_id,
                message_id=cmd_id,
                title=req.title,
                content=req.content,
                sender=user_id,
                item_type=req.type,
                quick=req.quick,
                tags=req.tags,
                created_at=created_at,
            )
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc

    if BRIDGE_COMPAT_EXPORT_ENABLED:
        legacy_type = "new_discussion" if req.type == "discussion" else "new_request"
        _write_command(
            user_id,
            {
                "id": cmd_id,
                "type": legacy_type,
                "timestamp": created_at,
                "sender": user_id,
                "title": req.title,
                "content": req.content,
                "quick": req.quick,
                "tags": req.tags,
                "item_id": item_id,
            },
        )
        _export_compat_item(user_id, item)
    return {"item_id": item_id, "status": "queued", "item": item}


@app.post("/api/{user_id}/tasks/{task_id}/reply")
def reply_to_task_api(user_id: str, task_id: str, reply: Reply):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    cmd_id = _command_id()
    now = _utc_iso()
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.append_user_reply(
                user_id=user_id,
                task_id=task_id,
                message_id=cmd_id,
                sender=user_id,
                content=reply.content,
                created_at=now,
            )
    except KeyError:
        raise HTTPException(404, "Task not found")
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc

    if BRIDGE_COMPAT_EXPORT_ENABLED:
        _write_command(
            user_id,
            {
                "id": cmd_id,
                "type": "reply",
                "timestamp": now,
                "sender": user_id,
                "item_id": task_id,
                "content": reply.content,
            },
        )
        _export_compat_item(user_id, item)
    return {"status": "sent", "item": item}


@app.post("/api/{user_id}/health/export")
def ingest_health_export_api(user_id: str, export: HealthExportIn):
    """Ingest HealthKit export directly through the control API.

    The old path wrote `apple_health_export.json` through iCloud and waited for a
    later agent sweep. This endpoint makes the phone-to-Mac health path explicit
    and durable while keeping the file fallback available in the app.
    """
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    if not export.metrics:
        return {"status": "empty", "inserted": 0}
    person_id = export.person_id or user_id
    try:
        health_dir = Path(__file__).resolve().parent.parent / "agents" / "health"
        if str(health_dir) not in sys.path:
            sys.path.insert(0, str(health_dir))
        from config import DATABASE_URL
        from health_store import HealthStore
        from ingest import expand_health_metrics
        from summary import write_summary_to_bridge

        metric_rows = [
            (m.model_dump(exclude_none=True) if hasattr(m, "model_dump") else m.dict(exclude_none=True))
            for m in export.metrics
        ]
        metrics = expand_health_metrics(metric_rows)
        store = HealthStore(DATABASE_URL)
        store.insert_metrics_batch(person_id, metrics, source="apple_health_api")
        write_summary_to_bridge(store, MIRA_DIR, person_id)
        store.close()
    except Exception as exc:
        raise HTTPException(503, f"Health export failed: {exc}") from exc
    return {"status": "ingested", "inserted": len(metrics), "person_id": person_id}


@app.post("/api/{user_id}/tasks/{task_id}/pin")
def pin_task_api(user_id: str, task_id: str, update: PinUpdate):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.set_pinned(user_id, task_id, update.pinned)
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc
    if item is None:
        raise HTTPException(404, "Task not found")
    if BRIDGE_COMPAT_EXPORT_ENABLED:
        _write_command(
            user_id,
            {
                "id": _command_id(),
                "type": "pin",
                "timestamp": _utc_iso(),
                "sender": user_id,
                "item_id": task_id,
                "pinned": update.pinned,
            },
        )
        _export_compat_item(user_id, item)
    return {"pinned": update.pinned, "item": item}


@app.post("/api/{user_id}/tasks/{task_id}/archive")
def archive_task_api(user_id: str, task_id: str):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.archive_task(user_id, task_id)
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc
    if item is None:
        raise HTTPException(404, "Task not found")
    if BRIDGE_COMPAT_EXPORT_ENABLED:
        _write_command(
            user_id,
            {
                "id": _command_id(),
                "type": "archive",
                "timestamp": _utc_iso(),
                "sender": user_id,
                "item_id": task_id,
            },
        )
        _archive_compat_item(user_id, item)
    return {"status": "archived", "item": item}


@app.post("/api/{user_id}/tasks/{task_id}/cancel")
def cancel_task_api(user_id: str, task_id: str):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    reason = "Cancelled by user"
    cancelled = None
    try:
        cancelled = _task_manager().cancel_task(task_id, reason=reason)
    except Exception:
        cancelled = None
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.update_task_status(
                user_id,
                task_id,
                "failed",
                summary=reason,
                error_code="cancelled",
                error_message=reason,
            )
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc
    if item is None and cancelled is None:
        raise HTTPException(404, "Task not found")
    if BRIDGE_COMPAT_EXPORT_ENABLED:
        _write_command(
            user_id,
            {
                "id": _command_id(),
                "type": "cancel",
                "timestamp": _utc_iso(),
                "sender": user_id,
                "item_id": task_id,
            },
        )
        if item:
            _export_compat_item(user_id, item)
    return {"status": "cancelled", "item": item}


@app.post("/api/{user_id}/tasks/{task_id}/retry")
def retry_task_api(user_id: str, task_id: str):
    if not CONTROL_API_WRITES_ENABLED:
        raise HTTPException(409, "Control API writes are disabled")
    if not CONTROL_RUNTIME_DB_ENABLED:
        raise HTTPException(409, "Control runtime DB dispatch is disabled")
    removed = None
    try:
        removed = _task_manager().reset_for_retry(task_id)
    except Exception:
        removed = None
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            item = repo.update_task_status(user_id, task_id, "queued", summary="Retry requested")
    except Exception as exc:
        raise HTTPException(503, f"Control DB unavailable: {exc}") from exc
    if item is None and removed is None:
        raise HTTPException(404, "Task not found")
    return {"status": "queued", "item": item}


@app.post("/api/{user_id}/request")
def create_request(user_id: str, req: NewRequest):
    if not ICLOUD_COMMAND_FALLBACK_ENABLED:
        return create_task_api(
            user_id,
            NewTask(
                type="request",
                title=req.title,
                content=req.content,
                quick=req.quick,
                tags=req.tags,
            ),
        )
    cmd_id = _command_id()
    item_id = f"req_{cmd_id}"
    cmd = {
        "id": cmd_id,
        "type": "new_request",
        "timestamp": _utc_iso(),
        "sender": user_id,
        "title": req.title,
        "content": req.content,
        "quick": req.quick,
        "tags": req.tags,
        "item_id": item_id,
    }
    _write_command(user_id, cmd)

    # Optimistic: create item immediately (same item_id as in command)
    item = {
        "id": item_id,
        "type": "request",
        "title": req.title,
        "status": "queued",
        "tags": req.tags,
        "origin": "user",
        "pinned": False,
        "quick": req.quick,
        "parent_id": None,
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "messages": [
            {"id": cmd_id, "sender": user_id, "content": req.content, "timestamp": _utc_iso(), "kind": "text"}
        ],
        "error": None,
        "result_path": None,
    }
    _export_compat_item(user_id, item)
    return {"item_id": item_id, "status": "queued"}


@app.post("/api/{user_id}/items/{item_id}/reply")
def reply_to_item(user_id: str, item_id: str, reply: Reply):
    if not ICLOUD_COMMAND_FALLBACK_ENABLED:
        return reply_to_task_api(user_id, item_id, reply)
    item_path, item = _load_item_or_404(user_id, item_id)
    cmd_id = _command_id()
    cmd = {
        "id": cmd_id,
        "type": "reply",
        "timestamp": _utc_iso(),
        "sender": user_id,
        "item_id": item_id,
        "content": reply.content,
    }
    _write_command(user_id, cmd)

    # Optimistic: append message to item
    item["messages"].append(
        {
            "id": cmd_id,
            "sender": user_id,
            "content": reply.content,
            "timestamp": _utc_iso(),
            "kind": "text",
        }
    )
    item["updated_at"] = _utc_iso()
    _atomic_write(item_path, item)
    return {"status": "sent"}


@app.post("/api/{user_id}/recall")
def recall(user_id: str, q: RecallQuery):
    if not ICLOUD_COMMAND_FALLBACK_ENABLED:
        raise HTTPException(410, "Legacy command endpoint disabled; use canonical task API")
    cmd_id = uuid.uuid4().hex[:8]
    cmd = {
        "id": cmd_id,
        "type": "recall",
        "timestamp": _utc_iso(),
        "sender": user_id,
        "query": q.query,
    }
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)
    return {"status": "searching", "cmd_id": cmd_id}


@app.post("/api/{user_id}/items/{item_id}/share")
def share_item(user_id: str, item_id: str):
    if not ICLOUD_COMMAND_FALLBACK_ENABLED:
        raise HTTPException(410, "Legacy command endpoint disabled; use canonical task API")
    _load_item_or_404(user_id, item_id)
    cmd_id = uuid.uuid4().hex[:8]
    cmd = {
        "id": cmd_id,
        "type": "share",
        "timestamp": _utc_iso(),
        "sender": user_id,
        "item_id": item_id,
    }
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)
    return {"status": "shared"}


@app.post("/api/{user_id}/items/{item_id}/pin")
def pin_item(user_id: str, item_id: str):
    item_path, item = _load_item_or_404(user_id, item_id)
    item["pinned"] = not item.get("pinned", False)
    _atomic_write(item_path, item)
    return {"pinned": item["pinned"]}


@app.post("/api/{user_id}/items/{item_id}/archive")
def archive_item(user_id: str, item_id: str):
    item_path, item = _load_item_or_404(user_id, item_id)
    item["status"] = "archived"
    archive_dir = _user_dir(user_id) / "archive"
    archive_dir.mkdir(exist_ok=True)
    _atomic_write(archive_dir / f"{item_id}.json", item)
    item_path.unlink()
    _rebuild_manifest(user_id)
    return {"status": "archived"}


def _rebuild_manifest(user_id: str):
    items_dir = _user_dir(user_id) / "items"
    entries = []
    if items_dir.exists():
        for path in items_dir.glob("*.json"):
            item = _read_json(path)
            if item:
                entries.append(
                    {
                        "id": item["id"],
                        "type": item.get("type", "request"),
                        "status": item.get("status", "queued"),
                        "updated_at": item.get("updated_at", ""),
                    }
                )
    _atomic_write(_user_dir(user_id) / "manifest.json", {"updated_at": _utc_iso(), "items": entries})


# ---------------------------------------------------------------------------
# API — Artifacts
# ---------------------------------------------------------------------------

# All data from iCloud Drive only — accessible from any network
_ICLOUD_ARTIFACTS = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Artifacts"
_USER_ARTIFACT_SECTIONS = ("writings", "briefings", "audio", "video", "photos", "research", "books")
_SHARED_ARTIFACT_SECTIONS = ("briefings", "writings", "research")


def _artifacts_dir(user_id: str) -> Path:
    return _ICLOUD_ARTIFACTS / user_id


def _shared_artifacts_dir() -> Path:
    return _ICLOUD_ARTIFACTS / "shared"


def _safe_join(base: Path, *parts: str) -> Path:
    clean: list[str] = []
    for part in parts:
        if not part or part in {".", ".."}:
            raise HTTPException(404)
        if "/" in part or "\\" in part:
            raise HTTPException(404)
        clean.append(part)
    path = base.joinpath(*clean).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(404) from exc
    return path


def _resolve_artifact_path(user_id: str, section: str, *parts: str) -> Path:
    if section == "shared":
        if not parts:
            return _shared_artifacts_dir()
        subsection, *rest = parts
        if subsection not in _SHARED_ARTIFACT_SECTIONS:
            raise HTTPException(404, "Artifact section not found")
        return _safe_join(_shared_artifacts_dir(), subsection, *rest)
    if section not in _USER_ARTIFACT_SECTIONS:
        raise HTTPException(404, "Artifact section not found")
    return _safe_join(_artifacts_dir(user_id), section, *parts)


def _list_shared_sections() -> list[dict[str, str | int]]:
    shared = _shared_artifacts_dir()
    files = []
    for name in _SHARED_ARTIFACT_SECTIONS:
        path = shared / name
        if not path.exists():
            continue
        files.append(
            {
                "name": f"{name}/",
                "size": 0,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
    return files


@app.get("/api/{user_id}/artifacts")
def list_artifact_sections(user_id: str):
    base = _artifacts_dir(user_id)
    sections = []
    for name in _USER_ARTIFACT_SECTIONS:
        d = base / name
        if d.exists():
            count = len(list(d.glob("*")))
            sections.append({"name": name, "count": count, "href": f"/api/{user_id}/artifacts/{name}"})
    # Also check shared
    shared = _shared_artifacts_dir()
    if shared.exists():
        for name in _SHARED_ARTIFACT_SECTIONS:
            d = shared / name
            if d.exists():
                count = len(list(d.glob("*")))
                if count:
                    sections.append(
                        {"name": f"shared/{name}", "count": count, "href": f"/api/{user_id}/artifacts/shared/{name}"}
                    )
    return sections


@app.get("/api/{user_id}/artifacts/{section}")
def list_artifacts(user_id: str, section: str):
    if section == "shared":
        return _list_shared_sections()
    base = _resolve_artifact_path(user_id, section)
    if not base.exists():
        return []
    return _list_dir(base)


@app.get("/api/{user_id}/artifacts/{section}/{subsection}")
def list_artifacts_sub(user_id: str, section: str, subsection: str):
    """List files in a subdirectory (e.g. writings/project-name/)."""
    base = _resolve_artifact_path(user_id, section, subsection)
    if not base.exists():
        raise HTTPException(404)
    if base.is_file():
        return _read_file(base)
    return _list_dir(base)


@app.get("/api/{user_id}/artifacts/{section}/{subsection}/{filename}")
def read_artifact(user_id: str, section: str, subsection: str, filename: str):
    path = _resolve_artifact_path(user_id, section, subsection, filename)
    if not path.exists():
        raise HTTPException(404)
    return _read_file(path)


def _list_dir(base: Path):
    files = []
    for path in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.startswith("."):
            continue
        if path.is_file():
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        elif path.is_dir():
            files.append(
                {
                    "name": path.name + "/",
                    "size": 0,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
    return files


def _read_file(path: Path):
    if path.suffix in (".md", ".txt", ".json", ".csv", ".log", ".yml", ".yaml"):
        return HTMLResponse(
            content=path.read_text(encoding="utf-8", errors="replace"), media_type="text/plain; charset=utf-8"
        )
    return FileResponse(path)


# ---------------------------------------------------------------------------
# SSE — Real-time notifications via Server-Sent Events
# ---------------------------------------------------------------------------


@app.get("/api/{user_id}/events")
async def events(user_id: str, request: Request, last_event_id: int = 0):
    """SSE stream for task events, with legacy manifest polling fallback."""

    async def generate():
        if CONTROL_SSE_ENABLED:
            header_id = request.headers.get("last-event-id", "").strip()
            try:
                last_id = int(header_id or last_event_id or 0)
            except ValueError:
                last_id = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    from control.db import transaction
                    from control.repository import ControlRepository

                    with transaction() as conn:
                        repo = ControlRepository(conn)
                        events = repo.list_events_since(user_id, last_id, limit=100)
                    for event in events:
                        last_id = int(event["id"])
                        yield (
                            f"id: {last_id}\n"
                            f"event: {event.get('event_type') or 'task.updated'}\n"
                            f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                        )
                except Exception as exc:
                    payload = {"message": f"Control DB unavailable: {exc}", "server_time": _utc_iso()}
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(2)
            return

        last_timestamps: dict[str, str] = {}
        while True:
            if await request.is_disconnected():
                break
            manifest = _read_json(_user_dir(user_id) / "manifest.json")
            if manifest:
                for entry in manifest.get("items", []):
                    eid = entry["id"]
                    ts = entry.get("updated_at", "")
                    if last_timestamps.get(eid) != ts:
                        last_timestamps[eid] = ts
                        item = _read_json(_user_dir(user_id) / "items" / f"{eid}.json")
                        if item:
                            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            # Also push heartbeat
            hb = _read_json(BRIDGE / "heartbeat.json")
            if hb:
                yield f"event: heartbeat\ndata: {json.dumps(hb, ensure_ascii=False)}\n\n"
            await asyncio.sleep(10)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "backend-dashboard.html").read_text(encoding="utf-8")


@app.get("/backend", response_class=HTMLResponse)
@app.get("/backend/{page}", response_class=HTMLResponse)
def backend_dashboard(page: str = "pipelines"):
    return (WEB_DIR / "backend-dashboard.html").read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/mira-icon.png")
def mira_icon():
    return FileResponse(WEB_ICON, media_type="image/png")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(WEB_ICON, media_type="image/png")


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(WEB_ICON, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    ssl_kwargs = {}
    if WEBGUI_HTTPS_ENABLED:
        ssl_kwargs = {"ssl_certfile": str(WEBGUI_TLS_CERT_FILE), "ssl_keyfile": str(WEBGUI_TLS_KEY_FILE)}
    uvicorn.run(app, host=WEBGUI_HOST, port=WEBGUI_PORT, **ssl_kwargs)
