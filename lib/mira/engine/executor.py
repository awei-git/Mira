"""Memory-first pipeline executor."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mira.agents.base import Agent, StepInput, StepOutput
from mira.capabilities import PreflightResult, preflight_for_pipeline, run_preflight
from mira.engine.checkpoint import Checkpoint, CheckpointStore
from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalRequest, ApprovalStore, grant_required
from mira.kernel.commit import MemoryCommitLog, SecurityGateway
from mira.kernel.consolidation import ConsolidationResult, MemoryConsolidator
from mira.kernel.delta import MemoryAction, MemoryDelta
from mira.kernel.ledger import ExperienceLedger, ExperienceRecord, new_run_id
from mira.kernel.schema import MemoryKernel
from mira.kernel.snapshot import MemorySnapshot, SnapshotBuilder
from mira.kernel.store import KernelStore

from .pipeline import Pipeline, Step


@dataclass(frozen=True)
class PipelineRunResult:
    run_id: str
    record: ExperienceRecord
    snapshot: MemorySnapshot
    outputs: dict[str, Any]
    consolidation: ConsolidationResult


class PipelineExecutor:
    """Executes pipelines with memory as the first and last dependency."""

    def __init__(
        self,
        kernel_store: KernelStore,
        ledger: ExperienceLedger,
        agents: dict[str, Agent] | None = None,
        consolidator: MemoryConsolidator | None = None,
        gateway: SecurityGateway | None = None,
        commit_log: MemoryCommitLog | None = None,
        effect_log: EffectLog | None = None,
        approval_store: ApprovalStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        connector_status: dict[str, bool] | None = None,
        artifact_root: Path | str | None = None,
    ):
        self.kernel_store = kernel_store
        self.ledger = ledger
        self.agents = agents or {}
        self.consolidator = consolidator or MemoryConsolidator()
        self.gateway = gateway or SecurityGateway()
        self.commit_log = commit_log
        self.effect_log = effect_log
        self.approval_store = approval_store
        self.checkpoint_store = checkpoint_store
        self.connector_status = connector_status or {}
        self.artifact_root = Path(artifact_root) if artifact_root else None

    def run(
        self,
        pipeline: Pipeline,
        payload: dict[str, Any],
        intent: str,
        trigger: str = "manual",
        run_id: str | None = None,
        resume: bool = False,
    ) -> PipelineRunResult:
        run_id = run_id or new_run_id(pipeline.name)
        kernel = self.kernel_store.load()
        snapshot = SnapshotBuilder(self.ledger).build(
            kernel=kernel,
            pipeline=pipeline.name,
            memory_class=pipeline.memory_class,
            involved_skills=pipeline.involved_skills,
            intent=intent,
        )
        preflight = self._preflight(pipeline, payload)
        checkpoint = self.checkpoint_store.load(run_id) if resume and self.checkpoint_store else None
        if preflight.ok:
            outputs, failure = self._execute_steps(pipeline, run_id, payload, snapshot, checkpoint)
        else:
            outputs = {
                "_outcome": "blocked_preflight",
                "_what_happened": f"{pipeline.name} blocked by missing capabilities",
                "_what_mattered": ", ".join(preflight.missing),
                "_what_changed": "No workflow side effects ran because preflight failed",
                "_preflight": preflight,
            }
            failure = f"missing capabilities: {', '.join(preflight.missing)}"
        delta = self._build_delta(pipeline, run_id, outputs, failure)
        commit = self.gateway.validate(delta)
        consolidation = self.consolidator.apply_commit(kernel, delta, commit)
        if self.commit_log is not None:
            self.commit_log.append(commit)
        record = ExperienceRecord(
            id=run_id,
            pipeline=pipeline.name,
            trigger=trigger,
            intent=intent,
            outcome=outputs.get("_outcome", "completed" if failure is None else "failed"),
            delta=delta,
            causal_links=list(outputs.get("_causal_links", snapshot.causal_links())),
            confidence=0.8 if failure is None else 0.4,
            memory_class=pipeline.memory_class,
            artifacts=list(outputs.get("_artifacts", [])),
            eval_refs=list(outputs.get("_eval_refs", [])),
            side_effect_refs=list(outputs.get("_side_effect_refs", [])),
            memory_commit_id=commit.commit_id,
        )
        self.ledger.append(record)
        self.kernel_store.save(kernel)
        return PipelineRunResult(run_id, record, snapshot, outputs, consolidation)

    def _execute_steps(
        self,
        pipeline: Pipeline,
        run_id: str,
        payload: dict[str, Any],
        snapshot: MemorySnapshot,
        checkpoint: Checkpoint | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        outputs: dict[str, Any] = dict(checkpoint.outputs) if checkpoint else {}
        index = self._resume_index(pipeline, checkpoint)
        loop_counts: dict[str, int] = {}
        failure: str | None = None
        while index < len(pipeline.steps):
            step = pipeline.steps[index]
            if step.name in outputs.get("_skip_steps", []):
                outputs[step.name] = {"status": "skipped_by_prior_step"}
                self._save_checkpoint(pipeline, run_id, step.name, outputs, index)
                index += 1
                continue
            try:
                output = self._execute_step(step, pipeline, run_id, payload, outputs, snapshot)
            except Exception as exc:
                failure = f"{step.name}: {exc}"
                if step.on_fail == "skip":
                    index += 1
                    continue
                if step.on_fail == "retry" and loop_counts.get(step.name, 0) < step.retries:
                    loop_counts[step.name] = loop_counts.get(step.name, 0) + 1
                    continue
                if step.on_fail == "escalate":
                    outputs.setdefault("_memory_actions", []).append(
                        MemoryAction("escalate", f"step:{step.name}", str(exc))
                    )
                break
            outputs[step.name] = output.payload
            self._merge_control_fields(outputs, output.payload)
            if output.summary:
                outputs.setdefault("_summaries", []).append(output.summary)
            self._save_checkpoint(pipeline, run_id, step.name, outputs, index)
            if not output.succeeded and step.loop_to:
                count = loop_counts.get(step.name, 0)
                if count < step.loop_max:
                    loop_counts[step.name] = count + 1
                    index = pipeline.step_index(step.loop_to)
                    continue
            if not output.succeeded and step.on_fail == "abort":
                failure = f"{step.name}: failed evaluation"
                break
            index += 1
        return outputs, failure

    def _merge_control_fields(self, outputs: dict[str, Any], payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if not key.startswith("_"):
                continue
            if key == "_memory_actions":
                outputs.setdefault("_memory_actions", []).extend(value)
            else:
                outputs[key] = value

    def _execute_step(
        self,
        step: Step,
        pipeline: Pipeline,
        run_id: str,
        payload: dict[str, Any],
        outputs: dict[str, Any],
        snapshot: MemorySnapshot,
    ) -> StepOutput:
        step_input = StepInput(
            run_id=run_id, pipeline=pipeline.name, step=step.name, payload=payload, prior_outputs=outputs
        )
        approval = self._approval_blocker(pipeline, step, run_id)
        if approval is not None:
            return approval
        effect_key = self._effect_key(pipeline, step, run_id, payload)
        if effect_key and self.effect_log:
            self.effect_log.plan(
                idempotency_key=effect_key,
                run_id=run_id,
                pipeline=pipeline.name,
                action=pipeline.effect_steps.get(step.name, step.name),
                target=str(payload.get("target") or payload.get("title") or run_id),
            )
            self.effect_log.mark_executing(effect_key)
        try:
            if step.type == "agent":
                if not step.agent or step.agent not in self.agents:
                    raise ValueError(f"agent not registered: {step.agent}")
                output = self.agents[step.agent].execute(step_input, snapshot)
            elif step.action is None:
                output = StepOutput(payload={"status": "skipped"}, summary=f"{step.name} had no action")
            else:
                result = step.action(input=step_input, memory=snapshot)
                if isinstance(result, StepOutput):
                    output = result
                elif isinstance(result, dict):
                    output = StepOutput(payload=result)
                else:
                    output = StepOutput(payload={"result": result})
        except Exception:
            if effect_key and self.effect_log:
                self.effect_log.mark_unknown(effect_key, "step raised after execution started")
            raise
        if effect_key and self.effect_log:
            if output.succeeded:
                effect_entry = self.effect_log.mark_succeeded(effect_key, output.summary)
            else:
                effect_entry = self.effect_log.mark_failed(effect_key, output.summary)
            output.payload.setdefault("_side_effect_refs", []).append(effect_entry.effect_id)
        return output

    def _build_delta(
        self,
        pipeline: Pipeline,
        run_id: str,
        outputs: dict[str, Any],
        failure: str | None,
    ) -> MemoryDelta:
        explicit = outputs.get("_memory_delta")
        if isinstance(explicit, MemoryDelta):
            return explicit
        actions = list(outputs.get("_memory_actions", []))
        if failure:
            actions.append(MemoryAction("create_scar", f"scar:{pipeline.name}:{run_id}", failure))
        return MemoryDelta(
            pipeline=pipeline.name,
            run_id=run_id,
            memory_class=pipeline.memory_class,
            what_happened=outputs.get("_what_happened", f"{pipeline.name} pipeline ran"),
            what_mattered=outputs.get("_what_mattered", "Run produced structured experience"),
            what_changed=outputs.get("_what_changed", "Future snapshots include this run as causal context"),
            what_failed=failure,
            actions=actions,
        )

    def _preflight(self, pipeline: Pipeline, payload: dict[str, Any]) -> PreflightResult:
        connectors = dict(self.connector_status)
        connectors.update(payload.get("connectors", {}))
        if pipeline.required_capabilities:
            return run_preflight(
                pipeline.name,
                {name: connectors.get(name, available) for name, available in pipeline.required_capabilities.items()},
            )
        return preflight_for_pipeline(pipeline.name, connectors)

    def _approval_blocker(
        self,
        pipeline: Pipeline,
        step: Step,
        run_id: str,
    ) -> StepOutput | None:
        risk = pipeline.risk_actions.get(step.name)
        if not risk or not grant_required(risk):  # type: ignore[arg-type]
            return None
        if self.approval_store and self.approval_store.find_grant(action=step.name, risk=risk, scope=pipeline.name):
            return None
        if self.approval_store:
            request = self.approval_store.request(
                ApprovalRequest(
                    action=step.name,
                    risk=risk,  # type: ignore[arg-type]
                    scope=pipeline.name,
                    reason=f"{pipeline.name}.{step.name} requires {risk} approval",
                    run_id=run_id,
                )
            )
            return StepOutput(
                payload={
                    "_outcome": "approval_required",
                    "_what_failed": f"approval required: {request.request_id}",
                    "_approval_request_id": request.request_id,
                },
                summary=f"approval required: {request.request_id}",
                succeeded=False,
            )
        return StepOutput(
            payload={
                "_outcome": "approval_required",
                "_what_failed": f"{pipeline.name}.{step.name} requires {risk} approval",
            },
            summary=f"{risk} approval required",
            succeeded=False,
        )

    def _effect_key(self, pipeline: Pipeline, step: Step, run_id: str, payload: dict[str, Any]) -> str | None:
        if step.name in pipeline.effect_steps:
            target = str(payload.get("target") or payload.get("title") or run_id)
            return f"{pipeline.name}:{step.name}:{target}"
        return None

    def _save_checkpoint(
        self,
        pipeline: Pipeline,
        run_id: str,
        step_name: str,
        outputs: dict[str, Any],
        index: int,
    ) -> None:
        if self.checkpoint_store is None:
            return
        if pipeline.checkpoint_every <= 0 or (index + 1) % pipeline.checkpoint_every != 0:
            return
        self.checkpoint_store.save(Checkpoint(run_id=run_id, pipeline=pipeline.name, step=step_name, outputs=outputs))

    def _resume_index(self, pipeline: Pipeline, checkpoint: Checkpoint | None) -> int:
        if checkpoint is None:
            return 0
        try:
            return pipeline.step_index(checkpoint.step) + 1
        except KeyError:
            return 0
