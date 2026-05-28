import json
from pathlib import Path

from mira.agents.base import StepInput, StepOutput
from mira.engine import ApprovalRequest, ApprovalStore, EffectLog, PipelineExecutor, Step, Trigger
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
    checkpoint_every: int = 1,
) -> Pipeline:
    return Pipeline(
        name=name,
        trigger=Trigger("manual", "test"),
        steps=steps,
        priority=1,
        version=1,
        max_duration_s=60,
        checkpoint_every=checkpoint_every,
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
    assert result.record.eval_refs == ["preflight_missing:local_files"]


def test_executor_records_degraded_preflight_notes_on_successful_run(tmp_path: Path):
    def action(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"ok": True}, summary="drafted locally")

    executor = PipelineExecutor(_store(tmp_path), ExperienceLedger(tmp_path / "ledger.jsonl"))
    result = executor.run(
        _pipeline("article_creation", [Step("draft", "deterministic", action=action)]),
        {"connectors": {"substack": False, "twitter": False}},
        intent="draft article",
    )

    assert result.record.outcome == "completed"
    assert "preflight_degraded:substack: write_output_folder" in result.record.eval_refs
    assert "preflight_degraded:twitter: skip_social_promo" in result.record.eval_refs
    assert result.outputs["_preflight_degradation_notes"] == [
        "substack: write_output_folder",
        "twitter: skip_social_promo",
    ]


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

    grant = approvals.grant(pending[0].request_id, granted_by="wa")
    second = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=effects,
    ).run(pipeline, {"connectors": {"substack": True}}, intent="publish")

    assert second.record.outcome == "completed"
    effect = effects.get_by_idempotency_key(f"article_creation:publish_substack:{second.run_id}")
    assert effect.status == "succeeded"
    assert effect.approval_token_id == grant.grant_id
    assert effect.preview_hash == grant.preview_hash


def test_executor_requires_new_approval_when_preview_changes_after_grant(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    effects = EffectLog(tmp_path / "effects.jsonl")
    calls = 0

    def publish(input: StepInput, memory) -> StepOutput:
        nonlocal calls
        calls += 1
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
    ).run(pipeline, {"target": "article-1", "body": "draft one"}, intent="publish", run_id="run_publish")

    pending = approvals.list_requests(status="pending")
    assert first.record.outcome == "approval_required"
    assert len(pending) == 1
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    second = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=effects,
    ).run(pipeline, {"target": "article-1", "body": "draft two"}, intent="publish", run_id="run_publish")

    new_pending = approvals.list_requests(status="pending")
    assert calls == 0
    assert second.record.outcome == "approval_required"
    assert len(new_pending) == 1
    assert new_pending[0].preview_hash != grant.preview_hash
    assert effects.get_by_idempotency_key("article_creation:publish_substack:article-1") is None


def test_executor_auto_pauses_noncritical_approval_when_queue_over_budget(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    for index in range(11):
        approvals.request(
            ApprovalRequest(
                action="publish_substack",
                risk="publish_public",
                scope="article_creation",
                reason="publish public article",
                run_id=f"queued_{index}",
            )
        )

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        risk_actions={"publish_substack": "publish_public"},
        effect_steps={"publish_substack": "publish_substack"},
    )

    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=EffectLog(tmp_path / "effects.jsonl"),
    ).run(pipeline, {"target": "article-1", "body": "draft"}, intent="publish")

    assert result.record.outcome == "blocked_preflight"
    assert result.record.delta.what_failed == "approval queue over budget: auto-paused non-critical approval request"
    assert len(approvals.list_requests(status="pending")) == 11


def test_executor_allows_critical_approval_when_queue_over_budget(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    for index in range(11):
        approvals.request(
            ApprovalRequest(
                action="publish_substack",
                risk="publish_public",
                scope="article_creation",
                reason="publish public article",
                run_id=f"queued_{index}",
            )
        )

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        risk_actions={"publish_substack": "publish_public"},
        effect_steps={"publish_substack": "publish_substack"},
    )

    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
        effect_log=EffectLog(tmp_path / "effects.jsonl"),
    ).run(
        pipeline,
        {"target": "article-1", "body": "draft", "approval_critical": True},
        intent="publish",
    )

    assert result.record.outcome == "approval_required"
    assert len(approvals.list_requests(status="pending")) == 12


def test_executor_persists_effect_preview_hash_and_external_ref(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(
            payload={
                "published": True,
                "_external_ref": "substack:post:article-1",
            },
            summary="published",
        )

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(pipeline, {"target": "article-1", "body": "draft"}, intent="publish")
    latest = effects.get_by_idempotency_key("article_creation:publish_substack:article-1")

    assert latest is not None
    assert latest.step_id == "publish_substack"
    assert latest.action_type == "publish_substack"
    assert latest.preview_hash
    assert latest.replay_bundle_ref
    bundle = json.loads(Path(latest.replay_bundle_ref).read_text(encoding="utf-8"))
    assert bundle["run_id"] == result.run_id
    assert bundle["action_type"] == "publish_substack"
    assert bundle["idempotency_key"] == "article_creation:publish_substack:article-1"
    assert bundle["preview_hash"] == latest.preview_hash
    assert bundle["payload"]["body"] == "draft"
    assert latest.external_ref == "substack:post:article-1"
    assert latest.executed_at is not None


def test_executor_replay_bundle_redacts_sensitive_payload_fields(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(
        pipeline,
        {"target": "article-1", "body": "draft", "api_token": "secret-token"},
        intent="publish",
    )
    latest = effects.get_by_idempotency_key("article_creation:publish_substack:article-1")
    assert latest is not None
    bundle = json.loads(Path(latest.replay_bundle_ref).read_text(encoding="utf-8"))

    assert bundle["payload"]["api_token"] == "[redacted]"
    assert bundle["compensation"]["strategy"] == "unpublish_or_mark_retracted"


def test_executor_forces_checkpoint_before_effect_action(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    checkpoints = CheckpointStore(tmp_path / "checkpoints")

    def publish(input: StepInput, memory) -> StepOutput:
        raise RuntimeError("process died after side-effect handoff")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
        checkpoint_every=99,
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
        checkpoint_store=checkpoints,
    ).run(pipeline, {"target": "article-1"}, intent="publish")
    checkpoint = checkpoints.load(result.run_id)
    latest = effects.get_by_idempotency_key("article_creation:publish_substack:article-1")

    assert result.record.outcome == "failed"
    assert checkpoint is not None
    assert checkpoint.phase == "before_effect"
    assert checkpoint.step == "publish_substack"
    assert "publish_substack" not in checkpoint.outputs
    assert latest is not None
    assert latest.status == "unknown"


def test_executor_resumes_before_effect_checkpoint_without_skipping_action(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    checkpoints = CheckpointStore(tmp_path / "checkpoints")
    checkpoints.save(
        Checkpoint(
            run_id="run_resume_effect",
            pipeline="article_creation",
            step="publish_substack",
            outputs={"prepare": {"value": 1}},
            phase="before_effect",
        )
    )
    calls = {"prepare": 0, "publish": 0}

    def prepare(input: StepInput, memory) -> StepOutput:
        calls["prepare"] += 1
        return StepOutput(payload={"value": 0})

    def publish(input: StepInput, memory) -> StepOutput:
        calls["publish"] += 1
        return StepOutput(payload={"published": input.prior_outputs["prepare"]["value"] == 1})

    pipeline = _pipeline(
        "article_creation",
        [
            Step("prepare", "deterministic", action=prepare),
            Step("publish_substack", "deterministic", action=publish),
        ],
        effect_steps={"publish_substack": "publish_substack"},
        checkpoint_every=99,
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
        checkpoint_store=checkpoints,
    ).run(pipeline, {"target": "article-1"}, intent="publish", run_id="run_resume_effect", resume=True)
    checkpoint = checkpoints.load("run_resume_effect")

    assert calls == {"prepare": 0, "publish": 1}
    assert result.outputs["prepare"]["value"] == 1
    assert result.outputs["publish_substack"]["published"] is True
    assert checkpoint is not None
    assert checkpoint.phase == "after_step"


def test_executor_allows_actions_to_leave_effects_unresolved(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")

    def publish(input: StepInput, memory) -> StepOutput:
        return StepOutput(
            payload={
                "_effect_status": "unknown",
                "_effect_detail": "adapter returned before external confirmation",
            },
            summary="publish confirmation pending",
        )

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(pipeline, {"target": "article-1"}, intent="publish")

    latest = effects.get_by_idempotency_key("article_creation:publish_substack:article-1")
    assert latest is not None
    assert latest.status == "unknown"
    assert latest.detail == "adapter returned before external confirmation"
    assert result.record.side_effect_refs == [latest.effect_id]


def test_executor_does_not_retry_succeeded_side_effect(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.plan(
        idempotency_key="article_creation:publish_substack:article-1",
        run_id="run_previous",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    succeeded = effects.mark_succeeded("article_creation:publish_substack:article-1", "remote post exists")
    calls = 0

    def publish(input: StepInput, memory) -> StepOutput:
        nonlocal calls
        calls += 1
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(pipeline, {"target": "article-1"}, intent="publish")

    assert calls == 0
    assert result.record.outcome == "completed"
    assert result.record.side_effect_refs == [succeeded.effect_id]


def test_executor_blocks_unknown_side_effect_until_reconciled(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.plan(
        idempotency_key="article_creation:publish_substack:article-1",
        run_id="run_previous",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    unknown = effects.mark_unknown("article_creation:publish_substack:article-1", "process died after API call")
    calls = 0

    def publish(input: StepInput, memory) -> StepOutput:
        nonlocal calls
        calls += 1
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(pipeline, {"target": "article-1"}, intent="publish")

    assert calls == 0
    assert result.record.outcome == "reconciliation_required"
    assert (
        result.record.delta.what_failed == "effect reconciliation required: article_creation:publish_substack:article-1"
    )
    assert result.record.side_effect_refs == [unknown.effect_id]


def test_executor_marks_stale_executing_effect_unknown_before_retry(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.plan(
        idempotency_key="article_creation:publish_substack:article-1",
        run_id="run_previous",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    executing = effects.mark_executing("article_creation:publish_substack:article-1", "remote call started")
    calls = 0

    def publish(input: StepInput, memory) -> StepOutput:
        nonlocal calls
        calls += 1
        return StepOutput(payload={"published": True}, summary="published")

    pipeline = _pipeline(
        "article_creation",
        [Step("publish_substack", "deterministic", action=publish)],
        effect_steps={"publish_substack": "publish_substack"},
    )
    result = PipelineExecutor(
        _store(tmp_path),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
    ).run(pipeline, {"target": "article-1"}, intent="publish")
    latest = effects.get_by_idempotency_key("article_creation:publish_substack:article-1")

    assert calls == 0
    assert result.record.outcome == "reconciliation_required"
    assert latest is not None
    assert latest.status == "unknown"
    assert latest.effect_id != executing.effect_id
    assert result.record.side_effect_refs == [latest.effect_id]


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
