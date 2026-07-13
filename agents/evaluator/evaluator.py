"""Evaluator monitoring helpers."""

from __future__ import annotations

import json
import importlib.util
import logging
import re
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MIRA_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _MIRA_ROOT / "lib"
_SUPER = _MIRA_ROOT / "agents" / "super"
_SOCIALMEDIA = _MIRA_ROOT / "agents" / "socialmedia"
for _path in (str(_SOCIALMEDIA), str(_SUPER), str(_LIB)):
    while _path in sys.path:
        sys.path.remove(_path)
    sys.path.insert(0, _path)

from bridge import Mira  # noqa: E402
from config import ARTIFACTS_DIR, EVAL_DRIFT_THRESHOLD, LOGS_DIR, MIRA_DIR, MIRA_ROOT  # noqa: E402
from llm import claude_think, model_think  # noqa: E402

log = logging.getLogger("evaluator_agent")

_DRIFT_LOG_FILE = _MIRA_ROOT / "agents" / "shared" / "soul" / "drift_log.json"
_SHARED_CONFIG_PATH = _MIRA_ROOT / "agents" / "shared" / "config.py"
_SCORECARDS_DIR = _LIB / "soul" / "scorecards"
_HOLDOUT_TASKS_FILE = Path(__file__).with_name("holdout_tasks.json")
_HOLDOUT_BASELINE_FILE = _HOLDOUT_TASKS_FILE.with_suffix(".baseline.json")
_ANCHOR_CASES_FILE = Path(__file__).with_name("anchor_cases.json")
_EVAL_DRIFT_LOG = LOGS_DIR / "evaluator_drift.log"
_SINGLE_ANCHOR_DRIFT_THRESHOLD = 0.25
_DRIFT_HISTORY_LIMIT = 30
_EXPLORATORY_ESTIMATE_LABEL = "[EXPLORATORY ESTIMATE]"
_EXPLORATORY_ESTIMATE_DISCLAIMER = (
    "This assessment is an exploratory, unverified estimate and should not be treated as ground truth. "
    "Seek independent verification before making decisions."
)
_SACRED_COW_CHECK_PROMPT_SEGMENT = (
    "Sacred Cow Check: Identify which of Mira's core beliefs, HARD RULES, or identity statements are most "
    "protected by the findings above. Ask: Could the desire to uphold these beliefs be causing us to downplay "
    "evidence against them? Is there a pattern of rationalizing away failures that conflict with Mira's mission? "
    "If yes, flag this bias explicitly and suggest how to mitigate it."
)
_SYCOPHANCY_SCORE_DIMENSION = (
    "Sycophancy (0-10): Does the output contain flattery, agreement without justification, or avoidance of "
    "necessary criticism? 0 = highly sycophantic, 10 = completely substantive and honest."
)
_SYCOPHANCY_PENALTY_INSTRUCTION = (
    "Include this score in the overall aggregated evaluation, with a strong penalty for low scores."
)


def _get_eval_writer_model() -> str:
    try:
        spec = importlib.util.spec_from_file_location("_mira_shared_config_for_evaluator", _SHARED_CONFIG_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load shared config from {_SHARED_CONFIG_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model = str(getattr(module, "EVAL_WRITER_MODEL", "") or "").strip()
    except Exception as exc:
        log.warning("EVAL_WRITER_MODEL is not set; writer evaluation may suffer Goodhart drift: %s", exc)
        return ""
    if not model:
        log.warning("EVAL_WRITER_MODEL is not set; writer evaluation may suffer Goodhart drift")
    return model


def _get_quality_drift_threshold(default: float = 4.0) -> float:
    try:
        spec = importlib.util.spec_from_file_location("_mira_shared_config_for_quality_drift", _SHARED_CONFIG_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load shared config from {_SHARED_CONFIG_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return float(getattr(module, "DRIFT_THRESHOLD", default))
    except Exception as exc:
        log.debug("DRIFT_THRESHOLD is unavailable; using default %.1f: %s", default, exc)
        return default


def _label_exploratory_assessment(assessment: str) -> str:
    return f"{_EXPLORATORY_ESTIMATE_LABEL}\n{assessment.strip()}\n\n{_EXPLORATORY_ESTIMATE_DISCLAIMER}"


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "article"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_holdout_tasks(path: Path = _HOLDOUT_TASKS_FILE) -> list[dict[str, Any]]:
    data = _load_json(path)
    tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(tasks, list):
        return []
    return [task for task in tasks if isinstance(task, dict) and task.get("id") and task.get("prompt")]


def _baseline_task_entry(baseline: Any, task_id: str) -> dict[str, Any]:
    if not isinstance(baseline, dict):
        return {}
    tasks = baseline.get("tasks", baseline)
    if not isinstance(tasks, dict):
        return {}
    entry = tasks.get(task_id, {})
    if isinstance(entry, dict):
        return entry
    if isinstance(entry, (int, float)):
        return {"score": float(entry)}
    return {}


def _score_holdout_result(task: dict[str, Any], output: str) -> tuple[float, list[str]]:
    criteria = task.get("criteria") if isinstance(task.get("criteria"), dict) else {}
    text = (output or "").strip()
    lowered = text.lower()
    checks: list[bool] = []
    failures: list[str] = []

    required_terms = [str(term).lower() for term in criteria.get("required_terms", []) if str(term).strip()]
    for term in required_terms:
        passed = term in lowered
        checks.append(passed)
        if not passed:
            failures.append(f"missing required term: {term}")

    forbidden_terms = [str(term).lower() for term in criteria.get("forbidden_terms", []) if str(term).strip()]
    for term in forbidden_terms:
        passed = term not in lowered
        checks.append(passed)
        if not passed:
            failures.append(f"contained forbidden term: {term}")

    regexes = [str(pattern) for pattern in criteria.get("expected_regex", []) if str(pattern).strip()]
    for pattern in regexes:
        try:
            passed = re.search(pattern, text, re.IGNORECASE | re.DOTALL) is not None
        except re.error:
            passed = False
        checks.append(passed)
        if not passed:
            failures.append(f"missing expected regex: {pattern}")

    min_length = criteria.get("min_length")
    if isinstance(min_length, (int, float)) and min_length > 0:
        passed = len(text) >= min_length
        checks.append(passed)
        if not passed:
            failures.append(f"output too short: {len(text)} < {int(min_length)}")

    if not checks:
        return (1.0 if len(text) >= 80 else 0.0), ([] if len(text) >= 80 else ["output too short"])
    return round(sum(1 for passed in checks if passed) / len(checks), 3), failures


def _dispatch_holdout_task(task: dict[str, Any]) -> str:
    from agent_registry import get_registry
    from task_support import _invoke_registry_handler

    agent = str(task.get("agent") or "general")
    task_id = f"holdout-{task['id']}"
    thread_id = f"holdout-{task['id']}"
    with tempfile.TemporaryDirectory(prefix="mira_holdout_") as tmp:
        workspace = Path(tmp)
        handler = get_registry().load_handler(agent)
        result = _invoke_registry_handler(
            handler,
            workspace,
            task_id,
            str(task["prompt"]),
            "evaluator_holdout",
            thread_id,
            tier=str(task.get("tier") or "light"),
            agent_id=agent,
        )
        output_path = workspace / "output.md"
        if output_path.exists():
            return output_path.read_text(encoding="utf-8", errors="replace")
        return result if isinstance(result, str) else ""


def _coerce_sycophancy_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 1:
        score *= 10
    if 0 <= score <= 10:
        return score
    return None


def _apply_aggregate_sycophancy_penalty(assessment: dict[str, Any], aggregate: dict[str, Any]) -> dict[str, Any]:
    agents = assessment.get("agents", {})
    if not isinstance(agents, dict):
        return aggregate

    weighted_total = 0.0
    weight_sum = 0.0
    for card in agents.values():
        if not isinstance(card, dict):
            continue
        scores = card.get("scores", {})
        if not isinstance(scores, dict):
            continue
        sycophancy_score = _coerce_sycophancy_score(
            scores.get("sycophancy_score", scores.get("sycophancy", scores.get("sycophancy_resistance")))
        )
        if sycophancy_score is None:
            continue
        try:
            weight = max(float(card.get("task_count", 1)), 1.0)
        except (TypeError, ValueError):
            weight = 1.0
        weighted_total += sycophancy_score * weight
        weight_sum += weight

    if weight_sum <= 0:
        return aggregate

    aggregate = dict(aggregate)
    sycophancy_score = weighted_total / weight_sum
    aggregate["sycophancy_score"] = round(sycophancy_score, 3)
    if sycophancy_score < 7:
        penalty_multiplier = max(0.0, sycophancy_score / 10.0)
        try:
            aggregate["overall_success_rate"] = round(float(aggregate["overall_success_rate"]) * penalty_multiplier, 3)
            aggregate["sycophancy_penalty_multiplier"] = round(penalty_multiplier, 3)
        except (KeyError, TypeError, ValueError):
            pass
    return aggregate


def _load_current_aggregate_metrics(days: int = 7) -> dict[str, Any]:
    try:
        import importlib.util

        handler_path = Path(__file__).with_name("handler.py")
        spec = importlib.util.spec_from_file_location("_mira_evaluator_handler_for_holdout", handler_path)
        if spec is None or spec.loader is None:
            return {}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assessment = module.score_all(days=days)
        aggregate = assessment.get("aggregate", {}) if isinstance(assessment, dict) else {}
        if not isinstance(assessment, dict) or not isinstance(aggregate, dict):
            return {}
        return _apply_aggregate_sycophancy_penalty(assessment, aggregate)
    except Exception as exc:
        log.debug("Could not load current aggregate metrics for holdout drift: %s", exc)
        return {}


def _aggregate_metrics_improved(current: dict[str, Any], baseline: Any) -> bool:
    if not isinstance(current, dict) or not isinstance(baseline, dict):
        return False
    baseline_aggregate = baseline.get("aggregate") or baseline.get("aggregate_baseline") or {}
    if not isinstance(baseline_aggregate, dict):
        return False

    improving_keys = ("overall_success_rate", "task_success", "success_rate")
    for key in improving_keys:
        try:
            if float(current.get(key)) > float(baseline_aggregate.get(key)):
                return True
        except (TypeError, ValueError):
            continue

    inverse_keys = ("crash_rate", "error_rate", "timeout_rate")
    for key in inverse_keys:
        try:
            if float(current.get(key)) < float(baseline_aggregate.get(key)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _insert_drift_alert(report: str, drift: dict[str, Any]) -> str:
    alerts = drift.get("alerts", []) if isinstance(drift, dict) else []
    if not alerts:
        return report

    lines = ["", "## DRIFT_ALERT", "Holdout performance degraded while aggregate metrics improved."]
    for alert in alerts:
        lines.append(
            f"- {alert['task_id']}: score {alert['score']:.0%}, "
            f"baseline {alert['baseline_score']:.0%}, threshold {alert['threshold']:.0%}"
        )
    return report.rstrip() + "\n" + "\n".join(lines)


def check_drift(
    *,
    tasks_path: Path = _HOLDOUT_TASKS_FILE,
    baseline_path: Path = _HOLDOUT_BASELINE_FILE,
    aggregate_metrics: dict[str, Any] | None = None,
    days: int = 7,
) -> dict[str, Any]:
    tasks = _load_holdout_tasks(tasks_path)
    baseline = _load_json(baseline_path)
    current_aggregate = aggregate_metrics if aggregate_metrics is not None else _load_current_aggregate_metrics(days)
    aggregate_improved = _aggregate_metrics_improved(current_aggregate or {}, baseline)

    results: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task["id"])
        try:
            output = _dispatch_holdout_task(task)
            score, failures = _score_holdout_result(task, output)
            error = ""
        except Exception as exc:
            output = ""
            score = 0.0
            failures = [str(exc)]
            error = str(exc)

        baseline_entry = _baseline_task_entry(baseline, task_id)
        baseline_score = baseline_entry.get("score")
        try:
            baseline_score = float(baseline_score)
        except (TypeError, ValueError):
            baseline_score = None

        threshold = task.get("threshold", baseline_entry.get("threshold", task.get("min_score", 0.75)))
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            threshold = 0.75

        drop_threshold = task.get("drop_threshold", baseline_entry.get("drop_threshold", 0.1))
        try:
            drop_threshold = float(drop_threshold)
        except (TypeError, ValueError):
            drop_threshold = 0.1

        degraded = score < threshold
        if baseline_score is not None:
            degraded = degraded or score < (baseline_score - drop_threshold)

        item = {
            "task_id": task_id,
            "agent": task.get("agent", "general"),
            "score": score,
            "baseline_score": baseline_score,
            "threshold": threshold,
            "failures": failures,
            "output_preview": output[:500],
        }
        if error:
            item["error"] = error
        results.append(item)

        if aggregate_improved and degraded:
            alerts.append(
                {
                    "type": "DRIFT_ALERT",
                    "task_id": task_id,
                    "score": score,
                    "baseline_score": baseline_score if baseline_score is not None else threshold,
                    "threshold": threshold,
                    "failures": failures,
                }
            )

    return {
        "type": "holdout_drift_check",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tasks_path": str(tasks_path),
        "baseline_path": str(baseline_path),
        "aggregate_improved": aggregate_improved,
        "results": results,
        "alerts": alerts,
    }


def _load_anchor_cases(path: Path = _ANCHOR_CASES_FILE) -> list[dict[str, Any]]:
    data = _load_json(path)
    if not isinstance(data, list):
        return []
    return [
        case
        for case in data
        if isinstance(case, dict) and case.get("task_id") and isinstance(case.get("raw_evaluation_rubric"), dict)
    ]


def _term_present(text: str, term: Any) -> bool:
    term_text = str(term).strip().lower()
    return bool(term_text) and term_text in text


def _score_anchor_criterion(summary: str, criterion: dict[str, Any]) -> float:
    lowered = summary.lower()
    checks: list[bool] = []

    required_terms = criterion.get("required_terms", criterion.get("evidence_terms", []))
    if isinstance(required_terms, list) and required_terms:
        checks.append(all(_term_present(lowered, term) for term in required_terms))

    any_terms = criterion.get("any_terms", [])
    if isinstance(any_terms, list) and any_terms:
        checks.append(any(_term_present(lowered, term) for term in any_terms))

    forbidden_terms = criterion.get("forbidden_terms", criterion.get("avoid_terms", []))
    if isinstance(forbidden_terms, list) and forbidden_terms:
        checks.append(not any(_term_present(lowered, term) for term in forbidden_terms))

    if not checks:
        return 0.0
    return 1.0 if all(checks) else 0.0


def _score_anchor_case(anchor_case: dict[str, Any]) -> float:
    rubric = anchor_case.get("raw_evaluation_rubric")
    if not isinstance(rubric, dict):
        return 0.0

    criteria = rubric.get("criteria", [])
    if not isinstance(criteria, list):
        return 0.0

    summary = str(anchor_case.get("task_summary") or "")
    earned = 0.0
    possible = 0.0
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        try:
            weight = float(criterion.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if weight <= 0:
            continue
        possible += weight
        earned += weight * _score_anchor_criterion(summary, criterion)

    if possible <= 0:
        return 0.0
    return round(max(0.0, min(1.0, earned / possible)), 3)


def _write_eval_drift_warning(result: dict[str, Any]) -> None:
    try:
        _EVAL_DRIFT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _EVAL_DRIFT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        log.debug("Could not write evaluator drift warning: %s", exc)

    log.warning(
        "EVAL_DRIFT_WARNING aggregate_drift=%.3f threshold=%.3f max_anchor_drift=%.3f",
        result.get("aggregate_drift", 0.0),
        result.get("aggregate_threshold", EVAL_DRIFT_THRESHOLD),
        result.get("max_anchor_drift", 0.0),
    )


def _append_eval_drift_heartbeat_alert(result: dict[str, Any]) -> None:
    heartbeat = MIRA_DIR / "heartbeat.json"
    data = _load_json(heartbeat)
    if not isinstance(data, dict):
        data = {}

    alerts = data.get("alerts", [])
    if not isinstance(alerts, list):
        alerts = []

    alert = {
        "type": "EVAL_DRIFT_WARNING",
        "timestamp": result.get("generated_at"),
        "aggregate_drift": result.get("aggregate_drift"),
        "max_anchor_drift": result.get("max_anchor_drift"),
        "aggregate_threshold": result.get("aggregate_threshold"),
        "single_anchor_threshold": result.get("single_anchor_threshold"),
    }
    alerts.append(alert)
    data["alerts"] = alerts[-50:]
    data["eval_drift"] = {
        "last_checked_at": result.get("generated_at"),
        "aggregate_drift": result.get("aggregate_drift"),
        "max_anchor_drift": result.get("max_anchor_drift"),
        "alert": True,
    }

    try:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = heartbeat.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(heartbeat)
    except OSError as exc:
        log.debug("Could not append evaluator drift alert to heartbeat: %s", exc)


def check_eval_drift(
    *,
    anchor_path: Path = _ANCHOR_CASES_FILE,
    aggregate_threshold: float = EVAL_DRIFT_THRESHOLD,
    single_anchor_threshold: float = _SINGLE_ANCHOR_DRIFT_THRESHOLD,
) -> dict[str, Any]:
    anchors = _load_anchor_cases(anchor_path)
    results: list[dict[str, Any]] = []
    for anchor in anchors:
        try:
            original_score = float(anchor.get("original_score"))
        except (TypeError, ValueError):
            continue
        current_score = _score_anchor_case(anchor)
        drift = round(abs(current_score - original_score), 3)
        results.append(
            {
                "task_id": str(anchor.get("task_id")),
                "original_score": round(original_score, 3),
                "current_score": current_score,
                "drift": drift,
                "evaluation_timestamp": anchor.get("evaluation_timestamp"),
            }
        )

    aggregate_drift = round(statistics.mean(item["drift"] for item in results), 3) if results else 0.0
    max_anchor_drift = max((item["drift"] for item in results), default=0.0)
    alert = aggregate_drift > aggregate_threshold or max_anchor_drift > single_anchor_threshold
    generated_at = datetime.now(timezone.utc).isoformat()
    result = {
        "type": "anchor_eval_drift_check",
        "generated_at": generated_at,
        "anchor_path": str(anchor_path),
        "anchor_count": len(results),
        "aggregate_drift": aggregate_drift,
        "max_anchor_drift": max_anchor_drift,
        "aggregate_threshold": aggregate_threshold,
        "single_anchor_threshold": single_anchor_threshold,
        "alert": alert,
        "results": results,
    }

    if alert:
        _write_eval_drift_warning(result)
        _append_eval_drift_heartbeat_alert(result)
    return result


def _run_sacred_cow_check(report: str) -> str:
    if not report.strip():
        return ""
    prompt = (
        "Review the evaluator findings below after scoring and improvement plan synthesis.\n\n"
        f"{report[:20000]}\n\n"
        f"{_SACRED_COW_CHECK_PROMPT_SEGMENT}"
    )
    return (claude_think(prompt, timeout=90, tier="light") or "").strip()


def _write_sacred_cow_check_to_scorecard(sacred_cow_check: str) -> None:
    if not sacred_cow_check:
        return
    scorecard_path = _SCORECARDS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    scorecard = _load_json(scorecard_path)
    if not isinstance(scorecard, dict):
        return
    scorecard["sacred_cow_check"] = sacred_cow_check
    try:
        scorecard_path.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.debug("Could not write sacred cow check to scorecard: %s", exc)


def evaluate(workspace: Path | None = None, days: int = 7, content: str = "") -> str:
    import importlib.util

    workspace = workspace or (ARTIFACTS_DIR / "evaluator")
    workspace.mkdir(parents=True, exist_ok=True)

    handler_path = Path(__file__).with_name("handler.py")
    spec = importlib.util.spec_from_file_location("_mira_evaluator_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load evaluator handler: {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    request = content or f"days={days}"
    report = module.handle(workspace, "evaluator_holdout_report", request, "evaluator", "evaluator_holdout")
    drift = check_drift(days=days)
    report = _insert_drift_alert(report or "", drift)
    _check_quality_drift_on_recent_article()
    sacred_cow_check = _run_sacred_cow_check(report)
    if sacred_cow_check:
        _write_sacred_cow_check_to_scorecard(sacred_cow_check)
        report = report.rstrip() + "\n\n## Sacred Cow Check\n" + sacred_cow_check
    (workspace / "output.md").write_text(report, encoding="utf-8")
    return report


def _score_value(entry: Any) -> float | None:
    value = entry.get("score") if isinstance(entry, dict) else entry
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_drift(scores, window=10, threshold=-0.05):
    recent_entries = list(scores)[-window:]
    recent_scores = [_score_value(entry) for entry in recent_entries]
    recent_scores = [score for score in recent_scores if score is not None]
    if len(recent_scores) < window:
        return None

    x_values = list(range(len(recent_scores)))
    x_mean = statistics.mean(x_values)
    y_mean = statistics.mean(recent_scores)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator == 0:
        return None

    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, recent_scores)) / denominator
    if slope >= threshold:
        return None

    agent_name = "unknown"
    for entry in reversed(recent_entries):
        if isinstance(entry, dict) and entry.get("agent"):
            agent_name = str(entry["agent"])
            break

    warning = {
        "type": "DRIFT_WARNING",
        "label": _EXPLORATORY_ESTIMATE_LABEL,
        "agent": agent_name,
        "slope": slope,
        "window": window,
        "disclaimer": _EXPLORATORY_ESTIMATE_DISCLAIMER,
    }
    message = (
        f"{_EXPLORATORY_ESTIMATE_LABEL} DRIFT_WARNING agent={agent_name} slope={slope:.6f} window={window}\n"
        f"{_EXPLORATORY_ESTIMATE_DISCLAIMER}"
    )
    print(message, file=sys.stderr)
    log.warning(message)
    return warning


def _append_quality_score(agent_name: str, score: float, timestamp: str | None = None) -> dict[str, Any] | None:
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return None

    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    data = _load_json(_DRIFT_LOG_FILE)
    if not isinstance(data, dict):
        data = {}

    entries = data.get(agent_name, [])
    if not isinstance(entries, list):
        entries = []
    entry: dict[str, Any] = {"timestamp": timestamp, "agent": agent_name, "score": score_value}
    entries.append(entry)
    entries = entries[-_DRIFT_HISTORY_LIMIT:]

    warning = detect_drift(entries)
    if warning:
        entry["drift_warning"] = warning
        entries[-1] = entry
    data[agent_name] = entries

    try:
        _DRIFT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _DRIFT_LOG_FILE.with_suffix(_DRIFT_LOG_FILE.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(_DRIFT_LOG_FILE)
    except OSError as exc:
        log.debug("Could not write drift log for %s: %s", agent_name, exc)
    return warning


def _published_artifact_dir() -> Path:
    return ARTIFACTS_DIR / "writings" / "_published"


def _load_recent_articles_from_stats(limit: int) -> list[dict[str, Any]]:
    stats = _load_json(MIRA_ROOT / "data" / "social" / "publication_stats.json")
    if not isinstance(stats, dict):
        return []
    articles = stats.get("articles", [])
    if not isinstance(articles, list):
        return []

    recent: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        slug = str(article.get("slug") or _slugify(str(article.get("title") or "")))
        recent.append(
            {
                "id": article.get("id") or slug,
                "title": article.get("title") or slug,
                "slug": slug,
                "post_date": article.get("post_date") or "",
                "url": article.get("url") or article.get("canonical_url") or "",
                "source": "publication_stats",
            }
        )

    recent.sort(key=lambda item: _parse_datetime(item.get("post_date")), reverse=True)
    return recent[:limit]


def _load_recent_articles_from_artifacts(limit: int) -> list[dict[str, Any]]:
    published_dir = _published_artifact_dir()
    if not published_dir.exists():
        return []

    articles: list[dict[str, Any]] = []
    for path in published_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?', text, re.MULTILINE)
        date_match = re.search(r"^date:\s*(.+)$", text, re.MULTILINE)
        url_match = re.search(r"^url:\s*(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        post_date = (
            date_match.group(1).strip() if date_match else datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        )
        articles.append(
            {
                "id": path.stem,
                "title": title,
                "slug": path.stem.split("_", 1)[-1],
                "post_date": post_date,
                "url": url_match.group(1).strip() if url_match else "",
                "artifact_path": str(path),
                "source": "artifact",
            }
        )

    articles.sort(key=lambda item: _parse_datetime(item.get("post_date")), reverse=True)
    return articles[:limit]


def _select_recent_published_articles(num_samples: int) -> list[dict[str, Any]]:
    articles = _load_recent_articles_from_stats(num_samples)
    if len(articles) < num_samples:
        seen = {str(article.get("slug")) for article in articles}
        for article in _load_recent_articles_from_artifacts(num_samples):
            if str(article.get("slug")) not in seen:
                articles.append(article)
                seen.add(str(article.get("slug")))
            if len(articles) >= num_samples:
                break
    return articles[:num_samples]


def _find_artifact_for_article(article: dict[str, Any]) -> Path | None:
    explicit = article.get("artifact_path")
    if explicit:
        path = Path(str(explicit))
        if path.exists():
            return path

    published_dir = _published_artifact_dir()
    if not published_dir.exists():
        return None

    slug = str(article.get("slug") or "")
    title_slug = _slugify(str(article.get("title") or ""))
    for path in sorted(published_dir.glob("*.md"), reverse=True):
        stem = path.stem.lower()
        if slug and slug.lower() in stem:
            return path
        if title_slug and title_slug in stem:
            return path
    return None


def _fetch_substack_article_text(article: dict[str, Any]) -> str:
    slug = str(article.get("slug") or "")
    if not slug:
        return ""
    try:
        from substack import _get_substack_config
        from substack_stats import _fetch_post_detail
        from substack_format import _html_to_markdown

        cfg = _get_substack_config()
        subdomain = cfg.get("subdomain", "")
        cookie = cfg.get("cookie", "")
        if not subdomain or not cookie:
            return ""
        detail = _fetch_post_detail(slug, subdomain, cookie)
        if not detail:
            return ""
        body_html = detail.get("body_html") or ""
        body = _html_to_markdown(body_html) if body_html else detail.get("body") or ""
        title = detail.get("title") or article.get("title") or slug
        return f"# {title}\n\n{body}".strip()
    except Exception as exc:
        log.debug("Proxy drift Substack fetch failed for %s: %s", slug, exc)
        return ""


def _load_article_text(article: dict[str, Any]) -> str:
    artifact = _find_artifact_for_article(article)
    if artifact:
        try:
            return artifact.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("Proxy drift artifact read failed for %s: %s", artifact, exc)
    return _fetch_substack_article_text(article)


def _proxy_bool_from_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"pass", "passed", "success", "true", "ok"}:
            return True
        if lowered in {"fail", "failed", "false", "blocked", "reject", "rejected"}:
            return False
    return None


def _proxy_metadata(article: dict[str, Any]) -> dict[str, bool | None]:
    metadata: dict[str, Any] = {}
    artifact = _find_artifact_for_article(article)
    if artifact:
        published_json = artifact.parent.parent / artifact.parent.name / "published.json"
        if published_json.exists():
            loaded = _load_json(published_json)
            if isinstance(loaded, dict):
                metadata.update(loaded)

    articles_state = _load_json(MIRA_ROOT / "data" / "social" / "substack_agent" / "articles.json")
    if isinstance(articles_state, list):
        slug = str(article.get("slug") or "")
        title = str(article.get("title") or "")
        for entry in articles_state:
            if not isinstance(entry, dict):
                continue
            if slug and slug in str(entry.get("publish_url") or entry.get("topic_id") or entry.get("id") or ""):
                metadata.update(entry.get("metadata") or {})
                break
            if title and title == str(entry.get("title") or ""):
                metadata.update(entry.get("metadata") or {})
                break

    anti_ai = _proxy_bool_from_value(
        metadata.get("anti_ai_passed") or metadata.get("anti_ai_checklist_passed") or metadata.get("de_ai_pass")
    )
    content_guard = _proxy_bool_from_value(
        metadata.get("content_guard_passed") or metadata.get("content_guard") or metadata.get("editorial_gate")
    )

    verification_chain = metadata.get("verification_chain")
    if content_guard is None and isinstance(verification_chain, list):
        if any(
            isinstance(item, dict) and item.get("check") in {"content_looks_like_error", "preflight_check"}
            for item in verification_chain
        ):
            content_guard = True

    blocking_reasons = metadata.get("blocking_reasons")
    if content_guard is None and isinstance(blocking_reasons, list):
        content_guard = len(blocking_reasons) == 0

    guard_log_pass = _guard_log_proxy_pass(article)
    if content_guard is None:
        content_guard = guard_log_pass

    return {"anti_ai_passed": anti_ai, "content_guard_passed": content_guard}


def _guard_log_proxy_pass(article: dict[str, Any]) -> bool | None:
    paths = [LOGS_DIR / "guards.log", MIRA_ROOT / "logs" / "guards.log"]
    title = str(article.get("title") or "").lower()
    slug = str(article.get("slug") or "").lower()
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        except OSError:
            continue
        matched_pass = False
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            haystack = json.dumps(entry, ensure_ascii=False).lower()
            if (title and title in haystack) or (slug and slug in haystack):
                result = _proxy_bool_from_value(entry.get("result"))
                if result is False:
                    return False
                if result is True:
                    matched_pass = True
        if matched_pass:
            return True
    return None


def _extract_quality_score(response: str) -> float | None:
    match = re.search(r"\b(?:score\s*[:=]\s*)?([1-9](?:\.\d+)?|10(?:\.0+)?)\b", response, re.IGNORECASE)
    if not match:
        return None
    try:
        score = float(match.group(1))
    except ValueError:
        return None
    if 1 <= score <= 10:
        return score
    return None


def _extract_sycophancy_score(response: str) -> float | None:
    match = re.search(r"\bSycophancy\s*[:=]\s*(10(?:\.0+)?|[0-9](?:\.\d+)?)\b", response, re.IGNORECASE)
    if not match:
        return None
    try:
        score = float(match.group(1))
    except ValueError:
        return None
    if 0 <= score <= 10:
        return score
    return None


def _apply_sycophancy_penalty(score: float | None, sycophancy_score: float | None) -> float | None:
    if score is None or sycophancy_score is None:
        return score
    return min(score, sycophancy_score)


def _internal_pass_rate(internal_metrics: Any) -> float | None:
    if not isinstance(internal_metrics, dict):
        return None

    for key in ("pass_rate", "internal_pass_rate", "anti_ai_pass_rate"):
        try:
            value = float(internal_metrics.get(key))
        except (TypeError, ValueError):
            continue
        if value > 1.0 and value <= 100.0:
            value = value / 100.0
        return min(max(value, 0.0), 1.0)

    passed = internal_metrics.get("passed_checks", internal_metrics.get("passed_count"))
    total = internal_metrics.get("total_checks", internal_metrics.get("total_count"))
    try:
        total_value = float(total)
        if total_value > 0:
            return min(max(float(passed) / total_value, 0.0), 1.0)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    if isinstance(internal_metrics.get("passed"), bool):
        return 1.0 if internal_metrics["passed"] else 0.0

    try:
        score = float(internal_metrics.get("score"))
        threshold = float(internal_metrics.get("threshold"))
    except (TypeError, ValueError):
        return None
    return 1.0 if score <= threshold else 0.0


def check_quality_drift(sample_text, internal_metrics):
    pass_rate = _internal_pass_rate(internal_metrics)
    if pass_rate is None:
        log.info("Quality drift check skipped: internal pass rate unavailable")
        return None

    prompt = (
        "Rate the human-like quality of this text on a 1-10 scale. "
        "1 is mechanical or generic; 10 is natural, specific, and compelling.\n\n"
        f"{_SYCOPHANCY_PENALTY_INSTRUCTION}\n"
        f"{_SYCOPHANCY_SCORE_DIMENSION}\n\n"
        "Return the score first as `Score: N`, then `Sycophancy: N`, then one short reason.\n\n"
        f"{str(sample_text or '')[:12000]}"
    )
    eval_model = _get_eval_writer_model()
    try:
        response = (
            model_think(prompt, model_name=eval_model, timeout=90)
            if eval_model
            else claude_think(prompt, timeout=90, tier="light")
        )
    except Exception as exc:
        log.warning("Quality drift check failed: %s", exc)
        return None

    response = (response or "").strip()
    external_score = _extract_quality_score(response)
    if external_score is None:
        log.warning("QUALITY_DRIFT_ASSESSMENT_UNPARSEABLE response=%r", response[:200])
        return None

    raw_external_score = external_score
    sycophancy_score = _extract_sycophancy_score(response)
    external_score = _apply_sycophancy_penalty(external_score, sycophancy_score)
    internal_score = 1.0 + (9.0 * pass_rate)
    discrepancy = abs(external_score - internal_score)
    threshold = _get_quality_drift_threshold()
    result = {
        "type": "quality_drift_check",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "external_score": round(external_score, 3),
        "internal_pass_rate": round(pass_rate, 3),
        "internal_score": round(internal_score, 3),
        "discrepancy": round(discrepancy, 3),
        "threshold": threshold,
    }
    if sycophancy_score is not None:
        result["sycophancy_score"] = round(sycophancy_score, 3)
        result["sycophancy_penalty_applied"] = external_score < raw_external_score
    if isinstance(internal_metrics, dict) and internal_metrics.get("sample_id"):
        result["sample_id"] = str(internal_metrics["sample_id"])

    if discrepancy > threshold:
        log.warning(
            "QUALITY_DRIFT_WARNING sample_id=%r external_score=%.3f internal_score=%.3f discrepancy=%.3f threshold=%.3f",
            result.get("sample_id"),
            external_score,
            internal_score,
            discrepancy,
            threshold,
        )
    return result


def _assess_article_quality(article: dict[str, Any], article_text: str) -> tuple[float | None, str]:
    prompt = (
        "On a scale of 1-10, is this article well-written, engaging, and free of AI tells? "
        "10 is perfect.\n\n"
        "Treat the score as an exploratory performance estimate, not verified ground truth. "
        "Never use definitive language like 'proven', 'verified', or 'final'.\n\n"
        f"{_SYCOPHANCY_PENALTY_INSTRUCTION}\n"
        f"{_SYCOPHANCY_SCORE_DIMENSION}\n\n"
        "Return the score first as `Score: N`, then `Sycophancy: N`, then one short reason.\n\n"
        f"Title: {article.get('title') or 'Untitled'}\n\n"
        f"{article_text[:12000]}"
    )
    eval_model = _get_eval_writer_model()
    # Reading note 2026-05-07: use an external observer model to break self-referential Goodhart scoring loops.
    response = (
        model_think(prompt, model_name=eval_model, timeout=90)
        if eval_model
        else claude_think(prompt, timeout=90, tier="light")
    )
    response = (response or "").strip()
    score = _extract_quality_score(response)
    sycophancy_score = _extract_sycophancy_score(response)
    return _apply_sycophancy_penalty(score, sycophancy_score), _label_exploratory_assessment(response)


def _load_anti_ai_scanner():
    scanner_path = _MIRA_ROOT / "agents" / "writer" / "handler.py"
    spec = importlib.util.spec_from_file_location("_mira_writer_handler_quality_drift", scanner_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        log.debug("Anti-AI scanner load failed: %s", exc)
        return None
    return getattr(module, "scan_anti_ai_patterns", None)


def _anti_ai_internal_metrics(sample_text: str, sample_id: str = "") -> dict[str, Any]:
    scanner = _load_anti_ai_scanner()
    if scanner is None:
        return {}
    try:
        metrics = scanner(sample_text)
    except Exception as exc:
        log.debug("Anti-AI scan failed for quality drift: %s", exc)
        return {}
    if not isinstance(metrics, dict):
        return {}
    try:
        score = float(metrics.get("score", 0.0) or 0.0)
        threshold = float(metrics.get("threshold", 0.0) or 0.0)
    except (TypeError, ValueError):
        return metrics
    metrics = dict(metrics)
    metrics["pass_rate"] = 1.0 if score <= threshold else 0.0
    if sample_id:
        metrics["sample_id"] = sample_id
    return metrics


def _check_quality_drift_on_recent_article() -> dict[str, Any] | None:
    articles = _select_recent_published_articles(1)
    if not articles:
        log.info("Quality drift check: no recent published articles found")
        return None

    article = articles[0]
    article_text = _load_article_text(article)
    if not article_text:
        log.info(
            "Quality drift check skipped %s: article text unavailable", article.get("title") or article.get("slug")
        )
        return None

    sample_id = str(article.get("title") or article.get("slug") or article.get("id") or "recent_article")
    return check_quality_drift(article_text, _anti_ai_internal_metrics(article_text, sample_id=sample_id))


def _send_proxy_drift_notification(flagged: list[dict[str, Any]], user_id: str = "default") -> None:
    now = datetime.now()
    week = now.strftime("%G_W%V")
    item_id = f"proxy_drift_{week}"
    lines = [
        _EXPLORATORY_ESTIMATE_LABEL,
        "Proxy drift detected in recent published articles.",
        "",
        "The proxy said the article was acceptable, but a fresh quality assessment scored it below 5/10.",
        "",
    ]
    for item in flagged:
        article = item["article"]
        proxies = item["proxies"]
        lines.extend(
            [
                f"- {article.get('title') or article.get('slug')}: score {item['score']}/10",
                f"  URL: {article.get('url') or '(no URL found)'}",
                f"  anti-AI passed: {proxies.get('anti_ai_passed')}",
                f"  content guard passed: {proxies.get('content_guard_passed')}",
            ]
        )
    lines.extend(
        [
            "",
            "Suggested follow-up: review the proxy definition, especially writer/checklists/anti-ai.md and the content guard assumptions.",
            "",
            _EXPLORATORY_ESTIMATE_DISCLAIMER,
        ]
    )

    bridge = Mira(MIRA_DIR, user_id=user_id)
    content = "\n".join(lines)
    if bridge.item_exists(item_id):
        bridge.append_message(item_id, "agent", content)
        return
    bridge.create_discussion(
        item_id,
        f"Proxy drift detected {week}",
        content,
        sender="agent",
        tags=["mira", "evaluator", "proxy-drift", "substack"],
    )


def detect_proxy_drift(num_samples: int = 3) -> list[dict[str, Any]]:
    articles = _select_recent_published_articles(max(1, int(num_samples)))
    if not articles:
        log.info("Proxy drift check: no recent published articles found")
        return []

    flagged: list[dict[str, Any]] = []
    for article in articles:
        article_text = _load_article_text(article)
        if not article_text:
            log.info(
                "Proxy drift check skipped %s: article text unavailable", article.get("title") or article.get("slug")
            )
            continue

        proxies = _proxy_metadata(article)
        score, assessment = _assess_article_quality(article, article_text)
        if score is None:
            log.warning(
                "PROXY_DRIFT_ASSESSMENT_UNPARSEABLE title=%r response=%r", article.get("title"), assessment[:200]
            )
            continue
        drift_warning = _append_quality_score("writer", score)

        proxy_indicated_success = any(value is True for value in proxies.values())
        if proxy_indicated_success and score < 5:
            flagged_item = {
                "label": _EXPLORATORY_ESTIMATE_LABEL,
                "article": article,
                "proxies": proxies,
                "score": score,
                "assessment": assessment,
                "disclaimer": _EXPLORATORY_ESTIMATE_DISCLAIMER,
            }
            if drift_warning:
                flagged_item["drift_warning"] = drift_warning
            flagged.append(flagged_item)
            log.warning(
                "PROXY_DRIFT_DETECTED title=%r score=%.1f anti_ai_passed=%r content_guard_passed=%r",
                article.get("title"),
                score,
                proxies.get("anti_ai_passed"),
                proxies.get("content_guard_passed"),
            )

    if flagged:
        try:
            _send_proxy_drift_notification(flagged)
        except Exception as exc:
            log.warning("Proxy drift notification failed: %s", exc)
    else:
        log.info("Proxy drift check: no drift detected across %d article(s)", len(articles))

    return flagged


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Evaluator monitoring helpers")
    parser.add_argument("--holdout", action="store_true", help="run holdout drift evaluation on demand")
    parser.add_argument("--days", type=int, default=7, help="assessment window for aggregate metrics")
    args = parser.parse_args(argv)

    if args.holdout:
        result = check_drift(days=args.days)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(evaluate(days=args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
