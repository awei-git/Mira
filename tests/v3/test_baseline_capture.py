from datetime import date
from pathlib import Path

from mira.baselines import capture_all_baselines
from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalStore
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.ledger import ExperienceLedger
from mira.runtime import capture_v31_baselines, run_named_workflow


def test_capture_all_baselines_writes_required_files(tmp_path: Path):
    result = capture_all_baselines(
        ledger=ExperienceLedger(tmp_path / "ledger.jsonl"),
        commit_log=MemoryCommitLog(tmp_path / "commits.jsonl"),
        effect_log=EffectLog(tmp_path / "effects.jsonl"),
        approval_store=ApprovalStore(tmp_path / "approvals.jsonl"),
        output_dir=tmp_path / "baselines",
        capture_date=date(2026, 5, 15),
    )

    assert result.date_key == "2026_05_15"
    assert set(result.paths) == {
        "operational",
        "voice",
        "briefing_interest",
        "approval_burden",
        "memory_audit",
        "trace_completeness",
    }
    assert all(Path(path).exists() for path in result.paths.values())


def test_capture_v31_baselines_uses_default_runtime_paths(tmp_path: Path):
    run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    result = capture_v31_baselines(tmp_path)

    assert result.paths["trace_completeness"].endswith(".json")
    assert Path(result.paths["trace_completeness"]).exists()
