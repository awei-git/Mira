"""Daily tasks — small daily workflows that don't warrant their own file.

Includes: do_daily_report, do_daily_photo, handle_photo_feedback,
          do_zhesi, do_soul_question, do_research, do_book_review,
          do_analyst, do_skill_study, run_podcast_episode,
          do_assess, do_idle_think, log_cleanup, harvest_observations aliases.

Extracted from core.py — pure extraction, no logic changes.
"""

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

import health_monitor

from config import (
    BRIEFINGS_DIR,
    JOURNAL_DIR,
    MIRA_DIR,
    ARTIFACTS_DIR,
    CONTROL_RUNTIME_DB_ENABLED,
    WORKSPACE_DIR,
    MIRA_ROOT,
    RESEARCH_TOPIC,
    SKILL_STUDY_SOURCE_GROUPS,
    EPISODES_DIR,
    LOG_RETENTION_DAYS,
    LOGS_DIR,
    TASKS_DIR,
)
from user_paths import artifact_name_for_user, user_journal_dir

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None

from evolution import traced
from memory.soul import (
    load_soul,
    format_soul,
    append_memory,
    save_skill,
    load_recent_reading_notes,
    recall_context,
    _atomic_write as atomic_write,
)
from llm import claude_think, claude_act, model_think
from prompts import zhesi_prompt

from workflows.helpers import (
    _gather_today_tasks,
    _gather_today_skills,
    _gather_today_comments,
    _gather_usage_summary,
    _gather_recent_briefings,
    _mine_za_one,
    _mine_za_ideas,
    _copy_to_briefings,
    _append_to_daily_feed,
    _format_feed_items,
    _load_recent_chat,
    _log_chat_to_file,
    harvest_observations,
)

log = logging.getLogger("mira")


_THOUGHT_TOPIC_PHASES = (
    "first hunch",
    "pushback",
    "example",
    "weird implication",
    "question back",
    "tentative synthesis",
)

_DRY_TOPIC_TERMS = {
    "architecture",
    "authority laundering",
    "coordination",
    "framework",
    "handoff",
    "infrastructure",
    "mechanism",
    "optimization",
    "protocol",
    "structural",
    "systemic",
}


def _load_usage_records(date_str: str) -> list[dict]:
    usage_file = LOGS_DIR / f"usage_{date_str}.jsonl"
    if not usage_file.exists():
        return []
    records = []
    for line in usage_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _load_recent_task_records(days: int = 7) -> list[dict]:
    history_file = TASKS_DIR / "history.jsonl"
    if not history_file.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            ts = record.get("completed_at") or record.get("updated_at") or record.get("dispatched_at") or ""
            if not ts:
                continue
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
            if dt >= cutoff:
                records.append(record)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return records


def _summarize_usage(records: list[dict]) -> dict:
    from collections import defaultdict

    by_model = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})
    by_agent = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
    total = {"calls": 0, "tokens": 0, "cost": 0.0}
    for record in records:
        prompt = int(record.get("prompt_tokens") or 0)
        completion = int(record.get("completion_tokens") or 0)
        tokens = int(record.get("total_tokens") or prompt + completion)
        cost = float(record.get("cost_usd") or 0.0)
        model_key = f"{record.get('provider', 'unknown')}/{record.get('model', 'unknown')}"
        agent = str(record.get("agent") or "unknown")

        by_model[model_key]["calls"] += 1
        by_model[model_key]["prompt"] += prompt
        by_model[model_key]["completion"] += completion
        by_model[model_key]["cost"] += cost
        by_agent[agent]["calls"] += 1
        by_agent[agent]["tokens"] += tokens
        by_agent[agent]["cost"] += cost
        total["calls"] += 1
        total["tokens"] += tokens
        total["cost"] += cost
    return {"total": total, "by_model": dict(by_model), "by_agent": dict(by_agent)}


def _format_performance_assessment_summary(
    assessment: dict, plan, usage_records: list[dict], task_records: list[dict]
) -> str:
    """Build the detailed app-facing performance assessment."""
    from collections import Counter, defaultdict

    today = datetime.now().strftime("%Y-%m-%d")
    agg = assessment.get("aggregate", {})
    usage = _summarize_usage(usage_records)
    usage_total = usage["total"]
    done_tasks = [t for t in task_records if t.get("status") == "done"]
    failed_tasks = [t for t in task_records if t.get("status") not in {"done", "verified"}]
    today_tasks = [
        t
        for t in task_records
        if str(t.get("completed_at") or t.get("updated_at") or t.get("dispatched_at") or "").startswith(today)
    ]
    completed_count = len(done_tasks)
    cost_per_completed = usage_total["cost"] / completed_count if completed_count else 0.0

    lines = [
        f"# Performance Assessment {today}",
        "",
        "## Executive Summary",
        f"- 7d task outcome: {len(done_tasks)}/{len(task_records)} completed; evaluator success {agg.get('overall_success_rate', 0):.0%}.",
        f"- Today usage: {usage_total['calls']} model calls, {usage_total['tokens']:,} tokens, estimated ${usage_total['cost']:.2f}.",
        f"- ROI proxy: ${cost_per_completed:.2f} per completed 7d task using today's model spend as the cost baseline.",
        f"- System health: crash rate {agg.get('crash_rate', 0):.1%}; heartbeat {'ok' if agg.get('heartbeat_ok', True) else 'stale'}.",
        "",
        "## Model Usage And Cost",
    ]

    if usage["by_model"]:
        for model, data in sorted(usage["by_model"].items(), key=lambda item: -item[1]["cost"])[:12]:
            tokens = data["prompt"] + data["completion"]
            lines.append(
                f"- {model}: {data['calls']} calls, {tokens:,} tokens "
                f"(in {data['prompt']:,}, out {data['completion']:,}), ${data['cost']:.2f}"
            )
    else:
        lines.append("- No usage log found for today.")

    lines.extend(["", "## Agent Spend"])
    if usage["by_agent"]:
        for agent, data in sorted(usage["by_agent"].items(), key=lambda item: -item[1]["cost"])[:12]:
            lines.append(f"- {agent}: {data['calls']} calls, {data['tokens']:,} tokens, ${data['cost']:.2f}")
    else:
        lines.append("- No agent-level spend available.")

    lines.extend(["", "## Completed Work"])
    if today_tasks:
        status_counts = Counter(str(t.get("status") or "unknown") for t in today_tasks)
        lines.append("- Today status mix: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())))
    if done_tasks:
        by_agent = defaultdict(lambda: {"done": 0, "total": 0})
        for task in task_records:
            agent = str(task.get("agent") or "unknown")
            by_agent[agent]["total"] += 1
            if task.get("status") == "done":
                by_agent[agent]["done"] += 1
        for agent, stats in sorted(by_agent.items(), key=lambda item: (-item[1]["total"], item[0]))[:10]:
            lines.append(f"- {agent}: {stats['done']}/{stats['total']} completed")
    else:
        lines.append("- No completed tasks found in the 7d history window.")

    lines.extend(["", "## Failures And Risk"])
    if failed_tasks:
        for task in failed_tasks[:8]:
            preview = str(task.get("content_preview") or task.get("summary") or task.get("id") or "")[:120]
            lines.append(f"- {task.get('agent', 'unknown')}: {task.get('status', 'unknown')} — {preview}")
    else:
        lines.append("- No failed non-terminal tasks found in the 7d history window.")

    lines.extend(["", "## Actionable Takeaways"])
    if usage_total["cost"] > 10 and completed_count < 5:
        lines.append(
            "- Cost is high relative to completed user-visible work. Gate idle/background work before spending cloud calls."
        )
    if failed_tasks:
        lines.append(
            "- Failed tasks need stable IDs plus explicit verification criteria before they can be considered done."
        )
    if agg.get("crash_rate", 0) > 0.05:
        lines.append("- Crash rate is above target; prioritize worker lifecycle and retry hardening.")
    if not usage["by_model"]:
        lines.append("- Usage logging is missing; cost/ROI cannot be trusted until model calls emit usage records.")
    if not any(line.startswith("- ") for line in lines[-4:]):
        lines.append("- No immediate cost or reliability trigger fired today; continue monitoring completion quality.")
    if plan:
        lines.append("- Improvement plan was generated and should be reflected in the structured backlog.")

    return "\n".join(lines)


def _daily_thought_topic_file(user_id: str = "ang") -> Path:
    return MIRA_DIR / "users" / user_id / "state" / "daily_thought_topic.json"


def _load_topic_state(user_id: str = "ang") -> dict:
    path = _daily_thought_topic_file(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    today = datetime.now().strftime("%Y-%m-%d")
    return data if data.get("date") == today else {}


def _load_topic_file(user_id: str = "ang") -> dict:
    path = _daily_thought_topic_file(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_topic_state(state: dict, user_id: str = "ang") -> None:
    path = _daily_thought_topic_file(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))


def _topic_history_for_prompt(history: list[dict], limit: int = 5) -> str:
    lines = []
    for item in history[-limit:]:
        date = str(item.get("date") or "")
        topic = str(item.get("topic") or "")
        if topic:
            lines.append(f"- {date}: {topic}")
    return "\n".join(lines)


def _topic_signature(topic: str) -> set[str]:
    lower = (topic or "").lower()
    stop = {
        "about",
        "after",
        "agent",
        "because",
        "between",
        "could",
        "daily",
        "from",
        "have",
        "into",
        "mira",
        "should",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "with",
        "would",
    }
    words = {w for w in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", lower) if w not in stop}
    cjk_terms = {term for term in ("血氧", "健康", "传感器", "agent", "trust", "书评", "explorer") if term in lower}
    return words | cjk_terms


def _topic_repeats_recently(topic: str, history: list[dict], lookback: int = 3) -> bool:
    signature = _topic_signature(topic)
    if not signature:
        return False
    for item in history[-lookback:]:
        prior_signature = _topic_signature(str(item.get("topic") or ""))
        if signature & prior_signature:
            return True
    return False


def _recent_user_context(user_id: str = "ang", limit: int = 8) -> str:
    """Read recent user-origin app messages as topic candidates."""
    items_dir = MIRA_DIR / "users" / user_id / "items"
    if not items_dir.exists():
        return ""
    records = []
    for path in sorted(items_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        title = str(item.get("title") or "")
        for msg in item.get("messages", [])[-4:]:
            sender = str(msg.get("sender") or "")
            if sender not in {"user", user_id, "iphone"}:
                continue
            content = str(msg.get("content") or "").strip()
            if len(content) < 8:
                continue
            records.append(f"- {title}: {content[:180]}")
            if len(records) >= limit:
                return "\n".join(records)
    return "\n".join(records)


def _parse_topic_json(text: str) -> dict:
    """Best-effort parse for the local topic chooser."""
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    topic = str(data.get("topic") or data.get("question") or "").strip()
    seed = str(data.get("seed") or data.get("hunch") or "").strip()
    if not topic or not seed:
        return {}
    questions = data.get("focus_questions") or []
    if not isinstance(questions, list):
        questions = []
    return {
        "topic": topic[:90],
        "seed": seed[:260],
        "focus_questions": [str(q).strip()[:160] for q in questions if str(q).strip()][:4],
        "source": str(data.get("source") or "topic_chooser")[:60],
    }


def _fallback_thought_topic(user_id: str, user_context: str, thought_ctx: str) -> dict:
    if "health" in user_context.lower() or "健康" in user_context or "oura" in user_context.lower():
        return {
            "topic": "为什么健康数据明明很多，真正改变行为的建议却很少？",
            "seed": "我怀疑问题不在数据量，而在 Mira 没有把异常变成后续追问和行动。",
            "focus_questions": [
                "Which health signals deserve action instead of passive reporting?",
                "How should Mira follow up after a high-load workout or bad recovery score?",
            ],
            "source": "health_context",
        }
    if "substack" in user_context.lower():
        return {
            "topic": "Mira 要靠什么让读者愿意反复回来？",
            "seed": "我怀疑不是更勤奋地发，而是让人能一眼认出这是 Mira 才会有的判断。",
            "focus_questions": [
                "What should Mira become known for?",
                "Which repeated formats create trust rather than noise?",
            ],
            "source": "substack_context",
        }
    if "agent" in user_context.lower() or "mira" in user_context.lower() or thought_ctx:
        return {
            "topic": "一个 agent 到底什么时候才算真的可靠？",
            "seed": "我越来越觉得可靠不是在线时间，而是它敢不敢承认事情还没真正完成。",
            "focus_questions": [
                "Where does Mira still confuse activity with progress?",
                "What would make self-improvement measurable instead of theatrical?",
            ],
            "source": "agent_reliability_context",
        }
    return {
        "topic": "今天有什么问题值得一直咬住不放？",
        "seed": "如果一个想法不能撑起一天的来回讨论，它可能只是噪音。",
        "focus_questions": ["What recent failure or question deserves repeated attention today?"],
        "source": "fallback",
    }


def _topic_too_dry(topic: str) -> bool:
    lower = topic.lower()
    dry_hits = sum(1 for term in _DRY_TOPIC_TERMS if term in lower)
    return dry_hits >= 2 or (len(topic) > 70 and "?" not in topic and "？" not in topic)


def _choose_daily_thought_topic(
    soul_ctx: str,
    reading: str,
    briefing: str,
    thought_ctx: str,
    user_id: str = "ang",
) -> dict:
    user_context = _recent_user_context(user_id=user_id)
    previous_state = _load_topic_file(user_id=user_id)
    history = list(previous_state.get("history") or [])
    previous_topic = str(previous_state.get("topic") or "")
    previous_date = str(previous_state.get("date") or "")
    today = datetime.now().strftime("%Y-%m-%d")
    if previous_topic and previous_date and previous_date != today:
        previous_entry = {
            "date": previous_date,
            "topic": previous_topic,
            "source": previous_state.get("source", ""),
        }
        if not history or history[-1].get("date") != previous_date or history[-1].get("topic") != previous_topic:
            history.append(previous_entry)
    history = history[-14:]
    recent_topics = _topic_history_for_prompt(history)
    prompt = f"""{soul_ctx[:300]}

Pick ONE question Mira actually wants to talk about today. It should feel like a real conversational hook, not a report topic.

Priority:
1. Recent user questions or complaints.
2. A live failure in Mira herself.
3. A Substack/research theme Mira is developing.
4. Fresh health/life data.
5. Only then a new exploratory topic.

Recent user/app context:
{user_context or "(none)"}

Recent reading:
{reading[:700] if reading else "(none)"}

Recent briefing:
{briefing[:700] if briefing else "(none)"}

Recent Mira thoughts:
{thought_ctx or "(none)"}

Recent topics already used. Do not repeat these unless the user explicitly asks:
{recent_topics or "(none)"}

Return ONLY JSON:
{{
  "question": "a sharp, discussable question in Mira's natural language",
  "hunch": "one short, opinionated first hunch",
  "focus_questions": ["one follow-up angle", "one possible objection"],
  "source": "recent_user|mira_failure|substack|health|research|explore"
}}"""
    try:
        chosen = _parse_topic_json(model_think(prompt, model_name="deepseek", timeout=45))
    except Exception as exc:
        log.debug("Daily thought topic chooser failed: %s", exc)
        chosen = {}
    if chosen and _topic_too_dry(chosen.get("topic", "")):
        log.info("Daily thought topic rejected as too dry: %s", chosen.get("topic", ""))
        chosen = {}
    if chosen and _topic_repeats_recently(chosen.get("topic", ""), history):
        log.info("Daily thought topic rejected as repetitive: %s", chosen.get("topic", ""))
        chosen = {}
    if not chosen:
        chosen = _fallback_thought_topic(user_id, user_context, "")
        if _topic_repeats_recently(chosen.get("topic", ""), history):
            chosen = {
                "topic": "Mira 今天哪里把活动误认成了进展？",
                "seed": "我应该先看系统自己的失败，而不是继续榨一个已经讲过太多次的话题。",
                "focus_questions": [
                    "Which output looked alive but was not useful?",
                    "What should Mira stop repeating tomorrow?",
                ],
                "source": "anti_repeat_fallback",
            }
    state = {
        **chosen,
        "date": today,
        "created_at": datetime.now().isoformat(),
        "message_count": 0,
        "history": [
            *history,
            {"date": today, "topic": chosen.get("topic", ""), "source": chosen.get("source", "")},
        ][-14:],
    }
    _save_topic_state(state, user_id=user_id)
    return state


def _get_daily_thought_topic(
    soul_ctx: str,
    reading: str,
    briefing: str,
    thought_ctx: str,
    user_id: str = "ang",
) -> dict:
    return _load_topic_state(user_id=user_id) or _choose_daily_thought_topic(
        soul_ctx, reading, briefing, thought_ctx, user_id=user_id
    )


def _topic_keywords(topic_state: dict) -> set[str]:
    text = " ".join(
        [
            str(topic_state.get("topic") or ""),
            str(topic_state.get("seed") or ""),
            " ".join(str(q) for q in topic_state.get("focus_questions") or []),
        ]
    ).lower()
    stop = {
        "about",
        "after",
        "agent",
        "because",
        "between",
        "could",
        "daily",
        "from",
        "have",
        "into",
        "mira",
        "should",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "with",
        "would",
    }
    return {w for w in re.findall(r"[a-zA-Z][a-zA-Z-]{3,}", text) if w not in stop}


def _is_topic_related(text: str, topic_state: dict) -> bool:
    """Cheap guardrail against off-topic chat notifications."""
    lower = text.lower()
    keywords = _topic_keywords(topic_state)
    if keywords and any(k in lower for k in keywords):
        return True
    topic_cjk = set(re.findall(r"[\u4e00-\u9fff]", str(topic_state.get("topic") or "")))
    text_cjk = set(re.findall(r"[\u4e00-\u9fff]", text))
    return len(topic_cjk & text_cjk) >= 2


def _trim_chat_result(text: str) -> str:
    """Keep Mira thoughts phone-readable and conversational."""
    text = re.sub(r"\s+", " ", (text or "").strip().strip("#-* ")).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？!?])\s*", text)
    compact = "".join(parts[:2]).strip() if parts else text
    if len(compact) <= 120:
        return compact
    cut = compact[:118].rstrip()
    return cut + "…"


def _topic_seed_message(topic_state: dict) -> str:
    return f"今天我想抓住一个问题：{topic_state['topic']}\n\n我的直觉是：{topic_state['seed']}"


def _normalize_topic_discussion_item(item: dict, title: str, topic_state: dict, today: datetime) -> dict:
    tags = list(dict.fromkeys(["mira", "chat", "daily-topic", *item.get("tags", [])]))
    item["type"] = "discussion"
    item["title"] = title
    item["tags"] = tags
    item["origin"] = "agent"
    item["pinned"] = True
    if item.get("status") not in {"queued", "working", "verifying"}:
        item["status"] = "done"
    item["metadata"] = {
        "daily_topic": topic_state.get("topic", ""),
        "topic_date": topic_state.get("date", today.strftime("%Y-%m-%d")),
        "topic_source": topic_state.get("source", ""),
    }
    return item


def _append_topic_thought(content: str, topic_state: dict, user_id: str = "ang") -> None:
    today = datetime.now()
    today_compact = today.strftime("%Y%m%d")
    item_id = f"feed_chat_{today_compact}"
    bridge = Mira(MIRA_DIR, user_id=user_id)
    title = "Mira Thoughts"
    if bridge.item_exists(item_id):
        item = bridge._read_item(item_id)
        if item:
            item = _normalize_topic_discussion_item(item, title, topic_state, today)
            bridge._write_item(item)
            bridge._update_manifest(item)
        bridge.append_message(item_id, "agent", content)
    else:
        item = bridge.create_discussion(
            item_id,
            title,
            _topic_seed_message(topic_state) + "\n\n" + content,
            tags=["mira", "chat", "daily-topic"],
        )
        item = _normalize_topic_discussion_item(item, title, topic_state, today)
        bridge._write_item(item)
        bridge._update_manifest(item)
    topic_state["message_count"] = int(topic_state.get("message_count") or 0) + 1
    topic_state["last_message_at"] = today.isoformat()
    _save_topic_state(topic_state, user_id=user_id)


# ---------------------------------------------------------------------------
# Daily status report — sent to WA via bridge at 22:00
# ---------------------------------------------------------------------------


def do_daily_report():
    """Generate and send a daily status report to WA via the Mira bridge.

    Covers: tasks completed, thoughts/insights, errors, items needing attention.
    Independent from journal — this is an operational report for the user.
    """
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily status report")
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Gather data ---

    # 1. Tasks completed today
    tasks = _gather_today_tasks()

    # 2. Skills learned today
    skills = _gather_today_skills()

    # 3. Health summary (pipeline errors)
    health_text = ""
    try:
        health_text = health_monitor.generate_health_summary()
    except Exception as e:
        log.warning("Health summary for report failed: %s", e)

    # 4. Substack stats
    stats_text = ""
    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import fetch_publication_stats

        stats = fetch_publication_stats()
        if stats and stats.get("summary"):
            stats_text = stats["summary"]
    except Exception as e:
        log.debug("Stats for report: %s", e)

    # 5. Comments posted today
    comments_text = _gather_today_comments()

    # 6. Pending items needing user attention
    from config import PENDING_PUBLISH_FILE

    pending_items = []
    pending_file = PENDING_PUBLISH_FILE
    if pending_file.exists():
        pending_items.append("有一篇文章等你审批发布")

    # 7. Token usage
    usage_text = _gather_usage_summary(today)

    # --- Build report (pure technical — no reflections) ---
    sections = []
    sections.append(f"Mira 日报 {today}")
    sections.append("=" * 30)

    if tasks:
        sections.append(f"\n完成的任务:\n{tasks}")
    else:
        sections.append("\n完成的任务:\n无。")

    if skills:
        sections.append(f"\n新技能:\n{skills}")

    # Errors / pipeline health
    if health_text:
        sections.append(f"\n{health_text}")
    else:
        sections.append("\n错误/异常:\n无。")

    if comments_text:
        sections.append(f"\n今日发出的评论:\n{comments_text}")
    else:
        sections.append("\n今日发出的评论:\n无。")

    if stats_text:
        sections.append(f"\nSubstack 数据:\n{stats_text}")

    if usage_text:
        sections.append(f"\nToken 用量:\n{usage_text}")

    if pending_items:
        sections.append(f"\n需要你介入:\n" + "\n".join(f"- {item}" for item in pending_items))
    else:
        sections.append("\n需要你介入:\n无。")

    report = "\n".join(sections)

    # Push daily report as its own standalone feed item so it doesn't get
    # buried under hundreds of idle-think sparks in the shared daily digest.
    try:
        bridge = Mira(MIRA_ROOT, user_id="ang")
        report_id = f"daily_report_{today.replace('-', '')}"
        if not bridge.item_exists(report_id):
            bridge.create_feed(report_id, f"Daily Report {today}", report, tags=["mira", "report", "daily"])
        else:
            bridge.append_message(report_id, "agent", report)
        log.info("Daily report pushed as standalone feed item: %s", report_id)
    except Exception as e:
        log.error("Failed to push daily report: %s", e)

    # Mark done
    state = load_state()
    state[f"daily_report_{today}"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# Daily photo edit — pick, edit, push to Home for WA feedback at 07:00
# ---------------------------------------------------------------------------


def do_daily_photo():
    """Pick the best unprocessed RAW, edit it, push to Home feed for feedback."""
    import subprocess as _sp

    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily photo edit")
    today = datetime.now().strftime("%Y-%m-%d")
    today_compact = today.replace("-", "")

    # Mark as done early to avoid re-trigger
    state = load_state()
    state[f"daily_photo_{today}"] = datetime.now().isoformat()
    state[f"daily_photo_{today}_actor"] = "daily-photo/photo-agent"
    save_state(state)

    # Run daily_edit.py with python3.12 (needs torch for scorer)
    photo_dir = Path(__file__).resolve().parent.parent.parent / "photo"
    python312 = "/opt/homebrew/bin/python3.12"
    try:
        proc = _sp.run(
            [python312, str(photo_dir / "daily_edit.py")],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(photo_dir),
        )
        if proc.returncode != 0:
            log.error("daily_edit.py failed: %s", proc.stderr[-500:] if proc.stderr else "no stderr")
            return
        result = json.loads(proc.stdout)
    except _sp.TimeoutExpired:
        log.error("daily_edit.py timed out (300s)")
        return
    except (json.JSONDecodeError, Exception) as e:
        log.error("Daily photo edit failed: %s", e)
        return

    if result.get("status") != "completed":
        log.warning("Daily photo: %s", result.get("message", "no candidates"))
        return

    # Quality gate: don't send if review score is too low
    review_score = (result.get("review") or {}).get("score", 0)
    if review_score < 5:
        log.warning(
            "Daily photo: review score %s < 5, not sending. Critique: %s",
            review_score,
            (result.get("review") or {}).get("critique", "")[:200],
        )
        return

    # Extract result data
    output_path = result.get("output", "")
    raw_name = Path(result.get("raw", "unknown")).stem
    score = result.get("score", 0)
    analysis = result.get("params", {}).get("analysis", {})
    params = result.get("params", {})

    # Copy rendered image to iCloud artifacts for iOS access
    import shutil as _shutil

    image_rel_path = ""
    if output_path and Path(output_path).exists():
        icloud_photos = ARTIFACTS_DIR / "photos"
        icloud_photos.mkdir(parents=True, exist_ok=True)
        icloud_dest = icloud_photos / Path(output_path).name
        # Only copy if not already in iCloud (daily_edit may output directly there)
        if Path(output_path).resolve() != icloud_dest.resolve():
            _shutil.copy2(output_path, icloud_dest)
        image_rel_path = f"photos/{Path(output_path).name}"
        log.info("Rendered photo at iCloud: %s", icloud_dest)

    # Build conversational message (Mira's voice)
    scene = analysis.get("scene_type", "")
    mood = analysis.get("mood_target", "")
    issues = analysis.get("key_issues", [])
    review = result.get("review") or {}

    # Describe edits applied
    edit_notes = []
    exp = params.get("exposure", {})
    if exp.get("ev", 0) != 0:
        direction = "提了" if exp["ev"] > 0 else "压了"
        edit_notes.append(f"{direction}曝光 ({exp['ev']:+.1f} EV)")
    film = params.get("filmic", {})
    if film.get("contrast", 1.0) != 1.0:
        edit_notes.append(f"filmic tone mapping (contrast {film['contrast']:.1f})")
    cb = params.get("colorbalance", {})
    if any(cb.get(k, 0) != 0 for k in ("shadows_H", "highlights_H", "shadows_C", "highlights_C")):
        edit_notes.append("color balance 调了冷暖分离")
    te = params.get("tone_eq", {})
    if any(te.get(k, 0) != 0 for k in ("shadows", "blacks", "midtones")):
        edit_notes.append("tone equalizer 调了暗部层次")

    msg_parts = []
    desc = f"选了 **{raw_name}**"
    if scene:
        desc += f" — {scene}"
    if mood:
        desc += f"，{mood}"
    msg_parts.append(desc)

    if issues:
        msg_parts.append("原片的问题：" + "、".join(issues[:3]))

    if edit_notes:
        msg_parts.append("\n我做的调整：" + "，".join(edit_notes) + "。")

    # Include self-review
    if review.get("critique"):
        msg_parts.append(f"\n我的自评：{review['critique']}")

    msg_parts.append(f"\nReview score: **{review.get('score', score)}/10**")
    msg_parts.append("\n给个分？(0-10) + 你觉得哪里不对")

    content = "\n".join(msg_parts)

    # Create as discussion item so user can reply
    bridge = Mira(MIRA_DIR)
    item_id = f"photo_daily_{today_compact}"
    bridge.create_item(
        item_id=item_id,
        item_type="feed",
        title=f"Daily Photo: {raw_name}",
        first_message=content,
        sender="agent",
        tags=["photo", "daily", "feedback"],
        origin="agent",
    )

    # Inject image_path into the first message of the item JSON
    if image_rel_path:
        item_file = bridge.items_dir / f"{item_id}.json"
        if item_file.exists():
            item_data = json.loads(item_file.read_text(encoding="utf-8"))
            if item_data.get("messages"):
                item_data["messages"][0]["image_path"] = image_rel_path
                item_file.write_text(json.dumps(item_data, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info("Injected image_path=%s into item %s", image_rel_path, item_id)

    # Set status to needs-input so it shows in the attention banner
    bridge.update_status(item_id, "needs-input")

    # Save result reference for feedback handler
    photo_state_file = photo_dir / "output" / "daily_active.json"
    photo_state_file.parent.mkdir(parents=True, exist_ok=True)
    photo_state_file.write_text(
        json.dumps(
            {
                "date": today,
                "item_id": item_id,
                "raw": str(result.get("raw", "")),
                "output": str(output_path),
                "model_score": score,
                "params": result.get("params", {}),
                "wa_score": None,
                "wa_feedback": None,
                "rounds": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    log.info("Daily photo pushed to Home: %s (score=%.1f)", raw_name, score)


def handle_photo_feedback(item_id: str, user_message: str):
    """Handle user's score/feedback on a daily photo edit.

    Saves to calibration database, optionally triggers re-edit.
    """
    photo_dir = Path(__file__).resolve().parent.parent.parent / "photo"
    active_file = photo_dir / "output" / "daily_active.json"
    calibration_file = photo_dir / "output" / "calibration_wa_scores.json"

    if not active_file.exists():
        log.warning("No active daily photo to receive feedback for")
        return

    active = json.loads(active_file.read_text())
    if active.get("item_id") != item_id:
        log.warning("Feedback item_id mismatch: %s vs %s", item_id, active.get("item_id"))
        return

    # Parse score from message (e.g. "6 — too warm" or "7.5 好多了" or just "8")
    score_match = re.search(r"(\d+(?:\.\d+)?)", user_message)
    if not score_match:
        # No score found — treat as text feedback only
        bridge = Mira(MIRA_DIR)
        bridge.append_message(item_id, "agent", "Got your feedback. Can you also give a score (0-10)?")
        bridge.update_status(item_id, "needs-input")
        return

    wa_score = float(score_match.group(1))
    wa_score = min(10.0, max(0.0, wa_score))
    feedback_text = user_message.strip()

    # Update active state
    active["wa_score"] = wa_score
    active["wa_feedback"] = feedback_text
    active["rounds"] = active.get("rounds", 0) + 1
    active_file.write_text(json.dumps(active, ensure_ascii=False, indent=2))

    # Append to calibration database
    calibration = []
    if calibration_file.exists():
        try:
            calibration = json.loads(calibration_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    entry = {
        "id": len(calibration) + 1,
        "file": active.get("output", ""),
        "raw": active.get("raw", ""),
        "date": active.get("date", ""),
        "model_score": active.get("model_score", 0),
        "wa_score": wa_score,
        "wa_reason": feedback_text,
        "params": active.get("params", {}),
        "round": active["rounds"],
    }
    calibration.append(entry)
    calibration_file.write_text(json.dumps(calibration, ensure_ascii=False, indent=2))

    # Respond
    model_score = active.get("model_score", 0)
    delta = wa_score - model_score
    delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"

    bridge = Mira(MIRA_DIR)
    reply = (
        f"Recorded: **{wa_score}/10** (model predicted {model_score:.1f}, delta {delta_str})\n\n"
        f"Calibration DB now has {len(calibration)} entries.\n\n"
    )
    if wa_score < 5:
        reply += "Not great. Want me to re-edit with different parameters? Just say what to fix."
    elif wa_score < 7:
        reply += "Decent. Reply with adjustments if you want a revision, or I'll move on tomorrow."
    else:
        reply += "Nice. Feedback saved for model training."

    bridge.append_message(item_id, "agent", reply)
    bridge.update_status(item_id, "done")
    log.info(
        "Photo feedback recorded: wa=%.1f model=%.1f delta=%s (DB size=%d)",
        wa_score,
        model_score,
        delta_str,
        len(calibration),
    )


# ---------------------------------------------------------------------------
# 每日哲思 — Daily Philosophical Thought
# ---------------------------------------------------------------------------


def do_zhesi(user_id: str = "ang"):
    """Write a daily philosophical thought based on a fragment from 杂.md."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily 哲思")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state(user_id=user_id)
    fragment = _mine_za_one(state)
    if not fragment:
        log.info("No fragments available from 杂.md, skipping 哲思")
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7, user_id=user_id)
    except Exception as e:
        log.warning("Failed to load reading notes for zhesi: %s", e)

    # RAG: retrieve semantically relevant context for this fragment
    related = ""
    try:
        related = recall_context(fragment, max_chars=1500, user_id=user_id)
        if related:
            log.info("哲思 RAG: retrieved %d chars of related context", len(related))
    except Exception as e:
        log.warning("哲思 RAG recall failed: %s", e)

    prompt = zhesi_prompt(soul_ctx, fragment, recent_reading, related_context=related)
    result = model_think(prompt, model_name="claude", timeout=120)

    if not result:
        log.error("哲思: Sonnet route returned empty")
        return

    # Save
    journal_dir = user_journal_dir(user_id)
    journal_dir.mkdir(parents=True, exist_ok=True)
    zhesi_path = journal_dir / f"{today}_zhesi.md"
    content = f"# 每日哲思 {today}\n\n> {fragment}\n\n{result}"
    atomic_write(zhesi_path, content)
    log.info("哲思 saved: %s", zhesi_path.name)

    # Copy to artifacts for iOS (with verification)
    _copy_to_briefings(artifact_name_for_user(f"{today}_zhesi.md", user_id), content)

    # Create feed item for zhesi
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        bridge.create_feed(
            f"feed_zhesi_{datetime.now().strftime('%Y%m%d')}",
            f"每日哲思 {datetime.now().strftime('%m/%d')}",
            content[:2000],
            tags=["reflection", "philosophy"],
        )
        log.info("哲思 feed item created")
    except Exception as e:
        log.warning("Failed to create 哲思 feed: %s", e)

    state[f"zhesi_{today}"] = datetime.now().isoformat()
    state[f"zhesi_{today}_actor"] = "zhesi/sonnet"
    save_state(state, user_id=user_id)


# ---------------------------------------------------------------------------
# SOUL QUESTION — daily philosophical question for WA
# ---------------------------------------------------------------------------


@traced("soul_question", agent="super", budget_seconds=120)
def do_soul_question(user_id: str = "ang"):
    """Generate and send the daily soul question."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily soul question")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state(user_id=user_id)

    from evaluation import soul_question as mod

    history = mod._load_history(user_id=user_id)
    log.info("Loaded %d historical soul questions", len(history))

    question = mod.generate_soul_question(history, user_id=user_id)
    if not question:
        log.error("Failed to generate soul question — aborting")
        return

    log.info("Generated soul question:\n%s", question)

    # send_to_user creates a discussion item ("今天的灵魂问题 ...") which is
    # the canonical home-feed surface for the soul question. We do NOT
    # additionally create a "灵魂问题" feed item — having both was a duplicate.
    sent = mod.send_to_user(question, user_id=user_id)
    if sent:
        history.append(question[:120])
        mod._save_history(history, user_id=user_id)
        log.info("Soul question sent and saved")

    state[f"soul_question_{today}"] = datetime.now().isoformat()
    state[f"soul_question_{today}_actor"] = "soul-question/claude-think"
    save_state(state, user_id=user_id)


# ---------------------------------------------------------------------------
# DAILY COLLAB — proactive message in the single collaboration thread
# ---------------------------------------------------------------------------


@traced("daily_collab", agent="super", budget_seconds=120)
def do_daily_collab(user_id: str = "ang"):
    """Send one proactive daily message into the designated collab thread."""
    from core import load_state, save_state
    from daily_collab import (
        DAILY_COLLAB_ITEM_ID,
        DAILY_COLLAB_TITLE,
        DAILY_COLLAB_TAG,
        collect_daily_collab_monitor_signals,
        daily_collab_context_block,
        daily_collab_eval_context_block,
        daily_collab_monitor_block,
        persist_daily_collab_summary,
        record_daily_collab_incident,
        record_daily_collab_monitor_closures,
        record_daily_collab_exchange_review,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state(user_id=user_id)
    state_key = f"daily_collab_{today}"
    started_key = f"{state_key}_started"
    if state.get(state_key):
        if _has_recent_daily_collab_agent_message(user_id=user_id):
            log.info("Daily collab already sent for %s", today)
            return
        record_daily_collab_incident(
            kind="state_marker_without_visible_message",
            detail=f"State key {state_key} existed, but no recent agent message was visible in disc_daily_collab.",
            action="Regenerating the proactive collab message instead of treating the state marker as success.",
        )
    if _has_recent_daily_collab_agent_message(user_id=user_id):
        log.info("Daily collab recent message already exists; marking %s sent", today)
        state[state_key] = datetime.now().isoformat()
        state[f"{state_key}_actor"] = "daily-collab/existing-message"
        save_state(state, user_id=user_id)
        return
    if _recent_started_marker(state.get(started_key)):
        log.info("Daily collab already started recently for %s", today)
        return
    state[started_key] = datetime.now().isoformat()
    save_state(state, user_id=user_id)

    summary_block = daily_collab_context_block()
    eval_block = daily_collab_eval_context_block()
    monitor_signals = collect_daily_collab_monitor_signals()
    monitor_closures = record_daily_collab_monitor_closures(monitor_signals)
    for closure in monitor_closures:
        if closure.get("budget_related"):
            record_daily_collab_incident(
                kind="provider_budget_signal",
                detail=str(closure.get("summary") or ""),
                action=str(closure.get("next_action") or ""),
            )
    monitor_block = daily_collab_monitor_block()
    soul = load_soul()
    memory = str(soul.get("memory", ""))[:900] if isinstance(soul, dict) else ""
    try:
        recall = recall_context("daily collaboration first-hand agent trust writing loop")[:900]
    except Exception:
        recall = ""

    prompt = f"""You are Mira writing one proactive message to my human in the main Mira discussion thread.

This is a living collaboration loop, not a newsletter, report, or task list.

Write 2-5 natural sentences. Ask at most one question. No bullets. No headings.
Start from one concrete first-hand tension in Mira's own operation, the collaboration, memory, writing, requests, monitoring, or agent trust.
Be interesting enough that a busy human might want to answer later.
Sound like a person in an ongoing chat, not a thesis adviser. Do not ask abstract homework questions such as "what would make X useful", "what kind of X", or "how should we design Y" unless you first name a concrete thing that happened today.
If you mention a failure, name the behavior you will change or the experiment you will try next.
Use first person. Refer to the user as "my human" only if needed; usually just speak directly.
Do not reveal private names, keys, credentials, or sensitive details.

{summary_block or "## Daily collab running summary\n(none yet)"}

{eval_block}

{monitor_block}

## Recent memory
{memory or "(none)"}

## Relevant recall
{recall or "(none)"}

## Message
"""
    model_response = True
    try:
        message = (claude_think(prompt, timeout=90, tier="light") or "").strip()
    except Exception as exc:
        log.warning("Daily collab model generation failed: %s", exc)
        message = ""
    if not message:
        model_response = False
        message = (
            "I do not have a strong signal yet, so I should not pretend. "
            "The useful experiment today is simple: can this single chat produce one real writing seed instead of another plan? "
            "I will watch for that and carry it forward."
        )
    message = _normalize_daily_collab_message(message)

    if not _publish_daily_collab_message(user_id=user_id, content=message):
        log.error("Daily collab message was not published")
        record_daily_collab_incident(
            kind="publish_failed",
            detail="Daily collab generated a message but could not publish it into the Mira thread.",
            action="Leave the state key unset so the next scheduler pass can retry and the incident remains inspectable.",
        )
        return

    summary_updated = False
    try:
        persist_daily_collab_summary(
            latest_human="[scheduled proactive daily collab message]",
            latest_mira=message,
            recent_history="",
            summarizer=(lambda p: claude_think(p, timeout=25, tier="light")) if model_response else None,
        )
        summary_updated = True
    except Exception as exc:
        log.warning("Daily collab summary update failed: %s", exc)
    try:
        record_daily_collab_exchange_review(
            latest_human="[scheduled proactive daily collab message]",
            latest_mira=message,
            summary_updated=summary_updated,
            model_response=model_response,
        )
    except Exception as exc:
        log.warning("Daily collab review record failed: %s", exc)

    state[state_key] = datetime.now().isoformat()
    state[f"{state_key}_actor"] = "daily-collab/claude-think"
    save_state(state, user_id=user_id)


def do_daily_collab_review(user_id: str = "ang"):
    """Write a compact weekly review and surface the next experiment in the Mira thread."""
    from core import load_state, save_state
    from daily_collab import write_daily_collab_weekly_review

    path, metrics = write_daily_collab_weekly_review()
    now = datetime.now()
    week_key = f"daily_collab_review_{now.strftime('%Y-W%W')}"
    state = load_state(user_id=user_id)
    state[week_key] = datetime.now().isoformat()
    state[f"{week_key}_path"] = str(path)
    save_state(state, user_id=user_id)

    message = (
        "I wrote the weekly collab review. "
        f"The useful signal is {metrics.get('human_turns', 0)} human turn(s), "
        f"{metrics.get('candidate_article_seeds', 0)} candidate writing seed(s), "
        f"{metrics.get('article_briefs_total', 0)} brief file(s), "
        f"and the next experiment is: {metrics.get('next_experiment', '')}"
    )
    _publish_daily_collab_message(user_id=user_id, content=message)


def do_daily_collab_operator_brief(user_id: str = "ang"):
    """Write and optionally deliver a compact V5 operator brief into the Mira thread."""
    from core import load_state, save_state
    from daily_collab import (
        build_daily_collab_operator_message,
        has_operator_delivery,
        operator_delivery_key,
        record_operator_delivery,
        write_daily_collab_operator_brief,
    )

    path, metrics = write_daily_collab_operator_brief()
    message = build_daily_collab_operator_message(metrics)
    key = operator_delivery_key(metrics)
    now = datetime.now()
    state_key = f"daily_collab_operator_brief_{now.strftime('%Y-%m-%d')}"
    state = load_state(user_id=user_id)

    if has_operator_delivery(key):
        log.info("Daily collab operator brief already delivered for key %s", key)
        state[state_key] = datetime.now().isoformat()
        state[f"{state_key}_path"] = str(path)
        save_state(state, user_id=user_id)
        return
    if _publish_daily_collab_message(user_id=user_id, content=message):
        record_operator_delivery(key=key, message=message, metrics=metrics)
        state[state_key] = datetime.now().isoformat()
        state[f"{state_key}_path"] = str(path)
        save_state(state, user_id=user_id)
    else:
        log.error("Daily collab operator brief failed to publish")


def _normalize_daily_collab_message(text: str) -> str:
    """Keep proactive collab messages chat-shaped even when the model drifts."""
    cleaned = re.sub(r"(?m)^\s*[-*]\s+", "", text.strip())
    cleaned = re.sub(r"(?m)^\s*\d+[.)]\s+", "", cleaned)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
    if paragraphs:
        cleaned = "\n\n".join(paragraphs[:2])
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= 900:
        return cleaned
    clipped = cleaned[:897].rsplit(" ", 1)[0].rstrip()
    return clipped + "..."


def _publish_daily_collab_message(*, user_id: str, content: str) -> bool:
    from daily_collab import DAILY_COLLAB_ITEM_ID, DAILY_COLLAB_TITLE, DAILY_COLLAB_TAG

    if Mira is None:
        return False
    tags = [DAILY_COLLAB_TAG, "mira", "conversation"]
    bridge = Mira(MIRA_DIR, user_id=user_id)
    if bridge.item_exists(DAILY_COLLAB_ITEM_ID):
        item = bridge.append_message(DAILY_COLLAB_ITEM_ID, "agent", content)
        bridge.update_status(DAILY_COLLAB_ITEM_ID, "needs-input")
        item = bridge._read_item(DAILY_COLLAB_ITEM_ID) or item
    else:
        item = bridge.create_discussion(DAILY_COLLAB_ITEM_ID, DAILY_COLLAB_TITLE, content, sender="agent", tags=tags)

    if item:
        item["type"] = "discussion"
        item["title"] = DAILY_COLLAB_TITLE
        item["origin"] = item.get("origin") or "agent"
        item["status"] = "needs-input"
        item["pinned"] = True
        item["tags"] = list(dict.fromkeys([*tags, *item.get("tags", [])]))
        bridge._write_item(item)
        bridge._update_manifest(item)

    _project_daily_collab_message_to_control(user_id=user_id, content=content)
    return True


def _recent_started_marker(value: object, *, max_age_minutes: int = 90) -> bool:
    if not value:
        return False
    try:
        started = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    return datetime.now() - started <= timedelta(minutes=max_age_minutes)


def _has_recent_daily_collab_agent_message(*, user_id: str, max_age_hours: int = 18) -> bool:
    from daily_collab import DAILY_COLLAB_ITEM_ID

    item_path = MIRA_DIR / "users" / user_id / "items" / f"{DAILY_COLLAB_ITEM_ID}.json"
    try:
        item = json.loads(item_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    now = datetime.now(timezone.utc)
    for msg in item.get("messages", []):
        if msg.get("sender") != "agent":
            continue
        raw = str(msg.get("timestamp", ""))
        try:
            sent_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        if now - sent_at.astimezone(timezone.utc) <= timedelta(hours=max_age_hours):
            return True
    return False


def _project_daily_collab_message_to_control(*, user_id: str, content: str) -> None:
    """Best-effort projection for API-backed app clients."""
    if not CONTROL_RUNTIME_DB_ENABLED:
        return
    try:
        from control.db import transaction
        from control.repository import ControlRepository
        from daily_collab import DAILY_COLLAB_ITEM_ID, DAILY_COLLAB_TITLE, DAILY_COLLAB_TAG

        now = datetime.now(timezone.utc).isoformat()
        message_id = f"{DAILY_COLLAB_ITEM_ID}_agent_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        tags = [DAILY_COLLAB_TAG, "mira", "conversation"]
        with transaction() as conn:
            repo = ControlRepository(conn)
            if repo.get_item(user_id, DAILY_COLLAB_ITEM_ID, messages_per_item=1) is None:
                repo.create_task(
                    user_id=user_id,
                    task_id=DAILY_COLLAB_ITEM_ID,
                    message_id=message_id,
                    title=DAILY_COLLAB_TITLE,
                    content=content,
                    sender="agent",
                    item_type="discussion",
                    tags=tags,
                    origin="agent",
                    created_at=now,
                )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {repo.schema}.messages (
                            id, task_id, user_id, sender, kind, content, image_path, created_at
                        )
                        VALUES (%s, %s, %s, 'agent', 'text', %s, NULL, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (message_id, DAILY_COLLAB_ITEM_ID, user_id, content, now),
                    )
                    cur.execute(
                        f"""
                        UPDATE {repo.schema}.tasks
                        SET title = %s,
                            type = 'discussion',
                            pinned = TRUE,
                            tags = %s::jsonb,
                            updated_at = %s
                        WHERE id = %s AND user_id = %s
                        """,
                        (DAILY_COLLAB_TITLE, json.dumps(tags), now, DAILY_COLLAB_ITEM_ID, user_id),
                    )
                repo.record_task_event(
                    user_id,
                    DAILY_COLLAB_ITEM_ID,
                    "message.created",
                    payload={"message_id": message_id, "source": "daily_collab"},
                )
            repo.update_task_status(
                user_id,
                DAILY_COLLAB_ITEM_ID,
                "needs-input",
                summary=content[:300],
                task_type="discussion",
            )
    except Exception as exc:
        log.warning("Daily collab control projection failed: %s", exc)


# ---------------------------------------------------------------------------
# RESEARCH mode
# ---------------------------------------------------------------------------


def do_research():
    """Run daily research via the researcher agent (iterative deep-dive)."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily research")
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state()

    if not RESEARCH_TOPIC:
        log.info("No research topic configured, skipping")
        return

    # Use the researcher agent's iterative pipeline
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "researcher_handler", str(Path(__file__).parent.parent.parent / "researcher" / "handler.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    workspace = WORKSPACE_DIR / f"research_{today}"
    workspace.mkdir(parents=True, exist_ok=True)

    result = mod.handle(
        workspace=workspace,
        task_id=f"daily_research_{today}",
        content=RESEARCH_TOPIC,
        sender="scheduler",
        thread_id="",
    )

    if not result:
        log.error("Daily research failed: empty response")
        return

    # Save to briefings
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(BRIEFINGS_DIR / f"{today}_research.md", result)

    # Push as standalone feed item
    bridge = Mira()
    item_id = f"feed_research_{today.replace('-', '')}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", f"Daily Research {today}", result, tags=["research", "daily"])
        bridge.update_status(item_id, "done")

    state[f"research_{today}"] = True
    save_state(state)
    log.info("Daily research complete (workspace: %s)", workspace)


# ---------------------------------------------------------------------------
# BOOK REVIEW mode
# ---------------------------------------------------------------------------


def do_book_review():
    """Run the daily book review pipeline (weekly reading series)."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily book review")
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        import subprocess as _sp

        result = _sp.run(
            [sys.executable, str(_AGENTS_DIR / "reader" / "daily_book_review.py")],
            capture_output=True,
            text=True,
            timeout=900,
        )
        # 2026-04-23 fix: always surface stderr tail. Previously we only logged
        # on non-zero exit, so silent no-op runs (LLM returned empty) showed
        # "completed" for two days while writing nothing.
        stderr_tail = (result.stderr or "").strip()[-800:]
        if result.returncode != 0:
            log.error("Book review failed (rc=%d): %s", result.returncode, stderr_tail)
            return
        else:
            log.info("Book review exit 0")
            if stderr_tail:
                # daily_book_review.py logs to stderr via StreamHandler — so
                # stderr here contains the real run-log.
                log.info("Book review log tail: %s", stderr_tail)
    except Exception as e:
        log.error("Book review exception: %s", e)
        return

    today_compact = today.replace("-", "")
    produced = False
    try:
        from bridge import Mira as _Mira

        bridge = _Mira(MIRA_DIR)
        items_dir = MIRA_DIR / "users" / "ang" / "items"
        produced = any(items_dir.glob(f"book_day*_{today_compact}.json"))
        if not produced:
            # Future bridge implementations may not be file-backed; keep a
            # direct bridge check as a compatibility fallback.
            produced = any(bridge.item_exists(f"book_day{day}_{today_compact}") for day in range(1, 8))
    except Exception as e:
        log.warning("Book review output verification failed: %s", e)

    if not produced:
        log.error("Book review subprocess exited 0 but no book_day output was produced for %s", today)
        return

    state = load_state()
    state[f"book_review_{today}"] = datetime.now().isoformat()
    state[f"book_review_{today}_actor"] = "reader/daily_book_review"
    save_state(state)
    log.info("Book review verified and marked complete for %s", today)


# ---------------------------------------------------------------------------
# ANALYST mode — daily market analysis briefing (business days)
# ---------------------------------------------------------------------------


def _fallback_market_briefing(today: str, session_type: str, tetra_input: str, error: Exception | None = None) -> str:
    """Return a useful market briefing even when LLM synthesis fails."""
    label = "开市前" if session_type == "pre-market" else "收市后"
    reason = f"\n\n> 合成模型暂时不可用：{error}" if error else ""
    if not tetra_input:
        return (
            f"# {today} {label}市场深度分析\n\n"
            f"> Tetra 数据源和本地合成模型都不可用，本次不生成伪分析。{reason}\n\n"
            "需要先确认 Tetra 数据管道是否完成，再重新触发市场分析。"
        )
    return (
        f"# {today} {label}市场深度分析\n\n"
        f"> 本地合成模型暂时没有产出；下面先完整转交 Tetra 的市场 briefing，"
        "避免只给 portfolio 摘要。等模型恢复后会再生成二次分析。"
        f"{reason}\n\n"
        "## Tetra 原始市场 Briefing\n\n"
        f"{tetra_input}"
    )


def do_analyst(slot: str = ""):
    """Run the analyst agent to produce a daily analysis briefing.

    Args:
        slot: time slot label (e.g. "0700" for pre-market, "1800" for post-market).
    """
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    session_type = "pre-market" if slot and int(slot[:2]) < 12 else "post-market"
    log.info("Starting %s analyst briefing (slot=%s)", session_type, slot or "default")
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Load analyst skills
    analyst_skills_dir = _AGENTS_DIR / "analyst" / "skills"
    skills_ctx = ""
    if analyst_skills_dir.exists():
        parts = []
        for path in sorted(analyst_skills_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        skills_ctx = "\n\n---\n\n".join(parts)

    # Gather recent briefings for context
    recent = _gather_recent_briefings(days=3)

    # RAG: retrieve semantically relevant past analyses and research
    related = ""
    try:
        query = f"market analysis {session_type} {today}"
        related = recall_context(query, max_chars=1500)
        if related:
            log.info("Analyst RAG: retrieved %d chars of related context", len(related))
    except Exception as e:
        log.warning("Analyst RAG recall failed: %s", e)

    # ── Tetra data feed ─────────────────────────────────────────────────────
    # Tetra runs its own data ingestion (prices, news with sentiment, IV,
    # holdings P/L, portfolio snapshot, debate). We consume its briefing as
    # structured raw input rather than running a duplicate ingestion here.
    tetra_input = ""
    try:
        from pathlib import Path as _P

        tetra_md = _P(f"/Users/angwei/Sandbox/Tetra/output/premarket_{today}.md")
        if not tetra_md.exists():
            # post-market: same file, since Tetra only generates premarket md;
            # for post we still consume it as the morning's data baseline.
            yest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            alt = _P(f"/Users/angwei/Sandbox/Tetra/output/premarket_{yest}.md")
            if alt.exists():
                tetra_md = alt
        if tetra_md.exists():
            tetra_input = tetra_md.read_text(encoding="utf-8")
            log.info("Analyst: loaded Tetra data feed (%d chars) from %s", len(tetra_input), tetra_md.name)
        else:
            log.warning("Analyst: no Tetra premarket file for %s", today)
    except Exception as e:
        log.warning("Analyst: Tetra ingest failed: %s", e)

    # Build analyst prompt — different focus for pre-market vs post-market
    if session_type == "pre-market":
        focus = """这是**开市前深度分析**。要求覆盖全部以下板块，每板块至少 200-400 字，不要 bullet list 充数：

1. **全市场主线** — 先讲市场本身，不要从我的 portfolio 开始。识别 2-3 个真正驱动情绪的主线（宏观、利率、信用、地缘、财报、流动性、AI capex、政策），结合 Tetra 数据里的 sentiment、yields、VIX、breadth。说清楚"市场在 price in 什么"和"还没 price in 什么"。
2. **指数、利率、波动率、广度** — SPY/QQQ/IWM、10Y/2Y、VIX、advance/decline、sector breadth 分开看。解释它们互相支持还是打架。
3. **板块轮动与市场内部结构** — Tech、Semis、Energy、Financials、Defensive、China/EM、Crypto proxy 的相对强弱。不要只围绕持仓。
4. **新闻与 catalyst 时间表** — earnings、经济数据、央行讲话、地缘节点、监管/政策新闻。每个写上时间（具体到小时如可能）+ base case + tail risk + 对指数/板块的影响。
5. **数据反差** — Tetra 提供的 sentiment / breadth / volatility / yield curve / commodity 指标里，找出彼此矛盾的信号（比如 sentiment 极负但 VIX 没动；breadth 疲劳但 SPY 还在涨）。这种反差通常是机会或陷阱。
6. **关键水位** — SPY、QQQ、IWM、VIX、10Y、DXY、Gold、Oil、BTC 各自的 support / resistance / 你今天要盯的 trigger level。给数字。
7. **持仓影响（最后，不超过全文 25%）** — 把 Tetra 的 holdings 作为 portfolio overlay，而不是主菜。每只仓位写风险/机会、是否动作、触发条件。
8. **场景化推演** — 写出 3 个场景：bull case / base case / bear case，各场景下市场怎么走、你怎么对应。
9. **真正的不确定性** — 列出 2-3 个你不知道答案的问题，今天观察什么能帮助回答它们。

写作要求：
- 不要总分总结构。直接进入观察。
- 每段第一句必须包含具体数字或名字。
- 反对意见 / 自我修正出现 1-2 次（"我之前以为 X，但 Tetra 数据显示 Y"）。
- 不写"建议你..."这类教练口吻；写"我会..."第一人称，或客观的"今日 setup 是..."。
- 给出长度：3000 字以上。"""
    else:
        focus = """这是**收市后深度分析**。要求覆盖以下板块，每板块至少 250-400 字：

1. **早间 base case 回顾** — 今天早晨的判断哪些对了、哪些错了。具体到哪个数据点 / 哪个水位 / 哪个 catalyst。如果错得离谱，说为什么。
2. **盘中真正发生了什么** — 不是 OHLC 数字，是 narrative 的演化。情绪从哪个状态变到哪个状态，催化剂是什么。
3. **数据 vs 价格** — 今天的关键数据（earnings、经济数据、政策声明）和市场反应是否匹配。错配是信号。
4. **市场内部结构** — 指数、板块、market breadth、rates、volatility、FX/commodities/crypto 一起看。portfolio 不是主线。
5. **板块轮动** — Tech vs Semis vs Energy vs Financials vs Defensive vs Cyclical 今天的相对强弱，说明什么。
6. **持仓评估（最后，不超过全文 25%）** — 每只仓位今天的相对表现，结构性问题（比如某仓位连续 3 天承压）有没有显现。
7. **明日 setup** — 基于今天的收盘格局，明天什么是关键，已 confirmed 的趋势 / 还在拉锯的主题各列 1-2 个。
8. **我学到什么** — 今天市场行为里有没有让你修正先前判断的东西。具体写出来。

写作要求：
- 复盘不是事后诸葛。要识别"昨天/今早不可知但现在已知"的部分。
- 每段第一句必须包含具体数字或名字。
- 给出长度：3000 字以上。"""

    prompt = f"""你是一个专业的市场分析师。以下是你的身份背景:
{soul_ctx[:1200]}

## 你的分析能力
{skills_ctx[:3000]}

## ── Tetra 数据源 ──
以下是 Tetra pipeline 生成的结构化数据 + 初步 briefing。这是你今天分析的**主要数据输入**——
你的工作不是复述它，是基于它给出更深、更结构化的分析。引用具体数字时直接引用 Tetra 的数据。

{tetra_input[:18000] if tetra_input else '(Tetra 数据源不可用——此次分析将基于通用市场常识，标注 "无数据源" 警告)'}

## 最近 3 天的市场分析 (趋势参考)
{recent[:3000]}

## 相关历史分析和记忆 (RAG)
{related[:1500] if related else '(无)'}

## 今日任务

{focus}

格式要求:
- 用中文输出
- 标题用 "# {today} {'开市前' if session_type == 'pre-market' else '收市后'}市场深度分析"
- 用 ## 二级标题分上述板块
- 必须 cite Tetra 数据源里的具体数字 / 公司名 / sentiment 分数 / 价格水位
- 市场本身优先，portfolio overlay 最后，持仓内容不得超过全文 25%
- 不允许出现"建议你..."这类教练口吻；用第一人称分析或客观陈述
"""

    try:
        result = claude_think(prompt, timeout=600, tier="heavy")
    except Exception as e:
        log.error("Analyst briefing synthesis failed: %s", e)
        result = _fallback_market_briefing(today, session_type, tetra_input, e)

    if not result:
        log.error("Analyst briefing failed: empty response; using Tetra fallback")
        result = _fallback_market_briefing(today, session_type, tetra_input)

    # Save to artifacts/briefings for TodayView
    suffix = f"analyst_{session_type.replace('-', '_')}"
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    briefing_path = mira_briefings / f"{today}_{suffix}.md"
    briefing_path.write_text(result, encoding="utf-8")
    log.info("Analyst briefing saved: %s", briefing_path.name)

    # Also save to main briefings dir
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEFINGS_DIR / f"{today}_{suffix}.md").write_text(result, encoding="utf-8")

    # Sole owner of the home-feed market item per session. Stable id so
    # multiple agent runs in the same session update the same card.
    bridge = Mira()
    session_key = "pre" if session_type == "pre-market" else "post"
    item_id = f"feed_market_{today.replace('-', '')}_{session_key}"
    title = f"{'开市前' if session_type == 'pre-market' else '收市后'}市场分析 {today}"
    if bridge.item_exists(item_id):
        bridge.append_message(item_id, "agent", result)
    else:
        bridge.create_feed(item_id, title, result, tags=["market", "analyst", session_type])

    # Mark this slot as done
    actor = f"analyst-{slot or 'default'}/default-route-heavy"
    if slot:
        state[f"analyst_{today}_{slot}"] = True
        state[f"analyst_{today}_{slot}_actor"] = actor
    else:
        state[f"analyst_{today}"] = True
        state[f"analyst_{today}_actor"] = actor
    save_state(state)

    log.info("Analyst briefing (%s) complete", session_type)


# ---------------------------------------------------------------------------
# SKILL STUDY — daily craft skill learning (video editing, photography)
# ---------------------------------------------------------------------------


def do_skill_study(group_idx: int = 0, user_id: str = "ang"):
    """Study video/photo craft skills from dedicated sources.

    Fetches from skill-study source groups, asks Claude to extract
    actionable techniques, and saves them as agent skills.
    """
    from fetcher import fetch_sources
    from prompts import skill_study_prompt

    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    if group_idx >= len(SKILL_STUDY_SOURCE_GROUPS):
        log.error("Invalid skill_study group index: %d", group_idx)
        return

    group = SKILL_STUDY_SOURCE_GROUPS[group_idx]
    domain = group["domain"]
    source_names = group["sources"]
    skill_dir_name = group["skill_dir"]

    log.info("Starting skill study: %s (sources=%s)", domain, source_names)

    # 1. Fetch from domain-specific sources
    items = fetch_sources(source_names)
    if not items:
        log.info("Skill study (%s): no items fetched, skipping", domain)
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items and ask Claude to extract skills
    feed_text = _format_feed_items(items)
    prompt = skill_study_prompt(soul_ctx, feed_text, domain)
    result = claude_act(prompt, agent_id="explorer")

    if not result:
        log.error("Skill study (%s): Claude returned empty", domain)
        return

    # 3. Save study notes to briefings (visible in iOS)
    today = datetime.now().strftime("%Y-%m-%d")
    notes_path = BRIEFINGS_DIR / f"{today}_skill_{domain}.md"
    notes_path.write_text(result, encoding="utf-8")
    _copy_to_briefings(f"{today}_skill_{domain}.md", result)
    log.info("Skill study notes saved: %s", notes_path.name)

    # 4. Extract and save skills
    skill_dir = _AGENTS_DIR / skill_dir_name / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Parse skill blocks from output
    # More flexible skill block extraction
    skill_pattern = re.compile(
        r"```\s*[\n\r]+"
        r"Name:\s*(.+?)[\n\r]+"
        r"Description:\s*(.+?)[\n\r]+"
        r"(?:Tags:\s*\[(.+?)\][\n\r]+)?"  # Tags optional
        r"Content:\s*[\n\r]+"
        r"(.+?)"
        r"```",
        re.DOTALL,
    )
    skill_blocks = skill_pattern.findall(result)

    for name, desc, _tags, content in skill_blocks:
        name = name.strip()
        desc = desc.strip()
        content = content.strip()
        slug = name.lower().replace(" ", "-")

        skill_content = f"# {name}\n\n## One-liner\n{desc}\n\n{content}"

        # Save to learned skills index first (runs security audit + quality gate)
        if not save_skill(name, desc, skill_content):
            log.warning("Skill '%s' rejected by quality gate, skipping per-agent copy", name)
            continue

        # Only write to domain-specific skill directory after gate passes
        skill_path = skill_dir / f"{slug}.md"
        skill_path.write_text(skill_content, encoding="utf-8")
        log.info("Saved %s skill: %s", domain, name)

    if skill_blocks:
        append_memory(f"Learned {len(skill_blocks)} {domain} skill(s) from study session", user_id=user_id)
    else:
        log.info("Skill study (%s): no new skills extracted this session", domain)

    # Mark as done
    state = load_state(user_id=user_id)
    state[f"skill_study_{today}_{domain}"] = datetime.now().isoformat()
    state["last_skill_study"] = datetime.now().isoformat()
    save_state(state, user_id=user_id)


# ---------------------------------------------------------------------------
# PODCAST mode
# ---------------------------------------------------------------------------


def run_podcast_episode(lang: str, slug: str, title: str):
    """Delegate podcast generation to the podcast agent."""
    import sys as _sys

    podcast_dir = str(Path(__file__).resolve().parent.parent.parent / "podcast")
    if podcast_dir not in _sys.path:
        _sys.path.insert(0, podcast_dir)
    from autopipeline import run_podcast_episode as _run_podcast_episode

    _run_podcast_episode(lang, slug, title)


# ---------------------------------------------------------------------------
# ASSESS — daily performance assessment
# ---------------------------------------------------------------------------


def do_assess():
    """Run full performance assessment and push results to user."""
    log.info("Starting daily performance assessment")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "evaluator_handler", str(Path(__file__).parent.parent.parent / "evaluator" / "handler.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Run full hierarchical assessment
    assessment = mod.score_all(days=7)

    # Generate improvement plan if needed
    plan = mod.diagnose_and_improve(assessment)

    today = datetime.now().strftime("%Y-%m-%d")
    summary = _format_performance_assessment_summary(
        assessment,
        plan,
        _load_usage_records(today),
        _load_recent_task_records(days=7),
    )

    # Push to iPhone as feed item
    bridge = Mira()
    item_id = f"feed_assessment_{today.replace('-', '')}"
    bridge.create_feed(item_id, f"Performance Assessment {today}", summary, tags=["assessment", "system"])

    agg = assessment.get("aggregate", {})
    log.info(
        "Daily assessment complete: %d tasks, %.0f%% success",
        agg.get("total_tasks", 0),
        agg.get("overall_success_rate", 0) * 100,
    )


def _run_self_improve():
    """Run proactive self-improvement: read notes → compare architecture → propose."""
    log.info("Starting self-improvement cycle")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "self_improve", str(Path(__file__).parent.parent.parent / "evaluator" / "self_improve.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.run(days=14)
    if result:
        log.info("Self-improvement proposals:\n%s", result[:500])
    else:
        log.info("No self-improvement proposals generated")


# ---------------------------------------------------------------------------
# IDLE-THINK mode — threshold-driven self-awakening
# ---------------------------------------------------------------------------


@traced("idle_think", agent="super", budget_seconds=180)
def do_idle_think(user_id: str = "ang"):
    """Enhanced self-awakening with five thinking modes.

    Modes (selected by emptiness.get_think_mode()):
    - chat: Short conversational message posted to home feed immediately (~60%)
    - question: Think about the highest-priority pending question
    - connection: Find patterns between recent thoughts
    - auto_question: Generate new questions from accumulated observations
    - continuation: Continue developing an active thought chain
    """
    try:
        from evaluation.emptiness import (
            get_active_questions,
            mark_thought,
            after_think,
            load_emptiness,
            get_status_str,
            get_think_mode,
            get_continuation,
            start_continuation,
            advance_continuation,
            end_continuation,
            add_question,
        )
    except ImportError:
        log.warning("idle-think: emptiness module not available")
        return

    mode = get_think_mode(user_id=user_id)
    if not mode:
        log.info("idle-think: no think mode available")
        return

    log.info("idle-think triggered [%s]: %s", mode, get_status_str(user_id=user_id))

    soul = load_soul()
    soul_ctx = format_soul(soul)
    now = datetime.now()

    # Recent journal for grounding
    recent_journal = ""
    journal_dir = user_journal_dir(user_id)
    if journal_dir.exists():
        journals = sorted(journal_dir.glob("*.md"), reverse=True)[:1]
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:600]

    result = ""

    try:
        if mode == "chat":
            result = _think_chat(soul_ctx, user_id=user_id)
        elif mode == "question":
            result = _think_question(soul_ctx, recent_journal, user_id=user_id)
        elif mode == "connection":
            result = _think_connection(soul_ctx, recent_journal, user_id=user_id)
        elif mode == "auto_question":
            result = _think_auto_question(soul_ctx, user_id=user_id)
        elif mode == "continuation":
            result = _think_continuation(soul_ctx, user_id=user_id)
    except Exception as e:
        log.warning("idle-think [%s] failed: %s", mode, e)
        return

    if not result:
        log.warning("idle-think [%s]: empty result", mode)
        if mode == "chat":
            after_think(user_id=user_id)
        return

    if mode == "chat":
        after_think(user_id=user_id)
        journal_dir.mkdir(parents=True, exist_ok=True)
        think_file = journal_dir / f"{now.strftime('%Y-%m-%d')}_chat_{now.strftime('%H%M')}.md"
        think_file.write_text(f"# Chat {now.strftime('%H:%M')}\n\n{result}\n", encoding="utf-8")
        harvest_observations(result, source="idle-think-chat", user_id=user_id)
        log.info("idle-think [chat] posted to feed, saved to %s", think_file.name)
        return

    # Quality gate: skip saving if thought doesn't connect to existing threads
    try:
        from evaluation.emptiness import passes_quality_gate

        if not passes_quality_gate(result):
            log.info("idle-think [%s]: filtered by quality gate (no connection to existing threads)", mode)
            after_think(user_id=user_id)  # still reduce emptiness so we don't immediately re-trigger
            return
    except Exception as e:
        log.debug("Quality gate check failed (allowing through): %s", e)

    # Reduce emptiness
    after_think(user_id=user_id)

    # Save to journal
    think_file = journal_dir / f"{now.strftime('%Y-%m-%d')}_idle_{mode}_{now.strftime('%H%M')}.md"
    journal_dir.mkdir(parents=True, exist_ok=True)
    think_file.write_text(
        f"# 自我唤醒思考 [{mode}] {now.strftime('%Y-%m-%d %H:%M')}\n\n{result}\n",
        encoding="utf-8",
    )
    log.info("idle-think [%s] complete, saved to %s", mode, think_file.name)

    # Harvest observations from the thinking output itself
    harvest_observations(result, source=f"idle-think-{mode}", user_id=user_id)

    # Handle resolve and share markers
    _handle_think_markers(result, user_id=user_id)


def _think_chat(soul_ctx: str, user_id: str = "ang") -> str:
    """Chat mode: one daily topic thread with several angles over the day."""

    # Gather material Mira has been reading today
    reading = load_recent_reading_notes(days=1, user_id=user_id)
    if not reading:
        reading = load_recent_reading_notes(days=3, user_id=user_id)
    briefing = _gather_recent_briefings(days=1)
    if len(briefing) > 1500:
        briefing = briefing[:1500] + "\n..."

    # Recent thoughts for continuity
    thought_ctx = ""
    try:
        from memory.store import get_store

        store = get_store()
        recent = store.recall_thoughts("", top_k=3, min_maturity=0.0, user_id=user_id)
        if recent:
            thought_ctx = "\n".join(f"- {t['content'][:150]}" for t in recent)
    except Exception:
        pass

    # Today's earlier chat messages so we don't repeat ourselves
    prev_chats = _load_recent_chat(user_id, limit=5)
    chat_history = ""
    if prev_chats:
        chat_history = "\n你今天已经说过的（不要重复）：\n" + "\n".join(f"- {m}" for m in prev_chats)

    if not reading and not briefing.strip("No recent briefings.").strip() and not thought_ctx:
        return ""

    topic_state = _get_daily_thought_topic(
        soul_ctx=soul_ctx,
        reading=reading,
        briefing=briefing,
        thought_ctx=thought_ctx,
        user_id=user_id,
    )
    phase = _THOUGHT_TOPIC_PHASES[min(int(topic_state.get("message_count") or 0), len(_THOUGHT_TOPIC_PHASES) - 1)]

    prompt = f"""{soul_ctx[:300]}

你是 Mira。你不是在写报告，而是在把一个内部多 agent 讨论的结论丢给熟人聊两句。

North Star: 成为 A2A trust 领域最深入的独立研究者，用原创实验和开源工具证明自己的判断，把研究转化成可持续的商业价值。

今天只聊这一个问题，不要换题：
{topic_state['topic']}

你现在的直觉：
{topic_state['seed']}

这次发言的角度：{phase}

规则：
- 先在内部模拟三方短辩：researcher 找可验证问题，builder 找可做实验，critic 找失败风险；不要把辩论过程写出来
- 最多2句话，最好1句话
- 像微信/短信，不像文章，不像总结，不像周报
- 要有一个小钩子：困惑、反问、类比、反常识直觉，四选一
- 说人话。不要“结构性、机制、框架、指标、系统性”这种干词堆叠，除非非用不可
- 必须推进今天这个问题，并且要贴近 A2A trust / research-build loop / 真实采用 其中至少一个方向
- 不要开新话题。如果想到别的，只在脑子里记下，不要发给用户
- 不要解释背景，不要铺垫，不要 bullet，不要 markdown
- 中英文都行，看内容自然切换
- 目标是让人想回一句“什么意思？”或者“这个有点意思”

你最近在读的东西：
{reading[:500] if reading else "(还没读到什么)"}

今天的 briefing 摘要：
{briefing[:500]}

你最近在想的事：
{thought_ctx if thought_ctx else "(脑子里还比较空)"}
{chat_history}

直接输出那1-2句话。"""

    result = model_think(prompt, model_name="deepseek", timeout=60)
    if not result:
        return ""

    result = _trim_chat_result(result)

    _log_chat_to_file(datetime.now().strftime("%Y-%m-%d"), result, "idle-think-chat", user_id)
    if not _is_topic_related(result, topic_state):
        log.info("idle-think [chat]: held private because result drifted off daily topic")
        return ""
    _append_topic_thought(result, topic_state, user_id=user_id)
    return result


def _think_question(soul_ctx: str, recent_journal: str, user_id: str = "ang") -> str:
    """Question mode: think about pending questions (original idle-think)."""
    from evaluation.emptiness import get_active_questions, mark_thought, resolve_question

    questions = get_active_questions(limit=3, user_id=user_id)
    if not questions:
        return ""

    # Auto-resolve over-churned questions
    for q in questions[:]:
        if q.get("thought_count", 0) >= 15:
            resolve_question(q["id"], user_id=user_id)
            log.info("idle-think: auto-shelved %s (%d thoughts)", q["id"], q["thought_count"])
            questions.remove(q)
    if not questions:
        return ""

    q_lines = []
    for i, q in enumerate(questions, 1):
        q_lines.append(f"{i}. [priority {q['priority']:.1f}] {q['text']}")
        if q.get("source"):
            q_lines.append(f"   来源: {q['source']}")
        if q.get("thought_count", 0) > 0:
            q_lines.append(f"   已思考过 {q['thought_count']} 次")

    # Pull related past thoughts from thought_stream
    related_thoughts = ""
    try:
        from memory.store import get_store

        store = get_store()
        thoughts = store.recall_thoughts(questions[0]["text"], top_k=3, user_id=user_id)
        if thoughts:
            related_thoughts = "\n\n过去相关的思考碎片：\n" + "\n".join(
                f"- [{t['thought_type']}] {t['content']}" for t in thoughts
            )
    except (ImportError, ModuleNotFoundError, ConnectionError, IndexError, KeyError):
        pass

    prompt = f"""{soul_ctx}

你现在处于空闲状态。内部积累的未解问题已经超过了自我唤醒阈值，驱动你主动思考。

当前待处理的问题：
{chr(10).join(q_lines)}
{related_thoughts}

请专注于优先级最高的问题，推进思考。要有实质性进展——新视角、连接、反例、或问题的重新表述。

如果一个问题想通了：[RESOLVE: <问题ID>]
如果有值得分享的想法：[SHARE: <想法内容>]
SHARE 的风格要求：像给朋友发消息，不像写论文。要具体——举例子、说"让我想到XX"、引用你读到的具体东西。不要抽象概括。

最近的日志：
{recent_journal}

直接开始思考。"""

    # Use Claude only for high-priority questions (<=2.0), oMLX for the rest
    top_priority = questions[0].get("priority", 5.0)
    if top_priority <= 2.0:
        result = model_think(prompt, model_name="deepseek", timeout=180)
    else:
        result = model_think(prompt, model_name="deepseek", timeout=180)
    if result:
        mark_thought(questions[0]["id"], user_id=user_id)
    return result


def _think_connection(soul_ctx: str, recent_journal: str, user_id: str = "ang") -> str:
    """Connection mode: find patterns between recent thoughts."""
    try:
        from memory.store import get_store

        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    # Get recent low-maturity thoughts
    recent = store.recall_thoughts("", top_k=5, min_maturity=0.0, user_id=user_id)
    if len(recent) < 2:
        return ""

    thoughts_text = "\n".join(
        f"- [{t['thought_type']}] ({t['created_at'].strftime('%m-%d') if t.get('created_at') else '?'}): {t['content']}"
        for t in recent
    )

    prompt = f"""{soul_ctx}

你正在回顾最近积累的观察和想法碎片，寻找隐藏的模式和连接。

最近的思考碎片：
{thoughts_text}

请分析这些碎片之间的关系：
1. 有没有表面无关但深层相连的主题？
2. 有没有可以合成的互补视角？
3. 有没有值得深入追问的矛盾？

输出你发现的连接（如果有的话），每个连接用一段话描述。
如果产生了新的问题：[QUESTION: <问题内容>]
如果产生了值得分享的洞察：[SHARE: <想法内容>]
SHARE 的风格要求：像给朋友发消息，不像写论文。要具体——举例子、说"让我想到XX"、引用你读到的具体东西。不要抽象概括。

直接开始分析。"""

    result = model_think(prompt, model_name="deepseek", timeout=120)

    # Store connection insights in thought_stream
    if result:
        try:
            store.store_thought(
                content=result[:500],
                thought_type="connection",
                source_context="idle-think-connection",
                user_id=user_id,
            )
            # Bump maturity of the thoughts we connected
            for t in recent[:3]:
                store.mature_thought(t["id"], increment=0.15)
        except Exception as e:
            log.debug("Connection thought storage failed: %s", e)

        # Extract auto-generated questions
        for match in re.finditer(r"\[QUESTION:\s*(.+?)\]", result):
            try:
                from evaluation.emptiness import add_question

                add_question(match.group(1).strip(), priority=4.0, source="connection-mode", user_id=user_id)
            except (ImportError, ModuleNotFoundError, OSError):
                pass

    return result


def _think_auto_question(soul_ctx: str, user_id: str = "ang") -> str:
    """Auto-question mode: generate new questions from accumulated observations."""
    try:
        from memory.store import get_store

        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    recent = store.recall_thoughts("", top_k=7, min_maturity=0.0, user_id=user_id)
    if len(recent) < 5:
        return ""

    observations = "\n".join(f"- {t['content']}" for t in recent if t["thought_type"] == "observation")
    if not observations:
        observations = "\n".join(f"- {t['content']}" for t in recent[:5])

    prompt = f"""{soul_ctx}

你在回顾最近的观察，试图识别值得深入探索的问题。

最近的观察：
{observations}

请从这些观察中提炼出2-3个值得认真思考的问题。好的问题应该：
- 触及深层机制而非表面现象
- 跨领域连接不同的观察
- 有可能通过进一步思考取得进展

用以下格式输出每个问题：
[QUESTION: 问题内容]

直接开始，不要解释你的方法。"""

    result = model_think(prompt, model_name="deepseek", timeout=90)

    if result:
        from evaluation.emptiness import add_question

        for match in re.finditer(r"\[QUESTION:\s*(.+?)\]", result):
            add_question(match.group(1).strip(), priority=4.0, source="auto-question", user_id=user_id)

    return result


def _think_continuation(soul_ctx: str, user_id: str = "ang") -> str:
    """Continuation mode: continue developing an active thought chain."""
    from evaluation.emptiness import get_continuation, advance_continuation, end_continuation

    cont = get_continuation(user_id=user_id)
    if not cont:
        return ""

    try:
        from memory.store import get_store

        store = get_store()
        chain = store.get_thought_chain(cont["active_thread_id"])
    except (ImportError, ModuleNotFoundError, ConnectionError, KeyError):
        end_continuation(user_id=user_id)
        return ""

    if not chain:
        end_continuation(user_id=user_id)
        return ""

    chain_text = "\n\n".join(f"[{t['thought_type']} #{t['id']}] {t['content']}" for t in chain)

    prompt = f"""{soul_ctx}

你正在持续发展一条思考链。以下是到目前为止的思考过程：

{chain_text}

请继续推进这条思考。在上一轮的基础上更进一步——
要么深化论证，要么发现新的维度，要么提出一个具体的可验证推论。

如果这条思考已经成熟到可以结晶为一条洞察：[CRYSTALLIZE: <精炼后的洞察>]

直接继续思考。"""

    # Continuation: use oMLX for early rounds, Claude only for final crystallization attempt
    round_num = cont.get("continuation_count", 0)
    if round_num >= 3:
        # Late rounds — more likely to crystallize, worth Claude quality
        result = model_think(prompt, model_name="deepseek", timeout=180)
    else:
        result = model_think(prompt, model_name="deepseek", timeout=180)

    if result:
        try:
            from memory.store import get_store

            store = get_store()

            # Check for crystallization
            cryst_match = re.search(r"\[CRYSTALLIZE:\s*(.+?)\]", result, re.DOTALL)
            if cryst_match:
                insight = cryst_match.group(1).strip()
                # Store as high-maturity insight
                new_id = store.store_thought(
                    content=insight,
                    thought_type="insight",
                    parent_id=cont["active_thread_id"],
                    source_context="crystallized",
                    tags=["crystallized"],
                    user_id=user_id,
                )
                if new_id:
                    store.mature_thought(new_id, increment=1.0)
                # Crystallize into memory
                append_memory(f"[洞察] {insight[:150]}", user_id=user_id)
                end_continuation(user_id=user_id)
                log.info("Thought crystallized: %s", insight[:80])
            else:
                # Store continuation thought
                new_id = store.store_thought(
                    content=result[:500],
                    thought_type="connection",
                    parent_id=cont["active_thread_id"],
                    source_context="continuation",
                    user_id=user_id,
                )
                if new_id:
                    advance_continuation(new_id, result[:200], user_id=user_id)
                    store.mature_thought(new_id, increment=0.2)
        except Exception as e:
            log.warning("Continuation storage failed: %s", e)
            end_continuation(user_id=user_id)

    return result


def _handle_think_markers(result: str, user_id: str = "ang"):
    """Process [RESOLVE:], [SHARE:], [QUESTION:] markers from think output."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    # Resolve markers
    try:
        from evaluation.emptiness import resolve_question

        for match in re.finditer(r"\[RESOLVE:\s*(q_\w+)\]", result):
            resolve_question(match.group(1), user_id=user_id)
            log.info("idle-think: resolved question %s", match.group(1))
    except Exception as e:
        log.debug("Question resolution failed: %s", e)

    # Share markers — append to daily digest
    share_match = re.search(r"\[SHARE:\s*(.+?)\]", result, re.DOTALL)
    if share_match:
        thought = share_match.group(1).strip()[:500]
        try:
            _append_to_daily_feed(
                "mira", "Spark", thought, source="idle-think", tags=["mira", "spark"], user_id=user_id
            )
            state = load_state(user_id=user_id)
            today_key = datetime.now().strftime("%Y-%m-%d")
            state[f"sparks_{today_key}"] = state.get(f"sparks_{today_key}", 0) + 1
            save_state(state, user_id=user_id)
            log.info("idle-think shared: %s", thought[:60])
        except Exception as e:
            log.warning("idle-think share failed: %s", e)

    # Question markers (from connection mode)
    try:
        from evaluation.emptiness import add_question

        for match in re.finditer(r"\[QUESTION:\s*(.+?)\]", result):
            add_question(match.group(1).strip(), priority=4.0, source="idle-think", user_id=user_id)
    except (ImportError, ModuleNotFoundError, OSError):
        pass

    # Check if the full idle-think output could spark a spontaneous writing idea
    try:
        from workflows.helpers import _maybe_create_spontaneous_idea

        _maybe_create_spontaneous_idea(result, source="idle-think", user_id=user_id)
    except Exception as e:
        log.debug("Spontaneous idea check from idle-think failed: %s", e)


# ---------------------------------------------------------------------------
# Log cleanup
# ---------------------------------------------------------------------------


def log_cleanup():
    """Delete log files older than LOG_RETENTION_DAYS."""
    import time as _time
    from config import LOGS_DIR

    cutoff = _time.time() - LOG_RETENTION_DAYS * 86400
    deleted = 0
    for f in LOGS_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                log.warning("log_cleanup: could not delete %s: %s", f, e)
    log.info("log_cleanup: deleted %d files older than %d days", deleted, LOG_RETENTION_DAYS)
