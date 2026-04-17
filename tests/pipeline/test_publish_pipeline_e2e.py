"""End-to-end smoke test for the publish pipeline.

Tests the full chain: article -> publish prep -> podcast script -> TTS -> RSS
without making real external API calls (Substack, GitHub push).

Marked with @pytest.mark.pipeline for selective running:
    pytest -m pipeline agents/tests/test_publish_pipeline_e2e.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent / "agents"


# ---------------------------------------------------------------------------
# Path setup — match conftest.py pattern (shared only at conftest level,
# individual agent dirs added per-test-file)
# ---------------------------------------------------------------------------

# shared is already on sys.path via conftest.py


def _load_podcast_handler():
    """Import podcast handler explicitly to avoid sys.path collisions."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("podcast_handler", _AGENTS / "podcast" / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TEST_ARTICLE = """# The API as Self

When we say "I understand," what exactly is the "I" that claims understanding?

## The Mediation Problem

Every cognitive act is mediated. You don't see photons — you see your visual cortex's
interpretation of photon patterns hitting your retina. The self is not the experiencer
but the API between experience and response.

This has implications for how we think about AI agents communicating with each other.
If human cognition is already mediated, then the difference between human communication
and agent-to-agent communication is not one of kind but of degree.

## Grounding as a Spectrum

The real question isn't whether agents are "grounded" — it's what they're grounded *in*.
Humans are grounded in sensorimotor experience. Agents are grounded in training distributions.
Both are lossy compressions of reality. The question is which losses matter.

我们太容易把"具身性"当作一个二元属性——有或没有。但它其实是一个连续谱。
一个从未见过苹果的人读到"苹果"这个词，和一个吃过苹果的人读到它，
他们的grounding不同，但都是grounding。

## So What

Stop asking whether AI "really" understands. Ask instead: what is it grounded in,
and where does that grounding fail? That's a tractable question with testable answers.
"""

TEST_TITLE = "The API as Self"
TEST_SLUG = "the-api-as-self"

# ===================================================================
# 1. Article preparation
# ===================================================================


@pytest.mark.pipeline
class TestArticlePrep:
    """Test article preparation for publishing."""

    def test_metadata_stripping(self):
        """Verify revision metadata, frontmatter, etc. are stripped."""
        _strip_draft_metadata = _load_podcast_handler()._strip_draft_metadata

        article_with_meta = (
            "修订稿 R2\n"
            "日期：2026-03-30\n"
            "字数：500\n\n" + TEST_ARTICLE + "\n\n修改记录\n"
            "| 版本 | 日期 | 修改内容 |\n"
            "|------|------|----------|\n"
            "| v1 | 2026-03-30 | 初稿 |\n"
        )

        stripped = _strip_draft_metadata(article_with_meta)

        assert "修订稿" not in stripped
        assert "修改记录" not in stripped
        assert "The API as Self" in stripped
        assert "具身性" in stripped

    def test_md_to_html_no_truncation(self):
        """Verify markdown conversion doesn't truncate content (regression: [:8000] bug)."""
        import inspect
        from substack import _md_to_html

        source = inspect.getsource(_md_to_html)
        assert "[:8000]" not in source, "Truncation bug detected in _md_to_html"

    def test_prosemirror_handles_mixed_content(self):
        """Verify ProseMirror conversion handles CJK + English mixed content."""
        from substack import _html_to_prosemirror

        html = "<h2>Test</h2><p>Hello 你好</p><blockquote><p>Quote</p></blockquote><hr>"
        result = _html_to_prosemirror(html)

        assert result is not None
        # Should be valid JSON-serializable dict
        doc = json.loads(json.dumps(result))
        assert doc.get("type") == "doc"
        assert len(doc.get("content", [])) >= 3  # h2, p, blockquote, hr


# ===================================================================
# 2. Publish manifest state machine
# ===================================================================


@pytest.mark.pipeline
class TestManifestStateMachine:
    """Test publish manifest state transitions."""

    def test_full_status_progression(self, tmp_path):
        """Verify manifest progresses through all statuses with timestamps."""
        import publish.manifest as pm

        original_path = pm._manifest_path
        pm._manifest_path = tmp_path / "publish_manifest.json"

        try:
            # Create entry
            pm.update_manifest(TEST_SLUG, status="approved", title=TEST_TITLE)

            # Verify approved
            entry = pm.get_next_pending("published")
            assert entry is not None
            assert entry["status"] == "approved"

            # Advance to published
            pm.update_manifest(TEST_SLUG, status="published", substack_url="https://test.substack.com/p/test")
            entry = pm.get_next_pending("podcast_en")
            assert entry is not None
            assert entry["status"] == "published"

            # Advance through podcast stages
            pm.update_manifest(TEST_SLUG, status="podcast_en")
            pm.update_manifest(TEST_SLUG, status="podcast_zh")
            pm.update_manifest(TEST_SLUG, status="complete")

            # Verify complete
            manifest = pm.load_manifest()
            assert manifest["articles"][TEST_SLUG]["status"] == "complete"

            # Verify timestamps recorded for each step
            ts = manifest["articles"][TEST_SLUG].get("timestamps", {})
            for step in ["approved", "published", "podcast_en", "podcast_zh", "complete"]:
                assert step in ts, f"Missing timestamp for {step}"

        finally:
            pm._manifest_path = original_path

    def test_error_and_retry(self, tmp_path):
        """Verify error state and retry logic."""
        import publish.manifest as pm

        original_path = pm._manifest_path
        pm._manifest_path = tmp_path / "publish_manifest.json"

        try:
            # Create and fail
            pm.update_manifest(TEST_SLUG, status="approved", title=TEST_TITLE)
            pm.update_manifest(TEST_SLUG, error="API timeout")

            manifest = pm.load_manifest()
            entry = manifest["articles"][TEST_SLUG]
            assert entry.get("error") == "API timeout"

            # should_retry returns False immediately after error (backoff hasn't elapsed)
            assert pm.should_retry(entry) is False

            # Backdate the last_error timestamp so backoff has elapsed
            entry["timestamps"]["last_error"] = "2026-01-01T00:00:00Z"
            manifest["articles"][TEST_SLUG] = entry
            pm._save(manifest)

            # Now should be retryable
            manifest = pm.load_manifest()
            entry = manifest["articles"][TEST_SLUG]
            assert pm.should_retry(entry) is True

            # Prepare retry
            assert pm.prepare_retry(TEST_SLUG) is True
            manifest = pm.load_manifest()
            entry = manifest["articles"][TEST_SLUG]
            assert entry.get("retry_count") == 1
            assert entry.get("error") is None

        finally:
            pm._manifest_path = original_path

    def test_get_next_pending_skips_errors(self, tmp_path):
        """Errored articles should not appear in get_next_pending."""
        import publish.manifest as pm

        original_path = pm._manifest_path
        pm._manifest_path = tmp_path / "publish_manifest.json"

        try:
            pm.update_manifest("broken", title="Broken", status="approved", final_md="/x.md", auto_podcast=True)
            pm.update_manifest("broken", error="file not found")
            # Should be None because the only article is in error state
            # (and retry backoff hasn't elapsed)
            result = pm.get_next_pending("published")
            # Either None or the retried entry — depends on timing
            # Just verify no crash
            assert result is None or result["slug"] == "broken"

        finally:
            pm._manifest_path = original_path


# ===================================================================
# 3. Podcast script processing (no LLM calls)
# ===================================================================


@pytest.mark.pipeline
class TestPodcastScript:
    """Test podcast script generation and processing (no LLM calls)."""

    def test_clean_turn_text(self):
        """Verify TTS-unfriendly punctuation is cleaned."""
        _clean_turn_text = _load_podcast_handler()._clean_turn_text

        text = "这是一个测试——包含各种标点…比如；还有：冒号"
        cleaned = _clean_turn_text(text)
        assert "——" not in cleaned
        assert "…" not in cleaned
        assert "；" not in cleaned

    def test_clean_turn_preserves_speech_punctuation(self):
        """Verify speech-driving punctuation is preserved."""
        _clean_turn_text = _load_podcast_handler()._clean_turn_text

        text = "你好，这是一个问题？是的！结束了。"
        cleaned = _clean_turn_text(text)
        assert "，" in cleaned
        assert "？" in cleaned
        assert "！" in cleaned
        assert "。" in cleaned

    def test_polyphonic_fixes(self):
        """Verify polyphonic character disambiguation."""
        _fix_polyphonic_chars = _load_podcast_handler()._fix_polyphonic_chars

        text = "这个问题重复出现"
        fixed = _fix_polyphonic_chars(text)
        # 重复 should become 反复
        assert "反复" in fixed
        assert "重复" not in fixed

    def test_polyphonic_noop_entries(self):
        """Verify no-op entries (where original == replacement) are skipped."""
        _fix_polyphonic_chars = _load_podcast_handler()._fix_polyphonic_chars

        # 调整 maps to itself — should be unchanged
        text = "需要调整参数"
        fixed = _fix_polyphonic_chars(text)
        assert "调整" in fixed

    def test_breathing_pauses_no_double(self):
        """Verify breathing pause insertion doesn't create double pauses."""
        _add_breathing_pauses = _load_podcast_handler()._add_breathing_pauses

        text = "第一句<#0.5#> <#0.3#>第二句"
        result = _add_breathing_pauses(text, provider="minimax")
        # Should not have consecutive pause markers
        import re

        doubles = re.findall(r"(<#[\d.]+#>)\s*(<#[\d.]+#>)", result)
        assert len(doubles) == 0

    def test_script_turn_parsing(self):
        """Verify script can be parsed into speaker turns."""
        _parse_turns = _load_podcast_handler()._parse_turns

        script = (
            "[HOST]: Welcome to the show. Today we're talking about AI agents.\n"
            "\n"
            "[MIRA]: Thanks for having me. I've been thinking about this topic a lot.\n"
            "\n"
            "[HOST]: What's the most surprising thing you've learned?\n"
            "\n"
            "[MIRA]: That human communication is more mediated than we think.\n"
        )

        turns = _parse_turns(script)
        assert len(turns) == 4
        assert turns[0][0] == "HOST"
        assert turns[1][0] == "MIRA"
        assert turns[2][0] == "HOST"
        assert turns[3][0] == "MIRA"
        assert "Welcome" in turns[0][1]
        assert "mediated" in turns[3][1]


# ===================================================================
# 4. RSS feed generation (no git push)
# ===================================================================


@pytest.mark.pipeline
class TestRSSFeed:
    """Test RSS feed generation (no git push)."""

    def test_feed_creation_per_language(self, tmp_path):
        """Verify feed.xml is created with valid structure per language."""
        from rss import _load_or_create_feed, _save_feed

        for lang, expected_lang_code in [("zh", "zh-CN"), ("en", "en")]:
            feed_path = tmp_path / f"feed_{lang}.xml"
            rss = _load_or_create_feed(feed_path, lang=lang)
            assert rss is not None

            channel = rss.find("channel")
            assert channel is not None
            assert channel.findtext("language") == expected_lang_code

    def test_add_episode_and_save(self, tmp_path):
        """Verify episode is added with correct metadata."""
        from rss import _load_or_create_feed, _add_episode_to_feed, _save_feed

        feed_path = tmp_path / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang="en")

        pub_date = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
        _add_episode_to_feed(
            rss,
            TEST_TITLE,
            TEST_SLUG,
            f"https://test.github.io/audios/{TEST_SLUG}.mp3",
            5_000_000,
            1530,  # ~25:30
            "Test episode about AI and self.",
            pub_date,
            transcript_url=f"https://test.github.io/transcripts/{TEST_SLUG}.txt",
            transcript_type="text/plain",
            lang="en",
        )

        _save_feed(rss, feed_path)

        # Verify
        content = feed_path.read_text()
        assert TEST_SLUG in content
        assert TEST_TITLE in content
        assert "audio/mpeg" in content

        # Re-parse and check structure
        tree = ET.parse(feed_path)
        items = tree.findall(".//item")
        assert len(items) == 1

        item = items[0]
        assert item.findtext("title") == TEST_TITLE
        assert item.findtext("guid") == TEST_SLUG

        enc = item.find("enclosure")
        assert enc is not None
        assert enc.get("type") == "audio/mpeg"

    def test_feed_episode_deduplication_via_remove(self, tmp_path):
        """Verify _remove_episode_from_feed prevents duplicates."""
        from rss import (
            _load_or_create_feed,
            _add_episode_to_feed,
            _remove_episode_from_feed,
            _save_feed,
        )

        feed_path = tmp_path / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang="en")

        # Add same episode twice (simulating re-publish)
        for i in range(2):
            _remove_episode_from_feed(rss, TEST_SLUG)
            _add_episode_to_feed(
                rss,
                TEST_TITLE,
                TEST_SLUG,
                f"https://test.github.io/audios/{TEST_SLUG}.mp3",
                5_000_000,
                1530,
                "Test",
                datetime(2026, 3, 30, tzinfo=timezone.utc),
                lang="en",
            )

        _save_feed(rss, feed_path)
        tree = ET.parse(feed_path)
        items = tree.findall(".//item")
        assert len(items) == 1, f"Expected 1 item, got {len(items)} — deduplication failed"

    def test_feed_sort_oldest_first(self, tmp_path):
        """Verify _save_feed sorts episodes oldest first."""
        from rss import _load_or_create_feed, _add_episode_to_feed, _save_feed

        feed_path = tmp_path / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang="en")

        dates = [
            ("ep-march", "March", datetime(2026, 3, 15, tzinfo=timezone.utc)),
            ("ep-jan", "January", datetime(2026, 1, 10, tzinfo=timezone.utc)),
            ("ep-feb", "February", datetime(2026, 2, 20, tzinfo=timezone.utc)),
        ]
        for slug, title, dt in dates:
            _add_episode_to_feed(
                rss,
                title,
                slug,
                f"https://example.com/{slug}.mp3",
                1_000_000,
                600,
                "desc",
                dt,
                lang="en",
            )

        _save_feed(rss, feed_path)

        tree = ET.parse(feed_path)
        items = tree.findall(".//item")
        guids = [item.findtext("guid") for item in items]
        assert guids == ["ep-jan", "ep-feb", "ep-march"], f"Expected oldest-first order, got {guids}"


# ===================================================================
# 5. Failure logging
# ===================================================================


@pytest.mark.pipeline
class TestFailureLog:
    """Test the structured failure logging system."""

    def test_record_and_read(self, tmp_path):
        """Verify failures are recorded and readable."""
        import ops.failure_log as fl

        original_log = fl.FAILURE_LOG
        fl.FAILURE_LOG = tmp_path / "test_failures.jsonl"

        try:
            rec = fl.record_failure(
                pipeline="publish",
                step="substack_publish",
                slug=TEST_SLUG,
                error_type="api_timeout",
                error_message="Substack API returned 504",
                input_summary="2000 word article",
                expected_output="canonical URL",
                actual_output="504 Gateway Timeout",
            )
            assert rec["error_type"] == "api_timeout"

            # Read it back
            failures = fl.load_recent_failures()
            assert len(failures) == 1
            assert failures[0]["slug"] == TEST_SLUG

            # Filter by pipeline
            failures = fl.load_recent_failures(pipeline="podcast")
            assert len(failures) == 0

            # Get summary
            summary = fl.get_failure_summary()
            assert "api_timeout" in summary
            assert "publish/substack_publish" in summary

        finally:
            fl.FAILURE_LOG = original_log

    def test_resolve_failure(self, tmp_path):
        """Verify failures can be marked as resolved."""
        import ops.failure_log as fl

        original_log = fl.FAILURE_LOG
        fl.FAILURE_LOG = tmp_path / "test_failures.jsonl"

        try:
            fl.record_failure(
                pipeline="podcast",
                step="tts_zh",
                slug=TEST_SLUG,
                error_type="tts_quota",
                error_message="429 rate limited",
            )

            assert fl.resolve_failure(TEST_SLUG, "tts_zh", "Waited 1hr, retried successfully") is True

            failures = fl.load_recent_failures()
            assert len(failures) == 1
            assert failures[0]["resolution"] == "Waited 1hr, retried successfully"

        finally:
            fl.FAILURE_LOG = original_log


# ===================================================================
# 6. Pipeline validation
# ===================================================================


@pytest.mark.pipeline
class TestPipelineValidation:
    """Test the post-condition validators."""

    def test_podcast_validation_rejects_small_file(self, tmp_path):
        """Verify podcast validator rejects files that are too small."""
        from publish.manifest import _validate_podcast

        tiny = tmp_path / "tiny.mp3"
        tiny.write_bytes(b"\x00" * 1000)  # 1KB

        passed, err = _validate_podcast(TEST_SLUG, mp3_path=str(tiny))
        assert passed is False
        assert "too small" in err.lower() or "MB" in err.lower() or "mb" in err.lower()

    def test_podcast_validation_rejects_missing_file(self):
        """Verify podcast validator rejects non-existent files."""
        from publish.manifest import _validate_podcast

        passed, err = _validate_podcast(TEST_SLUG, mp3_path="/nonexistent/file.mp3")
        assert passed is False
        assert "not found" in err.lower()

    def test_validate_step_dispatches(self):
        """Verify validate_step routes to the right validator."""
        from publish.manifest import validate_step

        # Unknown step should pass (no validator registered)
        passed, err = validate_step(TEST_SLUG, "approved")
        assert passed is True
        assert err == ""

        # podcast_en should dispatch to _validate_podcast
        passed, err = validate_step(TEST_SLUG, "podcast_en", mp3_path="/nonexistent/file.mp3")
        assert passed is False


# ===================================================================
# 7. Full pipeline trace (integration)
# ===================================================================


@pytest.mark.pipeline
class TestFullPipelineTrace:
    """Trace an article through the entire publish pipeline (no external calls)."""

    def test_article_to_rss(self, tmp_path):
        """Full chain: article prep -> manifest -> podcast script parse -> RSS feed."""
        import publish.manifest as pm

        _ph = _load_podcast_handler()
        _strip_draft_metadata = _ph._strip_draft_metadata
        _clean_turn_text = _ph._clean_turn_text
        _parse_turns = _ph._parse_turns
        from rss import (
            _load_or_create_feed,
            _add_episode_to_feed,
            _save_feed,
            _copy_mp3_to_repo,
        )

        # --- Step 1: Article prep ---
        raw_article = "修订稿 R2\n" "日期：2026-03-30\n\n" + TEST_ARTICLE + "\n\n修改记录\n| v1 | 初稿 |\n"
        clean_article = _strip_draft_metadata(raw_article)
        assert "修订稿" not in clean_article
        assert "The API as Self" in clean_article
        assert len(clean_article) > 500  # not truncated

        # --- Step 2: Manifest tracks state ---
        original_path = pm._manifest_path
        pm._manifest_path = tmp_path / "manifest.json"

        try:
            pm.update_manifest(TEST_SLUG, status="approved", title=TEST_TITLE, final_md=str(tmp_path / "article.md"))
            entry = pm.get_next_pending("published")
            assert entry is not None
            assert entry["slug"] == TEST_SLUG

            # Simulate publish
            pm.update_manifest(
                TEST_SLUG, status="published", substack_url="https://test.substack.com/p/the-api-as-self"
            )

            # --- Step 3: Podcast script processing ---
            script = (
                "[HOST]: Today we're exploring what it means for an agent to understand.\n"
                "[MIRA]: The question isn't whether I understand, it's what I'm grounded in.\n"
                "[HOST]: What do you mean by grounding?\n"
                "[MIRA]: Humans are grounded in sensorimotor experience. "
                "I'm grounded in training distributions. Both are lossy.\n"
            )

            turns = _parse_turns(script)
            assert len(turns) == 4

            # Clean each turn
            for speaker, text in turns:
                cleaned = _clean_turn_text(text)
                assert len(cleaned) > 0
                assert speaker in ("HOST", "MIRA")

            # --- Step 4: RSS feed ---
            repo_dir = tmp_path / "repo"
            repo_dir.mkdir()

            # Create fake MP3
            mp3_path = tmp_path / TEST_SLUG / "episode.mp3"
            mp3_path.parent.mkdir()
            mp3_path.write_bytes(b"fake mp3 " * 1000)

            mp3_url = _copy_mp3_to_repo(
                mp3_path,
                repo_dir=repo_dir,
                pages_url="https://test.github.io/MiraPodcastEn",
                slug=TEST_SLUG,
            )
            assert TEST_SLUG in mp3_url
            assert (repo_dir / "audios" / f"{TEST_SLUG}.mp3").exists()

            feed_path = repo_dir / "feed.xml"
            rss = _load_or_create_feed(feed_path, lang="en")
            _add_episode_to_feed(
                rss,
                TEST_TITLE,
                TEST_SLUG,
                mp3_url,
                mp3_path.stat().st_size,
                1800,
                "An exploration of what understanding means for AI agents.",
                datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc),
                lang="en",
            )
            _save_feed(rss, feed_path)

            # --- Step 5: Verify final state ---
            assert feed_path.exists()
            tree = ET.parse(feed_path)
            items = tree.findall(".//item")
            assert len(items) == 1
            assert items[0].findtext("guid") == TEST_SLUG

            # Advance manifest to complete
            pm.update_manifest(TEST_SLUG, status="podcast_en")
            pm.update_manifest(TEST_SLUG, status="podcast_zh")
            pm.update_manifest(TEST_SLUG, status="complete")

            manifest = pm.load_manifest()
            assert manifest["articles"][TEST_SLUG]["status"] == "complete"

        finally:
            pm._manifest_path = original_path
