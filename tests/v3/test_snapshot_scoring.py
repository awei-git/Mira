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
    included = next(item for item in snapshot.items if item.item_id == "relationship:0")
    assert included.memory_id == included.item_id
    assert included.why_included == "relationship preference"
    assert {
        "relevance",
        "recency",
        "importance",
        "causal_success",
        "trust",
        "privacy",
        "diversity",
        "token_budget",
    }.issubset(included.score_breakdown)
    assert snapshot.manifest.profile == "article_creation"
    assert snapshot.manifest.snapshot_hash == snapshot.manifest.hash


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


def test_snapshot_manifest_records_run_profile_and_token_budget(tmp_path: Path):
    kernel = MemoryKernel()
    kernel.relationship_model.notes.append("WA prefers concrete examples.")

    snapshot = SnapshotBuilder(ExperienceLedger(tmp_path / "ledger.jsonl")).build(
        kernel=kernel,
        pipeline="communication",
        memory_class="operational",
        intent="reply",
        run_id="run_123",
    )

    assert snapshot.manifest.run_id == "run_123"
    assert snapshot.manifest.profile == "communication"
    assert snapshot.manifest.total_tokens >= 1
    assert snapshot.manifest.hash
