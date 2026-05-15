from pathlib import Path

from mira.agents.base import StepInput, StepOutput
from mira.engine import ApprovalStore, EffectLog, PipelineExecutor, Step, Trigger
from mira.engine.checkpoint import Checkpoint, CheckpointStore
from mira.engine.pipeline import Pipeline
from mira.kernel import ExperienceLedger, MemoryKernel
from mira.kernel.store import JsonKernelStore


def _pipeline(
    name: str,
    steps: list[Step],
    *,
    required_capabilities: dict[str, bool] | None = None,
    risk_actions: dict[str, str] | None = None,
    effect_steps: dict[str, str] | None = None,
) -> Pipeline:
    return Pipeline(
        name=name,
        trigger=Trigger("manual", "test"),
        steps=steps,
        priority=1,
        version=1,
        max_duration_s=60,
        checkpoint_every=1,
        memory_class="operational",
        required_capabilities=required_capabilities or {},
        risk_actions=risk_actions or {},
        effect_steps=effect_steps or {},
    )


def _store(tmp_path: Path) -> JsonKernelStore:
    store = JsonKernelStore(tmp_path / "kernel.json")
    store.save(MemoryKernel())
    return store


def test_executor_records_preflight_block_without_running_steps(tmp_path: Path):
    ran = False

    def action(input: StepInput, memory) -> StepOutput:
        nonlocal ran
        ran = True
        return StepOutput(payload={"ok": True})

    executor = PipelineExecutor(_store(tmp_path), ExperienceLedger(tmp_path / "ledger.jsonl"))
    result = executor.run(
        _pipeline(
            "local_required",
            [Step("work", "deterministic", action=action)],
            required_capabilities={"local_files": False},
        ),
        {},
        intent="test preflight",
    )

    assert ran is False
    assert result.record.outcome == "blocked_preflight"
    assert result.record.delta.what_failed == "missing capabilities: local_files"


def test_executor_queues_approval_then_runs_after_grant(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    effects = EffectLog(tmp_path / "effects.jsonl")

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        risk_actions={"publish_substack": "publish_public"},
        effect_steps={"publish_substack": "publish_substack"},
    )
    first = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=effects,
    ).run(pipeline, {"connectors": {"substack": True}}, intent="publish")

    pending = approvals.list_requests(status="pending")
    assert first.record.outcome == "approval_required"
    assert len(pending) == 1

    approvals.grant(pending[0].request_id, granted_by="wa")
    second = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=effects,
    ).run(pipeline, {"connectors": {"substack": True}}, intent="publish")

    assert second.record.outcome == "completed"
    assert effects.get_by_idempotency_key(f"article_creation:publish_substack:{second.run_id}").status == "succeeded"


def test_executor_resumes_from_checkpoint(tmp_path: Path):
    checkpoints = CheckpointStore(tmp_path / "checkpoints")
    checkpoints.save(
        Checkpoint(
            run_id="run_resume",
            pipeline="resume_pipeline",
            step="first",
            outputs={"first": {"value": 1}},
        )
    )

    def second(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"value": input.prior_outputs["first"]["value"] + 1})

    pipeline = _pipeline(
        "resume_pipeline",
        [
            Step("first", "deterministic", action=lambda input, memory: StepOutput(payload={"value": 0})),
            Step("second", "deterministic", action=second),
        ],
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        checkpoint_store=checkpoints,
    ).run(pipeline, {}, intent="resume", run_id="run_resume", resume=True)

    assert result.outputs["first"]["value"] == 1
    assert result.outputs["second"]["value"] == 2
