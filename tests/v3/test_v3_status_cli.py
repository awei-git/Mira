import importlib.util
from pathlib import Path
from types import SimpleNamespace

from mira.web.dashboard import DashboardSnapshot


def _load_v3_status_module():
    module_path = Path(__file__).resolve().parents[2] / "agents" / "super" / "cli" / "v3_status.py"
    spec = importlib.util.spec_from_file_location("v3_status_cli_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_lines_expose_scores_watch_gates_and_blockers(tmp_path: Path):
    module = _load_v3_status_module()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    data_dir = tmp_path / "data" / "v3"
    reports_dir = data_dir / "artifacts" / "north_star_reports"
    reports_dir.mkdir(parents=True)
    closure_dir = data_dir / "artifacts" / "north_star_closure_packets" / "2026-05-21"
    closure_dir.mkdir(parents=True)
    handoff = docs_dir / "v31-north-star-remaining-gates-2026-05-21.md"
    handoff.write_text("# gates\n", encoding="utf-8")
    closure_manifest = closure_dir / "closure_manifest.json"
    closure_manifest.write_text("{}\n", encoding="utf-8")
    closure_checklist = closure_dir / "closure_checklist.md"
    closure_checklist.write_text("# closure\n", encoding="utf-8")
    older_report = reports_dir / "north-star-week-2026-05-14.md"
    older_report.write_text("# old\n", encoding="utf-8")
    latest_report = reports_dir / "north-star-week-2026-05-21.md"
    latest_report.write_text("# latest\n", encoding="utf-8")
    paths = SimpleNamespace(
        root=data_dir,
        kernel=data_dir / "kernel.json",
        ledger=data_dir / "ledger.jsonl",
        commits=data_dir / "commits.jsonl",
        effect_log=data_dir / "effects.jsonl",
        artifacts=data_dir / "artifacts",
    )
    snapshot = DashboardSnapshot(
        active_pipelines=["communication"],
        scars=[],
        active_hypotheses=[],
        skill_traces={},
        recent_experience_ids=[],
        hard_policy_count=43,
        soft_policy_count=9,
        review_queues={
            "briefing_feedback": [
                {
                    "item_id": "briefing_item:weekly:1:abc123",
                    "feedback_packet_artifact": "/tmp/mira/data/v3/artifacts/briefing_feedback_packets/abc/briefing_feedback_packet.json",
                    "feedback_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py "
                        "--item-id briefing_item:weekly:1:abc123 --json"
                    ),
                    "record_feedback_from_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py "
                        "--packet /tmp/briefing_feedback_packet.json --button <button> --json"
                    ),
                },
                {"item_id": "briefing_item:weekly:2:def456"},
            ],
            "public_feedback_followup": [
                {
                    "slug": "v31_green_dot_is_not_evidence",
                    "feedback_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py "
                        "--slug v31_green_dot_is_not_evidence --published-url https://example.com/post --json"
                    ),
                    "record_feedback_from_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py "
                        "--packet /tmp/public_feedback_packet.json --feedback-source <source> --json"
                    ),
                }
            ],
            "customer_discovery_feedback": [
                {
                    "topic": "a2a_trust_manifest",
                    "missing_feedback_count": "1",
                    "feedback_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py "
                        "--topic a2a_trust_manifest --json"
                    ),
                    "record_feedback_from_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py "
                        "--packet /tmp/customer_discovery_packet.json --source <source> --insight <insight> --json"
                    ),
                }
            ],
            "effect_reconciliation": [
                {
                    "effect_id": "effectlog_123",
                    "inspection_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_effect_reconciliation.py "
                        "--effect-id effectlog_123 --json"
                    ),
                }
            ],
            "public_writeup_review": [
                {
                    "draft_artifact": "/tmp/a2a_public_writeup_draft.md",
                    "publish_ref_template": "public_writeup:a2a_manifest_note:url=<url>",
                    "publication_safety_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_public_writeup_safety.py "
                        "--draft-artifact /tmp/a2a_public_writeup_draft.md --json"
                    ),
                    "publication_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_writeup_packet.py "
                        "--slug a2a_manifest_note --draft-artifact /tmp/a2a_public_writeup_draft.md --json"
                    ),
                    "record_evidence_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py "
                        "--slug a2a_manifest_note --published-url <url> --json"
                    ),
                    "record_evidence_from_packet_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py "
                        "--packet /tmp/publication_packet.json --published-url <url> --feedback-source <source> --json"
                    ),
                }
            ],
            "provider_provisioning": [
                {
                    "scoped_provider": "tts",
                    "scoped_missing_env_count": "2",
                    "scoped_missing_env_vars": "MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN",
                    "env_template_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py "
                        "--write-env-template /tmp/provider_provisioning.template --json"
                    ),
                    "runbook_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py "
                        "--write-runbook /tmp/provider_provisioning.runbook.md --json"
                    ),
                    "scoped_env_template_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py "
                        "--write-env-template /tmp/provider_provisioning.tts.template --skip-resolvers "
                        "--require-adapter tts --json"
                    ),
                    "scoped_readiness_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py "
                        "--skip-resolvers --require-adapter tts --json"
                    ),
                    "scoped_dry_run_command_template": (
                        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py "
                        "--provider tts --dry-run --json"
                    ),
                }
            ],
        },
        effect_log_ids=[],
        causal_evidence_counts={"L3": 1, "L4": 1},
        approval_capacity={},
        operational_scorecard={"score": 0.9807},
        strategic_scorecard={
            "score": 0.85,
            "public_writeups": 1,
            "public_feedback_items": 2,
            "briefing_feedback_items": 1,
            "briefing_feedback_coverage_rate": 0.25,
            "watch_gates": [
                "external_feedback_below_standard:0/3",
                "provider_production_readiness_blocked",
            ],
        },
        implementation_status_matrix=[
            {
                "section": "Provider Production Readiness",
                "status": "blocked_external",
                "checks": [{"name": "provider_production_readiness", "passed": False}],
            }
        ],
    )

    text = "\n".join(module._status_lines(paths, snapshot))

    assert "Operational score: 0.9807" in text
    assert "Strategic score: 0.8500" in text
    assert "North star progress: writeups=1, external_feedback=2/3, briefing_feedback=1 (0.2500 coverage)" in text
    assert (
        "External feedback paths: public_writeup=v31_green_dot_is_not_evidence, "
        "publication_review=a2a_manifest_note, "
        "customer_discovery=a2a_trust_manifest (1 remaining)"
    ) in text
    assert (
        "Briefing feedback next: 2 queued; first=briefing_item:weekly:1:abc123; "
        "packet=/tmp/mira/data/v3/artifacts/briefing_feedback_packets/abc/briefing_feedback_packet.json"
    ) in text
    assert (
        "Provider first canary: tts " "(2 missing env vars: MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN)"
    ) in text
    assert "Review queues: 7" in text
    assert (
        "Review queue breakdown: briefing_feedback:2, customer_discovery_feedback:1, "
        "effect_reconciliation:1, provider_provisioning:1, public_feedback_followup:1, "
        "public_writeup_review:1"
    ) in text
    assert "Watch gates: external_feedback_below_standard:0/3, provider_production_readiness_blocked" in text
    assert (
        "Implementation blockers: Provider Production Readiness "
        "(blocked_external; failed=provider_production_readiness)"
    ) in text
    assert f"Remaining gates handoff: {handoff}" in text
    assert f"Latest closure packet manifest: {closure_manifest}" in text
    assert f"Latest closure packet checklist: {closure_checklist}" in text
    assert f"Latest weekly report: {latest_report}" in text
    assert f"Weekly report directory: {reports_dir}" in text

    actions = "\n".join(module._action_lines(snapshot))
    assert "Suggested Next Commands" in actions
    assert "Replace placeholder values such as `<url>`, `<source>`, `<insight>`, and `<button>`" in actions
    assert "v3_prepare_north_star_closure_packets.py --json" in actions
    assert "v3_public_writeup_safety.py --draft-artifact /tmp/a2a_public_writeup_draft.md --json" in actions
    assert "v3_prepare_public_writeup_packet.py --slug a2a_manifest_note" in actions
    assert "v3_record_public_evidence.py --packet /tmp/publication_packet.json" in actions
    assert "v3_prepare_public_feedback_packet.py --slug v31_green_dot_is_not_evidence" in actions
    assert "v3_record_public_feedback.py --packet /tmp/public_feedback_packet.json" in actions
    assert "v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest --json" in actions
    assert "v3_record_customer_discovery_feedback.py --packet /tmp/customer_discovery_packet.json" in actions
    assert "v3_prepare_briefing_feedback_packet.py --item-id briefing_item:weekly:1:abc123 --json" in actions
    assert "v3_prepare_briefing_feedback_packet.py --all --json" in actions
    assert "v3_record_briefing_feedback.py --packet /tmp/briefing_feedback_packet.json" in actions
    assert "v3_effect_reconciliation.py --effect-id effectlog_123 --json" in actions
    assert "--publish-manifest <path>" in actions
    assert "--provider-state-manifest <path>" in actions
    assert "v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.template --json" in actions
    assert "v3_provider_readiness.py --write-runbook /tmp/provider_provisioning.runbook.md --json" in actions
    assert "v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.tts.template" in actions
    assert "v3_provider_readiness.py --skip-resolvers --require-adapter tts --json" in actions
    assert "v3_provider_production_canary.py --provider tts --dry-run --json" in actions
