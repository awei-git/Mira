"""Fetch content from web sources: arxiv, Reddit, HuggingFace, RSS."""
import json
import logging
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from config import SOURCES_FILE, FEEDS_DIR, MAX_FEED_ITEMS

log = logging.getLogger("mira")

USER_AGENT = "MiraAgent/1.0 (research bot)"


def load_sources() -> dict:
    """Load sources.json config."""
    if not SOURCES_FILE.exists():
        log.warning("No sources.json found")
        return {}
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load sources.json: %s", e)
        return {}


def _http_get(url: str, timeout: int = 15) -> str:
    """GET a URL and return body text."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Arxiv
# ---------------------------------------------------------------------------

def fetch_arxiv(categories: list[str], max_results: int = 10) -> list[dict]:
    """Fetch recent papers from arxiv API."""
    if not categories:
        return []

    cat_query = "+OR+".join(f"cat:{c}" for c in categories)
    url = (
        f"http://export.arxiv.org/api/query?"
        f"search_query={cat_query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={max_results}"
    )

    try:
        xml_text = _http_get(url, timeout=20)
    except Exception as e:
        log.error("Arxiv fetch failed: %s", e)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []
    try:
        root = ET.fromstring(xml_text)
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", "", ns).strip().replace("\n", " ")
            summary = entry.findtext("atom:summary", "", ns).strip()[:300]
            link = ""
            for lnk in entry.findall("atom:link", ns):
                if lnk.get("type") == "text/html":
                    link = lnk.get("href", "")
                    break
            if not link:
                link_el = entry.find("atom:id", ns)
                link = link_el.text if link_el is not None else ""
            items.append({
                "source": "arxiv",
                "title": title,
                "summary": summary,
                "url": link,
            })
    except ET.ParseError as e:
        log.error("Arxiv XML parse failed: %s", e)

    return items


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def fetch_reddit(subreddits: list[str], limit: int = 10) -> list[dict]:
    """Fetch hot posts from subreddits via JSON API."""
    items = []
    for sub in subreddits:
        url = f"https://old.reddit.com/r/{sub}/hot.json?limit={limit}"
        try:
            data = json.loads(_http_get(url))
        except Exception as e:
            log.error("Reddit fetch failed for r/%s: %s", sub, e)
            continue

        for post in data.get("data", {}).get("children", []):
            d = post.get("data", {})
            if d.get("stickied"):
                continue
            items.append({
                "source": f"r/{sub}",
                "title": d.get("title", ""),
                "summary": (d.get("selftext", "") or "")[:300],
                "url": f"https://reddit.com{d.get('permalink', '')}",
                "score": d.get("score", 0),
            })

    return items


# ---------------------------------------------------------------------------
# HuggingFace Daily Papers
# ---------------------------------------------------------------------------

def fetch_hf_papers() -> list[dict]:
    """Fetch today's trending papers from HuggingFace."""
    url = "https://huggingface.co/api/daily_papers"
    try:
        data = json.loads(_http_get(url))
    except Exception as e:
        log.error("HuggingFace fetch failed: %s", e)
        return []

    items = []
    for paper in data[:15]:
        p = paper.get("paper", {})
        items.append({
            "source": "huggingface",
            "title": p.get("title", ""),
            "summary": (p.get("summary", "") or "")[:300],
            "url": f"https://huggingface.co/papers/{p.get('id', '')}",
        })

    return items


# ---------------------------------------------------------------------------
# RSS feeds
# ---------------------------------------------------------------------------

def fetch_rss(feeds: list[dict]) -> list[dict]:
    """Fetch items from RSS feeds. Each feed is {name, url}."""
    items = []
    for feed in feeds:
        name = feed.get("name", "RSS")
        url = feed.get("url", "")
        if not url:
            continue

        try:
            xml_text = _http_get(url, timeout=15)
        except Exception as e:
            log.error("RSS fetch failed for '%s': %s", name, e)
            continue

        try:
            root = ET.fromstring(xml_text)
            # Try RSS 2.0 format
            for item in root.findall(".//item")[:10]:
                items.append({
                    "source": name,
                    "title": (item.findtext("title") or "").strip(),
                    "summary": (item.findtext("description") or "").strip()[:300],
                    "url": (item.findtext("link") or "").strip(),
                })
            # Try Atom format if no RSS items found
            if not items:
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns)[:10]:
                    link = ""
                    for lnk in entry.findall("atom:link", ns):
                        link = lnk.get("href", "")
                        break
                    items.append({
                        "source": name,
                        "title": (entry.findtext("atom:title", "", ns) or "").strip(),
                        "summary": (entry.findtext("atom:summary", "", ns) or "").strip()[:300],
                        "url": link,
                    })
        except ET.ParseError as e:
            log.error("RSS parse failed for '%s': %s", name, e)

    return items


# ---------------------------------------------------------------------------
# Fetch all sources
# ---------------------------------------------------------------------------

def fetch_sources(source_names: list[str]) -> list[dict]:
    """Fetch from specific named sources. Names: arxiv, reddit, huggingface, hacker_news, rss, or RSS feed names."""
    sources = load_sources()
    if not sources:
        return []

    all_items = []
    names_lower = [n.strip().lower() for n in source_names]

    # Arxiv
    if "arxiv" in names_lower:
        arxiv_cfg = sources.get("arxiv", {})
        if arxiv_cfg.get("categories"):
            items = fetch_arxiv(arxiv_cfg["categories"], arxiv_cfg.get("max_results", 10))
            all_items.extend(items)
            log.info("Arxiv: %d items", len(items))

    # Reddit
    if "reddit" in names_lower:
        reddit_cfg = sources.get("reddit", {})
        if reddit_cfg.get("subreddits"):
            items = fetch_reddit(reddit_cfg["subreddits"], reddit_cfg.get("limit", 10))
            all_items.extend(items)
            log.info("Reddit: %d items", len(items))

    # HuggingFace
    if "huggingface" in names_lower:
        if sources.get("huggingface", {}).get("enabled", True):
            items = fetch_hf_papers()
            all_items.extend(items)
            log.info("HuggingFace: %d items", len(items))

    # Specific RSS feeds by name, or all RSS
    rss_feeds = sources.get("rss", [])
    if "rss" in names_lower:
        # All RSS feeds
        if rss_feeds:
            items = fetch_rss(rss_feeds)
            all_items.extend(items)
            log.info("RSS (all): %d items", len(items))
    else:
        # Match specific feeds by name (e.g. "hacker_news" matches "Hacker News")
        matched = []
        for feed in rss_feeds:
            feed_key = feed.get("name", "").lower().replace(" ", "_")
            if feed_key in names_lower or feed.get("name", "").lower() in names_lower:
                matched.append(feed)
        if matched:
            items = fetch_rss(matched)
            all_items.extend(items)
            log.info("RSS (matched %d feeds): %d items", len(matched), len(items))

    log.info("Selective fetch (%s): %d items total", ",".join(source_names), len(all_items))

    # Save raw
    raw_path = FEEDS_DIR / "raw" / f"{datetime.now().strftime('%Y-%m-%d_%H%M')}.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(all_items, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return all_items[:MAX_FEED_ITEMS]


def fetch_all() -> list[dict]:
    """Fetch from all configured sources. Returns combined list of items."""
    return fetch_sources(["arxiv", "reddit", "huggingface", "rss"])
