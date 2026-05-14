"""Runtime wiring for Mira V3.

This module is the bridge between the legacy super-agent runtime and the new
memory-first package. It intentionally keeps side effects narrow: load stores,
write experience records, and run the first migrated pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from mira.engine import PipelineExecutor
from mira.kernel import ExperienceLedger, MemoryAction, MemoryDelta
from mira.kernel.commit import MemoryCommitLog, SecurityGateway
from mira.kernel.consolidation import MemoryConsolidator
from mira.kernel.ledger import ExperienceRecord, new_run_id
from mira.kernel.schema import MemoryClass, to_jsonable
from mira.kernel.snapshot import SnapshotBuilder
from mira.kernel.store import JsonKernelStore, KernelStore
from mira.pipelines.operational import build_communication_pipeline

V3_DIRNAME = "v3"

JOB_PIPELINE_MAP: dict[str, str] = {
    "explore": "intelligence_briefing",
    "writing-pipeline": "article_creation",
    "autowrite-check": "article_creation",
    "journal": "daily_journal",
    "research-cycle": "research_deep_dive",
    "reflect": "weekly_reflection",
    "soul-question": "daily_thought_discussion",
    "spark-check": "daily_thought_discussion",
    "idle-think": "daily_thought_discussion",
    "substack-comments": "social_reactive",
    "substack-growth": "weekly_growth_report",
    "substack-notes": "social_proactive",
    "analyst-pre": "market_monitor",
    "analyst-post": "market_monitor",
    "daily-research": "research_deep_dive",
    "book-review": "book_reading_notes",
    "comparative-book-project": "book_reading_notes",
    "skill-study": "skill_learning",
    "health-check": "health_wellness",
    "health-weekly": "health_wellness",
    "self-audit": "self_evolution",
    "self-evolve": "self_evolution",
    "backlog-executor": "memory_maintenance",
    "restore-dry-run": "system_health",
    "assessment": "memory_maintenance",
    "daily-report": "daily_journal",
    "zhesi": "daily_thought_discussion",
    "log-cleanup": "memory_maintenance",
}

TASK_TAG_PIPELINE_MAP: dict[str, str] = {
    "writing": "article_creation",
    "writer": "article_creation",
    "publish": "article_creation",
    "podcast": "podcast_production",
    "research": "research_deep_dive",
    "briefing": "intelligence_briefing",
    "social": "social_reactive",
    "health": "health_wellness",
    "analyst": "market_monitor",
    "market": "market_monitor",
    "code": "self_evolution",
    "coder": "self_evolution",
    "skill": "skill_learning",
    "discussion": "daily_thought_discussion",
    "communication": "communication",
}

PIPELINE_MEMORY_CLASS: dict[str, MemoryClass] = {
    "article_creation": "creative",
    "podcast_production": "creative",
    "book_reading_notes": "creative",
    "social_reactive": "social",
    "social_proactive": "social",
    "weekly_growth_report": "social",
    "intelligence_briefing": "epistemic",
    "research_deep_dive": "epistemic",
    "daily_thought_discussion": "epistemic",
    "daily_journal": "epistemic",
    "weekly_reflection": "epistemic",
    "market_monitor": "operational",
    "communication": "operational",
    "system_health": "operational",
    "incident_response": "operational",
    "health_wellness": "bodily",
    "self_evolution": "self_modification",
    "skill_learning": "self_modification",
    "memory_maintenance": "self_modification",
    "deterministic_reference": "operational",
}


@dataclass(frozen=True)
class V3Paths:
    root: Path
    kernel: Path
    ledger: Path
    commits: Path
    effect_log: Path
    eval_history: Path
    snapshots: Path


def default_v3_paths(root: Path | str | None = None) -> V3Paths:
    if root is None:
        try:
            import config

            root = Path(config.MIRA_ROOT)
        except Exception:
            root = Path.cwd()
    data_dir = Path(root) / "data" / V3_DIRNAME
    return V3Paths(
        root=data_dir,
        kernel=data_dir / "kernel.json",
        ledger=data_dir / "experience_ledger.jsonl",
        commits=data_dir / "memory_commits.jsonl",
        effect_log=data_dir / "effect_log.jsonl",
        eval_history=data_dir / "eval_history.jsonl",
        snapshots=data_dir / "snapshots",
    )


def default_kernel_store(root: Path | str | None = None) -> KernelStore:
    return JsonKernelStore(default_v3_paths(root).kernel)


def default_ledger(root: Path | str | None = None) -> ExperienceLedger:
    return ExperienceLedger(default_v3_paths(root).ledger)


def default_commit_log(root: Path | str | None = None) -> MemoryCommitLog:
    return MemoryCommitLog(default_v3_paths(root).commits)


def pipeline_for_background_job(bg_name: str) -> str:
    normalized = bg_name.strip()
    for prefix, pipeline in JOB_PIPELINE_MAP.items():
        if normalized == prefix or normalized.startswith(prefix + "-"):
            return pipeline
    return "memory_maintenance"


def pipeline_for_task(tags: list[str] | None) -> str:
    for tag in tags or []:
        pipeline = TASK_TAG_PIPELINE_MAP.get(str(tag).lower())
        if pipeline:
            return pipeline
    return "communication"


def record_experience(
    *,
    pipeline: str,
    trigger: str,
    intent: str,
    outcome: str,
    what_happened: str,
    what_mattered: str,
    what_changed: str,
    what_failed: str | None,
    actions: list[MemoryAction] | None = None,
    causal_links: list[str] | None = None,
    confidence: float = 0.8,
    root: Path | str | None = None,
) -> ExperienceRecord:
    memory_class = PIPELINE_MEMORY_CLASS.get(pipeline, "operational")
    run_id = new_run_id(pipeline)
    delta = MemoryDelta(
        pipeline=pipeline,
        run_id=run_id,
        memory_class=memory_class,
        what_happened=what_happened,
        what_mattered=what_mattered,
        what_changed=what_changed,
        what_failed=what_failed,
        actions=list(actions or []),
    )
    store = default_kernel_store(root)
    kernel = store.load()
    commit = SecurityGateway().validate(delta)
    MemoryConsolidator().apply_commit(kernel, delta, commit)
    default_commit_log(root).append(commit)
    record = ExperienceRecord(
        id=run_id,
        pipeline=pipeline,
        trigger=trigger,
        intent=intent,
        outcome=outcome,
        delta=delta,
        causal_links=list(causal_links or []),
        confidence=confidence,
        memory_class=memory_class,
        memory_commit_id=commit.commit_id,
    )
    default_ledger(root).append(record)
    store.save(kernel)
    return record


def record_task_completion(
    *,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None,
    root: Path | str | None = None,
) -> ExperienceRecord:
    pipeline = pipeline_for_task(tags)
    failed = status not in {"done", "verified", "completed", "completed_unverified"}
    actions: list[MemoryAction] = [
        MemoryAction(
            "update_skill_trace",
            f"skill:{pipeline}",
            f"task={task_id} status={status}",
        )
    ]
    if failed:
        actions.append(MemoryAction("create_scar", f"scar:{pipeline}:{task_id}", summary[:500] or status))
    return record_experience(
        pipeline=pipeline,
        trigger="task_result",
        intent=f"complete task {task_id}",
        outcome=status,
        what_happened=f"Task {task_id} finished with status {status}",
        what_mattered=(summary or "Task completed without a summary")[:1000],
        what_changed=f"Future {pipeline} snapshots include task outcome {task_id}",
        what_failed=summary[:1000] if failed else None,
        actions=actions,
        confidence=0.75 if not failed else 0.45,
        root=root,
    )


def record_background_completion(
    bg_name: str,
    *,
    root: Path | str | None = None,
) -> ExperienceRecord:
    pipeline = pipeline_for_background_job(bg_name)
    return record_experience(
        pipeline=pipeline,
        trigger="background_job",
        intent=f"run scheduled job {bg_name}",
        outcome="completed",
        what_happened=f"Background job {bg_name} completed",
        what_mattered=f"{bg_name} is now part of the V3 experience ledger",
        what_changed=f"Future {pipeline} snapshots include scheduled-job outcome {bg_name}",
        what_failed=None,
        actions=[
            MemoryAction(
                "update_skill_trace",
                f"skill:{pipeline}",
                f"background job completed: {bg_name}",
            )
        ],
        confidence=0.7,
        root=root,
    )


def prepare_background_context(
    bg_name: str,
    *,
    root: Path | str | None = None,
) -> dict[str, str]:
    """Write a memory snapshot for a legacy background job and return env vars."""

    pipeline = pipeline_for_background_job(bg_name)
    paths = default_v3_paths(root)
    kernel = default_kernel_store(root).load()
    snapshot = SnapshotBuilder(default_ledger(root)).build(
        kernel=kernel,
        pipeline=pipeline,
        memory_class=PIPELINE_MEMORY_CLASS.get(pipeline, "operational"),
        involved_skills=[pipeline],
        intent=f"run scheduled job {bg_name}",
    )
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", bg_name)[:120] or "background"
    target = paths.snapshots / f"{safe_name}.json"
    target.write_text(json.dumps(to_jsonable(snapshot), indent=2, sort_keys=True), encoding="utf-8")
    return {
        "MIRA_V3_PIPELINE": pipeline,
        "MIRA_V3_MEMORY_SNAPSHOT": str(target),
        "MIRA_V3_LEDGER": str(paths.ledger),
        "MIRA_V3_KERNEL": str(paths.kernel),
    }


def run_communication(message: str, *, root: Path | str | None = None) -> str:
    executor = PipelineExecutor(default_kernel_store(root), default_ledger(root), commit_log=default_commit_log(root))
    result = executor.run(
        build_communication_pipeline(),
        {"message": message},
        intent="answer WA communication request",
        trigger="manual",
    )
    return result.outputs["execute"]["reply"]
