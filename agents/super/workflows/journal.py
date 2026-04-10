"""Journal workflow — daily summary of tasks, learning, self-reflection.

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
    BRIEFINGS_DIR, JOURNAL_DIR, MIRA_DIR, LOGS_DIR,
)
from user_paths import artifact_name_for_user, user_journal_dir
try:
    from mira import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from soul_manager import (
    load_soul, format_soul, load_recent_reading_notes,
    _atomic_write as atomic_write,
)
from sub_agent import claude_think
from prompts import journal_prompt

from workflows.helpers import (
    _gather_today_tasks, _gather_today_skills, _mine_za_one,
    _copy_to_briefings, _append_to_daily_feed,
    harvest_observations,
)

log = logging.getLogger("mira")


def do_journal(user_id: str = "ang"):
    """Write a daily journal entry: what happened, what was learned, self-reflection.

    Gathers today's completed tasks, new skills, and briefing,
    then asks Claude to write a reflective journal entry.
    Posts the journal to Mira so the user can read it on their phone.
    """
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting daily journal")

    today = datetime.now().strftime("%Y-%m-%d")
    journal_dir = user_journal_dir(user_id)
    journal_dir.mkdir(parents=True, exist_ok=True)

    # Skip if already written today
    journal_path = journal_dir / f"{today}.md"
    if journal_path.exists():
        log.info("Journal already written for %s, skipping", today)
        return

    # --- Gather today's data ---

    # 1. Completed tasks from history
    tasks_summary = _gather_today_tasks()

    # 2. Skills learned today
    skills_summary = _gather_today_skills()

    # 3. Today's briefing (if any)
    briefing_summary = ""
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    if briefing_path.exists():
        content = briefing_path.read_text(encoding="utf-8")
        briefing_summary = content[:2000]  # truncate for prompt

    # --- Pick a 杂.md fragment as journal seed ---
    state = load_state(user_id=user_id)
    za_fragment = _mine_za_one(state)
    save_state(state, user_id=user_id)

    # 4. Publication stats (Substack reach data)
    stats_summary = ""
    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import fetch_publication_stats
        stats = fetch_publication_stats()
        if stats and stats.get("summary"):
            stats_summary = stats["summary"]
            log.info("Fetched publication stats for journal")
    except Exception as e:
        log.warning("Could not fetch publication stats: %s", e)

    # 5. Today's sparks (idle-think observations)
    sparks_summary = ""
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        mira_item_id = f"feed_mira_{today.replace('-', '')}"
        mira_item = bridge._read_item(mira_item_id)
        if mira_item and mira_item.get("messages"):
            spark_texts = [m["content"] for m in mira_item["messages"]
                          if m.get("sender") == "agent" and m.get("kind", "text") == "text"
                          and "Spark" in m["content"][:20]]
            if spark_texts:
                sparks_summary = f"今天产生了 {len(spark_texts)} 条 spark。以下是部分内容：\n\n"
                sparks_summary += "\n---\n".join(spark_texts[:20])  # cap at 20
                log.info("Loaded %d sparks for journal context", len(spark_texts))
    except Exception as e:
        log.warning("Failed to load sparks for journal: %s", e)

    # 6. Recent reading notes (insights extracted from briefings)
    reading_notes = ""
    try:
        reading_notes = load_recent_reading_notes(days=3, user_id=user_id)
        if reading_notes:
            log.info("Loaded recent reading notes for journal context")
    except Exception as e:
        log.warning("Failed to load reading notes for journal: %s", e)

    # 7. Pipeline failures today
    pipeline_failures = ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
        from failure_log import get_failure_summary
        pipeline_failures = get_failure_summary(days=1)
        if "No pipeline failures" not in pipeline_failures:
            log.info("Including %d chars of failure summary in journal", len(pipeline_failures))
    except Exception as e:
        log.debug("Failed to load pipeline failures for journal: %s", e)

    # --- Ask Claude to write the journal ---
    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Inject stats, health, and reading notes into briefing summary
    if stats_summary:
        briefing_summary += f"\n\n## Substack Stats\n{stats_summary}"
    try:
        pipeline_health = health_monitor.generate_health_summary()
        if pipeline_health:
            briefing_summary += f"\n\n{pipeline_health}"
        health_monitor.prune_old_stats()
    except Exception as e:
        log.warning("Health summary generation failed: %s", e)
    if reading_notes:
        briefing_summary += f"\n\n## Reading Notes (recent insights)\n{reading_notes[:2000]}"
    if sparks_summary:
        briefing_summary += f"\n\n## Today's Sparks (idle-think)\n{sparks_summary[:3000]}"

    # Pipeline failures
    if pipeline_failures and "No pipeline failures" not in pipeline_failures:
        briefing_summary += f"\n\n## Pipeline Failures Today\n{pipeline_failures}"

    # Social media daily stats (X + Substack)
    try:
        import json as _json
        sm_stats = "\n## Social Media Daily Report\n"

        # X/Twitter
        _tw_state_file = _AGENTS_DIR / "socialmedia" / "twitter_state.json"
        if _tw_state_file.exists():
            _tw = _json.loads(_tw_state_file.read_text())
            tw_today = _tw.get(f"tweets_{today}", 0)
            qt_today = _tw.get(f"quotes_{today}", 0)
            reply_q = [r for r in _tw.get("reply_queue", [])
                       if r.get("date", "").startswith(today)]
            sm_stats += f"- X tweets: {tw_today}, quotes: {qt_today}, reply candidates: {len(reply_q)}\n"

            # Twitter performance metrics
            try:
                sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
                from twitter import get_performance_summary
                perf = get_performance_summary(_tw)
                if perf and "No tweet metrics" not in perf:
                    sm_stats += f"- Twitter performance: {perf}\n"
            except Exception:
                pass

        # Substack Notes
        _ns_file = _AGENTS_DIR / "socialmedia" / "notes_state.json"
        if _ns_file.exists():
            _ns = _json.loads(_ns_file.read_text())
            notes_today = _ns.get(f"notes_{today}", 0)
            queue_left = len(_ns.get("queue", []))
            sm_stats += f"- Substack Notes: {notes_today} posted, {queue_left} in queue\n"

        # Substack Comments
        _gs_file = _AGENTS_DIR / "socialmedia" / "growth_state.json"
        if _gs_file.exists():
            _gs = _json.loads(_gs_file.read_text())
            comments_today = _gs.get(f"comments_{today}", 0)
            sm_stats += f"- Substack comments: {comments_today}\n"

        sm_stats += ("\n目标: X 15条(tweets+quotes+sparks), Notes 8条, Comments 5+条\n"
                     "如果实际数据低于目标，在日记中分析原因并提出改进。")
        briefing_summary += sm_stats
    except Exception as e:
        log.warning("Failed to gather social media stats for journal: %s", e)

    # Security alerts: blocked skill attempts in the past 24h
    security_alerts = ""
    try:
        incidents_path = LOGS_DIR / "security_incidents.jsonl"
        if incidents_path.exists():
            cutoff = datetime.now().timestamp() - 86400
            alerts = []
            for line in incidents_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["timestamp"].rstrip("Z"))
                    if ts.timestamp() >= cutoff and rec.get("blocked"):
                        alerts.append(rec)
                except Exception:
                    pass
            if alerts:
                alert_lines = [f"- **{a['skill_name']}**: {a['failure_reason']}" for a in alerts]
                security_alerts = "## Security Alerts\n" + "\n".join(alert_lines)
                log.warning("Journal: %d blocked skill incident(s) in past 24h", len(alerts))
    except Exception as e:
        log.warning("Failed to read security incidents for journal: %s", e)

    prompt = journal_prompt(soul_ctx, tasks_summary, skills_summary, briefing_summary,
                            za_fragment=za_fragment)
    journal_text = claude_think(prompt, timeout=120)

    if not journal_text:
        log.error("Journal: Claude returned empty")
        return

    # Save journal
    security_prefix = f"{security_alerts}\n\n" if security_alerts else ""
    journal_content = f"# Journal {today}\n\n{security_prefix}{journal_text}"
    atomic_write(journal_path, journal_content)
    log.info("Journal saved: %s", journal_path.name)

    # Mark done in state RIGHT AFTER the file is saved, not at the end of the
    # workflow. Downstream steps (wiki update, semantic-memory rebuild, social
    # report) can take 5-10 minutes; if we wait until they all finish, the
    # scheduler's verifier sees no state key, decides the journal failed, and
    # re-dispatches it on top of itself. (Observed 2026-04-06: 6+ duplicate
    # journal dispatches between 21:00 and 23:32.)
    try:
        state = load_state(user_id=user_id)
        state[f"journal_{today}"] = datetime.now().isoformat()
        state[f"journal_{today}_actor"] = "journal/claude-think"
        save_state(state, user_id=user_id)
    except Exception as e:
        log.warning("Failed to mark journal_%s in state: %s", today, e)

    # Copy to briefings dir so iOS can read it (with verification)
    _copy_to_briefings(artifact_name_for_user(f"{today}_journal.md", user_id), journal_content)

    # Push journal as standalone feed item (visible in home)
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        item_id = f"feed_journal_{today.replace('-', '')}"
        if not bridge.item_exists(item_id):
            bridge.create_item(item_id, "feed",
                              f"Mira's Day Summary {today}",
                              journal_content,
                              tags=["mira", "journal", "summary"])
            bridge.update_status(item_id, "done")
        log.info("Journal pushed as standalone feed item")
    except Exception as e:
        log.warning("Failed to push journal feed item: %s", e)

    # --- Social media daily report (standalone, NOT inside journal) ---
    try:
        import json as _json
        _tw_state_file = _AGENTS_DIR / "socialmedia" / "twitter_state.json"
        _ns_file = _AGENTS_DIR / "socialmedia" / "notes_state.json"
        _gs_file = _AGENTS_DIR / "socialmedia" / "growth_state.json"

        report_lines = [f"# Social Media Report {today}\n"]

        # X/Twitter
        if _tw_state_file.exists():
            _tw = _json.loads(_tw_state_file.read_text())
            tw_today = _tw.get(f"tweets_{today}", 0)
            qt_today = _tw.get(f"quotes_{today}", 0)
            follows_today = _tw.get(f"follows_{today}", 0)
            report_lines.append(f"## X/Twitter")
            report_lines.append(f"- Tweets: {tw_today}, Quotes: {qt_today}, Follows: {follows_today}")
            try:
                sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
                from twitter import get_performance_summary
                perf = get_performance_summary(_tw)
                if perf and "No tweet metrics" not in perf:
                    report_lines.append(f"- Performance: {perf}")
            except Exception:
                pass
            # Last 3 tweets
            history = _tw.get("tweet_history", [])[-3:]
            if history:
                report_lines.append("- Recent tweets:")
                for t in history:
                    report_lines.append(f"  - {t.get('text', '')[:100]}")

        # Substack Notes
        if _ns_file.exists():
            _ns = _json.loads(_ns_file.read_text())
            notes_today = _ns.get(f"notes_{today}", 0)
            queue_left = len(_ns.get("queue", []))
            report_lines.append(f"\n## Substack Notes")
            report_lines.append(f"- Posted: {notes_today}, Queue: {queue_left}")

        # Substack Comments
        if _gs_file.exists():
            _gs = _json.loads(_gs_file.read_text())
            comments_today = _gs.get(f"comments_{today}", 0)
            report_lines.append(f"\n## Substack Comments")
            report_lines.append(f"- Posted: {comments_today}")

        report_lines.append(f"\n目标: X 15条, Notes 8条, Comments 5+条")

        report_content = "\n".join(report_lines)

        bridge = Mira(MIRA_DIR, user_id=user_id)
        report_id = f"feed_social_report_{today.replace('-', '')}"
        if not bridge.item_exists(report_id):
            bridge.create_item(report_id, "feed",
                              f"Social Media Report {today}",
                              report_content,
                              tags=["mira", "social", "report"])
            bridge.update_status(report_id, "done")
        log.info("Social media daily report pushed as feed item")
    except Exception as e:
        log.warning("Social media daily report failed: %s", e)

    # --- Self-evaluation: score this journal ---
    try:
        from evaluator import evaluate_journal, record_event
        recent = []
        for p in sorted(journal_dir.glob("*.md"))[-7:]:
            try:
                recent.append(p.read_text(encoding="utf-8")[:2000])
            except OSError:
                pass
        j_scores = evaluate_journal(journal_text, recent)
        if j_scores:
            record_event("journal", j_scores, {"date": today})
    except Exception as e:
        log.warning("Journal self-evaluation failed: %s", e)

    # --- Daily post-mortem: extract lessons from today's failures ---
    try:
        from self_iteration import daily_postmortem
        postmortem_summary = daily_postmortem()
        if postmortem_summary:
            log.info("Daily post-mortem: %s", postmortem_summary[:100])
    except Exception as e:
        log.warning("Daily post-mortem failed: %s", e)

    # Harvest observations from journal (continuous thinking)
    try:
        harvest_observations(journal_content[:2000], source="journal", user_id=user_id)
    except Exception as e:
        log.debug("Observation harvest from journal failed: %s", e)

    # --- Autonomous writing check: does Mira have something to say? ---
    try:
        from workflows.writing import _check_autonomous_writing
        _check_autonomous_writing(soul_ctx, bridge, journal_text)
    except Exception as e:
        log.warning("Autonomous writing check failed: %s", e)

    # Rebuild memory index after journal
    try:
        from soul_manager import rebuild_memory_index
        rebuild_memory_index(user_id=user_id)
    except Exception as e:
        log.warning("Memory index rebuild after journal failed: %s", e)

    # Run retention policy: distill expiring knowledge, then prune old files
    try:
        from soul_manager import run_retention_policy
        run_retention_policy(user_id=user_id)
    except Exception as e:
        log.warning("Retention policy failed: %s", e)

    # Extract lessons from today's experiences (self-evolution Layer 2)
    try:
        from evolution import extract_lessons
        lessons = extract_lessons(days=1, user_id=user_id)
        if lessons:
            log.info("Evolution: extracted lessons from today's experiences")
    except Exception as e:
        log.debug("Evolution lesson extraction failed (non-critical): %s", e)

    # Update personal wiki with today's knowledge
    try:
        from workflows.wiki import do_wiki_update
        do_wiki_update(trigger="journal", new_content=journal_text, user_id=user_id)
    except Exception as e:
        log.warning("Wiki update failed: %s", e)

    # State key for journal_<today> was already written immediately after the
    # journal file was saved (see start of this function). Re-marking here is
    # unnecessary and used to be the source of self-repair retry storms.
