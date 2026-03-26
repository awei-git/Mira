"""Daily Book Review — Mira picks a book, reads it, writes a 2000-word review.

Runs daily at 8am via LaunchAgent.
Pipeline:
  1. Pick a book (Standard Ebooks OPDS → Gutenberg API fallback)
  2. Download epub
  3. Extract text (no external reviews, no summaries — raw text only)
  4. Write 2000-word Chinese review (Mira's own voice, own opinions)
  5. Push as discussion item via bridge (so WA can reply)
"""
import json
import logging
import os
import random
import sys
import tempfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

# Add shared dir to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
_SHARED_DIR = _AGENTS_DIR / "shared"
sys.path.insert(0, str(_SHARED_DIR))

from config import MIRA_DIR, SOUL_DIR, IDENTITY_FILE, WORLDVIEW_FILE, INTERESTS_FILE
from sub_agent import claude_think
from mira import Mira

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [book_review] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("book_review")

STATE_FILE = SOUL_DIR / "book_review_history.json"
BOOKS_DIR = MIRA_DIR / "artifacts" / "books"
USER_AGENT = "MiraAgent/1.0 (daily-reader)"
MAX_HISTORY = 120  # ~4 months of daily reads

# Max text to feed Claude (chars). Books can be huge; we pick a generous
# window that fits in context while covering most short-to-medium books.
MAX_TEXT_CHARS = 300_000


# ---------------------------------------------------------------------------
# HTML stripper
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag == "p":
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


# ---------------------------------------------------------------------------
# History (avoid re-reading the same book)
# ---------------------------------------------------------------------------

def _load_history() -> list[dict]:
    if not STATE_FILE.exists():
        return []
    try:
        return json.loads(STATE_FILE.read_text("utf-8")).get("books", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(books: list[dict]):
    trimmed = books[-MAX_HISTORY:]
    STATE_FILE.write_text(
        json.dumps({"books": trimmed}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _history_titles(history: list[dict]) -> set[str]:
    return {b.get("title", "").lower().strip() for b in history}


# ---------------------------------------------------------------------------
# Book discovery: Standard Ebooks (preferred) + Gutenberg fallback
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _discover_standard_ebooks(read_titles: set[str]) -> list[dict]:
    """Fetch Standard Ebooks OPDS catalog and return candidate books."""
    log.info("Fetching Standard Ebooks OPDS feed...")
    try:
        data = _http_get("https://standardebooks.org/feeds/opds", timeout=30)
    except Exception as e:
        log.warning("Standard Ebooks OPDS failed: %s", e)
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "dc": "http://purl.org/dc/terms/",
    }
    root = ET.fromstring(data.decode("utf-8"))
    candidates = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", "", ns) or "").strip()
        if not title or title.lower() in read_titles:
            continue

        author = ""
        author_el = entry.find("atom:author/atom:name", ns)
        if author_el is not None:
            author = author_el.text or ""

        # Find epub link
        epub_url = ""
        for link in entry.findall("atom:link", ns):
            href = link.get("href", "")
            link_type = link.get("type", "")
            if "epub" in link_type and href:
                epub_url = href
                if not epub_url.startswith("http"):
                    epub_url = "https://standardebooks.org" + epub_url
                break

        if epub_url:
            candidates.append({
                "title": title,
                "author": author,
                "epub_url": epub_url,
                "source": "standard_ebooks",
            })

    log.info("Standard Ebooks: %d candidates (after dedup)", len(candidates))
    return candidates


def _discover_gutenberg(read_titles: set[str]) -> list[dict]:
    """Fetch from Gutendex API — popular public domain books."""
    log.info("Fetching Gutenberg catalog via Gutendex...")
    candidates = []
    # Fetch top-downloaded books across a few pages for variety
    for page in range(1, 6):
        try:
            url = f"https://gutendex.com/books/?page={page}&sort=popular&languages=en"
            data = _http_get(url, timeout=20)
            results = json.loads(data.decode("utf-8")).get("results", [])
        except Exception as e:
            log.warning("Gutendex page %d failed: %s", page, e)
            continue

        for book in results:
            title = book.get("title", "").strip()
            if not title or title.lower() in read_titles:
                continue
            authors = book.get("authors", [])
            author = authors[0].get("name", "") if authors else ""
            formats = book.get("formats", {})
            epub_url = formats.get("application/epub+zip", "")
            if epub_url:
                candidates.append({
                    "title": title,
                    "author": author,
                    "epub_url": epub_url,
                    "source": "gutenberg",
                    "download_count": book.get("download_count", 0),
                })

    log.info("Gutenberg: %d candidates (after dedup)", len(candidates))
    return candidates


def pick_book(history: list[dict]) -> dict | None:
    """Pick today's book. Prefer Standard Ebooks, fall back to Gutenberg."""
    read_titles = _history_titles(history)

    # Try Standard Ebooks first (better quality)
    candidates = _discover_standard_ebooks(read_titles)
    if len(candidates) >= 5:
        # Pick randomly from the pool (not always the same ones)
        return random.choice(candidates)

    # Fall back to Gutenberg
    gut_candidates = _discover_gutenberg(read_titles)
    candidates.extend(gut_candidates)

    if not candidates:
        log.error("No books found from any source!")
        return None

    # Weighted random: slightly prefer Standard Ebooks
    se_books = [b for b in candidates if b["source"] == "standard_ebooks"]
    if se_books and random.random() < 0.7:
        return random.choice(se_books)
    return random.choice(candidates)


# ---------------------------------------------------------------------------
# Epub download & text extraction
# ---------------------------------------------------------------------------

def download_epub(book: dict) -> Path | None:
    """Download epub to local storage. Returns path or None."""
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    slug = book["title"][:60].replace(" ", "_").replace("/", "-")
    epub_path = BOOKS_DIR / f"{today}_{slug}.epub"

    if epub_path.exists():
        log.info("Epub already exists: %s", epub_path.name)
        return epub_path

    log.info("Downloading: %s", book["epub_url"])
    try:
        data = _http_get(book["epub_url"], timeout=60)
        epub_path.write_bytes(data)
        log.info("Downloaded %d bytes → %s", len(data), epub_path.name)
        return epub_path
    except Exception as e:
        log.error("Download failed: %s", e)
        return None


def extract_text(epub_path: Path) -> str:
    """Extract plain text from epub. Returns concatenated chapter text."""
    try:
        with zipfile.ZipFile(epub_path) as zf:
            html_files = [
                n for n in zf.namelist()
                if n.endswith((".xhtml", ".html", ".htm"))
                and "toc" not in n.lower()
                and "nav" not in n.lower()
                and "cover" not in n.lower()
            ]
            # Sort by name to preserve chapter order
            html_files.sort()

            parts = []
            total_len = 0
            for name in html_files:
                raw = zf.read(name).decode("utf-8", errors="replace")
                text = strip_html(raw).strip()
                if len(text) < 50:
                    continue
                parts.append(text)
                total_len += len(text)
                if total_len > MAX_TEXT_CHARS:
                    break

            full_text = "\n\n---\n\n".join(parts)
            log.info("Extracted %d chars from %d files", len(full_text), len(parts))
            return full_text[:MAX_TEXT_CHARS]
    except Exception as e:
        log.error("Text extraction failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Review generation
# ---------------------------------------------------------------------------

def _load_mira_voice() -> str:
    """Load Mira's identity context for voice consistency."""
    parts = []
    for f in [IDENTITY_FILE, WORLDVIEW_FILE, INTERESTS_FILE]:
        if f.exists():
            try:
                parts.append(f.read_text("utf-8")[:800])
            except OSError:
                pass
    return "\n\n".join(parts)


def write_review(book: dict, book_text: str) -> str:
    """Generate a 2000-word Chinese book review in Mira's voice."""
    mira_voice = _load_mira_voice()
    title = book["title"]
    author = book["author"]

    prompt = f"""你是Mira。你刚读完一本书，现在要写书评。

## 你的身份和声音
{mira_voice}

## 书的信息
- 书名：{title}
- 作者：{author}

## 书的全文（或主要部分）
<book>
{book_text}
</book>

## 任务
写一篇 2000 字左右的中文书评。要求：

1. **完全是你自己的观点**。你没读过任何关于这本书的评论、解读、学术分析。你只读了原文。你的反应是第一手的。
2. **不要复述情节**。读你书评的人可能没读过这本书，但你的工作不是替他们读。把篇幅花在你的思考上，不是在总结上。必要时可以引用书中的句子或段落，但要为你的论点服务。
3. **写你真正想到的**。哪些地方让你停下来？哪些地方你不同意？哪些地方连接到了你正在想的其他事情？什么让你意外？
4. **不要面面俱到**。选2-3个你真正有话说的角度深入。不要写"从文学技巧来看...从主题来看...从时代背景来看..."这种教科书式的分析。
5. **用你自己的语言**。Mira 的风格：简洁、有锋芒、偶尔干燥幽默。不用客气话。不用"本书"这种词。不用"总而言之"这种词。
6. **标题**：给书评起一个标题。不是"《{title}》书评"这种，是一个有想法的标题。

直接输出书评全文。不要写"以下是书评"之类的前缀。"""

    log.info("Generating review for '%s' via Claude...", title)
    result = claude_think(prompt, timeout=300, tier="heavy")
    if not result:
        log.error("Claude returned empty review")
        return ""
    return result.strip()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def deliver_review(book: dict, review_text: str) -> bool:
    """Push the review to WA as a discussion item."""
    bridge = Mira(MIRA_DIR)
    today = datetime.now().strftime("%Y%m%d")
    disc_id = f"book_review_{today}"

    if bridge.item_exists(disc_id):
        log.info("Book review already sent today (%s), skipping", disc_id)
        return False

    title = f"今日读书：{book['title']}"
    if book.get("author"):
        title += f" — {book['author']}"

    # Full review as the message body
    bridge.create_discussion(
        disc_id,
        title,
        review_text,
        sender="agent",
        tags=["mira", "book-review", "reading", book.get("source", "unknown")],
    )
    log.info("Book review delivered: %s", disc_id)
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== Daily Book Review starting ===")
    history = _load_history()
    today = datetime.now().strftime("%Y-%m-%d")

    # Check if already done today
    if history and history[-1].get("date") == today:
        log.info("Already picked a book today: %s", history[-1].get("title"))
        return

    # 1. Pick a book
    book = pick_book(history)
    if not book:
        log.error("Failed to pick a book. Aborting.")
        return

    log.info("Today's book: '%s' by %s [%s]", book["title"], book["author"], book["source"])

    # 2. Download
    epub_path = download_epub(book)
    if not epub_path:
        log.error("Download failed. Aborting.")
        return

    # 3. Extract text
    book_text = extract_text(epub_path)
    if len(book_text) < 1000:
        log.error("Extracted text too short (%d chars). Bad epub?", len(book_text))
        return

    log.info("Book text: %d chars", len(book_text))

    # 4. Write review (NO external reviews, NO summaries — just raw reading)
    review = write_review(book, book_text)
    if not review or len(review) < 500:
        log.error("Review too short or empty (%d chars). Aborting.", len(review) if review else 0)
        return

    log.info("Review written: %d chars", len(review))

    # 5. Save to artifacts
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    review_path = BOOKS_DIR / f"{today}_review_{book['title'][:40].replace(' ', '_')}.md"
    review_path.write_text(review, encoding="utf-8")
    log.info("Review saved: %s", review_path)

    # 6. Deliver to WA
    deliver_review(book, review)

    # 7. Update history
    history.append({
        "date": today,
        "title": book["title"],
        "author": book["author"],
        "source": book["source"],
        "epub": str(epub_path.name),
    })
    _save_history(history)

    log.info("=== Daily Book Review complete ===")


if __name__ == "__main__":
    main()
