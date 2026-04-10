"""Layer 2: Lesson Extraction.

Daily: distill today's experiences into reusable principles using oMLX.
Principles are saved to soul/lessons/ and searchable via get_recent_lessons().
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from .config import LESSON_DIR
from .experience import load_experiences

log = logging.getLogger("mira.evolution")


def extract_lessons(days: int = 1, user_id: str = "ang") -> str:
    """Distill recent experiences into reusable lessons using oMLX.

    Called from journal workflow (daily). Returns extracted text, also
    saved to soul/lessons/{date}.md.
    """
    experiences = load_experiences(days=days)
    if not experiences:
        log.info("extract_lessons: no experiences to distill")
        return ""

    positive = [e for e in experiences if e.get("score", 0) > 0]
    negative = [e for e in experiences if e.get("score", 0) < 0]

    if not positive and not negative:
        return ""

    lines = []
    if positive:
        lines.append("## 成功经验 (正反馈)")
        for e in sorted(positive, key=lambda x: -x.get("score", 0))[:10]:
            lines.append(f"- {e['action']} -> {e['outcome']} (score: {e.get('score', 0):+.1f})")
            if e.get("context"):
                lines.append(f"  context: {json.dumps(e['context'], ensure_ascii=False)}")
    if negative:
        lines.append("\n## 失败经验 (负反馈)")
        for e in sorted(negative, key=lambda x: x.get("score", 0))[:10]:
            lines.append(f"- {e['action']} -> {e['outcome']} (score: {e.get('score', 0):+.1f})")
            if e.get("context"):
                lines.append(f"  context: {json.dumps(e['context'], ensure_ascii=False)}")

    combined = "\n".join(lines)

    prompt = f"""你是 Mira, 一个正在从自身经验中学习的 AI agent.

以下是你最近的行动和结果. 请提炼出可复用的经验教训.

{combined}

## 要求
- 每条 lesson 必须是可执行的原则, 不是泛泛的总结
- 格式: "当X情况下, 做Y比做Z好, 因为..."
- 同时标注这条 lesson 的适用范围 (什么时候该用, 什么时候不该用)
- 负面经验比正面经验更重要 — 失败教训必须被提炼
- 如果没有值得提炼的教训, 只输出: "无新教训"
- 用中文

## 输出格式
每条一段:
**[适用范围]** 教训内容"""

    try:
        from sub_agent import model_think
        result = model_think(prompt, model_name="omlx", timeout=90)
    except Exception as e:
        log.warning("extract_lessons: LLM call failed: %s", e)
        return ""

    if not result or "无新教训" in result or len(result) < 30:
        log.info("extract_lessons: no lessons worth keeping")
        return ""

    # Save
    LESSON_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    lesson_path = LESSON_DIR / f"{today}.md"

    try:
        content = f"# Lessons — {today}\n\n{result}\n"
        if lesson_path.exists():
            existing = lesson_path.read_text(encoding="utf-8")
            content = existing + f"\n---\n\n{result}\n"
        lesson_path.write_text(content, encoding="utf-8")
        log.info("extract_lessons: saved to %s", lesson_path.name)
    except OSError as e:
        log.warning("Failed to save lessons: %s", e)

    return result


def get_recent_lessons(days: int = 7) -> str:
    """Load recent lessons for injection into prompts."""
    if not LESSON_DIR.exists():
        return ""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    parts = []
    for path in sorted(LESSON_DIR.glob("*.md"), reverse=True):
        if path.stem < cutoff:
            break
        try:
            text = path.read_text(encoding="utf-8")
            parts.append(text[:1000])
        except OSError:
            continue
        if len(parts) >= 3:
            break
    return "\n\n".join(parts) if parts else ""
