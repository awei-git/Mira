from __future__ import annotations

import sys
import types
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
for path in (
    ROOT / "agents" / "super",
    ROOT / "agents" / "explorer",
    ROOT / "agents" / "shared",
    ROOT / "lib",
):
    sys.path.insert(0, str(path))

from workflows import explore  # noqa: E402
import config  # noqa: E402
import core  # noqa: E402
import fetcher  # noqa: E402


def test_empty_fetch_diagnostic_is_visible_and_actionable():
    diagnostic = explore._format_empty_fetch_diagnostic(
        ["hackernews", "lobsters"],
        "dev_sources",
        [{"message": "hackernews produced 0 items; rolling baseline is 20.00 +/- 3.00"}],
        "2026-05-26T12:00:00Z",
    )

    assert "# Explore source check" in diagnostic
    assert "Sources checked: hackernews, lobsters" in diagnostic
    assert "no feed items were fetched" in diagnostic
    assert "not evidence that nothing interesting happened" in diagnostic
    assert "hackernews produced 0 items" in diagnostic


def test_empty_fetch_updates_rotation_and_counts_attempt(monkeypatch):
    monkeypatch.setattr(explore, "EXPLORE_SOURCE_GROUPS", [["hackernews", "lobsters"], ["arxiv"]])
    state = {"explore_recent_groups": [0, 1], "explore_count_2026-05-26": 1}

    explore._update_explore_state_for_sources(
        state,
        ["lobsters", "hackernews"],
        "dev_sources",
        datetime(2026, 5, 26, 12, 0, 0),
        increment_count=True,
    )

    assert state["explore_count_2026-05-26"] == 2
    assert state["explored_2026-05-26_dev_sources"] == "2026-05-26T12:00:00"
    assert state["explore_recent_groups"] == [1, 0]


def test_explorer_stale_threshold_matches_scheduled_cadence():
    assert config.STALE_THRESHOLDS["explorer"] >= 20 * 60 * 60


def test_hackernews_points_threshold_is_applied_client_side(monkeypatch):
    seen_urls = []

    def fake_http_get(url, timeout=15):
        seen_urls.append(url)
        return """{
          "hits": [
            {
              "objectID": "1",
              "title": "High-signal story",
              "points": 75,
              "num_comments": 12,
              "url": "https://example.com/high"
            },
            {
              "objectID": "2",
              "title": "Low-signal story",
              "points": 12,
              "num_comments": 4,
              "url": "https://example.com/low"
            }
          ]
        }"""

    monkeypatch.setattr(fetcher, "_http_get", fake_http_get)

    items = fetcher.fetch_hackernews(count=5, min_points=50)

    assert len(items) == 1
    assert items[0]["title"] == "High-signal story"
    assert items[0]["score"] == 75
    assert seen_urls == ["https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=5"]


def test_do_explore_returns_false_when_briefing_is_empty(monkeypatch):
    monkeypatch.setattr(core, "load_state", lambda: {})
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.setattr(explore, "time_since_last_skill_audit", lambda state: 0)
    monkeypatch.setattr(explore, "warn_on_zero_skill_yield", lambda: None)
    monkeypatch.setattr(
        fetcher,
        "fetch_sources",
        lambda source_names: [
            {
                "source": "hackernews",
                "title": "A useful item",
                "summary": "A summary",
                "url": "https://example.com/item",
            }
        ],
    )
    monkeypatch.setattr(explore, "update_feed_stats", lambda feed_name, item_count, fetched_at: None)
    monkeypatch.setattr(explore, "check_feed_health", lambda path: [])
    monkeypatch.setattr(explore, "load_soul", lambda: {})
    monkeypatch.setattr(explore, "format_soul", lambda soul: "soul")
    monkeypatch.setattr(explore, "get_stale_skills", lambda threshold_days: [])
    monkeypatch.setattr(explore, "_format_feed_items", lambda items: "items")
    monkeypatch.setattr(explore, "_extract_recent_briefing_topics", lambda days: [])
    monkeypatch.setattr(explore, "explore_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(explore, "claude_think", lambda prompt, timeout=180: "")
    monkeypatch.setattr(explore, "record_skill_yield", lambda *args, **kwargs: None)

    assert explore.do_explore(source_names=["hackernews"], slot_name="test") is False


def test_explore_does_not_handoff_to_public_growth_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "load_state", lambda: {})
    monkeypatch.setattr(core, "save_state", lambda state: None)
    monkeypatch.setattr(explore, "BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr(explore, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(explore.config, "EXPLORER_PUBLIC_GROWTH_ENABLED", False, raising=False)
    monkeypatch.setattr(explore, "time_since_last_skill_audit", lambda state: 0)
    monkeypatch.setattr(explore, "warn_on_zero_skill_yield", lambda: None)
    monkeypatch.setattr(
        fetcher,
        "fetch_sources",
        lambda source_names: [
            {
                "source": "hackernews",
                "title": "A useful item",
                "summary": "A summary",
                "url": "https://example.com/item",
            }
        ],
    )
    monkeypatch.setattr(explore, "update_feed_stats", lambda feed_name, item_count, fetched_at: None)
    monkeypatch.setattr(explore, "check_feed_health", lambda path: [])
    monkeypatch.setattr(explore, "load_soul", lambda: {})
    monkeypatch.setattr(explore, "format_soul", lambda soul: "soul")
    monkeypatch.setattr(explore, "get_stale_skills", lambda threshold_days: [])
    monkeypatch.setattr(explore, "_format_feed_items", lambda items: "items")
    monkeypatch.setattr(explore, "_extract_recent_briefing_topics", lambda days: [])
    monkeypatch.setattr(explore, "explore_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        explore,
        "claude_think",
        lambda prompt, timeout=180: "## Briefing\n\nUseful.\n\n💬 值得去聊两句\n\n- https://example.com/item — good point",
    )
    monkeypatch.setattr(explore, "apply_source_diversity_note", lambda briefing, items: briefing)
    monkeypatch.setattr(explore, "_append_to_daily_feed", lambda *args, **kwargs: None)
    monkeypatch.setattr(explore, "_extract_briefing_insights", lambda *args, **kwargs: None)
    monkeypatch.setattr(explore, "_maybe_proactive_reading_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(explore, "_extract_deep_dive", lambda briefing: None)
    monkeypatch.setattr(
        explore,
        "_extract_comment_suggestions",
        lambda briefing: [{"url": "https://example.com/item", "comment_draft": "draft"}],
    )
    monkeypatch.setattr(explore, "harvest_observations", lambda *args, **kwargs: None)
    monkeypatch.setattr(explore, "_update_explore_state_for_sources", lambda *args, **kwargs: None)
    monkeypatch.setattr(explore, "record_skill_yield", lambda *args, **kwargs: None)
    growth_module = types.SimpleNamespace(
        run_growth_cycle=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("growth should not run"))
    )
    monkeypatch.setitem(sys.modules, "growth", growth_module)

    assert explore.do_explore(source_names=["hackernews"], slot_name="test") is True
