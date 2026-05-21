"""Deterministic V3.1 workflow actions for the MVP executable packs."""

from __future__ import annotations

from pathlib import Path

from mira.agents.base import StepInput, StepOutput
from mira.kernel.causal import CausalEvidence
from mira.kernel.delta import MemoryAction
from mira.kernel.snapshot import MemorySnapshot


def generic_step(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    return StepOutput(payload={"status": "ok"}, summary=f"{input.step} completed")


def system_health_probe(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    failures = list(input.payload.get("failures", []))
    status = "degraded" if failures else "ok"
    return StepOutput(payload={"status": status, "failures": failures}, succeeded=True)


def system_health_record(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    probe = input.prior_outputs.get("heartbeat_processes_crash_tasks_failure_sig", {})
    failures = list(probe.get("failures", []))
    actions = []
    if failures:
        actions.append(
            MemoryAction(
                "create_scar",
                f"scar:system_health:{input.run_id}",
                "; ".join(failures),
                metadata={"evidence_ref": input.run_id},
            )
        )
    return StepOutput(
        payload={
            "_memory_actions": actions,
            "_outcome": "degraded" if failures else "healthy",
            "_what_happened": "System health check completed",
            "_what_mattered": "; ".join(failures) if failures else "No system failures detected",
            "_what_changed": "Future health checks include this run as operational context",
        }
    )


def briefing_fetch_sources(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    sources = input.payload.get("sources") or [
        {"title": "A2A trust protocol drift", "trust": "observed", "url": "local:a2a-trust"},
        {"title": "Agent memory poisoning incident pattern", "trust": "verified", "url": "local:memory-security"},
    ]
    return StepOutput(payload={"sources": sources}, summary=f"fetched {len(sources)} sources")


def briefing_write(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    sources = input.prior_outputs.get("fetch_sources_parallel", {}).get("sources", [])
    lines = ["# Intelligence Briefing", ""]
    for source in sources:
        lines.append(f"- [{source.get('trust', 'observed')}] {source.get('title')} ({source.get('url')})")
    artifact = _write_artifact(input, "briefing.md", "\n".join(lines) + "\n")
    return StepOutput(
        payload={
            "briefing": lines,
            "_artifacts": [str(artifact)],
            "_what_happened": "Generated trust-labeled intelligence briefing",
            "_what_mattered": f"{len(sources)} source items were triaged",
            "_what_changed": "Future briefings can compare source trust and interest fit",
        },
        summary="briefing artifact written",
    )


def article_draft(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    title = input.payload.get("title", "Mira V3.1 Evidence Spine")
    body = [
        f"# {title}",
        "",
        "Mira V3.1 should prove every durable behavior through ledger, effect log, approval, and causal trace.",
        "",
        "The useful threshold is not autonomy by volume. It is autonomy with receipts.",
    ]
    artifact = _write_artifact(input, "article.md", "\n".join(body) + "\n")
    payload = {
        "title": title,
        "draft": body,
        "_artifacts": [str(artifact)],
        "_what_happened": "Drafted an article artifact",
        "_what_mattered": "The output is inspectable before any public publish step",
        "_what_changed": "Article workflow now defaults to artifact-first execution",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior article workflow context changed this draft into artifact-first V3.1 evidence-spine framing",
        )
    )
    return StepOutput(
        payload=payload,
        summary="article draft artifact written",
    )


def article_publish_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    connectors = input.payload.get("connectors", {})
    if not connectors.get("substack"):
        return StepOutput(
            payload={
                "status": "draft_only",
                "_skip_steps": ["publish_substack_idempotent", "social_promo_idempotent"],
            },
            summary="substack unavailable; kept as draft",
        )
    return StepOutput(payload={"status": "ready_to_publish"}, summary="substack connector available")


def a2a_experiment(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    question = input.payload.get("question", "Which trust boundary makes A2A delegation auditable?")
    artifact = _write_artifact(
        input,
        "a2a_trust_experiment.md",
        "\n".join(
            [
                "# A2A Trust Experiment",
                "",
                f"Question: {question}",
                "Experiment: compare delegated-agent outputs with and without causal evidence manifests.",
                "Tool idea: manifest validator that rejects unsupported trust claims.",
                "Public feedback plan: publish the manifest format and collect implementation objections.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_memory_actions": [
            MemoryAction(
                "form_hypothesis",
                "hypothesis:a2a_trust_manifest",
                "A2A trust improves when each delegated output ships with a causal evidence manifest.",
                metadata={"evidence_ref": input.run_id},
            )
        ],
        "_eval_refs": ["strategic:a2a_trust_experiment"],
        "_what_happened": "Completed an A2A trust experiment artifact",
        "_what_mattered": "The strategic loop produced a research question, tool idea, and feedback plan",
        "_what_changed": "Future strategic scorecards can count this as an A2A trust experiment",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior A2A trust experiment context changed this run into a manifest-validation follow-up",
        )
    )
    return StepOutput(
        payload=payload,
        summary="A2A trust experiment artifact written",
    )


ACTION_REGISTRY = {
    "system_health_probe": system_health_probe,
    "system_health_record": system_health_record,
    "briefing_fetch_sources": briefing_fetch_sources,
    "briefing_write": briefing_write,
    "article_draft": article_draft,
    "article_publish_guard": article_publish_guard,
    "a2a_experiment": a2a_experiment,
}


def action_for(name: str):
    return ACTION_REGISTRY.get(name, generic_step)


def _write_artifact(input: StepInput, filename: str, body: str) -> Path:
    root = Path(input.payload.get("artifact_dir", ".")).expanduser()
    target = root / input.pipeline / input.run_id / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _causal_evidence_payload(input: StepInput, memory: MemorySnapshot, reason: str) -> dict:
    if not memory.causal_context:
        return {}
    return {
        "_causal_evidence": [
            CausalEvidence(
                memory_id=memory.causal_context[0],
                level="L3",
                reason=reason,
                run_id=input.run_id,
                pipeline=input.pipeline,
                effect_ids=[f"effect:{input.run_id}:{input.step}"],
            )
        ]
    }
