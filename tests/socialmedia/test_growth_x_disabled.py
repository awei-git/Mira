from __future__ import annotations

import sys
from types import SimpleNamespace


def test_growth_cycle_skips_x_when_disabled(monkeypatch):
    import growth

    called = {"twitter_promotion": False, "twitter_engagement": False}

    monkeypatch.setattr(growth, "X_PROMOTION_ENABLED", False)
    monkeypatch.setattr(growth, "MIN_POSTS_TO_ENABLE_COMMENTING", 1)
    monkeypatch.setitem(sys.modules, "substack", SimpleNamespace(get_published_post_count=lambda: 99))
    monkeypatch.setattr(growth, "_can_post_note_today", lambda: False)
    monkeypatch.setattr(growth, "run_like_cycle", lambda: None)
    monkeypatch.setattr(growth, "should_discover", lambda: False)
    monkeypatch.setattr(growth, "_follow_up_on_replies", lambda soul_context: None)
    monkeypatch.setattr(growth, "can_comment_now", lambda: False)
    monkeypatch.setattr(growth, "_proactive_note_comment", lambda soul_context: None)
    monkeypatch.setattr(
        growth, "_twitter_promotion", lambda soul_context: called.__setitem__("twitter_promotion", True)
    )
    monkeypatch.setitem(
        sys.modules,
        "twitter",
        SimpleNamespace(run_twitter_engagement=lambda soul_context: called.__setitem__("twitter_engagement", True)),
    )
    monkeypatch.setitem(
        sys.modules,
        "comment_metrics",
        SimpleNamespace(poll_open_records=lambda limit=10: None, attribute_follows=lambda lookback_days=14: None),
    )
    monkeypatch.setitem(sys.modules, "notes", SimpleNamespace(poll_own_notes=lambda: None))

    growth.run_growth_cycle()

    assert called == {"twitter_promotion": False, "twitter_engagement": False}
