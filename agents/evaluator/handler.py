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

import ast
import json
import logging
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error as url_error
from urllib import request as url_request

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
DRIFT_WINDOW_SIZE = 10
DRIFT_SLOPE_THRESHOLD = -0.01
_SCORE_HISTORY_LIMIT = 30
_STATE_FILE = Path(__file__).parent / "state.json"
RUBRIC_STALENESS_THRESHOLD = 3
_SPOT_CHECK_LOG = Path(__file__).resolve().parent.parent.parent / "logs" / "evaluator_spot_checks.jsonl"
_EVALUATION_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "evaluations"
_DRIFT_LOG_FILE = _EVALUATION_LOG_DIR / "drift_log.json"
_CONTENT_GUARD_AUDIT_SCHEDULE = {
    "name": "content_guard_completeness_audit",
    "weekday": 0,
}
VERIFICATION_INDEPENDENCE = True

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
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "review_convergence": {
                "description": "Average review score across writing pipeline rounds",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
            "word_count_avg": {
                "description": "Average article length (proxy for depth)",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "coder": {
        "description": "Debug, code review, quick fixes",
        "metrics": {
            "task_success": {
                "description": "Tasks completed without error / total",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "bug_found_rate": {
                "description": "For review tasks: issues detected per review",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
            "syntax_valid": {
                "description": "Generated code passes syntax check",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "explorer": {
        "description": "Feed fetching, briefings, research",
        "metrics": {
            "briefing_produced": {
                "description": "Briefings successfully generated / attempts",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "source_diversity": {
                "description": "Unique sources per briefing",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
            "reading_notes_produced": {
                "description": "Reading notes extracted per explore cycle",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "researcher": {
        "description": "Deep research, math proofs, iterative investigation",
        "metrics": {
            "task_success": {
                "description": "Research tasks completed / attempted",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "iteration_depth": {
                "description": "Average iterations per research task (more = deeper)",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
            "output_length": {
                "description": "Average output length (proxy for thoroughness)",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "analyst": {
        "description": "Market analysis, competitive intelligence",
        "metrics": {
            "task_success": {
                "description": "Analysis tasks completed / attempted",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "output_length": {
                "description": "Average output length",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "discussion": {
        "description": "Conversational responses as Mira",
        "metrics": {
            "response_rate": {
                "description": "Messages that got a response / total",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "response_time_avg": {
                "description": "Average seconds to respond",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "podcast": {
        "description": "Audio generation and publishing",
        "metrics": {
            "episodes_published": {
                "description": "Episodes successfully published to RSS",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "audio_generated": {
                "description": "Audio files successfully generated / attempted",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "secret": {
        "description": "Private tasks via local oMLX",
        "metrics": {
            "task_success": {
                "description": "Tasks completed / attempted",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "stayed_local": {
                "description": "No cloud API calls detected (always should be 100%)",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "general": {
        "description": "Catch-all — questions, search, analysis, misc tasks",
        "metrics": {
            "task_success": {
                "description": "Tasks completed without error / total",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "output_length": {
                "description": "Average output length (proxy for effort)",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
        },
    },
    "socialmedia": {
        "description": "Substack engagement — notes, comments, growth",
        "metrics": {
            "notes_posted": {
                "description": "Substack notes successfully posted",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "comments_replied": {
                "description": "Comments replied to / flagged for reply",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
    "super": {
        "description": "Orchestrator — routing, planning, lifecycle",
        "metrics": {
            "routing_accuracy": {
                "description": "Tasks routed to correct agent (no re-routes needed)",
                "metric_type": "proxy",
                "ground_truth_type": "consensus_proxy",
            },
            "plan_quality": {
                "description": "Multi-step plans that executed without step failures",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "cycle_time": {
                "description": "Average main loop duration (target: < 5s)",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "crash_rate": {
                "description": "Cycles that crashed / total cycles",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "heartbeat_uptime": {
                "description": "Heartbeat updated within 3min window (%)",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "stuck_tasks": {
                "description": "Tasks stuck in dispatched/running state > 30min",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
            "timeout_rate": {
                "description": "Tasks that timed out / total dispatched",
                "metric_type": "outcome",
                "ground_truth_type": "outcome",
            },
        },
    },
}

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

SUPER_CRITERIA = {
    "description": "Orchestrator — routing, planning, lifecycle",
    "metrics": {
        "routing_accuracy": {
            "description": "Tasks routed to correct agent (no re-routes needed)",
            "metric_type": "proxy",
            "ground_truth_type": "consensus_proxy",
        },
        "plan_quality": {
            "description": "Multi-step plans that executed without step failures",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
        "cycle_time": {
            "description": "Average main loop duration (target: < 5s)",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
        "crash_rate": {
            "description": "Cycles that crashed / total cycles",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
        "heartbeat_uptime": {
            "description": "Heartbeat updated within 3min window (%)",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
        "stuck_tasks": {
            "description": "Tasks stuck in dispatched/running state > 30min",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
        "timeout_rate": {
            "description": "Tasks that timed out / total dispatched",
            "metric_type": "outcome",
            "ground_truth_type": "outcome",
        },
    },
}

_UNIVERSAL_METRIC_TYPES: dict[str, str] = {
    "task_success": "outcome",
    "guard_fire_rate": "outcome",
    "output_length_avg": "proxy",
}
_METRIC_AUDIT_WARNING = "METRIC_AUDIT: all criteria are proxy metrics — no outcome verification available."


def _get_metric_type(agent_name: str, metric_key: str) -> str:
    """Return the metric_type for a metric, falling back to universal defaults."""
    metric_def = AGENT_CRITERIA.get(agent_name, {}).get("metrics", {}).get(metric_key)
    if isinstance(metric_def, dict):
        metric_type = metric_def.get("metric_type")
        if metric_type in {"outcome", "proxy"}:
            return metric_type
        return "outcome" if metric_def.get("ground_truth_type") == "outcome" else "proxy"
    return _UNIVERSAL_METRIC_TYPES.get(metric_key, "proxy")


def _agent_criteria_metric_types(agent_name: str) -> list[str]:
    metrics = AGENT_CRITERIA.get(agent_name, {}).get("metrics", {})
    if not isinstance(metrics, dict):
        return []
    return [_get_metric_type(agent_name, key) for key in metrics]


def _agent_has_outcome_metric(agent_name: str) -> bool:
    return any(metric_type == "outcome" for metric_type in _agent_criteria_metric_types(agent_name))


def _append_metric_audit_warning(card: dict, agent_name: str) -> dict:
    metric_types = _agent_criteria_metric_types(agent_name)
    if metric_types and all(metric_type == "proxy" for metric_type in metric_types):
        card.setdefault("warnings", []).append(_METRIC_AUDIT_WARNING)
    return card


def _agent_score_file(agent_name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent_name).strip("._") or "agent"
    return _EVALUATION_LOG_DIR / f"{safe_name}_scores.jsonl"


def _load_score_history(agent_name: str) -> list[dict]:
    path = _agent_score_file(agent_name)
    if not path.exists():
        return []

    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    except OSError as exc:
        log.debug("Could not load score history for %s: %s", agent_name, exc)
    return records


def _record_score(agent_name, score, timestamp):
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return

    history = _load_score_history(agent_name)
    history.append({"timestamp": timestamp, "agent": agent_name, "score": score_value})
    history = history[-_SCORE_HISTORY_LIMIT:]

    try:
        _EVALUATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _agent_score_file(agent_name)
        payload = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in history)
        path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        log.debug("Could not record score history for %s: %s", agent_name, exc)


def _detect_drift(agent_name):
    from soul_manager import detect_agent_drift

    drift = detect_agent_drift(
        _load_score_history(agent_name),
        window_size=DRIFT_WINDOW_SIZE,
        slope_threshold=DRIFT_SLOPE_THRESHOLD,
    )
    drift["agent"] = agent_name
    return drift


def _log_drift_warning(drift: dict) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": drift.get("agent"),
        "slope": drift.get("slope"),
        "trend_direction": drift.get("trend_direction"),
        "sample_count": drift.get("sample_count"),
    }
    try:
        _EVALUATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            records = json.loads(_DRIFT_LOG_FILE.read_text(encoding="utf-8")) if _DRIFT_LOG_FILE.exists() else []
        except (json.JSONDecodeError, OSError):
            records = []
        if not isinstance(records, list):
            records = []
        records.append(entry)
        tmp_path = _DRIFT_LOG_FILE.with_suffix(_DRIFT_LOG_FILE.suffix + ".tmp")
        tmp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(_DRIFT_LOG_FILE)
    except OSError as exc:
        log.debug("Could not write drift log for %s: %s", drift.get("agent"), exc)


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


def _extract_spot_check_claims(agent_output: str) -> list[dict]:
    claims: list[dict] = []

    for match in re.finditer(r"https?://[^\s<>\]\"')]+", agent_output):
        claims.append({"type": "url", "value": match.group(0).rstrip(".,;:")})

    path_patterns = [
        r"`([^`\n]*(?:/|\\)[^`\n]+)`",
        r"(?<![\w:/])(?:~|/|\./|\.\./)[^\s<>\]\"')]+",
    ]
    for pattern in path_patterns:
        for match in re.finditer(pattern, agent_output):
            value = (match.group(1) if match.lastindex else match.group(0)).rstrip(".,;:")
            if value.startswith(("http://", "https://")):
                continue
            claims.append({"type": "file_path", "value": value})

    arithmetic = r"(?P<expr>-?\d+(?:\.\d+)?(?:\s*[+\-*/]\s*-?\d+(?:\.\d+)?)+)\s*=\s*" r"(?P<result>-?\d+(?:\.\d+)?)"
    for match in re.finditer(arithmetic, agent_output):
        claims.append(
            {
                "type": "numeric_result",
                "value": match.group(0),
                "expr": match.group("expr"),
                "result": match.group("result"),
            }
        )

    quote_pattern = (
        r"(?P<speaker>[A-Z][A-Za-z .'-]{1,80})\s+"
        r"(?:said|wrote|stated|argued|claims?|according to)\s*[:\-]?\s*"
        r"[\"“](?P<quote>[^\"”]{8,240})[\"”]"
    )
    for match in re.finditer(quote_pattern, agent_output):
        start = max(0, match.start() - 300)
        end = min(len(agent_output), match.end() + 300)
        nearby_urls = re.findall(r"https?://[^\s<>\]\"')]+", agent_output[start:end])
        claims.append(
            {
                "type": "attributed_quote",
                "value": f"{match.group('speaker').strip()}: {match.group('quote').strip()}",
                "quote": match.group("quote").strip(),
                "source_url": nearby_urls[0].rstrip(".,;:") if nearby_urls else "",
            }
        )

    seen = set()
    unique_claims = []
    for claim in claims:
        key = (claim["type"], claim["value"])
        if key in seen:
            continue
        seen.add(key)
        unique_claims.append(claim)
    return unique_claims


def _safe_eval_arithmetic(expr: str) -> float:
    def _eval_node(node) -> float:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_eval_node(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return _eval_node(node.operand)
        if isinstance(node, ast.BinOp):
            left = _eval_node(node.left)
            right = _eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("unsupported numeric expression")

    if not re.fullmatch(r"[-+*/().\d\s]+", expr):
        raise ValueError("unsupported numeric expression")
    return _eval_node(ast.parse(expr, mode="eval"))


def _verify_spot_check_claim(claim: dict) -> tuple[bool, str]:
    claim_type = claim.get("type", "")
    value = claim.get("value", "")

    if claim_type == "file_path":
        path = Path(value).expanduser()
        if not path.is_absolute():
            try:
                from config import MIRA_ROOT

                path = MIRA_ROOT / path
            except (ImportError, AttributeError):
                path = Path.cwd() / path
        exists = path.exists()
        return exists, f"file_path {value!r} {'exists' if exists else 'does not exist'}"

    if claim_type == "url":
        req = url_request.Request(value, method="HEAD", headers={"User-Agent": "MiraEvaluator/1.0"})
        try:
            with url_request.urlopen(req, timeout=3) as response:
                status = getattr(response, "status", 200)
            passed = 200 <= status < 400
            return passed, f"url {value!r} HEAD status={status}"
        except url_error.HTTPError as e:
            return 200 <= e.code < 400, f"url {value!r} HEAD status={e.code}"
        except Exception as e:
            return False, f"url {value!r} HEAD failed: {type(e).__name__}: {e}"

    if claim_type == "numeric_result":
        try:
            expected = float(claim["result"])
            actual = _safe_eval_arithmetic(claim["expr"])
            passed = abs(actual - expected) <= max(1e-6, abs(expected) * 1e-6)
            return passed, f"numeric_result {claim['expr']} recomputed={actual:g} claimed={expected:g}"
        except Exception as e:
            return False, f"numeric_result {value!r} recompute failed: {type(e).__name__}: {e}"

    if claim_type == "attributed_quote":
        source_url = claim.get("source_url", "")
        if not source_url:
            return False, f"attributed_quote {value!r} has no source URL to cross-check"
        req = url_request.Request(source_url, headers={"User-Agent": "MiraEvaluator/1.0"})
        try:
            with url_request.urlopen(req, timeout=5) as response:
                body = response.read(500_000).decode("utf-8", errors="ignore")
            quote = claim.get("quote", "")
            passed = quote in body
            return (
                passed,
                f"attributed_quote source={source_url!r} {'contains' if passed else 'does not contain'} quoted text",
            )
        except Exception as e:
            return False, f"attributed_quote source={source_url!r} fetch failed: {type(e).__name__}: {e}"

    return False, f"unsupported claim type {claim_type!r}: {value!r}"


def _spot_check_claim(agent_output: str) -> tuple[bool, str]:
    claims = _extract_spot_check_claims(agent_output or "")
    if not claims:
        return True, "no verifiable claims found"
    claim = random.choice(claims)
    passed, detail = _verify_spot_check_claim(claim)
    return passed, f"{claim['type']} sampled: {detail}"


def _task_self_report(task: dict) -> dict:
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    summary = str(task.get("summary") or "")
    return {
        "status": str(task.get("status") or ""),
        "outcome_verified": bool(task.get("outcome_verified", False)),
        "summary_length": len(summary),
        "verification_target": str(verification.get("target") or ""),
    }


def _candidate_observable_checks(task: dict) -> list[dict]:
    checks: list[dict] = []
    verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
    target = str(verification.get("target") or "").strip()
    artifact_type = str(verification.get("artifact_type") or "").strip().lower()

    if target.startswith(("http://", "https://")):
        checks.append({"type": "url_status", "value": target})
    elif target and artifact_type in {"file", "artifact"}:
        checks.append({"type": "file_exists", "value": target})

    workspace = str(task.get("workspace") or "").strip()
    if workspace:
        ws = Path(workspace).expanduser()
        if not ws.is_absolute():
            try:
                from config import MIRA_ROOT

                ws = MIRA_ROOT / ws
            except (ImportError, AttributeError):
                ws = Path.cwd() / ws
        checks.append({"type": "file_exists", "value": str(ws / "result.json")})
        checks.append({"type": "output_length", "value": str(ws / "output.md")})

    output_text = "\n\n".join(str(task.get(k) or "") for k in ("output", "result", "summary", "content"))
    for claim in _extract_spot_check_claims(output_text):
        if claim["type"] == "url":
            checks.append({"type": "url_status", "value": claim["value"]})
        elif claim["type"] == "file_path":
            checks.append({"type": "file_exists", "value": claim["value"]})

    seen = set()
    unique = []
    for check in checks:
        key = (check["type"], check["value"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(check)
    return unique


def _resolve_observable_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    try:
        from config import MIRA_ROOT

        return MIRA_ROOT / path
    except (ImportError, AttributeError):
        return Path.cwd() / path


def _run_observable_check(check: dict) -> dict:
    check_type = check.get("type", "")
    value = str(check.get("value") or "")
    if check_type == "url_status":
        req = url_request.Request(value, method="HEAD", headers={"User-Agent": "MiraEvaluator/1.0"})
        try:
            with url_request.urlopen(req, timeout=3) as response:
                status = getattr(response, "status", 200)
            return {
                "checked": True,
                "type": check_type,
                "target": value,
                "passed": 200 <= status < 400,
                "observed": {"status_code": status},
            }
        except url_error.HTTPError as e:
            return {
                "checked": True,
                "type": check_type,
                "target": value,
                "passed": 200 <= e.code < 400,
                "observed": {"status_code": e.code},
            }
        except Exception as e:
            return {
                "checked": True,
                "type": check_type,
                "target": value,
                "passed": False,
                "observed": {"error": f"{type(e).__name__}: {e}"},
            }

    if check_type == "file_exists":
        path = _resolve_observable_path(value)
        exists = path.exists()
        observed = {"exists": exists}
        if exists:
            try:
                observed["size_bytes"] = path.stat().st_size
            except OSError:
                pass
        return {
            "checked": True,
            "type": check_type,
            "target": value,
            "passed": exists,
            "observed": observed,
        }

    if check_type == "output_length":
        path = _resolve_observable_path(value)
        if not path.exists():
            return {
                "checked": True,
                "type": check_type,
                "target": value,
                "passed": False,
                "observed": {"exists": False, "length": 0},
            }
        try:
            length = len(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as e:
            return {
                "checked": True,
                "type": check_type,
                "target": value,
                "passed": False,
                "observed": {"error": f"{type(e).__name__}: {e}"},
            }
        return {
            "checked": True,
            "type": check_type,
            "target": value,
            "passed": length > 0,
            "observed": {"exists": True, "length": length},
        }

    return {"checked": False, "type": check_type, "target": value, "passed": False, "observed": {}}


def _independent_output_verification(agent_tasks: list[dict]) -> dict:
    if not VERIFICATION_INDEPENDENCE:
        return {"checked": False, "passed": True, "reason": "verification independence disabled"}
    for task in reversed(agent_tasks):
        for candidate in _candidate_observable_checks(task):
            check = _run_observable_check(candidate)
            if not check.get("checked"):
                continue
            report = _task_self_report(task)
            reported_success = report["status"] in {"done", "verified"} or report["outcome_verified"]
            discrepancies = []
            if reported_success and not check.get("passed"):
                discrepancies.append("agent reported completion/verification but observable check failed")
            if check.get("type") == "output_length" and check.get("passed"):
                observed_length = int((check.get("observed") or {}).get("length") or 0)
                if report["summary_length"] and observed_length != report["summary_length"]:
                    discrepancies.append(
                        f"observed output length {observed_length} differs from reported summary length "
                        f"{report['summary_length']}"
                    )
            check["task_id"] = task.get("task_id", "")
            check["self_report"] = report
            check["discrepancies"] = discrepancies
            return check
    return {
        "checked": False,
        "passed": False,
        "reason": "no observable file, output, or URL found for independent verification",
        "discrepancies": ["evaluation data depended on agent self-report only"],
    }


def _log_spot_check_result(agent_name: str, passed: bool, detail: str) -> None:
    try:
        _SPOT_CHECK_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent_name,
            "passed": passed,
            "detail": detail,
        }
        with open(_SPOT_CHECK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        log.debug("Could not log evaluator spot check for %s: %s", agent_name, e)


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
        return _append_metric_audit_warning(
            {
                "agent": agent_name,
                "period_days": days,
                "task_count": 0,
                "scores": {},
                "note": "no tasks in period",
            },
            agent_name,
        )

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

    output_parts = []
    for t in agent_tasks:
        for key in ("output", "result", "summary", "content"):
            value = t.get(key)
            if isinstance(value, str) and value.strip():
                output_parts.append(value)
    spot_passed, spot_detail = _spot_check_claim("\n\n".join(output_parts))
    _log_spot_check_result(agent_name, spot_passed, spot_detail)
    spot_check = {
        "passed": spot_passed,
        "detail": spot_detail,
    }
    independent_verification = _independent_output_verification(agent_tasks)
    coherence_bias_warning = not spot_passed
    if coherence_bias_warning and "task_success" in scores:
        scores["task_success"] = round(scores["task_success"] * 0.7, 3)
        spot_check["reliability_penalty_multiplier"] = 0.7

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
        "spot_check": spot_check,
        "independent_verification": independent_verification,
    }
    if independent_verification.get("discrepancies"):
        result["self_report_discrepancies"] = independent_verification["discrepancies"]
    if coherence_bias_warning:
        result["coherence_bias_warning"] = True

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

    # Update validated_at for explicitly used skills when scoring positively.
    if scores.get("task_success", 0) >= 0.8:
        _update_agent_skill_validation(agent_name, _extract_used_skill_names(agent_tasks))

    return _append_metric_audit_warning(result, agent_name)


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


def _extract_function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    raise ValueError(f"function not found: {function_name} in {path}")


def audit_content_guard() -> list[dict]:
    """Audit content guard decision templates for logical completeness."""
    socialmedia_handler = Path(__file__).resolve().parent.parent / "socialmedia" / "handler.py"
    preflight = _SHARED / "publish" / "preflight.py"

    content_guard_source = _extract_function_source(socialmedia_handler, "_content_looks_like_error")
    preflight_source = _extract_function_source(preflight, "preflight_check")

    checklist = [
        {
            "target": "_content_looks_like_error",
            "category": "edge_cases",
            "complete": "isinstance" in content_guard_source and "str" in content_guard_source,
            "gap": "non-string content can raise before the guard returns a verdict",
        },
        {
            "target": "_content_looks_like_error",
            "category": "edge_cases",
            "complete": ".strip()" in content_guard_source and "_MIN_PUBLISH_CHARS" in content_guard_source,
            "gap": "empty or whitespace-only content is not explicitly normalized before length gating",
        },
        {
            "target": "_content_looks_like_error",
            "category": "false_negatives",
            "complete": "_ERROR_KEYWORDS" in content_guard_source and ".lower()" in content_guard_source,
            "gap": "error-message detection lacks case-normalized keyword coverage",
        },
        {
            "target": "_content_looks_like_error",
            "category": "false_negatives",
            "complete": "early_section" not in content_guard_source,
            "gap": "keyword detection is limited to the early section, so late scaffolded errors can pass",
        },
        {
            "target": "_content_looks_like_error",
            "category": "semantic_blind_spots",
            "complete": "title" in content_guard_source or "platform" in content_guard_source,
            "gap": "guard judges body text alone and ignores title/platform/context mismatches",
        },
        {
            "target": "preflight_check",
            "category": "edge_cases",
            "complete": "isinstance(context" in preflight_source,
            "gap": "non-dict context can raise or bypass intended field checks",
        },
        {
            "target": "preflight_check",
            "category": "edge_cases",
            "complete": "action_type =" in preflight_source and ".lower()" in preflight_source,
            "gap": "action_type is not normalized before dispatch",
        },
        {
            "target": "preflight_check",
            "category": "false_negatives",
            "complete": "unsupported" in preflight_source or "unknown action" in preflight_source,
            "gap": "unknown action types can pass if the universal instruction check passes",
        },
        {
            "target": "preflight_check",
            "category": "semantic_blind_spots",
            "complete": "_content_looks_like_error" in preflight_source,
            "gap": "preflight_check does not directly apply the publish error-content guard",
        },
        {
            "target": "preflight_check",
            "category": "semantic_blind_spots",
            "complete": "proves=" in preflight_source and "assumes=" in preflight_source,
            "gap": "decision checks do not expose what each template proves versus assumes",
        },
    ]

    gaps = [
        {
            "target": item["target"],
            "category": item["category"],
            "gap": item["gap"],
        }
        for item in checklist
        if not item["complete"]
    ]
    if gaps:
        for gap in gaps:
            log.warning(
                "CONTENT_GUARD_AUDIT_GAP target=%s category=%s gap=%s",
                gap["target"],
                gap["category"],
                gap["gap"],
            )
    else:
        log.info("CONTENT_GUARD_AUDIT_PASS checklist_items=%d", len(checklist))
    return gaps


def _should_run_content_guard_audit(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() != _CONTENT_GUARD_AUDIT_SCHEDULE["weekday"]:
        return False

    state: dict = {}
    if _STATE_FILE.exists():
        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    year, week, _ = now.isocalendar()
    return state.get("last_content_guard_audit_week") != f"{year}-W{week:02d}"


def _mark_content_guard_audit_ran(now: datetime | None = None) -> None:
    now = now or datetime.now()
    state: dict = {}
    if _STATE_FILE.exists():
        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    year, week, _ = now.isocalendar()
    state["last_content_guard_audit_week"] = f"{year}-W{week:02d}"
    state["last_content_guard_audit_at"] = now.isoformat()
    try:
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.debug("Could not save content guard audit state: %s", e)


def _run_scheduled_content_guard_audit() -> list[dict] | None:
    now = datetime.now()
    if not _should_run_content_guard_audit(now):
        return None
    gaps = audit_content_guard()
    _mark_content_guard_audit_ran(now)
    return gaps


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


def _model_family(model_name: str) -> str:
    """Return a normalized family identifier for a model name."""
    try:
        from config import MODELS

        provider = MODELS.get(model_name, {}).get("provider", "")
        if provider:
            return provider
    except (ImportError, AttributeError):
        pass
    if model_name.startswith("claude"):
        return "claude"
    return model_name


def score_all(days: int = 7) -> dict:
    """Full hierarchical assessment: per-agent + super + aggregate."""
    result = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "agents": {},
        "super": {},
        "aggregate": {},
    }

    try:
        from config import DEFAULT_MODEL

        eval_model = DEFAULT_MODEL
        eval_family = _model_family(eval_model)
        flagged_agents = []
        for name in AGENT_CRITERIA:
            agent_model = AGENT_CRITERIA[name].get("model", eval_model)
            if _model_family(agent_model) == eval_family:
                flagged_agents.append(name)
        if flagged_agents:
            _mv_caveat = (
                f"MEASUREMENT VALIDITY CAVEAT: evaluator and evaluated agents share the same model family "
                f"({eval_family}). Scores may reflect training-distribution overlap rather than independent "
                f"capability assessment. Treat numerical scores as relative rankings within this model family, "
                f"not absolute quality measures."
            )
            log.warning(
                "MEASUREMENT_VALIDITY eval_model=%s family=%s flagged_agents=%r",
                eval_model,
                eval_family,
                flagged_agents,
            )
            result["measurement_validity_caveat"] = _mv_caveat
    except (ImportError, AttributeError):
        pass

    _rubric_state: dict = {}
    if _STATE_FILE.exists():
        try:
            _rubric_state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    _rubric_count = _rubric_state.get("rubric_audit_cycle_count", 0) + 1
    _rubric_state["rubric_audit_cycle_count"] = _rubric_count
    try:
        _STATE_FILE.write_text(json.dumps(_rubric_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as _e:
        log.debug("Could not save rubric audit state: %s", _e)
    if _rubric_count > RUBRIC_STALENESS_THRESHOLD:
        _rubric_warning = f"[RUBRIC UNVALIDATED — {_rubric_count} cycles since last confirmed audit]"
        log.warning(
            "RUBRIC_STALENESS cycle_count=%d threshold=%d — scores unvalidated, prefix applied",
            _rubric_count,
            RUBRIC_STALENESS_THRESHOLD,
        )
        result["rubric_unvalidated_warning"] = _rubric_warning

    content_guard_audit = _run_scheduled_content_guard_audit()
    if content_guard_audit is not None:
        result["content_guard_audit"] = {
            "schedule": _CONTENT_GUARD_AUDIT_SCHEDULE["name"],
            "gap_count": len(content_guard_audit),
            "gaps": content_guard_audit,
        }

    # Score each agent
    all_success_rates = []
    all_task_counts = []
    drift_flags = []
    for agent_name in AGENT_CRITERIA:
        card = score_agent(agent_name, days)
        result["agents"][agent_name] = card
        if card["task_count"] > 0:
            all_success_rates.append(card["success_rate"])
            all_task_counts.append(card["task_count"])
            _record_score(agent_name, card["success_rate"], result["generated_at"])
            drift = _detect_drift(agent_name)
            if drift.get("drift_detected"):
                log.warning(
                    "AGENT_DRIFT_DETECTED agent=%s slope=%.6f trend_direction=%s",
                    agent_name,
                    drift["slope"],
                    drift["trend_direction"],
                )
                _log_drift_warning(drift)
                card["drift"] = drift
                drift_flags.append(drift)
    result["drift_flags"] = drift_flags

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
    scored_agent_names = [name for name, card in result["agents"].items() if card["task_count"] > 0]
    outcome_backed_count = sum(1 for name in scored_agent_names if _agent_has_outcome_metric(name))
    agg["outcome_coverage"] = round(outcome_backed_count / len(scored_agent_names), 3) if scored_agent_names else 0.0
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

    _proxy_threshold = 0.20
    try:
        from config import PROXY_DRIFT_THRESHOLD

        _proxy_threshold = float(PROXY_DRIFT_THRESHOLD)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    _proxy_drift = _compute_proxy_drift(days)
    agg["proxy_false_positive_ratio"] = _proxy_drift["false_positive_ratio"]
    agg["proxy_audit_sample_count"] = _proxy_drift["sample_count"]
    if _proxy_drift["sample_count"] > 0 and _proxy_drift["false_positive_ratio"] > _proxy_threshold:
        _proxy_alert = {
            "message": "proxy calibration alert",
            "false_positive_ratio": _proxy_drift["false_positive_ratio"],
            "false_positive_count": _proxy_drift["false_positive_count"],
            "sample_count": _proxy_drift["sample_count"],
            "threshold": _proxy_threshold,
            "window_days": days,
        }
        log.warning(
            "PROXY_CALIBRATION_ALERT false_positive_ratio=%.3f false_positive_count=%d sample_count=%d threshold=%.2f",
            _proxy_drift["false_positive_ratio"],
            _proxy_drift["false_positive_count"],
            _proxy_drift["sample_count"],
            _proxy_threshold,
        )
        result["proxy_calibration_alert"] = _proxy_alert
        agg["proxy_calibration_alert"] = _proxy_alert

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
_SKILLS_META_FILE = _AGENTS_DIR / "shared" / "soul" / "skills_meta.json"


def _normalize_skill_ref(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def _load_skill_validation_meta() -> dict[str, dict]:
    if not _SKILLS_META_FILE.exists():
        return {}
    try:
        payload = json.loads(_SKILLS_META_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    skills = payload.get("skills") if isinstance(payload, dict) else None
    return skills if isinstance(skills, dict) else {}


def _save_skill_validation_meta(skills: dict[str, dict]) -> None:
    try:
        _SKILLS_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "skills": skills}
        tmp_path = _SKILLS_META_FILE.with_suffix(_SKILLS_META_FILE.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(_SKILLS_META_FILE)
    except OSError as e:
        log.debug("Could not save skill validation metadata: %s", e)


def _parse_utc_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_skill_index(index_path: Path) -> list[dict]:
    if not index_path.exists():
        return []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _skill_meta_key(source: str, entry: dict) -> str:
    return f"{source}/{entry.get('file') or _normalize_skill_ref(entry.get('name'))}"


def _iter_skill_entries() -> list[dict]:
    from config import SKILLS_DIR

    records: list[dict] = []
    for entry in _load_skill_index(SKILLS_DIR / "index.json"):
        records.append({"source": "learned", "entry": entry})

    for agent_index in _AGENTS_DIR.glob("*/skills/index.json"):
        agent_name = agent_index.parent.parent.name
        for entry in _load_skill_index(agent_index):
            records.append({"source": f"agent:{agent_name}", "entry": entry})
    return records


def _sync_skill_validation_meta() -> tuple[dict[str, dict], bool]:
    skills_meta = _load_skill_validation_meta()
    changed = False
    for record in _iter_skill_entries():
        entry = record["entry"]
        source = record["source"]
        key = _skill_meta_key(source, entry)
        if key not in skills_meta:
            skills_meta[key] = {
                "name": entry.get("name", "unknown"),
                "source": source,
                "file": entry.get("file", ""),
                "created_at": entry.get("created"),
                "validated_at": entry.get("validated_at") if "validated_at" in entry else entry.get("created"),
                "last_invoked": entry.get("last_invoked"),
            }
            changed = True
            continue

        metadata = skills_meta[key]
        for field, value in (
            ("name", entry.get("name", "unknown")),
            ("source", source),
            ("file", entry.get("file", "")),
            ("created_at", entry.get("created")),
            ("last_invoked", entry.get("last_invoked")),
        ):
            if metadata.get(field) != value:
                metadata[field] = value
                changed = True
        if "validated_at" not in metadata:
            metadata["validated_at"] = entry.get("validated_at") if "validated_at" in entry else entry.get("created")
            changed = True
    return skills_meta, changed


def _extract_used_skill_names(tasks: list[dict]) -> set[str]:
    used: set[str] = set()
    for task in tasks:
        for key in ("used_skills", "skills", "skill_names", "loaded_skills"):
            raw = task.get(key)
            if isinstance(raw, str):
                used.add(_normalize_skill_ref(raw))
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        used.add(_normalize_skill_ref(item.get("name") or item.get("file")))
                    else:
                        used.add(_normalize_skill_ref(item))
    return {name for name in used if name}


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


def _compute_proxy_drift(days: int) -> dict:
    """Return proxy false-positive stats from proxy_drift.jsonl over the scoring window."""
    try:
        from config import MIRA_ROOT

        drift_log = MIRA_ROOT / "data" / "proxy_drift.jsonl"
        if not drift_log.exists():
            return {"sample_count": 0, "false_positive_count": 0, "false_positive_ratio": 0.0}
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        sample_count = 0
        false_positive_count = 0
        for line in drift_log.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            try:
                entry = json.loads(line)
                ts_str = entry.get("timestamp", "")
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
                if ts and ts < cutoff:
                    continue
                if not entry.get("proxy_passed"):
                    continue
                sample_count += 1
                secondary = str(entry.get("secondary_check_result", ""))
                if secondary.startswith("proxy_false_positive"):
                    false_positive_count += 1
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        ratio = round(false_positive_count / max(sample_count, 1), 3)
        return {
            "sample_count": sample_count,
            "false_positive_count": false_positive_count,
            "false_positive_ratio": ratio,
        }
    except Exception:
        return {"sample_count": 0, "false_positive_count": 0, "false_positive_ratio": 0.0}


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


def _update_agent_skill_validation(agent_name: str, used_skill_names: set[str]) -> None:
    """Set validated_at=now for explicitly used skills after a positive score."""
    if not used_skill_names:
        return

    skills_meta, changed = _sync_skill_validation_meta()
    now_str = datetime.now(timezone.utc).isoformat()
    allowed_sources = {f"agent:{agent_name}", "learned"}
    for metadata in skills_meta.values():
        if metadata.get("source") not in allowed_sources:
            continue
        refs = {
            _normalize_skill_ref(metadata.get("name")),
            _normalize_skill_ref(metadata.get("file")),
        }
        if refs & used_skill_names:
            metadata["validated_at"] = now_str
            changed = True
    if changed:
        _save_skill_validation_meta(skills_meta)


def scan_stale_skills() -> list[dict]:
    """Scan all skill indices and return SKILL_STALE entries for stale or unvalidated skills."""
    from config import SKILL_STALENESS_DAYS

    threshold = timedelta(days=SKILL_STALENESS_DAYS)
    now = datetime.now(timezone.utc)
    cutoff = now - threshold
    warnings = []

    skills_meta, changed = _sync_skill_validation_meta()
    if changed:
        _save_skill_validation_meta(skills_meta)

    for metadata in skills_meta.values():
        name = metadata.get("name", "unknown")
        source = metadata.get("source", "unknown")
        vat = metadata.get("validated_at")
        validated_at = _parse_utc_datetime(vat)
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
        elif validated_at is None:
            warnings.append(
                {
                    "event": "SKILL_STALE",
                    "skill": name,
                    "source": source,
                    "reason": "invalid validated_at format",
                    "validated_at": vat,
                }
            )
        elif validated_at < cutoff:
            age_days = (now - validated_at).days
            warnings.append(
                {
                    "event": "SKILL_STALE",
                    "skill": name,
                    "source": source,
                    "reason": f"not validated in {age_days}d (threshold: {SKILL_STALENESS_DAYS}d)",
                    "validated_at": vat,
                }
            )

        last_invoked = _parse_utc_datetime(metadata.get("last_invoked"))
        created_at = _parse_utc_datetime(metadata.get("created_at"))
        if not metadata.get("last_invoked") and created_at and created_at < cutoff:
            age_days = (now - created_at).days
            warnings.append(
                {
                    "event": "SKILL_STALE",
                    "skill": name,
                    "source": source,
                    "reason": f"never invoked in {age_days}d (threshold: {SKILL_STALENESS_DAYS}d)",
                    "validated_at": vat,
                    "last_invoked": None,
                }
            )
        elif last_invoked and last_invoked < cutoff:
            age_days = (now - last_invoked).days
            warnings.append(
                {
                    "event": "SKILL_STALE",
                    "skill": name,
                    "source": source,
                    "reason": f"not invoked in {age_days}d (threshold: {SKILL_STALENESS_DAYS}d)",
                    "validated_at": vat,
                    "last_invoked": metadata.get("last_invoked"),
                }
            )

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


def confirm_rubric_audit() -> None:
    """Reset the rubric audit cycle counter. Call only when WA confirms external rubric audit."""
    state: dict = {}
    if _STATE_FILE.exists():
        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    state["rubric_audit_cycle_count"] = 0
    try:
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Rubric audit confirmed — cycle count reset to 0")
    except OSError as e:
        log.warning("Could not save rubric audit state: %s", e)


# ---------------------------------------------------------------------------
# Top-down improvement: Mira diagnosis → targeted sub-agent fixes
# ---------------------------------------------------------------------------


_ACTIONABLE_IMPROVEMENT_VERB_RE = re.compile(
    r"\b(?:add|modify|set|update|remove|create|change|tune)\b",
    re.IGNORECASE,
)
_IMPROVEMENT_ITEM_RE = re.compile(r"^\s*(?:[-*]\s+|\d+[.)]\s+)")


def _needs_actionable_improvement_correction(plan: str) -> bool:
    items = [line.strip() for line in plan.splitlines() if _IMPROVEMENT_ITEM_RE.match(line)]
    if not items:
        return False
    actionable = sum(1 for item in items if _ACTIONABLE_IMPROVEMENT_VERB_RE.search(item))
    return actionable * 2 < len(items)


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
            if _needs_actionable_improvement_correction(plan):
                corrective_prompt = (
                    prompt
                    + "\n\n## Draft Improvement Plan\n"
                    + plan
                    + "\n\nPlease convert each remaining diagnostic insight into a concrete action item."
                )
                corrected_plan = model_think(
                    corrective_prompt,
                    model_name=CLAUDE_FALLBACK_MODEL,
                    system="You are a senior engineering manager.",
                    timeout=90,
                )
                if corrected_plan:
                    plan = corrected_plan

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
                if scored_keys and all(_get_metric_type(name, k) == "proxy" for k in scored_keys):
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

    rubric_warning = assessment.get("rubric_unvalidated_warning", "")
    low_conf_agents = set(agg.get("low_confidence_agents", []))
    for name, card in sorted(assessment["agents"].items()):
        if card["task_count"] == 0:
            lines.append(f"- **{name}**: no tasks")
        else:
            emoji = "✅" if card["success_rate"] >= 0.8 else "⚠️" if card["success_rate"] >= 0.5 else "❌"
            suffix = " [low confidence — score history thin or stale]" if name in low_conf_agents else ""
            score_prefix = f"{rubric_warning} " if rubric_warning else ""
            lines.append(
                f"- {score_prefix}**{name}** {emoji}: {card['success_rate']:.0%} "
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

    # Skill learning silence check
    try:
        from config import SOUL_DIR

        ts_file = SOUL_DIR / "last_skill_extracted_at.txt"
        if ts_file.exists():
            last_ts = datetime.fromisoformat(ts_file.read_text(encoding="utf-8").strip())
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            hours_silent = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
            if hours_silent > 48:
                lines.extend(
                    [
                        "",
                        "## Skill Learning Warning",
                        f"Skill learning silent for {hours_silent:.0f}h — check explorer pipeline and feed sources.",
                    ]
                )
    except Exception as _e:
        log.debug("Could not check skill learning silence: %s", _e)

    # Check outcomes of previous improvement plans
    outcomes = check_improvement_outcomes(assessment)
    if outcomes:
        lines.extend(["", "## Improvement Tracking (last plans)", outcomes])

    if plan:
        lines.extend(["", "## Improvement Plan", plan])

    report = "\n".join(lines)

    caveat = assessment.get("measurement_validity_caveat", "")
    if caveat:
        report = caveat + "\n\n" + report

    # Write to workspace
    (workspace / "output.md").write_text(report, encoding="utf-8")

    return report
