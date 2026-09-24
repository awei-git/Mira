"""Blocked text is recoverable evidence, never an approved draft or a retry."""

import json

import pytest

from content_worker.failure import read_failure, retain_candidate


def blocked(tmp_path):
    return retain_candidate(tmp_path, "Candidate awaiting editing.", {"violations": [{"trigger": "em_dash_overuse"}]})


def test_retention_keeps_prior_candidates_and_rejects_changed_bytes(tmp_path):
    first = blocked(tmp_path)
    retain_candidate(tmp_path, "Another blocked version.", {"violations": [{"trigger": "generic_ai_essay_shell"}]})
    assert (tmp_path / first["candidate_path"]).read_text() == "Candidate awaiting editing."
    second = read_failure(tmp_path)
    assert second["approved"] is False
    (tmp_path / second["candidate_path"]).write_text("changed")
    with pytest.raises(ValueError, match="blocked_candidate_changed"):
        read_failure(tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_path", "../outside.md"),
        ("approved", True),
        ("triggers", ["raw provider error"]),
        ("candidate_sha256", "bad"),
    ],
)
def test_untrusted_failure_receipt_is_not_used(tmp_path, field, value):
    row = blocked(tmp_path)
    row[field] = value
    (tmp_path / "writer_failure.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="invalid_writer_failure_receipt"):
        read_failure(tmp_path)


def test_symlink_candidate_is_rejected(tmp_path):
    row = blocked(tmp_path)
    path = tmp_path / row["candidate_path"]
    path.unlink()
    target = tmp_path / "other.md"
    target.write_text("Candidate awaiting editing.")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_failure(tmp_path)


def test_cloud_gate_preserves_candidate_before_summary_and_legacy_default_is_unchanged(tmp_path, monkeypatch):
    import pathsetup  # noqa: F401
    from agents.writer import handler

    monkeypatch.setattr(
        handler, "scan_obsession_constraints", lambda _: {"violations": [{"trigger": "em_dash_overuse"}]}
    )
    monkeypatch.setattr(handler, "_format_obsession_constraint_block", lambda _: "Blocked by editorial gate")
    output = tmp_path / "output.md"
    output.write_text("Original candidate")
    assert handler._apply_obsession_constraints_gate(
        tmp_path, "Original candidate", output_path=output, retain_blocked=True
    )
    row = read_failure(tmp_path)
    assert (tmp_path / row["candidate_path"]).read_text() == "Original candidate"
    assert output.read_text() == "Blocked by editorial gate"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    assert handler._apply_obsession_constraints_gate(legacy, "legacy")
    assert not (legacy / "writer_failure.json").exists()


@pytest.mark.parametrize("failure_point", ["candidate", "summary"])
def test_retention_io_failure_cannot_reach_legacy_writer_io_fallback(tmp_path, monkeypatch, failure_point):
    import pathsetup  # noqa: F401
    from agents.writer import handler
    from content_worker import failure

    monkeypatch.setattr(
        handler, "scan_obsession_constraints", lambda _: {"violations": [{"trigger": "em_dash_overuse"}]}
    )
    monkeypatch.setattr(handler, "_format_obsession_constraint_block", lambda _: "blocked")

    def fail(*args):
        raise OSError("disk full")

    if failure_point == "candidate":
        monkeypatch.setattr(failure, "retain_candidate", fail)
    else:
        from pathlib import Path

        original = Path.write_text

        def write(path, *args, **kwargs):
            if path.name == "summary.txt":
                raise OSError("disk full")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", write)
    output = tmp_path / "output.md"
    output.write_text("retained original")
    with pytest.raises(RuntimeError, match="blocked_candidate_retention_failed"):
        handler._apply_obsession_constraints_gate(
            tmp_path, "retained original", output_path=output, retain_blocked=True
        )
    assert output.read_text() == "retained original"
