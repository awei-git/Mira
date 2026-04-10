"""Mira Web Browser — general-purpose web browsing for all agents.

Provides:
    browse(url)        — fetch any URL, return readable text
    search(query, n)   — web search via DuckDuckGo, return top results
    read_article(url)  — article-optimized extraction (title + body)
    fetch_raw(url)     — raw HTML fetch (for agents that parse themselves)

Uses only Python stdlib. If `trafilatura` is installed, article extraction
is significantly better. Install with: pip install trafilatura
"""
import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

log = logging.getLogger("mira.web")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_TIMEOUT = 20
_MAX_BODY = 512_000  # 512KB max download


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class Page:
    url: str
    title: str
    text: str
    raw_html: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())

    def summary(self, max_chars: int = 2000) -> str:
        """First N chars of text, for embedding in prompts."""
        t = self.text.strip()
        if len(t) <= max_chars:
            return t
        return t[:max_chars] + "\n\n[... truncated]"


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = _TIMEOUT,
           headers: dict | None = None) -> tuple[str, str]:
    """Fetch URL, return (final_url, body_text). Raises on failure."""
    hdrs = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,zh;q=0.8"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.url
        # Detect encoding
        ct = resp.headers.get("Content-Type", "")
        charset = "utf-8"
        if "charset=" in ct:
            charset = ct.split("charset=")[-1].split(";")[0].strip()
        raw = resp.read(_MAX_BODY)
        return final_url, raw.decode(charset, errors="replace")


def fetch_raw(url: str, timeout: int = _TIMEOUT) -> str:
    """Fetch raw HTML from a URL. Returns empty string on failure."""
    try:
        _, body = _fetch(url, timeout)
        return body
    except Exception as e:
        log.warning("fetch_raw(%s) failed: %s", url, e)
        return ""


# ---------------------------------------------------------------------------
# HTML → readable text (stdlib fallback)
# ---------------------------------------------------------------------------

# Tags whose content should be completely removed
_REMOVE_TAGS = {"script", "style", "noscript", "svg", "iframe", "nav",
                "footer", "header", "aside", "form"}

# Block-level tags that should produce line breaks
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
               "li", "tr", "blockquote", "pre", "section", "article",
               "figcaption", "dt", "dd"}


class _TextExtractor(HTMLParser):
    """Extract readable text from HTML, preserving basic structure."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _REMOVE_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _REMOVE_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title = data.strip()
        self.parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.parts)
        # Collapse whitespace within lines but preserve paragraph breaks
        lines = raw.splitlines()
        cleaned = []
        for line in lines:
            stripped = " ".join(line.split())
            if stripped:
                cleaned.append(stripped)
            elif cleaned and cleaned[-1] != "":
                cleaned.append("")
        text = "\n".join(cleaned).strip()
        # Remove excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text


def _html_to_text(raw_html: str) -> tuple[str, str]:
    """Convert HTML to readable text. Returns (title, text)."""
    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        pass
    # Also try to extract title from <title> or og:title
    title = parser.title
    if not title:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                       raw_html, re.IGNORECASE)
        if m:
            title = html.unescape(m.group(1))
    return title, parser.get_text()


# ---------------------------------------------------------------------------
# Article extraction (trafilatura if available, else stdlib)
# ---------------------------------------------------------------------------

_HAS_TRAFILATURA = None


def _check_trafilatura() -> bool:
    global _HAS_TRAFILATURA
    if _HAS_TRAFILATURA is None:
        try:
            import trafilatura  # noqa: F401
            _HAS_TRAFILATURA = True
        except ImportError:
            _HAS_TRAFILATURA = False
    return _HAS_TRAFILATURA


def _extract_article_trafilatura(raw_html: str, url: str) -> tuple[str, str]:
    """Extract article text using trafilatura (high quality)."""
    import trafilatura
    result = trafilatura.extract(
        raw_html, url=url,
        include_comments=False,
        include_tables=True,
        output_format="txt",
        favor_recall=True,
    )
    # Get title
    metadata = trafilatura.extract_metadata(raw_html, url=url)
    title = metadata.title if metadata and metadata.title else ""
    return title, result or ""


def _extract_article_stdlib(raw_html: str) -> tuple[str, str]:
    """Extract article text using stdlib (decent quality).

    Tries to find the main content area by looking for <article>,
    <main>, or the largest text-dense <div>.
    """
    # Try <article> first
    article_match = re.search(
        r"<article[^>]*>(.*?)</article>", raw_html,
        re.DOTALL | re.IGNORECASE
    )
    if article_match:
        title, text = _html_to_text(article_match.group(1))
        if len(text) > 200:
            if not title:
                title, _ = _html_to_text(raw_html)
            return title, text

    # Try <main>
    main_match = re.search(
        r"<main[^>]*>(.*?)</main>", raw_html,
        re.DOTALL | re.IGNORECASE
    )
    if main_match:
        title, text = _html_to_text(main_match.group(1))
        if len(text) > 200:
            if not title:
                title, _ = _html_to_text(raw_html)
            return title, text

    # Fallback: full page
    return _html_to_text(raw_html)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def browse(url: str, timeout: int = _TIMEOUT) -> Page:
    """Fetch any URL and return readable text.

    Works for articles, docs, forums, etc. Not suitable for
    JavaScript-heavy SPAs.
    """
    try:
        final_url, raw_html = _fetch(url, timeout)
    except Exception as e:
        log.warning("browse(%s) failed: %s", url, e)
        return Page(url=url, title="", text=f"[Error fetching {url}: {e}]")

    title, text = _html_to_text(raw_html)
    log.info("browse(%s) → %d chars", url, len(text))
    return Page(url=final_url, title=title, text=text, raw_html=raw_html)


def read_article(url: str, timeout: int = _TIMEOUT) -> Page:
    """Fetch a URL and extract article content (optimized for articles).

    Uses trafilatura if installed, otherwise falls back to stdlib extraction.
    """
    try:
        final_url, raw_html = _fetch(url, timeout)
    except Exception as e:
        log.warning("read_article(%s) failed: %s", url, e)
        return Page(url=url, title="", text=f"[Error fetching {url}: {e}]")

    if _check_trafilatura():
        title, text = _extract_article_trafilatura(raw_html, final_url)
    else:
        title, text = _extract_article_stdlib(raw_html)

    if not text:
        # Fallback to full page extraction
        title, text = _html_to_text(raw_html)

    log.info("read_article(%s) → %d chars (trafilatura=%s)",
             url, len(text), _HAS_TRAFILATURA)
    return Page(url=final_url, title=title, text=text, raw_html=raw_html)


def search(query: str, max_results: int = 8) -> list[SearchResult]:
    """Search the web via DuckDuckGo. Returns list of SearchResult."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    try:
        _, body = _fetch(url, timeout=15, headers={
            "Referer": "https://duckduckgo.com/",
        })
    except Exception as e:
        log.warning("search(%s) failed: %s", query, e)
        return []

    results = []
    # Parse DuckDuckGo HTML results
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        body, re.DOTALL
    )

    for raw_url, raw_title, raw_snippet in blocks[:max_results]:
        # Decode DDG redirect URL
        actual_url = raw_url
        if "/l/?uddg=" in raw_url:
            m = re.search(r"uddg=([^&]+)", raw_url)
            if m:
                actual_url = urllib.parse.unquote(m.group(1))

        title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        snippet = html.unescape(re.sub(r"<[^>]+>", "", raw_snippet)).strip()

        # Skip DDG ad results
        if "duckduckgo.com/y.js" in actual_url:
            continue
        if title and actual_url:
            results.append(SearchResult(
                title=title, url=actual_url, snippet=snippet
            ))

    log.info("search(%s) → %d results", query, len(results))
    return results


def search_and_read(query: str, max_results: int = 3,
                    max_chars_per_page: int = 3000) -> str:
    """Search, then read top results. Returns formatted text for prompts.

    Useful for agents that need to research a topic autonomously.
    """
    results = search(query, max_results=max_results)
    if not results:
        return f"[No search results for: {query}]"

    parts = [f"Search: {query}\n"]
    for i, r in enumerate(results, 1):
        parts.append(f"\n--- Result {i}: {r.title} ---")
        parts.append(f"URL: {r.url}")
        page = read_article(r.url)
        if page.ok:
            parts.append(page.summary(max_chars_per_page))
        else:
            parts.append(f"Snippet: {r.snippet}")

    return "\n".join(parts)
