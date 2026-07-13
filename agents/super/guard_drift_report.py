"""Weekly observation report for publish guard drift."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from config import LOGS_DIR, PROXY_DRIFT_THRESHOLD
except Exception:
    LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
    PROXY_DRIFT_THRESHOLD = 0.20


REPORT_PATH = LOGS_DIR / "guard_drift_weekly.md"
WINDOW_DAYS = 7
SAMPLE_LIMIT = 5

_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Decision:
    timestamp: datetime
    outcome: str
    guard: str
    source: str
    snippet: str
    reason: str = ""
    item_id: str = ""


def analyze(
    *,
    now: datetime | None = None,
    logs_dir: Path | None = None,
    report_path: Path | None = None,
    threshold: float | None = None,
) -> dict:
    """Write the weekly guard drift report and return a compact summary."""
    end = _as_utc(now or datetime.now(timezone.utc))
    start = end - timedelta(days=WINDOW_DAYS)
    prior_start = start - timedelta(days=WINDOW_DAYS)
    base_dir = Path(logs_dir) if logs_dir is not None else LOGS_DIR
    target = Path(report_path) if report_path is not None else REPORT_PATH
    drift_threshold = float(PROXY_DRIFT_THRESHOLD if threshold is None else threshold)

    decisions = _read_decisions(base_dir, prior_start, end)
    current_decisions = [d for d in decisions if start <= d.timestamp <= end]
    prior_decisions = [d for d in decisions if prior_start <= d.timestamp < start]
    guard_events = _read_guard_events(base_dir, prior_start, end)
    current_guard_events = [e for e in guard_events if start <= e["timestamp"] <= end]
    prior_guard_events = [e for e in guard_events if prior_start <= e["timestamp"] < start]

    current = _decision_stats(current_decisions)
    prior = _decision_stats(prior_decisions)
    guard_activity = _guard_activity_rows(current_guard_events)
    flags = _drift_flags(current, prior, current_guard_events, prior_guard_events, drift_threshold)
    report = _render_report(
        generated_at=end,
        start=start,
        end=end,
        prior_start=prior_start,
        threshold=drift_threshold,
        current=current,
        prior=prior,
        guard_activity=guard_activity,
        flags=flags,
        pass_samples=_samples(current_decisions, "pass"),
        block_samples=_samples(current_decisions, "block"),
    )
    _write_text_atomic(target, report)
    return {
        "report_path": str(target),
        "generated_at": end.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "current": current,
        "prior": prior,
        "guard_activity": guard_activity,
        "flags": flags,
        "summary": _summary(current, flags, target),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _compact(value: object, limit: int = 180) -> str:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    return text[:limit].rstrip()


def _entry_timestamp(entry: dict) -> datetime | None:
    for key in ("timestamp", "ts", "created_at"):
        timestamp = _parse_timestamp(entry.get(key))
        if timestamp is not None:
            return timestamp
    return None


def _decision_outcome(entry: dict) -> str:
    preflight = entry.get("preflight_result")
    if isinstance(preflight, dict) and preflight.get("passed") is False:
        return "block"
    for key in ("outcome", "result", "decision", "content_guard_result"):
        value = str(entry.get(key) or "").lower()
        if value in {"pass", "passed", "allow", "allowed", "approved"}:
            return "pass"
        if value in {"block", "blocked", "reject", "rejected", "deny", "denied", "fail", "failed"}:
            return "block"
    return "other"


def _read_decisions(base_dir: Path, start: datetime, end: datetime) -> list[Decision]:
    paths = sorted((base_dir / "decisions").glob("*.jsonl"))
    paths.extend(base_dir / name for name in ("agent_decisions.jsonl", "publish_decisions.jsonl"))
    decisions: list[Decision] = []
    for path in paths:
        for entry in _read_jsonl(path):
            timestamp = _entry_timestamp(entry)
            if timestamp is None or timestamp < start or timestamp > end:
                continue
            guard = _compact(entry.get("guard") or entry.get("action") or "decision", 80)
            reason = _compact(entry.get("reason") or entry.get("rationale"), 180)
            snippet = _compact(entry.get("target") or entry.get("article_id") or reason, 180)
            decisions.append(
                Decision(
                    timestamp=timestamp,
                    outcome=_decision_outcome(entry),
                    guard=guard or "decision",
                    source=path.name,
                    snippet=snippet,
                    reason=reason,
                    item_id=_compact(entry.get("task_id") or entry.get("article_id"), 80),
                )
            )
    return sorted(decisions, key=lambda item: item.timestamp)


def _read_guard_events(base_dir: Path, start: datetime, end: datetime) -> list[dict]:
    names = (
        "guard_fires.jsonl",
        "scaffolding_catches.jsonl",
        "scaffolding_rejections.jsonl",
        "scaffolding_audit.jsonl",
        "content_guard_rejections.jsonl",
    )
    events: list[dict] = []
    for name in names:
        for entry in _read_jsonl(base_dir / name):
            timestamp = _entry_timestamp(entry)
            if timestamp is None or timestamp < start or timestamp > end:
                continue
            events.append(
                {
                    "timestamp": timestamp,
                    "guard": _compact(entry.get("guard") or entry.get("guard_name") or name, 80),
                    "reason": _compact(entry.get("reason") or entry.get("trigger_reason"), 180),
                    "source": name,
                }
            )
    return sorted(events, key=lambda item: item["timestamp"])


def _decision_stats(decisions: list[Decision]) -> dict:
    total = len(decisions)
    passed = sum(item.outcome == "pass" for item in decisions)
    blocked = sum(item.outcome == "block" for item in decisions)
    by_guard: dict[str, int] = {}
    for item in decisions:
        by_guard[item.guard] = by_guard.get(item.guard, 0) + 1
    return {
        "total": total,
        "passed": passed,
        "blocked": blocked,
        "other": total - passed - blocked,
        "pass_rate": passed / total if total else None,
        "block_rate": blocked / total if total else None,
        "by_guard": dict(sorted(by_guard.items())),
    }


def _guard_activity_rows(events: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for event in events:
        guard = str(event.get("guard") or "guard")
        counts[guard] = counts.get(guard, 0) + 1
    return [{"guard": guard, "events": count} for guard, count in sorted(counts.items())]


def _rate_change(current: dict, prior: dict, key: str) -> float | None:
    current_rate = current.get(key)
    prior_rate = prior.get(key)
    if current_rate is None or prior_rate is None:
        return None
    return float(current_rate) - float(prior_rate)


def _drift_flags(
    current: dict,
    prior: dict,
    current_events: list[dict],
    prior_events: list[dict],
    threshold: float,
) -> list[str]:
    flags: list[str] = []
    for key, label in (("pass_rate", "pass rate"), ("block_rate", "block rate")):
        change = _rate_change(current, prior, key)
        if change is not None and abs(change) >= threshold:
            flags.append(f"{label} changed by {change:+.1%} (threshold {threshold:.1%})")
    if len(current_events) > max(3, len(prior_events) * 2):
        flags.append(f"guard events increased from {len(prior_events)} to {len(current_events)}")
    return flags


def _samples(decisions: list[Decision], outcome: str) -> list[Decision]:
    return [item for item in reversed(decisions) if item.outcome == outcome][:SAMPLE_LIMIT]


def _format_rate(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.1%}"


def _render_report(
    *,
    generated_at: datetime,
    start: datetime,
    end: datetime,
    prior_start: datetime,
    threshold: float,
    current: dict,
    prior: dict,
    guard_activity: list[dict],
    flags: list[str],
    pass_samples: list[Decision],
    block_samples: list[Decision],
) -> str:
    lines = [
        "# Weekly Guard Drift Report",
        "",
        f"Generated: {generated_at.isoformat()}",
        f"Current window: {start.date()} through {end.date()}",
        f"Prior window: {prior_start.date()} through {start.date()}",
        f"Drift threshold: {threshold:.1%}",
        "",
        "## Decision rates",
        "",
        "| Window | Decisions | Passed | Blocked | Pass rate | Block rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| Current | {current['total']} | {current['passed']} | {current['blocked']} | "
            f"{_format_rate(current['pass_rate'])} | {_format_rate(current['block_rate'])} |"
        ),
        (
            f"| Prior | {prior['total']} | {prior['passed']} | {prior['blocked']} | "
            f"{_format_rate(prior['pass_rate'])} | {_format_rate(prior['block_rate'])} |"
        ),
        "",
        "## Drift flags",
        "",
    ]
    lines.extend([f"- {flag}" for flag in flags] or ["- No threshold breach detected."])
    lines.extend(["", "## Guard activity", ""])
    lines.extend(
        [f"- {item['guard']}: {item['events']} events" for item in guard_activity] or ["- No guard events recorded."]
    )
    for heading, samples in (("Recent passes", pass_samples), ("Recent blocks", block_samples)):
        lines.extend(["", f"## {heading}", ""])
        if not samples:
            lines.append("- No samples recorded.")
            continue
        for sample in samples:
            detail = sample.snippet or sample.reason or "No detail recorded."
            lines.append(f"- {sample.timestamp.date()} · {sample.guard} · {detail}")
    return "\n".join(lines) + "\n"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(content, encoding="utf-8")
    temporary_path.replace(path)


def _summary(current: dict, flags: list[str], path: Path) -> str:
    status = "drift detected" if flags else "no threshold breach"
    return f"{status}; {current['total']} decisions; report: {path}"
