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


def test_pending_publish_skips_twitter_when_x_disabled(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest
    import publish.writer_gate as writer_gate

    final = tmp_path / "final.md"
    final.write_text("# Essay\n\nBody", encoding="utf-8")
    updates = []
    queued_notes = []
    tweeted = []

    monkeypatch.setattr(publishing, "X_PROMOTION_ENABLED", False)
    monkeypatch.setattr(publishing, "Mira", lambda: SimpleNamespace(update_status=lambda *args, **kwargs: None))
    monkeypatch.setattr(
        manifest,
        "get_next_pending",
        lambda status: {
            "slug": "essay",
            "title": "Essay",
            "subtitle": "Subtitle",
            "final_md": str(final),
            "workspace": str(tmp_path),
            "writer_gate_passed": True,
        },
    )
    monkeypatch.setattr(manifest, "update_manifest", lambda slug, **fields: updates.append((slug, fields)))
    monkeypatch.setattr(manifest, "validate_step", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(writer_gate, "require_writer_gate", lambda *args, **kwargs: (True, "", {}))
    monkeypatch.setitem(
        sys.modules,
        "substack",
        SimpleNamespace(
            publish_to_substack=lambda **kwargs: "已发布到 Substack!\n链接: https://mira.substack.com/p/essay"
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "notes",
        SimpleNamespace(queue_notes_for_article=lambda *args, **kwargs: queued_notes.append((args, kwargs))),
    )
    monkeypatch.setitem(
        sys.modules,
        "twitter",
        SimpleNamespace(tweet_for_article=lambda *args, **kwargs: tweeted.append((args, kwargs))),
    )

    publishing._check_pending_publish()

    assert ("essay", {"status": "published", "substack_url": "https://mira.substack.com/p/essay"}) in updates
    assert queued_notes
    assert tweeted == []


def test_extract_substack_url_strips_trailing_punctuation():
    import publishing

    assert (
        publishing._extract_substack_url("链接: https://mira.substack.com/p/test).")
        == "https://mira.substack.com/p/test"
    )


def test_pending_podcast_dispatch_does_not_spend_weekly_quota(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest

    monkeypatch.setattr(publishing, "AUTO_PODCAST_ENABLED", True)
    final = tmp_path / "final.md"
    final.write_text("# Essay\n\nBody", encoding="utf-8")
    state = {}
    saved = []
    dispatched = []

    monkeypatch.setattr(publishing, "load_state", lambda: state)
    monkeypatch.setattr(publishing, "save_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "articles": {
                "essay": {
                    "slug": "essay",
                    "title": "Essay",
                    "status": "published",
                    "auto_podcast": True,
                    "final_md": str(final),
                    "timestamps": {"published": "2026-05-15T10:00:00Z"},
                }
            }
        },
    )
    monkeypatch.setattr(manifest, "update_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(publishing, "_dispatch_background", lambda name, cmd: dispatched.append((name, cmd)) or True)

    publishing._check_pending_podcast()

    assert dispatched and dispatched[0][0] == "podcast-en-essay"
    assert "podcast_en_week" not in state
    assert state["podcast_en_dispatch"]["slug"] == "essay"
    assert saved == [state]


def test_pending_podcast_syncs_quota_from_verified_manifest_transition(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest

    monkeypatch.setattr(publishing, "AUTO_PODCAST_ENABLED", True)
    state = {}
    saved = []
    dispatched = []
    now = publishing.datetime.now()
    current_week = now.strftime("%Y-W%W")

    monkeypatch.setattr(publishing, "load_state", lambda: state)
    monkeypatch.setattr(publishing, "save_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "articles": {
                "essay": {
                    "slug": "essay",
                    "title": "Essay",
                    "status": "podcast_en",
                    "auto_podcast": True,
                    "final_md": str(tmp_path / "final.md"),
                    "timestamps": {"podcast_en": now.strftime("%Y-%m-%dT10:00:00Z")},
                }
            }
        },
    )
    monkeypatch.setattr(manifest, "update_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(publishing, "_dispatch_background", lambda name, cmd: dispatched.append((name, cmd)) or True)

    publishing._check_pending_podcast()

    assert state["podcast_en_week"] == current_week
    assert saved == [state]
    assert not any(name.startswith("podcast-en-") for name, _cmd in dispatched)


def test_pending_podcast_disabled_does_not_dispatch(monkeypatch, tmp_path):
    import publishing
    import publish.manifest as manifest

    final = tmp_path / "final.md"
    final.write_text("# Essay\n\nBody", encoding="utf-8")
    state = {}
    dispatched = []

    monkeypatch.setattr(publishing, "AUTO_PODCAST_ENABLED", False)
    monkeypatch.setattr(publishing, "load_state", lambda: state)
    monkeypatch.setattr(
        manifest,
        "load_manifest",
        lambda: {
            "articles": {
                "essay": {
                    "slug": "essay",
                    "title": "Essay",
                    "status": "published",
                    "auto_podcast": True,
                    "final_md": str(final),
                }
            }
        },
    )
    monkeypatch.setattr(publishing, "_dispatch_background", lambda name, cmd: dispatched.append((name, cmd)) or True)

    publishing._check_pending_podcast()

    assert dispatched == []
    assert state == {}
