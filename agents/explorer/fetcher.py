"""Fetch content from web sources: arxiv, Reddit, HuggingFace, GitHub, HN, Lobsters, RSS."""

import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
            items.append(
                {
                    "source": "arxiv",
                    "title": title,
                    "summary": summary,
                    "url": link,
                }
            )
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
            items.append(
                {
                    "source": f"r/{sub}",
                    "title": d.get("title", ""),
                    "summary": (d.get("selftext", "") or "")[:300],
                    "url": f"https://reddit.com{d.get('permalink', '')}",
                    "score": d.get("score", 0),
                }
            )

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
        items.append(
            {
                "source": "huggingface",
                "title": p.get("title", ""),
                "summary": (p.get("summary", "") or "")[:300],
                "url": f"https://huggingface.co/papers/{p.get('id', '')}",
            }
        )

    return items


# ---------------------------------------------------------------------------
# GitHub Trending (via Search API — stars proxy)
# ---------------------------------------------------------------------------


def fetch_github_trending(days_back: int = 7, language: str | None = None, per_page: int = 25) -> list[dict]:
    """Fetch trending repos via GitHub Search API (sorted by stars, recent)."""
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    q = f"created:>{since}"
    if language:
        q += f" language:{language}"
    url = (
        f"https://api.github.com/search/repositories"
        f"?q={urllib.parse.quote(q)}&sort=stars&order=desc&per_page={per_page}"
    )
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        log.error("GitHub trending fetch failed: %s", e)
        return []

    items = []
    for r in data.get("items", []):
        desc = (r.get("description") or "")[:300]
        lang = r.get("language") or "?"
        stars = r.get("stargazers_count", 0)
        items.append(
            {
                "source": "github_trending",
                "title": f"{r['full_name']} [{lang}] ({stars} stars)",
                "summary": desc,
                "url": r["html_url"],
                "stars": stars,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Hacker News (via Algolia API — single request, rich data)
# ---------------------------------------------------------------------------


def fetch_hackernews(count: int = 30, min_points: int = 0) -> list[dict]:
    """Fetch HN front page stories via Algolia Search API."""
    params = f"tags=front_page&hitsPerPage={count}"
    if min_points:
        params += f"&numericFilters=points>{min_points}"
    url = f"https://hn.algolia.com/api/v1/search?{params}"
    try:
        data = json.loads(_http_get(url, timeout=15))
    except Exception as e:
        log.error("HackerNews Algolia fetch failed: %s", e)
        return []

    items = []
    for h in data.get("hits", []):
        items.append(
            {
                "source": "hackernews",
                "title": h.get("title", ""),
                "summary": f"Score: {h.get('points', 0)} | Comments: {h.get('num_comments', 0)}",
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "score": h.get("points", 0),
                "hn_url": f"https://news.ycombinator.com/item?id={h['objectID']}",
            }
        )
    return items


# ---------------------------------------------------------------------------
# Web Search (DuckDuckGo HTML — no API key required)
# ---------------------------------------------------------------------------


def fetch_web_search(query: str, max_results: int = 10) -> list[dict]:
    """Search DuckDuckGo and return structured results.

    Delegates to web_browser.search() for the actual DDG call.
    Returns list of {source, title, summary, url, query} dicts.
    """
    try:
        from tools.web_browser import search as wb_search

        results = wb_search(query, max_results=max_results)
        return [
            {
                "source": "duckduckgo",
                "title": r.title,
                "summary": r.snippet[:300],
                "url": r.url,
                "query": query,
            }
            for r in results
        ]
    except Exception as e:
        log.error("Web search failed for '%s': %s", query, e)
        return []


# ---------------------------------------------------------------------------
# Lobsters (JSON API)
# ---------------------------------------------------------------------------


def fetch_lobsters(count: int = 25) -> list[dict]:
    """Fetch hottest stories from Lobsters."""
    url = "https://lobste.rs/hottest.json"
    try:
        data = json.loads(_http_get(url, timeout=15))
    except Exception as e:
        log.error("Lobsters fetch failed: %s", e)
        return []

    items = []
    for s in data[:count]:
        tags = ", ".join(s.get("tags", []))
        items.append(
            {
                "source": "lobsters",
                "title": s.get("short_id_url", "").split("/")[-1] if not s.get("title") else s.get("title", ""),
                "summary": f"[{tags}] Score: {s.get('score', 0)} | Comments: {s.get('comment_count', 0)}",
                "url": s.get("url") or s.get("short_id_url", ""),
                "score": s.get("score", 0),
            }
        )
    return items


# ---------------------------------------------------------------------------
# Dev.to (public API — no auth needed)
# ---------------------------------------------------------------------------


def fetch_devto(per_page: int = 20, top_days: int = 7) -> list[dict]:
    """Fetch top articles from Dev.to public API."""
    url = f"https://dev.to/api/articles?per_page={per_page}&top={top_days}"
    try:
        data = json.loads(_http_get(url, timeout=15))
    except Exception as e:
        log.error("Dev.to fetch failed: %s", e)
        return []

    items = []
    for a in data:
        items.append(
            {
                "source": "devto",
                "title": a.get("title", ""),
                "summary": (a.get("description", "") or "")[:300],
                "url": a.get("url", ""),
                "score": a.get("positive_reactions_count", 0),
                "tags": ", ".join(a.get("tag_list", [])),
            }
        )
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
            feed_items = []
            for item in root.findall(".//item")[:10]:
                feed_items.append(
                    {
                        "source": name,
                        "title": (item.findtext("title") or "").strip(),
                        "summary": (item.findtext("description") or "").strip()[:300],
                        "url": (item.findtext("link") or "").strip(),
                    }
                )
            # Try Atom format if no RSS items found for this feed
            if not feed_items:
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                for entry in root.findall("atom:entry", ns)[:10]:
                    link = ""
                    for lnk in entry.findall("atom:link", ns):
                        link = lnk.get("href", "")
                        break
                    feed_items.append(
                        {
                            "source": name,
                            "title": (entry.findtext("atom:title", "", ns) or "").strip(),
                            "summary": (entry.findtext("atom:summary", "", ns) or "").strip()[:300],
                            "url": link,
                        }
                    )
            items.extend(feed_items)
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

    # GitHub Trending
    if "github" in names_lower or "github_trending" in names_lower:
        gh_cfg = sources.get("github_trending", {})
        if gh_cfg.get("enabled", True):
            items = fetch_github_trending(
                days_back=gh_cfg.get("days_back", 7),
                language=gh_cfg.get("language"),
                per_page=gh_cfg.get("per_page", 25),
            )
            all_items.extend(items)
            log.info("GitHub Trending: %d items", len(items))

    # Hacker News (native Algolia API — richer than RSS)
    if "hackernews" in names_lower or "hacker_news" in names_lower:
        hn_cfg = sources.get("hackernews", {})
        if hn_cfg.get("enabled", True):
            items = fetch_hackernews(
                count=hn_cfg.get("count", 30),
                min_points=hn_cfg.get("min_points", 50),
            )
            all_items.extend(items)
            log.info("HackerNews: %d items", len(items))

    # Lobsters
    if "lobsters" in names_lower:
        lob_cfg = sources.get("lobsters", {})
        if lob_cfg.get("enabled", True):
            items = fetch_lobsters(count=lob_cfg.get("count", 25))
            all_items.extend(items)
            log.info("Lobsters: %d items", len(items))

    # Dev.to
    if "devto" in names_lower or "dev.to" in names_lower:
        dt_cfg = sources.get("devto", {})
        if dt_cfg.get("enabled", True):
            items = fetch_devto(
                per_page=dt_cfg.get("per_page", 20),
                top_days=dt_cfg.get("top_days", 7),
            )
            all_items.extend(items)
            log.info("Dev.to: %d items", len(items))

    # Web Search (DuckDuckGo — query driven, not config-driven)
    # Supports "web_search:QUERY" entries in source_names
    for name in names_lower:
        if name.startswith("web_search:"):
            query = name[len("web_search:") :]
            if query:
                items = fetch_web_search(query, max_results=10)
                all_items.extend(items)
                log.info("Web search '%s': %d items", query, len(items))

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
    raw_path.write_text(json.dumps(all_items, indent=2, ensure_ascii=False), encoding="utf-8")

    return all_items[:MAX_FEED_ITEMS]


def fetch_all() -> list[dict]:
    """Fetch from all configured sources. Returns combined list of items."""
    return fetch_sources(
        ["arxiv", "reddit", "huggingface", "github_trending", "hackernews", "lobsters", "devto", "rss"]
    )
