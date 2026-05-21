"""Behaviorally visible Communication pipeline.

This is the Phase-1 migration proof: the first run writes relationship memory;
the second run receives it in the snapshot and changes its reply behavior.
"""

from __future__ import annotations

from mira.agents.base import StepInput, StepOutput
from mira.engine.pipeline import Pipeline, Step, Trigger
from mira.kernel.causal import CausalEvidence
from mira.kernel.delta import MemoryAction
from mira.kernel.snapshot import MemorySnapshot


def _message_intake(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    message = input.payload.get("message", "")
    return StepOutput(payload={"message": message})


def _privacy(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    message = input.prior_outputs["message_intake"]["message"]
    sensitive = any(term in message.lower() for term in ("password", "api key", "secret"))
    return StepOutput(payload={"sensitive": sensitive}, succeeded=not sensitive)


def _classify_route(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    message = input.prior_outputs["message_intake"]["message"]
    route = "coder" if any(term in message.lower() for term in ("implement", "bug", "code")) else "writer"
    return StepOutput(payload={"route": route})


def _execute(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    message = input.prior_outputs["message_intake"]["message"]
    wants_concise = any("concise" in hint.lower() for hint in memory.hints)
    prefix = "Short answer:" if wants_concise else "I read this as:"
    reply = f"{prefix} {message.strip()}"
    payload = {"reply": reply, "used_memory": wants_concise}
    if wants_concise and memory.causal_context:
        payload["_causal_evidence"] = [
            CausalEvidence(
                memory_id=memory.causal_context[0],
                level="L3",
                reason="prior communication memory changed the response prefix to concise status format",
                run_id=input.run_id,
                pipeline=input.pipeline,
                effect_ids=[f"effect:{input.run_id}:concise_reply"],
            )
        ]
    return StepOutput(payload=payload)


def _quality(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    reply = input.prior_outputs["execute"]["reply"]
    return StepOutput(payload={"quality": "ok"}, succeeded=bool(reply.strip()))


def _reply(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    reply = input.prior_outputs["execute"]["reply"]
    actions = [
        MemoryAction(
            "update_relationship",
            "relationship:wa",
            "WA prefers concise output when asking for implementation status.",
        )
    ]
    return StepOutput(
        payload={
            "reply": reply,
            "_memory_actions": actions,
            "_what_happened": "Handled a WA communication request",
            "_what_mattered": "The interaction exposed a stable response preference",
            "_what_changed": "Future communication snapshots include WA's concise-output preference",
            "_outcome": "replied",
        }
    )


def _collect_memory_fields(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    previous = input.prior_outputs["reply"]
    return StepOutput(payload=previous)


def build_communication_pipeline() -> Pipeline:
    return Pipeline(
        name="communication",
        trigger=Trigger("event", "message from iPhone / app thread"),
        steps=[
            Step("message_intake", "deterministic", action=_message_intake),
            Step("privacy", "policy", on_fail="abort", action=_privacy),
            Step("classify_route", "deterministic", action=_classify_route),
            Step("execute", "deterministic", action=_execute),
            Step("output_quality", "eval", action=_quality),
            Step("reply", "deterministic", action=_reply),
            Step("memory_fields", "memory", action=_collect_memory_fields),
        ],
        priority=10,
        version=1,
        max_duration_s=120,
        checkpoint_every=1,
        memory_class="operational",
        involved_skills=["intent_inference", "response_synthesis"],
    )
