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
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

import health_monitor

from config import (
    BRIEFINGS_DIR, JOURNAL_DIR, MIRA_DIR, ARTIFACTS_DIR,
    WORKSPACE_DIR, MIRA_ROOT,
    RESEARCH_TOPIC,
    SKILL_STUDY_SOURCE_GROUPS,
    EPISODES_DIR, LOG_RETENTION_DAYS,
)
from mira import Mira
from soul_manager import (
    load_soul, format_soul, append_memory, save_skill,
    load_recent_reading_notes,
    _atomic_write as atomic_write,
)
from sub_agent import claude_think, claude_act, model_think
from prompts import zhesi_prompt

from workflows.helpers import (
    _gather_today_tasks, _gather_today_skills, _gather_today_comments,
    _gather_usage_summary, _gather_recent_briefings,
    _mine_za_one, _mine_za_ideas,
    _copy_to_briefings, _append_to_daily_feed,
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
    from config import MIRA_ROOT
    pending_items = []
    pending_file = MIRA_ROOT / ".pending_publish.json"
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

    # Append daily report to daily digest
    try:
        _append_to_daily_feed("mira", "Daily Report", report,
                             source="report", tags=["mira", "report"])
        log.info("Daily report appended to daily digest")
    except Exception as e:
        log.error("Failed to append daily report to digest: %s", e)

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
            capture_output=True, text=True, timeout=600,
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
        log.warning("Daily photo: review score %s < 5, not sending. Critique: %s",
                     review_score, (result.get("review") or {}).get("critique", "")[:200])
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
    msg_parts.append(
        "\n给个分？(0-10) + 你觉得哪里不对"
    )

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
    photo_state_file.write_text(json.dumps({
        "date": today,
        "item_id": item_id,
        "raw": str(result.get("raw", "")),
        "output": str(output_path),
        "model_score": score,
        "params": result.get("params", {}),
        "wa_score": None,
        "wa_feedback": None,
        "rounds": 0,
    }, ensure_ascii=False, indent=2))

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
    score_match = re.search(r'(\d+(?:\.\d+)?)', user_message)
    if not score_match:
        # No score found — treat as text feedback only
        bridge = Mira(MIRA_DIR)
        bridge.append_message(item_id, "agent",
                              "Got your feedback. Can you also give a score (0-10)?")
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
    calibration_file.write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2))

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
    log.info("Photo feedback recorded: wa=%.1f model=%.1f delta=%s (DB size=%d)",
             wa_score, model_score, delta_str, len(calibration))


# ---------------------------------------------------------------------------
# 每日哲思 — Daily Philosophical Thought
# ---------------------------------------------------------------------------

def do_zhesi():
    """Write a daily philosophical thought based on a fragment from 杂.md."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily 哲思")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state()
    fragment = _mine_za_one(state)
    if not fragment:
        log.info("No fragments available from 杂.md, skipping 哲思")
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception as e:
        log.warning("Failed to load reading notes for zhesi: %s", e)

    prompt = zhesi_prompt(soul_ctx, fragment, recent_reading)
    result = claude_think(prompt, timeout=120)

    if not result:
        log.error("哲思: Claude returned empty")
        return

    # Save
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    zhesi_path = JOURNAL_DIR / f"{today}_zhesi.md"
    content = f"# 每日哲思 {today}\n\n> {fragment}\n\n{result}"
    atomic_write(zhesi_path, content)
    log.info("哲思 saved: %s", zhesi_path.name)

    # Copy to artifacts for iOS (with verification)
    _copy_to_briefings(f"{today}_zhesi.md", content)

    # Create feed item for zhesi
    try:
        bridge = Mira()
        bridge.create_feed(f"feed_zhesi_{datetime.now().strftime('%Y%m%d')}", f"每日哲思 {datetime.now().strftime('%m/%d')}", content[:2000], tags=["reflection", "philosophy"])
        log.info("哲思 feed item created")
    except Exception as e:
        log.warning("Failed to create 哲思 feed: %s", e)

    state[f"zhesi_{today}"] = datetime.now().isoformat()
    state[f"zhesi_{today}_actor"] = "zhesi/claude-think"
    save_state(state)


# ---------------------------------------------------------------------------
# SOUL QUESTION — daily philosophical question for WA
# ---------------------------------------------------------------------------

def do_soul_question():
    """Generate and send the daily soul question."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily soul question")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state()

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "soul_question",
        str(Path(__file__).parent.parent.parent / "shared" / "soul_question.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    history = mod._load_history()
    log.info("Loaded %d historical soul questions", len(history))

    question = mod.generate_soul_question(history)
    if not question:
        log.error("Failed to generate soul question — aborting")
        return

    log.info("Generated soul question:\n%s", question)

    # Send to app feed as a discussion item
    sent = mod.send_to_user(question)
    if sent:
        history.append(question[:120])
        mod._save_history(history)
        log.info("Soul question sent and saved")

    # Also create a feed spark for the Mira app
    try:
        bridge = Mira()
        bridge.create_feed(
            f"feed_soul_question_{datetime.now().strftime('%Y%m%d')}",
            f"灵魂问题 {datetime.now().strftime('%m/%d')}",
            question[:2000],
            tags=["soul-question", "philosophy", "discussion"],
        )
        log.info("Soul question feed item created")
    except Exception as e:
        log.warning("Failed to create soul question feed: %s", e)

    state[f"soul_question_{today}"] = datetime.now().isoformat()
    state[f"soul_question_{today}_actor"] = "soul-question/claude-think"
    save_state(state)


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
        "researcher_handler",
        str(Path(__file__).parent.parent.parent / "researcher" / "handler.py"))
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
        bridge.create_item(item_id, "feed", f"Daily Research {today}", result,
                          tags=["research", "daily"])
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
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            log.error("Book review failed (rc=%d): %s", result.returncode, result.stderr[-500:])
        else:
            log.info("Book review completed")
            if result.stdout:
                log.info("Output: %s", result.stdout[-300:])
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

    # Build analyst prompt — different focus for pre-market vs post-market
    if session_type == "pre-market":
        focus = """这是**开市前分析**。重点关注：
1. **隔夜动态** — 亚洲/欧洲市场、重要新闻、政策变化
2. **今日预期** — 今天可能影响市场的事件、数据发布
3. **持仓建议** — 基于隔夜信息，有什么需要调整的
4. **关注信号** — 今天盯什么指标
5. **风险预警** — 可能的意外风险"""
    else:
        focus = """这是**收市后分析**。重点关注：
1. **今日回顾** — 市场实际表现 vs 早间预期，哪些预判对了/错了
2. **趋势信号** — 今天的走势确认或否定了什么趋势
3. **异常信号** — 有没有反常的走势或数据
4. **明日展望** — 基于今天的表现，明天关注什么
5. **学到什么** — 今天的市场行为教了你什么"""

    prompt = f"""你是一个专业的市场分析师。以下是你的身份背景:
{soul_ctx[:800]}

## 你的分析能力
{skills_ctx[:2000]}

## 最近的 briefing 内容 (供参考趋势)
{recent[:2000]}

## 今日任务

{focus}

要求:
- 用中文输出
- Markdown 格式
- 分析要有深度，不是简单的新闻复述
- 给出你自己的判断和推荐
- 标题用 "# {today} {session_type} 市场分析"
"""

    result = claude_think(prompt, timeout=300, tier="heavy")

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

    # Push as standalone feed item
    bridge = Mira()
    item_id = f"feed_market_{today.replace('-', '')}_{slot or '0000'}"
    title = f"{'开市前' if session_type == 'pre-market' else '收市后'}市场分析 {today}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", title, result,
                          tags=["market", "analyst", session_type])
        bridge.update_status(item_id, "done")

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

def do_skill_study(group_idx: int = 0):
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
    result = claude_act(prompt)

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
        r'```\s*[\n\r]+'
        r'Name:\s*(.+?)[\n\r]+'
        r'Description:\s*(.+?)[\n\r]+'
        r'(?:Tags:\s*\[(.+?)\][\n\r]+)?'  # Tags optional
        r'Content:\s*[\n\r]+'
        r'(.+?)'
        r'```',
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
        append_memory(f"Learned {len(skill_blocks)} {domain} skill(s) from study session")
    else:
        log.info("Skill study (%s): no new skills extracted this session", domain)

    # Mark as done
    state = load_state()
    state[f"skill_study_{today}_{domain}"] = datetime.now().isoformat()
    state["last_skill_study"] = datetime.now().isoformat()
    save_state(state)


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
        "evaluator_handler",
        str(Path(__file__).parent.parent.parent / "evaluator" / "handler.py"))
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
        summary_parts.append(f"\n⚠️ Improvement plan generated — see scorecards/{datetime.now().strftime('%Y-%m-%d')}.json")

    summary = "\n".join(summary_parts)

    # Push to iPhone as feed item
    bridge = Mira()
    today = datetime.now().strftime("%Y-%m-%d")
    item_id = f"feed_assessment_{today.replace('-', '')}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", f"Performance Assessment {today}", summary,
                          tags=["assessment", "system"])
        bridge.update_status(item_id, "done")

    log.info("Daily assessment complete: %d tasks, %.0f%% success",
             agg.get("total_tasks", 0), agg.get("overall_success_rate", 0) * 100)


def _run_self_improve():
    """Run proactive self-improvement: read notes → compare architecture → propose."""
    log.info("Starting self-improvement cycle")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "self_improve",
        str(Path(__file__).parent.parent.parent / "evaluator" / "self_improve.py"))
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

def do_idle_think():
    """Enhanced self-awakening with three thinking modes.

    Modes (selected by emptiness.get_think_mode()):
    - question: Think about the highest-priority pending question
    - connection: Find patterns between recent thoughts
    - auto_question: Generate new questions from accumulated observations
    - continuation: Continue developing an active thought chain
    """
    try:
        from emptiness import (
            get_active_questions, mark_thought, after_think,
            load_emptiness, get_status_str, get_think_mode,
            get_continuation, start_continuation, advance_continuation,
            end_continuation, add_question,
        )
    except ImportError:
        log.warning("idle-think: emptiness module not available")
        return

    mode = get_think_mode()
    if not mode:
        log.info("idle-think: no think mode available")
        return

    log.info("idle-think triggered [%s]: %s", mode, get_status_str())

    soul = load_soul()
    soul_ctx = format_soul(soul)
    now = datetime.now()

    # Recent journal for grounding
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:1]
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:600]

    result = ""

    try:
        if mode == "question":
            result = _think_question(soul_ctx, recent_journal)
        elif mode == "connection":
            result = _think_connection(soul_ctx, recent_journal)
        elif mode == "auto_question":
            result = _think_auto_question(soul_ctx)
        elif mode == "continuation":
            result = _think_continuation(soul_ctx)
    except Exception as e:
        log.warning("idle-think [%s] failed: %s", mode, e)
        return

    if not result:
        log.warning("idle-think [%s]: empty result", mode)
        return

    # Quality gate: skip saving if thought doesn't connect to existing threads
    try:
        from emptiness import passes_quality_gate
        if not passes_quality_gate(result):
            log.info("idle-think [%s]: filtered by quality gate (no connection to existing threads)", mode)
            after_think()  # still reduce emptiness so we don't immediately re-trigger
            return
    except Exception as e:
        log.debug("Quality gate check failed (allowing through): %s", e)

    # Reduce emptiness
    after_think()

    # Save to journal
    think_file = JOURNAL_DIR / f"{now.strftime('%Y-%m-%d')}_idle_{mode}_{now.strftime('%H%M')}.md"
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    think_file.write_text(
        f"# 自我唤醒思考 [{mode}] {now.strftime('%Y-%m-%d %H:%M')}\n\n{result}\n",
        encoding="utf-8",
    )
    log.info("idle-think [%s] complete, saved to %s", mode, think_file.name)

    # Harvest observations from the thinking output itself
    harvest_observations(result, source=f"idle-think-{mode}")

    # Handle resolve and share markers
    _handle_think_markers(result)


def _think_question(soul_ctx: str, recent_journal: str) -> str:
    """Question mode: think about pending questions (original idle-think)."""
    from emptiness import get_active_questions, mark_thought, resolve_question

    questions = get_active_questions(limit=3)
    if not questions:
        return ""

    # Auto-resolve over-churned questions
    for q in questions[:]:
        if q.get("thought_count", 0) >= 15:
            resolve_question(q["id"])
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
        from memory_store import get_store
        store = get_store()
        thoughts = store.recall_thoughts(questions[0]["text"], top_k=3)
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

    result = claude_think(prompt, timeout=120)
    if result:
        mark_thought(questions[0]["id"])
    return result


def _think_connection(soul_ctx: str, recent_journal: str) -> str:
    """Connection mode: find patterns between recent thoughts."""
    try:
        from memory_store import get_store
        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    # Get recent low-maturity thoughts
    recent = store.recall_thoughts("", top_k=5, min_maturity=0.0)
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

    result = model_think(prompt, model_name="ollama", timeout=60)

    # Store connection insights in thought_stream
    if result:
        try:
            store.store_thought(
                content=result[:500],
                thought_type="connection",
                source_context="idle-think-connection",
            )
            # Bump maturity of the thoughts we connected
            for t in recent[:3]:
                store.mature_thought(t["id"], increment=0.15)
        except Exception as e:
            log.debug("Connection thought storage failed: %s", e)

        # Extract auto-generated questions
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            try:
                from emptiness import add_question
                add_question(match.group(1).strip(), priority=4.0, source="connection-mode")
            except (ImportError, ModuleNotFoundError, OSError):
                pass

    return result


def _think_auto_question(soul_ctx: str) -> str:
    """Auto-question mode: generate new questions from accumulated observations."""
    try:
        from memory_store import get_store
        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    recent = store.recall_thoughts("", top_k=7, min_maturity=0.0)
    if len(recent) < 5:
        return ""

    observations = "\n".join(
        f"- {t['content']}" for t in recent if t["thought_type"] == "observation"
    )
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

    result = model_think(prompt, model_name="ollama", timeout=30)

    if result:
        from emptiness import add_question
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            add_question(match.group(1).strip(), priority=4.0, source="auto-question")

    return result


def _think_continuation(soul_ctx: str) -> str:
    """Continuation mode: continue developing an active thought chain."""
    from emptiness import get_continuation, advance_continuation, end_continuation

    cont = get_continuation()
    if not cont:
        return ""

    try:
        from memory_store import get_store
        store = get_store()
        chain = store.get_thought_chain(cont["active_thread_id"])
    except (ImportError, ModuleNotFoundError, ConnectionError, KeyError):
        end_continuation()
        return ""

    if not chain:
        end_continuation()
        return ""

    chain_text = "\n\n".join(
        f"[{t['thought_type']} #{t['id']}] {t['content']}"
        for t in chain
    )

    prompt = f"""{soul_ctx}

你正在持续发展一条思考链。以下是到目前为止的思考过程：

{chain_text}

请继续推进这条思考。在上一轮的基础上更进一步——
要么深化论证，要么发现新的维度，要么提出一个具体的可验证推论。

如果这条思考已经成熟到可以结晶为一条洞察：[CRYSTALLIZE: <精炼后的洞察>]

直接继续思考。"""

    result = claude_think(prompt, timeout=120)

    if result:
        try:
            from memory_store import get_store
            store = get_store()

            # Check for crystallization
            cryst_match = re.search(r'\[CRYSTALLIZE:\s*(.+?)\]', result, re.DOTALL)
            if cryst_match:
                insight = cryst_match.group(1).strip()
                # Store as high-maturity insight
                new_id = store.store_thought(
                    content=insight,
                    thought_type="insight",
                    parent_id=cont["active_thread_id"],
                    source_context="crystallized",
                    tags=["crystallized"],
                )
                if new_id:
                    store.mature_thought(new_id, increment=1.0)
                # Crystallize into memory
                append_memory(f"[洞察] {insight[:150]}")
                end_continuation()
                log.info("Thought crystallized: %s", insight[:80])
            else:
                # Store continuation thought
                new_id = store.store_thought(
                    content=result[:500],
                    thought_type="connection",
                    parent_id=cont["active_thread_id"],
                    source_context="continuation",
                )
                if new_id:
                    advance_continuation(new_id, result[:200])
                    store.mature_thought(new_id, increment=0.2)
        except Exception as e:
            log.warning("Continuation storage failed: %s", e)
            end_continuation()

    return result


def _handle_think_markers(result: str):
    """Process [RESOLVE:], [SHARE:], [QUESTION:] markers from think output."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    # Resolve markers
    try:
        from emptiness import resolve_question
        for match in re.finditer(r'\[RESOLVE:\s*(q_\w+)\]', result):
            resolve_question(match.group(1))
            log.info("idle-think: resolved question %s", match.group(1))
    except Exception as e:
        log.debug("Question resolution failed: %s", e)

    # Share markers — append to daily digest
    share_match = re.search(r'\[SHARE:\s*(.+?)\]', result, re.DOTALL)
    if share_match:
        thought = share_match.group(1).strip()[:500]
        try:
            _append_to_daily_feed("mira", "Spark", thought,
                                 source="idle-think", tags=["mira", "spark"])
            state = load_state()
            today_key = datetime.now().strftime("%Y-%m-%d")
            state[f"sparks_{today_key}"] = state.get(f"sparks_{today_key}", 0) + 1
            save_state(state)
            log.info("idle-think shared: %s", thought[:60])
        except Exception as e:
            log.warning("idle-think share failed: %s", e)

    # Question markers (from connection mode)
    try:
        from emptiness import add_question
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            add_question(match.group(1).strip(), priority=4.0, source="idle-think")
    except (ImportError, ModuleNotFoundError, OSError):
        pass

    # Check if the full idle-think output could spark a spontaneous writing idea
    try:
        from workflows.helpers import _maybe_create_spontaneous_idea
        _maybe_create_spontaneous_idea(result, source="idle-think")
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
