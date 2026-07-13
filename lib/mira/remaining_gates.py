"""Render live V3.1 north-star remaining-gates handoffs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from mira.web.dashboard import DashboardSnapshot


def render_remaining_gates(snapshot: DashboardSnapshot, *, root: Path, report_date: date | None = None) -> str:
    strategic = snapshot.strategic_scorecard
    queues = snapshot.review_queues
    gates = [str(gate) for gate in strategic.get("watch_gates", [])]
    public_feedback = _first_queue_item(queues, "public_feedback_followup")
    customer_discovery = _first_queue_item(queues, "customer_discovery_feedback")
    public_writeup = _first_queue_item(queues, "public_writeup_review")
    briefing_items = list(queues.get("briefing_feedback") or [])
    provider = _first_queue_item(queues, "provider_provisioning")
    effect = _first_queue_item(queues, "effect_reconciliation")
    implementation_blockers = _implementation_blockers(snapshot.implementation_status_matrix)
    date_label = (report_date or date.today()).isoformat()
    gate_lines = [f"  - `{gate}`" for gate in gates] if gates else ["  - `PASS`"]

    lines = [
        "# V3.1 North-Star Remaining Gates",
        "",
        f"Date: {date_label}",
        "",
        "This handoff is generated from the live dashboard snapshot. It is a closure checklist for external or operator-gated work; it must not be used to invent feedback, publication, or provider evidence.",
        "",
        "Regenerate it from current state:",
        "",
        "```bash",
        f"PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_remaining_gates.py --date {date_label} --output {root / 'docs' / f'v31-north-star-remaining-gates-{date_label}.md'}",
        "```",
        "",
        "Prepare all local no-network closure packets for the current external-feedback, publication-review, and briefing-feedback queues:",
        "",
        "```bash",
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_north_star_closure_packets.py --json",
        "```",
        "",
        "## Current Live Scorecard",
        "",
        f"- strategic_score: `{float(strategic.get('score', 0.0)):.2f}`",
        f"- public_writeups: `{strategic.get('public_writeups', 0)}`",
        f"- external_feedback_events: `{strategic.get('public_feedback_items', 0)}`",
        f"- briefing_feedback_items: `{strategic.get('briefing_feedback_items', 0)}`",
        f"- briefing_feedback_coverage_rate: `{float(strategic.get('briefing_feedback_coverage_rate', 0.0)):.4f}`",
        "- watch_gates:",
        *gate_lines,
        f"- implementation_blockers: `{', '.join(implementation_blockers) if implementation_blockers else 'none'}`",
        "",
    ]
    lines.extend(_external_feedback_lines(strategic, public_feedback, customer_discovery, public_writeup))
    lines.extend(_briefing_feedback_lines(briefing_items))
    lines.extend(_provider_readiness_lines(provider))
    lines.extend(_effect_reconciliation_lines(effect))
    lines.extend(
        [
            "## Verification After Operator Actions",
            "",
            "Run these checks after recording feedback or provisioning providers:",
            "",
            "```bash",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions",
            "```",
            "",
            "```bash",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --json",
            "```",
            "",
            "```bash",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_north_star_report.py --output-dir data/v3/artifacts/north_star_reports --window-days 7",
            "```",
            "",
            "```bash",
            "PYTHONPATH=lib .venv/bin/python -m pytest tests/v3 -q",
            "```",
            "",
            "The goal is not complete until the dashboard strategic scorecard no longer reports the watch gates above and the implementation matrix has no non-external or newly introduced failed checks.",
            "",
        ]
    )
    return "\n".join(lines)


def _external_feedback_lines(
    strategic: Mapping[str, Any],
    public_feedback: Mapping[str, str],
    customer_discovery: Mapping[str, str],
    public_writeup: Mapping[str, str],
) -> list[str]:
    count = int(strategic.get("public_feedback_items") or 0)
    lines = [
        "## Gate 1: External Feedback Below Standard",
        "",
        f"Standard: record at least three concrete external feedback events. Current count: `{count}/3`.",
        "",
    ]
    if public_feedback:
        lines.extend(
            [
                "Recorded public writeup awaiting feedback:",
                "",
                f"- slug: `{public_feedback.get('slug', '')}`",
                f"- published URL: `{public_feedback.get('published_url', '')}`",
                f"- current stats snapshot: comments `{public_feedback.get('comments', '0')}`, likes `{public_feedback.get('likes', '0')}`, restacks `{public_feedback.get('restacks', '0')}`, views `{public_feedback.get('views', '0')}`",
                "",
            ]
        )
        _append_command(
            lines, "Prepare a feedback solicitation packet:", public_feedback.get("feedback_packet_command_template")
        )
        _append_command(
            lines,
            "Prepare feedback solicitation packets for every recorded writeup still missing feedback:",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py --all --json",
        )
        _append_command(
            lines,
            "After a real external source exists, record it. Replace `<source>` with a concrete, attributable source such as a comment URL, reply URL, review identifier, or customer-discovery reference:",
            public_feedback.get("record_feedback_command_template"),
        )
        _append_command(
            lines,
            "If a feedback packet has been prepared, prefer recording from its packet metadata:",
            public_feedback.get("record_feedback_from_packet_command_template"),
        )
        lines.append("Do not record generic engagement, internal notes, or placeholder sources as feedback.")
        lines.append("")
    else:
        lines.extend(["No recorded public writeup is currently queued for feedback follow-up.", ""])
    _append_command(
        lines,
        "Prepare an independent customer-discovery packet for feedback that is not tied to a public writeup:",
        customer_discovery.get(
            "feedback_packet_command_template",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest --json",
        ),
    )
    _append_command(
        lines,
        "After a real customer-discovery source exists, record it. Replace `<source>` and `<insight>` with concrete external evidence:",
        customer_discovery.get(
            "record_feedback_command_template",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --source <source> --insight <insight> --json",
        ),
    )
    _append_command(
        lines,
        "If a customer-discovery packet has been prepared, prefer recording from its packet metadata:",
        customer_discovery.get(
            "record_feedback_from_packet_command_template",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --packet data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json --source <source> --insight <insight> --json",
        ),
    )
    if public_writeup:
        lines.extend(
            [
                "Draft that can become another public feedback surface after operator publication review:",
                "",
                f"- slug: `{_slug_from_ref(public_writeup.get('publish_ref_template', ''))}`",
                f"- title: `{public_writeup.get('title', '')}`",
                f"- draft: `{public_writeup.get('draft_artifact', '')}`",
                f"- preview hash: `{public_writeup.get('preview_hash', '')}`",
                "",
            ]
        )
        _append_command(lines, "Safety check:", public_writeup.get("publication_safety_command_template"))
        _append_command(
            lines, "Prepare the publication packet:", public_writeup.get("publication_packet_command_template")
        )
        _append_command(
            lines,
            "After publication and at least one concrete external feedback source, record evidence. Replace `<url>` and `<source>` with the actual public URL and feedback source:",
            public_writeup.get("record_evidence_from_packet_command_template")
            or public_writeup.get("record_evidence_command_template"),
        )
    return lines + [""]


def _briefing_feedback_lines(items: list[Mapping[str, str]]) -> list[str]:
    lines = [
        "## Gate 2: Briefing Feedback Missing",
        "",
        "Standard: at least one operator feedback event on the current weekly blind-sample queue, then at least two promoted briefing items once feedback exists.",
        "",
    ]
    if not items:
        return lines + ["No briefing blind-sample queue items are currently exposed by the dashboard.", ""]
    first = items[0]
    lines.extend(
        [
            "Current first queue item:",
            "",
            f"- item id: `{first.get('item_id', '')}`",
            f"- topics: `{first.get('topics', '')}`",
            f"- matched interests: `{first.get('matched_interests', '')}`",
            f"- available buttons: `{first.get('available_buttons', '')}`",
            "",
        ]
    )
    _append_command(
        lines,
        "Prepare a local review packet for the item:",
        first.get("feedback_packet_command_template"),
    )
    _append_command(
        lines,
        "Prepare local review packets for the full current blind-sample queue:",
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --all --json",
    )
    _append_command(
        lines,
        "Record operator feedback by replacing `<button>` with one of the available buttons:",
        first.get("record_feedback_command_template"),
    )
    _append_command(
        lines,
        "If a briefing packet has been prepared, prefer recording from its packet metadata:",
        first.get("record_feedback_from_packet_command_template"),
    )
    lines.extend(
        [
            f"The dashboard exposes `{len(items)}` weekly blind-sample items. Use the dashboard queue or `v3_status --json` to choose additional items and avoid repeatedly scoring only the first sample.",
            "",
        ]
    )
    return lines


def _provider_readiness_lines(provider: Mapping[str, str]) -> list[str]:
    lines = [
        "## Gate 3: Provider Production Readiness Blocked",
        "",
        "Standard: provider readiness must pass with real endpoint/token-backed resolver or adapter configuration before production canaries run.",
        "",
    ]
    if not provider:
        return lines + ["No provider-provisioning queue item is currently exposed by the dashboard.", ""]
    lines.extend(
        [
            "Current global readiness:",
            "",
            f"- status: `{provider.get('status', '')}`",
            f"- readiness findings: `{provider.get('readiness_finding_count', '0')}`",
            f"- missing env vars: `{provider.get('missing_env_count', '0')}`",
            f"- smallest current canary scope: `{provider.get('scoped_provider', '')}`",
            f"- scoped missing env vars: `{provider.get('scoped_missing_env_vars', '')}`",
            "",
        ]
    )
    _append_command(
        lines, "Regenerate the no-secret full provisioning template:", provider.get("env_template_command_template")
    )
    _append_command(lines, "Regenerate the no-secret runbook:", provider.get("runbook_command_template"))
    _append_command(
        lines, "Regenerate the scoped canary template:", provider.get("scoped_env_template_command_template")
    )
    scoped_provider = provider.get("scoped_provider", "selected provider")
    lines.extend(
        [
            f"Provision real `{scoped_provider}` endpoint/token values in the operator's secret-backed shell, launchd environment, or secret manager. Do not commit secrets into `{provider.get('scoped_env_template_artifact', '')}`.",
            "",
        ]
    )
    _append_command(
        lines, "After provisioning, check scoped readiness:", provider.get("scoped_readiness_command_template")
    )
    _append_command(
        lines,
        "After scoped readiness passes, preview the production canary without mutating state:",
        provider.get("scoped_dry_run_command_template"),
    )
    _append_command(
        lines,
        "Only after scoped readiness reports ready, run the production canary:",
        provider.get("scoped_canary_command_template"),
    )
    lines.append("Keep any provider promotion behind approval, effect logging, reconciliation, and causal evidence.")
    lines.append("")
    return lines


def _effect_reconciliation_lines(effect: Mapping[str, str]) -> list[str]:
    lines = [
        "## Open Operator Review: Effect Reconciliation",
        "",
        "Standard: inspect unresolved side effects against replay-bundle and provider evidence before retrying, reconciling, or closing the effect.",
        "",
    ]
    if not effect:
        return lines + ["No open effect-reconciliation queue item is currently exposed by the dashboard.", ""]
    lines.extend(
        [
            "Current first unresolved effect:",
            "",
            f"- effect id: `{effect.get('effect_id', '')}`",
            f"- pipeline/action: `{effect.get('pipeline', '')}` / `{effect.get('action', '')}`",
            f"- target: `{effect.get('target', '')}`",
            f"- status: `{effect.get('status', '')}`",
            f"- idempotency key: `{effect.get('idempotency_key', '')}`",
            f"- preview hash: `{effect.get('preview_hash', '')}`",
            f"- approval token id: `{effect.get('approval_token_id', '')}`",
            f"- replay bundle ref: `{effect.get('replay_bundle_ref', '')}`",
            f"- external ref: `{effect.get('external_ref', '')}`",
            f"- reconciliation ref: `{effect.get('reconciliation_ref', '')}`",
            "",
        ]
    )
    _append_command(
        lines,
        "Inspect the effect without mutating the effect log:",
        effect.get("inspection_command_template"),
    )
    lines.extend(
        [
            "When operator evidence lives outside the default provider-state directory, add `--publish-manifest <path>`, `--rss-feed <path>`, or `--provider-state-manifest <path>` to the inspector command before deciding whether reconciliation is justified.",
            "Do not retry or mark the effect complete from local intent alone; reconcile only after provider evidence proves the external side effect succeeded, failed, or is still unknown.",
            "",
        ]
    )
    return lines


def _append_command(lines: list[str], label: str, command: str | None) -> None:
    if not command:
        return
    lines.extend([label, "", "```bash", command, "```", ""])


def _first_queue_item(queues: Mapping[str, list[Mapping[str, str]]], name: str) -> Mapping[str, str]:
    items = queues.get(name) or []
    return items[0] if items else {}


def _implementation_blockers(rows: list[dict[str, object]]) -> list[str]:
    blockers: list[str] = []
    for row in rows:
        status = str(row.get("status") or "unknown")
        failed_checks = [
            str(check.get("name"))
            for check in row.get("checks", [])
            if isinstance(check, dict) and not check.get("passed")
        ]
        if status != "verified" or failed_checks:
            section = str(row.get("section") or "unknown")
            suffix = status
            if failed_checks:
                suffix = f"{suffix}; failed={'+'.join(failed_checks)}"
            blockers.append(f"{section} ({suffix})")
    return blockers


def _slug_from_ref(ref: str) -> str:
    if not ref.startswith("public_writeup:"):
        return ""
    return ref.removeprefix("public_writeup:").split(":", 1)[0]
