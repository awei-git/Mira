"""Authenticated ledger API. Human authorization/polling belongs to Muse."""

import hmac
import uuid
from typing import Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from obligations import Conflict, Forbidden, OWNERS


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObligationIn(Input):
    kind: str
    owner: str
    payload: dict
    idempotency_key: str = Field(min_length=1, max_length=300)


class ClaimIn(Input):
    expected_revision: int = Field(ge=0)
    lease_seconds: int = Field(default=3600, ge=60, le=10800)


class TransitionIn(Input):
    expected_revision: int = Field(ge=0)
    status: str
    result: Optional[Union[dict, str]] = None


class TaskIn(Input):
    kind: str = "agent"
    title: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=50_000)
    payload: dict = Field(default_factory=dict)


def legacy_view(row):
    payload = row["payload"]
    status = {"queued": "pending", "claimed": "running", "running": "running", "succeeded": "done"}.get(
        row["status"], row["status"]
    )
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": payload.get("title", ""),
        "body": payload.get("body", ""),
        "payload": payload.get("payload", {}),
        "status": status,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "result": row["result"],
    }


def create_app(ledger, tokens):
    if not tokens or any(
        actor not in OWNERS or not isinstance(token, str) or not token for actor, token in tokens.items()
    ):
        raise ValueError("valid_role_tokens_required")
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("role_tokens_must_be_distinct")
    app = FastAPI()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        path = request.url.path
        if path == "/bridge":
            request.scope["path"] = "/"
        elif path.startswith("/bridge/"):
            request.scope["path"] = path[len("/bridge") :]
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length", "")
            if not length.isdecimal():
                return JSONResponse({"detail": "content_length_required"}, status_code=411)
            if int(length) > 65_536 or len(await request.body()) > 65_536:
                return JSONResponse({"detail": "body_too_large"}, status_code=413)
        return await call_next(request)

    def actor(authorization: Optional[str] = Header(None), x_bridge_token: Optional[str] = Header(None)):
        token = x_bridge_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else "")
        if not token:
            raise HTTPException(401, "missing token")
        matches = [
            name for name, expected in tokens.items() if hmac.compare_digest(token.strip().encode(), expected.encode())
        ]
        if not matches:
            raise HTTPException(403, "bad token")
        return matches[0]

    @app.exception_handler(Forbidden)
    async def forbidden(request, error):
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": "invalid_request_or_artifact"}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "not_found"}, status_code=404)

    @app.get("/health")
    def health():
        return {"ok": True, "time": ledger.now()}

    @app.post("/obligations")
    def create(body: ObligationIn, caller=Depends(actor)):
        return ledger.create(body.kind, body.owner, body.payload, body.idempotency_key, caller)

    @app.get("/obligations")
    def listing(
        owner: Optional[str] = None,
        status: Optional[str] = None,
        after: str = "",
        limit: int = Query(100, ge=1, le=500),
        caller=Depends(actor),
    ):
        return ledger.listing(owner=owner, status=status, after=after, limit=limit)

    @app.get("/obligations/{identifier}")
    def get(identifier: str, caller=Depends(actor)):
        return ledger.get(identifier)

    @app.post("/obligations/{identifier}/claim")
    def claim(identifier: str, body: ClaimIn, caller=Depends(actor)):
        return ledger.claim(identifier, caller, body.expected_revision, lease_seconds=body.lease_seconds)

    @app.post("/obligations/{identifier}/transition")
    def transition(identifier: str, body: TransitionIn, caller=Depends(actor)):
        return ledger.transition(identifier, caller, body.expected_revision, body.status, body.result)

    @app.get("/events")
    def events(
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        epoch: Optional[str] = None,
        caller=Depends(actor),
    ):
        return ledger.events(after=after, limit=limit, epoch=epoch)

    @app.get("/artifacts/{identifier}")
    def artifact(identifier: str, caller=Depends(actor)):
        body, sha = ledger.artifact(identifier)
        return Response(
            body,
            media_type="text/markdown",
            headers={"X-Content-SHA256": sha, "Content-Disposition": "attachment", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/v1/tasks")
    def create_task(body: TaskIn, idempotency_key: Optional[str] = Header(None), caller=Depends(actor)):
        if body.kind not in {"agent", "ping"}:
            raise Forbidden("legacy_task_kind_not_supported")
        row = ledger.create(
            body.kind,
            "mira-aws",
            body.model_dump(exclude={"kind"}),
            "legacy-create:" + (idempotency_key or uuid.uuid4().hex),
            caller,
        )
        view = legacy_view(row)
        return {key: view[key] for key in ("id", "status", "result")}

    @app.get("/v1/tasks/{identifier}")
    def get_task(identifier: str, caller=Depends(actor)):
        return legacy_view(ledger.get(identifier))

    return app
