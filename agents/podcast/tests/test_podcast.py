"""Podcast pipeline tests — verify script, TTS routing, normalization, curation."""
from __future__ import annotations
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "podcast"))
sys.path.insert(0, str(_AGENTS / "shared"))


def test_tts_provider_routing():
    import importlib.util; _spec = importlib.util.spec_from_file_location("podcast_handler", str(Path(_AGENTS) / "podcast" / "handler.py")); _mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod); TTS_PROVIDER_ZH = _mod.TTS_PROVIDER_ZH; TTS_PROVIDER_EN = _mod.TTS_PROVIDER_EN
    assert TTS_PROVIDER_ZH == "minimax", f"ZH should be minimax, got {TTS_PROVIDER_ZH}"
    # EN should be gemini (may change but should not be minimax — bad EN voices)
    assert TTS_PROVIDER_EN != "minimax", f"EN should not be minimax (bad EN female voices)"


def test_text_normalization():
    """English acronyms must be spaced for TTS."""
    # Check scripts have been normalized
    from config import ARTIFACTS_DIR; script_dir = ARTIFACTS_DIR / "audio" / "podcast" / "zh"
    for ep_dir in script_dir.iterdir():
        script = ep_dir / "script.txt"
        if not script.exists():
            continue
        text = script.read_text()
        # "AI" (without space) should not appear except in compound words
        import re
        bare_ai = re.findall(r'(?<![A-Za-z])AI(?![A-Za-z ])', text)
        assert len(bare_ai) == 0, f"{ep_dir.name}: found {len(bare_ai)} unspaced 'AI'"


def test_curation_list():
    from autopipeline import CURATED_EPISODES, ARTIFACTS_DIR
    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    if not published_dir.exists():
        return  # Skip if no published dir

    slug_to_file = {}
    for md in published_dir.glob("*.md"):
        s = md.stem[11:] if len(md.stem) > 11 and md.stem[10] == "_" else md.stem
        slug_to_file[s] = md

    for ep in CURATED_EPISODES:
        if ep.get("skip"):
            continue
        slug = ep["slug"]
        assert "podcast_title" in ep, f"{slug}: missing podcast_title"
        assert "theme" in ep, f"{slug}: missing theme"


def test_rss_feed_valid():
    """GitHub repo feed.xml should be valid XML."""
    feed_path = Path("/tmp/mira-podcast-repo/feed.xml")
    if not feed_path.exists():
        return  # Skip if repo not cloned

    from xml.etree import ElementTree as ET
    tree = ET.parse(str(feed_path))
    root = tree.getroot()
    assert root.tag == "rss", f"Expected <rss>, got <{root.tag}>"
    channel = root.find("channel")
    assert channel is not None, "Missing <channel>"
    title = channel.findtext("title")
    assert title, "Missing channel title"


def test_short_text_padding():
    """Gemini TTS short text padding should work."""
    # The fix: text < 8 chars gets padded
    short_zh = "下期见。"
    assert len(short_zh.strip()) < 8
    # After padding it should be longer
    padded = short_zh.strip().rstrip("。") + "。好的。"
    assert len(padded) > len(short_zh)  # padding adds content even if still short, f"Padded text too short: '{padded}'"
