from pathlib import Path

from mira.runtime import (
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


def test_legacy_job_and_task_mapping():
    assert pipeline_for_background_job("explore-morning") == "intelligence_briefing"
    assert pipeline_for_background_job("analyst-pre") == "market_monitor"
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
