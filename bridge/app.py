"""Mira Bridge: task API for Codex (Mac) -> Mira (cloud).

Codex submits tasks; Mira's scheduler drains the queue and writes results back.
Auth: X-Bridge-Token header, or Authorization: Bearer <token>.
Served behind the Cloudflare Tunnel under the /bridge/ path prefix.
"""
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

BASE = "/opt/mira-bridge"
TASKS = os.path.join(BASE, "tasks")


def load_token():
    with open(os.path.join(BASE, ".token")) as f:
        return f.read().strip()


TOKEN = load_token()
app = FastAPI()


@app.middleware("http")
async def strip_bridge_prefix(request, call_next):
    p = request.url.path
    if p == "/bridge":
        request.scope["path"] = "/"
    elif p.startswith("/bridge/"):
        request.scope["path"] = p[len("/bridge"):]
    return await call_next(request)


def check(authorization, x_bridge_token):
    tok = None
    if x_bridge_token:
        tok = x_bridge_token.strip()
    elif authorization and authorization.startswith("Bearer "):
        tok = authorization[7:].strip()
    if not tok:
        raise HTTPException(401, "missing token")
    if not hmac.compare_digest(tok, TOKEN):
        raise HTTPException(403, "bad token")


class TaskIn(BaseModel):
    kind: str = "agent"   # agent = needs Mira the agent; ping = link test (auto pong)
    title: str = ""
    body: str = ""
    payload: dict = {}


def now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    return {"ok": True, "time": now()}


@app.post("/v1/tasks")
def create_task(
    t: TaskIn,
    authorization: str = Header(None),
    x_bridge_token: str = Header(None),
):
    check(authorization, x_bridge_token)
    tid = uuid.uuid4().hex[:12]
    task = {
        "id": tid,
        "kind": t.kind,
        "title": t.title,
        "body": t.body,
        "payload": t.payload,
        "status": "pending",
        "created_at": now(),
        "updated_at": now(),
        "result": None,
    }
    if t.kind == "ping":
        task["status"] = "done"
        task["result"] = "pong"
    os.makedirs(TASKS, exist_ok=True)
    with open(os.path.join(TASKS, tid + ".json"), "w") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
    return {"id": tid, "status": task["status"], "result": task["result"]}


@app.get("/v1/tasks/{tid}")
def get_task(
    tid: str,
    authorization: str = Header(None),
    x_bridge_token: str = Header(None),
):
    check(authorization, x_bridge_token)
    if not tid.isalnum():
        raise HTTPException(400, "bad id")
    p = os.path.join(TASKS, tid + ".json")
    if not os.path.exists(p):
        raise HTTPException(404, "not found")
    with open(p) as f:
        return json.load(f)

