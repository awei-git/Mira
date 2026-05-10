"""Comparative book project — 30-day multi-book reading sequence.

Runs alongside the normal daily single-book review. Each project compares
two or three books over 30 daily points, writing one polished Chinese essay
per day after draft/review/revision rounds.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
_LIB_DIR = _AGENTS_DIR.parent / "lib"
sys.path.insert(0, str(_LIB_DIR))

from config import ARTIFACTS_DIR, MIRA_DIR, SOUL_DIR
from llm import model_think

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [comparative_books] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("comparative_books")

STATE_FILE = SOUL_DIR / "comparative_book_project_state.json"
CACHE_DIR = SOUL_DIR / "comparative_book_cache"
PROJECTS_DIR = ARTIFACTS_DIR / "books" / "comparative"
ICLOUD_BOOKS = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "MtJoy" / "Books"
TARGET_CHARS = 3000
TOTAL_POINTS = 30
MAX_SOURCE_CHARS_PER_BOOK = 210_000


PROJECT_ID = "latin-american-time-memory"
PROJECT_TITLE = "三本拉美小说里的时间、死亡和说话的人"
PROJECT_BOOKS = [
    {
        "title": "百年孤独",
        "author": "加西亚·马尔克斯",
        "aliases": ["百年孤独", "马尔克斯", "加西亚马尔克斯", "孤独"],
    },
    {
        "title": "酒吧长谈",
        "author": "马里奥·巴尔加斯·略萨",
        "aliases": ["酒吧长谈", "略萨", "巴尔加斯", "酒吧"],
    },
    {
        "title": "佩德罗巴拉莫",
        "author": "胡安·鲁尔福",
        "aliases": ["佩德罗巴拉莫", "佩德罗·巴拉莫", "鲁尔福", "巴拉莫"],
    },
]

POINTS = [
    "先问一个笨问题：为什么这三本书都不肯正常讲故事？",
    "家族、城镇和国家，哪一个才是真正的主角？",
    "时间不是流动的河，是一间没人收拾的屋子。",
    "死者为什么比活人更会说话？",
    "名字重复的时候，命运是不是也在偷懒？",
    "酒吧、马孔多和科马拉：三个地方如何把人困住。",
    "父亲这个角色为什么总像一笔坏账？",
    "母亲、情人和幽灵：女性在三本书里的沉默权力。",
    "暴力不是事件，是天气。",
    "笑话、流言和诅咒：民间语言怎样替历史记账。",
    "为什么越宏大的历史，越需要琐碎的细节来证明？",
    "政治失败为什么常常长得像家庭失败？",
    "孤独不是情绪，是一种社会制度。",
    "这三本书里，真相为什么总是迟到？",
    "谁在讲述，谁就先作弊。",
    "记忆不是保存过去，而是继续惩罚现在。",
    "荒诞感从哪里来：魔幻、醉话，还是坟墓里的实话？",
    "三本书如何处理欲望：热、脏、滑稽，又很可怜。",
    "如果没有旁观者，罪行是否还存在？",
    "拉美小说里的现代性为什么总像一个误送的包裹？",
    "重复为什么不是无聊，而是一种历史机器？",
    "人物为什么总像被某个看不见的句子推着走？",
    "三本书里最残酷的东西，其实是叙事耐心。",
    "为什么失败的革命，比成功的革命更适合文学？",
    "城镇会不会做梦？如果会，它梦见谁？",
    "幽默感从灾难里长出来时，为什么反而更可信？",
    "这三本书怎样让读者变成帮凶？",
    "孤独、腐败和鬼魂：三种看似不同的同一种东西。",
    "结尾不是结束，是叙事终于承认自己没法救人。",
    "读完一个月后，留下来的不是观点，是一种听见历史喘气的方式。",
]


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "br", "div", "h1", "h2", "h3", "li"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        elif tag == "p":
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(html: str) -> str:
    parser = _HTMLStripper()
    parser.feed(html)
    return parser.text()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_title(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def discover_book_path(book: dict) -> str:
    aliases = [normalize_title(item) for item in book.get("aliases", [])]
    if not ICLOUD_BOOKS.exists():
        return ""
    candidates = []
    for epub in ICLOUD_BOOKS.rglob("*.epub"):
        name = normalize_title(epub.stem)
        score = sum(1 for alias in aliases if alias and alias in name)
        if score:
            candidates.append((score, len(epub.name), epub))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return str(candidates[0][2])


def safe_name(text: str, max_len: int = 80) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return (cleaned or "comparative-books")[:max_len]


def extract_epub_text(epub_path: Path) -> str:
    if str(epub_path).startswith(str(ICLOUD_BOOKS)):
        try:
            subprocess.run(["brctl", "download", str(epub_path)], timeout=45, capture_output=True)
        except Exception as exc:
            log.warning("iCloud download request failed for %s: %s", epub_path, exc)
    try:
        with zipfile.ZipFile(epub_path) as zf:
            names = [
                name
                for name in zf.namelist()
                if name.endswith((".xhtml", ".html", ".htm"))
                and "toc" not in name.lower()
                and "nav" not in name.lower()
                and "cover" not in name.lower()
            ]
            names.sort()
            parts = []
            total = 0
            for name in names:
                raw = zf.read(name).decode("utf-8", errors="replace")
                text = strip_html(raw).strip()
                if len(text) < 80:
                    continue
                parts.append(text)
                total += len(text)
                if total >= MAX_SOURCE_CHARS_PER_BOOK:
                    break
            return "\n\n---\n\n".join(parts)[:MAX_SOURCE_CHARS_PER_BOOK]
    except Exception as exc:
        log.warning("EPUB text extraction failed for %s: %s", epub_path, exc)
        return ""


def ensure_project_state() -> dict:
    state = load_state()
    if state.get("project_id") == PROJECT_ID:
        refresh_source_paths(state)
        return state

    project_dir = PROJECTS_DIR / PROJECT_ID
    books = []
    for book in PROJECT_BOOKS:
        item = dict(book)
        item["epub_path"] = discover_book_path(book)
        books.append(item)

    state = {
        "project_id": PROJECT_ID,
        "title": PROJECT_TITLE,
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "project_dir": str(project_dir),
        "total_points": TOTAL_POINTS,
        "target_chars": TARGET_CHARS,
        "current_point": 1,
        "completed_points": [],
        "books": books,
        "points": POINTS,
    }
    refresh_source_paths(state)
    save_state(state)
    write_index(state)
    return state


def refresh_source_paths(state: dict) -> None:
    changed = False
    for book in state.get("books", []):
        if book.get("epub_path"):
            continue
        discovered = discover_book_path(book)
        if discovered:
            book["epub_path"] = discovered
            changed = True
    missing = [book["title"] for book in state.get("books", []) if not book.get("epub_path")]
    if missing:
        state["status"] = "blocked_missing_sources"
        state["missing_sources"] = missing
    elif state.get("status") == "blocked_missing_sources":
        state["status"] = "active"
        state.pop("missing_sources", None)
    if changed:
        state["updated_at"] = datetime.now().isoformat()
    if changed or missing:
        save_state(state)


def cache_sources(state: dict) -> dict[str, str]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    unreadable = []
    for book in state.get("books", []):
        title = book["title"]
        cache_path = CACHE_DIR / f"{PROJECT_ID}_{safe_name(title)}.txt"
        if cache_path.exists() and cache_path.stat().st_size > 1000:
            out[title] = cache_path.read_text(encoding="utf-8")
            continue
        epub_path = book.get("epub_path")
        if not epub_path:
            log.warning("No EPUB path found for %s", title)
            out[title] = ""
            continue
        text = extract_epub_text(Path(epub_path))
        if text:
            cache_path.write_text(text, encoding="utf-8")
            book["text_cache"] = str(cache_path)
        if len(text) < 3000:
            unreadable.append(title)
        out[title] = text
    if unreadable:
        state["status"] = "blocked_unreadable_sources"
        state["unreadable_sources"] = unreadable
    elif state.get("status") == "blocked_unreadable_sources":
        state["status"] = "active"
        state.pop("unreadable_sources", None)
    save_state(state)
    return out


def source_window(text: str, point: int, total: int, size: int = 5200) -> str:
    if not text:
        return "（暂未读取到这本书的本地文本，只能基于项目设定和前文推进。）"
    if len(text) <= size:
        return text
    center = int((point - 0.5) / total * len(text))
    start = max(0, center - size // 2)
    end = min(len(text), start + size)
    return text[start:end]


def load_previous_context(project_dir: Path, point: int) -> str:
    if point <= 1:
        return "这是第一篇，没有前文。"
    parts = []
    for prev in range(1, point):
        path = project_dir / f"point{prev:02d}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        first = text.splitlines()[0].lstrip("# ").strip() if text else f"第 {prev} 篇"
        if prev >= max(1, point - 4):
            parts.append(f"### 前文第 {prev} 篇：{first}\n{text[:2200]}")
        else:
            parts.append(f"- 第 {prev} 篇：{first}。摘要片段：{text[:360]}")
    return "\n\n".join(parts) if parts else "前文文件暂缺。"


def build_prompt(state: dict, point: int, sources: dict[str, str], previous: str) -> str:
    topic = state["points"][point - 1]
    source_sections = []
    for book in state.get("books", []):
        title = book["title"]
        author = book["author"]
        excerpt = source_window(sources.get(title, ""), point, state["total_points"])
        source_sections.append(f"## 《{title}》作者：{author}\n{excerpt}")

    return f"""你是 Mira。现在做一个三十天的三书并读项目。

项目题目：{state['title']}
今天是第 {point} 个点，共 {state['total_points']} 个点。
今天的问题：{topic}

三本书：
- 《百年孤独》
- 《酒吧长谈》
- 《佩德罗巴拉莫》

写作目标：
- 全中文，不要夹杂英文，不要使用英文术语。
- 约三千个中文字符。可以略短或略长，但不要写成万字论文。
- 语言要诙谐、睿智、幽默。不是段子合集，而是聪明人带一点坏笑的认真阅读。
- 必须比较三本书。不要轮流摘要三本书，要让它们互相打架。
- 每天只抓一个点，写透，不要贪多。
- 必须接上前文。读者应该感觉这是一个连续三十天项目，而不是三十篇散稿。
- 观点要尖锐、具体，有判断。不要写“值得深思”“展现了复杂性”这种空话。
- 可以有小标题，但不要模板化。标题必须是中文短句，带判断。
- 不要解释你在做什么，不要列规则，直接输出文章正文。

前文 context：
{previous}

今日可用原文片段：
{chr(10).join(source_sections)}
"""


def review_prompt(draft: str, state: dict, point: int, round_no: int) -> str:
    topic = state["points"][point - 1]
    return f"""请审稿。只输出中文审稿意见，不要客套。

项目：{state['title']}
第 {point} 个点：{topic}
第 {round_no} 轮审稿。

检查标准：
1. 是否全中文，有没有英文或翻译腔。
2. 是否真的比较了《百年孤独》《酒吧长谈》《佩德罗巴拉莫》。
3. 是否接上前文，而不是孤立文章。
4. 是否约三千字，密度够但不拖。
5. 是否诙谐、睿智、幽默，而不是干巴巴的论文腔。
6. 是否有一个清楚、有趣、可争辩的中心判断。

请给出：
- 最该保留的一点。
- 最大问题。
- 必须修改的三条。
- 一句更好的标题建议。

稿件：
{draft}
"""


def revise_prompt(draft: str, critique: str, state: dict, point: int, previous: str) -> str:
    topic = state["points"][point - 1]
    return f"""按审稿意见改稿。直接输出完整改后文章，必须全中文。

项目：{state['title']}
第 {point} 个点：{topic}

硬要求：
- 不要夹杂英文。
- 不要写成论文摘要。
- 三本书必须互相照亮、互相拆台。
- 保持诙谐、睿智、幽默。
- 接住前文，不要重复前文。
- 约三千个中文字符。

前文提醒：
{previous[:5000]}

审稿意见：
{critique}

原稿：
{draft}
"""


def enforce_chinese_prompt(draft: str) -> str:
    return f"""把下面文章改成纯中文终稿。

要求：
- 删除或翻译所有英文、拼音式夹杂、英文标点感很重的表达。
- 不改变核心论点。
- 保留幽默、锋利和节奏。
- 直接输出终稿。

文章：
{draft}
"""


def latin_leak_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", text))


def generate_article(state: dict, point: int, sources: dict[str, str]) -> str:
    project_dir = Path(state["project_dir"])
    previous = load_previous_context(project_dir, point)

    draft = model_think(build_prompt(state, point, sources, previous), model_name="deepseek", timeout=600)
    if not draft:
        raise RuntimeError("initial draft returned empty")

    current = draft.strip()
    for round_no in range(1, 3):
        critique = model_think(review_prompt(current, state, point, round_no), model_name="deepseek", timeout=360)
        if not critique:
            log.warning("Review round %d returned empty; keeping current draft", round_no)
            continue
        revised = model_think(
            revise_prompt(current, critique, state, point, previous), model_name="deepseek", timeout=600
        )
        if revised:
            current = revised.strip()

    if latin_leak_count(current) > 0:
        cleaned = model_think(enforce_chinese_prompt(current), model_name="deepseek", timeout=360)
        if cleaned:
            current = cleaned.strip()
    return current


def write_index(state: dict) -> None:
    project_dir = Path(state["project_dir"])
    project_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {state['title']}",
        "",
        "三十天，三本书，每天一个点。",
        "",
        "## 书目",
        "",
    ]
    for book in state.get("books", []):
        path = book.get("epub_path") or "未找到本地文本"
        lines.append(f"- 《{book['title']}》：{book['author']}。来源：`{path}`")
    lines.extend(["", "## 进度", ""])
    completed = set(state.get("completed_points", []))
    for idx, topic in enumerate(state.get("points", []), start=1):
        article = project_dir / f"point{idx:02d}.md"
        if idx in completed and article.exists():
            title = article.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            lines.append(f"- 第 {idx:02d} 点：[{title}](point{idx:02d}.md)")
        else:
            lines.append(f"- 第 {idx:02d} 点：{topic}（待写）")
    (project_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def deliver_to_bridge(state: dict, point: int, article: str) -> None:
    try:
        from bridge import Mira

        bridge = Mira(MIRA_DIR)
        today = datetime.now().strftime("%Y%m%d")
        item_id = f"comparative_books_{PROJECT_ID}_{point:02d}_{today}"
        title = article.splitlines()[0].lstrip("# ").strip() if article.strip() else state["title"]
        feed_title = f"三书并读 {point:02d}/30：{title}"
        if bridge.item_exists(item_id):
            bridge.update_status(item_id, "done", agent_message=article)
        else:
            bridge.create_feed(
                item_id,
                feed_title,
                article,
                tags=["mira", "book-review", "comparative-reading", "三书并读"],
            )
    except Exception as exc:
        log.warning("Bridge delivery failed: %s", exc)


def run_once(*, force: bool = False) -> int:
    state = ensure_project_state()
    refresh_source_paths(state)
    missing = state.get("missing_sources") or []
    if missing:
        log.warning("Comparative project blocked; missing source EPUB(s): %s", ", ".join(missing))
        write_index(state)
        return 2
    if state.get("status") != "active":
        log.info("Comparative project is not active: %s", state.get("status"))
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_run_date") == today and not force:
        log.info("Comparative project already ran today")
        return 0

    point = int(state.get("current_point") or 1)
    if point > int(state.get("total_points") or TOTAL_POINTS):
        state["status"] = "complete"
        save_state(state)
        write_index(state)
        log.info("Comparative project complete")
        return 0

    sources = cache_sources(state)
    unreadable = state.get("unreadable_sources") or []
    if unreadable:
        log.warning("Comparative project blocked; unreadable source text: %s", ", ".join(unreadable))
        write_index(state)
        return 2

    log.info("Writing comparative point %d/%d", point, state["total_points"])
    article = generate_article(state, point, sources)
    if len(article) < 1200:
        raise RuntimeError(f"article too short: {len(article)} chars")

    project_dir = Path(state["project_dir"])
    project_dir.mkdir(parents=True, exist_ok=True)
    article_path = project_dir / f"point{point:02d}.md"
    article_path.write_text(article, encoding="utf-8")

    completed = state.setdefault("completed_points", [])
    if point not in completed:
        completed.append(point)
    state["last_run_date"] = today
    state["current_point"] = point + 1
    state["last_article_path"] = str(article_path)
    state["updated_at"] = datetime.now().isoformat()
    save_state(state)
    write_index(state)
    deliver_to_bridge(state, point, article)
    log.info("Comparative point %d written: %s", point, article_path)
    return 0


def main(argv: list[str]) -> int:
    if "--init" in argv:
        state = ensure_project_state()
        write_index(state)
        print(state["project_dir"])
        return 0
    return run_once(force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
