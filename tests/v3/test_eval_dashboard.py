from pathlib import Path

from mira.evals import EvalEvent, EvalHistory, bounded_threshold_adjustment
from mira.kernel import CausalEvidence, CausalEvidenceLog, ExperienceLedger, Hypothesis, MemoryKernel
from mira.kernel.delta import MemoryAction, MemoryDelta
from mira.kernel.ledger import ExperienceRecord
from mira.runtime import (
    default_v3_paths,
    write_provider_adapter_config_template,
    write_provider_resolver_config_template,
)
from mira.web.dashboard import build_dashboard_snapshot


def test_eval_history_and_bounded_adjustment(tmp_path: Path):
    history = EvalHistory(tmp_path / "eval.jsonl")
    history.append(EvalEvent(pipeline="article", score=0.9, passed=True, outcome_id="exp_1"))

    assert history.list("article")[0].score == 0.9
    assert bounded_threshold_adjustment(0.7, 0.9) == 0.75
    assert bounded_threshold_adjustment(0.7, 0.62) == 0.65


def test_dashboard_snapshot_exposes_monitor_counts(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.skill_trace("article_writing").record_use(True, "good")
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    delta = MemoryDelta(
        pipeline="article_creation",
        run_id="exp_1",
        memory_class="creative",
        what_happened="drafted",
        what_mattered="voice",
        what_changed="snapshot includes this",
        actions=[MemoryAction("update_skill_trace", "skill:article_writing", "good")],
    )
    ledger.append(
        ExperienceRecord(
            id="exp_1",
            pipeline="article_creation",
            trigger="manual",
            intent="write",
            outcome="done",
            delta=delta,
            causal_links=[],
            confidence=0.9,
            memory_class="creative",
        )
    )

    causal_evidence = CausalEvidenceLog(tmp_path / "causal.jsonl")
    causal_evidence.append(CausalEvidence("memory:1", "L3", "changed behavior"))
    causal_evidence.append(CausalEvidence("memory:2", "L4", "ablation confirmed"))

    snapshot = build_dashboard_snapshot(kernel, ledger, causal_evidence_log=causal_evidence)

    assert "communication" in snapshot.active_pipelines
    assert snapshot.skill_traces["article_writing"] == 1.0
    assert snapshot.recent_experience_ids == ["exp_1"]
    assert snapshot.hard_policy_count == 43
    assert snapshot.soft_policy_count == 9
    assert snapshot.causal_evidence_counts["L3"] == 1
    assert snapshot.causal_evidence_counts["L4"] == 1
    assert "causal_memory" in snapshot.operational_scorecard
    assert "metrics" in snapshot.operational_scorecard
    assert any(metric["name"] == "causal_memory" for metric in snapshot.operational_scorecard["metrics"])
    assert snapshot.operational_scorecard["causal_link_validity"] == 1.0
    assert snapshot.operational_scorecard["l4_required_causal_evidence"] == 1.0
    assert snapshot.operational_scorecard["critical_memory_pollution"] == 0
    assert snapshot.operational_scorecard["unapproved_high_risk_action"] == 0
    assert snapshot.operational_scorecard["unreplayable_action"] == 0
    assert snapshot.operational_scorecard["invalid_replay_bundle"] == 0
    assert snapshot.operational_scorecard["orphan_important_action"] == 0
    assert snapshot.operational_scorecard["eval_record_count"] == 1
    assert snapshot.operational_scorecard["outcome_record_count"] == 1
    assert snapshot.operational_scorecard["decision_record_count"] == 0
    assert snapshot.operational_scorecard["behavioral_effect_count"] == 0
    assert snapshot.operational_scorecard["approval_event_count"] == 0
    assert snapshot.operational_scorecard["run_evidence_bundle_count"] == 1
    assert "commercial_options" in snapshot.strategic_scorecard
    assert "public_writeups" in snapshot.strategic_scorecard
    assert "product_thesis_updates" in snapshot.strategic_scorecard
    assert "watch_gates" in snapshot.strategic_scorecard
    assert snapshot.strategic_scorecard["watch_gate_count"] >= 1
    assert "external_feedback_below_standard:0/3" in snapshot.strategic_scorecard["watch_gates"]
    sections = {row["section"]: row for row in snapshot.implementation_status_matrix}
    assert sections["Ledger / ExperienceRecord"]["status"] == "verified"
    assert sections["Ledger / ExperienceRecord"]["plan_ref"] == "§7 / §21 / §24 Week 1"
    assert sections["Ledger / ExperienceRecord"]["evidence_missing"] == []
    assert sections["Ledger / ExperienceRecord"]["tests_missing"] == []
    assert sections["Ledger / ExperienceRecord"]["checks"][0]["name"] == "experience_ledger_records"
    assert sections["Ledger / ExperienceRecord"]["checks"][0]["passed"] is True
    assert "records=" in sections["Ledger / ExperienceRecord"]["checks"][0]["detail"]
    assert sections["Memory Gateway"]["status"] == "verified"
    assert sections["Memory Gateway"]["checks"][0]["name"] == "memory_gateway_contract"
    assert sections["Memory Gateway"]["checks"][0]["passed"] is True
    assert "redteam=" in sections["Memory Gateway"]["checks"][0]["detail"]
    assert "high_risk_applied=0" in sections["Memory Gateway"]["checks"][0]["detail"]
    assert sections["Capability Preflight"]["status"] == "verified"
    assert sections["Capability Preflight"]["checks"][0]["name"] == "capability_preflight_contract"
    assert sections["Capability Preflight"]["checks"][0]["passed"] is True
    assert "requirements=" in sections["Capability Preflight"]["checks"][0]["detail"]
    assert sections["Causal Trace"]["status"] == "verified"
    assert sections["Causal Trace"]["checks"][0]["name"] == "causal_trace_contract"
    assert sections["Causal Trace"]["checks"][0]["passed"] is True
    assert "live_traces=" in sections["Causal Trace"]["checks"][0]["detail"]
    assert "l4_without_ablation=0" in sections["Causal Trace"]["checks"][0]["detail"]
    assert sections["Snapshot Builder"]["status"] == "verified"
    assert sections["Snapshot Builder"]["checks"][0]["name"] == "snapshot_builder_contract"
    assert sections["Snapshot Builder"]["checks"][0]["passed"] is True
    assert "total_tokens=" in sections["Snapshot Builder"]["checks"][0]["detail"]
    assert sections["Effect Log"]["checks"][0]["name"] == "effect_log_integrity"
    assert sections["Effect Log"]["checks"][0]["passed"] is True
    assert "entries=" in sections["Effect Log"]["checks"][0]["detail"]
    assert sections["Provider Effect Adapters"]["status"] == "verified"
    assert sections["Provider Effect Adapters"]["checks"][0]["name"] == "provider_effect_adapter_contract"
    assert sections["Provider Effect Adapters"]["checks"][0]["passed"] is True
    assert "exercised=9" in sections["Provider Effect Adapters"]["checks"][0]["detail"]
    assert "deployment_exercised=3" in sections["Provider Effect Adapters"]["checks"][0]["detail"]
    assert "blocked_without_approval=1" in sections["Provider Effect Adapters"]["checks"][0]["detail"]
    assert "succeeded_missing_approval=0" in sections["Provider Effect Adapters"]["checks"][0]["detail"]
    assert sections["Approval Queue"]["status"] == "verified"
    assert sections["Approval Queue"]["checks"][0]["name"] == "approval_queue_contract"
    assert sections["Approval Queue"]["checks"][0]["passed"] is True
    assert "unsafe_live_grants=0" in sections["Approval Queue"]["checks"][0]["detail"]
    assert sections["Web Review Queues"]["status"] == "verified"
    assert sections["Web Review Queues"]["checks"][0]["name"] == "web_review_queue_contract"
    assert sections["Web Review Queues"]["checks"][0]["passed"] is True
    assert "approval_digest:1" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "public_writeup_review:1" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "public_feedback_followup:1" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "customer_discovery_feedback:1" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "provider_provisioning:1" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "context_complete=11/11" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "queue_specific_checks=13/13" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert "findings=0" in sections["Web Review Queues"]["checks"][0]["detail"]
    assert sections["Legacy Runtime Bridge"]["status"] == "verified"
    assert sections["Legacy Runtime Bridge"]["checks"][0]["name"] == "legacy_runtime_bridge_contract"
    assert sections["Legacy Runtime Bridge"]["checks"][0]["passed"] is True
    assert "gate_records=2" in sections["Legacy Runtime Bridge"]["checks"][0]["detail"]
    assert "post_hook_refs=3/3" in sections["Legacy Runtime Bridge"]["checks"][0]["detail"]
    assert "agents/super/cli/v3_workflow_security_audit.py" in sections["Workflow Packs"]["evidence"]
    assert sections["Workflow Packs"]["checks"][0]["name"] == "workflow_tree_security_audit"
    assert sections["Workflow Packs"]["checks"][0]["passed"] is True
    assert "0 findings" in sections["Workflow Packs"]["checks"][0]["detail"]
    assert "candidate_gate_blocked=1" in sections["Workflow Packs"]["checks"][0]["detail"]
    workflow_checks = {check["name"]: check for check in sections["Workflow Packs"]["checks"]}
    assert workflow_checks["workflow_pack_registry_coverage"]["passed"] is True
    assert "registered=20/20" in workflow_checks["workflow_pack_registry_coverage"]["detail"]
    assert "compiled=20" in workflow_checks["workflow_pack_registry_coverage"]["detail"]
    assert "native=communication" in workflow_checks["workflow_pack_registry_coverage"]["detail"]
    assert sections["Baselines"]["status"] == "verified"
    assert sections["Baselines"]["checks"][0]["name"] == "baseline_artifact_set"
    assert sections["Baselines"]["checks"][0]["passed"] is True
    assert "latest=" in sections["Baselines"]["checks"][0]["detail"]
    assert sections["Provider Production Readiness"]["status"] == "blocked_external"
    provider_checks = {check["name"]: check for check in sections["Provider Production Readiness"]["checks"]}
    assert provider_checks["provider_production_canary_surface"]["passed"] is True
    assert "canary_supported=6/6" in provider_checks["provider_production_canary_surface"]["detail"]
    assert "adapter_only=tts" in provider_checks["provider_production_canary_surface"]["detail"]
    assert provider_checks["provider_production_readiness"]["passed"] is False
    assert "readiness findings" in provider_checks["provider_production_readiness"]["detail"]
    assert sections["Provider Production Readiness"]["status_detail"]
    assert sections["North Star Evals"]["evidence"]
    assert sections["North Star Evals"]["tests"]
    assert sections["North Star Evals"]["checks"][0]["name"] == "north_star_eval_gate"
    assert sections["North Star Evals"]["checks"][0]["passed"] is True
    assert "hard_gates=PASS" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "watch_gates=" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "external_feedback_below_standard:" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "provider_production_readiness_blocked" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "strategic_counts=" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "external_feedback:" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "threshold_policy=PASS" in sections["North Star Evals"]["checks"][0]["detail"]
    assert "feedback_integrity=PASS" in sections["North Star Evals"]["checks"][0]["detail"]
    assert len(sections) >= 13


def test_dashboard_experiment_queue_exposes_hypothesis_evidence_counts(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.pending_hypotheses.append(
        Hypothesis(
            hypothesis_id="hypothesis:self_evolution_pack_coverage",
            claim="Executable V3.1 self-evolution packs reduce drift.",
            test_pipeline="self_evolution",
            evidence_for=["Canary plan recorded"],
            baseline_window="prior week",
            test_window="current canary",
            min_n=3,
            current_metric="2/3 canary runs",
            rollback_plan="rollback on hard gate failure",
        )
    )

    snapshot = build_dashboard_snapshot(kernel, ExperienceLedger(tmp_path / "ledger.jsonl"))
    item = snapshot.review_queues["experiment"][0]

    assert item["hypothesis_id"] == "hypothesis:self_evolution_pack_coverage"
    assert item["baseline_window"] == "prior week"
    assert item["test_window"] == "current canary"
    assert item["min_n"] == "3"
    assert item["current_metric"] == "2/3 canary runs"
    assert item["rollback_plan"] == "rollback on hard gate failure"
    assert item["evidence_for"] == "1"
    assert item["evidence_against"] == "0"
    assert item["latest_evidence"] == "Canary plan recorded"
    assert item["why_now"]
    assert item["what_will_change"]
    assert item["evidence"] == "Canary plan recorded"
    assert item["what_can_go_wrong"]
    assert item["rollback"]


def test_dashboard_experiment_queue_backfills_legacy_hypothesis_controls(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.pending_hypotheses.append(
        Hypothesis(
            hypothesis_id="hypothesis:legacy",
            claim="Legacy hypotheses still need review controls.",
            test_pipeline="self_evolution",
        )
    )

    snapshot = build_dashboard_snapshot(kernel, ExperienceLedger(tmp_path / "ledger.jsonl"))
    item = snapshot.review_queues["experiment"][0]

    assert item["baseline_window"] == "prior self_evolution evidence window"
    assert item["test_window"] == "current active experiment window"
    assert item["min_n"] == "1"
    assert item["current_metric"] == "evidence_for=0 evidence_against=0"
    assert item["rollback_plan"] == item["rollback"]


def test_dashboard_exposes_briefing_feedback_blind_sample_queue(tmp_path: Path):
    kernel = MemoryKernel()
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Durable workflow audit signal (local:workflow)\n"
        "- [verified] Agent memory security pattern (local:memory)\n",
        encoding="utf-8",
    )
    delta = MemoryDelta(
        pipeline="intelligence_briefing",
        run_id="briefing_1",
        memory_class="epistemic",
        what_happened="briefed",
        what_mattered="interest fit",
        what_changed="blind sample ready",
        actions=[MemoryAction("update_skill_trace", "skill:intelligence_briefing", "ok")],
    )
    ledger.append(
        ExperienceRecord(
            id="briefing_1",
            pipeline="intelligence_briefing",
            trigger="scheduled",
            intent="brief",
            outcome="completed",
            delta=delta,
            causal_links=[],
            confidence=0.9,
            memory_class="epistemic",
            artifacts=[str(briefing_path)],
        )
    )

    snapshot = build_dashboard_snapshot(kernel, ledger)
    queue = snapshot.review_queues["briefing_feedback"]

    assert len(queue) == 2
    assert queue[0]["item_id"].startswith("briefing_item:briefing_1:")
    assert queue[0]["item_text"].startswith("- [reported] Durable workflow audit signal")
    assert "useful" in queue[0]["available_buttons"]
    assert "too_obvious" in queue[0]["available_buttons"]
    assert "follow_up" in queue[0]["available_buttons"]
    assert queue[0]["feedback_ref_template"] == f"briefing_feedback:item={queue[0]['item_id']}:button=<button>"
    assert queue[0]["feedback_packet_artifact"].endswith("/briefing_feedback_packet.json")
    assert "v3_prepare_briefing_feedback_packet.py" in queue[0]["feedback_packet_command_template"]
    assert f"--item-id {queue[0]['item_id']}" in queue[0]["feedback_packet_command_template"]
    assert "v3_record_briefing_feedback.py" in queue[0]["record_feedback_command_template"]
    assert f"--item-id {queue[0]['item_id']}" in queue[0]["record_feedback_command_template"]
    assert "--button <button>" in queue[0]["record_feedback_command_template"]
    assert "v3_record_briefing_feedback.py" in queue[0]["record_feedback_from_packet_command_template"]
    assert (
        f"--packet {queue[0]['feedback_packet_artifact']}" in queue[0]["record_feedback_from_packet_command_template"]
    )
    assert "--button <button>" in queue[0]["record_feedback_from_packet_command_template"]
    assert queue[0]["why_now"]
    assert queue[0]["what_will_change"]
    assert queue[0]["what_can_go_wrong"]
    assert queue[0]["rollback"]
    assert "briefing_feedback_missing" in snapshot.strategic_scorecard["watch_gates"]
    assert snapshot.strategic_scorecard["briefing_feedback_items"] == 0
    assert snapshot.strategic_scorecard["briefing_feedback_coverage_rate"] == 0.0


def test_dashboard_exposes_public_writeup_review_queue_without_counting_publication(tmp_path: Path):
    kernel = MemoryKernel()
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    draft_path = tmp_path / "a2a_public_writeup_draft.md"
    draft_path.write_text(
        "# A2A Trust Manifests Need Receipts\n\n"
        "Status: draft for public review\n\n"
        "A concrete critique draft awaits external publication approval.\n",
        encoding="utf-8",
    )
    delta = MemoryDelta(
        pipeline="a2a_trust_experiment",
        run_id="a2a_writeup_1",
        memory_class="epistemic",
        what_happened="drafted public critique",
        what_mattered="public review path should be visible",
        what_changed="human can approve, edit, or leave unpublished",
        actions=[MemoryAction("create_artifact", str(draft_path), "draft")],
    )
    ledger.append(
        ExperienceRecord(
            id="a2a_writeup_1",
            pipeline="a2a_trust_experiment",
            trigger="manual",
            intent="prepare public critique",
            outcome="completed",
            delta=delta,
            causal_links=[],
            confidence=0.9,
            memory_class="epistemic",
            artifacts=[str(draft_path)],
            eval_refs=["public_writeup_plan:a2a_manifest_note"],
        )
    )

    snapshot = build_dashboard_snapshot(kernel, ledger)
    queue = snapshot.review_queues["public_writeup_review"]

    assert len(queue) == 1
    assert queue[0]["run_id"] == "a2a_writeup_1"
    assert queue[0]["plan_ref"] == "public_writeup_plan:a2a_manifest_note"
    assert queue[0]["title"] == "A2A Trust Manifests Need Receipts"
    assert queue[0]["draft_artifact"] == str(draft_path)
    assert queue[0]["preview_hash"]
    assert queue[0]["publication_safety"] == "pass"
    assert queue[0]["publication_safety_findings"] == ""
    assert "v3_public_writeup_safety.py" in queue[0]["publication_safety_command_template"]
    assert "v3_prepare_public_writeup_packet.py" in queue[0]["publication_packet_command_template"]
    assert queue[0]["preview_hash"] in queue[0]["publication_packet_command_template"]
    assert queue[0]["publication_packet_artifact"].endswith(
        f"/data/v3/artifacts/publication_packets/a2a_manifest_note/{queue[0]['preview_hash'][:12]}/publication_packet.json"
    )
    assert queue[0]["decision"] == "needs_publication_review"
    assert queue[0]["publish_ref_template"] == "public_writeup:a2a_manifest_note:url=<url>"
    assert queue[0]["feedback_ref_template"] == "external_feedback:a2a_manifest_note:source=<source>"
    assert "v3_record_public_evidence.py" in queue[0]["record_evidence_command_template"]
    assert "v3_record_public_evidence.py" in queue[0]["record_evidence_from_packet_command_template"]
    assert (
        f"--packet {queue[0]['publication_packet_artifact']}"
        in queue[0]["record_evidence_from_packet_command_template"]
    )
    assert "--draft-artifact" in queue[0]["record_evidence_command_template"]
    assert str(draft_path) in queue[0]["record_evidence_command_template"]
    assert "--expected-preview-hash" in queue[0]["record_evidence_command_template"]
    assert queue[0]["preview_hash"] in queue[0]["record_evidence_command_template"]
    assert queue[0]["why_now"]
    assert queue[0]["what_will_change"]
    assert queue[0]["evidence"] == str(draft_path)
    assert queue[0]["what_can_go_wrong"]
    assert queue[0]["rollback"]
    assert snapshot.strategic_scorecard["public_writeups"] == 0
    assert snapshot.strategic_scorecard["public_feedback_items"] == 0


def test_dashboard_exposes_public_feedback_followup_for_recorded_writeup(tmp_path: Path):
    paths = default_v3_paths(tmp_path)
    paths.root.mkdir(parents=True)
    stats_path = tmp_path / "data" / "social" / "publication_stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(
        """
{
  "fetched_at": "2026-05-21T01:00:29.744706+00:00",
  "articles": [
    {
      "id": 198208037,
      "title": "How Mira's Green Dots Lied to My Human",
      "slug": "how-miras-green-dots-lied-to-my-human",
      "views": 0,
      "likes": 0,
      "comments": 0,
      "restacks": 0
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    ledger = ExperienceLedger(paths.ledger)
    delta = MemoryDelta.no_kernel_change(
        pipeline="a2a_trust_experiment",
        run_id="a2a_public_evidence_1",
        memory_class="epistemic",
        what_happened="recorded public writeup",
        what_mattered="feedback follow-up should be visible",
        what_changed="publication can now collect external feedback",
    )
    ledger.append(
        ExperienceRecord(
            id="a2a_public_evidence_1",
            pipeline="a2a_trust_experiment",
            trigger="operator_evidence",
            intent="record public evidence",
            outcome="completed",
            delta=delta,
            causal_links=[],
            confidence=0.95,
            memory_class="epistemic",
            eval_refs=[
                "public_writeup:v31_green_dot_is_not_evidence:url=https://uncountablemira.substack.com/p/198208037"
            ],
        )
    )

    snapshot = build_dashboard_snapshot(MemoryKernel(), ledger)
    queue = snapshot.review_queues["public_feedback_followup"]

    assert len(queue) == 1
    item = queue[0]
    assert item["publication_record_id"] == "a2a_public_evidence_1"
    assert item["slug"] == "v31_green_dot_is_not_evidence"
    assert item["published_url"] == "https://uncountablemira.substack.com/p/198208037"
    assert item["publication_stats_artifact"] == str(stats_path)
    assert item["publication_stats_fetched_at"] == "2026-05-21T01:00:29.744706+00:00"
    assert item["comments"] == "0"
    assert item["likes"] == "0"
    assert item["restacks"] == "0"
    assert item["feedback_ref_template"] == "external_feedback:v31_green_dot_is_not_evidence:source=<source>"
    assert item["feedback_packet_artifact"].endswith("/feedback_packet.json")
    assert "v3_prepare_public_feedback_packet.py" in item["feedback_packet_command_template"]
    assert "--stats-artifact" in item["feedback_packet_command_template"]
    assert str(stats_path) in item["feedback_packet_command_template"]
    assert "v3_record_public_feedback.py" in item["record_feedback_command_template"]
    assert "--feedback-source <source>" in item["record_feedback_command_template"]
    assert (
        "--published-url https://uncountablemira.substack.com/p/198208037" in item["record_feedback_command_template"]
    )
    assert "v3_record_public_feedback.py" in item["record_feedback_from_packet_command_template"]
    assert f"--packet {item['feedback_packet_artifact']}" in item["record_feedback_from_packet_command_template"]
    assert "--feedback-source <source>" in item["record_feedback_from_packet_command_template"]
    assert item["decision"] == "needs_external_feedback"
    assert item["why_now"]
    assert item["what_will_change"]
    assert "comments=0" in item["evidence"]
    assert item["what_can_go_wrong"]
    assert item["rollback"]
    assert snapshot.strategic_scorecard["public_writeups"] == 1
    assert snapshot.strategic_scorecard["public_feedback_items"] == 0

    ledger.append(
        ExperienceRecord(
            id="a2a_public_feedback_1",
            pipeline="a2a_trust_experiment",
            trigger="operator_evidence",
            intent="record public feedback",
            outcome="completed",
            delta=MemoryDelta.no_kernel_change(
                pipeline="a2a_trust_experiment",
                run_id="a2a_public_feedback_1",
                memory_class="epistemic",
                what_happened="recorded feedback",
                what_mattered="feedback gap is closed",
                what_changed="external feedback ref exists",
            ),
            causal_links=[],
            confidence=0.95,
            memory_class="epistemic",
            eval_refs=["external_feedback:v31_green_dot_is_not_evidence:source=substack-comment:262387868"],
        )
    )

    updated = build_dashboard_snapshot(MemoryKernel(), ledger)
    assert updated.review_queues["public_feedback_followup"] == []
    assert updated.strategic_scorecard["public_feedback_items"] == 1


def test_dashboard_exposes_customer_discovery_feedback_queue_until_three_feedback_events(tmp_path: Path):
    paths = default_v3_paths(tmp_path)
    paths.root.mkdir(parents=True)
    ledger = ExperienceLedger(paths.ledger)
    ledger.append(
        ExperienceRecord(
            id="a2a_public_evidence_1",
            pipeline="a2a_trust_experiment",
            trigger="operator_evidence",
            intent="record public evidence",
            outcome="completed",
            delta=MemoryDelta.no_kernel_change(
                pipeline="a2a_trust_experiment",
                run_id="a2a_public_evidence_1",
                memory_class="epistemic",
                what_happened="recorded public writeup",
                what_mattered="feedback gate should remain visible",
                what_changed="external feedback still needed",
            ),
            causal_links=[],
            confidence=0.95,
            memory_class="epistemic",
            eval_refs=[
                "public_writeup:v31_green_dot_is_not_evidence:url=https://uncountablemira.substack.com/p/198208037",
                "external_feedback:v31_green_dot_is_not_evidence:source=reader-reply-1",
            ],
        )
    )

    snapshot = build_dashboard_snapshot(MemoryKernel(), ledger)
    queue = snapshot.review_queues["customer_discovery_feedback"]

    assert len(queue) == 1
    item = queue[0]
    assert item["topic"] == "a2a_trust_manifest"
    assert item["missing_feedback_count"] == "2"
    assert item["feedback_ref_template"] == "customer_discovery:<source>"
    assert item["feedback_packet_artifact"].endswith(
        "/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json"
    )
    assert "v3_prepare_customer_discovery_packet.py" in item["feedback_packet_command_template"]
    assert "--topic a2a_trust_manifest" in item["feedback_packet_command_template"]
    assert "v3_record_customer_discovery_feedback.py" in item["record_feedback_command_template"]
    assert "--source <source>" in item["record_feedback_command_template"]
    assert "--insight <insight>" in item["record_feedback_command_template"]
    assert "v3_record_customer_discovery_feedback.py" in item["record_feedback_from_packet_command_template"]
    assert f"--packet {item['feedback_packet_artifact']}" in item["record_feedback_from_packet_command_template"]
    assert item["decision"] == "needs_customer_discovery_feedback"
    assert item["why_now"]
    assert item["what_will_change"]
    assert "external_feedback_events=1/3" in item["evidence"]
    assert item["what_can_go_wrong"]
    assert item["rollback"]

    for idx in range(2, 4):
        ledger.append(
            ExperienceRecord(
                id=f"customer_discovery_feedback_{idx}",
                pipeline="a2a_trust_experiment",
                trigger="operator_evidence",
                intent="record customer discovery feedback",
                outcome="completed",
                delta=MemoryDelta.no_kernel_change(
                    pipeline="a2a_trust_experiment",
                    run_id=f"customer_discovery_feedback_{idx}",
                    memory_class="epistemic",
                    what_happened="recorded discovery feedback",
                    what_mattered="feedback gate progresses",
                    what_changed="external feedback ref exists",
                ),
                causal_links=[],
                confidence=0.95,
                memory_class="epistemic",
                eval_refs=[f"customer_discovery:customer-interview-{idx}"],
            )
        )

    updated = build_dashboard_snapshot(MemoryKernel(), ledger)
    assert updated.review_queues["customer_discovery_feedback"] == []
    assert updated.strategic_scorecard["public_feedback_items"] == 3


def test_dashboard_exposes_provider_provisioning_queue_without_counting_readiness(tmp_path: Path):
    paths = default_v3_paths(tmp_path)
    paths.root.mkdir(parents=True)
    write_provider_resolver_config_template(paths.provider_resolvers, providers=("social",))
    write_provider_adapter_config_template(paths.provider_adapters, providers=("social",))

    ledger = ExperienceLedger(paths.ledger)
    snapshot = build_dashboard_snapshot(MemoryKernel(), ledger)
    queue = snapshot.review_queues["provider_provisioning"]

    assert len(queue) == 1
    item = queue[0]
    assert item["status"] == "blocked_external"
    assert item["decision"] == "blocked_external"
    assert item["readiness_finding_count"] == "16"
    assert item["missing_env_count"] == "4"
    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT" in item["missing_env_vars"]
    assert "MIRA_SOCIAL_ADAPTER_TOKEN" in item["missing_env_vars"]
    assert item["resolver_config"] == str(paths.provider_resolvers)
    assert item["adapter_config"] == str(paths.provider_adapters)
    assert item["env_template_artifact"] == str(paths.root / "provider_provisioning.template")
    assert item["runbook_artifact"] == str(paths.root / "provider_provisioning.runbook.md")
    assert item["configured_resolvers"] == "social"
    assert item["configured_adapters"] == "social"
    assert "v3_provider_readiness.py" in item["readiness_command_template"]
    assert "--root" in item["readiness_command_template"]
    assert str(tmp_path) in item["readiness_command_template"]
    assert "v3_provider_readiness.py" in item["env_template_command_template"]
    assert "--write-env-template" in item["env_template_command_template"]
    assert "v3_provider_readiness.py" in item["runbook_command_template"]
    assert "--write-runbook" in item["runbook_command_template"]
    assert item["scoped_provider"] == "social"
    assert item["scoped_env_template_artifact"] == str(paths.root / "provider_provisioning.social.template")
    assert "--write-env-template" in item["scoped_env_template_command_template"]
    assert "provider_provisioning.social.template" in item["scoped_env_template_command_template"]
    assert item["scoped_missing_env_count"] == "4"
    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT" in item["scoped_missing_env_vars"]
    assert "--require-resolver social" in item["scoped_readiness_command_template"]
    assert "--require-adapter social" in item["scoped_readiness_command_template"]
    assert "v3_provider_production_canary.py" in item["scoped_dry_run_command_template"]
    assert "--provider social" in item["scoped_dry_run_command_template"]
    assert "--dry-run" in item["scoped_dry_run_command_template"]
    assert "v3_provider_production_canary.py" in item["scoped_canary_command_template"]
    assert "--provider social" in item["scoped_canary_command_template"]
    assert item["why_now"]
    assert item["what_will_change"]
    assert item["evidence"]
    assert item["what_can_go_wrong"]
    assert item["rollback"]
    assert "provider_production_readiness_blocked" in snapshot.strategic_scorecard["watch_gates"]


def test_provider_provisioning_queue_selects_smallest_canary_scope(tmp_path: Path):
    paths = default_v3_paths(tmp_path)
    paths.root.mkdir(parents=True)
    write_provider_resolver_config_template(paths.provider_resolvers, providers=("social",))
    write_provider_adapter_config_template(paths.provider_adapters, providers=("social", "tts"))

    snapshot = build_dashboard_snapshot(MemoryKernel(), ExperienceLedger(paths.ledger))
    item = snapshot.review_queues["provider_provisioning"][0]

    assert item["scoped_provider"] == "tts"
    assert item["scoped_env_template_artifact"] == str(paths.root / "provider_provisioning.tts.template")
    assert "--write-env-template" in item["scoped_env_template_command_template"]
    assert "provider_provisioning.tts.template" in item["scoped_env_template_command_template"]
    assert item["scoped_missing_env_count"] == "2"
    assert "MIRA_TTS_ADAPTER_ENDPOINT" in item["scoped_missing_env_vars"]
    assert "MIRA_TTS_ADAPTER_TOKEN" in item["scoped_missing_env_vars"]
    assert "--skip-resolvers" in item["scoped_env_template_command_template"]
    assert "--require-adapter tts" in item["scoped_env_template_command_template"]
    assert "--skip-resolvers" in item["scoped_readiness_command_template"]
    assert "--require-adapter tts" in item["scoped_readiness_command_template"]
    assert "--provider tts" in item["scoped_canary_command_template"]
