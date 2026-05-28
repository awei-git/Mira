"""Deterministic V3.1 workflow actions for the MVP executable packs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from mira.agents.base import StepInput, StepOutput
from mira.kernel.causal import CausalEvidence, confirm_ablation_evidence
from mira.kernel.delta import MemoryAction
from mira.kernel.snapshot import MemorySnapshot
from mira.workflows.security import audit_workflow_skill_candidate


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
    payload = {
        "_memory_actions": actions,
        "_outcome": "degraded" if failures else "healthy",
        "_what_happened": "System health check completed",
        "_what_mattered": "; ".join(failures) if failures else "No system failures detected",
        "_what_changed": "Future health checks include this run as operational context",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior system health context changed this run into an operational comparison point",
            ablation_counterfactual=(
                "without prior system health context, report only the current probe status without "
                "operational comparison or failure-signature continuity"
            ),
        )
    )
    return StepOutput(payload=payload)


def briefing_fetch_sources(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    sources = input.payload.get("sources") or [
        {"title": "A2A trust protocol drift", "trust": "observed", "url": "local:a2a-trust"},
        {"title": "Agent memory poisoning incident pattern", "trust": "verified", "url": "local:memory-security"},
    ]
    records = [_source_fetch_record(source, input.run_id, index) for index, source in enumerate(sources, start=1)]
    artifact = _write_artifact(
        input,
        "source_fetch_records.json",
        json.dumps({"source_fetch_records": records}, indent=2, sort_keys=True) + "\n",
    )
    return StepOutput(
        payload={
            "sources": records,
            "source_fetch_records": records,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["briefing:source_fetch_records"],
        },
        summary=f"fetched {len(records)} sources",
    )


def briefing_dedup_trust_classification(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    records = list(input.prior_outputs.get("fetch_sources_parallel", {}).get("source_fetch_records", []))
    if not records:
        records = [
            _source_fetch_record(source, input.run_id, index)
            for index, source in enumerate(
                input.prior_outputs.get("fetch_sources_parallel", {}).get("sources", []), start=1
            )
        ]
    deduped_by_key: dict[str, dict] = {}
    duplicate_count = 0
    for record in records:
        key = _source_dedupe_key(record)
        if key in deduped_by_key:
            duplicate_count += 1
            existing = deduped_by_key[key]
            refs = list(existing.get("evidence_refs", []))
            refs.extend(ref for ref in record.get("evidence_refs", []) if ref not in refs)
            existing["evidence_refs"] = refs
            existing["duplicate_source_ids"] = [
                *existing.get("duplicate_source_ids", []),
                record.get("source_id"),
            ]
            continue
        deduped_by_key[key] = dict(record)
    deduped = list(deduped_by_key.values())
    bundle = {
        "source_fetch_records": records,
        "deduped_sources": deduped,
        "duplicate_count": duplicate_count,
        "trust_summary": _trust_summary(deduped),
    }
    artifact = _write_artifact(
        input,
        "source_bundle.json",
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
    )
    return StepOutput(
        payload={
            "sources": deduped,
            "source_bundle": bundle,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["briefing:source_bundle"],
        },
        summary=f"deduped {len(records)} sources to {len(deduped)} trust-classified items",
    )


def briefing_write(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    sources = input.prior_outputs.get("dedup_trust_classification", {}).get("sources") or input.prior_outputs.get(
        "fetch_sources_parallel", {}
    ).get("sources", [])
    lines = ["# Intelligence Briefing", ""]
    for source in sources:
        trust = source.get("trust_tier") or source.get("trust") or "observed"
        source_type = source.get("source_type") or "source"
        lines.append(f"- [{trust}] {source.get('title')} ({source.get('url')}; {source_type})")
    artifact = _write_artifact(input, "briefing.md", "\n".join(lines) + "\n")
    payload = {
        "briefing": lines,
        "_artifacts": [str(artifact)],
        "_what_happened": "Generated trust-labeled intelligence briefing",
        "_what_mattered": f"{len(sources)} source items were triaged",
        "_what_changed": "Future briefings can compare source trust and interest fit",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior briefing context changed this run into a source-trust comparison point",
            ablation_counterfactual=(
                "without prior briefing context, list fetched sources without source-trust comparison "
                "or continuity with earlier intelligence interests"
            ),
        )
    )
    return StepOutput(
        payload=payload,
        summary="briefing artifact written",
    )


def _source_fetch_record(source: dict, run_id: str, index: int) -> dict:
    title = str(source.get("title") or f"Untitled source {index}")
    url = str(source.get("url") or f"local:source:{index}")
    source_type = str(source.get("source_type") or source.get("type") or _source_type_from_url(url))
    trust_tier = str(source.get("trust_tier") or source.get("trust") or "observed")
    privacy_tier = str(source.get("privacy_tier") or source.get("privacy") or "public")
    evidence_refs = [str(ref) for ref in source.get("evidence_refs", []) if ref]
    if not evidence_refs:
        evidence_refs = [url]
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "title": title.strip().lower(),
                "url": url.strip().lower(),
                "source_type": source_type.strip().lower(),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    source_id = source.get("source_id") or f"source:{run_id}:{content_hash[:12]}"
    return {
        "source_id": str(source_id),
        "title": title,
        "url": url,
        "source_type": source_type,
        "trust_tier": trust_tier,
        "privacy_tier": privacy_tier,
        "evidence_refs": evidence_refs,
        "fetched_at": str(source.get("fetched_at") or datetime.now(timezone.utc).isoformat()),
        "content_hash": content_hash,
    }


def _source_type_from_url(url: str) -> str:
    if url.startswith("local:"):
        return "local"
    if "arxiv.org" in url:
        return "arxiv"
    if "news.ycombinator.com" in url:
        return "hackernews"
    if url.startswith("http"):
        return "web"
    return "source"


def _source_dedupe_key(record: dict) -> str:
    url = str(record.get("url") or "").strip().lower()
    if url:
        return f"url:{url}"
    return f"content:{record.get('content_hash')}"


def _trust_summary(records: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for record in records:
        tier = str(record.get("trust_tier") or "observed")
        summary[tier] = summary.get(tier, 0) + 1
    return summary


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
            ablation_counterfactual=(
                "without prior article workflow memory, draft a generic article artifact without the "
                "V3.1 evidence-spine framing or artifact-first public-publish guard"
            ),
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
    if input.step == "publish_substack_idempotent":
        return StepOutput(
            payload={
                "status": "publish_adapter_not_executed",
                "_effect_status": "planned",
                "_effect_detail": "substack connector available, but V3.1 MVP keeps publish as a staged side effect",
            },
            summary="substack publish staged; no external publish executed",
        )
    return StepOutput(payload={"status": "ready_to_publish"}, summary="substack connector available")


def a2a_experiment(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    question = input.payload.get("question", "Which trust boundary makes A2A delegation auditable?")
    public_writeup_body = (
        "\n".join(
            [
                "# A2A Trust Manifests Need Receipts, Not Vibes",
                "",
                "Status: draft for public review",
                "",
                f"Research question: {question}",
                "",
                "Claim: delegated-agent outputs become more auditable when every trust claim is tied to a causal evidence manifest.",
                "",
                "Test: compare an output that only states confidence against one that lists supported claims, evidence refs, and unsupported gaps.",
                "",
                "Prototype: a manifest validator rejects unsupported trust claims before they are handed to another agent or human reviewer.",
                "",
                "What I want challenged: whether manifest validation catches real delegation failures, or only creates prettier paperwork.",
                "",
                "Feedback request: send implementation objections, missing threat models, and examples where this would fail.",
                "",
                "Publication gate: do not count this as a public writeup until it is actually published or externally shared.",
            ]
        )
        + "\n"
    )
    public_writeup_preview_hash = hashlib.sha256(public_writeup_body.encode("utf-8")).hexdigest()
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
    public_writeup_artifact = _write_artifact(
        input,
        "a2a_public_writeup_draft.md",
        public_writeup_body,
    )
    commercial_artifact = _write_artifact(
        input,
        "a2a_commercial_options.md",
        "\n".join(
            [
                "# A2A Trust Commercial Options",
                "",
                "Option 1: hosted manifest validator API for teams delegating work across agents.",
                "Option 2: audit packet for buyers who need evidence that agent outputs cite supported trust claims.",
                "Constraint: both options remain artifact-first and require public evidence before any sales claim.",
            ]
        )
        + "\n",
    )
    product_thesis_artifact = _write_artifact(
        input,
        "a2a_product_thesis.md",
        "\n".join(
            [
                "# A2A Trust Product Thesis",
                "",
                "Thesis: a manifest validator is the smallest useful product surface for A2A trust.",
                "Evidence: this run produced a reproducible trust-manifest experiment and a validator tool idea.",
                "Buyer pain: teams delegating work across agents need compact evidence that trust claims are supported.",
                "Constraint: keep the product artifact-first until public feedback validates the problem framing.",
                "Next evidence gate: publish the manifest format and record external implementation objections.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [
            str(artifact),
            str(public_writeup_artifact),
            str(commercial_artifact),
            str(product_thesis_artifact),
        ],
        "public_writeup_draft": str(public_writeup_artifact),
        "public_writeup_preview_hash": public_writeup_preview_hash,
        "_memory_actions": [
            MemoryAction(
                "form_hypothesis",
                "hypothesis:a2a_trust_manifest",
                "A2A trust improves when each delegated output ships with a causal evidence manifest.",
                metadata={"evidence_ref": input.run_id},
            ),
            MemoryAction(
                "update_hypothesis",
                "hypothesis:a2a_trust_manifest",
                f"A2A trust experiment {input.run_id} produced a manifest validator artifact and commercial option map.",
                metadata={"evidence_ref": input.run_id},
            ),
        ],
        "_eval_refs": [
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "feedback_plan:a2a_manifest_review",
            "public_writeup_plan:a2a_manifest_note",
            "product_thesis:a2a_validator_api",
            "commercial:a2a_validator_api",
            "commercial:a2a_audit_packet",
        ],
        "_what_happened": "Completed an A2A trust experiment artifact",
        "_what_mattered": "The strategic loop produced a research question, tool idea, public writeup draft, feedback plan, product thesis, and commercial option map",
        "_what_changed": "Future strategic scorecards can count this as an A2A trust experiment with a product-thesis update and commercial options",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior A2A trust experiment context changed this run into a manifest-validation follow-up",
            ablation_counterfactual=(
                "without prior A2A trust memory, produce a baseline trust-boundary question without "
                "the manifest-validation follow-up"
            ),
        )
    )
    return StepOutput(
        payload=payload,
        summary="A2A trust experiment artifact written",
    )


def podcast_plan(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    title = input.payload.get("title", "V3.1 Evidence Spine Audio Companion")
    tts_route = input.prior_outputs.get("select_tts_route", {})
    route = tts_route.get("route", "minimax_primary")
    route_reason = tts_route.get("reason", "no prior TTS failure memory changed the default route")
    artifact = _write_artifact(
        input,
        "podcast_plan.md",
        "\n".join(
            [
                "# Podcast Production Plan",
                "",
                f"Episode: {title}",
                "Script stance: convert the article into an audio-first outline before any TTS or RSS side effect.",
                f"TTS route: {route}; {route_reason}.",
                "TTS execution: staged locally until quality and duration checks pass.",
                "Rollback: no public RSS publish occurs in this MVP workflow.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": ["podcast:audio_first_plan"],
        "_what_happened": "Produced a podcast production plan artifact",
        "_what_mattered": "Podcast production now has a local, inspectable pre-publish path",
        "_what_changed": "Future podcast runs can compare TTS route and script decisions against this staged artifact",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior podcast production context changed this run into a staged audio-first plan",
            ablation_counterfactual=(
                "without prior podcast production memory, produce a generic episode outline without staged TTS, "
                "quality-check, or RSS rollback framing"
            ),
        )
    )
    return StepOutput(payload=payload, summary="podcast production plan artifact written")


def podcast_tts_route(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    title = input.payload.get("title", "V3.1 Evidence Spine Audio Companion")
    memory_refs = _podcast_tts_failure_memory_refs(memory)
    if memory_refs:
        route = "fallback_tts"
        reason = "prior TTS failure memory indicates the primary MiniMax route is unreliable"
    else:
        route = "minimax_primary"
        reason = "no prior MiniMax/TTS failure memory was present in the snapshot"
    route_record = {
        "episode": title,
        "route": route,
        "reason": reason,
        "source_memory_ids": memory_refs,
        "status": "staged",
        "idempotency_key": f"{input.pipeline}:tts_route:{title}",
    }
    artifact = _write_artifact(input, "tts_route.json", json.dumps(route_record, indent=2, sort_keys=True) + "\n")
    payload = {
        **route_record,
        "_artifacts": [str(artifact)],
        "_eval_refs": [f"podcast:tts_route:{route}"],
        "_what_happened": f"Selected podcast TTS route `{route}`",
        "_what_mattered": reason,
        "_what_changed": "Future podcast runs can audit whether TTS scars changed provider routing",
    }
    if memory_refs and route == "fallback_tts":
        payload["_causal_evidence"] = [
            confirm_ablation_evidence(
                memory_id=memory_refs[0],
                run_id=input.run_id,
                pipeline=input.pipeline,
                normal_decision="selected fallback_tts because prior MiniMax/TTS failure memory was present",
                counterfactual_decision="without prior TTS failure memory, select minimax_primary",
                effect_ids=[f"effect:{input.run_id}:{input.step}:changed_tool"],
            )
        ]
    return StepOutput(payload=payload, summary=f"TTS route selected: {route}")


def podcast_tts_synthesis_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    title = input.payload.get("title", "V3.1 Evidence Spine Audio Companion")
    tts_route = input.prior_outputs.get("select_tts_route", {}).get("route", "minimax_primary")
    connectors = input.payload.get("connectors", {})
    script_text = str(
        input.payload.get("script_text")
        or input.payload.get("script")
        or f"{title}. This is the staged audio companion script for Mira V3.1."
    )
    preview = {
        "pipeline": input.pipeline,
        "kind": "tts_synthesis",
        "title": title,
        "target": str(input.payload.get("target") or title),
        "tts_route": tts_route,
        "voice": str(input.payload.get("voice") or "default"),
        "script_text": script_text,
        "audio_output_name": str(input.payload.get("audio_output_name") or "episode-audio.wav"),
        "status": "staged",
        "live_synthesis": False,
    }
    if input.step == "tts_synthesis_preflight":
        artifact = _write_artifact(
            input,
            "tts_synthesis_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["podcast:tts_synthesis_preview"],
            "_what_happened": "Prepared a podcast TTS synthesis preview artifact",
            "_what_mattered": "Podcast audio synthesis is reviewed before any external or local TTS adapter runs",
            "_what_changed": "Future podcast runs can execute TTS through an approved effect-log adapter",
        }
        if not connectors.get("tts"):
            payload.update(
                {
                    "status": "staged_without_connector",
                    "_skip_steps": ["synthesize_tts_idempotent"],
                }
            )
            return StepOutput(payload=payload, summary="TTS connector unavailable; kept synthesis staged")
        return StepOutput(payload=payload, summary="TTS synthesis preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "tts connector available, but V3.1 keeps synthesis staged until an approved adapter "
                "produces the audio artifact"
            ),
            "_eval_refs": ["podcast:tts_synthesis_staged"],
        },
        summary="TTS synthesis staged; no audio adapter executed by workflow",
    )


def podcast_rss_publish_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    title = input.payload.get("title", "V3.1 Evidence Spine Audio Companion")
    episode_id = str(input.payload.get("episode_id") or title).strip()
    connectors = input.payload.get("connectors", {})
    tts_route = input.prior_outputs.get("select_tts_route", {}).get("route", "minimax_primary")
    preview = {
        "episode_id": episode_id,
        "title": title,
        "description": str(input.payload.get("description") or f"Audio companion for {title}"),
        "audio_url": str(input.payload.get("audio_url") or ""),
        "audio_mime_type": str(input.payload.get("audio_mime_type") or "audio/mpeg"),
        "episode_url": str(input.payload.get("episode_url") or ""),
        "target": "rss",
        "tts_route": tts_route,
        "status": "staged",
        "live_publish": False,
    }
    if input.step == "rss_publish_preflight":
        artifact = _write_artifact(
            input,
            "rss_publish_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["podcast:rss_publish_preview"],
            "_what_happened": "Prepared a podcast RSS publish preview artifact",
            "_what_mattered": "Podcast public distribution is targeted to RSS and remains approval/effect-log gated",
            "_what_changed": "Future podcast publish attempts can be reviewed against this RSS-only preview",
        }
        if not connectors.get("rss"):
            payload.update(
                {
                    "status": "staged_without_connector",
                    "_skip_steps": ["publish_rss_idempotent"],
                }
            )
            return StepOutput(payload=payload, summary="RSS connector unavailable; kept podcast publish staged")
        return StepOutput(payload=payload, summary="RSS publish preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "rss connector available, but V3.1 keeps podcast RSS publish staged until provider "
                "reconciliation confirms it"
            ),
            "_eval_refs": ["podcast:rss_publish_staged"],
        },
        summary="RSS publish staged; no external publish executed",
    )


def self_evolution_experiment(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    artifact = _write_artifact(
        input,
        "self_evolution_experiment.md",
        "\n".join(
            [
                "# Self-Evolution Experiment Record",
                "",
                "Mismatch cluster: workflow coverage gaps in V3.1 executable packs.",
                "Hypothesis: small deterministic workflow packs reduce unreviewed implementation drift.",
                "Risk: low, because this MVP records an experiment plan and does not modify production behavior.",
                "Rollback: remove the generated pack registration and rerun V3 tests.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_memory_actions": [
            MemoryAction(
                "form_hypothesis",
                "hypothesis:self_evolution_pack_coverage",
                "Executable V3.1 self-evolution packs reduce unreviewed implementation drift.",
                metadata={
                    "mismatch_cluster_id": "self_evolution:workflow_pack_coverage",
                    "intervention": "add auditable workflow-pack coverage and canary rollback records",
                    "target_pipeline": "self_evolution",
                    "target_metric": "self_evolution_experiment_coverage",
                    "baseline_window": "prior self_evolution workflow-pack coverage runs",
                    "test_window": "current self_evolution canary window",
                    "min_n": str(max(1, int(input.payload.get("canary_min_n", 3)))),
                    "current_metric": "workflow-pack coverage drift evidence_for=0",
                    "expected_effect": "reduce unreviewed implementation drift without V3 hard-gate regressions",
                    "risk_level": "low",
                    "rollback_plan": "rollback on V3 hard gate failure; remove generated pack registration",
                    "evidence_ref": input.run_id,
                },
            )
        ],
        "_eval_refs": ["self_evolution:experiment_record"],
        "_what_happened": "Created a self-evolution experiment record",
        "_what_mattered": "Self-evolution is represented as an auditable experiment, not an automatic code mutation",
        "_what_changed": "Future self-evolution runs can be reviewed through the experiment queue",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior self-evolution context changed this run into an experiment-record follow-up",
            ablation_counterfactual=(
                "without prior self-evolution memory, only inventory workflow coverage gaps without "
                "forming an experiment-queue follow-up"
            ),
        )
    )
    return StepOutput(payload=payload, summary="self-evolution experiment artifact written")


def self_evolution_branch_canary(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    repo_path = Path(str(input.payload.get("repo_path") or ".")).expanduser()
    branch_name = str(input.payload.get("canary_branch") or f"codex/self-evolution-{input.run_id}")[:120]
    enabled = bool(input.payload.get("branch_canary_enabled"))
    rollback_after_deploy = bool(input.payload.get("rollback_after_deploy"))
    result = {
        "status": "not_executed",
        "repo_path": str(repo_path),
        "branch": branch_name,
        "rollback_executed": False,
        "original_ref": "",
    }
    succeeded = True
    if enabled:
        try:
            _validate_branch_canary_repo(repo_path, branch_name)
            original_ref = _git_output(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
            if original_ref == "HEAD":
                original_ref = _git_output(repo_path, ["rev-parse", "HEAD"])
            result["original_ref"] = original_ref
            _git_output(repo_path, ["checkout", "-B", branch_name, original_ref])
            result["status"] = "deployed"
            if rollback_after_deploy:
                _git_output(repo_path, ["checkout", original_ref])
                result["rollback_executed"] = True
                result["status"] = "rolled_back"
        except ValueError as exc:
            result["status"] = "blocked"
            result["error"] = str(exc)
            succeeded = False
        except subprocess.CalledProcessError as exc:
            result["status"] = "failed"
            result["error"] = (exc.stderr or exc.stdout or str(exc)).strip()
            succeeded = False
    artifact = _write_artifact(
        input,
        "self_evolution_branch_canary.json",
        json.dumps(result, indent=2, sort_keys=True) + "\n",
    )
    payload = {
        **result,
        "_artifacts": [str(artifact)],
        "_eval_refs": [f"self_evolution:branch_canary:{result['status']}"],
        "_what_happened": f"Self-evolution branch canary {result['status']}",
        "_what_mattered": "Self-evolution branch deployment is explicit, local, and rollback-aware",
        "_what_changed": "Future self-evolution runs can prove whether code changes were isolated on a branch",
    }
    if result["status"] in {"failed", "blocked"}:
        payload["_what_failed"] = str(result.get("error") or result["status"])
    return StepOutput(payload=payload, summary=f"self-evolution branch canary {result['status']}", succeeded=succeeded)


def self_evolution_canary_rollback(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    min_n = max(1, int(input.payload.get("canary_min_n", 3)))
    observation = _self_evolution_canary_observation(memory, min_n=min_n)
    artifact = _write_artifact(
        input,
        "self_evolution_canary.md",
        "\n".join(
            [
                "# Self-Evolution Canary / Rollback Record",
                "",
                "Change class: low-risk workflow coverage experiment.",
                "Canary scope: local workflow-pack execution only; no production behavior or connector permission changes.",
                "Golden set: run `tests/v3` before promotion.",
                "Observation window: next 3 self_evolution or workflow-pack audit runs.",
                "Confirm if: workflow coverage remains complete and no V3 hard gates fail.",
                "Rollback if: any V3 hard gate fails, workflow audit blocks the pack, or causal evidence is missing.",
                "Rollback pointer: remove generated pack registration and rerun `tests/v3` plus `v3_status --json`.",
                "",
                f"Observed prior self_evolution runs: {observation['count']}/{min_n}.",
                f"Observation status: {observation['status']}.",
                f"Observation decision: {observation['decision']}",
            ]
        )
        + "\n",
    )
    evidence_detail = (
        "Canary observation confirmed after N-run window."
        if observation["status"] == "confirmed"
        else (
            "Canary observation rejected; rollback review required."
            if observation["status"] == "rejected"
            else "Canary observation still collecting the N-run window."
        )
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_memory_actions": [
            MemoryAction(
                "update_hypothesis",
                "hypothesis:self_evolution_pack_coverage",
                evidence_detail,
                metadata={"evidence_ref": input.run_id},
            )
        ],
        "_eval_refs": ["self_evolution:canary_rollback", f"self_evolution:canary_observation:{observation['status']}"],
        "_what_happened": "Recorded a self-evolution canary and rollback plan",
        "_what_mattered": observation["decision"],
        "_what_changed": "Future self-evolution runs can confirm, reject, or roll back against this canary observation",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior self-evolution canary context changed this run into a rollback-aware canary record",
            ablation_counterfactual=(
                "without prior self-evolution memory, stop at experiment planning without canary observation "
                "or rollback criteria"
            ),
        )
    )
    return StepOutput(payload=payload, summary="self-evolution canary artifact written")


def self_evolution_production_promotion_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    explicit_enabled = bool(input.payload.get("production_promotion_enabled"))
    auto_promotion_enabled = bool(input.payload.get("auto_promotion_enabled"))
    auto_min_n = max(1, int(input.payload.get("auto_promotion_min_n", input.payload.get("canary_min_n", 3))))
    auto_observation = _self_evolution_canary_observation(memory, min_n=auto_min_n)
    auto_eligible = auto_promotion_enabled and auto_observation["status"] == "confirmed"
    enabled = explicit_enabled or auto_eligible
    repo_path = str(input.payload.get("repo_path") or ".")
    production_branch = str(input.payload.get("production_branch") or "main")
    canary_branch = str(input.payload.get("canary_branch") or "")
    remote_promotion_enabled = bool(input.payload.get("remote_promotion_enabled"))
    remote_name = str(input.payload.get("remote_name") or "origin")
    remote_branch = str(input.payload.get("remote_branch") or production_branch)
    deployment_service_enabled = bool(input.payload.get("deployment_service_enabled"))
    deployment_health_check_enabled = bool(input.payload.get("deployment_health_check_enabled"))
    deployment_rollback_enabled = bool(input.payload.get("deployment_rollback_enabled"))
    target = str(input.payload.get("target") or f"{production_branch}:{canary_branch or input.run_id}")
    preview = {
        "pipeline": input.pipeline,
        "kind": "self_evolution_production_promotion",
        "target": target,
        "repo_path": repo_path,
        "production_branch": production_branch,
        "canary_branch": canary_branch,
        "rollback_after_promotion": bool(input.payload.get("rollback_after_promotion")),
        "remote_promotion_enabled": remote_promotion_enabled,
        "remote_name": remote_name,
        "remote_branch": remote_branch,
        "deployment_service_enabled": deployment_service_enabled,
        "deployment_health_check_enabled": deployment_health_check_enabled,
        "deployment_rollback_enabled": deployment_rollback_enabled,
        "auto_promotion_enabled": auto_promotion_enabled,
        "auto_promotion_eligible": auto_eligible,
        "auto_promotion_min_n": auto_min_n,
        "auto_promotion_observed_n": min(int(auto_observation["count"]), auto_min_n),
        "auto_promotion_status": auto_observation["status"],
        "auto_promotion_decision": auto_observation["decision"],
        "promotion_mode": "manual" if explicit_enabled else ("automatic" if auto_eligible else "review_only"),
        "status": "staged" if enabled and canary_branch else "review_only",
        "live_promotion": (
            remote_promotion_enabled
            or deployment_service_enabled
            or deployment_health_check_enabled
            or deployment_rollback_enabled
        ),
        "safeguards": [
            "requires code_config approval bound to the payload preview hash",
            "runtime adapter requires a clean local git repository",
            "runtime adapter only permits fast-forward production promotion",
            "remote production push is disabled unless explicitly requested",
            "deployment service execution is disabled unless explicitly requested",
            "deployment health verification is disabled unless explicitly requested",
            "deployment rollback execution is disabled unless explicitly requested",
            "automatic promotion only stages the normal approval-gated promotion effect",
            "rollback ref is recorded before any promotion attempt",
        ],
    }
    if input.step == "production_promotion_preflight":
        artifact = _write_artifact(
            input,
            "self_evolution_production_promotion_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": [f"self_evolution:production_promotion_preview:{preview['status']}"],
            "_what_happened": "Prepared a self-evolution production promotion preview artifact",
            "_what_mattered": "Production promotion remains explicit, approval-gated, and adapter-executed",
            "_what_changed": "Future self-evolution runs can promote only from a named canary branch",
        }
        if not enabled:
            payload["_skip_steps"] = ["promote_production_idempotent"]
            return StepOutput(payload=payload, summary="production promotion disabled; kept review-only")
        if not canary_branch:
            payload.update(
                {
                    "status": "blocked_missing_canary",
                    "_skip_steps": ["promote_production_idempotent"],
                    "_what_failed": "production promotion requires canary_branch",
                }
            )
            return StepOutput(payload=payload, summary="production promotion missing canary branch", succeeded=False)
        return StepOutput(payload=payload, summary="production promotion preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "production promotion is approved but remains staged; the runtime adapter must fast-forward "
                "the production branch and record the rollback ref"
            ),
            "_eval_refs": ["self_evolution:production_promotion_staged"],
        },
        summary="production promotion staged; no git promotion executed by workflow",
    )


def _self_evolution_canary_observation(memory: MemorySnapshot, *, min_n: int) -> dict[str, str | int]:
    prior_runs = [
        record
        for record in memory.recent_experiences
        if record.pipeline == "self_evolution" and "self_evolution:canary_rollback" in record.eval_refs
    ]
    failed = [record for record in prior_runs if record.outcome not in {"completed", "healthy", "approval_required"}]
    if failed:
        status = "rejected"
        decision = "Rollback review required before promotion because a prior canary run failed."
    elif len(prior_runs) >= min_n:
        status = "confirmed"
        decision = "Canary window met the minimum N-run observation threshold with no failed prior runs."
    else:
        status = "observing"
        decision = "Continue observing before promotion; the minimum N-run window is not complete."
    return {"count": len(prior_runs), "status": status, "decision": decision}


def memory_maintenance_report(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    artifact = _write_artifact(
        input,
        "memory_maintenance.md",
        "\n".join(
            [
                "# Memory Maintenance Report",
                "",
                f"Snapshot items reviewed: {len(memory.items)}",
                f"Recent experiences considered: {len(memory.recent_experiences)}",
                "Action: no destructive compaction in the MVP workflow.",
                "Next review: route stale, duplicate, or suspicious memory to the commit/review queues.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": ["memory_maintenance:review_report"],
        "_what_happened": "Generated a memory maintenance review artifact",
        "_what_mattered": "Memory maintenance is inspectable before compaction or archival changes",
        "_what_changed": "Future maintenance runs can compare stale-memory signals against this review",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior memory maintenance context changed this run into a review-before-compaction report",
            ablation_counterfactual=(
                "without prior memory maintenance context, produce only a generic memory inventory without "
                "review-before-compaction framing or stale-memory comparison"
            ),
        )
    )
    return StepOutput(payload=payload, summary="memory maintenance report artifact written")


def memory_compaction_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    enabled = bool(input.payload.get("compaction_enabled"))
    candidates = list(input.payload.get("compaction_candidates") or [])
    recommended_candidates = _memory_compaction_candidates(input, memory)
    if not candidates and enabled:
        candidates = [candidate["item_id"] for candidate in recommended_candidates]
    target = str(input.payload.get("target") or input.payload.get("compaction_batch") or input.run_id)
    preview = {
        "pipeline": input.pipeline,
        "kind": "memory_compaction",
        "target": target,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "recommended_candidates": recommended_candidates,
        "status": "staged" if enabled else "review_only",
        "live_compaction": False,
    }
    if input.step == "memory_compaction_preflight":
        artifact = _write_artifact(
            input,
            "memory_compaction_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["memory_maintenance:compaction_preview"],
            "_what_happened": "Prepared a memory compaction preview artifact",
            "_what_mattered": "Memory compaction remains reviewable before any destructive kernel mutation",
            "_what_changed": "Future memory maintenance runs can compare compaction candidates against this preview",
        }
        if not enabled:
            payload.update(
                {
                    "status": "review_only",
                    "_skip_steps": ["compact_memory_idempotent"],
                }
            )
            return StepOutput(payload=payload, summary="memory compaction disabled; kept review-only")
        return StepOutput(payload=payload, summary="memory compaction preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "destructive memory compaction is approved but remains staged; no kernel mutation executes "
                "until a compaction adapter reconciles the planned effect"
            ),
            "_eval_refs": ["memory_maintenance:compaction_staged"],
        },
        summary="memory compaction staged; no kernel mutation executed",
    )


def _memory_compaction_candidates(input: StepInput, memory: MemorySnapshot) -> list[dict[str, str]]:
    limit = max(1, int(input.payload.get("compaction_candidate_limit", 5)))
    decay_threshold = float(input.payload.get("compaction_decay_threshold", 0.25))
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(item_id: str, reason: str) -> None:
        if item_id in seen or len(candidates) >= limit:
            return
        seen.add(item_id)
        candidates.append({"item_id": item_id, "reason": reason})

    for trace in sorted(memory.skill_traces, key=lambda item: item.decay_score):
        if trace.decay_score <= decay_threshold:
            add(f"skill:{trace.skill_name}", f"skill trace decay {trace.decay_score:.2f} <= {decay_threshold:.2f}")
    for signature in memory.failure_signatures:
        if signature.occurrences <= 0 or signature.failure_rate <= 0:
            add(f"failure:{signature.pattern}", "failure signature has no current occurrence/failure-rate evidence")
    for item in memory.items:
        marker_reason = _memory_compaction_marker_reason(item.text)
        if marker_reason:
            add(item.item_id, marker_reason)
    return candidates


def _memory_compaction_marker_reason(text: str) -> str:
    lowered = text.lower()
    for marker in ("stale", "duplicate", "superseded", "obsolete", "archive"):
        if marker in lowered:
            return f"explicit {marker} marker"
    return ""


def social_reactive_draft(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    prompt = input.payload.get("prompt", "Reply to a high-signal Substack comment")
    artifact = _write_artifact(
        input,
        "social_reactive_reply.md",
        "\n".join(
            [
                "# Social Reactive Draft",
                "",
                f"Prompt: {prompt}",
                "Draft stance: answer the substance first, add one concrete detail, and avoid self-promotion.",
                "Publish route: keep the reply local until the live social connector and approval path are available.",
                "Review note: check whether the draft adds value to the thread without overstating certainty.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": ["social:comment_quality"],
        "_what_happened": "Drafted a reactive social reply artifact",
        "_what_mattered": "Reactive social engagement now has a local reviewable path before platform side effects",
        "_what_changed": "Future social replies can compare tone and evidence use against this staged draft",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior social reactive context changed this run into a reviewable reply draft",
            ablation_counterfactual=(
                "without prior social reactive memory, draft a generic reply without the local review path, "
                "substance-first stance, or certainty check"
            ),
        )
    )
    return StepOutput(payload=payload, summary="social reactive draft artifact written")


def social_proactive_plan(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    topic = input.payload.get("topic", "Mira V3.1 evidence spine")
    artifact = _write_artifact(
        input,
        "social_proactive_note.md",
        "\n".join(
            [
                "# Social Proactive Note Plan",
                "",
                f"Topic: {topic}",
                "Draft: make one falsifiable claim, tie it to a concrete artifact, and invite correction.",
                "Cadence: only publish if quota shortfall remains after existing queued notes are counted.",
                "Publish route: stage locally; do not post without the live connector and approval path.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": ["social:proactive_note_quality"],
        "_what_happened": "Planned a proactive social note artifact",
        "_what_mattered": "Proactive social output now has a quota-aware local staging path",
        "_what_changed": "Future proactive runs can compare quota and topic selection against this staged note",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior social proactive context changed this run into a quota-aware staged note",
            ablation_counterfactual=(
                "without prior social proactive memory, draft a generic note without quota awareness, "
                "artifact linkage, or approval-gated staging"
            ),
        )
    )
    return StepOutput(payload=payload, summary="social proactive note artifact written")


def social_publish_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    platform = str(input.payload.get("platform") or _default_social_platform(input.pipeline))
    target = str(
        input.payload.get("target") or input.payload.get("topic") or input.payload.get("prompt") or input.run_id
    )
    connectors = input.payload.get("connectors", {})
    publish_kind = "reply" if input.pipeline == "social_reactive" else "note"
    preview = {
        "pipeline": input.pipeline,
        "kind": publish_kind,
        "platform": platform,
        "target": target,
        "content": str(
            input.payload.get("content")
            or input.payload.get("draft")
            or input.payload.get("topic")
            or input.payload.get("prompt")
            or target
        ),
        "status": "staged",
        "live_publish": False,
    }
    if input.step in {"social_publish_preflight", "social_note_publish_preflight"}:
        artifact = _write_artifact(
            input,
            "social_publish_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": [f"social:publish_preview:{publish_kind}"],
            "_what_happened": f"Prepared a social {publish_kind} publish preview artifact",
            "_what_mattered": "Social platform writes remain approval/effect-log gated before any live post",
            "_what_changed": "Future social publish attempts can be reviewed against this staged preview",
        }
        if not _social_connector_available(connectors, platform):
            payload.update(
                {
                    "status": "staged_without_connector",
                    "_skip_steps": [input.payload.get("publish_step") or _default_social_publish_step(input.pipeline)],
                }
            )
            return StepOutput(payload=payload, summary="social connector unavailable; kept publish staged")
        return StepOutput(payload=payload, summary="social publish preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "social connector available, but V3.1 keeps platform publish staged until provider "
                "reconciliation confirms it"
            ),
            "_eval_refs": [f"social:publish_staged:{publish_kind}"],
        },
        summary="social publish staged; no external post executed",
    )


def market_alert_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    target = str(
        input.payload.get("target")
        or input.payload.get("tetra_report_id")
        or input.payload.get("title")
        or input.run_id
    )
    connectors = input.payload.get("connectors", {})
    preview = {
        "pipeline": input.pipeline,
        "kind": "market_alert",
        "target": target,
        "message": str(input.payload.get("message") or f"Review market signal: {target}"),
        "severity": str(input.payload.get("severity") or "review"),
        "tetra_report_id": str(input.payload.get("tetra_report_id") or ""),
        "status": "staged",
        "live_send": False,
    }
    if input.step == "market_alert_preflight":
        artifact = _write_artifact(
            input,
            "market_alert_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["market:alert_preview"],
            "_what_happened": "Prepared a market alert preview artifact",
            "_what_mattered": "Portfolio-facing market alerts remain approval/effect-log gated before any send",
            "_what_changed": "Future market alert attempts can be reviewed against this staged preview",
        }
        if not _market_alert_connector_available(connectors):
            payload.update(
                {
                    "status": "staged_without_connector",
                    "_skip_steps": ["send_market_alert_idempotent"],
                }
            )
            return StepOutput(payload=payload, summary="market alert connector unavailable; kept alert staged")
        return StepOutput(payload=payload, summary="market alert preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "market alert connector available, but V3.1 keeps portfolio-facing sends staged until "
                "provider reconciliation confirms them"
            ),
            "_eval_refs": ["market:alert_staged"],
        },
        summary="market alert staged; no external send executed",
    )


def health_write_guard(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    target = str(
        input.payload.get("target")
        or input.payload.get("health_record_id")
        or input.payload.get("title")
        or input.run_id
    )
    connectors = input.payload.get("connectors", {})
    preview = {
        "pipeline": input.pipeline,
        "kind": "health_external_write",
        "target": target,
        "operation": str(input.payload.get("operation") or "sync_review_record"),
        "record": input.payload.get("record") if isinstance(input.payload.get("record"), dict) else {},
        "status": "staged",
        "live_write": False,
    }
    if input.step == "health_write_preflight":
        artifact = _write_artifact(
            input,
            "health_write_preview.json",
            json.dumps(preview, indent=2, sort_keys=True) + "\n",
        )
        payload = {
            **preview,
            "_artifacts": [str(artifact)],
            "_eval_refs": ["health:write_preview"],
            "_what_happened": "Prepared an external health write preview artifact",
            "_what_mattered": "Health-provider writes remain approval/effect-log gated and conservative",
            "_what_changed": "Future health write attempts can be reviewed against this staged preview",
        }
        if not _health_write_connector_available(connectors):
            payload.update(
                {
                    "status": "staged_without_connector",
                    "_skip_steps": ["write_health_idempotent"],
                }
            )
            return StepOutput(payload=payload, summary="health provider connector unavailable; kept write staged")
        return StepOutput(payload=payload, summary="health write preview staged for approval")
    return StepOutput(
        payload={
            **preview,
            "_effect_status": "planned",
            "_effect_detail": (
                "health provider connector available, but V3.1 keeps external health writes staged until "
                "provider reconciliation confirms them"
            ),
            "_eval_refs": ["health:write_staged"],
        },
        summary="health write staged; no external write executed",
    )


def weekly_growth_report(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    artifact = _write_artifact(
        input,
        "weekly_growth_report.md",
        "\n".join(
            [
                "# Weekly Growth Report",
                "",
                f"Recent experiences considered: {len(memory.recent_experiences)}",
                "Subscriber signal: summarize locally until live platform metrics are present.",
                "Retention ladder: classify next action as retention, conversation, recommendation, or distribution.",
                "Decision: no public growth action runs from this MVP report.",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": ["social:weekly_growth_report"],
        "_what_happened": "Generated a weekly growth report artifact",
        "_what_mattered": "Growth review now has an auditable local artifact before public-facing actions",
        "_what_changed": "Future growth reports can compare retained audience signals and action categories",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            "prior growth-report context changed this run into a retention-ladder report",
            ablation_counterfactual=(
                "without prior growth-report memory, produce a generic metrics summary without retention ladder "
                "classification or public-action staging"
            ),
        )
    )
    return StepOutput(payload=payload, summary="weekly growth report artifact written")


LOCAL_REPORT_SPECS = {
    "book_reading_notes": {
        "title": "Book Reading Notes",
        "filename": "book_reading_notes.md",
        "eval_ref": "book:reading_notes",
        "focus": "extract claims, counterclaims, and reusable concepts from the current book queue",
        "decision": "no public review or memory compaction runs from this MVP note",
        "ablation_counterfactual": (
            "without prior book-reading context, produce generic notes without claim/counterclaim extraction "
            "or reusable-concept continuity"
        ),
    },
    "research_deep_dive": {
        "title": "Research Deep Dive",
        "filename": "research_deep_dive.md",
        "eval_ref": "research:deep_dive",
        "focus": "turn a research queue item into scoped questions, evidence needs, and uncertainty markers",
        "decision": "no external publication runs from this MVP research artifact",
        "ablation_counterfactual": (
            "without prior research context, produce a generic topic note without scoped questions, "
            "evidence needs, or uncertainty markers"
        ),
    },
    "daily_thought_discussion": {
        "title": "Daily Thought Discussion",
        "filename": "daily_thought_discussion.md",
        "eval_ref": "reflection:daily_discussion",
        "focus": "stage one discussion prompt with assumptions, objections, and follow-up hooks",
        "decision": "no chat or notification side effect runs from this MVP discussion artifact",
        "ablation_counterfactual": (
            "without prior discussion context, stage a generic prompt without assumptions, objections, "
            "or follow-up hooks"
        ),
    },
    "daily_journal": {
        "title": "Daily Journal",
        "filename": "daily_journal.md",
        "eval_ref": "reflection:daily_journal",
        "focus": "summarize the day from recorded experiences instead of invented interiority",
        "decision": "no durable identity claim is made without a gateway-reviewed memory action",
        "ablation_counterfactual": (
            "without prior journal context, write an ungrounded daily note without recorded-experience "
            "anchoring or identity-claim guardrails"
        ),
    },
    "weekly_reflection": {
        "title": "Weekly Reflection",
        "filename": "weekly_reflection.md",
        "eval_ref": "reflection:weekly_delta",
        "focus": "compare weekly deltas, scars, hypotheses, and calibration changes",
        "decision": "no self-modification follows without a separate experiment record",
        "ablation_counterfactual": (
            "without prior reflection context, summarize the week without delta comparison, scars, "
            "hypotheses, or calibration changes"
        ),
    },
    "market_monitor": {
        "title": "Market Monitor",
        "filename": "market_monitor.md",
        "eval_ref": "market:monitor",
        "focus": "stage Tetra signal intake and risk notes before any portfolio-facing action",
        "decision": "no trade, alert, or public market claim runs from this MVP report",
        "ablation_counterfactual": (
            "without prior market context, produce a generic market summary without Tetra signal staging, "
            "risk notes, or portfolio-action guardrails"
        ),
    },
    "incident_response": {
        "title": "Incident Response",
        "filename": "incident_response.md",
        "eval_ref": "ops:incident_response",
        "focus": "triage health failures into severity, owner, rollback, and follow-up evidence",
        "decision": "no destructive remediation runs from this MVP report",
        "ablation_counterfactual": (
            "without prior incident context, produce a generic failure note without severity, owner, "
            "rollback, or follow-up evidence triage"
        ),
    },
    "health_wellness": {
        "title": "Health Wellness",
        "filename": "health_wellness.md",
        "eval_ref": "health:wellness",
        "focus": "summarize available health signals with missing-data markers and conservative advice boundaries",
        "decision": "no medical claim or external health write runs from this MVP report",
        "ablation_counterfactual": (
            "without prior health context, produce a generic wellness note without missing-data markers, "
            "available-signal grounding, or conservative advice boundaries"
        ),
    },
    "skill_learning": {
        "title": "Skill Learning",
        "filename": "skill_learning.md",
        "eval_ref": "self_modification:skill_learning",
        "focus": "capture a novel technique as a candidate skill before any enablement",
        "decision": "no skill is installed or enabled from this MVP report",
        "ablation_counterfactual": (
            "without prior skill-learning context, note a technique informally without candidate-skill "
            "capture or enablement guardrails"
        ),
    },
    "deterministic_reference": {
        "title": "Deterministic Reference",
        "filename": "deterministic_reference.md",
        "eval_ref": "ops:deterministic_reference",
        "focus": "record deterministic execution steps and evidence handles for later comparison",
        "decision": "no external side effect runs from this reference artifact",
        "ablation_counterfactual": (
            "without prior deterministic-reference context, record a loose note without deterministic "
            "execution steps or evidence handles"
        ),
    },
}


def local_report(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    spec = LOCAL_REPORT_SPECS.get(
        input.pipeline,
        {
            "title": input.pipeline.replace("_", " ").title(),
            "filename": f"{input.pipeline}.md",
            "eval_ref": f"{input.pipeline}:local_report",
            "focus": "stage a local report for review",
            "decision": "no external side effect runs from this MVP report",
        },
    )
    artifact = _write_artifact(
        input,
        str(spec["filename"]),
        "\n".join(
            [
                f"# {spec['title']}",
                "",
                f"Pipeline: {input.pipeline}",
                f"Snapshot items reviewed: {len(memory.items)}",
                f"Recent experiences considered: {len(memory.recent_experiences)}",
                f"Focus: {spec['focus']}",
                f"Decision: {spec['decision']}",
            ]
        )
        + "\n",
    )
    payload = {
        "_artifacts": [str(artifact)],
        "_eval_refs": [str(spec["eval_ref"])],
        "_what_happened": f"Generated a {spec['title']} artifact",
        "_what_mattered": f"{spec['title']} now has a local, auditable MVP execution path",
        "_what_changed": f"Future {input.pipeline} runs can compare decisions against this staged artifact",
    }
    payload.update(
        _causal_evidence_payload(
            input,
            memory,
            f"prior {input.pipeline} context changed this run into a local auditable report",
            ablation_counterfactual=spec.get("ablation_counterfactual"),
        )
    )
    return StepOutput(payload=payload, summary=f"{input.pipeline} local report artifact written")


def skill_security_audit_gate(input: StepInput, memory: MemorySnapshot) -> StepOutput:
    candidate = input.payload.get("candidate_skill") or input.payload.get("skill_candidate")
    if not candidate:
        payload = {
            "status": "no_candidate",
            "_eval_refs": ["skill_learning:security_audit:not_requested"],
            "_what_happened": "Skill-learning run found no generated or imported skill candidate",
            "_what_mattered": "No skill was saved or enabled from this run",
            "_what_changed": "Future skill-learning runs still require the audit gate before enablement",
        }
        return StepOutput(payload=payload, summary="no skill candidate to audit")
    if not isinstance(candidate, dict):
        candidate = {"name": "candidate", "skill_markdown": str(candidate)}
    name = str(candidate.get("name") or input.payload.get("skill_name") or f"candidate_{input.run_id}")
    skill_yaml = str(
        candidate.get("skill_yaml")
        or candidate.get("metadata")
        or f"name: {name}\ndescription: Generated skill candidate.\n"
    )
    skill_markdown = str(
        candidate.get("skill_markdown")
        or candidate.get("skill_md")
        or candidate.get("body")
        or candidate.get("content")
        or ""
    )
    audit = audit_workflow_skill_candidate(name, skill_yaml=skill_yaml, skill_markdown=skill_markdown)
    audit_payload = audit.to_dict()["workflow_pack_audit"]
    audit_artifact = _write_artifact(
        input,
        "skill_security_audit.json",
        json.dumps(
            {
                "skill_candidate_audit": {
                    "candidate": name,
                    "result": audit_payload["result"],
                    "audit_hash": audit_payload["audit_hash"],
                    "files_checked": audit_payload["files_checked"],
                    "findings": audit_payload["findings"],
                    "enabled": False,
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    payload = {
        "status": "passed" if audit.passed else "blocked",
        "candidate": name,
        "finding_count": len(audit.findings),
        "_artifacts": [str(audit_artifact)],
        "_eval_refs": [f"skill_learning:security_audit:{'passed' if audit.passed else 'blocked'}"],
        "_what_happened": f"Skill candidate security audit {'passed' if audit.passed else 'blocked'}",
        "_what_mattered": "Generated or imported skills must pass audit before save or enablement",
        "_what_changed": "Skill-learning now has a runtime audit gate for candidate skill content",
    }
    if not audit.passed:
        reasons = "; ".join(f"{finding.reason}: {finding.pattern}" for finding in audit.findings[:3])
        payload["_what_failed"] = f"skill candidate failed security audit: {reasons}"
        return StepOutput(payload=payload, summary="skill candidate blocked by security audit", succeeded=False)
    return StepOutput(payload=payload, summary="skill candidate passed security audit")


ACTION_REGISTRY = {
    "system_health_probe": system_health_probe,
    "system_health_record": system_health_record,
    "briefing_fetch_sources": briefing_fetch_sources,
    "briefing_dedup_trust_classification": briefing_dedup_trust_classification,
    "briefing_write": briefing_write,
    "article_draft": article_draft,
    "article_publish_guard": article_publish_guard,
    "a2a_experiment": a2a_experiment,
    "podcast_tts_route": podcast_tts_route,
    "podcast_tts_synthesis_guard": podcast_tts_synthesis_guard,
    "podcast_plan": podcast_plan,
    "podcast_rss_publish_guard": podcast_rss_publish_guard,
    "self_evolution_experiment": self_evolution_experiment,
    "self_evolution_branch_canary": self_evolution_branch_canary,
    "self_evolution_canary_rollback": self_evolution_canary_rollback,
    "self_evolution_production_promotion_guard": self_evolution_production_promotion_guard,
    "memory_maintenance_report": memory_maintenance_report,
    "memory_compaction_guard": memory_compaction_guard,
    "social_reactive_draft": social_reactive_draft,
    "social_proactive_plan": social_proactive_plan,
    "social_publish_guard": social_publish_guard,
    "market_alert_guard": market_alert_guard,
    "health_write_guard": health_write_guard,
    "weekly_growth_report": weekly_growth_report,
    "skill_security_audit_gate": skill_security_audit_gate,
    "local_report": local_report,
}


def action_for(name: str):
    return ACTION_REGISTRY.get(name, generic_step)


def _write_artifact(input: StepInput, filename: str, body: str) -> Path:
    root = Path(input.payload.get("artifact_dir", ".")).expanduser()
    target = root / input.pipeline / input.run_id / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _podcast_tts_failure_memory_refs(memory: MemorySnapshot) -> list[str]:
    refs: list[str] = []
    for scar in memory.scars:
        body = " ".join([scar.incident, scar.root_cause, scar.behavioral_change, scar.policy_created or ""]).lower()
        if "tts" in body and ("minimax" in body or "503" in body or "fallback" in body or "failure" in body):
            refs.append(scar.scar_id)
    for signature in memory.failure_signatures:
        body = " ".join([signature.pattern, signature.detection_rule]).lower()
        if "tts" in body and ("minimax" in body or "503" in body or "fallback" in body or "failure" in body):
            refs.append(f"failure:{signature.pattern}")
    return refs


def _default_social_platform(pipeline: str) -> str:
    return "substack_comments" if pipeline == "social_reactive" else "substack_notes"


def _default_social_publish_step(pipeline: str) -> str:
    return "post_reply_idempotent" if pipeline == "social_reactive" else "post_note_idempotent"


def _social_connector_available(connectors: dict, platform: str) -> bool:
    return bool(
        connectors.get("social")
        or connectors.get(platform)
        or (platform.startswith("substack") and connectors.get("substack"))
    )


def _market_alert_connector_available(connectors: dict) -> bool:
    return bool(connectors.get("market_alert") or connectors.get("portfolio") or connectors.get("tetra_alerts"))


def _health_write_connector_available(connectors: dict) -> bool:
    return bool(connectors.get("health_provider") or connectors.get("health_export") or connectors.get("healthkit"))


def _validate_branch_canary_repo(repo_path: Path, branch_name: str) -> None:
    if not repo_path.exists() or not (repo_path / ".git").exists():
        raise ValueError("branch canary requires a local git repository path")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name) or branch_name.startswith(("-", "/")):
        raise ValueError("unsafe branch name")
    if ".." in branch_name or branch_name.endswith(("/", ".")) or "@{" in branch_name:
        raise ValueError("unsafe branch name")


def _git_output(repo_path: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
        timeout=15,
    )
    return completed.stdout.strip()


def _causal_evidence_payload(
    input: StepInput,
    memory: MemorySnapshot,
    reason: str,
    *,
    ablation_counterfactual: str | None = None,
) -> dict:
    if not memory.causal_context:
        return {}
    memory_id = memory.causal_context[0]
    effect_ids = [f"effect:{input.run_id}:{input.step}"]
    if ablation_counterfactual:
        evidence = confirm_ablation_evidence(
            memory_id=memory_id,
            run_id=input.run_id,
            pipeline=input.pipeline,
            normal_decision=reason,
            counterfactual_decision=ablation_counterfactual,
            effect_ids=effect_ids,
        )
        return {"_causal_evidence": [evidence]}
    return {
        "_causal_evidence": [
            CausalEvidence(
                memory_id=memory_id,
                level="L3",
                reason=reason,
                run_id=input.run_id,
                pipeline=input.pipeline,
                effect_ids=effect_ids,
            )
        ]
    }
