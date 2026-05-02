from __future__ import annotations

import sys
from types import SimpleNamespace


def test_reply_candidates_stay_out_of_bridge_when_disabled(monkeypatch):
    import twitter

    state = {}
    saved = {}

    monkeypatch.setattr(twitter, "TWITTER_BRIDGE_REPLY_DRAFTS", False)
    monkeypatch.setattr(twitter, "_load_state", lambda: state)
    monkeypatch.setattr(twitter, "_save_state", lambda value: saved.update(value))
    monkeypatch.setattr(twitter, "_get_twitter_config", lambda: {"access_token": "me-token"})
    monkeypatch.setattr(twitter, "get_watchlist", lambda: ["hardmaru"])
    monkeypatch.setitem(
        sys.modules,
        "llm",
        SimpleNamespace(claude_think=lambda *args, **kwargs: "PICK: 1\nREPLY: useful draft"),
    )

    def fake_search_recent_tweets(query, max_results=5):
        return [
            {
                "id": "tweet_1",
                "author_id": "other",
                "_author": {"username": "hardmaru"},
                "text": "This is a long enough tweet about LLM prompt optimization and conductor models.",
                "public_metrics": {"like_count": 5},
            }
        ]

    monkeypatch.setattr(twitter, "search_recent_tweets", fake_search_recent_tweets)

    twitter._find_reply_candidates()

    assert saved["reply_queue"][0]["tweet_id"] == "tweet_1"
    assert saved["reply_queue"][0]["draft_reply"] == "useful draft"
