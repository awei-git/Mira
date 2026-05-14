"""Data model for the V3 monitor/config dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from mira.kernel.ledger import ExperienceLedger
from mira.kernel.schema import MemoryKernel
from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES, SOFT_POLICY_SPECS


@dataclass(frozen=True)
class DashboardSnapshot:
    active_pipelines: list[str]
    scars: list[str]
    active_hypotheses: list[str]
    skill_traces: dict[str, float]
    recent_experience_ids: list[str]
    hard_policy_count: int
    soft_policy_count: int


def build_dashboard_snapshot(kernel: MemoryKernel, ledger: ExperienceLedger) -> DashboardSnapshot:
    recent = ledger.list(limit=20)
    return DashboardSnapshot(
        active_pipelines=sorted(PIPELINE_CATALOG),
        scars=[scar.scar_id for scar in kernel.scars],
        active_hypotheses=[h.hypothesis_id for h in kernel.pending_hypotheses if h.status == "testing"],
        skill_traces={trace.skill_name: trace.success_rate for trace in kernel.skill_traces},
        recent_experience_ids=[record.id for record in recent],
        hard_policy_count=sum(len(names) for names in HARD_POLICY_NAMES.values()),
        soft_policy_count=len(SOFT_POLICY_SPECS),
    )
