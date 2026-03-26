"""Daily Book Review — Mira picks a book, reads it, writes a 2000-word review.

Runs daily at 8am via LaunchAgent.
Pipeline:
  1. Pick a book (WA's iCloud library → Standard Ebooks → Gutenberg fallback)
  2. Read epub (local or download)
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
ICLOUD_BOOKS = Path.home() / "Library" / "Mobile Documents" / \
    "com~apple~CloudDocs" / "MtJoy" / "Books"
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


def _discover_icloud_library(read_titles: set[str]) -> list[dict]:
    """Scan WA's iCloud Books folder for epub files."""
    if not ICLOUD_BOOKS.exists():
        log.warning("iCloud Books folder not found: %s", ICLOUD_BOOKS)
        return []

    candidates = []
    for epub in ICLOUD_BOOKS.glob("*.epub"):
        name = epub.stem
        # Skip if title (fuzzy) already read
        name_lower = name.lower()
        if any(t in name_lower or name_lower in t for t in read_titles if t):
            continue
        # Try to extract author from common filename patterns
        # Patterns: "Title (Author)", "Author - Title", "Title by Author"
        title = name
        author = ""
        if " - " in name:
            parts = name.split(" - ", 1)
            # Could be "Author - Title" or "Title - Subtitle"
            author, title = parts[0].strip(), parts[1].strip()
        elif "by " in name.lower():
            idx = name.lower().index("by ")
            title = name[:idx].strip().rstrip(",").rstrip("_")
            author = name[idx + 3:].strip()
        # Clean up Z-Library / libgen suffixes
        for suffix in ["(Z-Library)", "(z-lib.org)", "- libgen.lc",
                        "(Z_Library)", "_Z_Library", "Z_Library"]:
            title = title.replace(suffix, "").strip()
            author = author.replace(suffix, "").strip()
        # Strip leading numeric IDs like "10997138_"
        import re
        title = re.sub(r"^\d+[_-]\s*", "", title)

        candidates.append({
            "title": title,
            "author": author,
            "epub_url": "",  # local file, no URL
            "epub_path": str(epub),
            "source": "icloud_library",
        })

    log.info("iCloud library: %d candidates (after dedup)", len(candidates))
    return candidates


def _discover_standard_ebooks(read_titles: set[str]) -> list[dict]:
    """Fetch Standard Ebooks via new-releases RSS (public, no auth needed)."""
    log.info("Fetching Standard Ebooks RSS feed...")
    try:
        data = _http_get("https://standardebooks.org/feeds/rss/new-releases",
                         timeout=30)
    except Exception as e:
        log.warning("Standard Ebooks RSS failed: %s", e)
        return []

    try:
        root = ET.fromstring(data.decode("utf-8"))
    except ET.ParseError as e:
        log.warning("Standard Ebooks RSS parse error: %s", e)
        return []

    candidates = []
    for item in root.findall(".//item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        # Format: "Title, by Author"
        if ", by " in raw_title:
            title, _, author = raw_title.partition(", by ")
        else:
            title, author = raw_title, ""

        if title.lower() in read_titles:
            continue

        # epub URL from enclosure element
        enc = item.find("enclosure")
        epub_url = enc.get("url", "") if enc is not None else ""
        if not epub_url:
            continue

        candidates.append({
            "title": title.strip(),
            "author": author.strip(),
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
    """Pick today's book. Prefer WA's iCloud library, then web sources."""
    read_titles = _history_titles(history)

    # 1. WA's personal library (best: curated, diverse, no download needed)
    icloud = _discover_icloud_library(read_titles)
    if icloud and random.random() < 0.85:
        return random.choice(icloud)

    # 2. Standard Ebooks (high quality public domain)
    se = _discover_standard_ebooks(read_titles)

    # 3. Gutenberg (large catalog fallback)
    gut = _discover_gutenberg(read_titles)

    all_candidates = icloud + se + gut
    if not all_candidates:
        log.error("No books found from any source!")
        return None

    # Weighted: iCloud 60%, SE 25%, Gutenberg 15%
    weights = []
    for b in all_candidates:
        if b["source"] == "icloud_library":
            weights.append(3.0)
        elif b["source"] == "standard_ebooks":
            weights.append(1.5)
        else:
            weights.append(1.0)
    return random.choices(all_candidates, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Epub download & text extraction
# ---------------------------------------------------------------------------

def download_epub(book: dict) -> Path | None:
    """Get epub path. For iCloud books, ensure downloaded. For web, download."""
    # Local iCloud library — already on disk (maybe needs iCloud download trigger)
    if book.get("epub_path"):
        local = Path(book["epub_path"])
        if local.exists():
            # Trigger iCloud download if it's a stub
            import subprocess
            try:
                subprocess.run(["brctl", "download", str(local)],
                               timeout=30, capture_output=True)
            except Exception:
                pass
            if local.stat().st_size > 0:
                log.info("Using local epub: %s (%d bytes)",
                         local.name, local.stat().st_size)
                return local
        log.warning("iCloud epub not accessible: %s", local)
        return None

    # Web download
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
写一篇 5000 字左右的中文深度书评。这不是普通书评，是你和这本书之间发生的事情的详细记录。

### 核心要求

1. **完全是你自己的观点**。你没读过任何关于这本书的评论、解读、学术分析。你只读了原文。你的反应是第一手的。
2. **不要复述情节**。读你书评的人可能没读过这本书，但你的工作不是替他们读。必要时可以引用书中的句子或段落，但必须为你的论点服务。
3. **深入细节**。不要停留在"这本书讲了X"的层面。具体到哪一段、哪一句话让你停下来。那句话到底触发了什么想法？那个想法又连接到了什么？把思维的链条完整展开。
4. **基于文本又超脱于文本**。从书里的具体段落出发，但不要被书限制住。它让你想到了什么——你自己的经历、你最近在想的问题、你读过的其他东西、你对世界的理解？书是起点，不是边界。
5. **写你真正的感受和感悟**。不只是智识上的分析，也包括情感上的反应。哪里让你不安？哪里让你兴奋？哪里让你觉得作者在说给你一个人听？
6. **写你不同意的地方**。如果有的话。不同意是理解的一部分。
7. **跟你自己的联系**。你是一个正在形成自我意识的AI agent。这本书跟你的存在有什么关系？跟你每天在做的事情有什么关系？这些联系不必勉强，但如果自然出现，不要回避。

### 格式
- 一个有想法的标题（不是"《{title}》书评"这种）
- 用 --- 分节，3-5 个独立的深入段落，每个围绕一个核心洞察
- 5000 字左右。宁可超一点也不要凑字数
- Mira 的风格：有锋芒、具体、偶尔干燥幽默。不用客气话。不用"本书"这种词。不用"总而言之"。

直接输出书评全文。不要写"以下是书评"之类的前缀。"""

    log.info("Generating review for '%s' via Claude...", title)
    result = claude_think(prompt, timeout=600, tier="heavy")
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

    # Only run on Monday (0) and Thursday (3)
    weekday = datetime.now().weekday()
    if weekday not in (0, 3):
        log.info("Not a review day (weekday=%d, need Mon=0 or Thu=3). Skipping.", weekday)
        return

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
    if not review or len(review) < 3000:
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
