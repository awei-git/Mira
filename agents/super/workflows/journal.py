"""Journal workflow — daily summary of tasks, learning, self-reflection.

Extracted from core.py — pure extraction, no logic changes.
"""

import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

import health_monitor

from config import (
    BRIEFINGS_DIR,
    JOURNAL_DIR,
    MIRA_DIR,
    LOGS_DIR,
    SOCIAL_STATE_DIR,
)
from user_paths import artifact_name_for_user, user_journal_dir

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from memory.soul import (
    load_soul,
    format_soul,
    load_recent_reading_notes,
    _atomic_write as atomic_write,
)
from llm import model_think
from prompts import journal_prompt

from workflows.helpers import (
    _gather_today_tasks,
    _gather_today_skills,
    _mine_za_one,
    _copy_to_briefings,
    _append_to_daily_feed,
    harvest_observations,
)
from agents.shared.agency_report import get_autonomous_actions_since, format_autonomous_agency_report

log = logging.getLogger("mira")
_PREFLIGHT_BLOCK_LOG = Path("/tmp/mira-preflight-blocks.jsonl")


from evolution import traced  # noqa: E402


def _format_joint_garden_section() -> str:
    try:
        garden_path = Path(__file__).resolve().parent.parent.parent / "shared" / "soul" / "joint_garden.md"
        if not garden_path.exists():
            return ""
        garden_text = garden_path.read_text(encoding="utf-8")
        if "## Garden Log" not in garden_text:
            return ""
        log_start = garden_text.index("## Garden Log") + len("## Garden Log")
        log_content = garden_text[log_start:].strip()
        if not log_content:
            return ""
        recent = log_content[-1000:] if len(log_content) > 1000 else log_content
        return "## From the Joint Garden\n\n" + recent.strip() + "\n\n*What do you notice? Add observations via Notes.*"
    except Exception:
        return ""


def _format_daily_shared_memory(user_id: str) -> str:
    try:
        from memory.store import top_joint_attention_memory

        memory = top_joint_attention_memory(user_id=user_id)
    except Exception as e:
        log.debug("Daily shared memory lookup failed: %s", e)
        return ""

    if not memory:
        return ""

    content = re.sub(r"\s+", " ", memory.get("content", "")).strip()
    if not content:
        return ""
    if len(content) > 700:
        content = content[:697].rstrip() + "..."

    source = memory.get("source_type") or "memory"
    score = memory.get("joint_attention_score") or 0.0
    return (
        "## Daily shared memory\n\n"
        f"> [{source}, joint attention {score:.2f}] {content}\n\n"
        "What do you think about this now?"
    )


def _post_weekly_preflight_block_summary(state: dict, user_id: str) -> None:
    week_key = datetime.now(timezone.utc).strftime("%G-W%V")
    if state.get("preflight_block_summary_week") == week_key:
        return
    if not _PREFLIGHT_BLOCK_LOG.exists():
        return

    entries = []
    for line in _PREFLIGHT_BLOCK_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            entries.append(rec)
    if not entries:
        _PREFLIGHT_BLOCK_LOG.write_text("", encoding="utf-8")
        state["preflight_block_summary_week"] = week_key
        return

    reason_counts = Counter(str(rec.get("reason") or "unknown") for rec in entries)
    rule_counts = Counter(str(rec.get("rule_triggered") or "unknown") for rec in entries)
    repeated_rules = [(rule, count) for rule, count in rule_counts.most_common() if count >= 2]
    latest_timestamp = max(str(rec.get("timestamp") or "") for rec in entries)

    lines = [
        "# Preflight Block Summary",
        "",
        f"Week: {week_key}",
        f"Total blocks: {len(entries)}",
        f"Latest block: {latest_timestamp or 'unknown'}",
        "",
        "## Blocks by reason",
    ]
    lines.extend(f"- {reason}: {count}" for reason, count in reason_counts.most_common())
    lines.extend(["", "## Rule patterns"])
    if repeated_rules:
        lines.extend(f"- {rule}: {count} blocks" for rule, count in repeated_rules)
    else:
        lines.append("- No repeated rule triggers.")
    lines.extend(["", "## Recent blocked snippets"])
    for rec in entries[-5:]:
        reason = str(rec.get("reason") or "unknown")
        preview = re.sub(r"\s+", " ", str(rec.get("content_preview") or "")).strip()
        lines.append(f"- {reason}: {preview[:180]}")

    try:
        from notes_bridge import send_to_outbox

        sent = send_to_outbox(
            "\n".join(lines),
            metadata={"title": "Preflight Block Summary", "kind": "preflight_block_summary", "user_id": user_id},
        )
        if sent:
            _PREFLIGHT_BLOCK_LOG.write_text("", encoding="utf-8")
            state["preflight_block_summary_week"] = week_key
            log.info("Preflight block summary sent to Notes: %s", sent)
    except Exception as e:
        log.warning("Failed to post preflight block summary: %s", e)


def _format_mira_day_home_digest(today: str, journal_content: str, spark_count: int = 0) -> str:
    """Convert the private journal artifact into a phone-readable home digest."""
    text = re.sub(r"^#\s+Journal\s+\S+\s*", "", journal_content.strip())
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    body_paragraphs = [p for p in paragraphs if not p.startswith("## ") and not p.startswith("<!--")]

    lead = body_paragraphs[0] if body_paragraphs else text[:700].strip()
    if len(lead) > 900:
        lead = lead[:900].rstrip() + "..."

    question = ""
    for paragraph in reversed(body_paragraphs):
        if "？" in paragraph or "?" in paragraph or "想跟你聊" in paragraph:
            question = paragraph
            break
    if question and len(question) > 420:
        question = question[:420].rstrip() + "..."

    lines = [
        f"# Mira's Day {today}",
        "",
        "## 今天值得看",
        lead or "今天没有形成足够清晰的主线。",
    ]
    if question and question != lead:
        lines.extend(["", "## 想问你的事", question])
    if spark_count:
        lines.extend(["", "## 今日思考素材", f"- {spark_count} 条 sparks 已被合并进今天的判断。"])
    return "\n".join(lines)


@traced("journal", agent="super", budget_seconds=180)
def do_journal(user_id: str = "default"):
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
    _post_weekly_preflight_block_summary(state, user_id)
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

    # 5. Today's sparks — read from sparks log file (not home feed; the 9pm
    # consolidation flow writes Mira's Day digest below from this same source)
    sparks_summary = ""
    spark_entries: list[dict] = []
    try:
        import json as _json

        sparks_path = MIRA_DIR / "users" / user_id / "sparks" / f"{today}.jsonl"
        if sparks_path.exists():
            for line in sparks_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    spark_entries.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue
        if spark_entries:
            sparks_summary = f"今天产生了 {len(spark_entries)} 条 spark：\n\n"
            sparks_summary += "\n---\n".join(s.get("content", "")[:600] for s in spark_entries[:20])
            log.info("Loaded %d sparks for journal context", len(spark_entries))
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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
        from ops.failure_log import get_failure_summary

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
        _tw_state_file = SOCIAL_STATE_DIR / "twitter_state.json"
        if _tw_state_file.exists():
            _tw = _json.loads(_tw_state_file.read_text())
            tw_today = _tw.get(f"tweets_{today}", 0)
            qt_today = _tw.get(f"quotes_{today}", 0)
            reply_q = [r for r in _tw.get("reply_queue", []) if r.get("date", "").startswith(today)]
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
        _ns_file = SOCIAL_STATE_DIR / "notes_state.json"
        if _ns_file.exists():
            _ns = _json.loads(_ns_file.read_text())
            notes_today = _ns.get(f"notes_{today}", 0)
            queue_left = len(_ns.get("queue", []))
            sm_stats += f"- Substack Notes: {notes_today} posted, {queue_left} in queue\n"

        # Substack Comments
        _gs_file = SOCIAL_STATE_DIR / "growth_state.json"
        if _gs_file.exists():
            _gs = _json.loads(_gs_file.read_text())
            comments_today = _gs.get(f"comments_{today}", 0)
            sm_stats += f"- Substack comments: {comments_today}\n"

        sm_stats += (
            "\n目标: X 15条(tweets+quotes+sparks), Notes 8条, Comments 5+条\n"
            "如果实际数据低于目标，在日记中分析原因并提出改进。"
        )
        briefing_summary += sm_stats
    except Exception as e:
        log.warning("Failed to gather social media stats for journal: %s", e)

    # Security alerts: unresolved blocked skill backlog
    security_alerts = ""
    try:
        incidents_path = LOGS_DIR / "security_incidents.jsonl"
        if incidents_path.exists():
            incidents_by_skill: dict[str, dict] = {}
            for line in incidents_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    status = str(rec.get("status", "open")).lower()
                    if not rec.get("blocked") or status != "open":
                        continue
                    skill_name = rec.get("skill_name") or "unknown"
                    ts_raw = rec.get("timestamp") or ""
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = 0.0
                    existing = incidents_by_skill.get(skill_name)
                    if not existing:
                        incidents_by_skill[skill_name] = {
                            "count": 1,
                            "oldest_timestamp": ts_raw,
                            "oldest_ts": ts,
                            "newest_ts": ts,
                            "newest_failure_reason": rec.get("failure_reason") or "blocked",
                        }
                        continue
                    existing["count"] += 1
                    if ts < existing["oldest_ts"]:
                        existing["oldest_ts"] = ts
                        existing["oldest_timestamp"] = ts_raw
                    if ts >= existing["newest_ts"]:
                        existing["newest_ts"] = ts
                        existing["newest_failure_reason"] = rec.get("failure_reason") or "blocked"
                except Exception:
                    pass
            if incidents_by_skill:
                alert_lines = [
                    f"- **{skill_name}**: {incident['count']} open, newest: {incident['newest_failure_reason']}, oldest: {incident['oldest_timestamp']}"
                    for skill_name, incident in sorted(incidents_by_skill.items())
                ]
                security_alerts = "## Security Alerts\n" + "\n".join(alert_lines)
                log.warning("Journal: %d skill(s) with unresolved blocked incidents", len(incidents_by_skill))
    except Exception as e:
        log.warning("Failed to read security incidents for journal: %s", e)

    prompt = journal_prompt(soul_ctx, tasks_summary, skills_summary, briefing_summary, za_fragment=za_fragment)
    journal_text = model_think(prompt, model_name="claude", timeout=120)

    if not journal_text:
        log.error("Journal: Claude returned empty")
        return

    # Autonomous Agency Report — collect scheduled/proactive actions since last journal
    agency_section = ""
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _agency_state = load_state(user_id=user_id)
        _last_ts_str = _agency_state.get(f"journal_{yesterday}")
        _last_run = datetime.fromisoformat(_last_ts_str) if _last_ts_str else None
        _autonomous_actions = get_autonomous_actions_since(_last_run, user_id=user_id)
        agency_section = format_autonomous_agency_report(_autonomous_actions)
    except Exception as _e:
        log.debug("Autonomous agency report failed: %s", _e)

    # Save journal
    security_prefix = f"{security_alerts}\n\n" if security_alerts else ""
    journal_content = f"# Journal {today}\n\n{security_prefix}{journal_text}"
    daily_shared_memory = _format_daily_shared_memory(user_id)
    if daily_shared_memory:
        journal_content = f"{journal_content}\n\n{daily_shared_memory}"
    garden_section = _format_joint_garden_section()
    if garden_section:
        journal_content = f"{journal_content}\n\n{garden_section}"
    if agency_section:
        journal_content = f"{journal_content}\n\n{agency_section}"
    atomic_write(journal_path, journal_content)
    mira_day_content = _format_mira_day_home_digest(today, journal_content, spark_count=len(spark_entries))
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
        state[f"journal_{today}_actor"] = "journal/sonnet"
        save_state(state, user_id=user_id)
    except Exception as e:
        log.warning("Failed to mark journal_%s in state: %s", today, e)

    # Copy to briefings dir so iOS can read it (with verification)
    _copy_to_briefings(artifact_name_for_user(f"{today}_journal.md", user_id), journal_content)

    # Push journal as the canonical "Mira's Day" home item.
    # Uses the same id (feed_mira_{date}) that sparks would have used so any
    # legacy in-progress accumulator is replaced by this consolidated digest.
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        item_id = f"feed_mira_{today.replace('-', '')}"
        legacy_id = f"feed_journal_{today.replace('-', '')}"
        title = f"Mira's Day {today}"
        if bridge.item_exists(item_id):
            # Replace messages with the consolidated journal
            existing = bridge._read_item(item_id) or {}
            existing["title"] = title
            existing["status"] = "done"
            existing["tags"] = sorted(set((existing.get("tags") or []) + ["mira", "journal", "digest"]))
            existing["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            existing["messages"] = [
                {
                    "id": f"{abs(hash(item_id + today)) % 0xFFFFFFFF:08x}",
                    "sender": "agent",
                    "content": mira_day_content,
                    "timestamp": existing["updated_at"],
                    "kind": "text",
                }
            ]
            bridge._write_item(existing)
            bridge._update_manifest(existing)
        else:
            bridge.create_feed(item_id, title, mira_day_content, tags=["mira", "journal", "digest"])
        # Archive the now-redundant "Mira's Day Summary" legacy item if present
        if bridge.item_exists(legacy_id):
            try:
                bridge.update_status(legacy_id, "archived")
            except Exception:
                pass
        log.info("Mira's Day digest written as %s", item_id)
    except Exception as e:
        log.warning("Failed to push Mira's Day digest: %s", e)

    # --- Social media daily report (standalone, NOT inside journal) ---
    try:
        import json as _json

        _tw_state_file = SOCIAL_STATE_DIR / "twitter_state.json"
        _ns_file = SOCIAL_STATE_DIR / "notes_state.json"
        _gs_file = SOCIAL_STATE_DIR / "growth_state.json"

        # Targets — keep in one place so progress lines below stay in sync.
        TARGETS = {"x": 15, "notes": 8, "comments": 5, "bluesky": 5}

        def _bar(have: int, target: int) -> str:
            """Compact progress: '7/15 ⚠' or '15/15 ✓' so the gap is obvious at a glance."""
            mark = "✓" if have >= target else ("⚠" if have > 0 else "✗")
            return f"{have}/{target} {mark}"

        report_lines = [f"# Social Media Report {today}\n"]

        # X/Twitter
        if _tw_state_file.exists():
            _tw = _json.loads(_tw_state_file.read_text())
            tw_today = _tw.get(f"tweets_{today}", 0)
            qt_today = _tw.get(f"quotes_{today}", 0)
            follows_today = _tw.get(f"follows_{today}", 0)
            report_lines.append(f"## X/Twitter")
            report_lines.append(
                f"- Today: tweets {_bar(tw_today, TARGETS['x'])}, quotes {qt_today}, follows {follows_today}"
            )
            try:
                sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
                from twitter import get_performance_summary

                perf = get_performance_summary(_tw)
                # Drop the "Best: ..." segment when its metrics are 0/0 — a
                # "best tweet" with 0 likes/0 replies is just noise that
                # makes the report look broken.
                if perf and "No tweet metrics" not in perf:
                    if "(0 likes, 0 replies)" in perf:
                        perf = perf.split("Best:")[0].rstrip(" .,")
                    if perf:
                        report_lines.append(f"- Last 7 days: {perf}")
            except Exception:
                pass
            # Last 3 tweets — header makes it explicit this is across all
            # time, not today's count.
            history = _tw.get("tweet_history", [])[-3:]
            if history:
                report_lines.append(f"- Recent posts (lifetime, last {len(history)}):")
                for t in history:
                    report_lines.append(f"  - {t.get('text', '')[:100]}")

        # Bluesky — was missing entirely; bg-substack-growth.log shows
        # active posting and replies, so the report was literally hiding
        # output that did happen.
        try:
            _bs_file = SOCIAL_STATE_DIR / "bluesky_state.json"
            if _bs_file.exists():
                _bs = _json.loads(_bs_file.read_text())
                bs_today = _bs.get(f"posts_{today}", 0)
                bs_replies_today = _bs.get(f"replies_{today}", 0)
                report_lines.append(f"\n## Bluesky")
                report_lines.append(f"- Today: posts {_bar(bs_today, TARGETS['bluesky'])}, replies {bs_replies_today}")
        except Exception:
            pass

        # Substack Notes
        if _ns_file.exists():
            _ns = _json.loads(_ns_file.read_text())
            notes_today = _ns.get(f"notes_{today}", 0)
            queue_left = len(_ns.get("queue", []))
            report_lines.append(f"\n## Substack Notes")
            report_lines.append(f"- Today: posted {_bar(notes_today, TARGETS['notes'])}, queue {queue_left}")

        # Substack Comments
        if _gs_file.exists():
            _gs = _json.loads(_gs_file.read_text())
            comments_today = _gs.get(f"comments_{today}", 0)
            report_lines.append(f"\n## Substack Comments")
            report_lines.append(f"- Today: posted {_bar(comments_today, TARGETS['comments'])}")

        report_content = "\n".join(report_lines)

        bridge = Mira(MIRA_DIR, user_id=user_id)
        day_id = f"feed_mira_{today.replace('-', '')}"
        report_id = f"feed_social_report_{today.replace('-', '')}"
        if bridge.item_exists(day_id):
            item = bridge._read_item(day_id) or {}
            messages = item.get("messages") or []
            messages = [m for m in messages if m.get("id") != f"{day_id}_social_report"]
            messages.append(
                {
                    "id": f"{day_id}_social_report",
                    "sender": "agent",
                    "content": report_content,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "kind": "text",
                }
            )
            item["messages"] = messages
            item["tags"] = sorted(set((item.get("tags") or []) + ["social"]))
            item["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            bridge._write_item(item)
            bridge._update_manifest(item)
        if bridge.item_exists(report_id):
            bridge.update_status(report_id, "archived")
        log.info("Social media daily report added to Mira's Day")
    except Exception as e:
        log.warning("Social media daily report failed: %s", e)

    # --- Self-evaluation: score this journal ---
    try:
        from evaluation.scorer import evaluate_journal, record_event

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
        from evaluation.self_iteration import daily_postmortem

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
        from memory.soul import rebuild_memory_index

        rebuild_memory_index(user_id=user_id)
    except Exception as e:
        log.warning("Memory index rebuild after journal failed: %s", e)

    # Run retention policy: distill expiring knowledge, then prune old files
    try:
        from memory.soul import run_retention_policy

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
