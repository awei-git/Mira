from pathlib import Path

import pytest

from mira.kernel import ExperienceLedger, MemoryAction, MemoryDelta, MemoryKernel
from mira.kernel.consolidation import MemoryConsolidator
from mira.kernel.ledger import ExperienceRecord
from mira.kernel.store import DualKernelStore, JsonKernelStore, SQLiteKernelStore


def test_memory_delta_requires_contract_fields():
    with pytest.raises(ValueError, match="what_happened"):
        MemoryDelta(
            pipeline="article",
            run_id="run_1",
            memory_class="creative",
            what_happened="",
            what_mattered="mattered",
            what_changed="changed",
            actions=[],
        )


def test_experience_ledger_round_trips_delta(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    delta = MemoryDelta(
        pipeline="article",
        run_id="exp_1",
        memory_class="creative",
        what_happened="drafted",
        what_mattered="voice improved",
        what_changed="future runs should use the personal angle",
        actions=[MemoryAction("update_skill_trace", "skill:article_writing", "success")],
    )
    ledger.append(
        ExperienceRecord(
            id="exp_1",
            pipeline="article",
            trigger="manual",
            intent="write",
            outcome="drafted",
            delta=delta,
            causal_links=[],
            confidence=0.9,
            memory_class="creative",
        )
    )

    records = ledger.list()

    assert len(records) == 1
    assert records[0].delta.actions[0].target == "skill:article_writing"


def test_consolidator_applies_relationship_and_skill_trace():
    kernel = MemoryKernel()
    delta = MemoryDelta(
        pipeline="communication",
        run_id="run_1",
        memory_class="operational",
        what_happened="replied",
        what_mattered="preference emerged",
        what_changed="future replies should be concise",
        actions=[
            MemoryAction("update_relationship", "relationship:wa", "WA prefers concise output."),
            MemoryAction("update_skill_trace", "skill:response_synthesis", "clear answer"),
        ],
    )

    result = MemoryConsolidator().apply_delta(kernel, delta)

    assert "WA prefers concise output." in kernel.relationship_model.notes
    assert kernel.skill_trace("response_synthesis").times_used == 1
    assert "updated relationship:wa" in result.applied


def test_json_and_sqlite_kernel_stores_round_trip(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.relationship_model.notes.append("WA prefers concise output.")

    json_store = JsonKernelStore(tmp_path / "kernel.json")
    sqlite_store = SQLiteKernelStore(tmp_path / "kernel.sqlite")
    json_store.save(kernel)
    sqlite_store.save(kernel)

    assert json_store.load().relationship_model.notes == ["WA prefers concise output."]
    assert sqlite_store.load().relationship_model.notes == ["WA prefers concise output."]


def test_dual_kernel_store_writes_primary_and_fallback(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.relationship_model.notes.append("persist both")
    primary = JsonKernelStore(tmp_path / "primary.json")
    fallback = SQLiteKernelStore(tmp_path / "fallback.sqlite")

    DualKernelStore(primary, fallback).save(kernel)

    assert primary.load().relationship_model.notes == ["persist both"]
    assert fallback.load().relationship_model.notes == ["persist both"]
