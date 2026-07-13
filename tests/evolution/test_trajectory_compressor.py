"""Compressor preserves head + last-4 tail, LLM-summarizes middle."""

from __future__ import annotations

from evolution.trajectory_compressor import SUMMARY_PREFIX, compress
from schemas.trajectory import TrajectoryRecord, Turn


def _build(num_turns: int) -> TrajectoryRecord:
    turns = []
    # First 4 protected-role turns
    turns.append(Turn(role="system", content="S0"))
    turns.append(Turn(role="human", content="H0"))
    turns.append(Turn(role="assistant", content="A0"))
    turns.append(Turn(role="tool", content="", tool_name="Read", tool_result_preview="r0", tool_success=True))
    # Filler in the middle
    for i in range(num_turns - 8):
        role = "assistant" if i % 2 == 0 else "tool"
        turns.append(Turn(role=role, content=f"M{i}"))
    # Last 4 turns
    for i in range(4):
        turns.append(Turn(role="assistant", content=f"T{i}"))
    return TrajectoryRecord(task_id="t", agent="writer", conversations=turns)


def test_compressor_short_trajectory_unchanged():
    rec = _build(8)  # head 4 + tail 4 = all protected; no middle
    out = compress(rec, summarizer=lambda text: "should not be called")
    assert out is rec
    assert not out.compressed


def test_compressor_replaces_middle_with_summary():
    rec = _build(16)  # head 4 + 8 middle + 4 tail
    calls = []

    def fake_summary(text: str) -> str:
        calls.append(text)
        return "the gist of the middle"

    out = compress(rec, summarizer=fake_summary)
    assert out is not rec
    assert out.compressed is True
    assert out.original_turn_count == 16
    # 4 head + 1 summary + 4 tail
    assert len(out.conversations) == 9
    summary_turn = out.conversations[4]
    assert summary_turn.content.startswith(SUMMARY_PREFIX)
    assert "the gist" in summary_turn.content
    # Head and tail preserved verbatim
    assert out.conversations[0].content == "S0"
    assert out.conversations[-1].content == "T3"
    assert len(calls) == 1


def test_compressor_empty_summary_falls_back_to_original():
    rec = _build(14)

    def empty_summary(_text: str) -> str:
        return ""

    out = compress(rec, summarizer=empty_summary)
    assert out is rec  # no lossy drop when summarizer fails


def test_compressor_no_summarizer_drops_middle_with_marker(monkeypatch):
    """When neither an injected nor default summarizer is available,
    the compressor still drops the middle but records the omission."""
    from evolution import trajectory_compressor as mod

    monkeypatch.setattr(mod, "_default_summarizer", lambda: None)

    rec = _build(12)
    out = compress(rec)  # no summarizer passed, default returns None
    assert out is not rec
    assert out.compressed is True
    assert out.original_turn_count == 12
    assert any(
        t.content.startswith(SUMMARY_PREFIX) and "no summarizer available" in t.content for t in out.conversations
    )
