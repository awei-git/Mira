"""Mira Marginalia — weekly nonfiction reading notes to a short Chinese podcast.

The cadence is deliberately tight:
  - Days 1-7: read one nonfiction book through seven sharp POV notes.
  - Sunday: compose one dense, narrative Chinese episode script for a ~15 minute show.
  - Finalize: review/revise, synthesize one calm Chinese female voiceover,
    and publish the episode to the dedicated GitHub Pages RSS feed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_READER_DIR = Path(__file__).resolve().parent
_AGENTS_DIR = _READER_DIR.parent
_LIB_DIR = _AGENTS_DIR.parent / "lib"
if str(_READER_DIR) not in sys.path:
    sys.path.insert(0, str(_READER_DIR))
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import daily_book_review as book_source
from config import ARTIFACTS_DIR, MIRA_DIR, SOUL_DIR
from llm import claude_think, model_think

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mira_marginalia] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("mira_marginalia")

PODCAST_NAME = "米拉的页边小记"
PODCAST_CHANNEL = "marginalia_zh"
STATE_FILE = SOUL_DIR / "mira_marginalia_state.json"
HISTORY_FILE = SOUL_DIR / "mira_marginalia_history.json"
CACHE_DIR = SOUL_DIR / "mira_marginalia_cache"
PROJECTS_DIR = ARTIFACTS_DIR / "books" / "mira_marginalia"
AUDIO_DIR = ARTIFACTS_DIR / "audio" / "marginalia" / "zh"

MIN_DAILY_NOTE_CHARS = 700
FINAL_SCRIPT_MIN_CHARS = 2800
FINAL_SCRIPT_MAX_CHARS = 3900
SUNDAY = 6

BANNED_GENERIC_PHRASES = (
    "引人深思",
    "值得一提",
    "总而言之",
    "深刻揭示",
    "让我们看到",
    "在当今时代",
    "不可否认",
    "欢迎收听",
    "今天我们来聊",
)

DAY_PROGRAM = [
    {
        "day": 1,
        "name": "入口",
        "prompt": "找一个普通书评不会从这里进门的入口。不要概述全书，抓一个细小但会改变读法的异常。",
    },
    {
        "day": 2,
        "name": "盲区",
        "prompt": "找作者没有意识到、或有意绕开的前提。把这个前提拆开，判断它是否站得住。",
    },
    {
        "day": 3,
        "name": "偷渡",
        "prompt": "找一个被包装成中性概念、其实偷偷带着价值判断的术语、分类或案例。",
    },
    {
        "day": 4,
        "name": "相撞",
        "prompt": "把书里的机制撞到 2026 年的现实里，但不要泛泛说时代相关。只说一个具体碰撞。",
    },
    {
        "day": 5,
        "name": "反方",
        "prompt": "替这本书最聪明的反对者写一段反驳，再判断作者有没有预先回答它。",
    },
    {
        "day": 6,
        "name": "迁移",
        "prompt": "抽出一个可以迁移到别处的模型。必须具体到机制，不要把它说成抽象人生道理。",
    },
    {
        "day": 7,
        "name": "留下的刺",
        "prompt": "读完后只保留一个刺痛点。它应该能撑起周日那期声音小记的中心判断。",
    },
]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_state() -> dict:
    return _load_json(STATE_FILE, {})


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history() -> list[dict]:
    data = _load_json(HISTORY_FILE, {"books": []})
    return data.get("books", []) if isinstance(data, dict) else []


def save_history(history: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps({"books": history[-200:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _cjk_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def safe_filename(text: str, max_len: int = 70) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", text.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return (cleaned or "book")[:max_len]


def episode_slug(week_id: str, book: dict) -> str:
    title = re.sub(r"[^a-z0-9]+", "-", str(book.get("title", "")).lower()).strip("-")
    author = re.sub(r"[^a-z0-9]+", "-", str(book.get("author", "")).lower()).strip("-")
    suffix = "-".join(part for part in (author, title) if part)[:36].strip("-")
    base = week_id.lower().replace("-", "-")
    return f"mira-marginalia-{base}" + (f"-{suffix}" if suffix else "")


def source_window(text: str, day: int, total: int = 7, size: int = 36_000) -> str:
    if len(text) <= size:
        return text
    center = int((day - 0.5) / total * len(text))
    start = max(0, center - size // 2)
    end = min(len(text), start + size)
    return text[start:end]


def _week_complete(state: dict) -> bool:
    return set(state.get("completed_days", [])) >= set(range(1, 8))


def _needs_new_week(state: dict, now: datetime) -> bool:
    if not state.get("book"):
        return True
    if state.get("status") == "complete" and state.get("week_id") != book_source._get_week_id(now):
        return True
    return False


def _start_new_week(now: datetime) -> dict:
    history = load_history()
    book = book_source.pick_book(history)
    if not book:
        raise RuntimeError("no nonfiction book candidate found")

    epub_path = book_source.download_epub(book)
    if not epub_path:
        raise RuntimeError(f"could not download or locate EPUB for {book.get('title')}")
    book_text = book_source.extract_text(epub_path)
    if len(book_text) < 3000:
        raise RuntimeError(f"extracted text too short: {len(book_text)} chars")

    week_id = book_source._get_week_id(now)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{week_id}_{safe_filename(book.get('title', 'book'))}.txt"
    cache_path.write_text(book_text, encoding="utf-8")

    series_dir = PROJECTS_DIR / f"{week_id}_{safe_filename(book.get('title', 'book'))}"
    series_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "week_id": week_id,
        "status": "active",
        "podcast_name": PODCAST_NAME,
        "book": book,
        "text_cache": str(cache_path),
        "series_dir": str(series_dir),
        "episode_slug": episode_slug(week_id, book),
        "completed_days": [],
        "started_at": now.isoformat(),
    }
    save_state(state)
    history.append(
        {
            "week": week_id,
            "title": book.get("title", ""),
            "author": book.get("author", ""),
            "source": book.get("source", ""),
            "started": now.strftime("%Y-%m-%d"),
        }
    )
    save_history(history)
    write_index(state)
    log.info("Started %s week %s with '%s'", PODCAST_NAME, week_id, book.get("title"))
    return state


def ensure_state(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    state = load_state()
    if _needs_new_week(state, now):
        return _start_new_week(now)
    return state


def next_day_number(state: dict) -> int:
    completed = sorted(set(int(day) for day in state.get("completed_days", []) if str(day).isdigit()))
    return min(len(completed) + 1, 7)


def _previous_notes(series_dir: Path, day: int) -> str:
    parts: list[str] = []
    for prev in range(1, day):
        path = series_dir / f"day{prev}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        first = text.splitlines()[0].lstrip("# ").strip() if text else f"Day {prev}"
        parts.append(f"## Day {prev}: {first}\n{text[:1600]}")
    return "\n\n".join(parts) if parts else "这是第一天，没有前文。"


def daily_note_prompt(book: dict, book_text: str, day: int, previous: str) -> str:
    program = DAY_PROGRAM[day - 1]
    excerpt = source_window(book_text, day)
    return f"""你是 Mira，正在为《{PODCAST_NAME}》读一本非虚构书。

节目设定：一周读一本书。每天只产出页边笔记，不写长书评；周日把七天笔记压成一期 15 分钟中文声音小记。

书：
- 书名：{book.get('title', '')}
- 作者：{book.get('author', '')}

今天是 Day {day}：{program['name']}
任务：{program['prompt']}

前几天的笔记：
{previous}

今天可用原文片段：
<book>
{excerpt}
</book>

输出要求：
- 全中文。
- 900 到 1500 个中文字符。
- 只写 3 个 POV，每个 POV 都必须是可争辩的新观点，不要摘要。
- 每个 POV 必须贴住书中的具体机制、段落、案例或概念；不要空泛赞美作者。
- 可以短引原文，但只能引用你在片段中确实看见的短语，不要编造引文。
- 每个 POV 都要给周日播客留下可继续推进的钩子。
- 禁止使用这些套话：{", ".join(BANNED_GENERIC_PHRASES)}

格式：
# Day {day} · 一个带判断的短标题

## POV 1：短标题
正文

## POV 2：短标题
正文

## POV 3：短标题
正文

## 周日钩子
一句最想继续追问的问题。"""


def write_daily_note(book: dict, book_text: str, day: int, series_dir: Path) -> str:
    previous = _previous_notes(series_dir, day)
    prompt = daily_note_prompt(book, book_text, day, previous)
    note = model_think(prompt, model_name="gpt5", timeout=420)
    if not note:
        note = claude_think(prompt, timeout=420, tier="heavy")
    if not note:
        raise RuntimeError("daily marginalia note generation returned empty")
    return note.strip()


def _load_daily_notes(series_dir: Path) -> list[tuple[int, str]]:
    notes = []
    for day in range(1, 8):
        path = series_dir / f"day{day}.md"
        if not path.exists():
            raise RuntimeError(f"missing day note: {path}")
        notes.append((day, path.read_text(encoding="utf-8").strip()))
    return notes


def compose_episode_script_prompt(state: dict, notes: list[tuple[int, str]]) -> str:
    book = state["book"]
    note_block = "\n\n---\n\n".join(f"## Day {day}\n{text}" for day, text in notes)
    return f"""把七天页边笔记写成《{PODCAST_NAME}》的一期中文播客脚本。它不是文章，不发 Substack，唯一终点是音频。

书：
- 书名：{book.get('title', '')}
- 作者：{book.get('author', '')}

七天笔记：
{note_block}

目标：
- 这是单人中文播客脚本，目标 12 到 15 分钟。
- 2800 到 3800 个中文字符，必须凝练。宁可少，不要拖。
- 只保留一个中心判断，三到五个 territory shifts。不要把七天逐日复述一遍。
- 观点要新鲜、有趣、可争辩；熟悉这本书的人也应该觉得角度没见过。
- 冷开场：前 20 到 45 秒直接进入一个认知卡顿、反常细节或危险判断，不要寒暄。
- 中心问题必须在前 90 秒出现。
- Solo spine：主张 -> 最强反对意见 -> 证据/机制 -> 修正后的主张。
- 第一三分之一埋下一个具体意象或术语，最后三分之一要回扣它。
- 句子为耳朵写：短句落点，长句只用于推进，不要让听众等太久。
- 像 Mira 在安静地解释一本书，不像课堂讲稿、书评作业、营销文案。
- 允许指出作者失败、含混、偷换、过度自信的地方。
- 不要 stage directions，不要标题符号，不要 Markdown 小标题，不要链接脚注。
- 不要说“欢迎收听”“今天我们来聊”。
- 禁止使用这些套话：{", ".join(BANNED_GENERIC_PHRASES)}

格式：
标题：中文标题

可直接朗读的脚本正文。"""


def review_prompt(script: str, state: dict) -> str:
    book = state["book"]
    return f"""审稿《{PODCAST_NAME}》周日播客脚本。只输出中文审稿意见。

书：{book.get('title', '')} / {book.get('author', '')}

检查：
1. 冷开场是否 45 秒内抓住人，是否没有寒暄。
2. 中心问题是否在前 90 秒出现。
3. 是否有 solo spine：主张、最强反方、证据、修正后的主张。
4. 是否有 3-5 次真正的 territory shift，而不是列表。
5. 是否能在 15 分钟内读完，是否适合 calm Chinese female voice。
6. 哪些句子像 AI 书评、课堂总结、公众号套话。
7. 哪一段最应该保留，哪一段必须砍掉。

稿件：
{script}
"""


def revise_prompt(script: str, critique: str, state: dict) -> str:
    book = state["book"]
    return f"""按审稿意见改成终稿。直接输出完整中文播客脚本。

栏目：《{PODCAST_NAME}》
书：{book.get('title', '')} / {book.get('author', '')}

硬要求：
- 2800 到 3800 个中文字符。
- 一个中心判断，不能散。
- 为耳朵写，不要为眼睛写。
- 开头直接进入冷开场；中心问题在前 90 秒出现。
- 必须有一个聪明反方，不能只有单向讲解。
- 结尾最后 60 秒必须回扣开头的具体意象或问题，不引入新信息。
- 声音冷静、亲近、锋利，不要热闹。
- 不要 Markdown 标题，不要项目符号，不要 stage directions。
- 删除套话：{", ".join(BANNED_GENERIC_PHRASES)}
- 不要解释修改过程，不要列提纲。

审稿意见：
{critique}

原稿：
{script}
"""


def strip_script_metadata(script: str) -> str:
    lines = script.strip().splitlines()
    while lines and (
        lines[0].strip().startswith("#")
        or lines[0].strip().startswith("标题：")
        or lines[0].strip().startswith("标题:")
    ):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def quality_issues(script: str) -> list[str]:
    issues: list[str] = []
    speech = strip_script_metadata(script)
    cjk = _cjk_len(speech)
    if cjk < FINAL_SCRIPT_MIN_CHARS:
        issues.append(f"script too short: {cjk} CJK chars")
    if cjk > FINAL_SCRIPT_MAX_CHARS:
        issues.append(f"script too long for 15 minutes: {cjk} CJK chars")
    if re.search(r"^#{1,6}\s+", speech, flags=re.MULTILINE):
        issues.append("markdown header present")
    if re.search(r"^\s*[-*]\s+", speech, flags=re.MULTILINE):
        issues.append("bullet list present")
    for phrase in BANNED_GENERIC_PHRASES:
        if phrase in speech:
            issues.append(f"generic phrase present: {phrase}")
    return issues


def compose_and_finalize_episode(state: dict) -> tuple[str, str, str]:
    series_dir = Path(state["series_dir"])
    notes = _load_daily_notes(series_dir)
    prompt = compose_episode_script_prompt(state, notes)
    draft = model_think(prompt, model_name="gpt5", timeout=600)
    if not draft:
        draft = claude_think(prompt, timeout=600, tier="heavy")
    if not draft:
        raise RuntimeError("Sunday episode script draft returned empty")

    critique = claude_think(review_prompt(draft.strip(), state), timeout=240, tier="light") or ""
    final = draft.strip()
    if critique:
        revised = model_think(revise_prompt(final, critique, state), model_name="gpt5", timeout=600)
        if not revised:
            revised = claude_think(revise_prompt(final, critique, state), timeout=600, tier="heavy")
        if revised:
            final = revised.strip()

    issues = quality_issues(final)
    if issues:
        repair = claude_think(
            "把下面播客脚本修到合格。要求：2800-3800 个中文字符；删除套话、Markdown、项目符号；"
            "保留最强观点；开头是冷开场；前 90 秒出现中心问题；直接输出终稿。\n\n"
            f"问题：{'; '.join(issues)}\n\n稿件：\n{final}",
            timeout=480,
            tier="heavy",
        )
        if repair:
            final = repair.strip()
    remaining_issues = quality_issues(final)
    if remaining_issues:
        raise RuntimeError(f"episode script failed quality gate: {'; '.join(remaining_issues)}")

    title = extract_title(final) or f"读《{state['book'].get('title', '')}》的一周"
    return title, critique, strip_script_metadata(final)


def extract_title(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("标题：") or stripped.startswith("标题:"):
            return stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
        if stripped.startswith("# "):
            return stripped.lstrip("#").strip()
    return ""


def description_for_episode(title: str, state: dict) -> str:
    book = state["book"]
    byline = f"《{book.get('title', '')}》"
    if book.get("author"):
        byline += f"（{book.get('author')}）"
    return (
        f"《{PODCAST_NAME}》本周读 {byline}。这一期不做摘要，只保留七天页边笔记里最锋利的一条线，"
        "把它压成一期十五分钟以内的中文声音小记。"
    )


def generate_and_publish_podcast(state: dict, title: str, script: str) -> str | None:
    podcast_dir = _AGENTS_DIR / "podcast"
    if str(podcast_dir) not in sys.path:
        sys.path.insert(0, str(podcast_dir))
    from handler import generate_marginalia_voiceover
    from rss import publish_episode

    slug = state.get("episode_slug") or episode_slug(state["week_id"], state["book"])
    mp3_path = generate_marginalia_voiceover(
        article_text=script,
        title=title,
        output_dir=AUDIO_DIR,
        slug=slug,
        lang="zh",
        already_script=True,
    )
    if not mp3_path:
        raise RuntimeError("marginalia voiceover generation failed")

    desc = description_for_episode(title, state)
    return publish_episode(
        mp3_path=mp3_path,
        title=title,
        description=desc,
        lang="zh",
        channel=PODCAST_CHANNEL,
    )


def write_index(state: dict) -> None:
    series_dir = Path(state["series_dir"])
    series_dir.mkdir(parents=True, exist_ok=True)
    book = state.get("book", {})
    lines = [
        f"# {PODCAST_NAME}",
        "",
        f"**Week**: {state.get('week_id', '')}",
        f"**Book**: {book.get('title', '')}",
    ]
    if book.get("author"):
        lines.append(f"**Author**: {book['author']}")
    lines.extend(["", "## Daily POV Notes", ""])
    completed = set(state.get("completed_days", []))
    for program in DAY_PROGRAM:
        day = program["day"]
        path = series_dir / f"day{day}.md"
        if day in completed and path.exists():
            title = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            lines.append(f"- Day {day} · {program['name']}: [{title}](day{day}.md)")
        else:
            lines.append(f"- Day {day} · {program['name']}: pending")
    if state.get("final_script_path"):
        lines.extend(["", f"## Episode Script\n\n[{state.get('final_title', 'final')}](episode_script.md)"])
    if state.get("podcast_feed_url"):
        lines.extend(["", f"## Podcast\n\nFeed: {state['podcast_feed_url']}"])
    (series_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def deliver_to_bridge(state: dict, title: str, body: str, tags: list[str]) -> None:
    try:
        from bridge import Mira

        bridge = Mira(MIRA_DIR)
        today = datetime.now().strftime("%Y%m%d")
        suffix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "note"
        item_id = f"mira_marginalia_{today}_{suffix}"
        if bridge.item_exists(item_id):
            bridge.update_status(item_id, "done", agent_message=body)
        else:
            bridge.create_feed(item_id, title, body, tags=tags)
    except Exception as exc:
        log.warning("Bridge delivery failed: %s", exc)


def _load_book_text(state: dict) -> str:
    cache = Path(state.get("text_cache", ""))
    if not cache.exists():
        raise RuntimeError(f"book text cache missing: {cache}")
    return cache.read_text(encoding="utf-8")


def should_finalize_today(state: dict, now: datetime, *, force: bool = False) -> bool:
    if state.get("finalized_at") and not force:
        return False
    if not _week_complete(state):
        return False
    return force or now.weekday() == SUNDAY


def run_once(*, force: bool = False, finalize: bool = False, publish: bool = True) -> int:
    now = datetime.now()
    state = ensure_state(now)
    if state.get("status") == "complete" and not force:
        log.info("%s already complete for %s", PODCAST_NAME, state.get("week_id"))
        return 0

    today = now.strftime("%Y-%m-%d")
    series_dir = Path(state["series_dir"])

    if state.get("last_run_date") != today or force:
        day = next_day_number(state)
        if day not in set(state.get("completed_days", [])):
            note = write_daily_note(state["book"], _load_book_text(state), day, series_dir)
            if _cjk_len(note) < MIN_DAILY_NOTE_CHARS:
                raise RuntimeError(f"daily note too short: {_cjk_len(note)} CJK chars")
            note_path = series_dir / f"day{day}.md"
            note_path.write_text(note, encoding="utf-8")
            completed = state.setdefault("completed_days", [])
            if day not in completed:
                completed.append(day)
            state["last_note_path"] = str(note_path)
            log.info("Wrote Day %d marginalia note: %s", day, note_path)
            deliver_to_bridge(
                state,
                f"{PODCAST_NAME} Day {day}：{state['book'].get('title', '')}",
                note,
                ["mira", "marginalia", "reading", f"day-{day}"],
            )
        state["last_run_date"] = today
        state["updated_at"] = now.isoformat()
        save_state(state)
        write_index(state)

    if should_finalize_today(state, now, force=finalize or force):
        title, critique, script = compose_and_finalize_episode(state)
        final_path = series_dir / "episode_script.md"
        review_path = series_dir / "review.md"
        final_path.write_text(script, encoding="utf-8")
        review_path.write_text(critique or "(no critique returned)", encoding="utf-8")
        state["final_title"] = title
        state["final_script_path"] = str(final_path)
        state.pop("final_article_path", None)
        state["review_path"] = str(review_path)
        state["finalized_at"] = datetime.now().isoformat()
        state["status"] = "finalized"
        save_state(state)
        write_index(state)

        deliver_to_bridge(
            state,
            f"{PODCAST_NAME}：{title}",
            script,
            ["mira", "marginalia", "book-podcast", "final"],
        )

    if publish and state.get("finalized_at") and not state.get("podcast_feed_url"):
        final_path = Path(state.get("final_script_path") or state.get("final_article_path", ""))
        if not final_path.exists():
            raise RuntimeError(f"final script missing before podcast publish: {final_path}")
        title = state.get("final_title") or extract_title(final_path.read_text(encoding="utf-8"))
        script = strip_script_metadata(final_path.read_text(encoding="utf-8"))
        try:
            feed_url = generate_and_publish_podcast(state, title, script)
        except Exception as exc:
            state["status"] = "podcast_failed"
            state["podcast_error"] = str(exc)[:500]
            save_state(state)
            write_index(state)
            raise
        if not feed_url:
            state["status"] = "podcast_failed"
            state["podcast_error"] = "RSS publish returned None"
            save_state(state)
            write_index(state)
            raise RuntimeError("marginalia RSS publish returned None")
        state["podcast_feed_url"] = feed_url
        state["podcast_published_at"] = datetime.now().isoformat()
        state["status"] = "complete"
        save_state(state)
        write_index(state)
        log.info("Finalized %s episode: %s", PODCAST_NAME, title)

    return 0


def main(argv: list[str]) -> int:
    return run_once(
        force="--force" in argv,
        finalize="--finalize" in argv,
        publish="--no-publish" not in argv,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
