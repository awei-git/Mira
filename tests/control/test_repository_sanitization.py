from __future__ import annotations


def test_strip_nul_text_recurses_through_legacy_payload():
    from control.repository import _strip_nul_text

    payload = {
        "title": "daily\x00collab",
        "messages": [{"content": "hello\x00there"}],
        "tags": ["daily\x00-collab"],
    }

    assert _strip_nul_text(payload) == {
        "title": "dailycollab",
        "messages": [{"content": "hellothere"}],
        "tags": ["daily-collab"],
    }
