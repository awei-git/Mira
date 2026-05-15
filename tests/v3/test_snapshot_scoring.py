from pathlib import Path

from mira.kernel import ExperienceLedger, MemoryKernel, SnapshotBuilder


def test_snapshot_scores_items_and_excludes_local_only_memory(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.relationship_model.notes.append("WA prefers concise output.")
    kernel.relationship_model.notes.append("PRIVATE: health data should stay local-only.")

    snapshot = SnapshotBuilder(ExperienceLedger(tmp_path / "ledger.jsonl")).build(
        kernel=kernel,
        pipeline="article_creation",
        memory_class="creative",
        involved_skills=[],
        intent="draft article",
    )

    assert snapshot.items
    assert all(item.score > 0 for item in snapshot.items)
    assert "relationship:1" in snapshot.manifest.excluded_ids
    assert "health data" not in "\n".join(snapshot.hints).lower()
    assert snapshot.manifest.item_scores["relationship:0"] > 0


def test_bodily_snapshot_can_include_local_only_health_memory(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.relationship_model.notes.append("PRIVATE: health data should stay local-only.")

    snapshot = SnapshotBuilder(ExperienceLedger(tmp_path / "ledger.jsonl")).build(
        kernel=kernel,
        pipeline="health_wellness",
        memory_class="bodily",
        involved_skills=[],
        intent="health check",
    )

    assert snapshot.manifest.excluded_ids == ()
    assert "health data" in "\n".join(snapshot.hints).lower()
