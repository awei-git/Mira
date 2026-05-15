"""Risk grants for V3.1 tool actions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from mira.kernel.ledger_ids import new_id
from mira.kernel.schema import to_jsonable, utc_now
from mira.policies.action_risk import risk_requires_grant

ActionRisk = Literal["read", "draft", "write_internal", "publish_public", "code_config", "memory_kernel", "destructive"]


@dataclass(frozen=True)
class RiskGrant:
    action: str
    risk: ActionRisk
    granted_by: str
    scope: str
    expires_at: datetime
    grant_id: str = field(default_factory=lambda: new_id("grant"))
    created_at: datetime = field(default_factory=utc_now)

    def permits(self, action: str, risk: ActionRisk, scope: str, now: datetime | None = None) -> bool:
        now = now or utc_now()
        return self.action == action and self.risk == risk and self.scope == scope and now <= self.expires_at

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RiskGrant":
        return cls(
            action=data["action"],
            risk=data["risk"],
            granted_by=data["granted_by"],
            scope=data["scope"],
            expires_at=_parse_dt(data["expires_at"]),
            grant_id=data.get("grant_id") or new_id("grant"),
            created_at=_parse_dt(data.get("created_at")),
        )


@dataclass(frozen=True)
class ApprovalRequest:
    action: str
    risk: ActionRisk
    scope: str
    reason: str
    run_id: str
    request_id: str = field(default_factory=lambda: new_id("approval"))
    created_at: datetime = field(default_factory=utc_now)
    status: Literal["pending", "approved", "denied", "expired"] = "pending"

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalRequest":
        return cls(
            action=data["action"],
            risk=data["risk"],
            scope=data["scope"],
            reason=data["reason"],
            run_id=data["run_id"],
            request_id=data.get("request_id") or new_id("approval"),
            created_at=_parse_dt(data.get("created_at")),
            status=data.get("status", "pending"),
        )


class ApprovalStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def request(self, request: ApprovalRequest) -> ApprovalRequest:
        existing = self.pending(action=request.action, risk=request.risk, scope=request.scope, run_id=request.run_id)
        if existing is not None:
            return existing
        self._append(request.to_dict())
        return request

    def grant(self, request_id: str, *, granted_by: str, ttl_minutes: int = 60) -> RiskGrant:
        request = self.get_request(request_id)
        if request is None:
            raise KeyError(f"No approval request: {request_id}")
        grant = issue_risk_grant(
            action=request.action,
            risk=request.risk,
            granted_by=granted_by,
            scope=request.scope,
            ttl_minutes=ttl_minutes,
        )
        self._append({"kind": "grant", **grant.to_dict(), "request_id": request_id})
        return grant

    def list_requests(self, status: str | None = None) -> list[ApprovalRequest]:
        requests: list[ApprovalRequest] = []
        rows = self._rows()
        approved_ids = {row.get("request_id") for row in rows if row.get("kind") == "grant"}
        for row in rows:
            if row.get("kind") == "grant":
                continue
            request = ApprovalRequest.from_dict(row)
            if request.request_id in approved_ids:
                request = ApprovalRequest(
                    action=request.action,
                    risk=request.risk,
                    scope=request.scope,
                    reason=request.reason,
                    run_id=request.run_id,
                    request_id=request.request_id,
                    created_at=request.created_at,
                    status="approved",
                )
            if status is None or request.status == status:
                requests.append(request)
        return requests

    def list_grants(self) -> list[RiskGrant]:
        return [RiskGrant.from_dict(row) for row in self._rows() if row.get("kind") == "grant"]

    def pending(self, *, action: str, risk: ActionRisk, scope: str, run_id: str) -> ApprovalRequest | None:
        for request in reversed(self.list_requests(status="pending")):
            if (
                request.action == action
                and request.risk == risk
                and request.scope == scope
                and request.run_id == run_id
            ):
                return request
        return None

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        for request in self.list_requests():
            if request.request_id == request_id:
                return request
        return None

    def find_grant(self, *, action: str, risk: ActionRisk, scope: str, now: datetime | None = None) -> RiskGrant | None:
        for grant in reversed(self.list_grants()):
            if grant.permits(action, risk, scope, now=now):
                return grant
        return None

    def _append(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def issue_risk_grant(
    *,
    action: str,
    risk: ActionRisk,
    granted_by: str,
    scope: str,
    ttl_minutes: int = 60,
) -> RiskGrant:
    return RiskGrant(
        action=action,
        risk=risk,
        granted_by=granted_by,
        scope=scope,
        expires_at=utc_now() + timedelta(minutes=ttl_minutes),
    )


def grant_required(risk: ActionRisk) -> bool:
    return risk_requires_grant(risk)


def _parse_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return utc_now()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
