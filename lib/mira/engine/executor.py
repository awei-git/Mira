"""Memory-first pipeline executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mira.agents.base import Agent, StepInput, StepOutput
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
    ):
        self.kernel_store = kernel_store
        self.ledger = ledger
        self.agents = agents or {}
        self.consolidator = consolidator or MemoryConsolidator()

    def run(
        self, pipeline: Pipeline, payload: dict[str, Any], intent: str, trigger: str = "manual"
    ) -> PipelineRunResult:
        run_id = new_run_id(pipeline.name)
        kernel = self.kernel_store.load()
        snapshot = SnapshotBuilder(self.ledger).build(
            kernel=kernel,
            pipeline=pipeline.name,
            memory_class=pipeline.memory_class,
            involved_skills=pipeline.involved_skills,
            intent=intent,
        )
        outputs, failure = self._execute_steps(pipeline, run_id, payload, snapshot)
        delta = self._build_delta(pipeline, run_id, outputs, failure)
        record = ExperienceRecord(
            id=run_id,
            pipeline=pipeline.name,
            trigger=trigger,
            intent=intent,
            outcome=outputs.get("_outcome", "completed" if failure is None else "failed"),
            delta=delta,
            causal_links=snapshot.causal_links(),
            confidence=0.8 if failure is None else 0.4,
            memory_class=pipeline.memory_class,
        )
        consolidation = self.consolidator.apply_delta(kernel, delta)
        self.ledger.append(record)
        self.kernel_store.save(kernel)
        return PipelineRunResult(run_id, record, snapshot, outputs, consolidation)

    def _execute_steps(
        self,
        pipeline: Pipeline,
        run_id: str,
        payload: dict[str, Any],
        snapshot: MemorySnapshot,
    ) -> tuple[dict[str, Any], str | None]:
        outputs: dict[str, Any] = {}
        index = 0
        loop_counts: dict[str, int] = {}
        failure: str | None = None
        while index < len(pipeline.steps):
            step = pipeline.steps[index]
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
        if step.type == "agent":
            if not step.agent or step.agent not in self.agents:
                raise ValueError(f"agent not registered: {step.agent}")
            return self.agents[step.agent].execute(step_input, snapshot)
        if step.action is None:
            return StepOutput(payload={"status": "skipped"}, summary=f"{step.name} had no action")
        result = step.action(input=step_input, memory=snapshot)
        if isinstance(result, StepOutput):
            return result
        if isinstance(result, dict):
            return StepOutput(payload=result)
        return StepOutput(payload={"result": result})

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
