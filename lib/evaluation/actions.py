"""Turn score diagnostics into durable self-improvement actions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ops.backlog import ActionBacklog, ActionItem


_LOW_LIMIT = 5
_DECLINE_LIMIT = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_at(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _priority_for_low(score: float) -> str:
    if score < 2.0:
        return "high"
    if score < 4.0:
        return "medium"
    return "low"


def _priority_for_decline(delta: float) -> str:
    if delta <= -2.0:
        return "high"
    return "medium"


def _verification_criteria(dimension: str, *, baseline: float, kind: str) -> list[str]:
    criteria = [
        f"{dimension} score improves from {baseline:.2f} by at least 1.0 point or reaches 4.0.",
        "The next weekly review includes evidence explaining what changed.",
        "The action is not marked verified unless the underlying task/report data supports the score change.",
    ]
    if "reading_volume" in dimension:
        criteria.append("Reading-note ingestion is checked before treating the score as a behavior problem.")
    if "hallucination" in dimension:
        criteria.append("At least three completed tasks include explicit outcome verification before status is done.")
    if kind == "decline":
        criteria.append("The downward trend stops for at least two consecutive scoring events.")
    return criteria


def build_score_action_items(diagnosis: dict, plan_text: str = "") -> list[ActionItem]:
    """Build stable action items from a score diagnosis."""
    now = _utc_now()
    next_review = _review_at()
    items: list[ActionItem] = []

    for entry in diagnosis.get("low_scores", [])[:_LOW_LIMIT]:
        dimension = str(entry.get("dim") or entry.get("dimension") or "").strip()
        if not dimension:
            continue
        score = _float(entry.get("score"))
        category = str(entry.get("category") or "")
        payload = {
            "kind": "score_low",
            "dimension": dimension,
            "category": category,
            "baseline_score": score,
            "observed_at": now,
            "next_review_at": next_review,
            "verification_criteria": _verification_criteria(dimension, baseline=score, kind="low"),
            "plan_excerpt": plan_text[:2000],
        }
        items.append(
            ActionItem(
                title=f"Score improvement: {dimension}",
                description=(
                    f"{dimension} is {score:.2f}, below the healthy threshold. "
                    "Diagnose whether this is measurement drift or a real capability gap, then improve it."
                ),
                source="reflect",
                priority=_priority_for_low(score),
                target_dimension=dimension,
                payload=payload,
            )
        )

    for entry in diagnosis.get("declining", [])[:_DECLINE_LIMIT]:
        dimension = str(entry.get("dim") or entry.get("dimension") or "").strip()
        if not dimension:
            continue
        scores = entry.get("scores")
        delta = _float(entry.get("delta"))
        baseline = _float(scores[-1] if isinstance(scores, list) and scores else entry.get("score"))
        payload = {
            "kind": "score_decline",
            "dimension": dimension,
            "scores": scores if isinstance(scores, list) else [],
            "delta": delta,
            "baseline_score": baseline,
            "observed_at": now,
            "next_review_at": next_review,
            "verification_criteria": _verification_criteria(dimension, baseline=baseline, kind="decline"),
            "plan_excerpt": plan_text[:2000],
        }
        items.append(
            ActionItem(
                title=f"Score decline: {dimension}",
                description=(
                    f"{dimension} is declining (delta={delta:.2f}, scores={scores}). "
                    "Find the cause and define a measurable correction."
                ),
                source="reflect",
                priority=_priority_for_decline(delta),
                target_dimension=dimension,
                payload=payload,
            )
        )

    return items


def upsert_score_action_items(
    diagnosis: dict,
    plan_text: str = "",
    *,
    backlog: ActionBacklog | None = None,
) -> list[ActionItem]:
    """Continuously refresh backlog actions from the latest score diagnosis."""
    if backlog is None:
        backlog = ActionBacklog()
    saved: list[ActionItem] = []
    for item in build_score_action_items(diagnosis, plan_text=plan_text):
        if hasattr(backlog, "upsert_active"):
            _, saved_item = backlog.upsert_active(item)
            saved.append(saved_item)
        elif backlog.add(item):
            saved.append(item)
        else:
            backlog.update_item(
                item.title,
                description=item.description,
                source=item.source,
                priority=item.priority,
                target_dimension=item.target_dimension,
                expires_at=item.expires_at,
                executor=item.executor,
                payload=item.payload,
                last_error="",
            )
            saved.append(item)
    backlog.expire_stale()
    return saved
