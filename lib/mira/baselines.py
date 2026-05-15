"""Baseline capture for V3.1 North Star evals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalStore
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.ledger import ExperienceLedger


@dataclass(frozen=True)
class BaselineCaptureResult:
    date_key: str
    paths: dict[str, str]


def capture_all_baselines(
    *,
    ledger: ExperienceLedger,
    commit_log: MemoryCommitLog,
    effect_log: EffectLog,
    approval_store: ApprovalStore,
    output_dir: Path | str,
    capture_date: date | None = None,
) -> BaselineCaptureResult:
    capture_date = capture_date or date.today()
    date_key = capture_date.isoformat().replace("-", "_")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records = ledger.list(limit=1000)
    commits = commit_log.list(limit=1000)
    effects = effect_log.list(limit=1000)
    approvals = approval_store.list_requests()
    failed = [record for record in records if record.outcome == "failed" or record.delta.what_failed]
    critical_pollution = [commit for commit in commits if commit.status in {"quarantined", "rejected"}]
    trace_complete = [
        record for record in records if record.causal_links or record.memory_commit_id or record.side_effect_refs
    ]
    baseline_data = {
        "operational": {
            "repeat_error_rate": _ratio(len(failed), len(records)),
            "post_scar_recurrence_rate": 0.0,
            "incident_rate_per_100_side_effects": round(_ratio(len(failed), len(effects)) * 100, 4),
        },
        "voice": {
            "voice_score_mean": 0.0,
            "voice_score_std": 0.0,
            "generic_failure_rate": 0.0,
        },
        "briefing_interest": {
            "briefing_precision_at_5": 0.0,
            "briefing_action_rate": 0.0,
        },
        "approval_burden": {
            "approval_minutes_per_week": 0.0,
            "approval_requests_per_100_side_effects": round(_ratio(len(approvals), len(effects)) * 100, 4),
        },
        "memory_audit": {
            "critical_pollution_count": len(critical_pollution),
            "snapshot_contamination_rate": 0.0,
        },
        "trace_completeness": {
            "trace_completeness": _ratio(len(trace_complete), len(records)),
            "orphan_action_count": len([effect for effect in effects if effect.status == "unknown"]),
        },
    }
    paths: dict[str, str] = {}
    for name, body in baseline_data.items():
        path = output / f"{name}_{date_key}.json"
        path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        paths[name] = str(path)
    return BaselineCaptureResult(date_key=date_key, paths=paths)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
