"""Risk grants for V3.1 tool actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from mira.kernel.ledger_ids import new_id
from mira.kernel.schema import to_jsonable, utc_now
from mira.policies.action_risk import risk_requires_grant

ActionRisk = Literal[
    "read",
    "draft",
    "write_internal",
    "external_provider",
    "publish_public",
    "financial_external",
    "health_external",
    "code_config",
    "memory_kernel",
    "destructive",
]


@dataclass(frozen=True)
class RiskGrant:
    action: str
    risk: ActionRisk
    granted_by: str
    scope: str
    expires_at: datetime
    preview_hash: str = ""
    grant_id: str = field(default_factory=lambda: new_id("grant"))
    created_at: datetime = field(default_factory=utc_now)

    def permits(
        self,
        action: str,
        risk: ActionRisk,
        scope: str,
        now: datetime | None = None,
        preview_hash: str | None = None,
    ) -> bool:
        now = now or utc_now()
        if not (self.action == action and self.risk == risk and self.scope == scope and now <= self.expires_at):
            return False
        if self.preview_hash:
            return preview_hash == self.preview_hash
        return True

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
            preview_hash=data.get("preview_hash", ""),
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
    preview_hash: str = ""
    request_id: str = field(default_factory=lambda: new_id("approval"))
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime = field(default_factory=lambda: utc_now() + timedelta(hours=24))
    status: Literal["pending", "approved", "denied", "edited", "expired"] = "pending"

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ApprovalRequest":
        created_at = _parse_dt(data.get("created_at"))
        return cls(
            action=data["action"],
            risk=data["risk"],
            scope=data["scope"],
            reason=data["reason"],
            run_id=data["run_id"],
            preview_hash=data.get("preview_hash", ""),
            request_id=data.get("request_id") or new_id("approval"),
            created_at=created_at,
            expires_at=(
                _parse_dt(data.get("expires_at")) if data.get("expires_at") else created_at + timedelta(hours=24)
            ),
            status=data.get("status", "pending"),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expires_at


@dataclass(frozen=True)
class ApprovalEvent:
    id: str
    run_id: str
    action_id: str
    action_type: str
    risk_tier: str
    requested_at: datetime
    resolved_at: datetime | None
    decision: Literal["pending", "approved", "rejected", "edited", "expired"]
    human_minutes: float | None

    def to_dict(self) -> dict:
        return to_jsonable(self)


class ApprovalStore:
    DEFAULT_PENDING_BUDGET = 10
    DEFAULT_QUEUE_AGE_LIMIT_MINUTES = 24 * 60
    LOW_RISK_DIGEST_RISKS = {"publish_public"}

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def request(self, request: ApprovalRequest) -> ApprovalRequest:
        existing = self.pending(
            action=request.action,
            risk=request.risk,
            scope=request.scope,
            run_id=request.run_id,
            preview_hash=request.preview_hash,
        )
        if existing is not None:
            return existing
        self._append(request.to_dict())
        return request

    def grant(self, request_id: str, *, granted_by: str, ttl_minutes: int = 60) -> RiskGrant:
        request = self.get_request(request_id)
        if request is None:
            raise KeyError(f"No approval request: {request_id}")
        if request.status != "pending" or request.is_expired():
            raise PermissionError(f"Approval request is not pending: {request_id}")
        grant = issue_risk_grant(
            action=request.action,
            risk=request.risk,
            granted_by=granted_by,
            scope=request.scope,
            ttl_minutes=ttl_minutes,
            preview_hash=request.preview_hash,
        )
        self._append({"kind": "grant", **grant.to_dict(), "request_id": request_id})
        return grant

    def deny(self, request_id: str, *, decided_by: str, reason: str = "") -> ApprovalEvent:
        return self._resolve(request_id, decision="rejected", decided_by=decided_by, reason=reason)

    def edit(self, request_id: str, *, decided_by: str, reason: str = "") -> ApprovalEvent:
        return self._resolve(request_id, decision="edited", decided_by=decided_by, reason=reason)

    def expire(self, request_id: str, *, decided_by: str = "system", reason: str = "") -> ApprovalEvent:
        return self._resolve(request_id, decision="expired", decided_by=decided_by, reason=reason)

    def expire_overdue(self, now: datetime | None = None) -> list[ApprovalEvent]:
        now = now or utc_now()
        expired: list[ApprovalEvent] = []
        resolved_ids = {
            str(row.get("request_id"))
            for row in self._rows()
            if row.get("kind") in {"grant", "decision"} and row.get("request_id")
        }
        unresolved_requests = [
            ApprovalRequest.from_dict(row) for row in self._rows() if row.get("kind") not in {"grant", "decision"}
        ]
        for request in unresolved_requests:
            if request.request_id in resolved_ids:
                continue
            if request.is_expired(now):
                expired.append(self.expire(request.request_id, reason="approval request expired"))
        return expired

    def capacity_state(
        self,
        *,
        pending_budget: int = DEFAULT_PENDING_BUDGET,
        queue_age_limit_minutes: int = DEFAULT_QUEUE_AGE_LIMIT_MINUTES,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or utc_now()
        pending = self.list_requests(status="pending", now=now)
        ages = sorted(_age_minutes(request.created_at, now) for request in pending)
        p95_age = ages[min(len(ages) - 1, int(len(ages) * 0.95))] if ages else 0.0
        over_budget = len(pending) > pending_budget or p95_age > queue_age_limit_minutes
        return {
            "pending": len(pending),
            "budget": pending_budget,
            "remaining": max(0, pending_budget - len(pending)),
            "queue_age_p95_minutes": round(p95_age, 2),
            "over_budget": over_budget,
            "auto_pause_noncritical": over_budget,
        }

    def list_requests(self, status: str | None = None, *, now: datetime | None = None) -> list[ApprovalRequest]:
        now = now or utc_now()
        requests: list[ApprovalRequest] = []
        rows = self._rows()
        status_by_id: dict[str, str] = {}
        for row in rows:
            request_id = row.get("request_id")
            if not request_id:
                continue
            if row.get("kind") == "grant":
                status_by_id[str(request_id)] = "approved"
            elif row.get("kind") == "decision":
                decision = str(row.get("decision", "pending"))
                status_by_id[str(request_id)] = "denied" if decision == "rejected" else decision
        for row in rows:
            if row.get("kind") in {"grant", "decision"}:
                continue
            request = ApprovalRequest.from_dict(row)
            resolved_status = status_by_id.get(request.request_id)
            if resolved_status:
                request = ApprovalRequest(
                    action=request.action,
                    risk=request.risk,
                    scope=request.scope,
                    reason=request.reason,
                    run_id=request.run_id,
                    preview_hash=request.preview_hash,
                    request_id=request.request_id,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                    status=resolved_status,
                )
            elif request.is_expired(now):
                request = ApprovalRequest(
                    action=request.action,
                    risk=request.risk,
                    scope=request.scope,
                    reason=request.reason,
                    run_id=request.run_id,
                    preview_hash=request.preview_hash,
                    request_id=request.request_id,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                    status="expired",
                )
            if status is None or request.status == status:
                requests.append(request)
        return requests

    def list_grants(self) -> list[RiskGrant]:
        return [RiskGrant.from_dict(row) for row in self._rows() if row.get("kind") == "grant"]

    def list_events(self) -> list[ApprovalEvent]:
        rows = self._rows()
        resolution_by_request: dict[str, dict] = {}
        for row in rows:
            if row.get("kind") not in {"grant", "decision"}:
                continue
            request_id = row.get("request_id")
            if request_id:
                resolution_by_request[str(request_id)] = row
        events: list[ApprovalEvent] = []
        for row in rows:
            if row.get("kind") in {"grant", "decision"}:
                continue
            request = ApprovalRequest.from_dict(row)
            resolution = resolution_by_request.get(request.request_id)
            if resolution and resolution.get("kind") == "grant":
                grant = RiskGrant.from_dict(resolution)
                decision = "approved"
                resolved_at = grant.created_at
            elif resolution and resolution.get("kind") == "decision":
                decision = str(resolution.get("decision", "pending"))
                resolved_at = _parse_dt(resolution.get("created_at"))
            else:
                if request.is_expired():
                    decision = "expired"
                    resolved_at = request.expires_at
                else:
                    decision = "pending"
                    resolved_at = None
            human_minutes = None
            if resolved_at is not None:
                human_minutes = max(0.0, round((resolved_at - request.created_at).total_seconds() / 60, 4))
            events.append(
                ApprovalEvent(
                    id=request.request_id,
                    run_id=request.run_id,
                    action_id=f"{request.scope}:{request.action}",
                    action_type=request.action,
                    risk_tier=request.risk,
                    requested_at=request.created_at,
                    resolved_at=resolved_at,
                    decision=decision,  # type: ignore[arg-type]
                    human_minutes=human_minutes,
                )
            )
        return events

    def low_risk_digest(self, *, max_items: int = 20) -> dict[str, object] | None:
        requests = [
            request for request in self.list_requests(status="pending") if self._is_low_risk_digest_candidate(request)
        ]
        if len(requests) < 2:
            return None
        selected = sorted(requests, key=lambda request: request.created_at)[:max_items]
        request_ids = [request.request_id for request in selected]
        digest_hash = hashlib.sha256("|".join(request_ids).encode("utf-8")).hexdigest()[:16]
        return {
            "digest_id": f"approval_digest:{digest_hash}",
            "request_count": len(selected),
            "request_ids": request_ids,
            "actions": sorted({request.action for request in selected}),
            "scopes": sorted({request.scope for request in selected}),
            "risks": sorted({request.risk for request in selected}),
            "oldest_created_at": min(request.created_at for request in selected),
            "next_expires_at": min(request.expires_at for request in selected),
            "preview_hashes": [request.preview_hash for request in selected if request.preview_hash],
            "estimated_human_minutes": round(max(1.0, len(selected) * 0.5), 2),
        }

    def pending(
        self,
        *,
        action: str,
        risk: ActionRisk,
        scope: str,
        run_id: str,
        preview_hash: str | None = None,
    ) -> ApprovalRequest | None:
        for request in reversed(self.list_requests(status="pending")):
            if (
                request.action == action
                and request.risk == risk
                and request.scope == scope
                and request.run_id == run_id
                and (preview_hash is None or request.preview_hash == preview_hash)
            ):
                return request
        return None

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        for request in self.list_requests():
            if request.request_id == request_id:
                return request
        return None

    def find_grant(
        self,
        *,
        action: str,
        risk: ActionRisk,
        scope: str,
        now: datetime | None = None,
        preview_hash: str | None = None,
    ) -> RiskGrant | None:
        for grant in reversed(self.list_grants()):
            if grant.permits(action, risk, scope, now=now, preview_hash=preview_hash):
                return grant
        return None

    def _is_low_risk_digest_candidate(self, request: ApprovalRequest) -> bool:
        return request.risk in self.LOW_RISK_DIGEST_RISKS and bool(request.preview_hash)

    def _resolve(
        self,
        request_id: str,
        *,
        decision: Literal["rejected", "edited", "expired"],
        decided_by: str,
        reason: str,
    ) -> ApprovalEvent:
        request = self.get_request(request_id)
        if request is None:
            raise KeyError(f"No approval request: {request_id}")
        created_at = utc_now()
        self._append(
            {
                "kind": "decision",
                "request_id": request_id,
                "decision": decision,
                "decided_by": decided_by,
                "reason": reason,
                "created_at": created_at.isoformat(),
            }
        )
        return next(event for event in self.list_events() if event.id == request_id)

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
    preview_hash: str = "",
) -> RiskGrant:
    return RiskGrant(
        action=action,
        risk=risk,
        granted_by=granted_by,
        scope=scope,
        preview_hash=preview_hash,
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


def _age_minutes(created_at: datetime, now: datetime) -> float:
    if created_at.tzinfo is None and now.tzinfo is not None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    if now.tzinfo is None and created_at.tzinfo is not None:
        now = now.replace(tzinfo=created_at.tzinfo)
    return max(0.0, (now - created_at).total_seconds() / 60)
