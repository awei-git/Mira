import json
import subprocess
import sys
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
    operational = json.loads(Path(result.paths["operational"]).read_text(encoding="utf-8"))
    voice = json.loads(Path(result.paths["voice"]).read_text(encoding="utf-8"))
    briefing = json.loads(Path(result.paths["briefing_interest"]).read_text(encoding="utf-8"))
    approval = json.loads(Path(result.paths["approval_burden"]).read_text(encoding="utf-8"))
    memory = json.loads(Path(result.paths["memory_audit"]).read_text(encoding="utf-8"))
    trace = json.loads(Path(result.paths["trace_completeness"]).read_text(encoding="utf-8"))

    assert "repeat_error_rate" in operational
    assert "post_scar_recurrence_rate" in operational
    assert "voice_score_mean" in voice
    assert "voice_score_std" in voice
    assert "generic_failure_rate" in voice
    assert "briefing_precision_at_5" in briefing
    assert "briefing_action_rate" in briefing
    assert "approval_minutes_per_week" in approval
    assert "approval_requests_per_100_side_effects" in approval
    assert "critical_pollution_count" in memory
    assert "snapshot_contamination_rate" in memory
    assert "trace_completeness" in trace
    assert "orphan_action_count" in trace


def test_capture_v31_baselines_uses_default_runtime_paths(tmp_path: Path):
    run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    result = capture_v31_baselines(tmp_path)

    assert result.paths["trace_completeness"].endswith(".json")
    assert Path(result.paths["trace_completeness"]).exists()


def test_v31_baseline_capture_cli_writes_required_artifacts(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_baseline_capture.py",
            "--root",
            str(tmp_path),
            "--date",
            "2026-05-21",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["date_key"] == "2026_05_21"
    assert set(payload["paths"]) == {
        "operational",
        "voice",
        "briefing_interest",
        "approval_burden",
        "memory_audit",
        "trace_completeness",
    }
    assert all(Path(path).exists() for path in payload["paths"].values())
