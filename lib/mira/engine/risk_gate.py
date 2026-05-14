"""Risk grants for V3.1 tool actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from mira.kernel.ledger_ids import new_id
from mira.kernel.schema import to_jsonable, utc_now

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
    return risk in {"publish_public", "code_config", "memory_kernel", "destructive"}
