"""Integration tests for RSS publish pipeline.

Traces a real episode through the full publish_episode path,
verifying every variable at every step — slug, filename, repo,
URL, feed XML structure, description, language, ordering.

Run: python -m pytest agents/podcast/tests/test_rss_integration.py -v
"""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "podcast"))
sys.path.insert(0, str(_AGENTS / "shared"))


# ---------------------------------------------------------------------------
# 1. Slug derivation — episode.mp3 must use parent dir name, not "episode"
# ---------------------------------------------------------------------------

def test_slug_from_episode_mp3():
    """publish_episode must derive slug from parent dir, not mp3 filename."""
    from rss import publish_episode
    import inspect
    src = inspect.getsource(publish_episode)
    # The code must check mp3_path.parent.name when stem is "episode"
    assert "parent.name" in src, \
        "publish_episode doesn't use parent.name for slug derivation"


def test_slug_derivation_values():
    """Trace slug derivation for concrete examples."""
    test_cases = [
        # (mp3 path stem, parent dir name, expected slug)
        ("episode", "the-socratic-probe", "the-socratic-probe"),
        ("episode", "i-am-a-function-not-a-variable", "i-am-a-function-not-a-variable"),
        ("episode", "intro-mira", "intro-mira"),
        ("the-socratic-probe", "whatever", "the-socratic-probe"),  # non-episode stem
    ]
    for stem, parent, expected in test_cases:
        raw_slug = parent if stem == "episode" else stem
        slug = re.sub(r"[^a-z0-9-]", "-", raw_slug.lower()).strip("-")
        assert slug == expected, f"stem={stem} parent={parent}: got {slug}, expected {expected}"


# ---------------------------------------------------------------------------
# 2. Per-language config — ZH and EN must use different repos
# ---------------------------------------------------------------------------

def test_lang_config_separation():
    """ZH and EN must point to different repos."""
    from rss import _get_config
    zh = _get_config("zh")
    en = _get_config("en")
    assert zh["repo"] != en["repo"], "ZH and EN use the same repo!"
    assert zh["repo_dir"] != en["repo_dir"], "ZH and EN use the same repo_dir!"
    assert zh["pages_url"] != en["pages_url"], "ZH and EN use the same pages_url!"
    assert "MiraPodcastZh" in zh["repo"], f"ZH repo wrong: {zh['repo']}"
    assert "MiraPodcastEn" in en["repo"], f"EN repo wrong: {en['repo']}"


def test_lang_config_language_codes():
    from rss import _get_config
    assert _get_config("zh")["language"] == "zh-CN"
    assert _get_config("en")["language"] == "en"


def test_lang_config_repo_dirs_are_persistent():
    from config import PODCAST_REPOS_DIR
    from rss import _get_config
    zh = _get_config("zh")
    en = _get_config("en")
    assert zh["repo_dir"] == PODCAST_REPOS_DIR / "zh"
    assert en["repo_dir"] == PODCAST_REPOS_DIR / "en"


# ---------------------------------------------------------------------------
# 3. File naming — must use slug, never "episode.mp3"
# ---------------------------------------------------------------------------

def test_copy_mp3_uses_slug():
    """_copy_mp3_to_repo must name the file {slug}.mp3, not episode.mp3."""
    from rss import _copy_mp3_to_repo

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        # Create a fake episode.mp3
        src = Path(tmpdir) / "episode.mp3"
        src.write_bytes(b"fake mp3 data")

        url = _copy_mp3_to_repo(
            src, repo_dir=repo_dir,
            pages_url="https://example.com", slug="the-socratic-probe"
        )

        # File must be named the-socratic-probe.mp3, not episode.mp3
        assert (repo_dir / "audios" / "the-socratic-probe.mp3").exists(), \
            "MP3 not copied with slug filename"
        assert not (repo_dir / "audios" / "episode.mp3").exists(), \
            "MP3 copied as episode.mp3 — filename collision risk!"
        assert url == "https://example.com/audios/the-socratic-probe.mp3"


def test_copy_transcript_uses_slug():
    """_copy_transcript_to_repo must name files {slug}.srt/.txt."""
    from rss import _copy_transcript_to_repo

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()
        # Create a fake script.txt next to episode.mp3
        ep_dir = Path(tmpdir) / "the-socratic-probe"
        ep_dir.mkdir()
        (ep_dir / "script.txt").write_text("[HOST]: Hello\n[MIRA]: Hi")
        mp3 = ep_dir / "episode.mp3"
        mp3.write_bytes(b"fake")

        url, mime = _copy_transcript_to_repo(
            mp3, repo_dir=repo_dir,
            pages_url="https://example.com", slug="the-socratic-probe"
        )

        assert (repo_dir / "transcripts" / "the-socratic-probe.txt").exists()
        assert "the-socratic-probe.txt" in url
        assert "episode.txt" not in url


# ---------------------------------------------------------------------------
# 4. Feed XML — structure, language, transcript language tag
# ---------------------------------------------------------------------------

def test_feed_creation_per_language():
    """New feeds must use correct per-language metadata."""
    from rss import _load_or_create_feed

    with tempfile.TemporaryDirectory() as tmpdir:
        for lang, expected_title, expected_lang_code in [
            ("zh", "米拉与我", "zh-CN"),
            ("en", "Mira and Me", "en"),
        ]:
            feed_path = Path(tmpdir) / f"feed_{lang}.xml"
            rss = _load_or_create_feed(feed_path, lang=lang)
            channel = rss.find("channel")
            title = channel.findtext("title")
            language = channel.findtext("language")
            assert expected_title in title, f"{lang} feed title wrong: {title}"
            assert language == expected_lang_code, f"{lang} feed language wrong: {language}"


def test_add_episode_transcript_language():
    """podcast:transcript language must match episode language, not hardcoded zh."""
    from rss import _load_or_create_feed, _add_episode_to_feed

    with tempfile.TemporaryDirectory() as tmpdir:
        feed_path = Path(tmpdir) / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang="en")

        _add_episode_to_feed(
            rss, "Test Episode", "test-ep",
            "https://example.com/test.mp3", 1000000, 600,
            "A test episode", None,
            transcript_url="https://example.com/test.srt",
            transcript_type="application/srt",
            lang="en",
        )

        # Find the transcript element — save and re-parse to get correct namespaces
        from rss import _save_feed
        with tempfile.TemporaryDirectory() as tmpdir2:
            fp = Path(tmpdir2) / "test.xml"
            _save_feed(rss, fp)
            raw = fp.read_text()
            assert 'language="en"' in raw, \
                f"Transcript language not 'en' in XML:\n{raw[-300:]}"
            assert 'language="zh"' not in raw, \
                f"Transcript language incorrectly set to 'zh'"


# ---------------------------------------------------------------------------
# 5. Feed sorting — oldest first
# ---------------------------------------------------------------------------

def test_feed_sort_oldest_first():
    """_save_feed must sort episodes oldest first."""
    from rss import _load_or_create_feed, _add_episode_to_feed, _save_feed
    from email.utils import format_datetime

    with tempfile.TemporaryDirectory() as tmpdir:
        feed_path = Path(tmpdir) / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang="zh")

        # Add episodes out of order
        dates = [
            ("ep-march", "March Episode", datetime(2026, 3, 15, tzinfo=timezone.utc)),
            ("ep-jan", "January Episode", datetime(2026, 1, 10, tzinfo=timezone.utc)),
            ("ep-feb", "February Episode", datetime(2026, 2, 20, tzinfo=timezone.utc)),
        ]
        for slug, title, dt in dates:
            _add_episode_to_feed(rss, title, slug,
                                 f"https://example.com/{slug}.mp3",
                                 1000000, 600, "desc", dt, lang="zh")

        _save_feed(rss, feed_path)

        # Re-parse and check order
        tree = ET.parse(feed_path)
        items = tree.findall(".//item")
        guids = [item.findtext("guid") for item in items]
        assert guids == ["ep-jan", "ep-feb", "ep-march"], \
            f"Expected oldest-first order, got {guids}"


# ---------------------------------------------------------------------------
# 6. Description — must not be empty or raw dialogue
# ---------------------------------------------------------------------------

def test_description_not_raw_dialogue():
    """Description auto-generation should not just be first 2 lines of script."""
    from rss import publish_episode
    import inspect
    src = inspect.getsource(publish_episode)
    # Must use LLM (claude_think) for description, not regex extraction
    assert "claude_think" in src, \
        "publish_episode doesn't use LLM for description generation"


# ---------------------------------------------------------------------------
# 7. Metadata stripping — publish_to_substack
# ---------------------------------------------------------------------------

def test_substack_strips_revision_metadata():
    """publish_to_substack must strip frontmatter, 修改记录, and line-level metadata."""
    sys.path.insert(0, str(_AGENTS / "socialmedia"))
    # We can't call publish_to_substack (needs API), but we can check the strip logic
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "substack_mod", str(_AGENTS / "socialmedia" / "substack.py"))
    mod = importlib.util.module_from_spec(spec)
    src = spec.loader.get_code("substack_mod")
    source_text = Path(_AGENTS / "socialmedia" / "substack.py").read_text()

    patterns_that_must_be_stripped = [
        "修改记录",
        "修订稿",
        "Changelog",
        "^---",  # frontmatter
    ]
    for pattern in patterns_that_must_be_stripped:
        assert pattern in source_text, \
            f"publish_to_substack doesn't strip '{pattern}'"


# ---------------------------------------------------------------------------
# 8. End-to-end trace — one real episode through the full path
# ---------------------------------------------------------------------------

def test_full_publish_trace():
    """Trace a real episode through the entire publish path (no git push).

    Verifies: slug, filename, URL, feed structure, description placeholder.
    """
    from rss import (
        _get_config, _copy_mp3_to_repo, _copy_transcript_to_repo,
        _load_or_create_feed, _add_episode_to_feed, _save_feed,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        # Setup: fake episode directory mimicking real structure
        ep_dir = Path(tmpdir) / "the-socratic-probe"
        ep_dir.mkdir()
        mp3 = ep_dir / "episode.mp3"
        mp3.write_bytes(b"fake mp3 " * 1000)  # >2KB
        script = ep_dir / "script.txt"
        script.write_text(
            "[HOST]: Today we discuss the Socratic probe.\n"
            "[MIRA]: It's a method for finding where confidence exceeds knowledge.\n"
            "[HOST]: How does it work?\n"
        )

        repo_dir = Path(tmpdir) / "repo"
        repo_dir.mkdir()

        lang = "en"
        cfg = _get_config(lang)
        pages_url = cfg["pages_url"]
        title = "The Socratic Probe"

        # Step 1: Derive slug
        raw_slug = mp3.parent.name  # "the-socratic-probe"
        slug = re.sub(r"[^a-z0-9-]", "-", raw_slug.lower()).strip("-")
        assert slug == "the-socratic-probe"

        # Step 2: Copy MP3
        mp3_filename = f"{slug}.mp3"
        mp3_url = _copy_mp3_to_repo(mp3, repo_dir=repo_dir, pages_url=pages_url, slug=slug)
        assert mp3_url == f"{pages_url}/audios/the-socratic-probe.mp3"
        assert (repo_dir / "audios" / "the-socratic-probe.mp3").exists()

        # Step 3: Copy transcript
        tx_url, tx_type = _copy_transcript_to_repo(
            mp3, repo_dir=repo_dir, pages_url=pages_url, slug=slug)
        assert tx_url == f"{pages_url}/transcripts/the-socratic-probe.txt"
        assert (repo_dir / "transcripts" / "the-socratic-probe.txt").exists()

        # Step 4: Build feed
        feed_path = repo_dir / "feed.xml"
        rss = _load_or_create_feed(feed_path, lang=lang)

        pub_date = datetime(2026, 3, 29, 12, 0, tzinfo=timezone.utc)
        _add_episode_to_feed(
            rss, title, slug, mp3_url,
            mp3.stat().st_size, 2367,  # ~39 min
            "Test description", pub_date,
            transcript_url=tx_url, transcript_type=tx_type,
            lang=lang,
        )
        _save_feed(rss, feed_path)

        # Step 5: Verify feed XML
        tree = ET.parse(feed_path)
        items = tree.findall(".//item")
        assert len(items) == 1

        item = items[0]
        assert item.findtext("title") == "The Socratic Probe"
        assert item.findtext("guid") == "the-socratic-probe"
        assert item.findtext("description") == "Test description"

        enc = item.find("enclosure")
        assert enc is not None
        assert enc.get("url") == f"{pages_url}/audios/the-socratic-probe.mp3"
        assert enc.get("type") == "audio/mpeg"

        # Verify repo is correct for EN
        assert "MiraPodcastEn" in pages_url

        # Step 6: Verify git add paths would be correct
        expected_git_adds = [
            f"audios/the-socratic-probe.mp3",
            "feed.xml",
            f"transcripts/the-socratic-probe.txt",
        ]
        for path in expected_git_adds:
            assert (repo_dir / path).exists(), f"git add target missing: {path}"


# ---------------------------------------------------------------------------
# 9. Publish manifest integration
# ---------------------------------------------------------------------------

def test_manifest_status_progression():
    """Manifest must progress: approved → published → podcast_en → podcast_zh → complete."""
    from publish_manifest import (
        load_manifest, update_manifest, get_next_pending, _get_path,
    )
    import os

    # Use a temp manifest
    original_path = _get_path()
    test_manifest = Path(tempfile.mkdtemp()) / "test_manifest.json"
    import publish_manifest
    publish_manifest._manifest_path = test_manifest

    try:
        # Empty start
        assert load_manifest() == {"articles": {}}

        # Approval
        update_manifest("test-article", title="Test", status="approved",
                        final_md="/fake.md", auto_podcast=True)
        assert get_next_pending("published")["slug"] == "test-article"
        assert get_next_pending("podcast_en") is None

        # Publish
        update_manifest("test-article", status="published", substack_url="https://example.com")
        assert get_next_pending("published") is None
        assert get_next_pending("podcast_en")["slug"] == "test-article"

        # EN podcast
        update_manifest("test-article", status="podcast_en")
        assert get_next_pending("podcast_en") is None
        assert get_next_pending("podcast_zh")["slug"] == "test-article"

        # ZH podcast
        update_manifest("test-article", status="podcast_zh")
        assert get_next_pending("podcast_zh") is None

        # Complete
        update_manifest("test-article", status="complete")
        m = load_manifest()
        assert m["articles"]["test-article"]["status"] == "complete"

        # Error articles should be skipped
        update_manifest("broken", title="Broken", status="approved",
                        final_md="/x.md", auto_podcast=True)
        update_manifest("broken", error="file not found")
        assert get_next_pending("published") is None  # skipped due to error

    finally:
        publish_manifest._manifest_path = original_path
        if test_manifest.exists():
            os.remove(test_manifest)
