"""Tests for belief store."""
import json
import sys
import tempfile
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))


def _make_store(initial_beliefs=None):
    """Create a BeliefStore backed by a temp file."""
    from knowledge.beliefs import BeliefStore
    tmp = Path(tempfile.mktemp(suffix=".json"))
    if initial_beliefs:
        tmp.write_text(json.dumps(initial_beliefs, ensure_ascii=False), encoding="utf-8")
    return BeliefStore(path=tmp), tmp


def test_load_seeded_beliefs():
    """Verify the actual beliefs.json file loads correctly."""
    from knowledge.beliefs import BeliefStore
    store = BeliefStore()
    assert len(store) >= 10, f"Expected at least 10 seeded beliefs, got {len(store)}"
    domains = store.domains()
    assert "ai_systems" in domains
    assert "security" in domains
    assert "self" in domains


def test_add_and_retrieve():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store, tmp = _make_store()
    try:
        b = BeliefRecord(
            statement="Testing is important",
            domain="engineering",
            stance="strong",
            confidence=0.9,
        )
        assert store.add_belief(b)
        assert len(store) == 1

        beliefs = store.get_beliefs(domain="engineering")
        assert len(beliefs) == 1
        assert beliefs[0].statement == "Testing is important"
    finally:
        tmp.unlink(missing_ok=True)


def test_dedup():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store, tmp = _make_store()
    try:
        b1 = BeliefRecord(statement="Testing is important", domain="eng", confidence=0.9)
        b2 = BeliefRecord(statement="Testing is important", domain="eng", confidence=0.8)
        assert store.add_belief(b1)
        assert not store.add_belief(b2)  # duplicate
        assert len(store) == 1
    finally:
        tmp.unlink(missing_ok=True)


def test_update_belief():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store, tmp = _make_store()
    try:
        b = BeliefRecord(
            statement="AI will replace most jobs",
            domain="society",
            stance="tentative",
            confidence=0.5,
        )
        store.add_belief(b)
        updated = store.update_belief("AI will replace",
                                       new_stance="moderate",
                                       new_confidence=0.7,
                                       new_evidence="New labor study confirms")
        assert updated
        beliefs = store.get_beliefs(domain="society")
        assert beliefs[0].stance == "moderate"
        assert beliefs[0].confidence == 0.7
        assert any("New labor study" in e for e in beliefs[0].evidence_for)
        assert beliefs[0].last_reconsidered_at is not None
    finally:
        tmp.unlink(missing_ok=True)


def test_get_belief_context():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store, tmp = _make_store()
    try:
        store.add_belief(BeliefRecord(
            statement="Compression favors consistency",
            domain="ai_systems",
            stance="strong",
            confidence=0.9,
        ))
        store.add_belief(BeliefRecord(
            statement="Security is undervalued",
            domain="security",
            stance="moderate",
            confidence=0.7,
        ))
        ctx = store.get_belief_context(["ai_systems"])
        assert "Compression" in ctx
        assert "Security" not in ctx

        ctx_all = store.get_belief_context()
        assert "Compression" in ctx_all
        assert "Security" in ctx_all
    finally:
        tmp.unlink(missing_ok=True)


def test_persistence():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store, tmp = _make_store()
    try:
        store.add_belief(BeliefRecord(
            statement="Data persists",
            domain="test",
            confidence=0.8,
        ))
        # Reload from same file
        store2 = BeliefStore(path=tmp)
        assert len(store2) == 1
        assert store2.get_beliefs()[0].statement == "Data persists"
    finally:
        tmp.unlink(missing_ok=True)


def test_add_reloads_latest_state_before_write():
    from knowledge.beliefs import BeliefStore, BeliefRecord
    store1, tmp = _make_store()
    try:
        assert store1.add_belief(BeliefRecord(statement="Belief A", domain="test", confidence=0.8))
        store2 = BeliefStore(path=tmp)
        assert store2.add_belief(BeliefRecord(statement="Belief B", domain="test", confidence=0.7))

        assert store1.add_belief(BeliefRecord(statement="Belief C", domain="test", confidence=0.6))

        reloaded = BeliefStore(path=tmp)
        assert sorted(b.statement for b in reloaded.get_beliefs()) == ["Belief A", "Belief B", "Belief C"]
    finally:
        tmp.unlink(missing_ok=True)
        tmp.with_suffix(".lock").unlink(missing_ok=True)
