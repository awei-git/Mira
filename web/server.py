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

app = FastAPI(title="Mira", docs_url=None, redoc_url=None)
_mdns_process: subprocess.Popen | None = None
_JSON_FILE_LOCKS: collections.defaultdict[str, threading.RLock] = collections.defaultdict(threading.RLock)

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
# Rate limiting — simple in-memory per-IP limiter
# ---------------------------------------------------------------------------
_RATE_LIMIT = 60  # requests per window
_RATE_WINDOW = 60  # window in seconds
_rate_buckets: dict[str, collections.deque] = {}


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(client_ip, collections.deque())
    # Purge old entries
    while bucket and bucket[0] < now - _RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        return False
    bucket.append(now)
    return True


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
    if not _check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})
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


@app.get("/api/{user_id}/tasks")
def get_tasks(user_id: str, include_archived: bool = False, limit: int = 200, messages_per_item: int = 20):
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
def get_threads(user_id: str, include_archived: bool = False, limit: int = 200, messages_per_item: int = 20):
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
_USER_ARTIFACT_SECTIONS = ("writings", "briefings", "audio", "video", "photos", "research")
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
            sections.append({"name": name, "count": count})
    # Also check shared
    shared = _shared_artifacts_dir()
    if shared.exists():
        for name in _SHARED_ARTIFACT_SECTIONS:
            d = shared / name
            if d.exists():
                count = len(list(d.glob("*")))
                if count:
                    sections.append({"name": f"shared/{name}", "count": count})
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
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    ssl_kwargs = {}
    if WEBGUI_HTTPS_ENABLED:
        ssl_kwargs = {"ssl_certfile": str(WEBGUI_TLS_CERT_FILE), "ssl_keyfile": str(WEBGUI_TLS_KEY_FILE)}
    uvicorn.run(app, host=WEBGUI_HOST, port=WEBGUI_PORT, **ssl_kwargs)
