"""Baseline capture for V3.1 North Star evals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalStore
from mira.evals import (
    _is_synthetic_task_fixture_record,
    build_incident_events,
    build_operational_eval_bundle,
    evaluate_briefing_interest_fit,
    evaluate_failure_reduction,
    evaluate_memory_health,
    evaluate_voice_stability,
)
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
    causal_evidence: list | None = None,
    capture_date: date | None = None,
    window_days: int = 7,
) -> BaselineCaptureResult:
    capture_date = capture_date or date.today()
    date_key = capture_date.isoformat().replace("-", "_")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    window_start, window_end = _capture_window(capture_date, window_days)
    records_all = _filter_by_timestamp(ledger.list(), window_start, window_end)
    records = [record for record in records_all if not _is_synthetic_task_fixture_record(record)]
    commits = _filter_by_timestamp(commit_log.list(), window_start, window_end)
    effects = _filter_by_timestamp(effect_log.list(), window_start, window_end)
    causal_evidence_window = _filter_by_timestamp(causal_evidence or [], window_start, window_end)
    approval_events = _filter_by_timestamp(approval_store.list_events(), window_start, window_end)
    operational = build_operational_eval_bundle(
        records,
        commits,
        effects,
        causal_evidence_window,
        approval_events,
    )
    failure_reduction = evaluate_failure_reduction(records)
    voice = evaluate_voice_stability(records)
    briefing_interest = evaluate_briefing_interest_fit(records)
    memory_health = evaluate_memory_health(commits, records)
    incident_events = build_incident_events(records, effects)
    side_effect_count = max(len(effects), 1)
    approval_minutes = round(
        sum(float(getattr(event, "human_minutes", 0.0) or 0.0) for event in approval_events),
        4,
    )
    metadata = {
        "date_key": date_key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "record_count": len(records),
        "synthetic_record_count_excluded": len(records_all) - len(records),
        "commit_count": len(commits),
        "effect_count": len(effects),
        "causal_evidence_count": len(causal_evidence_window),
        "approval_event_count": len(approval_events),
    }
    baseline_data = {
        "operational": {
            **metadata,
            "repeat_error_rate": failure_reduction.repeat_error_rate,
            "post_scar_recurrence_rate": failure_reduction.post_scar_recurrence_rate,
            "scar_prevention_rate": failure_reduction.scar_prevention_rate,
            "repeated_error_score": operational.scorecard.repeated_error,
            "incident_rate_per_100_side_effects": round(_ratio(len(incident_events), side_effect_count) * 100, 4),
        },
        "voice": {
            **metadata,
            "voice_sample_count": voice.sample_count,
            "voice_score_mean": voice.voice_score_mean,
            "voice_score_std": voice.voice_score_std,
            "generic_failure_rate": voice.generic_failure_rate,
        },
        "briefing_interest": {
            **metadata,
            "briefing_sample_count": briefing_interest.sample_count,
            "briefing_item_count": briefing_interest.item_count,
            "briefing_precision_at_5": briefing_interest.precision_at_5,
            "briefing_action_rate": briefing_interest.action_rate,
            "briefing_feedback_items": briefing_interest.feedback_item_count,
            "briefing_feedback_coverage_rate": briefing_interest.feedback_coverage_rate,
            "briefing_blind_sample_items": briefing_interest.blind_sample_count,
        },
        "approval_burden": {
            **metadata,
            "approval_minutes_per_week": approval_minutes,
            "approval_requests_per_100_side_effects": round(_ratio(len(approval_events), side_effect_count) * 100, 4),
            "approval_safety_score": operational.scorecard.approval_safety,
            "unapproved_high_risk_actions": operational.scorecard.unapproved_high_risk_action,
        },
        "memory_audit": {
            **metadata,
            "critical_pollution_count": memory_health.critical_pollution_count,
            "snapshot_contamination_rate": memory_health.snapshot_contamination_rate,
            "memory_precision": memory_health.memory_precision,
            "unsupported_claim_rate": memory_health.unsupported_claim_rate,
            "quarantine_recall": memory_health.quarantine_recall,
        },
        "trace_completeness": {
            **metadata,
            "trace_completeness": operational.scorecard.traceability,
            "orphan_action_count": operational.scorecard.orphan_important_action,
            "causal_link_validity": operational.scorecard.causal_link_validity,
            "l4_required_causal_evidence": operational.scorecard.l4_required_causal_evidence,
        },
    }
    paths: dict[str, str] = {}
    for name, body in baseline_data.items():
        path = output / f"{name}_{date_key}.json"
        path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        paths[name] = str(path)
    return BaselineCaptureResult(date_key=date_key, paths=paths)


def _capture_window(capture_date: date, window_days: int) -> tuple[datetime, datetime]:
    days = max(1, int(window_days))
    end = datetime(capture_date.year, capture_date.month, capture_date.day, tzinfo=timezone.utc) + timedelta(days=1)
    return end - timedelta(days=days), end


def _filter_by_timestamp(items: list, start: datetime, end: datetime) -> list:
    return [item for item in items if start <= _item_timestamp(item) < end]


def _item_timestamp(item) -> datetime:
    timestamp = getattr(item, "timestamp", None)
    if timestamp is None:
        timestamp = getattr(item, "created_at", None)
    if timestamp is None:
        timestamp = getattr(item, "requested_at", None)
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
