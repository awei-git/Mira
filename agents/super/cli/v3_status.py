"""Print V3 memory-first runtime status."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.configuration import default_v3_config
from mira.engine.effect_log import EffectLog
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.store import JsonKernelStore
from mira.runtime import V3Paths, default_causal_evidence_log, default_ledger, default_v3_paths, run_communication
from mira.web.dashboard import DashboardSnapshot
from mira.web.dashboard import build_dashboard_snapshot


def render_status(root: Path | str = ROOT, *, include_actions: bool = False) -> str:
    root_path = Path(root)
    paths = default_v3_paths(root_path)
    kernel = JsonKernelStore(paths.kernel).load()
    snapshot = build_dashboard_snapshot(
        kernel,
        default_ledger(root_path),
        MemoryCommitLog(paths.commits),
        EffectLog(paths.effect_log),
        causal_evidence_log=default_causal_evidence_log(root_path),
    )
    lines = _status_lines(paths, snapshot)
    if include_actions:
        lines.extend(_action_lines(snapshot))
    return "\n".join(lines)


def _status_lines(paths: V3Paths, snapshot: DashboardSnapshot) -> list[str]:
    operational_score = snapshot.operational_scorecard.get("score", 0.0)
    strategic_score = snapshot.strategic_scorecard.get("score", 0.0)
    north_star_progress = _north_star_progress(snapshot)
    external_feedback_paths = _external_feedback_paths(snapshot)
    briefing_feedback_next = _briefing_feedback_next(snapshot)
    provider_first_canary = _provider_first_canary(snapshot)
    watch_gates = snapshot.strategic_scorecard.get("watch_gates") or []
    implementation_blockers = _implementation_blockers(snapshot.implementation_status_matrix)
    queue_breakdown = _review_queue_breakdown(snapshot)
    workspace_root = _workspace_root(paths)
    today = date.today().isoformat()
    remaining_gates = _latest_matching_file(
        workspace_root / "docs",
        "v31-north-star-remaining-gates-*.md",
        workspace_root / "docs" / f"v31-north-star-remaining-gates-{today}.md",
    )
    weekly_report_dir = paths.artifacts / "north_star_reports"
    weekly_report = _latest_matching_file(
        weekly_report_dir,
        "north-star-week-*.md",
        weekly_report_dir / f"north-star-week-{today}.md",
    )
    closure_packet_dir = paths.artifacts / "north_star_closure_packets"
    closure_manifest = _latest_matching_file(
        closure_packet_dir,
        "*/closure_manifest.json",
        closure_packet_dir / today / "closure_manifest.json",
    )
    closure_checklist = _latest_matching_file(
        closure_packet_dir,
        "*/closure_checklist.md",
        closure_packet_dir / today / "closure_checklist.md",
    )
    lines = [
        "Mira V3 Status",
        "==============",
        "",
        f"Kernel: {paths.kernel}",
        f"Ledger: {paths.ledger}",
        f"Commits: {paths.commits}",
        f"Effect log: {paths.effect_log}",
        f"Pipelines: {len(snapshot.active_pipelines)}",
        f"Recent experiences: {len(snapshot.recent_experience_ids)}",
        f"Scars: {len(snapshot.scars)}",
        f"Active hypotheses: {len(snapshot.active_hypotheses)}",
        f"Skill traces: {len(snapshot.skill_traces)}",
        f"Policies: {snapshot.hard_policy_count} hard, {snapshot.soft_policy_count} soft",
        f"Review queues: {sum(len(v) for v in snapshot.review_queues.values())}",
        f"Review queue breakdown: {queue_breakdown if queue_breakdown else 'none'}",
        f"Causal evidence: {sum(snapshot.causal_evidence_counts.values())}",
        f"Operational score: {float(operational_score):.4f}",
        f"Strategic score: {float(strategic_score):.4f}",
        f"North star progress: {north_star_progress}",
        f"External feedback paths: {external_feedback_paths if external_feedback_paths else 'none'}",
        f"Briefing feedback next: {briefing_feedback_next if briefing_feedback_next else 'none'}",
        f"Provider first canary: {provider_first_canary if provider_first_canary else 'none'}",
        f"Watch gates: {', '.join(str(gate) for gate in watch_gates) if watch_gates else 'PASS'}",
        f"Implementation blockers: {', '.join(implementation_blockers) if implementation_blockers else 'none'}",
        f"Remaining gates handoff: {remaining_gates}",
        f"Latest closure packet manifest: {closure_manifest}",
        f"Latest closure packet checklist: {closure_checklist}",
        f"Latest weekly report: {weekly_report}",
        f"Weekly report directory: {weekly_report_dir}",
    ]
    return lines


def _north_star_progress(snapshot: DashboardSnapshot) -> str:
    strategic = snapshot.strategic_scorecard
    public_writeups = int(strategic.get("public_writeups") or 0)
    external_feedback = int(strategic.get("public_feedback_items") or 0)
    briefing_feedback = int(strategic.get("briefing_feedback_items") or 0)
    briefing_coverage = float(strategic.get("briefing_feedback_coverage_rate") or 0.0)
    return (
        f"writeups={public_writeups}, "
        f"external_feedback={external_feedback}/3, "
        f"briefing_feedback={briefing_feedback} ({briefing_coverage:.4f} coverage)"
    )


def _external_feedback_paths(snapshot: DashboardSnapshot) -> str:
    parts: list[str] = []
    public_items = list(snapshot.review_queues.get("public_feedback_followup") or [])
    if public_items:
        slug = str(public_items[0].get("slug") or "").strip()
        if slug:
            parts.append(f"public_writeup={slug}")
    public_review_items = list(snapshot.review_queues.get("public_writeup_review") or [])
    if public_review_items:
        review_slug = _public_writeup_review_slug(public_review_items[0])
        if review_slug:
            parts.append(f"publication_review={review_slug}")
    customer_items = list(snapshot.review_queues.get("customer_discovery_feedback") or [])
    if customer_items:
        topic = str(customer_items[0].get("topic") or "").strip()
        missing = str(customer_items[0].get("missing_feedback_count") or "").strip()
        if topic and missing:
            parts.append(f"customer_discovery={topic} ({missing} remaining)")
        elif topic:
            parts.append(f"customer_discovery={topic}")
    return ", ".join(parts)


def _public_writeup_review_slug(item: dict[str, str]) -> str:
    publish_ref = str(item.get("publish_ref_template") or "").strip()
    if publish_ref.startswith("public_writeup:"):
        return publish_ref.removeprefix("public_writeup:").split(":", 1)[0]
    plan_ref = str(item.get("plan_ref") or "").strip()
    if plan_ref.startswith("public_writeup_plan:"):
        return plan_ref.removeprefix("public_writeup_plan:").split(":", 1)[0]
    return str(item.get("slug") or "").strip()


def _briefing_feedback_next(snapshot: DashboardSnapshot) -> str:
    items = list(snapshot.review_queues.get("briefing_feedback") or [])
    if not items:
        return ""
    first = items[0]
    item_id = str(first.get("item_id") or "").strip()
    packet = str(first.get("feedback_packet_artifact") or "").strip()
    prefix = f"{len(items)} queued"
    if item_id and packet:
        return f"{prefix}; first={item_id}; packet={packet}"
    if item_id:
        return f"{prefix}; first={item_id}"
    return prefix


def _provider_first_canary(snapshot: DashboardSnapshot) -> str:
    provider_items = list(snapshot.review_queues.get("provider_provisioning") or [])
    if not provider_items:
        return ""
    item = provider_items[0]
    scoped_provider = str(item.get("scoped_provider") or "selected provider")
    missing_count = str(item.get("scoped_missing_env_count") or "0")
    missing_vars = str(item.get("scoped_missing_env_vars") or "").strip()
    if missing_vars:
        return f"{scoped_provider} ({missing_count} missing env vars: {missing_vars})"
    return f"{scoped_provider} ({missing_count} missing env vars)"


def _action_lines(snapshot: DashboardSnapshot) -> list[str]:
    actions: list[str] = [
        "- Prepare all north-star closure packets: `PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_north_star_closure_packets.py --json`"
    ]
    public_writeup_items = list(snapshot.review_queues.get("public_writeup_review") or [])
    if public_writeup_items:
        public_writeup = public_writeup_items[0]
        for label, key, fallback_key in (
            (
                "Public writeup safety check",
                "publication_safety_command_template",
                "publication_safety_command_template",
            ),
            (
                "Public writeup publication packet",
                "publication_packet_command_template",
                "publication_packet_command_template",
            ),
            (
                "Public writeup record evidence from packet",
                "record_evidence_from_packet_command_template",
                "record_evidence_command_template",
            ),
        ):
            command = str(public_writeup.get(key) or public_writeup.get(fallback_key) or "").strip()
            if command:
                actions.append(f"- {label}: `{command}`")
    _append_first_command(
        actions,
        "Public feedback packet",
        snapshot.review_queues.get("public_feedback_followup") or [],
        "feedback_packet_command_template",
        "feedback_packet_command_template",
    )
    _append_first_command(
        actions,
        "Public feedback record from packet",
        snapshot.review_queues.get("public_feedback_followup") or [],
        "record_feedback_from_packet_command_template",
        "record_feedback_command_template",
    )
    _append_first_command(
        actions,
        "Customer discovery packet",
        snapshot.review_queues.get("customer_discovery_feedback") or [],
        "feedback_packet_command_template",
        "feedback_packet_command_template",
    )
    _append_first_command(
        actions,
        "Customer discovery record from packet",
        snapshot.review_queues.get("customer_discovery_feedback") or [],
        "record_feedback_from_packet_command_template",
        "record_feedback_command_template",
    )
    briefing_items = list(snapshot.review_queues.get("briefing_feedback") or [])
    _append_first_command(
        actions,
        "Briefing feedback packet",
        briefing_items,
        "feedback_packet_command_template",
        "feedback_packet_command_template",
    )
    if len(briefing_items) > 1:
        actions.append(
            "- Briefing feedback all packets: `PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --all --json`"
        )
    _append_first_command(
        actions,
        "Briefing feedback record from packet",
        briefing_items,
        "record_feedback_from_packet_command_template",
        "record_feedback_command_template",
    )
    _append_first_command(
        actions,
        "Effect reconciliation inspect",
        snapshot.review_queues.get("effect_reconciliation") or [],
        "inspection_command_template",
        "inspection_command_template",
    )
    if snapshot.review_queues.get("effect_reconciliation"):
        actions.append(
            "- Effect reconciliation external evidence: add `--publish-manifest <path>`, `--rss-feed <path>`, or `--provider-state-manifest <path>` to the inspect command when provider evidence lives outside `data/v3/provider_state`."
        )
    provider_items = list(snapshot.review_queues.get("provider_provisioning") or [])
    if provider_items:
        provider = provider_items[0]
        for label, key in (
            ("Provider full env template", "env_template_command_template"),
            ("Provider provisioning runbook", "runbook_command_template"),
            ("Provider scoped env template", "scoped_env_template_command_template"),
            ("Provider scoped readiness", "scoped_readiness_command_template"),
            ("Provider scoped canary dry-run", "scoped_dry_run_command_template"),
        ):
            command = str(provider.get(key) or "").strip()
            if command:
                actions.append(f"- {label}: `{command}`")
    if not actions:
        return ["", "Suggested Next Commands", "=======================", "", "- No queued action commands."]
    return [
        "",
        "Suggested Next Commands",
        "=======================",
        "",
        "Replace placeholder values such as `<url>`, `<source>`, `<insight>`, and `<button>` before running these commands.",
        "",
        *actions,
    ]


def _append_first_command(
    actions: list[str],
    label: str,
    items: list[dict[str, str]],
    preferred_key: str,
    fallback_key: str,
) -> None:
    if not items:
        return
    item = items[0]
    command = str(item.get(preferred_key) or item.get(fallback_key) or "").strip()
    if command:
        actions.append(f"- {label}: `{command}`")


def _latest_matching_file(directory: Path, pattern: str, fallback: Path) -> Path:
    try:
        matches = sorted(path for path in directory.glob(pattern) if path.is_file())
    except OSError:
        return fallback
    return matches[-1] if matches else fallback


def _workspace_root(paths: V3Paths) -> Path:
    data_root = Path(paths.root)
    if data_root.name == "v3" and data_root.parent.name == "data":
        return data_root.parents[1]
    return data_root


def _review_queue_breakdown(snapshot: DashboardSnapshot) -> str:
    return ", ".join(f"{name}:{len(items)}" for name, items in sorted(snapshot.review_queues.items()) if items)


def _implementation_blockers(rows: list[dict[str, object]]) -> list[str]:
    blockers: list[str] = []
    for row in rows:
        section = str(row.get("section") or "unknown")
        status = str(row.get("status") or "unknown")
        failed_checks = [
            str(check.get("name"))
            for check in row.get("checks", [])
            if isinstance(check, dict) and not check.get("passed")
        ]
        if status != "verified" or failed_checks:
            suffix = status
            if failed_checks:
                suffix = f"{suffix}; failed={'+'.join(failed_checks)}"
            blockers.append(f"{section} ({suffix})")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description="Show Mira V3 status.")
    parser.add_argument("--json", action="store_true", help="Print dashboard/config JSON.")
    parser.add_argument(
        "--actions", action="store_true", help="Append first safe action commands from live review queues."
    )
    parser.add_argument("--run-communication", metavar="MESSAGE", help="Run the migrated communication pipeline.")
    args = parser.parse_args()

    if args.run_communication:
        print(run_communication(args.run_communication, root=ROOT))
        return 0
    if args.json:
        paths = default_v3_paths(ROOT)
        kernel = JsonKernelStore(paths.kernel).load()
        dashboard = build_dashboard_snapshot(
            kernel,
            default_ledger(ROOT),
            MemoryCommitLog(paths.commits),
            EffectLog(paths.effect_log),
            causal_evidence_log=default_causal_evidence_log(ROOT),
        )
        print(json.dumps({"dashboard": dashboard.__dict__, "config": default_v3_config().to_dict()}, indent=2))
        return 0
    print(render_status(include_actions=args.actions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
