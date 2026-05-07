from __future__ import annotations

import sys
from types import SimpleNamespace


def test_pending_publish_blocks_quality_gate_without_marking_published(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest
    import publish.writer_gate as writer_gate

    final = tmp_path / "final.md"
    final.write_text("# Essay\n\nBody", encoding="utf-8")
    updates = []
    validate_calls = []

    monkeypatch.setattr(
        manifest,
        "get_next_pending",
        lambda status: {
            "slug": "essay",
            "title": "Essay",
            "final_md": str(final),
            "workspace": str(tmp_path),
            "writer_gate_passed": True,
        },
    )
    monkeypatch.setattr(manifest, "update_manifest", lambda slug, **fields: updates.append((slug, fields)))
    monkeypatch.setattr(
        manifest, "validate_step", lambda *args, **kwargs: validate_calls.append((args, kwargs)) or (True, "")
    )
    monkeypatch.setattr(writer_gate, "require_writer_gate", lambda *args, **kwargs: (True, "", {}))
    monkeypatch.setitem(
        sys.modules,
        "substack",
        SimpleNamespace(
            publish_to_substack=lambda **kwargs: "Substack quality gate blocked publish:\nopening too weak"
        ),
    )

    publishing._check_pending_publish()

    assert updates == [
        (
            "essay",
            {
                "status": "blocked_writer_gate",
                "error": "Substack quality gate blocked publish:\nopening too weak",
            },
        )
    ]
    assert validate_calls == []


def test_pending_publish_no_url_records_error_without_success_transition(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest
    import publish.writer_gate as writer_gate

    final = tmp_path / "final.md"
    final.write_text("# Essay\n\nBody", encoding="utf-8")
    updates = []

    monkeypatch.setattr(
        manifest,
        "get_next_pending",
        lambda status: {
            "slug": "essay",
            "title": "Essay",
            "final_md": str(final),
            "workspace": str(tmp_path),
            "writer_gate_passed": True,
        },
    )
    monkeypatch.setattr(manifest, "update_manifest", lambda slug, **fields: updates.append((slug, fields)))
    monkeypatch.setattr(manifest, "validate_step", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(writer_gate, "require_writer_gate", lambda *args, **kwargs: (True, "", {}))
    monkeypatch.setitem(
        sys.modules, "substack", SimpleNamespace(publish_to_substack=lambda **kwargs: "temporary API failure")
    )

    publishing._check_pending_publish()

    assert updates == [("essay", {"error": "publish returned no URL: temporary API failure"})]


def test_extract_substack_url_strips_trailing_punctuation():
    import publishing

    assert (
        publishing._extract_substack_url("链接: https://mira.substack.com/p/test).")
        == "https://mira.substack.com/p/test"
    )
