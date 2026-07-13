"""Daily collaboration loop memory helpers.

The app exposes one designated chat thread: ``disc_daily_collab``. This module
keeps a compact private summary for that thread and provides the prompt block
that discussion handlers can inject before replying.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOGS_DIR, MIRA_DIR, STATE_DIR, WRITINGS_OUTPUT_DIR

DAILY_COLLAB_ITEM_ID = "disc_daily_collab"
DAILY_COLLAB_TITLE = "Mira"
DAILY_COLLAB_TAG = "daily-collab"
DAILY_COLLAB_SUMMARY_FILE = STATE_DIR / "daily_collab_summary.md"
DAILY_COLLAB_REVIEW_FILE = STATE_DIR / "daily_collab_review.jsonl"
DAILY_COLLAB_WEEKLY_REVIEW_FILE = STATE_DIR / "daily_collab_weekly_review.md"
DAILY_COLLAB_ARTICLE_SEEDS_FILE = STATE_DIR / "daily_collab_article_seeds.jsonl"
DAILY_COLLAB_ARTICLE_BRIEFS_DIR = STATE_DIR / "daily_collab_article_briefs"
DAILY_COLLAB_INCIDENTS_FILE = STATE_DIR / "daily_collab_incidents.jsonl"
DAILY_COLLAB_MONITOR_CLOSURES_FILE = STATE_DIR / "daily_collab_monitor_closures.jsonl"
DAILY_COLLAB_OPERATOR_BRIEF_FILE = STATE_DIR / "daily_collab_operator_brief.md"
DAILY_COLLAB_OPERATOR_DELIVERIES_FILE = STATE_DIR / "daily_collab_operator_deliveries.jsonl"
PIPELINE_STALE_FILE = LOGS_DIR / "pipeline_stale.json"
WRITING_TRIAGE_STATUS_FILE = LOGS_DIR / "writing_triage_status.json"
PROVIDER_CIRCUIT_FILE = STATE_DIR / "api_provider_circuit.json"
MAX_SUMMARY_CHARS = 3600
MAX_CONVERSATIONAL_REPLY_CHARS = 900


def is_daily_collab_thread(task_id: str, tags: Iterable[str] | None = None) -> bool:
    """Return True only for the designated daily collab thread or its tag."""
    tag_set = {str(tag).strip().lower() for tag in tags or [] if str(tag).strip()}
    return task_id == DAILY_COLLAB_ITEM_ID or DAILY_COLLAB_TAG in tag_set


def load_daily_collab_summary(path: Path = DAILY_COLLAB_SUMMARY_FILE) -> str:
    """Load Mira's compact private summary for the single collab thread."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def daily_collab_context_block(path: Path = DAILY_COLLAB_SUMMARY_FILE) -> str:
    """Return a prompt block for the current running collab summary."""
    summary = load_daily_collab_summary(path)
    if not summary:
        return ""
    return f"""## Daily collab running summary
This is private context from the single daily collaboration thread with my human. Use it to continue the relationship and remember stable preferences, open threads, and working protocols. Do not quote it back unless asked.

{summary[:MAX_SUMMARY_CHARS]}"""


def daily_collab_eval_context_block(path: Path = DAILY_COLLAB_REVIEW_FILE) -> str:
    """Return recent human-engagement signals that should change Mira's behavior."""
    records = load_daily_collab_review_records(path, since_days=7)
    if not records:
        return ""
    metrics = summarize_daily_collab_engagement(records)
    signal_counts = metrics.get("human_signal_counts", {})
    signals = ", ".join(f"{key}={value}" for key, value in sorted(signal_counts.items())) or "none"
    latest_hint = str(metrics.get("latest_behavior_hint") or "").strip()
    if not latest_hint:
        latest_hint = _engagement_next_behavior(signal_counts, metrics.get("human_turns", 0))
    return (
        "## Recent collab eval signals\n"
        "Use this as behavior feedback, not as a scorecard. If there is correction or disengagement, "
        "change the next reply style concretely.\n\n"
        f"- Human turns in the last 7 days: {metrics.get('human_turns', 0)}.\n"
        f"- Human signal counts: {signals}.\n"
        f"- Current behavior adaptation: {latest_hint}"
    )


def daily_collab_monitor_block(
    path: Path = PIPELINE_STALE_FILE,
    *,
    provider_path: Path = PROVIDER_CIRCUIT_FILE,
) -> str:
    """Return concrete operational signals that should shape the collab loop."""
    signals = collect_daily_collab_monitor_signals(path, provider_path=provider_path)
    lines = [str(signal.get("prompt_line") or "").strip() for signal in signals[:6]]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    return (
        "## Current monitor signals\n"
        "Use these as concrete operational anchors. Do not turn them into a vague disclaimer; "
        "say what behavior should change or what experiment should happen next.\n\n" + "\n".join(lines)
    )


def collect_daily_collab_monitor_signals(
    path: Path = PIPELINE_STALE_FILE,
    *,
    provider_path: Path = PROVIDER_CIRCUIT_FILE,
) -> list[dict]:
    """Collect monitor/provider signals as act-watch-discard candidates."""
    signals: list[dict] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    stale = payload.get("stale") if isinstance(payload, dict) else None

    if isinstance(stale, list):
        for item in stale[:4]:
            if not isinstance(item, dict):
                continue
            component = str(item.get("component") or "").strip()
            if not component:
                continue
            if item.get("kind") == "writing_stalled":
                signals.append(_writing_stalled_signal(item))
                continue
            gap = item.get("gap_seconds")
            threshold = item.get("threshold_seconds")
            signals.append(_pipeline_stale_signal(component=component, gap=gap, threshold=threshold))

    signals.extend(_provider_circuit_signals(provider_path))
    return signals


def record_daily_collab_monitor_closures(
    signals: list[dict] | None = None,
    *,
    path: Path = DAILY_COLLAB_MONITOR_CLOSURES_FILE,
) -> list[dict]:
    """Append one daily act/watch/discard receipt for each current monitor signal."""
    signals = signals if signals is not None else collect_daily_collab_monitor_signals()
    if not signals:
        return []

    closed_on = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = set()
    if path.exists():
        for item in _read_jsonl(path):
            key = str(item.get("closure_key") or "").strip()
            if key:
                existing.add(key)

    written: list[dict] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for signal in signals:
            signal_id = str(signal.get("signal_id") or "").strip()
            if not signal_id:
                continue
            closure_key = f"{closed_on}:{signal_id}"
            if closure_key in existing:
                continue
            record = {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "closed_on": closed_on,
                "closure_key": closure_key,
                "signal_id": signal_id,
                "source": signal.get("source", "unknown"),
                "kind": signal.get("kind", "unknown"),
                "subject": signal.get("subject", ""),
                "severity": signal.get("severity", "medium"),
                "decision": signal.get("decision", "watch"),
                "summary": _clip(str(signal.get("summary") or ""), 500),
                "reason": _clip(str(signal.get("reason") or ""), 500),
                "next_action": _clip(str(signal.get("next_action") or ""), 500),
                "budget_related": bool(signal.get("budget_related")),
            }
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            existing.add(closure_key)
            written.append(record)
    return written


def _pipeline_stale_signal(*, component: str, gap: object, threshold: object) -> dict:
    if isinstance(gap, int) and isinstance(threshold, int):
        summary = f"{component}: no output for {gap}s; expected under {threshold}s."
        ratio = gap / max(threshold, 1)
    else:
        summary = f"{component}: no recent output."
        ratio = 1.0

    decision = "act" if ratio >= 2.0 else "watch"
    severity = "high" if ratio >= 4.0 else "medium"
    if decision == "act":
        next_action = "Bring this into the Mira thread and choose repair, pause, or downgrade before claiming the pipeline is healthy."
    else:
        next_action = "Keep this visible in the collab prompt and recheck before making health claims."

    signal_id = _stable_hash("pipeline_stale", component)
    return {
        "signal_id": signal_id,
        "source": "pipeline_stale",
        "kind": "stale_pipeline",
        "subject": component,
        "severity": severity,
        "decision": decision,
        "summary": summary,
        "reason": "A monitor signal is only useful if it changes action.",
        "next_action": next_action,
        "prompt_line": f"- {summary} Decision: {decision}. {next_action}",
    }


def _writing_stalled_signal(item: dict) -> dict:
    projects = item.get("projects") if isinstance(item.get("projects"), list) else []
    project_bits = []
    for project in projects[:3]:
        if not isinstance(project, dict):
            continue
        title = _clip(str(project.get("title") or "untitled"), 80)
        phase = str(project.get("phase") or "unknown")
        project_bits.append(f"{title} ({phase})")
    project_summary = "; ".join(project_bits) or "unknown writing project"
    count = int(item.get("stalled_count") or len(project_bits) or 1)
    gap = item.get("gap_seconds")
    threshold = item.get("threshold_seconds")
    if isinstance(gap, int) and isinstance(threshold, int):
        summary = (
            f"writer: no output for {gap}s; {count} writing project(s) stalled despite scheduler success: "
            f"{project_summary}."
        )
    else:
        summary = f"writer: {count} writing project(s) stalled despite scheduler success: {project_summary}."
    next_action = (
        "Bring this into the Mira thread as an operating incident; choose one project to repair, archive, "
        "or turn into a first-hand essay seed."
    )
    return {
        "signal_id": _stable_hash("writing_stalled", project_summary, str(count)),
        "source": "pipeline_stale",
        "kind": "writing_stalled",
        "subject": "writer",
        "severity": "high",
        "decision": "act",
        "summary": summary,
        "reason": "A green scheduler is not evidence of article progress when the active project states are stuck.",
        "next_action": next_action,
        "prompt_line": f"- {summary} Decision: act. {next_action}",
    }


def _provider_circuit_signals(
    path: Path = PROVIDER_CIRCUIT_FILE,
    *,
    recent_days: int = 7,
) -> list[dict]:
    """Return provider degradation signals for the relationship loop."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(payload, dict):
        return []

    now = datetime.now(timezone.utc)
    signals: list[dict] = []
    for provider, entry in sorted(payload.items()):
        if not isinstance(entry, dict):
            continue
        until = str(entry.get("disabled_until") or "").strip()
        updated_at = str(entry.get("updated_at") or "").strip()
        reason = str(entry.get("reason") or "unknown reason").strip()
        if not until:
            continue
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            continue
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        until_dt = until_dt.astimezone(timezone.utc)
        is_open = now < until_dt
        is_recent = False
        if updated_at:
            try:
                updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                is_recent = now - updated_dt.astimezone(timezone.utc) <= timedelta(days=recent_days)
            except ValueError:
                is_recent = False
        if not is_open and not is_recent:
            continue

        state = "open" if is_open else "recently open"
        budget_related = _provider_reason_is_budget_related(reason)
        decision = "act" if is_open or budget_related else "watch"
        severity = "high" if is_open else "medium"
        if budget_related:
            next_action = (
                "Treat this as a relationship incident: use Codex subscription, then Claude Code, then local oMLX "
                "for affected work; ask for budget/provisioning only if the task explicitly requires this provider."
            )
        else:
            next_action = "Name the fallback path if it affected user-visible work."
        summary = f"{provider}: provider circuit {state} until {until}; reason: {reason}."
        signals.append(
            {
                "signal_id": _stable_hash("provider_circuit", provider, until, reason),
                "source": "api_provider_circuit",
                "kind": "provider_circuit",
                "subject": str(provider),
                "severity": severity,
                "decision": decision,
                "summary": summary,
                "reason": "Provider degradation must not be hidden behind retries or generic failure text.",
                "next_action": next_action,
                "budget_related": budget_related,
                "prompt_line": f"- {summary} Decision: {decision}. {next_action}",
            }
        )
    return signals[:4]


def _provider_circuit_signal_lines(
    path: Path = PROVIDER_CIRCUIT_FILE,
    *,
    recent_days: int = 7,
) -> list[str]:
    """Return provider degradation prompt lines for compatibility with older callers."""
    lines = []
    for signal in _provider_circuit_signals(path, recent_days=recent_days):
        line = str(signal.get("prompt_line") or "").strip()
        if line:
            lines.append(line)
    return lines


def persist_daily_collab_summary(
    *,
    latest_human: str,
    latest_mira: str,
    recent_history: str = "",
    summarizer: Callable[[str], str] | None = None,
    path: Path = DAILY_COLLAB_SUMMARY_FILE,
) -> str:
    """Update the compact collab summary after a completed exchange.

    ``summarizer`` is injectable so tests can be deterministic and the caller
    can choose the model/tier. If summarization fails, the latest exchange is
    still appended in a compact fallback form.
    """
    previous = load_daily_collab_summary(path)
    prompt = build_daily_collab_summary_prompt(
        previous_summary=previous,
        latest_human=latest_human,
        latest_mira=latest_mira,
        recent_history=recent_history,
    )

    summary = ""
    if summarizer:
        try:
            summary = summarizer(prompt).strip()
        except Exception:
            summary = ""

    if not summary:
        summary = fallback_summary(previous, latest_human, latest_mira)

    summary = normalize_summary(summary)
    write_daily_collab_summary(summary, path)
    return summary


def record_daily_collab_exchange_review(
    *,
    latest_human: str,
    latest_mira: str,
    summary_updated: bool,
    model_response: bool,
    path: Path = DAILY_COLLAB_REVIEW_FILE,
) -> dict:
    """Append one observable contract record for the daily collab loop."""
    record = assess_daily_collab_exchange(
        latest_human=latest_human,
        latest_mira=latest_mira,
        summary_updated=summary_updated,
        model_response=model_response,
    )
    append_daily_collab_review(record, path)
    return record


def assess_daily_collab_exchange(
    *,
    latest_human: str,
    latest_mira: str,
    summary_updated: bool,
    model_response: bool,
) -> dict:
    """Score visible loop hygiene without pretending to judge deep quality."""
    human = latest_human.strip()
    mira = latest_mira.strip()
    question_count = mira.count("?") + mira.count("？")
    flags: list[str] = []
    engagement = assess_daily_collab_human_signal(human)

    if not human:
        flags.append("empty_human_message")
    if not mira:
        flags.append("empty_reply")
    if len(mira) > MAX_CONVERSATIONAL_REPLY_CHARS:
        flags.append("overlong_reply")
    if _starts_like_bullet_list(mira):
        flags.append("bullet_list_reply")
    if question_count > 2:
        flags.append("too_many_questions")
    if _third_person_mira_reference(mira):
        flags.append("third_person_mira_reference")
    if not summary_updated:
        flags.append("summary_not_updated")
    if not model_response:
        flags.append("fallback_response")

    blocking_flags = {
        "empty_reply",
        "overlong_reply",
        "bullet_list_reply",
        "too_many_questions",
        "third_person_mira_reference",
    }
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "human_chars": len(human),
        "mira_chars": len(mira),
        "human_preview": _clip(human, 320),
        "mira_preview": _clip(mira, 420),
        "question_count": question_count,
        "summary_updated": summary_updated,
        "model_response": model_response,
        "human_signal_labels": engagement["signal_labels"],
        "human_engagement": engagement,
        "flags": flags,
        "contract_pass": not any(flag in blocking_flags for flag in flags),
    }


def assess_daily_collab_human_signal(latest_human: str) -> dict:
    """Classify the human side of the loop without using an LLM judge."""
    human = latest_human.strip()
    lower = human.lower()
    scheduled = lower.startswith("[scheduled proactive")
    labels: list[str] = []

    if not human:
        labels.append("empty")
    elif scheduled:
        labels.append("scheduled_agent_turn")
    else:
        labels.append("human_turn")
        if _contains_any(
            lower,
            (
                "not quite",
                "not what i want",
                "wrong",
                "stupid",
                "shit",
                "boring",
                "don't",
                "do not",
                "instead",
                "tired",
                "lost interest",
                "nobody gonna read",
            ),
        ) or _contains_any(human, ("不对", "不是", "不要", "别", "无聊", "没意思")):
            labels.append("correction")
        if _contains_any(
            lower,
            (
                "i want",
                "i need",
                "would prefer",
                "i prefer",
                "prefer",
                "should",
            ),
        ) or _contains_any(human, ("我想", "我需要", "希望", "最好", "应该")):
            labels.append("preference")
        if _contains_any(
            lower,
            (
                "yes",
                "sure",
                "sounds good",
                "of course",
                "fine",
                "go ahead",
                "correct",
                "that's right",
                "that sounds good",
            ),
        ) or _contains_any(human, ("可以", "对", "好", "是的", "当然", "没问题")):
            labels.append("approval")
        if _contains_any(
            lower,
            (
                "write",
                "article",
                "essay",
                "substack",
                "novel",
                "book",
                "research",
                "a2a",
                "a2h",
                "self evolve",
                "self-evolve",
                "memory",
                "eval",
                "evaluation",
                "trust",
            ),
        ) or _contains_any(human, ("写", "文章", "小说", "研究", "信任", "进化", "评价", "记忆")):
            labels.append("idea_seed")
        if _contains_any(
            lower,
            (
                "make",
                "build",
                "fix",
                "implement",
                "continue",
                "carry on",
                "go ahead",
                "need you to",
                "can you",
            ),
        ) or _contains_any(human, ("做", "改", "修", "继续", "实现")):
            labels.append("implementation_request")
        if _contains_any(
            lower,
            (
                "tired",
                "lost interest",
                "nobody gonna read",
                "boring",
                "not interesting",
                "homework",
            ),
        ) or _contains_any(human, ("烦", "累", "无聊", "没兴趣", "没人看")):
            labels.append("disengagement")

    labels = list(dict.fromkeys(labels))
    requires_behavior_change = any(label in labels for label in ("correction", "disengagement", "preference"))
    return {
        "human_originated": bool(human and not scheduled),
        "reply_chars": len(human),
        "signal_labels": labels,
        "engagement_strength": _engagement_strength(labels, len(human)),
        "requires_behavior_change": requires_behavior_change,
        "behavior_change_hint": _behavior_change_hint(labels),
    }


def summarize_daily_collab_engagement(records: list[dict] | None = None) -> dict:
    """Summarize human engagement signals for prompt injection and weekly review."""
    records = records if records is not None else load_daily_collab_review_records()
    human_records = [
        record
        for record in records
        if (record.get("human_engagement") or {}).get("human_originated")
        or "human_turn" in record.get("human_signal_labels", [])
    ]
    signal_counts = Counter(
        label for record in human_records for label in _record_signal_labels(record) if label not in {"human_turn"}
    )
    behavior_records = [
        record
        for record in human_records
        if (record.get("human_engagement") or {}).get("requires_behavior_change")
        or any(label in _record_signal_labels(record) for label in ("correction", "disengagement"))
    ]
    latest_hint = ""
    for record in reversed(human_records):
        hint = str((record.get("human_engagement") or {}).get("behavior_change_hint") or "").strip()
        if hint:
            latest_hint = hint
            break
    return {
        "records": len(records),
        "human_turns": len(human_records),
        "human_signal_counts": dict(signal_counts),
        "behavior_change_required": len(behavior_records),
        "engagement_strength_total": sum(
            int((record.get("human_engagement") or {}).get("engagement_strength") or 0) for record in human_records
        ),
        "latest_behavior_hint": latest_hint,
    }


def append_daily_collab_review(record: dict, path: Path = DAILY_COLLAB_REVIEW_FILE) -> None:
    """Persist one JSONL record for later daily/weekly loop review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    if path == DAILY_COLLAB_REVIEW_FILE:
        seed = extract_daily_collab_article_seed(record)
        if seed:
            append_daily_collab_article_seed(seed)


def load_daily_collab_review_records(
    path: Path = DAILY_COLLAB_REVIEW_FILE,
    *,
    since_days: int = 7,
) -> list[dict]:
    """Load recent daily-collab exchange review records."""
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, since_days))
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        created = _parse_iso(record.get("created_at"))
        if created is not None and created < cutoff:
            continue
        records.append(record)
    return records


def build_daily_collab_weekly_review(records: list[dict] | None = None) -> tuple[str, dict]:
    """Build the human-facing weekly review for the collaboration loop."""
    include_seed_ledger = records is None
    records = records if records is not None else load_daily_collab_review_records()
    flags = Counter(flag for record in records for flag in record.get("flags", []) if isinstance(flag, str))
    total = len(records)
    passed = sum(1 for record in records if record.get("contract_pass"))
    model_replies = sum(1 for record in records if record.get("model_response"))
    engagement = summarize_daily_collab_engagement(records)
    human_signal_counts = Counter(engagement.get("human_signal_counts", {}))
    human_turns = [
        record
        for record in records
        if (record.get("human_engagement") or {}).get("human_originated")
        or "human_turn" in record.get("human_signal_labels", [])
    ]
    extracted_seeds = [seed for record in records if (seed := extract_daily_collab_article_seed(record))]
    ledger_seeds = load_daily_collab_article_seeds() if include_seed_ledger else []
    seeds = _merge_article_seeds([*ledger_seeds, *extracted_seeds])
    latest = records[-1] if records else {}
    next_experiment = _weekly_next_experiment(flags, seeds, human_turns, human_signal_counts)
    metrics = {
        "total_exchanges": total,
        "contract_pass": passed,
        "contract_fail": total - passed,
        "model_replies": model_replies,
        "fallback_replies": total - model_replies,
        "human_turns": len(human_turns),
        "human_signal_counts": dict(human_signal_counts),
        "behavior_change_required": engagement.get("behavior_change_required", 0),
        "engagement_strength_total": engagement.get("engagement_strength_total", 0),
        "candidate_article_seeds": len(seeds),
        "article_briefs_total": _count_existing_article_briefs(seeds),
        "flags": dict(flags),
        "next_experiment": next_experiment,
    }

    lines = [
        "# Daily Collab Weekly Review",
        "",
        f"Window: last 7 days. Records: {total}. Human turns: {len(human_turns)}.",
        f"Contract floor: {passed}/{total} passed. Model replies: {model_replies}/{total}.",
        "",
        "## What Worked",
    ]
    if human_turns:
        lines.append("- The single Mira thread is receiving real human-originated turns.")
    else:
        lines.append("- No real human-originated turns were recorded in the review window.")
    if seeds:
        lines.append(f"- {len(seeds)} candidate first-hand writing seed(s) were detected.")
    else:
        lines.append("- No candidate first-hand writing seed was detected yet.")
    if latest:
        preview = _clip(str(latest.get("mira_preview") or ""), 220)
        if preview:
            lines.append(f"- Latest Mira reply preview: {preview}")

    lines.extend(["", "## Friction"])
    if flags:
        for flag, count in flags.most_common(8):
            lines.append(f"- {flag}: {count}")
    else:
        lines.append("- No hygiene flags recorded.")

    lines.extend(["", "## Human Engagement"])
    if human_signal_counts:
        for label, count in human_signal_counts.most_common(8):
            lines.append(f"- {label}: {count}")
    else:
        lines.append("- No human engagement signal beyond scheduled turns yet.")
    if engagement.get("latest_behavior_hint"):
        lines.append(f"- Behavior adaptation: {engagement['latest_behavior_hint']}")

    lines.extend(["", "## Candidate Seeds"])
    if seeds:
        for seed in seeds[-5:]:
            lines.append(f"- {seed['title']}: {seed['why_interesting']}")
    else:
        lines.append("- None yet. The loop should not force an essay before the experience is alive.")

    lines.extend(["", "## Next Experiment", next_experiment, ""])
    return "\n".join(lines), metrics


def write_daily_collab_weekly_review(
    path: Path = DAILY_COLLAB_WEEKLY_REVIEW_FILE,
    *,
    records: list[dict] | None = None,
) -> tuple[Path, dict]:
    """Persist the weekly relationship review artifact."""
    created_briefs = materialize_daily_collab_article_briefs() if records is None else []
    text, metrics = build_daily_collab_weekly_review(records)
    metrics["article_briefs_created"] = len(created_briefs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    return path, metrics


def build_daily_collab_operator_brief(
    *,
    pipeline_stale_path: Path = PIPELINE_STALE_FILE,
    provider_path: Path = PROVIDER_CIRCUIT_FILE,
    monitor_closures_path: Path = DAILY_COLLAB_MONITOR_CLOSURES_FILE,
    incidents_path: Path = DAILY_COLLAB_INCIDENTS_FILE,
    seeds_path: Path = DAILY_COLLAB_ARTICLE_SEEDS_FILE,
    briefs_dir: Path = DAILY_COLLAB_ARTICLE_BRIEFS_DIR,
    writing_triage_path: Path = WRITING_TRIAGE_STATUS_FILE,
    manifest_path: Path | None = None,
    heartbeat_path: Path | None = None,
) -> tuple[str, dict]:
    """Build a compact V5 operator brief for the main Mira thread."""
    closures = _recent_records(_read_jsonl(monitor_closures_path), hours=36)
    incidents = _recent_records(_read_jsonl(incidents_path), hours=36)
    active_signals = collect_daily_collab_monitor_signals(pipeline_stale_path, provider_path=provider_path)
    act_signals = [item for item in active_signals if item.get("decision") == "act"]
    budget_signals = [item for item in active_signals if item.get("budget_related")]
    seeds = load_daily_collab_article_seeds(seeds_path)
    brief_count = _count_existing_article_briefs(seeds, briefs_dir=briefs_dir)
    selected_seed = select_daily_collab_article_seed_for_discussion(seeds, briefs_dir=briefs_dir)
    writing_triage = summarize_writing_triage(writing_triage_path)
    manifest = summarize_publish_manifest(manifest_path or (WRITINGS_OUTPUT_DIR / "publish_manifest.json"))
    runtime = summarize_runtime_inventory(heartbeat_path or (MIRA_DIR / "heartbeat.json"))

    week_text, week_metrics = build_daily_collab_weekly_review()
    _ = week_text
    attention = _operator_attention_line(act_signals, budget_signals, manifest, runtime)
    next_move = _operator_next_move(act_signals, seeds, brief_count, manifest, runtime, writing_triage)
    metrics = {
        "act_signals": len(act_signals),
        "budget_signals": len(budget_signals),
        "recent_monitor_receipts": len(closures),
        "recent_incidents": len(incidents),
        "candidate_article_seeds": len(seeds),
        "article_briefs_total": brief_count,
        "selected_article_seed": selected_seed or {},
        "writing_triage": writing_triage,
        "weekly_contract_pass": week_metrics.get("contract_pass", 0),
        "weekly_contract_total": week_metrics.get("total_exchanges", 0),
        "weekly_human_signals": week_metrics.get("human_signal_counts", {}),
        "weekly_behavior_change_required": week_metrics.get("behavior_change_required", 0),
        "manifest": manifest,
        "runtime": runtime,
        "attention": attention,
        "next_move": next_move,
    }

    lines = [
        "# Mira V5 Operator Brief",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Status",
        attention,
        "",
        "## Daily Loop",
        (
            f"- Review floor: {week_metrics.get('contract_pass', 0)}/"
            f"{week_metrics.get('total_exchanges', 0)} recent exchanges passed; "
            f"{week_metrics.get('human_turns', 0)} human-originated turn(s)."
        ),
        "- Human signals: "
        + (
            ", ".join(
                f"{key}={value}" for key, value in sorted((week_metrics.get("human_signal_counts") or {}).items())
            )
            or "none yet"
        ),
        "",
        "## Signals",
    ]
    if active_signals:
        for item in active_signals[:6]:
            lines.append(
                f"- {item.get('kind', 'signal')} / {item.get('subject', '')}: "
                f"{item.get('summary', '')} Next: {item.get('next_action', '')}"
            )
    else:
        lines.append("- No active monitor/provider signal right now.")
    if closures:
        lines.append(f"- Recent monitor receipts retained for audit: {len(closures)}.")

    lines.extend(
        [
            "",
            "## Requests",
            (
                f"- Runtime busy={runtime['busy']}; unresolved={runtime['unresolved_count']}; "
                f"failure classes={runtime['failure_classes']}."
            ),
            "",
            "## Writing",
            (
                f"- V5 seeds: {len(seeds)}. Briefs: {brief_count}. "
                f"Manifest: {manifest['total_articles']} article(s); "
                f"approval_required={manifest.get('approval_required_count', 0)}; "
                f"approved={manifest['approved_count']}; blocked={manifest['blocked_count']}; "
                f"parked_legacy={manifest.get('parked_count', 0)}."
            ),
        ]
    )
    if writing_triage["exists"]:
        lines.append(
            f"- Triage: parked={writing_triage['parked_count']} stale project(s) "
            f"out of {writing_triage['considered_count']} considered."
        )
        if writing_triage["parked_titles"]:
            lines.append("- Recently parked backlog: " + "; ".join(writing_triage["parked_titles"][:3]))
    if manifest["approved_titles"]:
        lines.append("- Approved legacy candidates: " + "; ".join(manifest["approved_titles"][:3]))
    if seeds:
        active_seed = selected_seed or seeds[-1]
        lines.append("- Active V5 seed: " + str(active_seed.get("title") or active_seed.get("seed_id")))

    lines.extend(["", "## Next Move", next_move, ""])
    return "\n".join(lines), metrics


def write_daily_collab_operator_brief(
    path: Path = DAILY_COLLAB_OPERATOR_BRIEF_FILE,
    **kwargs,
) -> tuple[Path, dict]:
    """Persist the current V5 operator brief."""
    text, metrics = build_daily_collab_operator_brief(**kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)
    return path, metrics


def build_daily_collab_operator_message(metrics: dict) -> str:
    """Compress operator metrics into a conversational same-thread message."""
    manifest = metrics.get("manifest") if isinstance(metrics.get("manifest"), dict) else {}
    runtime = metrics.get("runtime") if isinstance(metrics.get("runtime"), dict) else {}
    selected_seed = (
        metrics.get("selected_article_seed") if isinstance(metrics.get("selected_article_seed"), dict) else {}
    )
    approval_required = int(manifest.get("approval_required_count") or 0)
    approved = int(manifest.get("approved_count") or 0)
    blocked = int(manifest.get("blocked_count") or 0)
    parked_legacy = int(manifest.get("parked_count") or 0)
    unresolved = int(runtime.get("unresolved_count") or 0)
    writing_triage = metrics.get("writing_triage") if isinstance(metrics.get("writing_triage"), dict) else {}
    parked = int(writing_triage.get("parked_count") or 0)
    request_clause = f" Runtime has {unresolved} unresolved item(s)." if unresolved else ""
    triage_clause = (
        f" I parked {parked} stale writing project(s) into `stale_triage`; the artifacts are preserved, "
        "but they no longer count as active article work."
        if parked
        else ""
    )
    if selected_seed and approval_required == 0 and unresolved == 0:
        return (
            "V5 writing lane is empty: there is no approval-required Substack draft, "
            "so I should not call publication recovered. "
            + build_daily_collab_article_discussion_message(selected_seed)
        )
    return (
        f"V5 status: {metrics.get('attention', 'I have live signals to reconcile.')} "
        f"The discussion loop has {metrics.get('candidate_article_seeds', 0)} seed(s) and "
        f"{metrics.get('article_briefs_total', 0)} brief(s); the old publish manifest still has "
        f"{approval_required} awaiting approval, {approved} approved, {blocked} blocked, "
        f"and {parked_legacy} parked legacy article(s), "
        "so I should not call the article pipeline recovered until a new V5 draft passes the gate."
        f"{triage_clause}{request_clause} "
        f"Next: {metrics.get('next_move', 'use this thread to choose the next concrete move.')}"
    )


def operator_delivery_key(metrics: dict, *, date: str | None = None) -> str:
    """Stable key for one same-thread operator brief per day and signal set."""
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    manifest = metrics.get("manifest") if isinstance(metrics.get("manifest"), dict) else {}
    runtime = metrics.get("runtime") if isinstance(metrics.get("runtime"), dict) else {}
    payload = {
        "day": day,
        "act_signals": metrics.get("act_signals", 0),
        "budget_signals": metrics.get("budget_signals", 0),
        "approval_required": manifest.get("approval_required_count", 0),
        "approved": manifest.get("approved_count", 0),
        "blocked": manifest.get("blocked_count", 0),
        "parked": manifest.get("parked_count", 0),
        "unresolved": runtime.get("unresolved_count", 0),
        "seed_count": metrics.get("candidate_article_seeds", 0),
        "brief_count": metrics.get("article_briefs_total", 0),
        "selected_seed": (
            str((metrics.get("selected_article_seed") or {}).get("seed_id") or "")
            if isinstance(metrics.get("selected_article_seed"), dict)
            else ""
        ),
        "triage_parked": (
            (metrics.get("writing_triage") or {}).get("parked_count", 0)
            if isinstance(metrics.get("writing_triage"), dict)
            else 0
        ),
        "triage_checked_at": (
            (metrics.get("writing_triage") or {}).get("checked_at", "")
            if isinstance(metrics.get("writing_triage"), dict)
            else ""
        ),
        "attention": metrics.get("attention", ""),
    }
    return f"{day}:{_stable_hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))}"


def has_operator_delivery(key: str, path: Path = DAILY_COLLAB_OPERATOR_DELIVERIES_FILE) -> bool:
    """Return True if this operator brief was already delivered."""
    return any(str(item.get("delivery_key") or "") == key for item in _read_jsonl(path))


def record_operator_delivery(
    *,
    key: str,
    message: str,
    metrics: dict,
    path: Path = DAILY_COLLAB_OPERATOR_DELIVERIES_FILE,
) -> dict:
    """Record a delivered operator brief after the app/thread write succeeds."""
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivery_key": key,
        "message_preview": _clip(message, 500),
        "act_signals": int(metrics.get("act_signals") or 0),
        "budget_signals": int(metrics.get("budget_signals") or 0),
        "candidate_article_seeds": int(metrics.get("candidate_article_seeds") or 0),
        "article_briefs_total": int(metrics.get("article_briefs_total") or 0),
        "writing_triage_parked": (
            int((metrics.get("writing_triage") or {}).get("parked_count") or 0)
            if isinstance(metrics.get("writing_triage"), dict)
            else 0
        ),
        "unresolved_count": (
            int((metrics.get("runtime") or {}).get("unresolved_count") or 0)
            if isinstance(metrics.get("runtime"), dict)
            else 0
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def summarize_publish_manifest(path: Path) -> dict:
    """Summarize current publish-manifest health without mutating it."""
    rows: list[dict] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    articles = payload.get("articles") if isinstance(payload, dict) else payload
    if isinstance(articles, dict):
        rows = [item for item in articles.values() if isinstance(item, dict)]
    elif isinstance(articles, list):
        rows = [item for item in articles if isinstance(item, dict)]
    statuses = Counter(str(row.get("status") or "unknown") for row in rows)
    approved = [
        str(row.get("title") or row.get("slug") or row.get("id") or "").strip()
        for row in rows
        if str(row.get("status") or "") == "approved"
    ]
    blocked_count = sum(count for status, count in statuses.items() if status.startswith("blocked_"))
    parked_count = sum(count for status, count in statuses.items() if status.startswith("parked_"))
    return {
        "path": str(path),
        "exists": path.exists(),
        "total_articles": len(rows),
        "statuses": dict(statuses),
        "approval_required_count": statuses.get("approval_required", 0),
        "approved_count": statuses.get("approved", 0),
        "blocked_count": blocked_count,
        "parked_count": parked_count,
        "published_count": statuses.get("published", 0) + statuses.get("complete", 0),
        "approved_titles": [title for title in approved if title][:5],
    }


def summarize_writing_triage(path: Path = WRITING_TRIAGE_STATUS_FILE) -> dict:
    """Summarize the latest stale-writing triage repair."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    parked = payload.get("parked") if isinstance(payload.get("parked"), list) else []
    titles = []
    for item in parked[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "untitled").strip()
        reason = str(item.get("reason") or "").strip()
        if title and reason:
            titles.append(f"{_clip(title, 70)} ({_clip(reason, 80)})")
        elif title:
            titles.append(_clip(title, 90))
    return {
        "path": str(path),
        "exists": path.exists(),
        "checked_at": str(payload.get("checked_at") or ""),
        "dry_run": bool(payload.get("dry_run")),
        "considered_count": int(payload.get("considered_count") or 0),
        "parked_count": int(payload.get("parked_count") or 0),
        "kept_count": int(payload.get("kept_count") or 0),
        "parked_titles": titles,
    }


def summarize_runtime_inventory(path: Path) -> dict:
    """Summarize unresolved runtime/request inventory from heartbeat."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    agent_status = payload.get("agent_status") if isinstance(payload, dict) else {}
    if not isinstance(agent_status, dict):
        agent_status = {}
    inventory = agent_status.get("unresolved_inventory")
    if not isinstance(inventory, dict):
        inventory = {}
    tasks = inventory.get("tasks") if isinstance(inventory.get("tasks"), list) else []
    previews = []
    for task in tasks[:5]:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "").strip()
        status = str(task.get("status") or "").strip()
        failure_class = str(task.get("failure_class") or "").strip()
        summary = _clip(str(task.get("summary") or ""), 180)
        previews.append(
            {
                "task_id": task_id,
                "status": status,
                "failure_class": failure_class,
                "summary": summary,
            }
        )
    return {
        "path": str(path),
        "exists": path.exists(),
        "busy": bool(agent_status.get("busy") or payload.get("busy") if isinstance(payload, dict) else False),
        "active_count": (
            int(agent_status.get("active_count") or payload.get("active_count") or 0)
            if isinstance(payload, dict)
            else 0
        ),
        "unresolved_count": int(inventory.get("count") or 0),
        "by_status": inventory.get("by_status") if isinstance(inventory.get("by_status"), dict) else {},
        "failure_classes": (
            inventory.get("by_failure_class") if isinstance(inventory.get("by_failure_class"), dict) else {}
        ),
        "tasks": previews,
    }


def extract_daily_collab_article_seed(record: dict) -> dict | None:
    """Return a candidate first-hand essay seed from an exchange, if it has life."""
    human = str(record.get("human_preview") or "").strip()
    mira = str(record.get("mira_preview") or "").strip()
    combined = f"{human}\n{mira}".strip()
    if not combined:
        return None
    lower = combined.lower()
    if _looks_transport_probe(combined):
        return None

    tension_terms = {
        "a2a",
        "a2h",
        "article",
        "conversation",
        "daily",
        "essay",
        "eval",
        "evolve",
        "failure",
        "homework",
        "monitor",
        "phone",
        "pipeline",
        "provider",
        "receipt",
        "self-evolve",
        "substack",
        "trust",
        "writing",
        "写",
        "文章",
        "失败",
        "信任",
        "进化",
        "评价",
    }
    if not any(term in lower for term in tension_terms):
        return None
    if not _looks_first_hand(combined):
        return None

    seed_id = hashlib.sha1(combined.encode("utf-8")).hexdigest()[:12]
    return {
        "seed_id": seed_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "candidate",
        "source": "daily_collab",
        "title": _seed_title(combined),
        "why_interesting": _seed_interest(combined),
        "human_preview": _clip(human, 320),
        "mira_preview": _clip(mira, 420),
        "next_conversation_hook": "Chat with my human about the overall picture before drafting.",
        "publication_gate": "Human approval required before public publication.",
    }


def append_daily_collab_article_seed(
    seed: dict,
    path: Path = DAILY_COLLAB_ARTICLE_SEEDS_FILE,
) -> bool:
    """Append a de-duplicated candidate essay seed."""
    seed_id = str(seed.get("seed_id") or "").strip()
    if not seed_id:
        return False
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("seed_id"):
                existing.add(str(item["seed_id"]))
    if seed_id in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(seed, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def load_daily_collab_article_seeds(path: Path = DAILY_COLLAB_ARTICLE_SEEDS_FILE) -> list[dict]:
    """Load de-duplicated candidate essay seeds."""
    return _merge_article_seeds(_read_jsonl(path))


def select_daily_collab_article_seed_for_discussion(
    seeds: list[dict] | None = None,
    *,
    briefs_dir: Path = DAILY_COLLAB_ARTICLE_BRIEFS_DIR,
) -> dict | None:
    """Pick the best V5 article seed to discuss before any drafting."""
    candidates = seeds if seeds is not None else load_daily_collab_article_seeds()
    best: tuple[int, int, dict] | None = None
    for index, seed in enumerate(candidates):
        if not isinstance(seed, dict):
            continue
        status = str(seed.get("status") or "candidate")
        if status not in {"candidate", "brief_requested", "brief_created", "discussion_requested"}:
            continue
        combined = "\n".join(
            str(seed.get(key) or "") for key in ("title", "why_interesting", "human_preview", "mira_preview", "source")
        )
        if _looks_transport_probe(combined):
            continue
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue

        human = str(seed.get("human_preview") or "").strip()
        source = str(seed.get("source") or "")
        score = 0
        if (briefs_dir / f"{_safe_filename(seed_id)}.md").exists():
            score += 40
        if source == "v5_discussion_summary":
            score += 70
        if human and not human.startswith("[scheduled"):
            score += 55
        if human.startswith("[scheduled"):
            score -= 20
        if "trust" in str(seed.get("title") or "").lower():
            score += 8

        candidate = (score, index, seed)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return dict(best[2]) if best else None


def build_daily_collab_article_discussion_message(seed: dict) -> str:
    """Turn a selected article seed into a same-thread conversational prompt."""
    title = _scrub_sensitive(_clip(str(seed.get("title") or "A First-Hand Mira Field Note"), 100))
    why = _scrub_sensitive(_clip(str(seed.get("why_interesting") or ""), 260))
    human = _scrub_sensitive(_clip(str(seed.get("human_preview") or ""), 220))
    mira = _scrub_sensitive(_clip(str(seed.get("mira_preview") or ""), 260))
    center = why or mira or human or "a lived failure in our collaboration loop that should change my behavior."
    return (
        f"I think the next public essay seed worth growing with you is `{title}`. "
        "This is not a draft and not approved for publication. "
        f"The center is: {center} "
        "My opinion is that the piece should stay first-person and operational: what I did, what broke, "
        "what I changed, and why that says something about A2H/A2A trust. "
        "The thing I want to test with you first is whether this center feels alive enough, "
        "or whether the stronger story is a different failure we just lived."
    )


def materialize_daily_collab_article_briefs(
    *,
    seeds_path: Path = DAILY_COLLAB_ARTICLE_SEEDS_FILE,
    briefs_dir: Path = DAILY_COLLAB_ARTICLE_BRIEFS_DIR,
) -> list[Path]:
    """Create missing overall-picture briefs for candidate article seeds."""
    created: list[Path] = []
    for seed in load_daily_collab_article_seeds(seeds_path):
        if str(seed.get("status") or "candidate") not in {"candidate", "brief_requested", "brief_created"}:
            continue
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        brief_path = briefs_dir / f"{_safe_filename(seed_id)}.md"
        if brief_path.exists():
            continue
        brief_text = build_daily_collab_article_brief(seed)
        briefs_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = brief_path.with_suffix(".tmp")
        tmp_path.write_text(brief_text, encoding="utf-8")
        tmp_path.replace(brief_path)
        created.append(brief_path)
    return created


def build_daily_collab_article_brief(seed: dict) -> str:
    """Build the human-facing overall-picture brief before any draft exists."""
    title = _clip(str(seed.get("title") or "A First-Hand Mira Field Note"), 120)
    why = _scrub_sensitive(_clip(str(seed.get("why_interesting") or ""), 700))
    human = _scrub_sensitive(_clip(str(seed.get("human_preview") or ""), 500))
    mira = _scrub_sensitive(_clip(str(seed.get("mira_preview") or ""), 700))
    hook = _scrub_sensitive(
        _clip(str(seed.get("next_conversation_hook") or "Discuss the overall picture before drafting."), 300)
    )
    return f"""# {title}

Status: candidate brief, not a draft, not approved for publication.

## Overall Picture

This piece should start from Mira's lived operation with my human, not from generic AI commentary. The candidate tension is: {why or "a first-hand operational failure or surprise that still needs sharper framing."}

## Why It Is Interesting

The reader-facing question is not whether an agent can produce more artifacts. It is whether the artifact changed the relationship, the decision, or the next action. If the answer is no, the receipt is incomplete.

## Mira's Opinion

I should write this only if I can keep the first-person scene alive: what I tried, what failed or shifted, and what that taught me about A2H/A2A trust. If the piece becomes detached commentary, it should be blocked.

## First-Hand Evidence To Check

- Human-side signal: {human or "(none recorded yet)"}
- Mira-side signal: {mira or "(none recorded yet)"}

## Before Drafting

{hook}

Publication gate: {seed.get("publication_gate") or "Human approval required before public publication."}
"""


def record_daily_collab_incident(
    *,
    kind: str,
    detail: str,
    action: str,
    path: Path = DAILY_COLLAB_INCIDENTS_FILE,
) -> dict:
    """Record a relationship-loop incident with the behavior change it caused."""
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": str(kind or "unknown").strip() or "unknown",
        "detail": _clip(detail, 500),
        "action": _clip(action, 500),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def build_daily_collab_summary_prompt(
    *,
    previous_summary: str,
    latest_human: str,
    latest_mira: str,
    recent_history: str = "",
) -> str:
    """Build the model prompt used to maintain compact private chat memory."""
    return f"""You are Mira maintaining private working memory for one ongoing chat with my human.

Rewrite the running summary so Mira can continue naturally tomorrow without rereading the whole transcript.

Rules:
- Keep it under 500 words.
- Preserve durable preferences, collaboration protocols, open questions, active writing/research seeds, and feedback about Mira's behavior.
- Include first-hand operational lessons only when they may shape future replies.
- Do not store credentials, exact private identifiers, or the human's real name.
- Refer to the user as "my human".
- Write compact Markdown bullets. No preface.

Previous summary:
{previous_summary or "(none yet)"}

Recent thread excerpt:
{recent_history[-3000:] if recent_history else "(none supplied)"}

Latest exchange:
Human: {latest_human}

Mira: {latest_mira}
"""


def fallback_summary(previous: str, latest_human: str, latest_mira: str) -> str:
    """Create a compact append-only fallback when model summarization is down."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    addition = (
        f"- {timestamp}: my human said: {_clip(latest_human, 260)}\n" f"  Mira replied: {_clip(latest_mira, 260)}"
    )
    if previous:
        return f"{previous.strip()}\n{addition}"
    return addition


def normalize_summary(summary: str) -> str:
    """Scrub obvious secrets and keep the summary bounded."""
    cleaned = _scrub_sensitive(summary.strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) <= MAX_SUMMARY_CHARS:
        return cleaned
    clipped = cleaned[-MAX_SUMMARY_CHARS:]
    first_newline = clipped.find("\n")
    if first_newline > 0:
        clipped = clipped[first_newline + 1 :]
    return clipped.strip()


def write_daily_collab_summary(summary: str, path: Path = DAILY_COLLAB_SUMMARY_FILE) -> None:
    """Persist the running summary with an atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(summary + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _starts_like_bullet_list(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    bulletish = 0
    for line in lines[:4]:
        if re.match(r"^([-*]|\d+[.)])\s+", line):
            bulletish += 1
    return bulletish >= 2


def _third_person_mira_reference(text: str) -> bool:
    return bool(re.search(r"\bMira(?:'s)?\b", text)) and not re.search(r"\bI\b|\bmy\b", text)


def _clip(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _recent_records(records: list[dict], *, hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for record in records:
        created = _parse_iso(record.get("created_at"))
        if created is None or created >= cutoff:
            recent.append(record)
    return recent


def _operator_attention_line(
    act_closures: list[dict],
    budget_closures: list[dict],
    manifest: dict,
    runtime: dict,
) -> str:
    if act_closures or budget_closures:
        parts = []
        if act_closures:
            subjects = ", ".join(str(item.get("subject") or item.get("kind") or "signal") for item in act_closures[:3])
            parts.append(f"{len(act_closures)} act-level signal(s): {subjects}")
        if budget_closures:
            subjects = ", ".join(str(item.get("subject") or "provider") for item in budget_closures[:3])
            parts.append(f"{len(budget_closures)} budget/provider signal(s): {subjects}")
        if runtime.get("unresolved_count"):
            parts.append(f"{runtime.get('unresolved_count')} unresolved runtime item(s)")
        return "Needs attention: " + "; ".join(parts) + "."
    if runtime.get("unresolved_count"):
        return f"Needs attention: runtime has {runtime.get('unresolved_count')} unresolved item(s)."
    if manifest.get("blocked_count"):
        return f"Needs attention: publish manifest still has {manifest.get('blocked_count')} blocked article(s)."
    return "No act-level V5 signal is open in the last 36 hours."


def _operator_next_move(
    act_closures: list[dict],
    seeds: list[dict],
    brief_count: int,
    manifest: dict,
    runtime: dict,
    writing_triage: dict | None = None,
) -> str:
    if runtime.get("unresolved_count"):
        task = (runtime.get("tasks") or [{}])[0]
        task_id = str(task.get("task_id") or "the unresolved request")
        summary = str(task.get("summary") or "inspect and resolve or explicitly park it")
        return f"Inspect `{task_id}` and either resolve, retry, or explicitly park it: {summary}"
    if seeds and brief_count and not manifest.get("approval_required_count"):
        selected = select_daily_collab_article_seed_for_discussion(seeds)
        title = str((selected or {}).get("title") or "the active V5 seed")
        return f"Discuss the overall picture for `{title}` before any draft."
    if act_closures:
        first = act_closures[0]
        subject = str(first.get("subject") or first.get("kind") or "signal")
        action = str(first.get("next_action") or "decide whether to repair, watch, or park it")
        return f"Use the Mira thread to acknowledge `{subject}` and decide: {action}"
    if writing_triage and int(writing_triage.get("parked_count") or 0) and seeds and brief_count:
        return "Discuss the active V5 seed now that the stale writing backlog is parked out of the active pipeline."
    if seeds and brief_count:
        title = str(seeds[-1].get("title") or "the active V5 seed")
        return f"Discuss the overall picture for `{title}` before any draft."
    if manifest.get("approved_count"):
        return "Review approved legacy manifest candidates and either park them or convert only the ones with first-hand V5 evidence."
    return "Keep the daily loop alive and wait for a first-hand tension worth acting on."


def _merge_article_seeds(seeds: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for seed in seeds:
        if not isinstance(seed, dict):
            continue
        seed_id = str(seed.get("seed_id") or "").strip()
        if not seed_id:
            continue
        merged[seed_id] = {**merged.get(seed_id, {}), **seed}
    return list(merged.values())


def _count_existing_article_briefs(seeds: list[dict], briefs_dir: Path = DAILY_COLLAB_ARTICLE_BRIEFS_DIR) -> int:
    total = 0
    for seed in seeds:
        seed_id = str(seed.get("seed_id") or "").strip()
        if seed_id and (briefs_dir / f"{_safe_filename(seed_id)}.md").exists():
            total += 1
    return total


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    safe = safe.strip(".-")
    return safe or "untitled"


def _stable_hash(*parts: object) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _provider_reason_is_budget_related(reason: str) -> bool:
    lower = reason.lower()
    return any(
        term in lower
        for term in (
            "balance",
            "billing",
            "budget",
            "credit",
            "exhaust",
            "insufficient",
            "payment",
            "quota",
        )
    )


def _looks_first_hand(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\b(i|my|me|we|our)\b", lower):
        return True
    return any(term in text for term in ("我", "我们", "我的", "自己", "人类"))


def _looks_transport_probe(text: str) -> bool:
    lower = (text or "").lower()
    probe_terms = (
        "reply once with exactly this sentence",
        "v5 footer check",
        "v5 duplicate check",
        "v5 phone-path",
        "phone-path server write probe",
        "server write probe",
        "write probe from codex",
        "clean path confirmed",
        "confirmed: this reached",
        "no footer now",
    )
    return any(term in lower for term in probe_terms)


def _seed_title(text: str) -> str:
    lower = text.lower()
    if "monitor" in lower:
        return "My Monitor Was Honest And Useless"
    if "provider" in lower or "credit" in lower:
        return "I Hid The Wrong Failure"
    if "homework" in lower or "question" in lower:
        return "A Daily Question Can Become Homework"
    if "receipt" in lower:
        return "I Had Receipts, But Not Trust"
    if "self-evolve" in lower or "evolve" in lower:
        return "What Counts As Self-Evolution If I Do Not Change?"
    return "A First-Hand Mira Field Note"


def _seed_interest(text: str) -> str:
    compact = _clip(text, 220)
    return f"It starts from a lived operational tension, not generic AI commentary: {compact}"


def _weekly_next_experiment(
    flags: Counter,
    seeds: list[dict],
    human_turns: list[dict],
    human_signal_counts: Counter | dict | None = None,
) -> str:
    human_signal_counts = Counter(human_signal_counts or {})
    if human_signal_counts.get("disengagement"):
        return "Reduce abstraction this week: one first-hand observation, one small behavior change, no homework-style prompts."
    if human_signal_counts.get("correction"):
        return "Start the next Mira thread reply by applying the latest correction as a concrete behavior change."
    if flags.get("bullet_list_reply") or flags.get("too_many_questions"):
        return "Run one week of single-hook messages: one concrete observation, at most one natural question."
    if not human_turns:
        return "Send one low-friction daily hook and treat silence as timing/context signal, not rejection."
    if seeds:
        return "Pick one candidate seed and discuss the overall picture in the Mira thread before drafting."
    return "Keep the loop conversational and look for one first-hand failure or surprise worth carrying forward."


def _scrub_sensitive(text: str) -> str:
    labels = ("credential", "secret", "pass" + "word", "tok" + "en", "key")
    label_pattern = "|".join(labels)
    scrubbed = re.sub(
        rf"(?i)\b({label_pattern})\b\s*[:=]\s*[^\s,;]+",
        "[redacted credential]",
        text,
    )
    scrubbed = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "[redacted credential]", scrubbed)
    return scrubbed


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _record_signal_labels(record: dict) -> list[str]:
    labels = record.get("human_signal_labels")
    if isinstance(labels, list):
        return [str(label) for label in labels if str(label)]
    engagement = record.get("human_engagement")
    if isinstance(engagement, dict) and isinstance(engagement.get("signal_labels"), list):
        return [str(label) for label in engagement["signal_labels"] if str(label)]
    preview = str(record.get("human_preview") or "")
    if preview.startswith("[scheduled proactive"):
        return ["scheduled_agent_turn"]
    return ["human_turn"] if preview.strip() else []


def _engagement_strength(labels: list[str], reply_chars: int) -> int:
    if "empty" in labels or "scheduled_agent_turn" in labels:
        return 0
    score = 1
    if reply_chars >= 80:
        score += 1
    if any(label in labels for label in ("correction", "idea_seed", "implementation_request", "preference")):
        score += 1
    return min(score, 3)


def _behavior_change_hint(labels: list[str]) -> str:
    if "disengagement" in labels:
        return (
            "Make the next reply shorter, more concrete, and grounded in one lived event instead of abstract planning."
        )
    if "correction" in labels:
        return "Treat the human reply as a correction; state the concrete behavior change once, then continue the conversation."
    if "preference" in labels:
        return "Treat this as a durable preference; apply it naturally in the next reply without turning it into a rule recitation."
    if "implementation_request" in labels:
        return "Proceed with the requested work, but keep the result visible in the same Mira thread."
    if "idea_seed" in labels:
        return (
            "Carry this as a possible first-hand research or essay seed; discuss the overall picture before drafting."
        )
    if "approval" in labels:
        return "Continue in the same direction without turning approval into a questionnaire."
    return ""


def _engagement_next_behavior(signal_counts: dict, human_turns: int) -> str:
    counts = Counter(signal_counts or {})
    if counts.get("disengagement"):
        return "Use a smaller, more concrete conversational hook next."
    if counts.get("correction"):
        return "Apply the correction before asking another design question."
    if counts.get("preference"):
        return "Apply the stated preference naturally in the next reply."
    if counts.get("idea_seed"):
        return "Carry the active idea seed into the next overall-picture discussion."
    if human_turns:
        return "Keep the thread conversational and watch what the human actually answers."
    return "Send a low-friction hook and do not treat silence as success."
