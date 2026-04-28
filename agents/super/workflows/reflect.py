"""Reflect workflow — weekly memory consolidation, worldview evolution.

Extracted from core.py — pure extraction, no logic changes.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    BRIEFINGS_DIR,
    ARTIFACTS_DIR,
    MIRA_DIR,
    WORKSPACE_DIR,
)

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from memory.soul import (
    load_soul,
    format_soul,
    append_memory,
    update_interests,
    update_worldview,
    load_recent_reading_notes,
    _atomic_write as atomic_write,
    _log_change,
)
from llm import claude_think, claude_act
from prompts import reflect_prompt, worldview_evolution_prompt

from workflows.helpers import (
    _gather_recent_briefings,
    _gather_recent_episodes,
    _extract_section,
    _prune_episodes_from_reflect,
)

log = logging.getLogger("mira")


def _prune_worldview_by_decay():
    """Ebbinghaus-style pruning: remove worldview sections not accessed in 60+ days.

    Tracks per-section access metadata in worldview_decay.json.
    A section is "accessed" when the worldview file is loaded during a reflect
    cycle (proxy for relevance). Sections with zero recorded accesses after
    DECAY_DAYS are pruned from worldview.md.

    Permanent/HARD-RULE sections are never pruned.
    """
    from config import WORLDVIEW_FILE
    from datetime import timedelta

    DECAY_DAYS = 60
    PROTECTED_KEYWORDS = {"HARD RULE", "HARD-RULE", "honesty", "quotes", "never"}

    meta_file = WORLDVIEW_FILE.parent / "worldview_decay.json"
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    except (json.JSONDecodeError, OSError):
        meta = {}

    if not WORLDVIEW_FILE.exists():
        return

    worldview_text = WORLDVIEW_FILE.read_text(encoding="utf-8")
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d")

    # Parse sections by ## headings
    sections = []
    current_heading = None
    current_lines = []
    for line in worldview_text.splitlines(keepends=True):
        if line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, "".join(current_lines)))
            current_heading = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading is not None:
        sections.append((current_heading, "".join(current_lines)))

    if not sections:
        return

    # Update access log for all current sections (mark them as "seen today")
    for heading, _ in sections:
        heading_key = heading.strip()
        if heading_key not in meta:
            meta[heading_key] = {
                "first_added": now_str,
                "last_accessed": now_str,
                "access_count": 1,
            }
        else:
            meta[heading_key]["last_accessed"] = now_str
            meta[heading_key]["access_count"] = meta[heading_key].get("access_count", 0) + 1

    # Identify sections to prune (zero accesses beyond creation in 60+ days)
    pruned_headings = []
    surviving_sections = []
    header_lines = []  # Non-section preamble

    # Collect preamble (lines before first ##)
    preamble = ""
    if sections:
        first_idx = worldview_text.find(sections[0][0])
        preamble = worldview_text[:first_idx]

    for heading, body in sections:
        heading_key = heading.strip()
        entry = meta.get(heading_key, {})

        # Never prune hard-rule sections
        if any(kw.lower() in heading.lower() for kw in PROTECTED_KEYWORDS):
            surviving_sections.append((heading, body))
            continue

        # Check decay: if access_count == 1 (only the creation touch) and age > DECAY_DAYS
        first_added_str = entry.get("first_added", now_str)
        try:
            first_added = datetime.strptime(first_added_str, "%Y-%m-%d")
        except ValueError:
            first_added = now
        age_days = (now - first_added).days
        access_count = entry.get("access_count", 1)

        if age_days > DECAY_DAYS and access_count <= 2:
            pruned_headings.append(heading_key)
            log.info(
                "Worldview decay: pruning section '%s' (age=%d days, accesses=%d)",
                heading_key.strip(),
                age_days,
                access_count,
            )
        else:
            surviving_sections.append((heading, body))

    if pruned_headings:
        # Rewrite worldview with surviving sections only
        new_content = preamble + "".join(heading + body for heading, body in surviving_sections)
        update_worldview(new_content)
        log.info("Worldview pruned: removed %d section(s): %s", len(pruned_headings), [h[:40] for h in pruned_headings])
        _log_change("PRUNE_WORLDVIEW", "worldview.md", f"removed {len(pruned_headings)} section(s)")

    # Persist updated metadata
    try:
        atomic_write(meta_file, json.dumps(meta, ensure_ascii=False, indent=2))
    except OSError as e:
        log.warning("Could not save worldview decay metadata: %s", e)


from evolution import traced  # noqa: E402


@traced("reflect", agent="super", budget_seconds=600)
def do_reflect(user_id: str = "ang"):
    """Weekly reflection: consolidate memory, evolve interests, maybe self-initiate."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting reflect cycle")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather recent briefings (last 7 days)
    recent_briefings = _gather_recent_briefings(days=7)

    # Gather recent work from episode archives (not memory.md — it's a cognitive log now)
    recent_work = _gather_recent_episodes(days=7)

    audit_summary: dict = {}
    try:
        from memory.soul_skills import skill_audit_summary

        audit_summary = skill_audit_summary(days=7)
    except Exception as _ae:
        log.warning("Skill audit summary failed: %s", _ae)

    blocked_skill_alerts: list[str] = []
    try:
        from datetime import timedelta
        from config import LOGS_DIR

        _blocked_log = LOGS_DIR / "blocked_skills_log.jsonl"
        if _blocked_log.exists():
            _cutoff = datetime.utcnow() - timedelta(days=7)
            _counts: dict[tuple, int] = {}
            for _line in _blocked_log.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _entry = json.loads(_line)
                except json.JSONDecodeError:
                    continue
                try:
                    _ts = datetime.fromisoformat(_entry["timestamp"].rstrip("Z"))
                except (KeyError, ValueError):
                    continue
                if _ts < _cutoff:
                    continue
                _feed = _entry.get("source_feed", _entry.get("source", "unknown"))
                _reason = _entry.get("block_reason_category", "other")
                _counts[(_feed, _reason)] = _counts.get((_feed, _reason), 0) + 1
            for (_feed, _reason), _n in _counts.items():
                if _n >= 2:
                    blocked_skill_alerts.append(
                        f"Security: {_feed} blocked {_n}x for {_reason} — consider deprioritizing."
                    )
    except Exception as _bae:
        log.warning("Blocked skill pattern check failed: %s", _bae)

    prompt = reflect_prompt(soul_ctx, recent_briefings, recent_work)
    if audit_summary:
        prompt += (
            f"\n\n---\n\n## Skill audit failures this week\n"
            f"{json.dumps(audit_summary, indent=2)}\n\n"
            f"Are any of these patterns worth adding to the audit ruleset?"
        )
    if blocked_skill_alerts:
        prompt += "\n\n---\n\n## Blocked skill feed alerts\n" + "\n".join(blocked_skill_alerts)
    result = claude_think(prompt, timeout=300, tier="heavy")

    if not result:
        log.error("Reflect: Claude returned empty")
        return

    # Parse output sections
    interests_section = _extract_section(result, "Updated Interests")
    memory_section = _extract_section(result, "Updated Memory")
    project_section = _extract_section(result, "Self-Initiated Project")

    if interests_section:
        update_interests(f"# Current Interests\n\n{interests_section}")
        log.info("Interests updated from reflection")

    if memory_section and "no new insights" not in memory_section.lower():
        # Append new insights to memory.md (don't overwrite — it's a cognitive log)
        for line in memory_section.strip().splitlines():
            line = line.strip()
            if line.startswith("- ["):
                append_memory(line, user_id=user_id)

        log.info("New memory insights appended from reflection")

    # Episode pruning — delete old episodes, preserve insights
    pruning_section = _extract_section(result, "Episode Pruning")
    if pruning_section:
        _prune_episodes_from_reflect(pruning_section)

    # --- Evolve worldview ---
    try:
        recent_reading = load_recent_reading_notes(days=14, user_id=user_id)
        from config import WORLDVIEW_FILE

        current_wv = WORLDVIEW_FILE.read_text(encoding="utf-8") if WORLDVIEW_FILE.exists() else ""
        wv_prompt = worldview_evolution_prompt(soul_ctx, current_wv, recent_reading, recent_work)
        new_worldview = claude_think(wv_prompt, timeout=120, tier="heavy")
        if new_worldview and len(new_worldview) > 100:
            update_worldview(new_worldview)
            log.info("Worldview evolved from reflection")
    except Exception as e:
        log.warning("Worldview evolution failed: %s", e)

    # --- Ebbinghaus decay: prune stale worldview sections ---
    try:
        _prune_worldview_by_decay()
    except Exception as e:
        log.warning("Worldview decay pruning failed: %s", e)

    # --- Phase 1: trajectory-derived skill diff ---
    # No-op when ENABLE_TRAJECTORY_V2 is False or no trajectories have
    # been captured yet. When data is available, ask the model for
    # concrete skill/config proposals grounded in the measured reward
    # distribution and tool success rates, then split into auto-apply vs
    # human-review bins (CLAUDE.md hard rule 3).
    try:
        from evolution.config import ENABLE_TRAJECTORY_V2

        if ENABLE_TRAJECTORY_V2:
            from evolution.trajectory_reflect import (
                format_reflect_context,
                parse_skill_diff,
                record_proposals,
            )

            traj_ctx = format_reflect_context(days=7)
            if traj_ctx:
                diff_prompt = (
                    f"{traj_ctx}\n\n---\n\n"
                    "Based on the trajectory evidence above, propose concrete "
                    "skill or config changes that would improve reward. Output "
                    "ONLY a JSON array; each entry must have kind, target, "
                    "rationale, affects, diff. Empty array [] is fine if nothing "
                    "specific stands out. Do NOT propose changes to publish-flow "
                    "guard rails.\n"
                )
                diff_result = claude_think(diff_prompt, timeout=180, tier="light")
                proposals = parse_skill_diff(diff_result or "")
                auto, review = record_proposals(proposals)
                log.info(
                    "Trajectory reflect: %d proposals (%d auto, %d review)",
                    len(proposals),
                    len(auto),
                    len(review),
                )
    except Exception as e:
        log.warning("Trajectory reflect skipped: %s", e)

    if project_section and "nothing right now" not in project_section.lower():
        # The agent wants to start something on its own
        log.info("Self-initiated project proposed: %s", project_section[:100])
        project_slug = f"self-{datetime.now().strftime('%Y%m%d')}"
        project_dir = WORKSPACE_DIR / project_slug
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "proposal.md").write_text(project_section, encoding="utf-8")
        log.info("Self-initiated project saved: %s", project_slug)

        # Execute the project
        self_prompt = (
            f"You are an autonomous AI agent. Here is who you are:\n\n{soul_ctx}\n\n"
            f"---\n\n"
            f"You proposed the following project for yourself:\n\n{project_section}\n\n"
            f"Now execute it. Your workspace is: {project_dir}\n"
            f"Save your output there. Write a summary.txt when done."
        )
        output = claude_act(self_prompt, cwd=project_dir, tier="heavy", agent_id="researcher")
        if output:
            (project_dir / "output.md").write_text(output, encoding="utf-8")
            log.info("Self-initiated project completed: %s", project_slug)
    # --- Self-evaluation: score this reflection ---
    try:
        from evaluation.scorer import evaluate_reflect, record_event, compute_growth_velocity

        old_wv = current_wv if "current_wv" in dir() else ""
        new_wv = new_worldview if "new_worldview" in dir() else ""
        old_int = soul.get("interests", "")
        new_int = interests_section or old_int
        r_scores = evaluate_reflect(old_wv, new_wv, old_int, new_int, reflect_output=result)
        # Also compute growth velocity during reflect
        r_scores.update(compute_growth_velocity())
        if r_scores:
            record_event("reflect", r_scores)
    except Exception as e:
        log.warning("Reflect self-evaluation failed: %s", e)

    # --- Score → Action: diagnose weak areas and generate improvement plan ---
    try:
        from evaluation.scorer import diagnose_scores, generate_improvement_plan

        diagnosis = diagnose_scores()
        if diagnosis["needs_action"]:
            log.info("Score diagnosis: %d low, %d declining", len(diagnosis["low_scores"]), len(diagnosis["declining"]))
            plan = generate_improvement_plan(diagnosis)
            if plan:
                append_memory(
                    f"Self-improvement plan generated: {len(diagnosis['low_scores'])} weak areas identified",
                    user_id=user_id,
                )
                log.info("Improvement plan saved to soul/improvement_plan.json")

            # Feed low scores into action backlog
            try:
                from ops.backlog import ActionBacklog, ActionItem

                backlog = ActionBacklog()
                for ls in diagnosis.get("low_scores", [])[:5]:
                    backlog.add(
                        ActionItem(
                            title=f"Improve {ls['dim']}",
                            description=f"Score {ls['score']:.1f} — below threshold",
                            source="reflect",
                            priority="high" if ls["score"] < 2.0 else "medium",
                            target_dimension=ls["dim"],
                        )
                    )
                for dl in diagnosis.get("declining", [])[:3]:
                    backlog.add(
                        ActionItem(
                            title=f"Stop decline in {dl['dim']}",
                            description=f"Trend: {dl['scores']} (delta={dl['delta']:.2f})",
                            source="reflect",
                            priority="medium",
                            target_dimension=dl["dim"],
                        )
                    )
                backlog.expire_stale()
                log.info("Reflect → backlog: %s", backlog.summary())
            except (ImportError, OSError) as e:
                log.warning("Action backlog update failed: %s", e)
        else:
            log.info("Score diagnosis: all dimensions healthy")
    except (ImportError, OSError) as e:
        log.warning("Score diagnosis failed: %s", e)

    # Rebuild memory index after consolidation
    try:
        from memory.soul import rebuild_memory_index

        rebuild_memory_index(user_id=user_id)
    except Exception as e:
        log.warning("Memory index rebuild after reflect failed: %s", e)

    # --- Knowledge lint: check for contradictions, stale facts, orphans ---
    try:
        from knowledge.lint import lint_all, generate_lint_report

        lint_results = lint_all(user_id=user_id)
        total_issues = sum(len(v) for k, v in lint_results.items() if isinstance(v, list))
        if total_issues > 0:
            report_text = generate_lint_report(lint_results)
            lint_dir = ARTIFACTS_DIR / "lint"
            lint_dir.mkdir(parents=True, exist_ok=True)
            lint_path = lint_dir / f"lint_{datetime.now().strftime('%Y%m%d')}.md"
            atomic_write(lint_path, report_text)
            _log_change("LINT", "knowledge_system", f"{total_issues} issues found")
            log.info("Knowledge lint: %d issues, report saved to %s", total_issues, lint_path.name)
        else:
            log.info("Knowledge lint: all clean")
    except Exception as e:
        log.warning("Knowledge lint failed: %s", e)

    # --- Wiki maintenance: prune stale pages, refresh cross-links ---
    try:
        from workflows.wiki import do_wiki_maintenance

        do_wiki_maintenance(user_id=user_id)
    except Exception as e:
        log.warning("Wiki maintenance failed: %s", e)

    # --- Re-audit all grandfathered skills in the corpus ---
    try:
        from memory.soul_skills import reaudit_all_skills
        from config import SKILLS_DIR

        reaudit_failures = reaudit_all_skills(SKILLS_DIR)
        if reaudit_failures:
            _reaudit_section = (
                "## Skills failing security re-audit (NOT auto-deleted — human review required)\n"
                + "\n".join(f"- {name}" for name in reaudit_failures)
                + "\n\nThese grandfathered skills triggered audit findings. Review each before deciding to keep or remove."
            )
            log.warning("Skill re-audit findings:\n%s", _reaudit_section)
            if result:
                result = result + "\n\n---\n\n" + _reaudit_section
    except Exception as e:
        log.warning("Skill re-audit pass failed: %s", e)

    # --- Weekly self-evaluation report to WA ---
    try:
        from evaluation.scorer import generate_weekly_report

        report = generate_weekly_report()
        if report:
            bridge = Mira(MIRA_DIR, user_id=user_id)
            bridge.create_feed(
                f"feed_reflect_{datetime.now().strftime('%Y%m%d')}",
                "Weekly Reflection",
                report[:2000],
                tags=["reflection"],
            )
            bridge.create_task(
                task_id=f"weekly_eval_{datetime.now().strftime('%Y%m%d')}",
                title="Weekly self-evaluation",
                first_message=report,
                sender="agent",
                origin="auto",
                tags=["evaluation"],
            )
            log.info("Weekly self-evaluation report sent")
    except Exception as e:
        log.warning("Weekly report generation failed: %s", e)

    # --- Proactive self-improvement: reading notes → architecture proposals ---
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "self_improve", str(Path(__file__).parent.parent.parent / "evaluator" / "self_improve.py")
        )
        si_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(si_mod)
        proposals_text = si_mod.run(days=14)
        if proposals_text:
            log.info("Self-improvement proposals generated and pushed to user")
    except (ImportError, OSError) as e:
        log.warning("Self-improvement pipeline failed: %s", e)

    # --- Monthly public self-check article ---
    try:
        from evaluation.scorer import should_publish_monthly_report, generate_monthly_report_article

        if should_publish_monthly_report():
            article = generate_monthly_report_article()
            if article:
                from substack import publish_article

                result = publish_article(
                    title=article["title"],
                    article_text=article["body_markdown"],
                    subtitle="Mira's monthly self-evaluation scores and trajectory",
                )
                log.info("Monthly self-check article published: %s", result[:100] if result else "")
    except Exception as e:
        log.warning("Monthly self-check publish failed: %s", e)

    # --- Self-evolution Layer 3: propose strategy variant based on reward trends ---
    try:
        from evolution import propose_strategy_variant, evaluate_variant

        # First evaluate any active variant from last week
        variant_dir = Path(__file__).resolve().parent.parent.parent / "shared" / "soul" / "variants"
        if variant_dir.exists():
            for vf in variant_dir.glob("*.json"):
                try:
                    v = json.loads(vf.read_text(encoding="utf-8"))
                    if v.get("status") == "proposed":
                        result = evaluate_variant(v["id"])
                        if result:
                            log.info("Evolution: evaluated variant '%s': %s", v["id"], result.get("status"))
                except Exception:
                    pass
        # Then propose a new one
        variant = propose_strategy_variant(user_id=user_id)
        if variant:
            log.info("Evolution: proposed strategy variant '%s'", variant.get("id", ""))
    except Exception as e:
        log.debug("Evolution strategy mutation failed (non-critical): %s", e)

    state = load_state(user_id=user_id)
    state["last_reflect"] = datetime.now().isoformat()
    save_state(state, user_id=user_id)
