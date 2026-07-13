"""Governance helpers for V3.1 eval threshold changes."""

from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_MIN_THRESHOLD_CHANGE_N = 20
DEFAULT_MAX_AUTO_THRESHOLD_DELTA = 0.03


@dataclass(frozen=True)
class EvalThresholdChangeDecision:
    current_threshold: float
    proposed_threshold: float
    bounded_threshold: float
    delta: float
    sample_count: int
    min_n_required: int
    max_auto_delta: float
    golden_set_regression: bool
    golden_set_improved: bool
    approval_token_id: str | None
    affects_publish_send_post: bool
    affects_health_privacy_compliance: bool
    requires_human_approval: bool
    auto_allowed: bool
    allowed: bool
    reasons: list[str] = field(default_factory=list)


def govern_eval_threshold_change(
    *,
    current_threshold: float,
    proposed_threshold: float,
    sample_count: int,
    golden_set_regression: bool = False,
    golden_set_improved: bool = False,
    approval_token_id: str | None = None,
    affects_publish_send_post: bool = False,
    affects_health_privacy_compliance: bool = False,
    min_n_required: int = DEFAULT_MIN_THRESHOLD_CHANGE_N,
    max_auto_delta: float = DEFAULT_MAX_AUTO_THRESHOLD_DELTA,
) -> EvalThresholdChangeDecision:
    """Apply the V3.1 threshold-change rule.

    Automatic threshold changes require enough evidence, no golden-set
    regression, and a small bounded delta. Public/user-facing or
    health/privacy/compliance-affecting changes require a human approval token.
    """

    delta = proposed_threshold - current_threshold
    bounded_delta = max(-max_auto_delta, min(max_auto_delta, delta))
    bounded_threshold = round(current_threshold + bounded_delta, 4)
    delta_within_bound = abs(delta) <= max_auto_delta
    approval_present = bool(approval_token_id)
    requires_human_approval = affects_publish_send_post or affects_health_privacy_compliance
    evidence_gate = sample_count >= min_n_required or golden_set_improved or approval_present
    auto_allowed = (
        sample_count >= min_n_required
        and not golden_set_regression
        and delta_within_bound
        and not requires_human_approval
    )

    reasons: list[str] = []
    if not evidence_gate:
        reasons.append("insufficient_evidence")
    if golden_set_regression:
        reasons.append("golden_set_regression")
    if not delta_within_bound:
        reasons.append("delta_exceeds_0.03")
    if requires_human_approval and not approval_present:
        reasons.append("human_approval_required")

    allowed = (
        evidence_gate
        and not golden_set_regression
        and delta_within_bound
        and (not requires_human_approval or approval_present)
    )
    if allowed and auto_allowed:
        reasons.append("auto_allowed")
    elif allowed and approval_present:
        reasons.append("human_approved")
    elif allowed and golden_set_improved:
        reasons.append("golden_set_improved")

    return EvalThresholdChangeDecision(
        current_threshold=current_threshold,
        proposed_threshold=proposed_threshold,
        bounded_threshold=bounded_threshold,
        delta=round(delta, 4),
        sample_count=max(sample_count, 0),
        min_n_required=min_n_required,
        max_auto_delta=max_auto_delta,
        golden_set_regression=golden_set_regression,
        golden_set_improved=golden_set_improved,
        approval_token_id=approval_token_id,
        affects_publish_send_post=affects_publish_send_post,
        affects_health_privacy_compliance=affects_health_privacy_compliance,
        requires_human_approval=requires_human_approval,
        auto_allowed=auto_allowed,
        allowed=allowed,
        reasons=reasons,
    )


def govern_eval_threshold_change_from_metadata(
    metadata: dict,
    *,
    approval_token_id: str | None = None,
) -> EvalThresholdChangeDecision:
    """Parse action metadata and apply threshold-change governance."""

    effective_approval = str(metadata.get("approval_token_id") or approval_token_id or "") or None
    return govern_eval_threshold_change(
        current_threshold=_required_float(metadata, "current_threshold"),
        proposed_threshold=_required_float(metadata, "proposed_threshold"),
        sample_count=_safe_int(metadata.get("min_n") or metadata.get("sample_count")),
        golden_set_regression=_truthy(metadata.get("golden_set_regression")),
        golden_set_improved=_truthy(metadata.get("golden_set_improved")),
        approval_token_id=effective_approval,
        affects_publish_send_post=_truthy(metadata.get("affects_publish_send_post")),
        affects_health_privacy_compliance=_truthy(metadata.get("affects_health_privacy_compliance")),
    )


def _required_float(metadata: dict, key: str) -> float:
    value = metadata.get(key)
    if value is None or value == "":
        raise ValueError(f"missing {key}")
    return float(value)


def _safe_int(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
