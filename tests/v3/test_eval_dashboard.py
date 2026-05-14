from pathlib import Path

from mira.evals import EvalEvent, EvalHistory, bounded_threshold_adjustment
from mira.kernel import ExperienceLedger, MemoryKernel
from mira.kernel.delta import MemoryAction, MemoryDelta
from mira.kernel.ledger import ExperienceRecord
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

    snapshot = build_dashboard_snapshot(kernel, ledger)

    assert "communication" in snapshot.active_pipelines
    assert snapshot.skill_traces["article_writing"] == 1.0
    assert snapshot.recent_experience_ids == ["exp_1"]
    assert snapshot.hard_policy_count == 43
    assert snapshot.soft_policy_count == 9
