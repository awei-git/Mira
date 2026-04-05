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

_SHARED = Path(__file__).resolve().parent.parent / "shared"
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

# ---------------------------------------------------------------------------
# Per-agent criteria — each agent is scored on what MATTERS for its role
# All metrics are deterministic (no LLM self-eval)
# ---------------------------------------------------------------------------

AGENT_CRITERIA = {
    "writer": {
        "description": "Writing pipeline — articles, essays, stories",
        "metrics": {
            "publish_rate": "Articles that made it to Substack (published / attempted)",
            "review_convergence": "Average review score across writing pipeline rounds",
            "word_count_avg": "Average article length (proxy for depth)",
        },
    },
    "coder": {
        "description": "Debug, code review, quick fixes",
        "metrics": {
            "task_success": "Tasks completed without error / total",
            "bug_found_rate": "For review tasks: issues detected per review",
            "syntax_valid": "Generated code passes syntax check",
        },
    },
    "explorer": {
        "description": "Feed fetching, briefings, research",
        "metrics": {
            "briefing_produced": "Briefings successfully generated / attempts",
            "source_diversity": "Unique sources per briefing",
            "reading_notes_produced": "Reading notes extracted per explore cycle",
        },
    },
    "researcher": {
        "description": "Deep research, math proofs, iterative investigation",
        "metrics": {
            "task_success": "Research tasks completed / attempted",
            "iteration_depth": "Average iterations per research task (more = deeper)",
            "output_length": "Average output length (proxy for thoroughness)",
        },
    },
    "analyst": {
        "description": "Market analysis, competitive intelligence",
        "metrics": {
            "task_success": "Analysis tasks completed / attempted",
            "output_length": "Average output length",
        },
    },
    "discussion": {
        "description": "Conversational responses as Mira",
        "metrics": {
            "response_rate": "Messages that got a response / total",
            "response_time_avg": "Average seconds to respond",
        },
    },
    "podcast": {
        "description": "Audio generation and publishing",
        "metrics": {
            "episodes_published": "Episodes successfully published to RSS",
            "audio_generated": "Audio files successfully generated / attempted",
        },
    },
    "secret": {
        "description": "Private tasks via local Ollama",
        "metrics": {
            "task_success": "Tasks completed / attempted",
            "stayed_local": "No cloud API calls detected (always should be 100%)",
        },
    },
    "general": {
        "description": "Catch-all — questions, search, analysis, misc tasks",
        "metrics": {
            "task_success": "Tasks completed without error / total",
            "output_length": "Average output length (proxy for effort)",
        },
    },
    "socialmedia": {
        "description": "Substack engagement — notes, comments, growth",
        "metrics": {
            "notes_posted": "Substack notes successfully posted",
            "comments_replied": "Comments replied to / flagged for reply",
        },
    },
}

SUPER_CRITERIA = {
    "description": "Orchestrator — routing, planning, lifecycle",
    "metrics": {
        "routing_accuracy": "Tasks routed to correct agent (no re-routes needed)",
        "plan_quality": "Multi-step plans that executed without step failures",
        "cycle_time": "Average main loop duration (target: < 5s)",
        "crash_rate": "Cycles that crashed / total cycles",
        "heartbeat_uptime": "Heartbeat updated within 3min window (%)",
        "stuck_tasks": "Tasks stuck in dispatched/running state > 30min",
        "timeout_rate": "Tasks that timed out / total dispatched",
    },
}


# ---------------------------------------------------------------------------
# Score computation — all deterministic, from task history and logs
# ---------------------------------------------------------------------------

def _load_task_history(days: int = 7) -> list[dict]:
    """Load recent task records from history.jsonl."""
    from config import MIRA_ROOT
    history_file = MIRA_ROOT / "tasks" / "history.jsonl"
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


def score_agent(agent_name: str, days: int = 7) -> dict:
    """Score a specific agent based on its task history. Returns grounded metrics."""
    criteria = AGENT_CRITERIA.get(agent_name)
    if not criteria:
        return {"agent": agent_name, "error": "no criteria defined"}

    history = _load_task_history(days)
    # Match by explicit 'agent' field (preferred) or exact agent name in tags
    agent_tasks = [
        t for t in history
        if t.get("agent") == agent_name
        or agent_name in t.get("tags", [])
    ]

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

    return {
        "agent": agent_name,
        "period_days": days,
        "task_count": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "success_rate": scores.get("task_success", 0),
        "scores": scores,
    }


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
    from config import MIRA_ROOT
    status_file = MIRA_ROOT / "tasks" / "status.json"
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
        from sub_agent import usage_summary
        usage = usage_summary()
        agg["daily_cost_usd"] = usage.get("total_cost_usd", 0)
        agg["daily_tokens"] = usage.get("total_tokens", 0)
        agg["daily_calls"] = usage.get("calls", 0)
    except (ImportError, OSError):
        pass

    result["aggregate"] = agg

    # Save scorecard
    _SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    scorecard_path = _SCORECARDS_DIR / f"{today}.json"
    scorecard_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


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
# Top-down improvement: Mira diagnosis → targeted sub-agent fixes
# ---------------------------------------------------------------------------

def diagnose_and_improve(assessment: dict) -> str | None:
    """Analyze assessment, identify weak agents, generate targeted improvements.

    Top-down: Mira-level problem → trace to responsible agent → fix plan.
    Only triggered by GROUNDED metrics, never by LLM self-eval.
    """
    problems = []

    # Check aggregate health
    agg = assessment.get("aggregate", {})
    if agg.get("overall_success_rate", 1) < 0.8:
        problems.append(f"Overall success rate is {agg['overall_success_rate']:.0%} (target: 80%+)")
    if agg.get("crash_rate", 0) > 0.05:
        problems.append(f"Crash rate is {agg['crash_rate']:.1%} (target: < 5%)")

    # Find weak agents
    weak_agents = []
    for name, card in assessment.get("agents", {}).items():
        if card["task_count"] == 0:
            continue
        if card["success_rate"] < 0.7:
            weak_agents.append({
                "agent": name,
                "success_rate": card["success_rate"],
                "task_count": card["task_count"],
                "failed": card["failed"],
            })
            problems.append(
                f"{name} agent: {card['success_rate']:.0%} success "
                f"({card['failed']}/{card['task_count']} failed)"
            )

    # Enrich assessment with failure_log data
    try:
        from failure_log import get_failure_summary, load_recent_failures

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
                problems.append(
                    f"{pipeline} pipeline: {count} failures in 7 days"
                )
    except Exception as e:
        log.debug("Could not load failure log for evaluation: %s", e)

    if not problems:
        log.info("Assessment healthy — no improvements needed")
        return None

    # Generate improvement plan using a DIFFERENT model to avoid self-preference
    try:
        from sub_agent import model_think
        from config import CLAUDE_FALLBACK_MODEL

        # Use fallback model (not Claude) to evaluate Claude's work
        diagnosis_text = "\n".join(f"- {p}" for p in problems)
        weak_detail = json.dumps(weak_agents, indent=2) if weak_agents else "none"

        prompt = f"""You are an engineering manager reviewing an AI agent system's performance.

## Problems Detected (from deterministic metrics)
{diagnosis_text}

## Weak Agent Details
{weak_detail}

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

Be specific. "Improve the prompt" is not actionable. "Add error handling for empty input in coder/handler.py line 45" is."""

        plan = model_think(prompt, model_name=CLAUDE_FALLBACK_MODEL,
                          system="You are a senior engineering manager.", timeout=90)

        if plan:
            plan_data = {
                "generated_at": datetime.now().isoformat(),
                "problems": problems,
                "weak_agents": weak_agents,
                "plan": plan,
                "status": "pending",
                "source": "evaluator_agent",
            }
            _IMPROVEMENT_FILE.write_text(
                json.dumps(plan_data, ensure_ascii=False, indent=2), encoding="utf-8")
            save_improvement_plan_with_baseline(plan_data, assessment)
            log.info("Top-down improvement plan: %d problems, %d weak agents",
                     len(problems), len(weak_agents))
            return plan

    except (ImportError, OSError) as e:
        log.warning("Improvement plan generation failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Handler — can be triggered by super agent or on schedule
# ---------------------------------------------------------------------------

def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Run full hierarchical assessment and generate improvement plan if needed."""
    days = 7  # default assessment window

    # Parse optional days from content
    if "days=" in content:
        try:
            days = int(content.split("days=")[1].split()[0])
        except (ValueError, IndexError):
            pass

    log.info("Running full assessment (last %d days)", days)

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
        "",
        "## Per-Agent",
    ]

    for name, card in sorted(assessment["agents"].items()):
        if card["task_count"] == 0:
            lines.append(f"- **{name}**: no tasks")
        else:
            emoji = "✅" if card["success_rate"] >= 0.8 else "⚠️" if card["success_rate"] >= 0.5 else "❌"
            lines.append(
                f"- **{name}** {emoji}: {card['success_rate']:.0%} "
                f"({card['succeeded']}/{card['task_count']})"
            )

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
