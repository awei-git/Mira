from __future__ import annotations

from publish.writer_gate import read_writer_gate, record_writer_gate, require_writer_gate


def test_record_and_require_writer_gate(tmp_path):
    record = record_writer_gate(tmp_path, channel="substack", task_id="task1", artifact_path="/tmp/out.md")

    assert record["writer_gate_passed"] is True
    assert read_writer_gate(tmp_path)["task_id"] == "task1"
    ok, msg, loaded = require_writer_gate(tmp_path, channel="substack")
    assert ok, msg
    assert loaded["artifact_path"] == "/tmp/out.md"


def test_require_writer_gate_rejects_missing_or_wrong_channel(tmp_path):
    ok, msg, loaded = require_writer_gate(tmp_path, channel="substack")
    assert not ok
    assert loaded is None
    assert "missing" in msg

    record_writer_gate(tmp_path, channel="bluesky")
    ok, msg, loaded = require_writer_gate(tmp_path, channel="substack")
    assert not ok
    assert loaded is not None
    assert "channel mismatch" in msg
