"""Memory-first pipeline executor."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from mira.agents.base import Agent, StepInput, StepOutput
from mira.capabilities import PreflightResult, preflight_for_pipeline, run_preflight
from mira.engine.checkpoint import Checkpoint, CheckpointStore
from mira.engine.effect_log import SUCCESS_STATUSES, EffectLog
from mira.engine.risk_gate import ApprovalRequest, ApprovalStore, grant_required
from mira.kernel.causal import CausalEvidence, CausalEvidenceLog
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
        causal_evidence_log: CausalEvidenceLog | None = None,
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
        self.causal_evidence_log = causal_evidence_log
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
            run_id=run_id,
        )
        preflight = self._preflight(pipeline, payload)
        checkpoint = self.checkpoint_store.load(run_id) if resume and self.checkpoint_store else None
        if preflight.ok:
            outputs, failure = self._execute_steps(pipeline, run_id, payload, snapshot, checkpoint)
            if preflight.degraded:
                notes = preflight.degradation_notes
                outputs.setdefault("_eval_refs", []).extend(f"preflight_degraded:{note}" for note in notes)
                outputs.setdefault("_preflight_degradation_notes", notes)
        else:
            outputs = {
                "_outcome": "blocked_preflight",
                "_what_happened": f"{pipeline.name} blocked by missing capabilities",
                "_what_mattered": ", ".join(preflight.missing),
                "_what_changed": "No workflow side effects ran because preflight failed",
                "_preflight": preflight,
                "_eval_refs": [f"preflight_missing:{name}" for name in preflight.missing],
            }
            failure = f"missing capabilities: {', '.join(preflight.missing)}"
        delta = self._build_delta(pipeline, run_id, outputs, failure)
        commit = self.gateway.validate(delta)
        consolidation = self.consolidator.apply_commit(kernel, delta, commit)
        if self.commit_log is not None:
            self.commit_log.append(commit)
        causal_links = self._persist_causal_evidence(outputs, run_id, pipeline.name)
        record = ExperienceRecord(
            id=run_id,
            pipeline=pipeline.name,
            trigger=trigger,
            intent=intent,
            outcome=outputs.get("_outcome", "completed" if failure is None else "failed"),
            delta=delta,
            causal_links=causal_links,
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
                output = self._execute_step(step, pipeline, run_id, payload, outputs, snapshot, index)
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
            self._save_checkpoint(
                pipeline,
                run_id,
                step.name,
                outputs,
                index,
                phase="after_step",
                force=step.name in pipeline.effect_steps,
            )
            if not output.succeeded and step.loop_to:
                count = loop_counts.get(step.name, 0)
                if count < step.loop_max:
                    loop_counts[step.name] = count + 1
                    index = pipeline.step_index(step.loop_to)
                    continue
            if not output.succeeded and step.on_fail == "abort":
                failure = str(output.payload.get("_what_failed") or f"{step.name}: failed evaluation")
                break
            index += 1
        return outputs, failure

    def _merge_control_fields(self, outputs: dict[str, Any], payload: dict[str, Any]) -> None:
        for key, value in payload.items():
            if not key.startswith("_"):
                continue
            if key == "_memory_actions":
                outputs.setdefault("_memory_actions", []).extend(value)
            elif key == "_causal_evidence":
                outputs.setdefault("_causal_evidence", []).extend(
                    item.to_dict() if isinstance(item, CausalEvidence) else item for item in value
                )
            elif key in {"_artifacts", "_eval_refs", "_side_effect_refs"}:
                outputs.setdefault(key, []).extend(value)
            else:
                outputs[key] = value

    def _persist_causal_evidence(self, outputs: dict[str, Any], run_id: str, pipeline: str) -> list[str]:
        evidence_items = list(outputs.get("_causal_evidence", []))
        links: list[str] = []
        for item in evidence_items:
            if isinstance(item, CausalEvidence):
                evidence = item
            elif isinstance(item, dict):
                evidence = CausalEvidence.from_dict(item)
            else:
                continue
            if not evidence.run_id or not evidence.pipeline:
                evidence = CausalEvidence(
                    memory_id=evidence.memory_id,
                    level=evidence.level,
                    reason=evidence.reason,
                    run_id=evidence.run_id or run_id,
                    pipeline=evidence.pipeline or pipeline,
                    trace_ids=evidence.trace_ids,
                    decision_ids=evidence.decision_ids,
                    effect_ids=evidence.effect_ids,
                    ablation_ref=evidence.ablation_ref,
                    evidence_id=evidence.evidence_id,
                    timestamp=evidence.timestamp,
                )
            if self.causal_evidence_log is not None:
                self.causal_evidence_log.append(evidence)
            links.append(evidence.evidence_id)
        return links

    def _execute_step(
        self,
        step: Step,
        pipeline: Pipeline,
        run_id: str,
        payload: dict[str, Any],
        outputs: dict[str, Any],
        snapshot: MemorySnapshot,
        index: int,
    ) -> StepOutput:
        step_input = StepInput(
            run_id=run_id, pipeline=pipeline.name, step=step.name, payload=payload, prior_outputs=outputs
        )
        preview_hash = self._effect_preview_hash(payload)
        approval = self._approval_blocker(pipeline, step, run_id, preview_hash, payload)
        if approval is not None:
            return approval
        approval_token_id = self._approval_token_id(pipeline, step, preview_hash)
        effect_key = self._effect_key(pipeline, step, run_id, payload)
        if effect_key and self.effect_log:
            existing_effect = self.effect_log.get_by_idempotency_key(effect_key)
            if existing_effect is not None and existing_effect.status in SUCCESS_STATUSES:
                return StepOutput(
                    payload={
                        "status": "side_effect_already_succeeded",
                        "_side_effect_refs": [existing_effect.effect_id],
                    },
                    summary=f"side effect already completed: {existing_effect.effect_id}",
                )
            if existing_effect is not None and existing_effect.status in {"executing", "started"}:
                existing_effect = self.effect_log.mark_unknown(
                    effect_key,
                    "effect was still executing when retry inspected the log",
                )
            if existing_effect is not None and existing_effect.status == "unknown":
                return StepOutput(
                    payload={
                        "_outcome": "reconciliation_required",
                        "_what_failed": f"effect reconciliation required: {effect_key}",
                        "_side_effect_refs": [existing_effect.effect_id],
                    },
                    summary=f"effect reconciliation required: {effect_key}",
                    succeeded=False,
                )
            action_type = pipeline.effect_steps.get(step.name, step.name)
            target = str(payload.get("target") or payload.get("title") or run_id)
            replay_bundle_ref = self._write_replay_bundle(
                pipeline=pipeline,
                step=step,
                run_id=run_id,
                action_type=action_type,
                target=target,
                idempotency_key=effect_key,
                preview_hash=preview_hash,
                approval_token_id=approval_token_id,
                payload=payload,
            )
            self.effect_log.plan(
                idempotency_key=effect_key,
                run_id=run_id,
                pipeline=pipeline.name,
                action=action_type,
                target=target,
                step_id=step.name,
                preview_hash=preview_hash,
                approval_token_id=approval_token_id,
                replay_bundle_ref=replay_bundle_ref,
            )
            self._save_checkpoint(
                pipeline,
                run_id,
                step.name,
                outputs,
                index,
                phase="before_effect",
                force=True,
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
        if "_causal_evidence" in output.payload:
            output.payload["_causal_evidence"] = [
                item.to_dict() if isinstance(item, CausalEvidence) else item
                for item in output.payload["_causal_evidence"]
            ]
        if effect_key and self.effect_log:
            effect_status = output.payload.pop("_effect_status", None)
            effect_detail = str(output.payload.pop("_effect_detail", "") or output.summary)
            external_ref = output.payload.pop("_external_ref", None)
            if effect_status == "planned":
                current_effect = self.effect_log.get_by_idempotency_key(effect_key)
                effect_entry = self.effect_log.plan(
                    idempotency_key=effect_key,
                    run_id=run_id,
                    pipeline=pipeline.name,
                    action=pipeline.effect_steps.get(step.name, step.name),
                    target=str(payload.get("target") or payload.get("title") or run_id),
                    detail=effect_detail,
                    step_id=step.name,
                    preview_hash=preview_hash,
                    approval_token_id=approval_token_id,
                    replay_bundle_ref=current_effect.replay_bundle_ref if current_effect else "",
                )
            elif effect_status == "unknown":
                effect_entry = self.effect_log.mark_unknown(effect_key, effect_detail)
            elif effect_status == "failed":
                effect_entry = self.effect_log.mark_failed(effect_key, effect_detail)
            elif output.succeeded:
                effect_entry = self.effect_log.mark_succeeded(
                    effect_key,
                    output.summary,
                    external_ref=str(external_ref) if external_ref else None,
                )
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
        preview_hash: str,
        payload: dict[str, Any],
    ) -> StepOutput | None:
        risk = pipeline.risk_actions.get(step.name)
        if not risk or not grant_required(risk):  # type: ignore[arg-type]
            return None
        if self.approval_store and self.approval_store.find_grant(
            action=step.name,
            risk=risk,
            scope=pipeline.name,
            preview_hash=preview_hash,
        ):
            return None
        if self.approval_store:
            capacity = self.approval_store.capacity_state()
            if capacity["auto_pause_noncritical"] and self._is_noncritical_approval(risk, payload):
                return StepOutput(
                    payload={
                        "_outcome": "blocked_preflight",
                        "_what_failed": "approval queue over budget: auto-paused non-critical approval request",
                        "_approval_capacity": capacity,
                    },
                    summary="approval queue over budget; non-critical approval auto-paused",
                    succeeded=False,
                )
            request = self.approval_store.request(
                ApprovalRequest(
                    action=step.name,
                    risk=risk,  # type: ignore[arg-type]
                    scope=pipeline.name,
                    reason=f"{pipeline.name}.{step.name} requires {risk} approval",
                    run_id=run_id,
                    preview_hash=preview_hash,
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

    def _is_noncritical_approval(self, risk: str, payload: dict[str, Any]) -> bool:
        if payload.get("approval_critical") or payload.get("critical") or payload.get("emergency"):
            return False
        return risk in {"publish_public", "code_config", "memory_kernel"}

    def _approval_token_id(self, pipeline: Pipeline, step: Step, preview_hash: str) -> str | None:
        risk = pipeline.risk_actions.get(step.name)
        if not risk or not grant_required(risk):  # type: ignore[arg-type]
            return None
        if not self.approval_store:
            return None
        grant = self.approval_store.find_grant(
            action=step.name,
            risk=risk,
            scope=pipeline.name,
            preview_hash=preview_hash,
        )
        return grant.grant_id if grant else None

    def _effect_key(self, pipeline: Pipeline, step: Step, run_id: str, payload: dict[str, Any]) -> str | None:
        if step.name in pipeline.effect_steps:
            target = str(payload.get("target") or payload.get("title") or run_id)
            return f"{pipeline.name}:{step.name}:{target}"
        return None

    def _effect_preview_hash(self, payload: dict[str, Any]) -> str:
        preview = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(preview.encode("utf-8")).hexdigest()

    def _write_replay_bundle(
        self,
        *,
        pipeline: Pipeline,
        step: Step,
        run_id: str,
        action_type: str,
        target: str,
        idempotency_key: str,
        preview_hash: str,
        approval_token_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        base = self.artifact_root or (self.effect_log.path.parent if self.effect_log else Path("data/v3"))
        directory = Path(base) / "effect_replay_bundles"
        directory.mkdir(parents=True, exist_ok=True)
        safe_key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]
        path = directory / f"{run_id}-{step.name}-{safe_key}.json"
        bundle = {
            "run_id": run_id,
            "pipeline": pipeline.name,
            "step_id": step.name,
            "action_type": action_type,
            "target": target,
            "idempotency_key": idempotency_key,
            "preview_hash": preview_hash,
            "approval_token_id": approval_token_id,
            "payload_hash": self._effect_preview_hash(payload),
            "payload": _redact_replay_payload(payload),
            "compensation": _compensation_for_action(action_type),
        }
        path.write_text(json.dumps(bundle, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return str(path)

    def _save_checkpoint(
        self,
        pipeline: Pipeline,
        run_id: str,
        step_name: str,
        outputs: dict[str, Any],
        index: int,
        *,
        phase: str = "after_step",
        force: bool = False,
    ) -> None:
        if self.checkpoint_store is None:
            return
        if not force and (pipeline.checkpoint_every <= 0 or (index + 1) % pipeline.checkpoint_every != 0):
            return
        self.checkpoint_store.save(
            Checkpoint(run_id=run_id, pipeline=pipeline.name, step=step_name, outputs=outputs, phase=phase)
        )

    def _resume_index(self, pipeline: Pipeline, checkpoint: Checkpoint | None) -> int:
        if checkpoint is None:
            return 0
        try:
            index = pipeline.step_index(checkpoint.step)
            return index if checkpoint.phase == "before_effect" else index + 1
        except KeyError:
            return 0


def _redact_replay_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("secret", "token", "password", "api_key", "authorization")):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_replay_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_replay_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_replay_payload(item) for item in value]
    return value


def _compensation_for_action(action_type: str) -> dict[str, str]:
    lowered = action_type.lower()
    if "publish" in lowered or "post" in lowered:
        return {
            "strategy": "unpublish_or_mark_retracted",
            "rollback_note": "use provider reconciliation ref to remove or mark the public artifact as corrected",
        }
    if "email" in lowered or "send" in lowered:
        return {
            "strategy": "impossible",
            "rollback_note": "send follow-up correction; the original send cannot be unsent",
        }
    if "file" in lowered:
        return {"strategy": "restore_backup", "rollback_note": "restore the recorded pre-write backup"}
    if "memory" in lowered or "compact" in lowered:
        return {"strategy": "rollback_to_snapshot", "rollback_note": "restore from rollback pointer or archived memory"}
    if "deploy" in lowered or "promotion" in lowered or "rollback" in lowered:
        return {"strategy": "compensating_deployment_action", "rollback_note": "run configured rollback adapter"}
    return {"strategy": "manual_reconcile", "rollback_note": "review effect log and provider state before retry"}
