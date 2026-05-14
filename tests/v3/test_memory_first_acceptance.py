from pathlib import Path

from mira.engine import PipelineExecutor
from mira.kernel import ExperienceLedger
from mira.kernel.store import JsonKernelStore
from mira.pipelines.operational import build_communication_pipeline


def test_yesterdays_experience_causally_changes_next_run(tmp_path: Path):
    kernel_store = JsonKernelStore(tmp_path / "kernel.json")
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    executor = PipelineExecutor(kernel_store=kernel_store, ledger=ledger)
    pipeline = build_communication_pipeline()

    first = executor.run(
        pipeline,
        {"message": "Implement the next piece and give me status."},
        intent="answer WA implementation request",
    )
    second = executor.run(
        pipeline,
        {"message": "Implement the next piece and give me status."},
        intent="answer WA implementation request",
    )

    assert first.outputs["execute"]["used_memory"] is False
    assert second.outputs["execute"]["used_memory"] is True
    assert second.outputs["execute"]["reply"].startswith("Short answer:")
    assert first.record.id in second.record.causal_links
    assert "WA prefers concise output" in second.snapshot.hints[0]
