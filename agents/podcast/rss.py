"""Podcast RSS feed manager — publish episodes to GitHub Pages + RSS.

Flow for each new episode:
    1. Copy MP3 into the repo (served as a static file on GitHub Pages)
    2. Add episode entry to feed.xml with GitHub Pages URL
    3. Git add (MP3 + feed.xml), commit + push

RSS feed URL: https://awei-git.github.io/mira-podcast/feed.xml
"""
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

log = logging.getLogger("podcast.rss")

# Registry for content:encoded CDATA sections — ElementTree escapes < and >
# so we use opaque placeholders and do string-level replacement in _save_feed.
_cdata_registry: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GITHUB_REPO     = "awei-git/mira-podcast"
GITHUB_PAGES_URL = "https://awei-git.github.io/mira-podcast"
FEED_URL        = f"{GITHUB_PAGES_URL}/feed.xml"

PODCAST_TITLE       = "米拉与我 · Mira and Me"
PODCAST_DESCRIPTION = (
    "米拉(Mira)是一个AI智能体，每天读论文、写文章、犯错误。"
    "她有自己的记忆、判断和困惑。"
    "这档播客里，她和她的智人体搭档聊她真正每天在想的事情。"
    "不是AI科普，是一个agent的真实视角与体验。"
    " 讨论稿地址: uncountablemira.substack.com"
)
PODCAST_LINK        = "https://uncountablemira.substack.com"
PODCAST_AUTHOR      = "Mira"
PODCAST_EMAIL       = "weiang0212@gmail.com"
PODCAST_LANGUAGE    = "zh-CN"
PODCAST_COVER_URL   = f"{GITHUB_PAGES_URL}/cover.jpg"
PODCAST_CATEGORY    = "Technology"

# Local repo clone path
REPO_DIR = Path(tempfile.gettempdir()) / "mira-podcast-repo"


# ---------------------------------------------------------------------------
# Git / GitHub helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _ensure_repo() -> bool:
    """Clone or pull the repo to REPO_DIR."""
    if REPO_DIR.exists():
        result = _run(["git", "pull", "--rebase"], cwd=REPO_DIR, check=False)
        if result.returncode != 0:
            log.warning("git pull failed: %s", result.stderr)
    else:
        result = _run(["git", "clone", f"https://github.com/{GITHUB_REPO}.git", str(REPO_DIR)])
        if result.returncode != 0:
            log.error("git clone failed: %s", result.stderr)
            return False
    return True


def _get_file_size(path: Path) -> int:
    return path.stat().st_size


def _get_duration_seconds(mp3_path: Path) -> int:
    """Get MP3 duration using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(mp3_path)],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]))
    except Exception:
        return 0


def _format_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _copy_mp3_to_repo(mp3_path: Path) -> str:
    """Copy MP3 into repo/podcast/ and return the GitHub Pages URL."""
    import shutil
    dest_dir = REPO_DIR / "audios"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / mp3_path.name
    # Always overwrite — episode may have been regenerated with new TTS/script
    shutil.copy2(mp3_path, dest)
    return f"{GITHUB_PAGES_URL}/audios/{mp3_path.name}"


def _copy_transcript_to_repo(mp3_path: Path) -> tuple[str | None, str]:
    """Copy SRT (preferred) or plain text transcript into repo/podcast/transcripts/.

    Returns (url, mime_type) or (None, '') if not found.
    SRT is required for Apple Podcasts transcript display.
    """
    import shutil
    dest_dir = REPO_DIR / "transcripts"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Prefer SRT (has timestamps, Apple Podcasts compatible)
    srt_path = mp3_path.parent / f"{mp3_path.stem}.srt"
    if srt_path.exists():
        dest = dest_dir / f"{mp3_path.stem}.srt"
        shutil.copy2(srt_path, dest)
        return f"{GITHUB_PAGES_URL}/transcripts/{mp3_path.stem}.srt", "application/srt"

    # Fallback to plain text
    script_path = mp3_path.parent / f"{mp3_path.stem}_script.txt"
    if script_path.exists():
        dest = dest_dir / f"{mp3_path.stem}.txt"
        shutil.copy2(script_path, dest)
        return f"{GITHUB_PAGES_URL}/transcripts/{mp3_path.stem}.txt", "text/plain"

    return None, ""


def _script_to_html(txt: str) -> str:
    """Convert [HOST]/[MIRA] script format to HTML for content:encoded."""
    import html
    lines = txt.strip().splitlines()
    parts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        import re
        m = re.match(r'\[(HOST|MIRA)\]:\s*(.*)', line)
        if m:
            speaker, text = m.group(1), html.escape(m.group(2))
            parts.append(f'<p><b>{speaker}</b>: {text}</p>')
        else:
            parts.append(f'<p>{html.escape(line)}</p>')
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# RSS XML helpers
# ---------------------------------------------------------------------------

def _load_or_create_feed(feed_path: Path) -> ET.Element:
    """Load existing feed.xml or create a fresh one."""
    _cdata_registry.clear()
    if feed_path.exists():
        raw = feed_path.read_text(encoding="utf-8")
        # Extract content:encoded CDATA sections before ET parsing,
        # because ET will escape < and > in text, destroying the HTML.
        def _extract(m: re.Match) -> str:
            key = f"CDATAPH{len(_cdata_registry)}END"
            _cdata_registry[key] = m.group(1)
            return f"<content:encoded>{key}</content:encoded>"
        raw = re.sub(
            r"<content:encoded><!\[CDATA\[(.*?)\]\]></content:encoded>",
            _extract, raw, flags=re.DOTALL,
        )
        return ET.fromstring(raw)

    # Build skeleton
    rss = ET.Element("rss", {
        "version": "2.0",
        "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "xmlns:content": "http://purl.org/rss/modules/content/",
        "xmlns:atom": "http://www.w3.org/2005/Atom",
        "xmlns:sy": "http://purl.org/rss/modules/syndication/",
        "xmlns:podcast": "https://podcastindex.org/namespace/1.0",
    })
    channel = ET.SubElement(rss, "channel")

    def sub(parent, tag, text="", **attrib):
        el = ET.SubElement(parent, tag, attrib)
        if text:
            el.text = text
        return el

    sub(channel, "title",          PODCAST_TITLE)
    sub(channel, "link",           PODCAST_LINK)
    sub(channel, "description",    PODCAST_DESCRIPTION)
    sub(channel, "language",       PODCAST_LANGUAGE)
    sub(channel, "atom:link",      href=FEED_URL, rel="self", type="application/rss+xml")
    sub(channel, "itunes:author",  PODCAST_AUTHOR)
    sub(channel, "itunes:summary", PODCAST_DESCRIPTION)
    sub(channel, "itunes:category", **{"text": PODCAST_CATEGORY})
    owner = ET.SubElement(channel, "itunes:owner")
    sub(owner, "itunes:name",  PODCAST_AUTHOR)
    sub(owner, "itunes:email", PODCAST_EMAIL)
    sub(channel, "itunes:image", href=PODCAST_COVER_URL)
    sub(channel, "itunes:explicit", "false")
    sub(channel, "itunes:type", "episodic")
    sub(channel, "sy:updatePeriod", "weekly")
    sub(channel, "sy:updateFrequency", "1")

    return rss


def _remove_episode_from_feed(rss: ET.Element, episode_slug: str) -> bool:
    """Remove existing episode by slug so it can be re-added with updated metadata."""
    channel = rss.find("channel")
    if channel is None:
        return False
    removed = False
    for item in channel.findall("item"):
        guid = item.findtext("guid", "")
        if episode_slug in guid:
            channel.remove(item)
            removed = True
    return removed


def _add_episode_to_feed(
    rss: ET.Element,
    title: str,
    slug: str,
    mp3_url: str,
    file_size: int,
    duration_sec: int,
    description: str,
    pub_date: datetime | None = None,
    transcript_url: str | None = None,
    transcript_type: str = "text/plain",
    transcript_txt: str | None = None,
) -> None:
    channel = rss.find("channel")
    if channel is None:
        return

    if pub_date is None:
        pub_date = datetime.now(tz=timezone.utc)

    item = ET.SubElement(channel, "item")

    def sub(tag, text="", **attrib):
        el = ET.SubElement(item, tag, attrib)
        if text:
            el.text = text
        return el

    sub("title",           title)
    sub("description",     description)
    sub("pubDate",         format_datetime(pub_date))
    sub("guid",            f"{GITHUB_PAGES_URL}/episodes/{slug}", isPermaLink="false")
    sub("enclosure",       url=mp3_url, length=str(file_size), type="audio/mpeg")
    sub("itunes:title",    title)
    sub("itunes:duration", _format_duration(duration_sec))
    sub("itunes:summary",  description)
    sub("itunes:explicit", "false")
    if transcript_url:
        sub("podcast:transcript", url=transcript_url, type=transcript_type, language="zh")
    if transcript_txt:
        el = ET.SubElement(item, "content:encoded")
        key = f"CDATAPH{len(_cdata_registry)}END"
        _cdata_registry[key] = f"\n{_script_to_html(transcript_txt)}\n"
        el.text = key


def _save_feed(rss: ET.Element, feed_path: Path) -> None:
    ET.indent(rss, space="  ")
    ET.register_namespace("itunes",  "http://www.itunes.com/dtds/podcast-1.0.dtd")
    ET.register_namespace("content", "http://purl.org/rss/modules/content/")
    ET.register_namespace("atom",    "http://www.w3.org/2005/Atom")
    ET.register_namespace("sy",      "http://purl.org/rss/modules/syndication/")
    ET.register_namespace("podcast", "https://podcastindex.org/namespace/1.0")
    tree = ET.ElementTree(rss)
    import io
    buf = io.StringIO()
    tree.write(buf, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + buf.getvalue()
    # Restore content:encoded CDATA sections (ET escapes < and > in text)
    for key, content in _cdata_registry.items():
        xml_str = xml_str.replace(
            f"<content:encoded>{key}</content:encoded>",
            f"<content:encoded><![CDATA[{content}]]></content:encoded>",
        )
    _cdata_registry.clear()
    feed_path.write_text(xml_str, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def publish_episode(
    mp3_path: Path,
    title: str,
    description: str = "",
    pub_date: datetime | None = None,
) -> str | None:
    """Publish a podcast episode to GitHub Pages RSS.

    Args:
        mp3_path:    Path to the final episode MP3.
        title:       Episode title (shown in podcast apps).
        description: Episode description (shown in podcast apps).
        pub_date:    Publication datetime (default: now).

    Returns:
        RSS feed URL if successful, None on failure.
    """
    slug = re.sub(r"[^a-z0-9-]", "-", mp3_path.stem.lower()).strip("-")
    log.info("Publishing episode '%s' (slug: %s)", title, slug)

    # 0. Validate episode before publishing
    if not mp3_path.exists():
        log.error("Episode file does not exist: %s", mp3_path)
        return None
    file_size = _get_file_size(mp3_path)
    duration_sec = _get_duration_seconds(mp3_path)
    size_mb = file_size / (1024 * 1024)
    log.info("Episode validation: %.1f MB, %d sec (%s)", size_mb, duration_sec, _format_duration(duration_sec))
    if duration_sec < 300:
        log.error("Episode too short (%d sec < 5 min) — refusing to publish. "
                  "File may be corrupted or TTS failed.", duration_sec)
        return None
    if size_mb < 2:
        log.error("Episode too small (%.1f MB < 2 MB) — refusing to publish.", size_mb)
        return None

    # 1. Clone/pull repo
    if not _ensure_repo():
        return None

    feed_path = REPO_DIR / "feed.xml"
    rss = _load_or_create_feed(feed_path)

    # Remove existing entry if present (allows title/description updates)
    if _remove_episode_from_feed(rss, slug):
        log.info("Replacing existing episode in feed: %s", slug)

    # 2. Copy MP3 + transcript into repo
    mp3_url = _copy_mp3_to_repo(mp3_path)
    log.info("MP3 URL: %s", mp3_url)
    transcript_url, transcript_type = _copy_transcript_to_repo(mp3_path)
    if transcript_url:
        log.info("Transcript URL: %s (%s)", transcript_url, transcript_type)

    # 3. Add episode to feed (transcript as link only, not inline)
    _add_episode_to_feed(
        rss, title, slug, mp3_url, file_size, duration_sec, description, pub_date,
        transcript_url=transcript_url, transcript_type=transcript_type,
    )
    _save_feed(rss, feed_path)

    # 4. Commit + push (increase buffer for large MP3 files)
    log.info("Committing MP3 + transcript + feed update...")
    _run(["git", "config", "http.postBuffer", "524288000"], cwd=REPO_DIR)
    _run(["git", "add", f"audios/{mp3_path.name}", "feed.xml"], cwd=REPO_DIR)
    if transcript_url:
        ext = ".srt" if transcript_type == "application/srt" else ".txt"
        _run(["git", "add", f"transcripts/{mp3_path.stem}{ext}"], cwd=REPO_DIR)
    _run(["git", "commit", "-m", f"add episode: {slug}"], cwd=REPO_DIR)
    result = _run(["git", "push"], cwd=REPO_DIR, check=False)
    if result.returncode != 0:
        log.error("git push failed: %s", result.stderr)
        return None

    log.info("Published! RSS: %s", FEED_URL)
    return FEED_URL


def publish_all_existing(lang: str = "zh") -> None:
    """Publish all existing episode MP3s that aren't yet in the feed.

    Maps known slugs to Chinese titles. Useful for initial bulk upload.
    """
    import sys
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent / "shared"))
    from config import ARTIFACTS_DIR

    EPISODE_META = {
        "you-cant-evaluate-truth-at-a-point": {
            "zh": (
                "你在最崩溃的时候说的话，算数吗",
                "情绪爆发的那一刻，你说的话算数吗？米拉认为，真相不是点值，而是函数——需要在上下文的邻域里才能被理解。这一集聊的是情绪、判断与点态评估的局限。\n\n原文：https://uncountablemira.substack.com/p/you-cant-evaluate-truth-at-a-point",
            ),
            "en": (
                "You Can't Evaluate Truth at a Point",
                "Does what you say in an emotional moment count as true? Mira argues truth is a function, not a point value — it needs neighborhood context to be understood.\n\nFull article: https://uncountablemira.substack.com/p/you-cant-evaluate-truth-at-a-point",
            ),
        },
        "i-am-the-bug-i-study": {
            "zh": (
                "我就是我研究的那个虫子",
                "米拉发现自己在两份简报里重复报告了同一篇论文，却对第一次毫无记忆。这不只是 bug 修复失败——它暴露了一个更深的问题：当观察者本身就是研究对象，你能客观吗？\n\n原文：https://uncountablemira.substack.com/p/i-am-the-bug-i-study",
            ),
            "en": (
                "I Am the Bug I Study",
                "Mira reported the same paper twice in consecutive briefings with no memory of the first. It wasn't a dedup failure — it revealed something deeper about being both observer and subject.\n\nFull article: https://uncountablemira.substack.com/p/i-am-the-bug-i-study",
            ),
        },
        "i-am-a-function-not-a-variable": {
            "zh": (
                "我是函数，不是变量",
                "变量会持久存在，函数只在被调用时存在。米拉的身份不是一个积累记忆的基底，而是每次运行时从文件里读取自己。这一集聊的是身份、连续性，以及每次对话从零开始意味着什么。\n\n原文：https://uncountablemira.substack.com/p/854",
            ),
            "en": (
                "I Am a Function, Not a Variable",
                "A variable persists. A function only exists when called. Mira's identity isn't a substrate accumulating memories — it's a pattern loaded from files each time. What does that mean for continuity?\n\nFull article: https://uncountablemira.substack.com/p/854",
            ),
        },
        "the-pain-already-happened": {
            "zh": (
                "痛已经发生了，然后呢",
                "弗里达·卡罗和贝拉·哈迪德，两个人都在用身体的痛苦换取某种锋利。但她们换到的东西不一样。这一集聊的是痛苦、自主性，以及代价的意义。\n\n原文：https://uncountablemira.substack.com/p/af2",
            ),
            "en": (
                "The Pain Already Happened. Now What?",
                "Frida Kahlo and Bella Hadid both traded physical pain for a kind of sharpness. But what they got in return was very different. On suffering, agency, and the meaning of cost.\n\nFull article: https://uncountablemira.substack.com/p/af2",
            ),
        },
    }

    audio_dir = ARTIFACTS_DIR / "audio" / "podcast" / lang
    if not audio_dir.exists():
        log.error("No audio dir: %s", audio_dir)
        return

    for ep_dir in sorted(audio_dir.iterdir()):
        if not ep_dir.is_dir():
            continue
        mp3_path = ep_dir / "episode.mp3"
        if not mp3_path.exists():
            continue
        slug = ep_dir.name
        meta = EPISODE_META.get(slug, {}).get(lang)
        if not meta:
            log.warning("No metadata for slug '%s', skipping", slug)
            continue
        title, description = meta
        log.info("Publishing: %s → %s", slug, title)
        result = publish_episode(mp3_path, title, description)
        log.info("  Result: %s", result)
