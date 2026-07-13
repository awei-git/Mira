import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

from mira.remaining_gates import render_remaining_gates
from mira.web.dashboard import DashboardSnapshot


def _snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        active_pipelines=[],
        scars=[],
        active_hypotheses=[],
        skill_traces={},
        recent_experience_ids=[],
        hard_policy_count=0,
        soft_policy_count=0,
        review_queues={
            "public_feedback_followup": [
                {
                    "slug": "v31_green_dot_is_not_evidence",
                    "published_url": "https://example.com/post",
                    "comments": "0",
                    "likes": "1",
                    "restacks": "0",
                    "views": "12",
                    "feedback_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py --slug v31_green_dot_is_not_evidence --published-url https://example.com/post --json",
                    "record_feedback_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py --slug v31_green_dot_is_not_evidence --feedback-source <source> --published-url https://example.com/post --json",
                    "record_feedback_from_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py --packet /repo/data/v3/artifacts/public_feedback_packets/v31_green_dot_is_not_evidence/packet/feedback_packet.json --feedback-source <source> --json",
                }
            ],
            "public_writeup_review": [
                {
                    "title": "A2A Trust Manifests Need Receipts, Not Vibes",
                    "draft_artifact": "data/v3/artifacts/a2a.md",
                    "preview_hash": "abc123",
                    "publish_ref_template": "public_writeup:a2a_manifest_note:url=<url>",
                    "publication_safety_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_public_writeup_safety.py --draft-artifact data/v3/artifacts/a2a.md --json",
                    "publication_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_writeup_packet.py --slug a2a_manifest_note --draft-artifact data/v3/artifacts/a2a.md --expected-preview-hash abc123 --json",
                    "record_evidence_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py --slug a2a_manifest_note --published-url <url> --draft-artifact data/v3/artifacts/a2a.md --expected-preview-hash abc123 --feedback-source <source> --json",
                    "record_evidence_from_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py --packet /repo/data/v3/artifacts/publication_packets/a2a_manifest_note/abc123/publication_packet.json --published-url <url> --feedback-source <source> --json",
                }
            ],
            "customer_discovery_feedback": [
                {
                    "topic": "a2a_trust_manifest",
                    "missing_feedback_count": "3",
                    "feedback_packet_artifact": "/repo/data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json",
                    "feedback_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest --json",
                    "record_feedback_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --source <source> --insight <insight> --json",
                    "record_feedback_from_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --packet /repo/data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json --source <source> --insight <insight> --json",
                }
            ],
            "briefing_feedback": [
                {
                    "item_id": "briefing_item:weekly:1:abc123",
                    "topics": "a2a",
                    "matched_interests": "interest:a2a",
                    "available_buttons": "useful, too_obvious",
                    "feedback_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --item-id briefing_item:weekly:1:abc123 --json",
                    "record_feedback_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py --item-id briefing_item:weekly:1:abc123 --button <button> --json",
                    "record_feedback_from_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py --packet /repo/data/v3/artifacts/briefing_feedback_packets/packet/briefing_feedback_packet.json --button <button> --json",
                }
            ],
            "provider_provisioning": [
                {
                    "status": "blocked_external",
                    "readiness_finding_count": "28",
                    "missing_env_count": "28",
                    "scoped_provider": "tts",
                    "scoped_missing_env_vars": "MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN",
                    "scoped_env_template_artifact": "data/v3/provider_provisioning.tts.template",
                    "env_template_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --write-env-template data/v3/provider_provisioning.template --json",
                    "runbook_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --write-runbook data/v3/provider_provisioning.runbook.md --json",
                    "scoped_env_template_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --write-env-template data/v3/provider_provisioning.tts.template --skip-resolvers --require-adapter tts --json",
                    "scoped_readiness_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --skip-resolvers --require-adapter tts --json",
                    "scoped_dry_run_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --provider tts --dry-run --json",
                    "scoped_canary_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --provider tts --json",
                }
            ],
            "effect_reconciliation": [
                {
                    "effect_id": "effectlog_123",
                    "pipeline": "article_creation",
                    "action": "publish_substack",
                    "target": "article-1",
                    "status": "planned",
                    "idempotency_key": "publish:1",
                    "preview_hash": "preview-sha256",
                    "approval_token_id": "grant_1",
                    "replay_bundle_ref": "replay:publish:1",
                    "external_ref": "",
                    "reconciliation_ref": "",
                    "inspection_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_effect_reconciliation.py --effect-id effectlog_123 --json",
                }
            ],
        },
        effect_log_ids=[],
        causal_evidence_counts={},
        approval_capacity={},
        operational_scorecard={"score": 0.99},
        strategic_scorecard={
            "score": 0.85,
            "public_writeups": 1,
            "public_feedback_items": 0,
            "briefing_feedback_items": 0,
            "briefing_feedback_coverage_rate": 0.0,
            "watch_gates": [
                "external_feedback_below_standard:0/3",
                "briefing_feedback_missing",
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


def _load_cli_module():
    module_path = Path(__file__).resolve().parents[2] / "agents" / "super" / "cli" / "v3_remaining_gates.py"
    spec = importlib.util.spec_from_file_location("v3_remaining_gates_cli_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_remaining_gates_renderer_uses_live_dashboard_queue_commands():
    text = render_remaining_gates(_snapshot(), root=Path("/repo"), report_date=date(2026, 5, 21))

    assert "Date: 2026-05-21" in text
    assert (
        "v3_remaining_gates.py --date 2026-05-21 --output /repo/docs/v31-north-star-remaining-gates-2026-05-21.md"
        in text
    )
    assert "v3_prepare_north_star_closure_packets.py --json" in text
    assert "external_feedback_below_standard:0/3" in text
    assert (
        "v3_record_public_evidence.py --packet /repo/data/v3/artifacts/publication_packets/a2a_manifest_note/abc123/publication_packet.json"
        in text
    )
    assert "v3_prepare_public_feedback_packet.py --slug v31_green_dot_is_not_evidence" in text
    assert "v3_prepare_public_feedback_packet.py --all --json" in text
    assert (
        "v3_record_public_feedback.py --packet /repo/data/v3/artifacts/public_feedback_packets/v31_green_dot_is_not_evidence/packet/feedback_packet.json"
        in text
    )
    assert "v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest" in text
    assert "v3_record_customer_discovery_feedback.py --source <source> --insight <insight>" in text
    assert (
        "v3_record_customer_discovery_feedback.py --packet /repo/data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json"
        in text
    )
    assert "v3_prepare_briefing_feedback_packet.py --item-id briefing_item:weekly:1:abc123" in text
    assert "v3_prepare_briefing_feedback_packet.py --all --json" in text
    assert "v3_record_briefing_feedback.py --item-id briefing_item:weekly:1:abc123" in text
    assert (
        "v3_record_briefing_feedback.py --packet /repo/data/v3/artifacts/briefing_feedback_packets/packet/briefing_feedback_packet.json"
        in text
    )
    assert "smallest current canary scope: `tts`" in text
    assert "MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN" in text
    assert "v3_provider_production_canary.py --provider tts --dry-run --json" in text
    assert "v3_provider_production_canary.py --provider tts --json" in text
    assert "Open Operator Review: Effect Reconciliation" in text
    assert "effect id: `effectlog_123`" in text
    assert "v3_effect_reconciliation.py --effect-id effectlog_123 --json" in text
    assert "--publish-manifest <path>" in text
    assert "--provider-state-manifest <path>" in text
    assert "Do not retry or mark the effect complete from local intent alone" in text
    assert "v3_status.py --actions" in text
    assert "v3_status.py --json" in text
    assert "must not be used to invent feedback" in text


def test_remaining_gates_cli_help_exposes_output_flags():
    _load_cli_module()
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "agents/super/cli/v3_remaining_gates.py", "--help"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--output" in result.stdout
    assert "--date" in result.stdout
    assert "--json" in result.stdout
