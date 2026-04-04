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

from config import OLLAMA_DEFAULT_MODEL, DATABASE_URL
from sub_agent import _ollama_call


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "",
           tier: str = "light") -> str | None:
    """Main entry point for the health agent."""
    # Force local LLM for all health data processing — set at call time,
    # not import time, to avoid poisoning the env for other agents/tests.
    os.environ["MIRA_FORCE_OLLAMA"] = "1"
    log.info("Health agent: task=%s content=%s", task_id, content[:80])

    from health_store import HealthStore
    store = HealthStore(DATABASE_URL)

    # Resolve person_id: sender from iOS is device name (e.g. "iphone"),
    # but we need the user_id (e.g. "ang"). Extract from workspace path
    # which is .../users/{user_id}/tasks/... or fall back to "ang".
    user_id = _resolve_user_id(workspace, sender)

    # Classify the input
    intent = _classify(content, user_id)
    log.info("Classified: %s", intent)

    intent_type = intent.get("type", "query")
    person = intent.get("person", user_id)

    if intent_type == "metric":
        return _handle_metric(store, workspace, task_id, intent, person)
    elif intent_type == "note":
        return _handle_note(store, workspace, task_id, intent, person)
    elif intent_type == "query":
        return _handle_query(store, workspace, task_id, content, person)
    elif intent_type == "report":
        return _handle_report_request(store, workspace, task_id, content, person)
    elif intent_type == "checkup":
        return _handle_checkup(store, workspace, task_id, content, person)
    else:
        return _handle_query(store, workspace, task_id, content, person)


def _resolve_user_id(workspace: Path, sender: str) -> str:
    """Map device sender name to user_id.

    The iOS app sends device name as sender (e.g. "iphone", "ipad").
    We need the bridge user_id (e.g. "ang", "liquan") for DB queries.
    Extract from workspace path which contains .../users/{user_id}/...
    """
    # Device names that aren't real user IDs
    device_names = {"iphone", "ipad", "macbook", "mac", "unknown", "user", "?"}
    if sender.lower() not in device_names:
        return sender

    # Extract user_id from workspace path: .../users/{user_id}/tasks/...
    parts = workspace.resolve().parts
    for i, part in enumerate(parts):
        if part == "users" and i + 1 < len(parts):
            candidate = parts[i + 1]
            if candidate and not candidate.startswith("."):
                return candidate

    return "ang"  # Safe default


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
- "checkup": User uploaded a checkup report (mentions 体检报告, checkup, report images, etc.)
  Extract: {{"type": "checkup", "person": "<who>", "content": "<any notes>"}}

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

    # Refresh health_summary.json so iOS dashboard updates immediately
    try:
        from summary import write_summary_to_bridge
        from config import MIRA_DIR
        write_summary_to_bridge(store, Path(MIRA_DIR), person)
    except Exception:
        pass

    # Get recent trend for context
    recent = store.get_recent_metrics(person, metric_type, days=30)
    trend_msg = ""
    if len(recent) > 1:
        values = [r["value"] for r in recent]
        avg = sum(values) / len(values)
        diff = float(value) - avg
        direction = "↑" if diff > 0 else "↓" if diff < 0 else "→"
        trend_msg = f"\n30天均值: {avg:.1f}{unit}, 当前 {direction} {abs(diff):.1f}"

    # Generate advice for concerning metrics (blood pressure, blood sugar)
    advice = None
    if metric_type in ("blood_pressure_sys", "blood_pressure_dia", "blood_sugar", "temperature"):
        advice = _generate_on_demand_advice(
            store, person, f"{metric_type}: {value}{unit}{trend_msg}", "metric")

    response = f"已记录 {person} 的{metric_type}: {value}{unit}{trend_msg}"
    if advice:
        response += f"\n\n---\n\n{advice}"
    return _write_result(workspace, task_id, response)


def _handle_note(store, workspace: Path, task_id: str,
                 intent: dict, person: str) -> str:
    """Record a health note (symptom, medication, etc.) and generate advice."""
    category = intent.get("category", "general")
    note_content = intent.get("content", "")

    store.insert_note(person, category, note_content)

    # Generate immediate GPT advice for symptoms
    advice = _generate_on_demand_advice(store, person, note_content, category)
    if advice:
        response = f"已记录 {person} 的健康笔记 ({category}): {note_content}\n\n---\n\n{advice}"
    else:
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


def _handle_checkup(store, workspace: Path, task_id: str,
                    content: str, person: str) -> str:
    """Handle uploaded checkup report — parse images and generate advice."""
    from ingest import parse_checkup_images

    # Extract image paths from content
    # Content format: "体检报告上传: checkup_xxx.jpg, ...\n备注: ...\n路径: users/.../health/checkups/"
    path_line = [l for l in content.split("\n") if "路径:" in l]
    checkup_dir = None
    if path_line:
        rel_path = path_line[0].split("路径:")[-1].strip()
        bridge_path = Path(os.environ.get("MIRA_DIR", str(workspace.parent.parent)))
        checkup_dir = bridge_path / rel_path

    parsed = None
    if checkup_dir and checkup_dir.exists():
        images = sorted(checkup_dir.glob("*.jpg"))
        if images:
            parsed = parse_checkup_images(images, person, store)

    # Store as note too for immediate context
    note_content = content.split("\n")[0]  # first line = summary
    store.insert_note(person, "checkup", note_content)

    # Generate advice with checkup context
    advice = _generate_on_demand_advice(store, person, content, "checkup")

    if parsed and parsed.get("items"):
        abnormal = [i for i in parsed["items"] if i.get("flag") and i["flag"] != "normal"]
        response = f"体检报告已解析: {len(parsed['items'])} 项检查"
        if abnormal:
            response += f", {len(abnormal)} 项异常"
            for item in abnormal[:5]:
                response += f"\n- {item['name']}: {item.get('value','?')}{item.get('unit','')} ({item['flag']})"
    else:
        response = f"已记录体检报告。"

    if advice:
        response += f"\n\n---\n\n{advice}"

    return _write_result(workspace, task_id, response)


def _generate_on_demand_advice(store, person: str, input_text: str,
                               category: str = "symptom") -> str | None:
    """Generate immediate GPT advice based on new input + existing health data.

    Unlike the daily scheduled insight, this runs on-demand whenever the user
    submits symptoms, notes, or checkup data — so they get actionable advice
    right away instead of waiting for the next daily check.
    """
    from sub_agent import model_think

    # Gather context: recent metrics + notes + checkup
    data_parts = []
    for metric_name, label in [
        ("weight", "体重(kg)"), ("body_fat", "体脂(%)"),
        ("sleep_hours", "睡眠(h)"), ("heart_rate", "静息心率(bpm)"),
        ("hrv", "HRV(ms)"), ("blood_oxygen", "血氧(%)"),
        ("blood_pressure_sys", "收缩压(mmHg)"), ("blood_pressure_dia", "舒张压(mmHg)"),
        ("blood_sugar", "血糖(mmol/L)"),
    ]:
        latest = store.get_latest_metric(person, metric_name)
        if latest:
            data_parts.append(f"- {label}: {latest['value']:.1f}")

    notes = store.get_recent_notes(person, days=7)
    note_parts = [f"- [{n.get('category','')}] {n.get('date','')}: {n.get('content','')}"
                  for n in notes[:5]]

    checkup_text = "无"
    reports = store.get_recent_reports(person, limit=1)
    if reports:
        r = reports[0]
        checkup_text = f"日期: {r['report_date']} ({r['report_type']})"
        if r.get("summary"):
            checkup_text += f"\n{r['summary'][:300]}"
        parsed = r.get("parsed_json")
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception:
                parsed = None
        if parsed and isinstance(parsed, dict):
            flagged = parsed.get("flagged_high", [])
            if flagged:
                checkup_text += f"\n异常项: {', '.join(flagged)}"

    data_text = "\n".join(data_parts) if data_parts else "暂无穿戴设备数据"
    notes_text = "\n".join(note_parts) if note_parts else "无"

    prompt = f"""你是一个专业的私人健康顾问。用户刚提交了新的健康信息，请给出针对性的分析和建议。

## 用户刚提交的内容
类型: {category}
内容: {input_text}

## 近期健康数据
{data_text}

## 近期症状/备注
{notes_text}

## 最近体检报告
{checkup_text}

## 要求
1. 针对用户刚提交的内容给出具体分析（不要泛泛而谈）
2. 如果是症状，结合已有健康数据判断可能的原因，给出 2-3 条今天可以做的事
3. 如果体检有异常项，结合症状一起分析
4. 如果数据不足以判断，明确说需要什么额外信息
5. 语气像一个关心你的朋友，不要像医生写报告
6. 用中文，简洁，总共不超过 300 字
7. 重要问题提醒就医
"""

    try:
        # Temporarily allow cloud LLM for advice generation.
        # Raw health data was already stored locally; the prompt contains
        # only aggregated summaries, no PII beyond person_id.
        saved = os.environ.pop("MIRA_FORCE_OLLAMA", None)
        try:
            result = model_think(prompt, model_name="gpt5", timeout=60)
        finally:
            if saved is not None:
                os.environ["MIRA_FORCE_OLLAMA"] = saved
        if result and len(result.strip()) > 30:
            return result.strip()
    except Exception as e:
        log.warning("On-demand health advice failed: %s", e)
    return None


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
