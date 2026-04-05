"""Tests for unified memory schema."""
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))


def test_create_memory_record():
    from memory_schema import MemoryRecord
    r = MemoryRecord(
        content="AI agent adoption is accelerating",
        memory_type="fact",
        source="explore_briefing",
        source_id="task_abc123",
        confidence=0.8,
        tags=["ai", "trends"],
    )
    assert r.memory_type == "fact"
    assert r.confidence == 0.8
    assert len(r.record_id) == 12


def test_invalid_memory_type():
    from memory_schema import MemoryRecord
    import pytest
    with pytest.raises(ValueError, match="Invalid memory_type"):
        MemoryRecord(content="test", memory_type="invalid", source="test")


def test_confidence_clamping():
    from memory_schema import MemoryRecord
    r = MemoryRecord(content="test", memory_type="fact", source="test", confidence=1.5)
    assert r.confidence == 1.0
    r2 = MemoryRecord(content="test", memory_type="fact", source="test", confidence=-0.5)
    assert r2.confidence == 0.0


def test_to_dict_roundtrip():
    from memory_schema import MemoryRecord
    r = MemoryRecord(
        content="Test content",
        memory_type="episode",
        source="test",
        tags=["a", "b"],
    )
    d = r.to_dict()
    r2 = MemoryRecord.from_dict(d)
    assert r2.content == r.content
    assert r2.memory_type == r.memory_type
    assert r2.tags == r.tags
    assert r2.record_id == r.record_id


def test_freshness():
    from memory_schema import MemoryRecord
    r = MemoryRecord(content="test", memory_type="fact", source="test")
    assert r.is_fresh(max_age_days=30)  # just created


def test_decay():
    from memory_schema import MemoryRecord
    # No TTL = never decays
    r = MemoryRecord(content="test", memory_type="fact", source="test", ttl_days=None)
    assert not r.should_decay()

    # TTL set but very long
    r2 = MemoryRecord(content="test", memory_type="fact", source="test", ttl_days=365)
    assert not r2.should_decay()


def test_filter_by_confidence():
    from memory_schema import MemoryRecord, filter_by_confidence
    records = [
        MemoryRecord(content="high", memory_type="fact", source="t", confidence=0.9),
        MemoryRecord(content="low", memory_type="fact", source="t", confidence=0.1),
        MemoryRecord(content="mid", memory_type="fact", source="t", confidence=0.5),
    ]
    filtered = filter_by_confidence(records, min_confidence=0.3)
    assert len(filtered) == 2
    assert all(r.confidence >= 0.3 for r in filtered)


def test_deduplicate():
    from memory_schema import MemoryRecord, deduplicate
    records = [
        MemoryRecord(content="AI agents are useful for automation", memory_type="fact", source="t"),
        MemoryRecord(content="AI agents are useful for automation", memory_type="fact", source="t"),
        MemoryRecord(content="Something completely different", memory_type="fact", source="t"),
    ]
    unique = deduplicate(records)
    assert len(unique) == 2
