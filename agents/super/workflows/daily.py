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
from datetime import datetime, timedelta
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

import health_monitor

from config import (
    BRIEFINGS_DIR,
    JOURNAL_DIR,
    MIRA_DIR,
    ARTIFACTS_DIR,
    WORKSPACE_DIR,
    MIRA_ROOT,
    RESEARCH_TOPIC,
    SKILL_STUDY_SOURCE_GROUPS,
    EPISODES_DIR,
    LOG_RETENTION_DAYS,
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
    harvest_observations,
)

log = logging.getLogger("mira")


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
    result = claude_think(prompt, timeout=120)

    if not result:
        log.error("哲思: Claude returned empty")
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
    state[f"zhesi_{today}_actor"] = "zhesi/claude-think"
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

    # Mark as done early to avoid re-trigger
    state = load_state()
    state[f"book_review_{today}"] = datetime.now().isoformat()
    save_state(state)

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
        else:
            log.info("Book review exit 0")
            if stderr_tail:
                # daily_book_review.py logs to stderr via StreamHandler — so
                # stderr here contains the real run-log.
                log.info("Book review log tail: %s", stderr_tail)
    except Exception as e:
        log.error("Book review exception: %s", e)


# ---------------------------------------------------------------------------
# ANALYST mode — daily market analysis briefing (business days)
# ---------------------------------------------------------------------------


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

1. **隔夜叙事主线** — 不是新闻复述。识别出 1-2 个真正驱动情绪的主线（地缘、央行、单一公司、流动性事件），结合 Tetra 数据里的 sentiment 分数、yields、VIX、breadth。说清楚"市场在 price in 什么"和"还没 price in 什么"。
2. **数据反差** — Tetra 提供的 sentiment / breadth / volatility / yield curve / commodity 指标里，找出彼此矛盾的信号（比如 sentiment 极负但 VIX 没动；breadth 疲劳但 SPY 还在涨）。这种反差通常是机会或陷阱。
3. **今日 catalyst 时间表** — earnings、经济数据、央行讲话、地缘节点。每个写上时间（具体到小时如可能）+ 你的 base case + tail risk + 怎么影响哪只持仓。
4. **持仓逐项审视** — 把 Tetra 提供的 holdings 列表过一遍，每只仓位写：当前在面对什么风险/机会、是否动作（hold / trim / add），动作的触发条件（具体水位）。不要笼统说"AI 持仓 intact"——具体到 META、GOOGL、PLTR 各自的 setup。
5. **关键水位** — SPY、QQQ、VIX、10Y、DXY、Gold、Oil、BTC 各自的 support / resistance / 你今天要盯的 trigger level。给数字。
6. **场景化推演** — 写出 3 个场景：bull case / base case / bear case，各场景下市场怎么走、你怎么对应。
7. **真正的不确定性** — 列出 2-3 个你不知道答案的问题，今天观察什么能帮助回答它们。

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
4. **板块轮动** — Tech vs Energy vs Defensive vs Cyclical 今天的相对强弱，说明什么。
5. **持仓评估** — 每只仓位今天的相对表现，结构性问题（比如某仓位连续 3 天承压）有没有显现。
6. **明日 setup** — 基于今天的收盘格局，明天什么是关键，已 confirmed 的趋势 / 还在拉锯的主题各列 1-2 个。
7. **我学到什么** — 今天市场行为里有没有让你修正先前判断的东西。具体写出来。

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
- 不允许出现"建议你..."这类教练口吻；用第一人称分析或客观陈述
"""

    result = claude_think(prompt, timeout=600, tier="heavy")

    if not result:
        log.error("Analyst briefing failed: empty response")
        return

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
    actor = f"analyst-{slot or 'default'}/claude-think-heavy"
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

    # Format short summary for user
    agg = assessment["aggregate"]
    active_agents = []
    for name, card in assessment["agents"].items():
        if card["task_count"] > 0:
            emoji = "✅" if card["success_rate"] >= 0.8 else "⚠️" if card["success_rate"] >= 0.5 else "❌"
            active_agents.append(f"{emoji} {name}: {card['success_rate']:.0%} ({card['task_count']})")

    summary_parts = [
        f"📊 Weekly: {agg.get('total_tasks', 0)} tasks, {agg.get('overall_success_rate', 0):.0%} success",
        f"💰 Today: ${agg.get('daily_cost_usd', 0):.2f} ({agg.get('daily_calls', 0)} calls)",
        f"🫀 Crash rate: {agg.get('crash_rate', 0):.1%}",
    ]
    if active_agents:
        summary_parts.append("\nPer agent:")
        summary_parts.extend(active_agents)
    if plan:
        summary_parts.append(
            f"\n⚠️ Improvement plan generated — see scorecards/{datetime.now().strftime('%Y-%m-%d')}.json"
        )

    summary = "\n".join(summary_parts)

    # Push to iPhone as feed item
    bridge = Mira()
    today = datetime.now().strftime("%Y-%m-%d")
    item_id = f"feed_assessment_{today.replace('-', '')}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", f"Performance Assessment {today}", summary, tags=["assessment", "system"])
        bridge.update_status(item_id, "done")

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
    """Enhanced self-awakening with three thinking modes.

    Modes (selected by emptiness.get_think_mode()):
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
        if mode == "question":
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
        result = claude_think(prompt, timeout=180)
    else:
        result = model_think(prompt, model_name="omlx", timeout=180)
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

    result = model_think(prompt, model_name="omlx", timeout=120)

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

    result = model_think(prompt, model_name="omlx", timeout=90)

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
        result = claude_think(prompt, timeout=180)
    else:
        result = model_think(prompt, model_name="omlx", timeout=180)

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
