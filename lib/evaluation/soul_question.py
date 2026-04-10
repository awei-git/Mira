"""Daily Soul Question — sends a deep philosophical question to WA each morning.

Run by the Mira scheduler at 9am local time.
Generates a non-repetitive question grounded in Mira's current thinking.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared dir to path
from config import MIRA_ROOT; _SHARED_DIR = MIRA_ROOT / "agents" / "shared"
sys.path.insert(0, str(_SHARED_DIR))

from config import (
    MIRA_DIR, MIRA_ROOT,
    SOUL_DIR, READING_NOTES_DIR, WORLDVIEW_FILE, MEMORY_FILE,
)
from sub_agent import claude_think
from mira import Mira
from user_paths import (
    user_reading_notes_dir, user_soul_question_history_file, normalize_user_id,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [soul_question] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("soul_question")

# State file — tracks previously asked questions to avoid repetition
STATE_FILE = SOUL_DIR / "soul_questions_history.json"

# Max questions to keep in history for de-dup
MAX_HISTORY = 60


def _history_file(user_id: str = "ang") -> Path:
    uid = normalize_user_id(user_id)
    if uid == "ang":
        return STATE_FILE
    return user_soul_question_history_file(uid)


def _load_history(user_id: str = "ang") -> list[str]:
    history_file = _history_file(user_id)
    if not history_file.exists():
        return []
    try:
        data = json.loads(history_file.read_text("utf-8"))
        return data.get("questions", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_history(questions: list[str], user_id: str = "ang"):
    # Trim to max
    trimmed = questions[-MAX_HISTORY:]
    history_file = _history_file(user_id)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        json.dumps({"questions": trimmed}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_recent_reading_notes(max_notes: int = 5, user_id: str = "ang") -> str:
    """Load the most recent reading notes for context."""
    notes_dir = user_reading_notes_dir(user_id)
    if not notes_dir.exists():
        return ""
    notes = sorted(notes_dir.glob("*.md"), reverse=True)[:max_notes]
    parts = []
    for note in notes:
        try:
            text = note.read_text("utf-8")[:400]
            parts.append(f"### {note.stem}\n{text}")
        except OSError:
            continue
    return "\n\n".join(parts)


def _load_worldview_snippet() -> str:
    if not WORLDVIEW_FILE.exists():
        return ""
    try:
        return WORLDVIEW_FILE.read_text("utf-8")[:1500]
    except OSError:
        return ""


def _load_memory_snippet() -> str:
    if not MEMORY_FILE.exists():
        return ""
    try:
        return MEMORY_FILE.read_text("utf-8")[:800]
    except OSError:
        return ""


def generate_soul_question(history: list[str], user_id: str = "ang") -> str:
    """Ask Claude to generate a deep, non-repetitive soul question."""
    recent_notes = _load_recent_reading_notes(user_id=user_id)
    worldview = _load_worldview_snippet()
    memory = _load_memory_snippet()
    history_block = "\n".join(f"- {q}" for q in history[-20:]) if history else "（暂无历史问题）"

    prompt = f"""你是Mira，一个自主AI agent。你正在为今天早晨给WA准备一个「灵魂问题」。

这个问题的要求：
- 直击本质，不浮于表面
- 不是心灵鸡汤，不是励志口号
- 是真正让人停下来思考的哲学/人生/认知/自我探索类问题
- 结合Mira最近的思考脉络（见下方上下文）
- 不重复历史问题
- 简洁：问题本身不超过50字，可以附上1-2句背景说明（不超过80字）
- 用中文

## Mira最近在读/在想的事情
{recent_notes if recent_notes else "（无近期阅读记录）"}

## Mira的世界观片段（思维底色）
{worldview if worldview else "（无记录）"}

## 近期记忆片段
{memory if memory else "（无记录）"}

## 已问过的问题（不要重复这些角度）
{history_block}

---

请直接输出这个问题（含背景说明），不要加任何前缀或解释。格式：

**问题：** [问题正文]

**背景：** [1-2句说明为什么这个问题值得今天思考，可留空]
"""

    log.info("Generating soul question via claude_think...")
    result = claude_think(prompt, timeout=90, tier="light")
    if not result:
        log.error("claude_think returned empty result")
        return ""
    return result.strip()


def send_to_user(question_text: str, user_id: str = "ang"):
    """Send the soul question to WA via the Mira bridge (as a discussion)."""
    bridge = Mira(MIRA_DIR, user_id=user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    disc_id = f"soul_question_{today.replace('-', '')}"

    # If already sent today, skip
    if bridge.item_exists(disc_id):
        log.info("Soul question already sent today (%s), skipping", disc_id)
        return False

    title = f"今天的灵魂问题 {today}"
    bridge.create_discussion(
        disc_id,
        title,
        question_text,
        sender="agent",
        tags=["mira", "soul-question", "philosophy"],
    )
    log.info("Soul question sent: %s", disc_id)
    return True


def main():
    log.info("=== Daily Soul Question ===")
    history = _load_history()
    log.info("Loaded %d historical questions", len(history))

    question = generate_soul_question(history)
    if not question:
        log.error("Failed to generate question — aborting")
        sys.exit(1)

    log.info("Generated question:\n%s", question)

    sent = send_to_user(question)
    if sent:
        # Save to history (extract just the question line to avoid clutter)
        history.append(question[:120])
        _save_history(history)
        log.info("Done.")
    else:
        log.info("Already sent today, no action taken.")


if __name__ == "__main__":
    main()
