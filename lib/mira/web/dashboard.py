"""Data model for the V3 monitor/config dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re

from mira.engine.effect_log import OPEN_STATUSES, EffectLog
from mira.engine.risk_gate import ApprovalStore
from mira.evals import (
    BRIEFING_FEEDBACK_BUTTONS,
    build_north_star_watch_gates,
    build_operational_eval_bundle,
    build_strategic_scorecard,
    build_weekly_blind_sample,
    evaluate_briefing_interest_fit,
)
from mira.implementation_status import build_v31_implementation_status_matrix
from mira.kernel.causal import CausalEvidenceLog
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.ledger import ExperienceLedger
from mira.kernel.schema import MemoryKernel
from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES, SOFT_POLICY_SPECS


@dataclass(frozen=True)
class DashboardSnapshot:
    active_pipelines: list[str]
    scars: list[str]
    active_hypotheses: list[str]
    skill_traces: dict[str, float]
    recent_experience_ids: list[str]
    hard_policy_count: int
    soft_policy_count: int
    review_queues: dict[str, list[dict[str, str]]]
    effect_log_ids: list[str]
    causal_evidence_counts: dict[str, int]
    approval_capacity: dict[str, object]
    operational_scorecard: dict[str, object]
    strategic_scorecard: dict[str, object]
    implementation_status_matrix: list[dict[str, object]]


def build_dashboard_snapshot(
    kernel: MemoryKernel,
    ledger: ExperienceLedger,
    commit_log: MemoryCommitLog | None = None,
    effect_log: EffectLog | None = None,
    approval_store: ApprovalStore | None = None,
    causal_evidence_log: CausalEvidenceLog | None = None,
    include_implementation_status: bool = True,
) -> DashboardSnapshot:
    all_records = ledger.list(limit=500)
    recent = all_records[-20:]
    commits = commit_log.list(limit=50) if commit_log else []
    causal_evidence = causal_evidence_log.list(limit=500) if causal_evidence_log else []
    effects = effect_log.list(limit=20) if effect_log else []
    pending_approvals = approval_store.list_requests(status="pending") if approval_store else []
    approval_events = approval_store.list_events() if approval_store else []
    memory_queue = [
        {
            "commit_id": commit.commit_id,
            "proposal_id": commit.proposal_id,
            "pipeline": commit.pipeline,
            "status": commit.status,
            "reason": "; ".join(f.reason for f in commit.findings),
            "finding_type": commit.findings[0].finding_type if commit.findings else "",
            "severity": commit.findings[0].severity if commit.findings else "",
            "source_trust": commit.source_trust,
            "memory_class": commit.memory_class,
            "risk_level": commit.risk_level,
            "privacy_tier": commit.privacy_tier,
            "evidence_refs": ", ".join(commit.evidence_refs),
            "contradictions": "; ".join(commit.contradictions),
            "available_decisions": "allow, reject, quarantine, edit, merge",
            **_memory_commit_review_context(commit),
        }
        for commit in commits
        if commit.status in {"quarantined", "requires_human", "rejected"}
    ]
    incident_queue = [
        {
            "run_id": record.id,
            "pipeline": record.pipeline,
            "status": record.outcome,
            "proposal_id": record.memory_delta_proposal_id,
            **_incident_review_context(record),
        }
        for record in recent
        if _is_incident_record(record)
    ]
    latest_effects = {effect.idempotency_key: effect for effect in effects}
    effect_queue = [
        {
            "effect_id": effect.effect_id,
            "pipeline": effect.pipeline,
            "action": effect.action,
            "target": effect.target,
            "status": effect.status,
            "idempotency_key": effect.idempotency_key,
            "preview_hash": effect.preview_hash,
            "approval_token_id": effect.approval_token_id or "",
            "replay_bundle_ref": effect.replay_bundle_ref,
            "external_ref": effect.external_ref or "",
            "reconciliation_ref": effect.reconciliation_ref or "",
            "inspection_command_template": _effect_reconciliation_inspection_command(effect),
            **_effect_reconciliation_review_context(effect),
        }
        for effect in latest_effects.values()
        if effect.status in OPEN_STATUSES
    ]
    approval_queue = [
        {
            "request_id": request.request_id,
            "pipeline": request.scope,
            "action": request.action,
            "risk": request.risk,
            "reason": request.reason,
            "preview_hash": request.preview_hash,
            "created_at": request.created_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
            "age_minutes": f"{_approval_age_minutes(request.created_at):.2f}",
            "evidence": request.reason,
            "rollback": _approval_rollback_note(request.action, request.risk),
            "decision": "pending",
            **_approval_review_context(request),
        }
        for request in pending_approvals
    ]
    approval_digest = approval_store.low_risk_digest() if approval_store else None
    approval_digest_queue = [_approval_digest_queue_item(approval_digest)] if approval_digest else []
    workspace_root = _infer_workspace_root(ledger)
    briefing_feedback_queue = [
        _briefing_feedback_queue_item(item, workspace_root) for item in build_weekly_blind_sample(all_records)
    ]
    public_writeup_queue = _public_writeup_review_queue(all_records, workspace_root)
    public_feedback_queue = _public_feedback_followup_queue(all_records, workspace_root)
    customer_discovery_queue = _customer_discovery_feedback_queue(all_records, workspace_root)
    provider_provisioning_queue = _provider_provisioning_review_queue(workspace_root)
    review_queues = {
        "approval": approval_queue,
        "approval_digest": approval_digest_queue,
        "memory_commit": memory_queue,
        "experiment": [_experiment_queue_item(h) for h in kernel.pending_hypotheses],
        "incident_dlq": incident_queue,
        "effect_reconciliation": effect_queue,
        "briefing_feedback": briefing_feedback_queue,
        "public_writeup_review": public_writeup_queue,
        "public_feedback_followup": public_feedback_queue,
        "customer_discovery_feedback": customer_discovery_queue,
        "provider_provisioning": provider_provisioning_queue,
    }
    approval_capacity = _approval_capacity(approval_store, pending_approvals)
    operational_bundle = build_operational_eval_bundle(all_records, commits, effects, causal_evidence, approval_events)
    operational = operational_bundle.scorecard
    strategic = build_strategic_scorecard(all_records)
    briefing_interest = evaluate_briefing_interest_fit(all_records)
    north_star_watch_gates = build_north_star_watch_gates(strategic, briefing_interest, review_queues)
    return DashboardSnapshot(
        active_pipelines=sorted(PIPELINE_CATALOG),
        scars=[scar.scar_id for scar in kernel.scars],
        active_hypotheses=[h.hypothesis_id for h in kernel.pending_hypotheses if h.status == "testing"],
        skill_traces={trace.skill_name: trace.success_rate for trace in kernel.skill_traces},
        recent_experience_ids=[record.id for record in recent],
        hard_policy_count=sum(len(names) for names in HARD_POLICY_NAMES.values()),
        soft_policy_count=len(SOFT_POLICY_SPECS),
        review_queues=review_queues,
        effect_log_ids=[entry.effect_id for entry in effects],
        causal_evidence_counts=_causal_evidence_counts(causal_evidence),
        approval_capacity=approval_capacity,
        operational_scorecard={
            "score": operational.score,
            "hard_gate_failures": operational.hard_gate_failures,
            "repeated_error": operational.repeated_error,
            "causal_memory": operational.causal_memory,
            "output_quality": operational.output_quality,
            "memory_health": operational.memory_health,
            "self_evolution": operational.self_evolution,
            "approval_safety": operational.approval_safety,
            "traceability": operational.traceability,
            "critical_memory_pollution": operational.critical_memory_pollution,
            "unapproved_high_risk_action": operational.unapproved_high_risk_action,
            "unreplayable_action": operational.unreplayable_action,
            "invalid_replay_bundle": operational.invalid_replay_bundle,
            "orphan_important_action": operational.orphan_important_action,
            "causal_link_validity": operational.causal_link_validity,
            "l4_required_causal_evidence": operational.l4_required_causal_evidence,
            "eval_record_count": len(operational_bundle.eval_records),
            "outcome_record_count": len(operational_bundle.outcome_records),
            "decision_record_count": len(operational_bundle.decision_records),
            "behavioral_effect_count": len(operational_bundle.behavioral_effects),
            "approval_event_count": len(operational_bundle.approval_events),
            "run_evidence_bundle_count": len(operational_bundle.run_evidence_bundles),
            "metrics": [
                {
                    "name": metric.name,
                    "score": metric.score,
                    "passed": metric.passed,
                    "detail": metric.detail,
                }
                for metric in operational_bundle.metrics
            ],
        },
        strategic_scorecard={
            "score": strategic.score,
            "hard_gate_failures": strategic.hard_gate_failures,
            "a2a_experiments_completed": strategic.a2a_experiments_completed,
            "reproducible_artifacts": strategic.reproducible_artifacts,
            "tool_prototypes": strategic.tool_prototypes,
            "public_writeups": strategic.public_writeups,
            "public_feedback_items": strategic.public_feedback_items,
            "product_thesis_updates": strategic.product_thesis_updates,
            "commercial_options": strategic.commercial_options,
            "watch_gates": north_star_watch_gates,
            "watch_gate_count": len(north_star_watch_gates),
            "briefing_feedback_items": briefing_interest.feedback_item_count,
            "briefing_feedback_coverage_rate": briefing_interest.feedback_coverage_rate,
        },
        implementation_status_matrix=(
            build_v31_implementation_status_matrix(workspace_root) if include_implementation_status else []
        ),
    )


def _infer_workspace_root(ledger: ExperienceLedger) -> Path:
    path = getattr(ledger, "path", None)
    if path:
        resolved = Path(path).resolve()
        if len(resolved.parents) >= 3 and resolved.parent.name == "v3" and resolved.parent.parent.name == "data":
            return resolved.parents[2]
    return Path.cwd()


def _causal_evidence_counts(causal_evidence: list) -> dict[str, int]:
    counts = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0}
    for evidence in causal_evidence:
        level = getattr(evidence, "level", "")
        if level in counts:
            counts[level] += 1
    return counts


def _approval_capacity(approval_store: ApprovalStore | None, pending_approvals: list) -> dict[str, object]:
    daily_budget_minutes = 15
    warning_minutes = 20
    hard_throttle_minutes = 30
    state = (
        approval_store.capacity_state()
        if approval_store
        else {
            "pending": len(pending_approvals),
            "budget": 10,
            "remaining": max(0, 10 - len(pending_approvals)),
            "queue_age_p95_minutes": 0.0,
            "over_budget": False,
            "auto_pause_noncritical": False,
        }
    )
    return {
        **state,
        "daily_budget_minutes": daily_budget_minutes,
        "warning_minutes": warning_minutes,
        "hard_throttle_minutes": hard_throttle_minutes,
    }


def _approval_age_minutes(created_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 60)


def _approval_rollback_note(action: str, risk: str) -> str:
    if "publish" in action or risk == "publish_public":
        return "keep staged draft unpublished or remove provider artifact if already executed"
    if risk in {"code_config", "destructive", "memory_kernel"}:
        return "use recorded rollback pointer or compensating memory/effect record"
    if risk in {"external_provider", "financial_external", "health_external"}:
        return "reconcile provider state and run configured compensation path if execution occurred"
    return "no external rollback expected before approval"


def _approval_review_context(request) -> dict[str, str]:
    return {
        "why_now": f"{request.scope}.{request.action} is waiting at a runtime approval boundary",
        "what_will_change": f"Approving allows run {request.run_id} to execute this action with the reviewed preview",
        "what_can_go_wrong": _approval_risk_note(request.action, request.risk),
    }


def _approval_risk_note(action: str, risk: str) -> str:
    if risk == "publish_public" or "publish" in action or "post" in action:
        return "public output may expose private context or require later correction"
    if risk in {"financial_external", "health_external"}:
        return "external financial or health state may be written incorrectly"
    if risk in {"code_config", "memory_kernel", "destructive"}:
        return "system behavior or memory state may change in a way that needs rollback"
    return "external provider state may diverge from the local effect log"


def _approval_digest_queue_item(digest: dict[str, object]) -> dict[str, str]:
    request_ids = [str(item) for item in digest.get("request_ids", [])]
    actions = [str(item) for item in digest.get("actions", [])]
    scopes = [str(item) for item in digest.get("scopes", [])]
    risks = [str(item) for item in digest.get("risks", [])]
    preview_hashes = [str(item) for item in digest.get("preview_hashes", [])]
    return {
        "digest_id": str(digest.get("digest_id", "")),
        "request_count": str(digest.get("request_count", 0)),
        "request_ids": ", ".join(request_ids),
        "actions": ", ".join(actions),
        "scopes": ", ".join(scopes),
        "risks": ", ".join(risks),
        "oldest_created_at": _dashboard_dt(digest.get("oldest_created_at")),
        "next_expires_at": _dashboard_dt(digest.get("next_expires_at")),
        "preview_hashes": ", ".join(preview_hashes),
        "estimated_human_minutes": f"{float(digest.get('estimated_human_minutes', 0.0)):.2f}",
        "decision": "batch_review_only",
        "why_now": "Multiple low-risk approval requests can be reviewed together within the daily budget",
        "what_will_change": "Operator can inspect the grouped previews and then decide each underlying request explicitly",
        "evidence": f"{len(request_ids)} preview-bound requests: {', '.join(request_ids)}",
        "what_can_go_wrong": "Batch review can miss a bad individual preview if request ids and hashes are not checked",
        "rollback": "deny or leave individual requests pending; this digest does not grant anything automatically",
    }


def _dashboard_dt(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _memory_commit_review_context(commit) -> dict[str, str]:
    finding_reason = "; ".join(f.reason for f in commit.findings) or commit.status
    rollback = commit.rollback_pointer or "reject or keep quarantined; no kernel change is required"
    return {
        "why_now": "Security gateway did not apply this memory proposal automatically",
        "what_will_change": "Operator decision can allow, reject, quarantine, edit, or merge the proposed memory delta",
        "evidence": finding_reason,
        "what_can_go_wrong": "Unsafe memory could pollute future snapshots or suppress required approval policy",
        "rollback": rollback,
    }


def _experiment_review_context(hypothesis) -> dict[str, str]:
    latest = hypothesis.evidence_for[-1] if hypothesis.evidence_for else hypothesis.claim
    return {
        "why_now": "Active self-evolution or strategic hypothesis is awaiting review",
        "what_will_change": "New evidence can confirm, reject, or continue the experiment window",
        "evidence": latest,
        "what_can_go_wrong": "Weak evidence can overfit the system toward a local metric or stale hypothesis",
        "rollback": "record evidence_against or mark the hypothesis rejected before changing production behavior",
    }


def _experiment_queue_item(hypothesis) -> dict[str, str]:
    review_context = _experiment_review_context(hypothesis)
    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "pipeline": hypothesis.test_pipeline,
        "status": hypothesis.status,
        "claim": hypothesis.claim,
        "baseline_window": hypothesis.baseline_window or f"prior {hypothesis.test_pipeline} evidence window",
        "test_window": hypothesis.test_window or "current active experiment window",
        "min_n": str(hypothesis.min_n),
        "current_metric": hypothesis.current_metric or _experiment_current_metric(hypothesis),
        "rollback_plan": hypothesis.rollback_plan or review_context["rollback"],
        "evidence_for": str(len(hypothesis.evidence_for)),
        "evidence_against": str(len(hypothesis.evidence_against)),
        "latest_evidence": hypothesis.evidence_for[-1] if hypothesis.evidence_for else "",
        **review_context,
    }


def _experiment_current_metric(hypothesis) -> str:
    return f"evidence_for={len(hypothesis.evidence_for)} evidence_against={len(hypothesis.evidence_against)}"


def _briefing_feedback_queue_item(item, workspace_root: Path) -> dict[str, str]:
    buttons = list(BRIEFING_FEEDBACK_BUTTONS)
    packet_artifact = _briefing_feedback_packet_artifact(item.item_id, workspace_root)
    record_command = (
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py "
        f"--item-id {item.item_id} --button <button> --json"
    )
    packet_record_command = (
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py "
        f"--packet {packet_artifact} --button <button> --json"
    )
    packet_command = (
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py "
        f"--item-id {item.item_id} --json"
    )
    return {
        "item_id": item.item_id,
        "item_text": item.text,
        "topics": ", ".join(item.topics),
        "matched_interests": ", ".join(item.matched_interest_ids),
        "novelty_score": f"{item.novelty_score:.4f}",
        "actionability_score": f"{item.actionability_score:.4f}",
        "available_buttons": ", ".join(buttons),
        "feedback_ref_template": f"briefing_feedback:item={item.item_id}:button=<button>",
        "feedback_packet_artifact": str(packet_artifact),
        "feedback_packet_command_template": packet_command,
        "record_feedback_command_template": record_command,
        "record_feedback_from_packet_command_template": packet_record_command,
        "why_now": "Weekly blind-sample briefing item needs human interest feedback",
        "what_will_change": "Button feedback updates Eval 4 interest-fit, dismissal, and promotion metrics",
        "evidence": f"topics={', '.join(item.topics) or 'none'}; novelty={item.novelty_score:.4f}",
        "what_can_go_wrong": "Without feedback, briefing relevance can look good while drifting from real interests",
        "rollback": "leave unreviewed; no runtime behavior or external side effect changes",
    }


def _briefing_feedback_packet_artifact(item_id: str, workspace_root: Path) -> Path:
    packet_hash = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:12]
    return (
        workspace_root
        / "data"
        / "v3"
        / "artifacts"
        / "briefing_feedback_packets"
        / packet_hash
        / "briefing_feedback_packet.json"
    )


def _public_writeup_review_queue(records: list, workspace_root: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in reversed(records):
        plan_refs = [ref for ref in record.eval_refs if ref.startswith("public_writeup_plan:")]
        if not plan_refs:
            continue
        draft_artifacts = [
            artifact
            for artifact in record.artifacts
            if "writeup" in Path(artifact).name.lower() and "draft" in Path(artifact).name.lower()
        ]
        if not draft_artifacts:
            draft_artifacts = [""]
        for plan_ref in plan_refs:
            for artifact in draft_artifacts:
                key = (plan_ref, artifact)
                if key in seen:
                    continue
                seen.add(key)
                items.append(_public_writeup_review_queue_item(record, plan_ref, artifact, workspace_root))
                if len(items) >= 5:
                    return items
    return items


def _public_writeup_review_queue_item(record, plan_ref: str, artifact: str, workspace_root: Path) -> dict[str, str]:
    artifact_path = Path(artifact) if artifact else None
    title = _public_writeup_title(artifact_path)
    preview_hash = _public_writeup_preview_hash(artifact_path)
    safety_status, safety_findings = _public_writeup_safety_fields(artifact_path)
    slug = plan_ref.removeprefix("public_writeup_plan:") or record.id
    packet_artifact = _public_writeup_packet_artifact(slug, preview_hash, workspace_root)
    return {
        "run_id": record.id,
        "pipeline": record.pipeline,
        "plan_ref": plan_ref,
        "title": title,
        "draft_artifact": artifact,
        "preview_hash": preview_hash,
        "publication_safety": safety_status,
        "publication_safety_findings": safety_findings,
        "publication_safety_command_template": _public_writeup_safety_command(artifact),
        "publication_packet_artifact": str(packet_artifact),
        "publication_packet_command_template": _public_writeup_packet_command(slug, artifact, preview_hash),
        "decision": "needs_publication_review",
        "publish_ref_template": f"public_writeup:{slug}:url=<url>",
        "feedback_ref_template": f"external_feedback:{slug}:source=<source>",
        "record_evidence_command_template": _public_writeup_record_command(slug, artifact, preview_hash),
        "record_evidence_from_packet_command_template": _public_writeup_packet_record_command(packet_artifact),
        "why_now": "This strategic public-writeup draft has not been recorded as shipped",
        "what_will_change": "Operator review can approve publication or request edits before this draft is counted",
        "evidence": artifact or plan_ref,
        "what_can_go_wrong": "Public critique draft may expose private context or overstate evidence if published without review",
        "rollback": "leave draft unpublished, edit it, or record a correction/retraction if publication already happened",
    }


def _public_writeup_packet_artifact(slug: str, preview_hash: str, workspace_root: Path) -> Path:
    packet_hash = preview_hash[:12] if preview_hash else hashlib.sha256(slug.encode("utf-8")).hexdigest()[:12]
    return (
        workspace_root
        / "data"
        / "v3"
        / "artifacts"
        / "publication_packets"
        / slug
        / packet_hash
        / "publication_packet.json"
    )


def _public_writeup_packet_record_command(packet_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_public_evidence.py",
            "--packet",
            str(packet_artifact),
            "--published-url",
            "<url>",
            "--feedback-source",
            "<source>",
            "--json",
        ]
    )


def _public_feedback_followup_queue(records: list, workspace_root: Path) -> list[dict[str, str]]:
    feedback_slugs = {
        _feedback_ref_slug(ref)
        for record in records
        for ref in record.eval_refs
        if ref.startswith(("external_feedback:", "public_feedback:", "reader_feedback:", "customer_discovery:"))
    }
    feedback_slugs.discard("")
    publication_stats = _publication_stats_by_url(workspace_root)
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in reversed(records):
        for ref in record.eval_refs:
            if not ref.startswith(("public_writeup:", "public_note:", "published_writeup:")):
                continue
            slug = _feedback_ref_slug(ref)
            if not slug or slug in feedback_slugs or slug in seen:
                continue
            published_url = _public_ref_url(ref) or _published_url_from_artifacts(record.artifacts)
            stats = publication_stats.get(published_url, {}) if published_url else {}
            seen.add(slug)
            items.append(_public_feedback_followup_queue_item(record.id, slug, published_url, stats, workspace_root))
            if len(items) >= 5:
                return items
    return items


def _public_feedback_followup_queue_item(
    record_id: str,
    slug: str,
    published_url: str,
    stats: dict[str, object],
    workspace_root: Path,
) -> dict[str, str]:
    comments = str(stats.get("comments", 0))
    likes = str(stats.get("likes", 0))
    restacks = str(stats.get("restacks", 0))
    views = str(stats.get("views", 0))
    fetched_at = str(stats.get("fetched_at", ""))
    stats_artifact = workspace_root / "data" / "social" / "publication_stats.json"
    packet_artifact = _public_feedback_packet_artifact(slug, published_url, workspace_root)
    command = _public_feedback_record_command(slug, published_url)
    packet_record_command = _public_feedback_packet_record_command(packet_artifact)
    packet_command = _public_feedback_packet_command(slug, published_url, stats_artifact)
    return {
        "publication_record_id": record_id,
        "slug": slug,
        "published_url": published_url,
        "publication_stats_artifact": str(stats_artifact),
        "publication_stats_fetched_at": fetched_at,
        "views": views,
        "likes": likes,
        "comments": comments,
        "restacks": restacks,
        "feedback_ref_template": f"external_feedback:{slug}:source=<source>",
        "feedback_packet_artifact": str(packet_artifact),
        "feedback_packet_command_template": packet_command,
        "record_feedback_command_template": command,
        "record_feedback_from_packet_command_template": packet_record_command,
        "decision": "needs_external_feedback",
        "why_now": "A public writeup is recorded, but no external feedback ref has been recorded for it",
        "what_will_change": "A verified comment, reply, review, or customer-discovery source can satisfy the feedback evidence gap",
        "evidence": f"publication stats comments={comments} likes={likes} restacks={restacks} fetched_at={fetched_at}",
        "what_can_go_wrong": "Counting generic engagement or unrelated social activity would inflate strategic feedback without a concrete source",
        "rollback": "leave feedback unrecorded until a concrete external source can be verified",
    }


def _public_feedback_packet_artifact(slug: str, published_url: str, workspace_root: Path) -> Path:
    packet_hash = hashlib.sha256(published_url.encode("utf-8")).hexdigest()[:12]
    return (
        workspace_root
        / "data"
        / "v3"
        / "artifacts"
        / "public_feedback_packets"
        / slug
        / packet_hash
        / "feedback_packet.json"
    )


def _public_feedback_packet_record_command(packet_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_public_feedback.py",
            "--packet",
            str(packet_artifact),
            "--feedback-source",
            "<source>",
            "--json",
        ]
    )


def _feedback_ref_slug(ref: str) -> str:
    parts = ref.strip().split(":", 2)
    if len(parts) < 2:
        return ""
    slug = parts[1].split("=", 1)[0].strip()
    return slug if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", slug) else ""


def _public_ref_url(ref: str) -> str:
    match = re.search(r":url=([^\s]+)$", ref.strip())
    return match.group(1) if match else ""


def _published_url_from_artifacts(artifacts: list[str]) -> str:
    for artifact in artifacts:
        path = Path(artifact)
        if path.name not in {"public_evidence.json", "public_feedback.json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        url = str(payload.get("published_url") or "")
        if url:
            return url
    return ""


def _publication_stats_by_url(workspace_root: Path) -> dict[str, dict[str, object]]:
    path = workspace_root / "data" / "social" / "publication_stats.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    fetched_at = str(payload.get("fetched_at") or "")
    rows: dict[str, dict[str, object]] = {}
    for article in payload.get("articles", []):
        if not isinstance(article, dict):
            continue
        enriched = {**article, "fetched_at": fetched_at}
        article_id = article.get("id")
        slug = article.get("slug")
        if article_id:
            rows[f"https://uncountablemira.substack.com/p/{article_id}"] = enriched
        if slug:
            rows[f"https://uncountablemira.substack.com/p/{slug}"] = enriched
    return rows


def _public_feedback_packet_command(slug: str, published_url: str, stats_artifact: Path) -> str:
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_prepare_public_feedback_packet.py",
        "--slug",
        slug,
        "--published-url",
        published_url,
        "--stats-artifact",
        str(stats_artifact),
    ]
    parts.append("--json")
    return " ".join(parts)


def _public_feedback_record_command(slug: str, published_url: str) -> str:
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_record_public_feedback.py",
        "--slug",
        slug,
        "--feedback-source",
        "<source>",
    ]
    if published_url:
        parts.extend(["--published-url", published_url])
    parts.append("--json")
    return " ".join(parts)


def _customer_discovery_feedback_queue(records: list, workspace_root: Path) -> list[dict[str, str]]:
    scorecard = build_strategic_scorecard(records)
    if scorecard.public_feedback_items >= 3:
        return []
    remaining = 3 - scorecard.public_feedback_items
    topic = "a2a_trust_manifest"
    question = "What would make this A2A trust/evidence workflow useful, credible, or not worth adopting?"
    packet_artifact = _customer_discovery_packet_artifact(topic, question, workspace_root)
    return [
        {
            "topic": topic,
            "question": question,
            "missing_feedback_count": str(remaining),
            "feedback_ref_template": "customer_discovery:<source>",
            "feedback_packet_artifact": str(packet_artifact),
            "feedback_packet_command_template": _customer_discovery_packet_command(topic),
            "record_feedback_command_template": _customer_discovery_record_command(),
            "record_feedback_from_packet_command_template": _customer_discovery_packet_record_command(packet_artifact),
            "decision": "needs_customer_discovery_feedback",
            "why_now": "The V3.1 strategic feedback gate requires three concrete external feedback events",
            "what_will_change": "A validated customer interview or builder review can count as independent external feedback",
            "evidence": f"external_feedback_events={scorecard.public_feedback_items}/3",
            "what_can_go_wrong": "Internal opinions, placeholder sources, or vague impressions would falsely satisfy the gate",
            "rollback": "leave the event unrecorded until a concrete external source and insight can be verified",
        }
    ]


def _customer_discovery_packet_artifact(topic: str, question: str, workspace_root: Path) -> Path:
    packet_hash = hashlib.sha256(f"{topic}:{question}".encode("utf-8")).hexdigest()[:12]
    return (
        workspace_root
        / "data"
        / "v3"
        / "artifacts"
        / "customer_discovery_packets"
        / topic
        / packet_hash
        / "customer_discovery_packet.json"
    )


def _customer_discovery_packet_command(topic: str) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_prepare_customer_discovery_packet.py",
            "--topic",
            topic,
            "--json",
        ]
    )


def _customer_discovery_record_command() -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--source",
            "<source>",
            "--insight",
            "<insight>",
            "--json",
        ]
    )


def _customer_discovery_packet_record_command(packet_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--packet",
            str(packet_artifact),
            "--source",
            "<source>",
            "--insight",
            "<insight>",
            "--json",
        ]
    )


def _public_writeup_record_command(slug: str, artifact: str, preview_hash: str) -> str:
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_record_public_evidence.py",
        "--slug",
        slug,
        "--published-url",
        "<url>",
    ]
    if artifact:
        parts.extend(["--draft-artifact", artifact])
    if preview_hash:
        parts.extend(["--expected-preview-hash", preview_hash])
    parts.extend(["--feedback-source", "<source>", "--json"])
    return " ".join(parts)


def _public_writeup_safety_command(artifact: str) -> str:
    if not artifact:
        return ""
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_public_writeup_safety.py",
            "--draft-artifact",
            artifact,
            "--json",
        ]
    )


def _public_writeup_packet_command(slug: str, artifact: str, preview_hash: str) -> str:
    if not artifact:
        return ""
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_prepare_public_writeup_packet.py",
        "--slug",
        slug,
        "--draft-artifact",
        artifact,
    ]
    if preview_hash:
        parts.extend(["--expected-preview-hash", preview_hash])
    parts.append("--json")
    return " ".join(parts)


def _public_writeup_safety_fields(path: Path | None) -> tuple[str, str]:
    if path is None:
        return "blocked", "draft artifact is missing"
    try:
        from mira.runtime import public_writeup_safety_report

        report = public_writeup_safety_report(path)
    except Exception as exc:
        return "blocked", f"{type(exc).__name__}: {exc}"
    return ("pass" if report.passed else "blocked", "; ".join(report.findings[:5]))


def _public_writeup_title(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
    except OSError:
        return ""
    return ""


def _public_writeup_preview_hash(path: Path | None) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    try:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _provider_provisioning_review_queue(workspace_root: Path) -> list[dict[str, str]]:
    try:
        from mira.runtime import (
            default_v3_paths,
            provider_production_canary_surface,
            provider_production_readiness_report,
        )

        paths = default_v3_paths(workspace_root)
        report = provider_production_readiness_report(root=workspace_root)
    except Exception as exc:
        return [
            {
                "surface": "provider_provisioning",
                "status": "readiness_check_error",
                "readiness_finding_count": "1",
                "missing_env_count": "0",
                "missing_env_vars": "",
                "resolver_config": "",
                "adapter_config": "",
                "env_template_artifact": "",
                "runbook_artifact": "",
                "configured_resolvers": "",
                "configured_adapters": "",
                "readiness_command_template": _provider_readiness_command(workspace_root),
                "env_template_command_template": "",
                "runbook_command_template": "",
                "scoped_provider": "",
                "scoped_env_template_artifact": "",
                "scoped_env_template_command_template": "",
                "scoped_missing_env_count": "0",
                "scoped_missing_env_vars": "",
                "scoped_readiness_command_template": "",
                "scoped_dry_run_command_template": "",
                "scoped_canary_command_template": "",
                "decision": "blocked_external",
                "why_now": "Provider readiness could not be evaluated, so production canaries cannot safely run",
                "what_will_change": "Fixing the readiness check restores visibility before any provider side effect executes",
                "evidence": f"{type(exc).__name__}: {exc}",
                "what_can_go_wrong": "Skipping readiness can produce untracked or unreconciled external side effects",
                "rollback": "do not run production canaries until readiness is visible and passing",
            }
        ]
    if report.get("ready"):
        return []

    findings = _provider_readiness_findings(report)
    missing_env_vars = _provider_missing_env_vars(findings)
    canary_surface = provider_production_canary_surface()
    scoped_provider = _provider_scoped_queue_provider(report, canary_surface, findings)
    scoped_missing_env_vars = _provider_missing_env_vars(_provider_findings_for_provider(findings, scoped_provider))
    requires_resolver = bool(canary_surface.get(scoped_provider, {}).get("requires_resolver"))
    resolver_config = str(report.get("resolver_config") or paths.provider_resolvers)
    adapter_config = str(report.get("adapter_config") or paths.provider_adapters)
    scoped_env_template = (
        paths.root / f"provider_provisioning.{scoped_provider}.template"
        if scoped_provider
        else paths.root / "provider_provisioning.scoped.template"
    )
    return [
        {
            "surface": "provider_provisioning",
            "status": "blocked_external",
            "readiness_finding_count": str(len(findings)),
            "missing_env_count": str(len(missing_env_vars)),
            "missing_env_vars": ", ".join(missing_env_vars[:12]),
            "resolver_config": str(report.get("resolver_config") or paths.provider_resolvers),
            "adapter_config": str(report.get("adapter_config") or paths.provider_adapters),
            "env_template_artifact": str(paths.root / "provider_provisioning.template"),
            "runbook_artifact": str(paths.root / "provider_provisioning.runbook.md"),
            "configured_resolvers": ", ".join(str(item) for item in report.get("configured_resolvers", [])),
            "configured_adapters": ", ".join(str(item) for item in report.get("configured_adapters", [])),
            "readiness_command_template": _provider_readiness_command(workspace_root),
            "env_template_command_template": _provider_env_template_command(
                workspace_root, paths.root / "provider_provisioning.template"
            ),
            "runbook_command_template": _provider_runbook_command(
                workspace_root, paths.root / "provider_provisioning.runbook.md"
            ),
            "scoped_provider": scoped_provider,
            "scoped_env_template_artifact": str(scoped_env_template),
            "scoped_env_template_command_template": _provider_scoped_env_template_command(
                workspace_root,
                resolver_config,
                adapter_config,
                scoped_provider,
                scoped_env_template,
                requires_resolver=requires_resolver,
            ),
            "scoped_missing_env_count": str(len(scoped_missing_env_vars)),
            "scoped_missing_env_vars": ", ".join(scoped_missing_env_vars),
            "scoped_readiness_command_template": _provider_scoped_readiness_command(
                workspace_root,
                resolver_config,
                adapter_config,
                scoped_provider,
                requires_resolver=requires_resolver,
            ),
            "scoped_dry_run_command_template": _provider_scoped_canary_command(
                workspace_root,
                resolver_config,
                adapter_config,
                scoped_provider,
                dry_run=True,
            ),
            "scoped_canary_command_template": _provider_scoped_canary_command(
                workspace_root,
                resolver_config,
                adapter_config,
                scoped_provider,
            ),
            "decision": "blocked_external",
            "why_now": "Provider endpoints and tokens are still missing, so production canaries remain externally blocked",
            "what_will_change": "Provisioned env vars can move provider readiness from blocked to canary-eligible without changing local evidence",
            "evidence": "; ".join(findings[:3]) if findings else "provider readiness is blocked",
            "what_can_go_wrong": "Running a canary before readiness passes can create unapproved or unreconciled provider side effects",
            "rollback": "leave provider effects staged; rerun readiness and canary commands only after env-backed credentials are present",
        }
    ]


def _provider_readiness_findings(report: dict[str, object]) -> list[str]:
    findings: list[str] = []
    surfaces = report.get("findings")
    if not isinstance(surfaces, dict):
        return findings
    for surface, provider_map in surfaces.items():
        if not isinstance(provider_map, dict):
            continue
        for provider, items in provider_map.items():
            if not isinstance(items, list):
                continue
            for item in items:
                findings.append(f"{surface}.{provider}: {item}")
    return findings


def _provider_missing_env_vars(findings: list[str]) -> list[str]:
    seen: set[str] = set()
    missing: list[str] = []
    for finding in findings:
        for env_var in re.findall(r"\bMIRA_[A-Z0-9_]+\b", finding):
            if env_var in seen:
                continue
            seen.add(env_var)
            missing.append(env_var)
    return missing


def _provider_findings_for_provider(findings: list[str], provider: str) -> list[str]:
    if not provider:
        return []
    prefixes = (f"provider_resolvers.{provider}:", f"provider_adapters.{provider}:")
    return [finding for finding in findings if finding.startswith(prefixes)]


def _provider_scoped_queue_provider(
    report: dict[str, object],
    canary_surface: dict[str, dict[str, object]],
    findings: list[str],
) -> str:
    configured_adapters = [str(item) for item in report.get("configured_adapters", [])]
    candidates = [provider for provider in configured_adapters if provider in canary_surface]
    if candidates:
        priority = {
            provider: index for index, provider in enumerate(("tts", "social", "substack", "rss", "market", "health"))
        }
        ranked = sorted(
            candidates,
            key=lambda provider: (
                len(_provider_missing_env_vars(_provider_findings_for_provider(findings, provider))),
                bool(canary_surface.get(provider, {}).get("requires_resolver")),
                priority.get(provider, len(priority)),
                provider,
            ),
        )
        return ranked[0]
    return sorted(canary_surface)[0] if canary_surface else ""


def _provider_readiness_command(workspace_root: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_provider_readiness.py",
            "--root",
            str(workspace_root),
            "--json",
        ]
    )


def _provider_runbook_command(workspace_root: Path, runbook_path: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_provider_readiness.py",
            "--root",
            str(workspace_root),
            "--write-runbook",
            str(runbook_path),
            "--overwrite-runbook",
            "--json",
        ]
    )


def _provider_env_template_command(workspace_root: Path, env_template_path: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_provider_readiness.py",
            "--root",
            str(workspace_root),
            "--write-env-template",
            str(env_template_path),
            "--overwrite-env-template",
            "--json",
        ]
    )


def _provider_scoped_readiness_command(
    workspace_root: Path,
    resolver_config: str,
    adapter_config: str,
    provider: str,
    *,
    requires_resolver: bool,
) -> str:
    if not provider:
        return ""
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_provider_readiness.py",
        "--root",
        str(workspace_root),
        "--resolver-config",
        resolver_config,
        "--adapter-config",
        adapter_config,
    ]
    if requires_resolver:
        parts.extend(["--require-resolver", provider])
    else:
        parts.append("--skip-resolvers")
    parts.extend(["--require-adapter", provider, "--json"])
    return " ".join(parts)


def _provider_scoped_env_template_command(
    workspace_root: Path,
    resolver_config: str,
    adapter_config: str,
    provider: str,
    env_template_path: Path,
    *,
    requires_resolver: bool,
) -> str:
    if not provider:
        return ""
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_provider_readiness.py",
        "--root",
        str(workspace_root),
        "--resolver-config",
        resolver_config,
        "--adapter-config",
        adapter_config,
        "--write-env-template",
        str(env_template_path),
        "--overwrite-env-template",
    ]
    if requires_resolver:
        parts.extend(["--require-resolver", provider])
    else:
        parts.append("--skip-resolvers")
    parts.extend(["--require-adapter", provider, "--json"])
    return " ".join(parts)


def _provider_scoped_canary_command(
    workspace_root: Path,
    resolver_config: str,
    adapter_config: str,
    provider: str,
    *,
    dry_run: bool = False,
) -> str:
    if not provider:
        return ""
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_provider_production_canary.py",
        "--root",
        str(workspace_root),
        "--resolver-config",
        resolver_config,
        "--adapter-config",
        adapter_config,
        "--provider",
        provider,
    ]
    if dry_run:
        parts.append("--dry-run")
    parts.append("--json")
    return " ".join(parts)


def _incident_review_context(record) -> dict[str, str]:
    evidence = record.delta.what_failed or record.outcome
    return {
        "why_now": "Run failed or produced failure detail that needs triage",
        "what_will_change": "Triage can replay, classify root cause, create a scar, or add a golden regression",
        "evidence": evidence,
        "what_can_go_wrong": "A blind retry can repeat the same failure or duplicate an external side effect",
        "rollback": "use the replay bundle, effect log, and prior memory commit pointer before rerunning",
    }


def _effect_reconciliation_review_context(effect) -> dict[str, str]:
    evidence = effect.detail or effect.external_ref or effect.preview_hash or effect.idempotency_key
    return {
        "why_now": "Effect is still open and requires provider-state reconciliation",
        "what_will_change": "Provider evidence can mark the effect succeeded, failed, or keep it blocked for retry",
        "evidence": evidence,
        "what_can_go_wrong": "Retrying before reconciliation can duplicate the side effect",
        "rollback": "use provider refs, reconciliation refs, approval token, and compensating effect when available",
    }


def _effect_reconciliation_inspection_command(effect) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_effect_reconciliation.py",
            "--effect-id",
            effect.effect_id,
            "--json",
        ]
    )


def _is_incident_record(record) -> bool:
    if record.outcome == "approval_required":
        return False
    return record.outcome == "failed" or bool(record.delta.what_failed)
