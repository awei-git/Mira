"""Local, content-only operational receipts in the existing obligation ledger.

No model dispatch, service control, public endpoint, or second queue. Receipts
must be supplied by a trusted host collector; this module does not infer health
from missing files or SMTP acceptance.
"""

from datetime import datetime, timezone
import json
import re

from obligations import Conflict, digest, encoded

UNITS = {
    "mira-creator.service",
    "tetra-market-collect.service",
    "tetra-research-morning.service",
    "tetra-research-evening.service",
    "tetra-mail-morning.service",
    "tetra-mail-evening.service",
}
UNITS |= {unit.removesuffix(".service") + ".timer" for unit in UNITS}
REASONS = {
    "produced": {"artifact_recorded", "formal_report_delivered"},
    "empty": {"no_output"},
    "skipped": {"outside_edition_window", "already_processed", "no_ready_seed"},
    "failed": {
        "service_failed",
        "source_unavailable",
        "research_unaccepted",
        "delivery_unconfirmed",
        "receipt_invalid",
    },
}
FIELDS = {"unit", "run_id", "completed_at", "outcome", "reason"}


def normalize(receipt):
    if (
        not isinstance(receipt, dict)
        or set(receipt) != FIELDS
        or not all(isinstance(value, str) for value in receipt.values())
    ):
        raise ValueError("operational_receipt_fields_invalid")
    if receipt["unit"] not in UNITS:
        raise ValueError("operational_unit_invalid")
    if not isinstance(receipt["run_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", receipt["run_id"]):
        raise ValueError("stable_invocation_id_required")
    if receipt["outcome"] not in REASONS or receipt["reason"] not in REASONS[receipt["outcome"]]:
        raise ValueError("operational_outcome_reason_invalid")
    value = datetime.fromisoformat(receipt["completed_at"])
    if value.tzinfo is None:
        raise ValueError("receipt_requires_timezone")
    return {**receipt, "completed_at": value.astimezone(timezone.utc).isoformat()}


def record_operation(ledger, receipt, *, empty_threshold=4):
    """Atomically append observation + optional alert, deduplicated by invocation.

    Only consecutive explicit empty outcomes count. Missing samples are unknown.
    New observations must arrive in completion order per unit; replay of an older
    identical invocation is allowed. Threshold changes require a reviewed rollout.
    """
    if type(empty_threshold) is not int or not 1 <= empty_threshold <= 96:
        raise ValueError("invalid_empty_threshold")
    row = normalize(receipt)
    if row["completed_at"] > ledger.now():
        raise ValueError("future_operation_receipt")
    body = {**row, "empty_threshold": empty_threshold}
    key = "operation:" + row["unit"] + ":" + row["run_id"]
    identifier = digest(key.encode())
    state_key = "operations:" + row["unit"]
    with ledger.transaction("Record content operation and deduplicated alert (issue22 task5)") as db:
        old = db.execute("SELECT * FROM obligations WHERE idempotency_key=?", (key,)).fetchone()
        if old:
            if old["payload_json"] != encoded(body):
                raise Conflict("operation_receipt_conflict")
            return ledger.unpack(old)["result"]
        previous = db.execute("SELECT value FROM meta WHERE key=?", (state_key,)).fetchone()
        state = json.loads(previous["value"]) if previous else {}
        if state and state["empty_threshold"] != empty_threshold:
            raise Conflict("operation_threshold_change_requires_migration")
        if state and row["completed_at"] <= state["completed_at"]:
            raise Conflict("operation_out_of_order")
        streak = state.get("empty_streak", 0) + 1 if row["outcome"] == "empty" else 0
        alert = None
        if row["outcome"] in {"failed", "skipped"}:
            alert = "operation." + row["outcome"]
        elif streak == empty_threshold:
            alert = "operation.no_output"
        now = ledger.now()
        # Terminal means receipt ingestion completed, never that production succeeded.
        db.execute(
            "INSERT INTO obligations (id,kind,owner,created_by,status,payload_json,result_json,idempotency_key,revision,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
            (
                identifier,
                "operation.observation",
                "mira-aws",
                "mira-aws",
                "succeeded",
                encoded(body),
                None,
                key,
                now,
                now,
            ),
        )
        observed = ledger._event(db, identifier, "operation.observed", "mira-aws", row, key + ":observed")
        event = None
        if alert:
            event = ledger._event(
                db,
                identifier,
                alert,
                "mira-aws",
                {**row, "empty_streak": streak, "empty_threshold": empty_threshold},
                key + ":alert",
            )
        result = {"observation_event_id": observed, "alert_event_id": event, "empty_streak": streak}
        db.execute("UPDATE obligations SET result_json=? WHERE id=?", (encoded(result), identifier))
        db.execute(
            "INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (
                state_key,
                encoded(
                    {"completed_at": row["completed_at"], "empty_streak": streak, "empty_threshold": empty_threshold}
                ),
            ),
        )
        return result
