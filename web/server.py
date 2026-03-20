"""Mira Web GUI — lightweight FastAPI server reading from bridge files."""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add agents to path
sys.path.insert(0, str(Path(__file__).parent.parent / "agents" / "shared"))
from config import MIRA_DIR

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

# ---------------------------------------------------------------------------
# API — Read
# ---------------------------------------------------------------------------

@app.get("/api/profiles")
def get_profiles():
    data = _read_json(BRIDGE / "profiles.json")
    if data:
        return data
    return {"profiles": [
        {"id": "ang", "display_name": "Ang", "agent_name": "Mira"},
        {"id": "liquan", "display_name": "Liquan", "agent_name": "Mika"},
    ]}

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

@app.get("/api/{user_id}/todos")
def get_todos(user_id: str):
    path = _user_dir(user_id) / "todos.json"
    return _read_json(path) or []

@app.post("/api/{user_id}/todos")
def add_todo(user_id: str, todo: NewTodo):
    path = _user_dir(user_id) / "todos.json"
    todos = _read_json(path) or []
    new = {
        "id": f"todo_{uuid.uuid4().hex[:8]}",
        "title": todo.title,
        "priority": todo.priority,
        "status": "pending",
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "response": None,
    }
    todos.append(new)
    _atomic_write(path, todos)
    return new

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
    item_path = _user_dir(user_id) / "items" / f"{item_id}.json"
    item = _read_json(item_path)
    if item:
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
    item_path = _user_dir(user_id) / "items" / f"{item_id}.json"
    item = _read_json(item_path)
    if not item:
        raise HTTPException(404)
    item["pinned"] = not item.get("pinned", False)
    _atomic_write(item_path, item)
    return {"pinned": item["pinned"]}

@app.post("/api/{user_id}/items/{item_id}/archive")
def archive_item(user_id: str, item_id: str):
    item_path = _user_dir(user_id) / "items" / f"{item_id}.json"
    item = _read_json(item_path)
    if not item:
        raise HTTPException(404)
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

def _artifacts_dir(user_id: str) -> Path:
    return _ICLOUD_ARTIFACTS / user_id

@app.get("/api/{user_id}/artifacts")
def list_artifact_sections(user_id: str):
    base = _artifacts_dir(user_id)
    sections = []
    for name in ["briefings", "writings", "research", "audio", "video"]:
        d = base / name
        if d.exists():
            count = len(list(d.glob("*")))
            sections.append({"name": name, "count": count})
    # Also check shared
    shared = _ICLOUD_ARTIFACTS / "shared"
    if shared.exists():
        for name in ["briefings", "writings", "research"]:
            d = shared / name
            if d.exists():
                count = len(list(d.glob("*")))
                if count:
                    sections.append({"name": f"shared/{name}", "count": count})
    return sections

@app.get("/api/{user_id}/artifacts/{section}")
def list_artifacts(user_id: str, section: str):
    base = _artifacts_dir(user_id) / section
    if not base.exists():
        return []
    return _list_dir(base)

@app.get("/api/{user_id}/artifacts/{section}/{subsection}")
def list_artifacts_sub(user_id: str, section: str, subsection: str):
    """List files in a subdirectory (e.g. writings/project-name/)."""
    base = _artifacts_dir(user_id) / section / subsection
    if not base.exists():
        raise HTTPException(404)
    if base.is_file():
        return _read_file(base)
    return _list_dir(base)

@app.get("/api/{user_id}/artifacts/{section}/{subsection}/{filename}")
def read_artifact(user_id: str, section: str, subsection: str, filename: str):
    path = _artifacts_dir(user_id) / section / subsection / filename
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
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8384)
