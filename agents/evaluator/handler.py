"""Evaluator agent — hierarchical performance assessment.

Architecture:
  Layer 1: Per-agent scorecards (deterministic, grounded)
  Layer 2: Super-agent (orchestration) scorecard
  Layer 3: Mira aggregate (weighted rollup)
  Layer 4: Top-down improvement (diagnosis → targeted sub-agent fixes)

Scoring philosophy:
  - Only GROUNDED metrics drive action (task outcomes, file existence, timing)
  - LLM-as-judge is recorded but NEVER triggers improvement plans
  - Different eval model from the agent being evaluated (avoid self-preference)
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent.parent / "lib"
_SUPER = Path(__file__).resolve().parent.parent / "super"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
if str(_SUPER) not in sys.path:
    sys.path.insert(0, str(_SUPER))

log = logging.getLogger("evaluator_agent")

_SOUL_DIR = _SHARED / "soul"
_SCORECARDS_DIR = _SOUL_DIR / "scorecards"
_IMPROVEMENT_FILE = _SOUL_DIR / "improvement_plan.json"
_IMPROVEMENT_HISTORY = Path(__file__).parent / "improvement_history.jsonl"
_MISCALIBRATION_COUNTS_FILE = Path(__file__).parent / "miscalibration_counts.json"
_VARIANCE_STATE_FILE = Path(__file__).parent / "variance_state.json"
_VARIANCE_STD_THRESHOLD = 0.05
_VARIANCE_CONSECUTIVE_K = 3

# ---------------------------------------------------------------------------
# Per-agent criteria — each agent is scored on what MATTERS for its role
# All metrics are deterministic (no LLM self-eval)
# ---------------------------------------------------------------------------

AGENT_CRITERIA = {
    "writer": {
        "description": "Writing pipeline — articles, essays, stories",
        "metrics": {
            "publish_rate": {
                "description": "Articles that made it to Substack (published / attempted)",
                "ground_truth_type": "outcome",
            },
            "review_convergence": {
                "description": "Average review score across writing pipeline rounds",
                "ground_truth_type": "consensus_proxy",
            },
            "word_count_avg": {
                "description": "Average article length (proxy for depth)",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "coder": {
        "description": "Debug, code review, quick fixes",
        "metrics": {
            "task_success": {"description": "Tasks completed without error / total", "ground_truth_type": "outcome"},
            "bug_found_rate": {
                "description": "For review tasks: issues detected per review",
                "ground_truth_type": "consensus_proxy",
            },
            "syntax_valid": {"description": "Generated code passes syntax check", "ground_truth_type": "outcome"},
        },
    },
    "explorer": {
        "description": "Feed fetching, briefings, research",
        "metrics": {
            "briefing_produced": {
                "description": "Briefings successfully generated / attempts",
                "ground_truth_type": "outcome",
            },
            "source_diversity": {"description": "Unique sources per briefing", "ground_truth_type": "consensus_proxy"},
            "reading_notes_produced": {
                "description": "Reading notes extracted per explore cycle",
                "ground_truth_type": "outcome",
            },
        },
    },
    "researcher": {
        "description": "Deep research, math proofs, iterative investigation",
        "metrics": {
            "task_success": {"description": "Research tasks completed / attempted", "ground_truth_type": "outcome"},
            "iteration_depth": {
                "description": "Average iterations per research task (more = deeper)",
                "ground_truth_type": "consensus_proxy",
            },
            "output_length": {
                "description": "Average output length (proxy for thoroughness)",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "analyst": {
        "description": "Market analysis, competitive intelligence",
        "metrics": {
            "task_success": {"description": "Analysis tasks completed / attempted", "ground_truth_type": "outcome"},
            "output_length": {"description": "Average output length", "ground_truth_type": "consensus_proxy"},
        },
    },
    "discussion": {
        "description": "Conversational responses as Mira",
        "metrics": {
            "response_rate": {"description": "Messages that got a response / total", "ground_truth_type": "outcome"},
            "response_time_avg": {"description": "Average seconds to respond", "ground_truth_type": "outcome"},
        },
    },
    "podcast": {
        "description": "Audio generation and publishing",
        "metrics": {
            "episodes_published": {
                "description": "Episodes successfully published to RSS",
                "ground_truth_type": "outcome",
            },
            "audio_generated": {
                "description": "Audio files successfully generated / attempted",
                "ground_truth_type": "outcome",
            },
        },
    },
    "secret": {
        "description": "Private tasks via local oMLX",
        "metrics": {
            "task_success": {"description": "Tasks completed / attempted", "ground_truth_type": "outcome"},
            "stayed_local": {
                "description": "No cloud API calls detected (always should be 100%)",
                "ground_truth_type": "outcome",
            },
        },
    },
    "general": {
        "description": "Catch-all — questions, search, analysis, misc tasks",
        "metrics": {
            "task_success": {"description": "Tasks completed without error / total", "ground_truth_type": "outcome"},
            "output_length": {
                "description": "Average output length (proxy for effort)",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "socialmedia": {
        "description": "Substack engagement — notes, comments, growth",
        "metrics": {
            "notes_posted": {"description": "Substack notes successfully posted", "ground_truth_type": "outcome"},
            "comments_replied": {
                "description": "Comments replied to / flagged for reply",
                "ground_truth_type": "outcome",
            },
        },
    },
    "super": {
        "description": "Orchestrator — routing, planning, lifecycle",
        "metrics": {
            "routing_accuracy": {
                "description": "Tasks routed to correct agent (no re-routes needed)",
                "ground_truth_type": "consensus_proxy",
            },
            "plan_quality": {
                "description": "Multi-step plans that executed without step failures",
                "ground_truth_type": "outcome",
            },
            "cycle_time": {"description": "Average main loop duration (target: < 5s)", "ground_truth_type": "outcome"},
            "crash_rate": {"description": "Cycles that crashed / total cycles", "ground_truth_type": "outcome"},
            "heartbeat_uptime": {
                "description": "Heartbeat updated within 3min window (%)",
                "ground_truth_type": "outcome",
            },
            "stuck_tasks": {
                "description": "Tasks stuck in dispatched/running state > 30min",
                "ground_truth_type": "outcome",
            },
            "timeout_rate": {"description": "Tasks that timed out / total dispatched", "ground_truth_type": "outcome"},
        },
    },
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

SUPER_CRITERIA = {
    "description": "Orchestrator — routing, planning, lifecycle",
    "metrics": {
        "routing_accuracy": {
            "description": "Tasks routed to correct agent (no re-routes needed)",
            "ground_truth_type": "consensus_proxy",
        },
        "plan_quality": {
            "description": "Multi-step plans that executed without step failures",
            "ground_truth_type": "outcome",
        },
        "cycle_time": {"description": "Average main loop duration (target: < 5s)", "ground_truth_type": "outcome"},
        "crash_rate": {"description": "Cycles that crashed / total cycles", "ground_truth_type": "outcome"},
        "heartbeat_uptime": {"description": "Heartbeat updated within 3min window (%)", "ground_truth_type": "outcome"},
        "stuck_tasks": {
            "description": "Tasks stuck in dispatched/running state > 30min",
            "ground_truth_type": "outcome",
        },
        "timeout_rate": {"description": "Tasks that timed out / total dispatched", "ground_truth_type": "outcome"},
    },
}

_UNIVERSAL_METRIC_TYPES: dict[str, str] = {
    "task_success": "outcome",
    "guard_fire_rate": "outcome",
    "output_length_avg": "consensus_proxy",
}


def _get_metric_type(agent_name: str, metric_key: str) -> str:
    """Return the ground_truth_type for a metric, falling back to universal defaults."""
    metric_def = AGENT_CRITERIA.get(agent_name, {}).get("metrics", {}).get(metric_key)
    if isinstance(metric_def, dict):
        return metric_def.get("ground_truth_type", "consensus_proxy")
    return _UNIVERSAL_METRIC_TYPES.get(metric_key, "consensus_proxy")


# ---------------------------------------------------------------------------
# Score computation — all deterministic, from task history and logs
# ---------------------------------------------------------------------------


def _load_task_history(days: int = 7) -> list[dict]:
    """Load recent task records from history.jsonl."""
    from config import TASKS_DIR

    history_file = TASKS_DIR / "history.jsonl"
    if not history_file.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    for line in history_file.read_text(encoding="utf-8").strip().splitlines():
        try:
            r = json.loads(line)
            ts = r.get("completed_at") or r.get("dispatched_at", "")
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.replace(tzinfo=None) > cutoff:
                    records.append(r)
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def _count_guard_fires(days: int) -> int:
    """Count GUARD_FIRED log entries over the scoring window."""
    from config import LOGS_DIR

    count = 0
    for i in range(days):
        date_str = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        log_file = LOGS_DIR / f"{date_str}.log"
        if log_file.exists():
            try:
                count += log_file.read_text(encoding="utf-8", errors="replace").count("GUARD_FIRED")
            except OSError:
                pass
    return count


def _count_audit_guard_fires(days: int) -> int:
    """Count guard fire entries from scaffolding_audit.jsonl over the scoring window."""
    try:
        from config import MIRA_ROOT

        audit_log = MIRA_ROOT / "logs" / "scaffolding_audit.jsonl"
        if not audit_log.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        count = 0
        for line in audit_log.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.replace(tzinfo=None) > cutoff.replace(tzinfo=None):
                        count += 1
            except (json.JSONDecodeError, ValueError):
                continue
        return count
    except Exception:
        return 0


def _count_scaffolding_rejections(days: int) -> int:
    """Count scaffolding rejection entries from scaffolding_rejections.jsonl over the scoring window."""
    try:
        from config import MIRA_ROOT

        rejection_log = MIRA_ROOT / "logs" / "scaffolding_rejections.jsonl"
        if not rejection_log.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        count = 0
        for line in rejection_log.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("ts", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.replace(tzinfo=timezone.utc) >= cutoff:
                        count += 1
            except (json.JSONDecodeError, ValueError):
                continue
        return count
    except Exception:
        return 0


def score_agent(agent_name: str, days: int = 7) -> dict:
    """Score a specific agent based on its task history. Returns grounded metrics."""
    criteria = AGENT_CRITERIA.get(agent_name)
    if not criteria:
        return {"agent": agent_name, "error": "no criteria defined"}

    history = _load_task_history(days)
    # Match by explicit 'agent' field (preferred) or exact agent name in tags
    agent_tasks = [t for t in history if t.get("agent") == agent_name or agent_name in t.get("tags", [])]

    total = len(agent_tasks)
    if total == 0:
        return {
            "agent": agent_name,
            "period_days": days,
            "task_count": 0,
            "scores": {},
            "note": "no tasks in period",
        }

    scores = {}

    # Universal: task success rate
    succeeded = sum(1 for t in agent_tasks if t.get("status") == "done")
    scores["task_success"] = round(succeeded / total, 3) if total else 0

    # Universal: guard fire rate — scaffold interventions over the window.
    # High guard_fire_rate + high task_success_rate = silent degradation signature.
    guard_fires = _count_guard_fires(days)
    scores["guard_fire_rate"] = round(guard_fires / max(total, 1), 3)

    # Universal: average output length
    lengths = []
    for t in agent_tasks:
        summary = t.get("summary", "")
        if summary:
            lengths.append(len(summary))
    if lengths:
        scores["output_length_avg"] = round(sum(lengths) / len(lengths))

    # Agent-specific metrics would go here
    # (each agent type can register custom scoring functions)

    # Persist scores derived from task logs with log_verified evidence
    try:
        from evaluation.storage import update_weakness_score

        update_weakness_score(f"{agent_name}.task_success", scores["task_success"], evidence_type="log_verified")
        update_weakness_score(f"{agent_name}.guard_fire_rate", scores["guard_fire_rate"], evidence_type="log_verified")
        if "output_length_avg" in scores:
            update_weakness_score(
                f"{agent_name}.output_length_avg", scores["output_length_avg"], evidence_type="log_verified"
            )
    except Exception as _e:
        log.debug("update_weakness_score failed for %s: %s", agent_name, _e)

    audit_guard_fires = _count_audit_guard_fires(days)
    result = {
        "agent": agent_name,
        "period_days": days,
        "task_count": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "success_rate": scores.get("task_success", 0),
        "guard_fires": guard_fires,
        "guard_fire_rate": scores.get("guard_fire_rate", 0),
        "guard_fired_count": audit_guard_fires,
        "scores": scores,
    }

    # Update validated_at for this agent's skills when scoring positively
    if scores.get("task_success", 0) >= 0.8:
        _update_agent_skill_validation(agent_name)

    return result


def score_super(days: int = 7) -> dict:
    """Score the super agent (orchestrator) from logs and state."""
    from config import LOGS_DIR

    scores = {}

    # Crash rate from logs
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{today}.log"
    if log_file.exists():
        content = log_file.read_text(encoding="utf-8", errors="replace")
        wakes = content.count("Mira Agent wake")
        crashes = content.count("Mira failed:") + content.count("Traceback")
        scores["crash_rate"] = round(crashes / max(wakes, 1), 3)
        scores["cycles_today"] = wakes
    else:
        scores["crash_rate"] = 0
        scores["cycles_today"] = 0

    # Heartbeat freshness
    from config import MIRA_BRIDGE_DIR

    hb_file = MIRA_BRIDGE_DIR / "heartbeat.json"
    if hb_file.exists():
        try:
            hb = json.loads(hb_file.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(hb["timestamp"].replace("Z", "+00:00"))
            age = (datetime.now(ts.tzinfo) - ts).total_seconds()
            scores["heartbeat_age_seconds"] = round(age)
            scores["heartbeat_ok"] = age < 180
        except (json.JSONDecodeError, KeyError, ValueError):
            scores["heartbeat_ok"] = False

    # Routing: count tasks that needed re-routing or clarification
    history = _load_task_history(days)
    total_tasks = len(history)
    clarify_tasks = sum(1 for t in history if "clarify" in t.get("tags", []))
    if total_tasks > 0:
        scores["routing_accuracy"] = round(1 - clarify_tasks / total_tasks, 3)
    scores["total_tasks"] = total_tasks

    # Timeout and stuck tasks
    timeout_tasks = sum(1 for t in history if t.get("status") == "timeout")
    error_tasks = sum(1 for t in history if t.get("status") == "error")
    scores["timeout_count"] = timeout_tasks
    scores["error_count"] = error_tasks
    if total_tasks > 0:
        scores["timeout_rate"] = round(timeout_tasks / total_tasks, 3)
        scores["error_rate"] = round(error_tasks / total_tasks, 3)

    # Check for currently stuck tasks (dispatched > 30 min ago, not completed)
    from config import TASKS_DIR

    status_file = TASKS_DIR / "status.json"
    if status_file.exists():
        try:
            active = json.loads(status_file.read_text(encoding="utf-8"))
            now = datetime.now()
            stuck = 0
            for t in active:
                if t.get("status") in ("dispatched", "running"):
                    dispatched = t.get("dispatched_at", "")
                    if dispatched:
                        dt = datetime.fromisoformat(dispatched.replace("Z", "+00:00"))
                        age_min = (now - dt.replace(tzinfo=None)).total_seconds() / 60
                        if age_min > 30:
                            stuck += 1
            scores["stuck_tasks"] = stuck
        except (json.JSONDecodeError, OSError):
            pass

    return {
        "component": "super",
        "period_days": days,
        "scores": scores,
    }


def _emit_variance_warning(std_dev: float, consecutive: int) -> None:
    notes_inbox = _SUPER / "notes_inbox"
    try:
        notes_inbox.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        note_path = notes_inbox / f"evaluator_variance_warning_{ts}.md"
        note_path.write_text(
            f"EVALUATOR_DEGRADATION_WARNING\n\n"
            f"Evaluator score variance has been near-zero for {consecutive}+ cycles — "
            f"possible rubber-stamp degradation.\n\n"
            f"std_dev={std_dev:.4f} (threshold: {_VARIANCE_STD_THRESHOLD}) "
            f"over {consecutive} consecutive cycles.\n\n"
            f"Action: review recent scorecards in lib/soul/scorecards/ for score clustering. "
            f"If all agents are scoring uniformly high, the evaluator may have lost discriminative signal.",
            encoding="utf-8",
        )
    except OSError as e:
        log.debug("Could not write variance warning note: %s", e)


def _check_score_variance(success_rates: list[float]) -> None:
    state: dict = {}
    if _VARIANCE_STATE_FILE.exists():
        try:
            state = json.loads(_VARIANCE_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if len(success_rates) < 2:
        state["consecutive_low_variance"] = 0
        try:
            _VARIANCE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return

    mean = sum(success_rates) / len(success_rates)
    variance = sum((x - mean) ** 2 for x in success_rates) / (len(success_rates) - 1)
    std_dev = variance**0.5
    state["last_std_dev"] = round(std_dev, 4)

    if std_dev < _VARIANCE_STD_THRESHOLD:
        state["consecutive_low_variance"] = state.get("consecutive_low_variance", 0) + 1
    else:
        state["consecutive_low_variance"] = 0

    try:
        _VARIANCE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.debug("Could not save variance state: %s", e)

    consecutive = state["consecutive_low_variance"]
    if consecutive >= _VARIANCE_CONSECUTIVE_K:
        log.warning(
            "EVALUATOR_DEGRADATION_WARNING std_dev=%.4f consecutive_low_variance_cycles=%d — "
            "possible rubber-stamp degradation",
            std_dev,
            consecutive,
        )
        _emit_variance_warning(std_dev, consecutive)


def score_all(days: int = 7) -> dict:
    """Full hierarchical assessment: per-agent + super + aggregate."""
    result = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "agents": {},
        "super": {},
        "aggregate": {},
    }

    # Score each agent
    all_success_rates = []
    all_task_counts = []
    for agent_name in AGENT_CRITERIA:
        card = score_agent(agent_name, days)
        result["agents"][agent_name] = card
        if card["task_count"] > 0:
            all_success_rates.append(card["success_rate"])
            all_task_counts.append(card["task_count"])

    # Score super
    result["super"] = score_super(days)

    # Aggregate: Mira-level
    agg = {}
    if all_success_rates:
        # Weighted by task count
        total_tasks = sum(all_task_counts)
        weighted_success = sum(r * c for r, c in zip(all_success_rates, all_task_counts))
        agg["overall_success_rate"] = round(weighted_success / total_tasks, 3)
    agg["total_tasks"] = sum(all_task_counts) if all_task_counts else 0
    agg["active_agents"] = sum(1 for c in result["agents"].values() if c["task_count"] > 0)
    agg["crash_rate"] = result["super"]["scores"].get("crash_rate", 0)
    agg["heartbeat_ok"] = result["super"]["scores"].get("heartbeat_ok", True)

    # Cost
    try:
        from llm import usage_summary

        usage = usage_summary()
        agg["daily_cost_usd"] = usage.get("total_cost_usd", 0)
        agg["daily_tokens"] = usage.get("total_tokens", 0)
        agg["daily_calls"] = usage.get("calls", 0)
    except (ImportError, OSError):
        pass

    # Stale weakness-score check
    from config import EVAL_SCORE_TTL_DAYS

    stale_count, low_confidence_agents = _compute_score_staleness(EVAL_SCORE_TTL_DAYS)
    if stale_count > 0:
        log.warning("STALE_SCORES excluded=%d ttl_days=%d", stale_count, EVAL_SCORE_TTL_DAYS)
    agg["stale_score_count"] = stale_count
    low_conf_list = [a for a, lc in low_confidence_agents.items() if lc]
    if low_conf_list:
        agg["low_confidence_agents"] = low_conf_list

    from config import SCAFFOLDING_CATCH_RATE_WINDOW_HOURS

    _catch_rate = _compute_scaffolding_catch_rate(SCAFFOLDING_CATCH_RATE_WINDOW_HOURS)
    agg["scaffolding_catch_rate_per_hour"] = _catch_rate
    if _catch_rate > 3.0:
        log.warning(
            "SCAFFOLDING_CATCH_RATE_HIGH catches_per_hour=%.2f window_hours=%d"
            " — model output quality signal, not scaffolding success",
            _catch_rate,
            SCAFFOLDING_CATCH_RATE_WINDOW_HOURS,
        )
        agg["scaffolding_quality_flag"] = True

    _rejection_threshold = 0.2
    try:
        from config import SCAFFOLDING_REJECTION_THRESHOLD

        _rejection_threshold = SCAFFOLDING_REJECTION_THRESHOLD
    except (ImportError, AttributeError):
        pass
    _rejection_count = _count_scaffolding_rejections(days)
    _rejection_rate = round(_rejection_count / max(agg.get("total_tasks", 1), 1), 3)
    agg["scaffolding_rejection_count"] = _rejection_count
    agg["scaffolding_rejection_rate"] = _rejection_rate
    if _rejection_rate > _rejection_threshold:
        log.warning(
            "SCAFFOLDING_REJECTION_RATE_HIGH rate=%.3f count=%d threshold=%.2f"
            " — model output failing content guards at elevated rate",
            _rejection_rate,
            _rejection_count,
            _rejection_threshold,
        )
        agg["scaffolding_rejection_warning"] = True

    result["aggregate"] = agg

    _check_score_variance(all_success_rates)

    # Skill staleness scan
    stale = scan_stale_skills()
    if stale:
        for w in stale:
            log.warning("SKILL_STALE skill=%r source=%s reason=%s", w["skill"], w["source"], w["reason"])
    result["stale_skills"] = stale

    # Skill marginalization scan
    marginalized = scan_marginalized_skills()
    if marginalized:
        for m in marginalized:
            log.warning(
                "SKILL_MARGINALIZED skill=%r reason=%s use_count=%d — retain or remove",
                m["skill"],
                m["reason"],
                m.get("use_count", 0),
            )
    result["marginalized_skills"] = marginalized

    # Save scorecard
    _SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    scorecard_path = _SCORECARDS_DIR / f"{today}.json"
    scorecard_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


# ---------------------------------------------------------------------------
# Skill staleness instrumentation
# ---------------------------------------------------------------------------

_AGENTS_DIR = Path(__file__).resolve().parent.parent


def _compute_scaffolding_catch_rate(window_hours: int) -> float:
    """Return catches/hour from scaffolding_catches.jsonl over the given window."""
    try:
        from config import MIRA_ROOT

        catch_log = MIRA_ROOT / "logs" / "scaffolding_catches.jsonl"
        if not catch_log.exists():
            return 0.0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        count = 0
        for line in catch_log.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("ts", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts >= cutoff:
                        count += 1
            except (json.JSONDecodeError, ValueError):
                continue
        return round(count / max(window_hours, 1), 3)
    except Exception:
        return 0.0


def _compute_score_staleness(ttl_days: int) -> tuple[int, dict[str, bool]]:
    """Return (stale_count, low_confidence_agents).

    Loads weakness_scores from storage, classifies entries with last_updated
    older than ttl_days as stale, and marks agents where >50% of their entries
    are stale as low-confidence.
    """
    try:
        from evaluation.storage import load_scores

        data = load_scores()
    except Exception:
        return 0, {}

    weakness_scores = data.get("weakness_scores", {})
    cutoff = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y-%m-%d")

    per_agent: dict[str, dict[str, int]] = {}
    stale_count = 0

    for metric, entry in weakness_scores.items():
        if "." not in metric:
            continue
        agent = metric.split(".")[0]
        if agent not in per_agent:
            per_agent[agent] = {"total": 0, "stale": 0}
        per_agent[agent]["total"] += 1
        last_updated = entry.get("last_updated", "")
        if not last_updated or last_updated < cutoff:
            per_agent[agent]["stale"] += 1
            stale_count += 1

    low_confidence = {
        agent: counts["stale"] / counts["total"] > 0.5 for agent, counts in per_agent.items() if counts["total"] > 0
    }
    return stale_count, low_confidence


def _update_agent_skill_validation(agent_name: str) -> None:
    """Set validated_at=now for skills in this agent's index that are stale or unset."""
    from config import SKILL_STALENESS_DAYS

    index_path = _AGENTS_DIR / agent_name / "skills" / "index.json"
    if not index_path.exists():
        return
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
        now_str = datetime.now(timezone.utc).isoformat()
        cutoff = datetime.now(timezone.utc) - timedelta(days=SKILL_STALENESS_DAYS)
        changed = False
        for entry in entries:
            vat = entry.get("validated_at")
            if not vat:
                entry["validated_at"] = now_str
                changed = True
            else:
                try:
                    ts = datetime.fromisoformat(vat.replace("Z", "+00:00"))
                    if ts < cutoff:
                        entry["validated_at"] = now_str
                        changed = True
                except ValueError:
                    entry["validated_at"] = now_str
                    changed = True
        if changed:
            index_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError) as e:
        log.debug("Could not update skill validation for %s: %s", agent_name, e)


def scan_stale_skills() -> list[dict]:
    """Scan all skill indices and return SKILL_STALE entries for stale or unvalidated skills."""
    from config import SKILL_STALENESS_DAYS, SKILLS_DIR

    threshold = timedelta(days=SKILL_STALENESS_DAYS)
    now = datetime.now(timezone.utc)
    cutoff = now - threshold
    warnings = []

    def _check_index(index_path: Path, source: str) -> None:
        if not index_path.exists():
            return
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for entry in entries:
            name = entry.get("name", "unknown")
            vat = entry.get("validated_at")
            if not vat:
                warnings.append(
                    {
                        "event": "SKILL_STALE",
                        "skill": name,
                        "source": source,
                        "reason": "validated_at absent",
                        "validated_at": None,
                    }
                )
            else:
                try:
                    ts = datetime.fromisoformat(vat.replace("Z", "+00:00"))
                    if ts < cutoff:
                        age_days = (now - ts).days
                        warnings.append(
                            {
                                "event": "SKILL_STALE",
                                "skill": name,
                                "source": source,
                                "reason": f"not validated in {age_days}d (threshold: {SKILL_STALENESS_DAYS}d)",
                                "validated_at": vat,
                            }
                        )
                except ValueError:
                    warnings.append(
                        {
                            "event": "SKILL_STALE",
                            "skill": name,
                            "source": source,
                            "reason": "invalid validated_at format",
                            "validated_at": vat,
                        }
                    )

    _check_index(SKILLS_DIR / "index.json", "learned")
    for agent_index in _AGENTS_DIR.glob("*/skills/index.json"):
        agent_name = agent_index.parent.parent.name
        _check_index(agent_index, f"agent:{agent_name}")

    return warnings


def scan_marginalized_skills() -> list[dict]:
    """Flag skills that have never been invoked (after 14 days) or not invoked in >30 days."""
    skills_index = _SOUL_DIR / "learned" / "index.json"
    if not skills_index.exists():
        return []
    try:
        index = json.loads(skills_index.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    now = datetime.now(timezone.utc)
    marginalized = []
    for entry in index:
        name = entry.get("name", "unknown")
        last_invoked = entry.get("last_invoked")
        created = entry.get("created")

        if last_invoked is None:
            if created:
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_days = (now - created_dt).days
                    if age_days > 14:
                        marginalized.append(
                            {
                                "skill": name,
                                "reason": f"never invoked, added {age_days}d ago",
                                "last_invoked": None,
                                "use_count": entry.get("use_count", 0),
                            }
                        )
                except ValueError:
                    pass
        else:
            try:
                invoked_dt = datetime.fromisoformat(last_invoked.replace("Z", "+00:00"))
                if invoked_dt.tzinfo is None:
                    invoked_dt = invoked_dt.replace(tzinfo=timezone.utc)
                age_days = (now - invoked_dt).days
                if age_days > 30:
                    marginalized.append(
                        {
                            "skill": name,
                            "reason": f"last invoked {age_days}d ago",
                            "last_invoked": last_invoked,
                            "use_count": entry.get("use_count", 0),
                        }
                    )
            except ValueError:
                pass

    return marginalized


# ---------------------------------------------------------------------------
# Improvement plan outcome tracking
# ---------------------------------------------------------------------------


def _extract_baseline_scores(assessment: dict) -> dict[str, float]:
    """Extract per-agent success rates from an assessment for baseline comparison."""
    scores = {}
    for name, card in assessment.get("agents", {}).items():
        if card.get("task_count", 0) > 0:
            scores[name] = card["success_rate"]
    # Include aggregate
    agg = assessment.get("aggregate", {})
    if "overall_success_rate" in agg:
        scores["_overall"] = agg["overall_success_rate"]
    if "crash_rate" in agg:
        scores["_crash_rate"] = agg["crash_rate"]
    return scores


def save_improvement_plan_with_baseline(plan_data: dict, assessment: dict):
    """Save improvement plan with baseline scores for later comparison."""
    baseline = _extract_baseline_scores(assessment)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "problems": plan_data.get("problems", []),
        "weak_agents": plan_data.get("weak_agents", []),
        "baseline_scores": baseline,
        "status": "pending",
    }
    with open(_IMPROVEMENT_HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.info("Saved improvement baseline with %d agent scores", len(baseline))


def check_improvement_outcomes(current_assessment: dict) -> str:
    """Compare current scores against previous improvement plan baselines.

    Returns a summary of which improvements worked and which didn't.
    """
    if not _IMPROVEMENT_HISTORY.exists():
        return ""

    plans = []
    for line in _IMPROVEMENT_HISTORY.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                plans.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not plans:
        return ""

    current = _extract_baseline_scores(current_assessment)
    results = []

    for plan_record in plans[-5:]:  # Check last 5 plans
        baseline = plan_record.get("baseline_scores", {})
        ts = plan_record.get("timestamp", "unknown")[:10]
        problems = plan_record.get("problems", [])

        improved = []
        regressed = []
        unchanged = []

        for agent, baseline_score in baseline.items():
            if not isinstance(baseline_score, (int, float)):
                continue
            current_score = current.get(agent)
            if current_score is None or not isinstance(current_score, (int, float)):
                continue

            diff = current_score - baseline_score
            # For crash_rate, lower is better — invert the comparison
            if agent == "_crash_rate":
                diff = -diff

            if diff > 0.05:
                improved.append(f"{agent}: {baseline_score:.2f}->{current_score:.2f}")
            elif diff < -0.05:
                regressed.append(f"{agent}: {baseline_score:.2f}->{current_score:.2f}")
            else:
                unchanged.append(agent)

        if improved and not regressed:
            status = "improved"
        elif regressed:
            status = "regressed"
        else:
            status = "no_change"

        # Update status in the record
        plan_record["status"] = status

        results.append(f"[{ts}] {status}: +{len(improved)} -{len(regressed)} ={len(unchanged)}")
        if problems:
            results.append(f"  Problems: {'; '.join(problems[:3])}")
        if improved:
            results.append(f"  Improved: {', '.join(improved)}")
        if regressed:
            results.append(f"  Regressed: {', '.join(regressed)}")

    return "\n".join(results) if results else ""


# ---------------------------------------------------------------------------
# Rubric quarantine — disabled rubrics are skipped in improvement-plan generation
# ---------------------------------------------------------------------------


def flag_rubric_miscalibrated(rubric_name: str) -> None:
    """Record a miscalibration flag for a rubric. Auto-disables after threshold."""
    from config import DISABLED_RUBRICS, MISCALIBRATION_FLAG_THRESHOLD

    counts: dict[str, int] = {}
    if _MISCALIBRATION_COUNTS_FILE.exists():
        try:
            counts = json.loads(_MISCALIBRATION_COUNTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    counts[rubric_name] = counts.get(rubric_name, 0) + 1
    try:
        _MISCALIBRATION_COUNTS_FILE.write_text(json.dumps(counts, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log.warning("Could not save miscalibration counts: %s", e)

    if counts[rubric_name] >= MISCALIBRATION_FLAG_THRESHOLD:
        DISABLED_RUBRICS.add(rubric_name)
        log.warning(
            "DISABLED_RUBRIC: %r auto-disabled after %d miscalibration flags",
            rubric_name,
            counts[rubric_name],
        )


# ---------------------------------------------------------------------------
# Top-down improvement: Mira diagnosis → targeted sub-agent fixes
# ---------------------------------------------------------------------------


def diagnose_and_improve(assessment: dict) -> str | None:
    """Analyze assessment, identify weak agents, generate targeted improvements.

    Top-down: Mira-level problem → trace to responsible agent → fix plan.
    Only triggered by GROUNDED metrics, never by LLM self-eval.
    """
    from config import DISABLED_RUBRICS, EVALUATOR_MIN_ISSUE_SEVERITY, LOGS_DIR, SUSPENDED_METRICS

    _min_sev = _SEVERITY_ORDER.get(EVALUATOR_MIN_ISSUE_SEVERITY, 1)
    _low_sev_log = LOGS_DIR / "evaluator_low_severity.log"

    def _passes(severity: str) -> bool:
        return _SEVERITY_ORDER.get(severity, 0) >= _min_sev

    def _log_low(desc: str, severity: str) -> None:
        try:
            with open(_low_sev_log, "a", encoding="utf-8") as _f:
                _f.write(f"{datetime.now().isoformat()} [{severity}] {desc}\n")
        except OSError:
            pass

    problems = []

    # Check aggregate health
    agg = assessment.get("aggregate", {})
    if agg.get("overall_success_rate", 1) < 0.8:
        desc = f"Overall success rate is {agg['overall_success_rate']:.0%} (target: 80%+)"
        if _passes("high"):
            problems.append(desc)
        else:
            _log_low(desc, "high")
    if agg.get("crash_rate", 0) > 0.05:
        desc = f"Crash rate is {agg['crash_rate']:.1%} (target: < 5%)"
        if _passes("high"):
            problems.append(desc)
        else:
            _log_low(desc, "high")
    if agg.get("scaffolding_rejection_warning"):
        _rej_rate = agg.get("scaffolding_rejection_rate", 0)
        _rej_count = agg.get("scaffolding_rejection_count", 0)
        desc = f"Scaffolding rejection rate is {_rej_rate:.1%} ({_rej_count} rejections) — model output failing content guards"
        if _passes("medium"):
            problems.append(desc)
        else:
            _log_low(desc, "medium")

    # Find weak agents
    weak_agents = []
    for name, card in assessment.get("agents", {}).items():
        if card["task_count"] == 0:
            continue
        for metric_key in card.get("scores", {}):
            if metric_key in DISABLED_RUBRICS:
                log.warning("QUARANTINED: skipping %r — rubric is disabled (miscalibrated)", metric_key)
                continue
            if metric_key in SUSPENDED_METRICS:
                log.warning(
                    "SUSPENDED_METRIC metric=%r agent=%r — excluded from improvement plan (pending rubric audit)",
                    metric_key,
                    name,
                )
        active_scores = {
            k: v for k, v in card.get("scores", {}).items() if k not in SUSPENDED_METRICS and k not in DISABLED_RUBRICS
        }
        card = {**card, "scores": active_scores}
        if card["success_rate"] < 0.7:
            severity = "high" if card["success_rate"] < 0.5 else "medium"
            weak_agent_entry = {
                "agent": name,
                "success_rate": card["success_rate"],
                "task_count": card["task_count"],
                "failed": card["failed"],
            }
            desc = f"{name} agent: {card['success_rate']:.0%} success ({card['failed']}/{card['task_count']} failed)"
            if _passes(severity):
                weak_agents.append(weak_agent_entry)
                problems.append(desc)
            else:
                _log_low(desc, severity)

    # Enrich assessment with failure_log data
    try:
        from ops.failure_log import get_failure_summary, load_recent_failures

        recent_failures = load_recent_failures(days=7)
        failure_summary = get_failure_summary(days=7)

        assessment["pipeline_failures_7d"] = len(recent_failures)
        assessment["failure_summary"] = failure_summary

        # Group by pipeline for per-agent scoring
        failure_by_pipeline = {}
        for f in recent_failures:
            p = f.get("pipeline", "unknown")
            failure_by_pipeline[p] = failure_by_pipeline.get(p, 0) + 1
        assessment["failures_by_pipeline"] = failure_by_pipeline

        # Flag pipelines with high failure counts as problems
        for pipeline, count in failure_by_pipeline.items():
            if count >= 3:
                desc = f"{pipeline} pipeline: {count} failures in 7 days"
                if _passes("medium"):
                    problems.append(desc)
                else:
                    _log_low(desc, "medium")
    except Exception as e:
        log.debug("Could not load failure log for evaluation: %s", e)

    if not problems:
        log.info("Assessment healthy — no improvements needed")
        return None

    # Generate improvement plan using a DIFFERENT model to avoid self-preference
    try:
        from llm import model_think
        from config import CLAUDE_FALLBACK_MODEL

        # Use fallback model (not Claude) to evaluate Claude's work
        diagnosis_text = "\n".join(f"- {p}" for p in problems)
        weak_detail = json.dumps(weak_agents, indent=2) if weak_agents else "none"

        blocked_skills_summary = ""
        try:
            _failures_path = _SHARED / "soul" / "skill_audit_failures.jsonl"
            if _failures_path.exists():
                _unresolved = []
                for _line in _failures_path.read_text(encoding="utf-8").splitlines():
                    if not _line.strip():
                        continue
                    try:
                        _rec = json.loads(_line)
                        if "resolved_at" not in _rec:
                            _unresolved.append(_rec.get("skill_name", "?"))
                    except json.JSONDecodeError:
                        continue
                if _unresolved:
                    blocked_skills_summary = (
                        f"\n\n## Blocked Skills (unresolved)\n"
                        f"{len(_unresolved)} skills blocked since last review: {', '.join(_unresolved)}"
                    )
        except Exception as _e:
            log.debug("Could not load skill audit failures: %s", _e)

        prompt = f"""You are an engineering manager reviewing an AI agent system's performance.

## Problems Detected (from deterministic metrics)
{diagnosis_text}

## Weak Agent Details
{weak_detail}{blocked_skills_summary}

## Scoring Dimensions
When assessing agent output quality, apply these dimensions in addition to task success metrics:

**Confidence Calibration** (weight: 10% — applies to writer, analyst, researcher outputs; skip for coder outputs where precision is correct behavior)
Does the output hedge appropriately on uncertain claims?
- Failure mode 1: asserting facts without a verifiable source or explicit retrieval when one is expected (e.g. "Studies show X" with no citation, unattributed empirical claims). Penalize each instance.
- Failure mode 2: omitting uncertainty markers ("likely", "unclear", "I cannot verify") on claims that are genuinely uncertain — market forecasts, model internals, contested empirical claims, future events. Penalize assertive language ("will", "definitely", "clearly") used on claims that are empirically uncertain or model-dependent.
Reward explicit uncertainty markers proportional to the actual epistemic state.

## Required Adversarial Pre-Output Step

Before emitting any score assessment or improvement recommendation:

1. **Dissent Check**: For any score above 0.5 in your analysis, explicitly state:
   DISSENT CHECK — What specific evidence would have to be true for this score to be wrong? If you cannot identify falsifying evidence, lower the score by 0.2 in your recommendation.

2. **Inversion Check**: If a score is <= 0.3 but your description of the agent or metric contains positive language (words like "improved", "good", "strong", "accurate"), flag the entry as:
   INVERTED: <agent_or_metric_name> — re-score required
   Then re-score before proceeding.

Include all DISSENT CHECK results and any INVERTED entries before your final recommendations.

## Available Levers
For each weak agent, you can recommend:
1. Prompt changes (adjust the system prompt in handler.py)
2. Skill additions (add a new .md skill file)
3. Routing changes (route certain tasks away from this agent)
4. Timeout/retry adjustments
5. Pre-processing steps (validate input before sending to agent)

Generate a concrete improvement plan. For each recommendation:
- Which agent
- What specific change
- Expected impact
- How to verify the fix worked

If confidence_calibration < 1, recommend the agent add explicit uncertainty markers ("likely", "uncertain", "I don't have enough signal") to the relevant claim types.

Be specific. "Improve the prompt" is not actionable. "Add error handling for empty input in coder/handler.py line 45" is.

At the end of your response, include exactly these three lines:
OVERCONFIDENCE_DETECTED: true|false
CONFIDENCE_NOTE: <one sentence summarizing calibration issues found, or "none detected">
INVERTED_SCORES: <comma-separated list of flagged agent/metric names, or "none">"""

        plan = model_think(
            prompt, model_name=CLAUDE_FALLBACK_MODEL, system="You are a senior engineering manager.", timeout=90
        )

        if plan:
            overconfidence_detected = False
            confidence_note = "none detected"
            inverted_scores: list[str] = []
            for _line in plan.splitlines():
                if _line.startswith("OVERCONFIDENCE_DETECTED:"):
                    overconfidence_detected = _line.split(":", 1)[1].strip().lower() == "true"
                elif _line.startswith("CONFIDENCE_NOTE:"):
                    confidence_note = _line.split(":", 1)[1].strip()
                elif _line.startswith("INVERTED_SCORES:"):
                    raw = _line.split(":", 1)[1].strip()
                    if raw.lower() != "none":
                        inverted_scores = [s.strip() for s in raw.split(",") if s.strip()]
                        for _inv in inverted_scores:
                            log.warning(
                                "INVERTED_SCORE detected: %r — score/description mismatch, re-scoring required", _inv
                            )

            consensus_only_agents = []
            for name, card in assessment.get("agents", {}).items():
                scored_keys = [
                    k for k in card.get("scores", {}) if k not in SUSPENDED_METRICS and k not in DISABLED_RUBRICS
                ]
                if scored_keys and all(_get_metric_type(name, k) == "consensus_proxy" for k in scored_keys):
                    consensus_only_agents.append(name)

            if consensus_only_agents:
                warning = (
                    "WARNING: All scores are consensus-proxies — no outcome-based ground truth available. "
                    "Plans derived from these scores measure style conformance, not correctness."
                )
                log.warning(
                    "CONSENSUS_PROXY_ONLY agents=%r — improvement plan flagged",
                    consensus_only_agents,
                )
                plan = warning + "\n\n" + plan

            plan_data = {
                "generated_at": datetime.now().isoformat(),
                "problems": problems,
                "weak_agents": weak_agents,
                "plan": plan,
                "overconfidence_detected": overconfidence_detected,
                "confidence_note": confidence_note,
                "inverted_scores": inverted_scores,
                "status": "pending",
                "source": "evaluator_agent",
            }
            _IMPROVEMENT_FILE.write_text(json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")
            save_improvement_plan_with_baseline(plan_data, assessment)
            log.info("Top-down improvement plan: %d problems, %d weak agents", len(problems), len(weak_agents))
            return plan

    except (ImportError, OSError) as e:
        log.warning("Improvement plan generation failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Handler — can be triggered by super agent or on schedule
# ---------------------------------------------------------------------------


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Run full hierarchical assessment and generate improvement plan if needed."""
    days = 7  # default assessment window

    # Parse optional days from content
    if "days=" in content:
        try:
            days = int(content.split("days=")[1].split()[0])
        except (ValueError, IndexError):
            pass

    log.info("Running full assessment (last %d days)", days)

    # Apply user-confirmed score overrides if provided in content
    # Format: "score agent_name.metric=value" e.g. "score writer.task_success=0.9"
    if "score " in content:
        try:
            from evaluation.storage import update_weakness_score

            for part in content.split():
                if "=" in part and "." in part.split("=")[0]:
                    metric, val_str = part.split("=", 1)
                    update_weakness_score(metric.strip(), float(val_str.strip()), evidence_type="user_confirmed")
                    log.info("User-confirmed score: %s = %s", metric.strip(), val_str.strip())
        except Exception as _e:
            log.debug("User-confirmed score parsing failed: %s", _e)

    # Score everything
    assessment = score_all(days)

    # Diagnose and generate improvements
    plan = diagnose_and_improve(assessment)

    # Format report
    agg = assessment["aggregate"]
    lines = [
        f"# Mira Performance Assessment ({days}-day window)",
        f"Generated: {assessment['generated_at'][:16]}",
        "",
        f"## Aggregate",
        f"- Tasks: {agg.get('total_tasks', 0)}",
        f"- Success rate: {agg.get('overall_success_rate', 0):.0%}",
        f"- Active agents: {agg.get('active_agents', 0)}",
        f"- Crash rate: {agg.get('crash_rate', 0):.1%}",
        f"- Daily cost: ${agg.get('daily_cost_usd', 0):.4f}",
        f"- Scaffolding rejections: {agg.get('scaffolding_rejection_count', 0)} "
        f"(rate: {agg.get('scaffolding_rejection_rate', 0):.1%})"
        + (" — model-health warning" if agg.get("scaffolding_rejection_warning") else ""),
        "",
        "## Per-Agent",
    ]

    low_conf_agents = set(agg.get("low_confidence_agents", []))
    for name, card in sorted(assessment["agents"].items()):
        if card["task_count"] == 0:
            lines.append(f"- **{name}**: no tasks")
        else:
            emoji = "✅" if card["success_rate"] >= 0.8 else "⚠️" if card["success_rate"] >= 0.5 else "❌"
            suffix = " [low confidence — score history thin or stale]" if name in low_conf_agents else ""
            lines.append(
                f"- **{name}** {emoji}: {card['success_rate']:.0%} "
                f"({card['succeeded']}/{card['task_count']}){suffix}"
            )

    # Stale skills
    stale_skills = assessment.get("stale_skills", [])
    if stale_skills:
        from config import SKILL_STALENESS_DAYS

        stale_lines = [f"- **{w['skill']}** ({w['source']}): {w['reason']}" for w in stale_skills]
        lines.extend(["", f"## Stale Skills ({len(stale_skills)}, threshold: {SKILL_STALENESS_DAYS}d)", *stale_lines])

    # Marginalized skills
    marginalized_skills = assessment.get("marginalized_skills", [])
    if marginalized_skills:
        marg_lines = [
            f"- **{m['skill']}**: {m['reason']} (use_count={m.get('use_count', 0)}) — retain or remove"
            for m in marginalized_skills
        ]
        lines.extend(["", f"## Marginalized Skills ({len(marginalized_skills)})", *marg_lines])

    # Skills added during this evaluation period
    try:
        from config import SOUL_DIR

        provenance_file = SOUL_DIR / "skill_provenance.json"
        if provenance_file.exists():
            provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            new_skills = [r for r in provenance if r.get("date_added", "") >= cutoff]
            if new_skills:
                skill_lines = [
                    f"- **{r['skill_name']}** (added {r['date_added'][:10]}"
                    + (f", from: {r['source_article_title']}" if r.get("source_article_title") else "")
                    + ")"
                    for r in new_skills
                ]
                lines.extend(["", f"## Skills Added This Period ({len(new_skills)})", *skill_lines])
    except Exception as _e:
        log.debug("Could not load skill provenance for report: %s", _e)

    # Skill audit gray inventory — WARN-level findings that need human eyes
    try:
        from memory.soul_skills import load_audit_warnings_digest

        audit_digest = load_audit_warnings_digest(days=days)
        if audit_digest:
            lines.extend(["", "## Skill Audit Warnings (Gray Inventory)", audit_digest])
    except Exception as _e:
        log.debug("Could not load audit warnings digest: %s", _e)

    # Blocked skill sources — flag high-rejection-rate origins
    try:
        from config import LOGS_DIR

        blocked_log = LOGS_DIR / "blocked_skills_log.jsonl"
        if blocked_log.exists():
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            source_counts: dict[str, int] = {}
            for line in blocked_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("timestamp", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts >= cutoff:
                            src = rec.get("source", "unknown")
                            source_counts[src] = source_counts.get(src, 0) + 1
                except (json.JSONDecodeError, ValueError):
                    continue
            high_suspicion = {src: cnt for src, cnt in source_counts.items() if cnt >= 2}
            if high_suspicion:
                suspicion_lines = [
                    f"- **{src}**: {cnt} blocked skills (30d)"
                    for src, cnt in sorted(high_suspicion.items(), key=lambda x: -x[1])
                ]
                lines.extend(["", "## High-Suspicion Skill Sources (>=2 blocks in 30d)", *suspicion_lines])
    except Exception as _e:
        log.debug("Could not load blocked skills log: %s", _e)

    # Check outcomes of previous improvement plans
    outcomes = check_improvement_outcomes(assessment)
    if outcomes:
        lines.extend(["", "## Improvement Tracking (last plans)", outcomes])

    if plan:
        lines.extend(["", "## Improvement Plan", plan])

    report = "\n".join(lines)

    # Write to workspace
    (workspace / "output.md").write_text(report, encoding="utf-8")

    return report
