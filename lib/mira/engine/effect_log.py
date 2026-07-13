"""Idempotent side-effect log for V3.1."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

from mira.kernel.ledger_ids import new_id
from mira.kernel.schema import to_jsonable, utc_now

EffectStatus = Literal[
    "planned",
    "executing",
    "started",
    "succeeded",
    "failed",
    "unknown",
    "reconciled_succeeded",
    "reconciled_failed",
    "compensated",
]

SUCCESS_STATUSES = {"succeeded", "reconciled_succeeded"}
OPEN_STATUSES = {"planned", "executing", "started", "unknown"}


@dataclass(frozen=True)
class ReconciliationResult:
    succeeded: bool
    detail: str = ""
    external_ref: str | None = None
    reconciliation_ref: str | None = None


@dataclass(frozen=True)
class EffectLogEntry:
    idempotency_key: str
    run_id: str
    pipeline: str
    action: str
    target: str
    status: EffectStatus
    detail: str = ""
    step_id: str = ""
    action_type: str = ""
    preview_hash: str = ""
    approval_token_id: str | None = None
    replay_bundle_ref: str = ""
    external_ref: str | None = None
    executed_at: datetime | None = None
    reconciliation_ref: str | None = None
    effect_id: str = field(default_factory=lambda: new_id("effectlog"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EffectLogEntry":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        executed_at = data.get("executed_at")
        if isinstance(executed_at, str):
            executed_at = datetime.fromisoformat(executed_at.replace("Z", "+00:00"))
        return cls(
            idempotency_key=data["idempotency_key"],
            run_id=data["run_id"],
            pipeline=data["pipeline"],
            action=data["action"],
            target=data["target"],
            status=data["status"],
            detail=data.get("detail", ""),
            step_id=data.get("step_id", ""),
            action_type=data.get("action_type", data.get("action", "")),
            preview_hash=data.get("preview_hash", ""),
            approval_token_id=data.get("approval_token_id"),
            replay_bundle_ref=data.get("replay_bundle_ref", ""),
            external_ref=data.get("external_ref"),
            executed_at=executed_at,
            reconciliation_ref=data.get("reconciliation_ref"),
            effect_id=data.get("effect_id") or new_id("effectlog"),
            timestamp=timestamp,
        )


class EffectLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: EffectLogEntry) -> EffectLogEntry:
        existing = self.get_by_idempotency_key(entry.idempotency_key)
        if existing is not None and existing.status in SUCCESS_STATUSES:
            return existing
        return self._append_raw(entry)

    def _append_raw(self, entry: EffectLogEntry) -> EffectLogEntry:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry

    def attach_replay_bundle(
        self,
        idempotency_key: str,
        replay_bundle_ref: str,
        detail: str = "",
    ) -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        if current.replay_bundle_ref:
            return current
        return self._append_raw(
            self._transition(
                current,
                current.status,
                detail or current.detail,
                replay_bundle_ref=replay_bundle_ref,
            )
        )

    def plan(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        pipeline: str,
        action: str,
        target: str,
        detail: str = "",
        step_id: str = "",
        preview_hash: str = "",
        approval_token_id: str | None = None,
        replay_bundle_ref: str = "",
    ) -> EffectLogEntry:
        return self.append(
            EffectLogEntry(
                idempotency_key=idempotency_key,
                run_id=run_id,
                pipeline=pipeline,
                action=action,
                target=target,
                status="planned",
                detail=detail,
                step_id=step_id,
                action_type=action,
                preview_hash=preview_hash,
                approval_token_id=approval_token_id,
                replay_bundle_ref=replay_bundle_ref,
            )
        )

    def mark_executing(self, idempotency_key: str, detail: str = "") -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        return self.append(self._transition(current, "executing", detail or current.detail))

    def mark_succeeded(
        self,
        idempotency_key: str,
        detail: str = "",
        *,
        external_ref: str | None = None,
    ) -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        return self.append(
            self._transition(
                current,
                "succeeded",
                detail or current.detail,
                external_ref=external_ref if external_ref is not None else current.external_ref,
                executed_at=utc_now(),
            )
        )

    def mark_failed(self, idempotency_key: str, detail: str = "") -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        return self.append(self._transition(current, "failed", detail or current.detail))

    def mark_unknown(self, idempotency_key: str, detail: str = "") -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        if current.status in SUCCESS_STATUSES:
            return current
        return self.append(self._transition(current, "unknown", detail or "effect result unknown"))

    def reconcile(
        self,
        idempotency_key: str,
        *,
        succeeded: bool,
        detail: str = "",
        external_ref: str | None = None,
        reconciliation_ref: str | None = None,
    ) -> EffectLogEntry:
        current = self._require_existing(idempotency_key)
        status: EffectStatus = "reconciled_succeeded" if succeeded else "reconciled_failed"
        return self.append(
            self._transition(
                current,
                status,
                detail or current.detail,
                external_ref=external_ref if external_ref is not None else current.external_ref,
                reconciliation_ref=reconciliation_ref,
                executed_at=current.executed_at or (utc_now() if succeeded else None),
            )
        )

    def reconcile_unknowns(
        self,
        resolver: Callable[[EffectLogEntry], bool | ReconciliationResult | None],
    ) -> list[EffectLogEntry]:
        reconciled: list[EffectLogEntry] = []
        for entry in self.unresolved():
            result = resolver(entry)
            if result is None:
                continue
            if isinstance(result, ReconciliationResult):
                reconciled.append(
                    self.reconcile(
                        entry.idempotency_key,
                        succeeded=result.succeeded,
                        detail=result.detail,
                        external_ref=result.external_ref,
                        reconciliation_ref=result.reconciliation_ref,
                    )
                )
            else:
                reconciled.append(self.reconcile(entry.idempotency_key, succeeded=result))
        return reconciled

    def list(self, limit: int | None = None) -> list[EffectLogEntry]:
        if not self.path.exists():
            return []
        rows: list[EffectLogEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(EffectLogEntry.from_dict(json.loads(line)))
        rows.sort(key=lambda row: row.timestamp)
        if limit is not None:
            return rows[-limit:]
        return rows

    def get_by_idempotency_key(self, key: str) -> EffectLogEntry | None:
        for entry in reversed(self.list()):
            if entry.idempotency_key == key:
                return entry
        return None

    def unresolved(self) -> list[EffectLogEntry]:
        latest: dict[str, EffectLogEntry] = {}
        for entry in self.list():
            latest[entry.idempotency_key] = entry
        return [entry for entry in latest.values() if entry.status in OPEN_STATUSES]

    def _require_existing(self, idempotency_key: str) -> EffectLogEntry:
        current = self.get_by_idempotency_key(idempotency_key)
        if current is None:
            raise KeyError(f"No effect log entry for idempotency key: {idempotency_key}")
        return current

    def _transition(
        self,
        current: EffectLogEntry,
        status: EffectStatus,
        detail: str,
        *,
        external_ref: str | None = None,
        reconciliation_ref: str | None = None,
        executed_at: datetime | None = None,
        replay_bundle_ref: str | None = None,
    ) -> EffectLogEntry:
        return EffectLogEntry(
            idempotency_key=current.idempotency_key,
            run_id=current.run_id,
            pipeline=current.pipeline,
            action=current.action,
            target=current.target,
            status=status,
            detail=detail,
            step_id=current.step_id,
            action_type=current.action_type or current.action,
            preview_hash=current.preview_hash,
            approval_token_id=current.approval_token_id,
            replay_bundle_ref=replay_bundle_ref if replay_bundle_ref is not None else current.replay_bundle_ref,
            external_ref=external_ref if external_ref is not None else current.external_ref,
            executed_at=executed_at if executed_at is not None else current.executed_at,
            reconciliation_ref=reconciliation_ref if reconciliation_ref is not None else current.reconciliation_ref,
        )
