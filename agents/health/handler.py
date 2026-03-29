"""Health agent — family health monitoring, privacy-first.

Tracks weight, sleep, vitals, symptoms, and checkup reports.
All data processing uses LOCAL LLM (Ollama) only — raw health data
never leaves localhost.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("health_agent")

# Force local LLM for all health data processing
os.environ["MIRA_FORCE_OLLAMA"] = "1"

from config import OLLAMA_DEFAULT_MODEL, DATABASE_URL
from sub_agent import _ollama_call


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "",
           tier: str = "light") -> str | None:
    """Main entry point for the health agent."""
    log.info("Health agent: task=%s content=%s", task_id, content[:80])

    from health_store import HealthStore
    store = HealthStore(DATABASE_URL)

    # Classify the input
    intent = _classify(content, sender)
    log.info("Classified: %s", intent)

    intent_type = intent.get("type", "query")
    person = intent.get("person", sender)

    if intent_type == "metric":
        return _handle_metric(store, workspace, task_id, intent, person)
    elif intent_type == "note":
        return _handle_note(store, workspace, task_id, intent, person)
    elif intent_type == "query":
        return _handle_query(store, workspace, task_id, content, person)
    elif intent_type == "report":
        return _handle_report_request(store, workspace, task_id, content, person)
    else:
        return _handle_query(store, workspace, task_id, content, person)


def _classify(content: str, sender: str) -> dict:
    """Use local LLM to classify the health message."""
    prompt = f"""Classify this health-related message. Return ONLY a JSON object.

Message: {content[:500]}
Sender: {sender}

Categories:
- "metric": User is recording a measurement (weight, blood pressure, temperature, etc.)
  Extract: {{"type": "metric", "person": "<who>", "metric_type": "<type>", "value": <number>, "unit": "<unit>"}}
- "note": User is reporting a symptom, medication, or health observation
  Extract: {{"type": "note", "person": "<who>", "category": "symptom|medication|diet|exercise|general", "content": "<cleaned note>"}}
- "query": User is asking about health data or trends
  Extract: {{"type": "query", "person": "<who>", "query": "<what they want to know>"}}
- "report": User wants a health report or summary
  Extract: {{"type": "report", "person": "<who>"}}

Rules:
- "person" defaults to "{sender}" unless another person is mentioned (e.g., 妈妈, 爸爸, liquan)
- For metric_type, normalize to: weight, sleep_hours, blood_pressure_sys, blood_pressure_dia, heart_rate, temperature, blood_sugar, steps
- For weight, default unit is "kg"; for temperature, "°C"

Return ONLY the JSON object, no explanation."""

    try:
        result = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, timeout=30)
        # Extract JSON from response
        text = result.strip()
        if "```" in text:
            text = text.split("```")[1].strip()
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
    except (json.JSONDecodeError, Exception) as e:
        log.warning("Classification failed: %s, defaulting to query", e)
        return {"type": "query", "person": sender}


def _handle_metric(store, workspace: Path, task_id: str,
                   intent: dict, person: str) -> str:
    """Record a health metric."""
    metric_type = intent.get("metric_type", "unknown")
    value = intent.get("value")
    unit = intent.get("unit", "")

    if value is None:
        return _write_result(workspace, task_id,
                             "I couldn't parse the value. Please try: 记录体重 72.5")

    store.insert_metric(person, metric_type, float(value), unit)

    # Get recent trend for context
    recent = store.get_recent_metrics(person, metric_type, days=30)
    trend_msg = ""
    if len(recent) > 1:
        values = [r["value"] for r in recent]
        avg = sum(values) / len(values)
        diff = float(value) - avg
        direction = "↑" if diff > 0 else "↓" if diff < 0 else "→"
        trend_msg = f"\n30天均值: {avg:.1f}{unit}, 当前 {direction} {abs(diff):.1f}"

    response = f"已记录 {person} 的{metric_type}: {value}{unit}{trend_msg}"
    return _write_result(workspace, task_id, response)


def _handle_note(store, workspace: Path, task_id: str,
                 intent: dict, person: str) -> str:
    """Record a health note (symptom, medication, etc.)."""
    category = intent.get("category", "general")
    note_content = intent.get("content", "")

    store.insert_note(person, category, note_content)
    response = f"已记录 {person} 的健康笔记 ({category}): {note_content}"
    return _write_result(workspace, task_id, response)


def _handle_query(store, workspace: Path, task_id: str,
                  content: str, person: str) -> str:
    """Answer a health data query using local LLM."""
    # Gather recent data
    metrics_30d = {}
    for metric_type in ["weight", "sleep_hours", "steps", "heart_rate",
                        "blood_pressure_sys", "blood_pressure_dia", "blood_sugar"]:
        data = store.get_recent_metrics(person, metric_type, days=30)
        if data:
            metrics_30d[metric_type] = data

    notes_30d = store.get_recent_notes(person, days=30)
    reports = store.get_recent_reports(person, limit=3)

    # Format data context
    data_context = f"## {person} 的健康数据 (近30天)\n\n"
    for mtype, records in metrics_30d.items():
        values = [f"{r['value']}{r.get('unit','')} ({r['date'][:10]})" for r in records[:10]]
        data_context += f"### {mtype}\n" + ", ".join(values) + "\n\n"

    if notes_30d:
        data_context += "### 健康笔记\n"
        for note in notes_30d[:10]:
            data_context += f"- [{note['category']}] {note['date'][:10]}: {note['content']}\n"

    if reports:
        data_context += "\n### 体检报告摘要\n"
        for r in reports:
            data_context += f"- {r['report_date']}: {r.get('summary', '(未解析)')}\n"

    if not metrics_30d and not notes_30d and not reports:
        data_context += "暂无健康数据记录。\n"

    prompt = f"""你是一个家庭健康助手。基于以下健康数据回答用户的问题。

{data_context}

用户问题: {content}

规则:
- 用中文回答，简洁具体
- 如果数据不足，说明需要更多记录
- 给出实用的建议，但声明你不是医生，重要问题请咨询专业医生
- 如果有异常趋势，明确指出"""

    response = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, timeout=60)
    return _write_result(workspace, task_id, response)


def _handle_report_request(store, workspace: Path, task_id: str,
                           content: str, person: str) -> str:
    """Generate a health summary report."""
    from report import generate_weekly_report
    report = generate_weekly_report(store, person)
    return _write_result(workspace, task_id, report)


def _write_result(workspace: Path, task_id: str, response: str) -> str:
    """Write result files for the task worker to pick up."""
    (workspace / "output.md").write_text(response, encoding="utf-8")
    result = {
        "status": "done",
        "summary": response[:500],
        "agent": "health",
    }
    (workspace / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return response
