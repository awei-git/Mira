import json
from pathlib import Path

from mira.runtime import (
    default_causal_evidence_log,
    default_ledger,
    default_v3_paths,
    pipeline_for_background_job,
    pipeline_for_task,
    record_background_completion,
    prepare_background_context,
    record_task_completion,
    run_communication,
)


def test_runtime_paths_are_under_data_v3(tmp_path: Path):
    paths = default_v3_paths(tmp_path)

    assert paths.root == tmp_path / "data" / "v3"
    assert paths.kernel.name == "kernel.json"
    assert paths.ledger.name == "experience_ledger.jsonl"
    assert paths.causal_evidence.name == "causal_evidence.jsonl"
    assert paths.approvals.name == "approvals.jsonl"
    assert paths.quarantine.name == "memory_quarantine.jsonl"
    assert paths.workflow_audits.name == "workflow_audits"
    assert paths.baselines.name == "baselines"


def test_legacy_job_and_task_mapping():
    assert pipeline_for_background_job("explore-morning") == "intelligence_briefing"
    assert pipeline_for_background_job("analyst-pre") == "market_monitor"
    assert pipeline_for_background_job("podcast-en-essay") == "podcast_production"
    assert pipeline_for_background_job("podcast-zh-essay") == "podcast_production"
    assert pipeline_for_background_job("voiceover-essay") == "podcast_production"
    assert pipeline_for_background_job("unknown") == "memory_maintenance"
    assert pipeline_for_task(["research"]) == "research_deep_dive"
    assert pipeline_for_task(["unknown"]) == "communication"


def test_record_task_completion_writes_v3_experience(tmp_path: Path):
    record = record_task_completion(
        task_id="task_1",
        status="done",
        summary="Finished a research task",
        tags=["research"],
        root=tmp_path,
    )

    records = default_ledger(tmp_path).list()
    assert record.pipeline == "research_deep_dive"
    assert records[0].id == record.id
    assert records[0].delta.actions[0].target == "skill:research_deep_dive"


def test_record_background_completion_writes_v3_experience(tmp_path: Path):
    record = record_background_completion("substack-comments", root=tmp_path)

    assert record.pipeline == "social_reactive"
    assert default_ledger(tmp_path).list()[0].pipeline == "social_reactive"


def test_record_background_completion_skips_writing_pipeline_noop_ticks(tmp_path: Path):
    record = record_background_completion("writing-pipeline", root=tmp_path)

    assert record is None
    assert default_ledger(tmp_path).list() == []


def test_prepare_background_context_exports_snapshot(tmp_path: Path):
    env = prepare_background_context("explore-morning", root=tmp_path)

    assert env["MIRA_V3_PIPELINE"] == "intelligence_briefing"
    snapshot_path = Path(env["MIRA_V3_MEMORY_SNAPSHOT"])
    assert snapshot_path.exists()
    assert snapshot_path.name == "explore-morning.json"


def test_run_communication_uses_memory_between_runs(tmp_path: Path):
    first = run_communication("Implement the next piece and give me status.", root=tmp_path)
    second = run_communication("Implement the next piece and give me status.", root=tmp_path)

    assert first.startswith("I read this as:")
    assert second.startswith("Short answer:")
    evidence = default_causal_evidence_log(tmp_path).list()
    assert len(evidence) == 1
    assert evidence[0].level == "L3"


def test_run_named_workflow_uses_default_v31_stores(tmp_path: Path):
    from mira.runtime import default_commit_log, run_named_workflow

    result = run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    assert result.record.pipeline == "a2a_trust_experiment"
    assert result.record.artifacts
    assert default_commit_log(tmp_path).list()[0].status == "applied"
    audit_artifacts = list(default_v3_paths(tmp_path).workflow_audits.glob("a2a_trust_experiment-*.json"))
    assert len(audit_artifacts) == 1
    audit = json.loads(audit_artifacts[0].read_text(encoding="utf-8"))["workflow_pack_audit"]
    assert audit["result"] == "pass"
    assert audit["audit_hash"]
    assert audit["enabled_at"]
    assert any(path.endswith("a2a_trust/SKILL.md") for path in audit["files_checked"])


def test_representative_workflows_emit_l3_causal_evidence_on_second_run(tmp_path: Path):
    from mira.runtime import run_named_workflow

    run_named_workflow("article_creation", payload={"connectors": {"substack": False, "twitter": False}}, root=tmp_path)
    second_article = run_named_workflow(
        "article_creation",
        payload={"connectors": {"substack": False, "twitter": False}},
        root=tmp_path,
    )
    run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )
    second_a2a = run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    evidence = default_causal_evidence_log(tmp_path).list()
    assert len(evidence) == 2
    assert {item.pipeline for item in evidence} == {"article_creation", "a2a_trust_experiment"}
    assert {item.level for item in evidence} == {"L3"}
    assert second_article.record.causal_links == [item.evidence_id for item in evidence if item.pipeline == "article_creation"]
    assert second_a2a.record.causal_links == [item.evidence_id for item in evidence if item.pipeline == "a2a_trust_experiment"]
