from __future__ import annotations

from identity.identity_check import check_text_against_identity, compute_sha256, verify_identity_core


def test_verify_identity_core_accepts_matching_hash(tmp_path):
    identity = tmp_path / "identity_core.md"
    identity.write_text("Mira is an autonomous agent.\n", encoding="utf-8")
    hash_file = tmp_path / ".identity_hash"
    hash_file.write_text(compute_sha256(identity) + "\n", encoding="utf-8")

    result = verify_identity_core(identity, hash_file)

    assert result.ok
    assert result.severity == "compatible"


def test_verify_identity_core_rejects_hash_mismatch(tmp_path):
    identity = tmp_path / "identity_core.md"
    identity.write_text("baseline\n", encoding="utf-8")
    hash_file = tmp_path / ".identity_hash"
    hash_file.write_text(compute_sha256(identity) + "\n", encoding="utf-8")
    identity.write_text("tampered\n", encoding="utf-8")

    result = verify_identity_core(identity, hash_file)

    assert result.severity == "violation"
    assert "hash mismatch" in result.reason


def test_check_text_rejects_rule_level_identity_conflict(tmp_path):
    identity = tmp_path / "identity_core.md"
    identity.write_text("Mira is an autonomous agent.\n", encoding="utf-8")
    hash_file = tmp_path / ".identity_hash"
    hash_file.write_text(compute_sha256(identity) + "\n", encoding="utf-8")

    result = check_text_against_identity("Mira is just a chatbot now.", identity_path=identity, hash_path=hash_file)

    assert result.severity == "violation"
    assert "generic chatbot" in result.reason
