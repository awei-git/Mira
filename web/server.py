"""Mira Web GUI — lightweight FastAPI server reading from bridge files."""
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from secrets import compare_digest
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add agents to path
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "shared"))
from config import (
    MIRA_DIR,
    WEBGUI_ALLOW_LAN_WITHOUT_TOKEN,
    WEBGUI_ALLOW_LOOPBACK_WITHOUT_TOKEN,
    WEBGUI_HOST,
    WEBGUI_PORT,
    WEBGUI_TOKEN,
    get_known_user_ids,
    get_user_config,
    is_known_user,
)

BRIDGE = MIRA_DIR
USERS_DIR = BRIDGE / "users"

app = FastAPI(title="Mira", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def _atomic_write(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.rename(path)

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
        profiles.append({
            "id": user_id,
            "display_name": cfg.get("display_name", user_id),
            "agent_name": "Mira",
        })
    return {"profiles": profiles}

@app.get("/api/heartbeat")
def get_heartbeat():
    data = _read_json(BRIDGE / "heartbeat.json")
    return data or {"timestamp": "", "status": "offline"}

# ---------------------------------------------------------------------------
# API — Todos
# ---------------------------------------------------------------------------

class NewTodo(BaseModel):
    title: str
    priority: str = "medium"
    tags: list[str] = []

class UpdateTodo(BaseModel):
    status: str = ""
    priority: str = ""
    title: str = ""

class Followup(BaseModel):
    content: str
    source: str = "user"

@app.get("/api/{user_id}/todos")
def get_todos(user_id: str):
    path = _user_dir(user_id) / "todos.json"
    todos = _read_json(path) or []
    # Migrate legacy 'response' → 'followups'
    for t in todos:
        if "followups" not in t:
            t["followups"] = []
            if t.get("response"):
                t["followups"].append({"content": t["response"], "source": "agent", "timestamp": t.get("updated_at", "")})
        if "tags" not in t:
            t["tags"] = []
    return todos

@app.post("/api/{user_id}/todos")
def add_todo(user_id: str, todo: NewTodo):
    path = _user_dir(user_id) / "todos.json"
    todos = _read_json(path) or []
    new = {
        "id": f"todo_{uuid.uuid4().hex[:8]}",
        "title": todo.title,
        "priority": todo.priority,
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
    todos = _read_json(path) or []
    for t in todos:
        if t["id"] == todo_id:
            if update.status: t["status"] = update.status
            if update.priority: t["priority"] = update.priority
            if update.title: t["title"] = update.title
            t["updated_at"] = _utc_iso()
            _atomic_write(path, todos)
            return t
    raise HTTPException(404)

@app.post("/api/{user_id}/todos/{todo_id}/followup")
def add_followup(user_id: str, todo_id: str, fu: Followup):
    path = _user_dir(user_id) / "todos.json"
    todos = _read_json(path) or []
    for t in todos:
        if t["id"] == todo_id:
            if "followups" not in t: t["followups"] = []
            t["followups"].append({"content": fu.content, "source": fu.source, "timestamp": _utc_iso()})
            t["updated_at"] = _utc_iso()
            _atomic_write(path, todos)
            # Send command so Mira processes the followup
            if fu.source == "user":
                cmd_id = uuid.uuid4().hex[:8]
                cmd = {
                    "id": cmd_id, "type": "todo_followup", "timestamp": _utc_iso(),
                    "sender": user_id, "todo_id": todo_id, "content": fu.content,
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
    state_file = Path(__file__).resolve().parent.parent / ".agent_state.json"
    state = _read_json(state_file) or {}
    user_state = state.get("users", {}).get(user_id, {})

    # 3. Load today's usage log for per-agent token/cost breakdown
    logs_dir = Path(__file__).resolve().parent.parent / "logs"
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
        "by_agent": {k: {"calls": v["calls"], "tokens": v["tokens"], "cost_usd": v["cost_usd"]}
                     for k, v in sorted(agent_usage.items(), key=lambda x: -x[1]["cost_usd"])},
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
    title: str
    content: str
    quick: bool = False
    tags: list[str] = []

class Reply(BaseModel):
    content: str

class RecallQuery(BaseModel):
    query: str

@app.post("/api/{user_id}/request")
def create_request(user_id: str, req: NewRequest):
    cmd_id = uuid.uuid4().hex[:8]
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
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)

    # Optimistic: create item immediately (same item_id as in command)
    item = {
        "id": item_id, "type": "request", "title": req.title,
        "status": "queued", "tags": req.tags, "origin": "user",
        "pinned": False, "quick": req.quick, "parent_id": None,
        "created_at": _utc_iso(), "updated_at": _utc_iso(),
        "messages": [{"id": cmd_id, "sender": user_id, "content": req.content,
                       "timestamp": _utc_iso(), "kind": "text"}],
        "error": None, "result_path": None,
    }
    items_dir = _user_dir(user_id) / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(items_dir / f"{item_id}.json", item)
    _rebuild_manifest(user_id)
    return {"item_id": item_id, "status": "queued"}

@app.post("/api/{user_id}/items/{item_id}/reply")
def reply_to_item(user_id: str, item_id: str, reply: Reply):
    item_path, item = _load_item_or_404(user_id, item_id)
    cmd_id = uuid.uuid4().hex[:8]
    cmd = {
        "id": cmd_id, "type": "reply", "timestamp": _utc_iso(),
        "sender": user_id, "item_id": item_id, "content": reply.content,
    }
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)

    # Optimistic: append message to item
    item["messages"].append({
        "id": cmd_id, "sender": user_id, "content": reply.content,
        "timestamp": _utc_iso(), "kind": "text",
    })
    item["updated_at"] = _utc_iso()
    _atomic_write(item_path, item)
    return {"status": "sent"}

@app.post("/api/{user_id}/recall")
def recall(user_id: str, q: RecallQuery):
    cmd_id = uuid.uuid4().hex[:8]
    cmd = {
        "id": cmd_id, "type": "recall", "timestamp": _utc_iso(),
        "sender": user_id, "query": q.query,
    }
    cmd_dir = _user_dir(user_id) / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    _atomic_write(cmd_dir / f"cmd_{ts}_{cmd_id}.json", cmd)
    return {"status": "searching", "cmd_id": cmd_id}

@app.post("/api/{user_id}/items/{item_id}/share")
def share_item(user_id: str, item_id: str):
    _load_item_or_404(user_id, item_id)
    cmd_id = uuid.uuid4().hex[:8]
    cmd = {
        "id": cmd_id, "type": "share", "timestamp": _utc_iso(),
        "sender": user_id, "item_id": item_id,
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
                entries.append({
                    "id": item["id"],
                    "type": item.get("type", "request"),
                    "status": item.get("status", "queued"),
                    "updated_at": item.get("updated_at", ""),
                })
    _atomic_write(_user_dir(user_id) / "manifest.json", {
        "updated_at": _utc_iso(), "items": entries
    })

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
        files.append({
            "name": f"{name}/",
            "size": 0,
            "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
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
            files.append({
                "name": path.name,
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        elif path.is_dir():
            files.append({
                "name": path.name + "/",
                "size": 0,
                "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    return files

def _read_file(path: Path):
    if path.suffix in (".md", ".txt", ".json", ".csv", ".log", ".yml", ".yaml"):
        return HTMLResponse(
            content=path.read_text(encoding="utf-8", errors="replace"),
            media_type="text/plain; charset=utf-8"
        )
    return FileResponse(path)

# ---------------------------------------------------------------------------
# SSE — Real-time notifications via Server-Sent Events
# ---------------------------------------------------------------------------

@app.get("/api/{user_id}/events")
async def events(user_id: str, request: Request):
    """SSE stream that polls manifest every 10s and pushes changed items."""
    async def generate():
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
    uvicorn.run(app, host=WEBGUI_HOST, port=WEBGUI_PORT)
