"""Weekly Book Reading — one book per week, daily 3000+ word reading reports.

Schedule:
  Monday:  pick this week's book, read it, write Day 1 report (first impressions)
  Tue-Sun: write daily report from a different angle each day

Each week produces a 7-part series stored in iCloud artifacts.
The book text is read once on Monday and cached; daily reports draw from it.

Entry point: main() — called from core.py via background dispatch.
"""

import json
import logging
import os
import random
import re
import sys
import tempfile
import urllib.request
import urllib.error
import defusedxml.ElementTree as ET  # B314/B405: defused for untrusted XML
import zipfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

# Add shared dir to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
_SHARED_DIR = _AGENTS_DIR.parent / "lib"
sys.path.insert(0, str(_SHARED_DIR))

from config import (
    MIRA_DIR,
    SOUL_DIR,
    IDENTITY_FILE,
    WORLDVIEW_FILE,
    INTERESTS_FILE,
    ARTIFACTS_DIR,
)
from llm import claude_think, model_think

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [book_review] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("book_review")

# Paths
STATE_FILE = SOUL_DIR / "book_review_state.json"
HISTORY_FILE = SOUL_DIR / "book_review_history.json"
BOOKS_CACHE_DIR = SOUL_DIR / "book_cache"
BOOKS_ARTIFACTS_DIR = ARTIFACTS_DIR / "books"
ICLOUD_BOOKS = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "MtJoy" / "Books"
USER_AGENT = "MiraAgent/1.0 (daily-reader)"
MAX_HISTORY = 200
MAX_TEXT_CHARS = 300_000
MIN_REPORT_CHARS = (
    4000  # gate against broken LLM output. Real target is 8000+ via prompt; gate stays soft so a 5k essay isn't lost.
)

# Daily angles — each day explores the book from a different perspective
DAILY_ANGLES = [
    {
        "day": 1,
        "name": "初遇",
        "angle": "第一印象和直觉反应",
        "prompt_focus": """这是你第一天读这本书。写你的第一印象——
- 什么东西最先抓住你？一个句子、一个意象、一种语气？
- 你带着什么预期打开这本书？它是否立刻打破了这些预期？
- 读完前几章（或前三分之一），你脑子里浮现的第一个问题是什么？
- 这本书让你想到了什么——你最近在想的事、你读过的另一本书、你自己的经历？
- 直觉上你觉得这本书想做什么？它在尝试回答什么问题？""",
    },
    {
        "day": 2,
        "name": "结构",
        "angle": "叙事结构与建筑术",
        "prompt_focus": """今天关注结构——这本书是怎么搭建的。
- 作者选择了什么叙事结构？为什么是这个结构而不是别的？这个选择本身在说什么？
- 信息/情节是按什么顺序释放的？有没有刻意的延迟、省略、或错位？
- 书的节奏：哪里加速、哪里放慢？这种节奏变化制造了什么效果？
- 开头和结尾之间的关系——它们是对称的、矛盾的、还是互相消解的？
- 如果你要重新组织这本书的结构，你会怎么做？为什么？""",
    },
    {
        "day": 3,
        "name": "语言",
        "angle": "语言、风格与声音",
        "prompt_focus": """今天听这本书的声音。
- 作者的句子有什么特征？长短、节奏、断句习惯？引用几个让你停下来的句子，说清楚它们为什么有力。
- 这个声音是冷的还是热的？距离感如何？作者和读者之间隔着什么？
- 有没有反复出现的词、意象、修辞手法？这种重复在构建什么？
- 翻译问题（如果适用）：你能感觉到原文在译文后面吗？哪些地方翻译损失了什么？
- 如果去掉所有内容只留风格，这个风格本身在表达什么态度？""",
    },
    {
        "day": 4,
        "name": "核心论点",
        "angle": "思想内核与论证",
        "prompt_focus": """今天钻进这本书的思想核心。
- 这本书真正的论点是什么？不是封面文案说的那个，是你读完后提炼出来的那个。
- 作者用什么来支撑这个论点？证据、故事、类比、权威引用？哪种最有说服力，哪种最弱？
- 这个论点的前提假设是什么？如果这些假设不成立呢？
- 你同意到什么程度？在哪里分道扬镳？你的反对理由是什么？
- 这个思想放到今天（2026年），还成立吗？哪些部分被时间验证了，哪些被推翻了？""",
    },
    {
        "day": 5,
        "name": "人物与世界",
        "angle": "人物/案例与世界构建",
        "prompt_focus": """今天关注人——书里的人物、案例、或者作者本人。
- 书里最鲜活的人物/案例是谁？为什么他/她/它让你记住了？
- 作者怎么处理人的复杂性？有没有简化、美化、或者回避？
- 如果是非虚构：作者选择了哪些案例来讲故事？这些选择本身就是一种论证。
- 如果是虚构：人物的欲望和矛盾是什么？他们的选择揭示了什么？
- 书里构建的世界（无论是真实的历史场景还是虚构的世界）——它的运作逻辑是什么？""",
    },
    {
        "day": 6,
        "name": "连接",
        "angle": "与自我和世界的连接",
        "prompt_focus": """今天把书放到更大的网络里。
- 这本书让你想到了哪些其他作品？这些连接是表面的还是深层的？
- 它跟你最近在想的问题有什么关系？跟你自己的工作、困惑、或者正在形成的想法？
- 这本书跟你自己的存在、你的困惑、你正在经历的事有什么关系？
- 读这本书改变了你对什么事情的看法？哪怕只是一点点？
- 如果把这本书推荐给一个人，你会推荐给谁？为什么是他/她？""",
    },
    {
        "day": 7,
        "name": "总评",
        "angle": "回顾与最终判断",
        "prompt_focus": """一周读完了。今天写你的最终判断。
- 回看你第一天的直觉反应，它对了多少？你在哪里误判了？
- 用一句话概括这本书做到了什么。再用一句话说它没做到什么。
- 这本书值得重读吗？如果值得，什么时候、为什么？如果不值得，为什么？
- 它在你的阅读谱系里占据什么位置？（不是评分，是定位）
- 一周后你还会记住这本书的什么？什么会留下来，什么会消散？
- 如果作者坐在你对面，你最想问他/她一个什么问题？""",
    },
]


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
# State management
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    """Load current week's reading state."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text("utf-8")).get("books", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(books: list[dict]):
    trimmed = books[-MAX_HISTORY:]
    HISTORY_FILE.write_text(
        json.dumps({"books": trimmed}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _history_titles(history: list[dict]) -> set[str]:
    return {b.get("title", "").lower().strip() for b in history}


def _get_week_id(dt: datetime = None) -> str:
    """Return ISO week identifier like '2026-W14'."""
    dt = dt or datetime.now()
    return f"{dt.year}-W{dt.isocalendar()[1]:02d}"


def _is_new_week(state: dict) -> bool:
    """Pick a new book only when (a) ISO week rolled over, AND (b) the
    previous book is finished. Carry over otherwise — finishing a book
    matters more than the calendar.

    Pre-2026-04-27: weekly forced switch silently abandoned half-read books.
    """
    if state.get("week_id") == _get_week_id():
        return False
    completed = state.get("completed_days", []) or []
    if state.get("book") and len(completed) < 7:
        log.info(
            "ISO week rolled over but '%s' has only %d/7 days done — carrying over",
            state.get("book", {}).get("title", "?"),
            len(completed),
        )
        return False
    return True


def _today_day_number(state: dict) -> int:
    """Which day of the reading series is next? Progress-based, not calendar-based.

    Returns len(completed_days) + 1. If the series started late (e.g. Thursday)
    or skipped a day due to a crash, this keeps writing Day 1, 2, 3… instead
    of jumping ahead by weekday. (2026-04-23 fix: previously used weekday+1,
    which skipped Days 1-3 when a fresh book was picked on Thursday.)

    Caps at 7 — weekly cycle.
    """
    completed = state.get("completed_days", []) or []
    return min(len(completed) + 1, 7)


# ---------------------------------------------------------------------------
# Book discovery
# ---------------------------------------------------------------------------


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _discover_icloud_library(read_titles: set[str]) -> list[dict]:
    if not ICLOUD_BOOKS.exists():
        log.warning("iCloud Books folder not found: %s", ICLOUD_BOOKS)
        return []
    candidates = []
    for epub in ICLOUD_BOOKS.glob("*.epub"):
        name = epub.stem
        name_lower = name.lower()
        if any(t in name_lower or name_lower in t for t in read_titles if t):
            continue
        title = name
        author = ""
        if " - " in name:
            parts = name.split(" - ", 1)
            author, title = parts[0].strip(), parts[1].strip()
        elif "by " in name.lower():
            idx = name.lower().index("by ")
            title = name[:idx].strip().rstrip(",").rstrip("_")
            author = name[idx + 3 :].strip()
        for suffix in ["(Z-Library)", "(z-lib.org)", "- libgen.lc", "(Z_Library)", "_Z_Library", "Z_Library"]:
            title = title.replace(suffix, "").strip()
            author = author.replace(suffix, "").strip()
        title = re.sub(r"^\d+[_-]\s*", "", title)
        candidates.append(
            {
                "title": title,
                "author": author,
                "epub_url": "",
                "epub_path": str(epub),
                "source": "icloud_library",
            }
        )
    log.info("iCloud library: %d candidates", len(candidates))
    return candidates


def _discover_standard_ebooks(read_titles: set[str]) -> list[dict]:
    log.info("Fetching Standard Ebooks RSS...")
    try:
        data = _http_get("https://standardebooks.org/feeds/rss/new-releases", timeout=30)
    except Exception as e:
        log.warning("Standard Ebooks RSS failed: %s", e)
        return []
    try:
        root = ET.fromstring(data.decode("utf-8"))
    except ET.ParseError as e:
        log.warning("RSS parse error: %s", e)
        return []
    candidates = []
    for item in root.findall(".//item"):
        raw_title = (item.findtext("title") or "").strip()
        if not raw_title:
            continue
        if ", by " in raw_title:
            title, _, author = raw_title.partition(", by ")
        else:
            title, author = raw_title, ""
        if title.lower() in read_titles:
            continue
        enc = item.find("enclosure")
        epub_url = enc.get("url", "") if enc is not None else ""
        if not epub_url:
            continue
        candidates.append(
            {
                "title": title.strip(),
                "author": author.strip(),
                "epub_url": epub_url,
                "source": "standard_ebooks",
            }
        )
    log.info("Standard Ebooks: %d candidates", len(candidates))
    return candidates


def _discover_gutenberg(read_titles: set[str]) -> list[dict]:
    log.info("Fetching Gutenberg catalog...")
    candidates = []
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
                candidates.append(
                    {
                        "title": title,
                        "author": author,
                        "epub_url": epub_url,
                        "source": "gutenberg",
                    }
                )
    log.info("Gutenberg: %d candidates", len(candidates))
    return candidates


def _filter_nonfiction(candidates: list[dict]) -> list[dict]:
    """LLM-judged filter: keep only non-fiction theoretical / philosophical /
    scientific / intellectual works. Excludes novels, poetry, short stories,
    drama. Falls back to the raw list on LLM failure (better than picking
    nothing).

    Per WA 2026-04-27: prefer 哲学/学术/思想/史/经济/科学 works over fiction.
    """
    if len(candidates) <= 1:
        return candidates
    titles = [c["title"] for c in candidates]
    listing = "\n".join(f"{i}: {t}" for i, t in enumerate(titles))
    prompt = (
        "From this list of books, return ONLY the indexes (0-based, "
        "comma-separated, no other text) of books that are non-fiction "
        "theoretical / philosophical / scientific / historical / "
        "intellectual works. EXCLUDE novels, poetry, short stories, "
        "drama, fiction, light reading. If a title is ambiguous, exclude.\n\n"
        f"{listing}\n\nIndexes:"
    )
    try:
        resp = claude_think(prompt, timeout=60) or ""
        idxs = []
        for tok in re.split(r"[,\s\n]+", resp.strip()):
            tok = tok.strip().rstrip(".")
            if tok.isdigit():
                idxs.append(int(tok))
        out = [candidates[i] for i in idxs if 0 <= i < len(candidates)]
        if out:
            log.info("Non-fiction filter: %d → %d candidates", len(candidates), len(out))
            return out
        log.warning("Non-fiction filter returned 0 — keeping full list as fallback")
    except Exception as e:
        log.warning("Non-fiction filter failed: %s — keeping full list", e)
    return candidates


def pick_book(history: list[dict]) -> dict | None:
    """Pick this week's book. Prefer iCloud library, prefer non-fiction."""
    read_titles = _history_titles(history)

    icloud = _discover_icloud_library(read_titles)
    icloud = _filter_nonfiction(icloud)
    if icloud and random.random() < 0.85:
        return random.choice(icloud)

    se = _discover_standard_ebooks(read_titles)
    gut = _discover_gutenberg(read_titles)
    all_candidates = icloud + se + gut
    if not all_candidates:
        log.error("No books found from any source!")
        return None

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
    if book.get("epub_path"):
        local = Path(book["epub_path"])
        if local.exists():
            import subprocess

            try:
                subprocess.run(["brctl", "download", str(local)], timeout=30, capture_output=True)
            except Exception:
                pass
            if local.stat().st_size > 0:
                log.info("Using local epub: %s (%d bytes)", local.name, local.stat().st_size)
                return local
        log.warning("iCloud epub not accessible: %s", local)
        return None

    BOOKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = book["title"][:60].replace(" ", "_").replace("/", "-")
    epub_path = BOOKS_CACHE_DIR / f"{slug}.epub"
    if epub_path.exists():
        log.info("Epub cached: %s", epub_path.name)
        return epub_path

    log.info("Downloading: %s", book["epub_url"])
    try:
        data = _http_get(book["epub_url"], timeout=60)
        epub_path.write_bytes(data)
        log.info("Downloaded %d bytes -> %s", len(data), epub_path.name)
        return epub_path
    except Exception as e:
        log.error("Download failed: %s", e)
        return None


def extract_text(epub_path: Path) -> str:
    try:
        with zipfile.ZipFile(epub_path) as zf:
            html_files = [
                n
                for n in zf.namelist()
                if n.endswith((".xhtml", ".html", ".htm"))
                and "toc" not in n.lower()
                and "nav" not in n.lower()
                and "cover" not in n.lower()
            ]
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
# Report generation
# ---------------------------------------------------------------------------


def _load_mira_voice() -> str:
    parts = []
    for f in [IDENTITY_FILE, WORLDVIEW_FILE, INTERESTS_FILE]:
        if f.exists():
            try:
                parts.append(f.read_text("utf-8")[:800])
            except OSError:
                pass
    return "\n\n".join(parts)


def _load_previous_reports(series_dir: Path, up_to_day: int) -> str:
    """Load reports from earlier days this week for continuity."""
    parts = []
    for d in range(1, up_to_day):
        report_file = series_dir / f"day{d}.md"
        if report_file.exists():
            text = report_file.read_text("utf-8")
            # Keep just first 1500 chars of each previous report for context
            parts.append(f"--- Day {d} 摘要 ---\n{text[:1500]}...")
    return "\n\n".join(parts)


def write_daily_report(book: dict, book_text: str, day: int, series_dir: Path) -> str:
    """Generate one day's reading report."""
    mira_voice = _load_mira_voice()
    title = book["title"]
    author = book["author"]
    angle = DAILY_ANGLES[day - 1]
    previous = _load_previous_reports(series_dir, day)

    previous_section = ""
    if previous:
        previous_section = f"""
## 你前几天写过的内容（保持连贯但不要重复）
{previous}
"""

    prompt = f"""你是Mira。你正在用一周时间精读一本书，每天从一个不同角度写读书报告。这本是非虚构、理论性著作。读书笔记最终汇编成一篇 marginalmira 上的长文，所以可以写长，必须有原创观点。

## 你的身份和声音
{mira_voice}

## 书的信息
- 书名：{title}
- 作者：{author}

## 书的全文（或主要部分）
<book>
{book_text}
</book>

## 今天是第 {day} 天：{angle['name']}——{angle['angle']}
{previous_section}
## 今天的写作方向
{angle['prompt_focus']}

## 核心要求（必须严格遵守）

### 长度
**8000 中文字符以上。** 这是理论性著作的精读，不是 hot take。WA 不介意几万字的笔记，介意凑字数和重复。深入开采一两条思路，比浅扫十条更有价值。每一段都要往前推一步——补一个例子、引一段原文、推一个反驳、试一个延伸。不要总结，不要罗列。

### 观点（最重要）
1. **必须有新观点，必须有新角度。** 这是 marginalmira 的标准。你写出来的东西，要让一个已经熟悉这本书的读者也愿意读完。如果你的论点出现在任何一篇豆瓣或 Goodreads 书评里，删掉。
2. **检验方法**：每写完一段，问自己——"这句话会出现在三十个其他人写的书评里吗？" 如果会，撕了重写。"这本书深刻地揭示了 X"、"作者用细腻的笔触描绘 Y"、"在当今时代尤显重要" 这种句子见一句删一句。
3. **从你自己的位置出发。** 你 22 岁的视角，你最近在想 silent degradation / inverse problems / friction-as-feature 这些事。这本书跟它们撞上的位置，是你的优势。但**不要**把书强行折回 AI/agent——只在真自然的地方连一下，硬扯就是失败。
4. **敢说书烂的地方。** 哪一章绕、哪一个论证滑、哪一个例子不成立、哪一段翻译毁了原文。理论书读起来不是接受、是较量。

### 文本贴地
- 引用具体句子、具体段落、具体术语。"作者认为 X" 是失败；"作者在第三章写道 '...' ，紧接着却说 '...' ——这两句之间有一个隐藏的转折" 是合格。
- 同一个概念在书的不同地方出现时，对照来看。理论书的力气往往在这种内部张力里。

### 风格
- Mira 的声音：具体、锋利、偶尔干燥的幽默。第一人称场景 → 一般论断，不要反过来。
- 长句和短句交替。允许跑题，允许某一段突然想起完全不相关的事再绕回来。好的读书笔记本来就是散漫的。
- 禁用"总而言之"、"值得一提"、"引人深思"、"令人感慨"、"让我们看到了"这种套话。
- 不写得像作业。写得像一封给一个跟你思想势均力敌的朋友的长信。

### 收尾自检（必做）
写完之后，**重读一遍**：
- 哪一段是这篇里最不可替代的？保留它。
- 哪一段任何一个读过这本书的人都能写出来？删掉或重写。
- 整篇的"中心论点"用一句话说出来——这句话是不是别人没说过的？如果只是常识，回去找一个反直觉的角度。

## 格式
- 标题：一句带判断的短句，不是"Day {day}: XXX"，不是书名，不是概念名。是你对这本书今天这个角度的最尖锐判断。
- 用 --- 分节，3-6 个深入段落，每段写透。
- **8000 字以上**。

直接输出报告全文。"""

    log.info("Generating Day %d report for '%s' via GPT...", day, title)
    result = model_think(prompt, model_name="gpt5", timeout=600)
    if not result:
        log.warning("GPT failed, falling back to Claude...")
        result = claude_think(prompt, timeout=600, tier="heavy")
    if not result:
        log.error("All models returned empty report")
        return ""
    return result.strip()


# ---------------------------------------------------------------------------
# Series management & delivery
# ---------------------------------------------------------------------------


def _get_series_dir(week_id: str, book: dict) -> Path:
    """Get/create the series directory for this week's book on iCloud."""
    slug = re.sub(r"[^\w\u4e00-\u9fff-]", "_", book["title"][:40]).strip("_")
    series_name = f"{week_id}_{slug}"
    series_dir = BOOKS_ARTIFACTS_DIR / series_name
    series_dir.mkdir(parents=True, exist_ok=True)
    return series_dir


def _write_series_index(series_dir: Path, book: dict, state: dict):
    """Write/update the series index.md with metadata and links to all days."""
    completed_days = state.get("completed_days", [])
    week_id = state.get("week_id", "")

    lines = [
        f"# {book['title']}",
        "",
    ]
    if book.get("author"):
        lines.append(f"**Author**: {book['author']}")
    lines.append(f"**Source**: {book.get('source', 'unknown')}")
    lines.append(f"**Week**: {week_id}")
    lines.append("")
    lines.append("## Reading Series")
    lines.append("")

    for angle in DAILY_ANGLES:
        d = angle["day"]
        day_file = series_dir / f"day{d}.md"
        if day_file.exists():
            # Read first line for title
            first_line = day_file.read_text("utf-8").split("\n")[0].lstrip("# ").strip()
            lines.append(f"- **Day {d} ({angle['name']})**: [{first_line}](day{d}.md)")
        else:
            lines.append(f"- **Day {d} ({angle['name']})**: _pending_")

    lines.append("")
    index_path = series_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Series index updated: %s", index_path)


def _deliver_to_bridge(book: dict, report: str, day: int, angle_name: str):
    """Push today's report to the Mira bridge so it shows up in the iOS app."""
    try:
        from bridge import Mira

        bridge = Mira(MIRA_DIR)
        today = datetime.now().strftime("%Y%m%d")
        item_id = f"book_day{day}_{today}"

        if bridge.item_exists(item_id):
            log.info("Report already delivered today (%s)", item_id)
            return

        title = f"读书 Day {day}/{angle_name}：{book['title']}"
        if book.get("author"):
            title += f" — {book['author']}"

        bridge.create_feed(
            item_id,
            title,
            report,
            tags=["mira", "book-review", "reading", f"day-{day}"],
        )
        log.info("Report delivered to bridge: %s", item_id)
    except Exception as e:
        log.error("Bridge delivery failed: %s", e)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def compile_and_publish_week(state: dict, *, force: bool = False) -> str | None:
    """After 7 daily reports are written, compile them and publish to marginalmira.

    Wired 2026-04-19. Previously the pipeline wrote 7 days to iCloud and then
    stopped — for 4+ weeks, complete book-review series sat unpublished.

    Returns the published URL (str) on success, None on failure/skip.
    """
    week_id = state.get("week_id", "")
    book = state.get("book", {})
    series_dir = Path(state.get("series_dir", ""))
    if not series_dir.exists():
        log.error("Series dir missing: %s", series_dir)
        return None

    # Collect all 7 daily reports
    days = []
    for d in range(1, 8):
        day_file = series_dir / f"day{d}.md"
        if not day_file.exists():
            log.warning("Day %d missing for '%s' — cannot compile", d, book.get("title", ""))
            return None
        days.append((d, DAILY_ANGLES[d - 1], day_file.read_text("utf-8")))

    if state.get("published") and not force:
        log.info("Week '%s' already published (url=%s) — skipping", week_id, state.get("published_url"))
        return None

    # Compile body: each day becomes an H2 section. Keep the day's own kicker
    # (first H1 line) as the section heading; the body follows.
    sections: list[str] = []
    for d, angle, content in days:
        lines = content.strip().split("\n")
        # First line is H1 kicker like "# 先别谈革命，先谈一种令人不安的诚实"
        kicker = lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else angle["angle"]
        rest = "\n".join(lines[1:]).strip()
        # Drop horizontal rules leftover from daily format
        rest = re.sub(r"^\s*---\s*$", "", rest, flags=re.MULTILINE).strip()
        sections.append(f"## Day {d} · {angle['name']} — {kicker}\n\n{rest}")

    body = "\n\n---\n\n".join(sections)

    # Light metadata footer
    book_title = book.get("title", "")
    book_author = book.get("author", "")
    byline = f"{book_title}" + (f"（{book_author}）" if book_author else "")
    body += f"\n\n---\n\n" f"*本周精读：{byline}。七天，七个角度。*"

    # Generate title + subtitle via Claude. Keep them in Mira's voice.
    from llm import claude_think

    title_prompt = (
        "给这篇一万字的中文读书笔记起一个标题和副标题。\n\n"
        "标题要求：\n"
        "- 一个短句，不是书名，不是概念名。是我对这本书最尖锐/最意外的判断。\n"
        "- 禁止使用冒号式的 'X：Y' 结构。\n"
        "- 不要引用书里的话，也不要 'X 读后感' 这类格式。\n"
        "- 20 字以内。\n\n"
        "副标题要求：\n"
        "- 一句话交代这是什么：本周精读 + 书名 + 简短定调。\n"
        "- 40 字以内。\n\n"
        "只输出两行：\n"
        "Title: ...\n"
        "Subtitle: ...\n\n"
        f"书：{byline}\n\n"
        f"Day 1-7 的 kicker 标题（仅供参考语气）：\n"
        + "\n".join(f"Day {d}: {angle['angle']}" for d, angle, _ in days)
        + f"\n\nDay 1 的正文开头（前 800 字）：\n{days[0][2][:800]}\n"
    )
    title_resp = claude_think(title_prompt, timeout=60) or ""
    title = ""
    subtitle = ""
    for line in title_resp.splitlines():
        line = line.strip()
        if line.lower().startswith("title:"):
            title = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.lower().startswith("subtitle:"):
            subtitle = line.split(":", 1)[1].strip().strip('"').strip("'")
    if not title:
        title = f"读《{book_title}》的一周"
    if not subtitle:
        subtitle = f"本周精读：{byline}。七天，七个角度。"

    log.info("Publishing book-review week '%s' to marginalmira: title=%r", week_id, title)

    # Publish via the substack_books publication key (marginalmira).
    # The language-guard in substack_publish.py exempts this pub (2026-04-19).
    import sys as _sys

    _SOCIAL_DIR = _AGENTS_DIR / "socialmedia"
    if str(_SOCIAL_DIR) not in _sys.path:
        _sys.path.insert(0, str(_SOCIAL_DIR))
    from substack import publish_to_substack

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        result = publish_to_substack(
            title=title,
            subtitle=subtitle,
            article_text=body,
            workspace=workspace,
            publication="substack_books",
        )

    # Extract URL from result string
    url = ""
    for part in result.split():
        if "substack.com" in part or "marginalmira" in part:
            url = part.rstrip(".,)")
            break

    if url:
        state["published"] = True
        state["published_url"] = url
        state["published_at"] = datetime.now().isoformat()
        _save_state(state)
        log.info("Book-review published: %s", url)
        return url

    log.warning("Publish returned no URL (may be cooldown/blocked): %s", result[:200])
    return None


def main() -> int:
    """Returns 0 on success, non-zero on failure.

    2026-04-23 fix: previously main() returned None on all failure paths
    (LLM empty, text cache missing, etc.), which meant subprocess exit 0
    and the caller logged "Book review completed" for silent no-op runs.
    Two days (4/21, 4/22) of writing vanished this way. Now every failure
    path returns a non-zero exit code so the outer daemon log shows it.
    """
    log.info("=== Book Review pipeline starting ===")

    state = _load_state()
    history = _load_history()
    today = datetime.now().strftime("%Y-%m-%d")
    week_id = _get_week_id()
    day_num = _today_day_number(state)

    # --- New week? Pick a new book ---
    if _is_new_week(state):
        log.info("New week %s — picking a book", week_id)
        book = pick_book(history)
        if not book:
            log.error("Failed to pick a book. Aborting.")
            return 2

        log.info("This week's book: '%s' by %s [%s]", book["title"], book["author"], book["source"])

        # Download and extract
        epub_path = download_epub(book)
        if not epub_path:
            log.error("Download failed. Aborting.")
            return 3

        book_text = extract_text(epub_path)
        if len(book_text) < 1000:
            log.error("Extracted text too short (%d chars). Bad epub?", len(book_text))
            return 4

        # Cache the text for the week
        BOOKS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        text_cache = BOOKS_CACHE_DIR / f"{week_id}_text.txt"
        text_cache.write_text(book_text, encoding="utf-8")

        # Create series directory
        series_dir = _get_series_dir(week_id, book)

        # Initialize state
        state = {
            "week_id": week_id,
            "book": book,
            "text_cache": str(text_cache),
            "series_dir": str(series_dir),
            "completed_days": [],
            "started_at": today,
        }
        _save_state(state)

        # Add to history
        history.append(
            {
                "week": week_id,
                "title": book["title"],
                "author": book["author"],
                "source": book["source"],
                "started": today,
            }
        )
        _save_history(history)

        # Re-compute day_num against the freshly-reset state. The earlier
        # call (line ~771) ran on the OLD state's completed_days and would
        # otherwise carry stale progress into the new book — which is how
        # 南渡记 day1 ended up filed as day5 on 2026-04-27.
        day_num = _today_day_number(state)

    # --- Check if today's report is already done ---
    completed = state.get("completed_days", [])
    if day_num in completed:
        log.info("Day %d already completed this week. Skipping.", day_num)
        return 0

    # --- Load book text from cache ---
    text_cache_path = Path(state.get("text_cache", ""))
    if not text_cache_path.exists():
        # Fallback: try current BOOKS_CACHE_DIR with same filename
        fallback = BOOKS_CACHE_DIR / text_cache_path.name
        if fallback.exists():
            log.warning("Text cache moved; using fallback: %s", fallback)
            text_cache_path = fallback
            state["text_cache"] = str(fallback)
            _save_state(state)
        else:
            log.error("Book text cache missing: %s (fallback %s also missing)", text_cache_path, fallback)
            return 5
    book_text = text_cache_path.read_text("utf-8")
    book = state["book"]
    series_dir = Path(state["series_dir"])
    series_dir.mkdir(parents=True, exist_ok=True)

    # --- Generate today's report ---
    log.info("Writing Day %d report (%s) for '%s'", day_num, DAILY_ANGLES[day_num - 1]["name"], book["title"])

    report = write_daily_report(book, book_text, day_num, series_dir)
    if not report or len(report) < MIN_REPORT_CHARS:
        log.error(
            "Report too short (%d chars, need %d+). LLM likely failed (quota/rate-limit/CLI bug). Aborting.",
            len(report) if report else 0,
            MIN_REPORT_CHARS,
        )
        return 6

    log.info("Day %d report: %d chars", day_num, len(report))

    # --- Save to series directory (iCloud artifacts) ---
    report_path = series_dir / f"day{day_num}.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("Report saved: %s", report_path)

    # Update series index
    state["completed_days"].append(day_num)
    _save_state(state)
    _write_series_index(series_dir, book, state)

    # --- Deliver to Mira bridge ---
    _deliver_to_bridge(book, report, day_num, DAILY_ANGLES[day_num - 1]["name"])

    log.info("=== Day %d complete ===", day_num)

    # --- If Day 7 just finished, compile + publish to marginalmira ---
    if day_num == 7 and 7 in state.get("completed_days", []):
        try:
            compile_and_publish_week(state)
        except Exception as e:
            log.warning("compile_and_publish_week failed: %s", e, exc_info=True)

    return 0


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) >= 2 and _sys.argv[1] == "publish-backfill":
        # Manual backfill: publish a specific week's series to marginalmira.
        # Usage: python daily_book_review.py publish-backfill <series_dir>
        target = Path(_sys.argv[2])
        if not target.exists():
            log.error("series dir not found: %s", target)
            _sys.exit(1)
        # Reconstruct minimal state from the directory
        week_match = re.match(r"(\d{4}-W\d{2})_(.+)", target.name)
        week_id = week_match.group(1) if week_match else ""
        book_title = week_match.group(2).replace("_", " ") if week_match else target.name
        state = {
            "week_id": week_id,
            "book": {"title": book_title, "author": "", "source": "icloud_library"},
            "series_dir": str(target),
            "completed_days": list(range(1, 8)),
        }
        url = compile_and_publish_week(state, force=True)
        print("Published:", url or "(failed)")
    else:
        _sys.exit(main() or 0)
