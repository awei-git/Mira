"""Health report generator — weekly/monthly summaries."""

import logging
from datetime import date, timedelta

log = logging.getLogger("health_report")


def generate_weekly_report(store, person_id: str) -> str:
    """Generate a weekly health summary for one person."""
    today = date.today()
    week_ago = today - timedelta(days=7)

    sections = []
    sections.append(f"# 健康周报 | {week_ago} ~ {today}\n## {person_id}\n")

    # Weight
    weight_data = store.get_recent_metrics(person_id, "weight", days=7)
    if weight_data:
        values = [d["value"] for d in weight_data]
        avg = sum(values) / len(values)
        latest = values[0]
        prev_week = store.get_metric_stats(person_id, "weight", days=14)
        trend = ""
        if prev_week and prev_week["avg"]:
            diff = avg - float(prev_week["avg"])
            trend = f" (vs 上两周均值 {diff:+.1f})"
        sections.append(f"### 体重\n- 本周均值: {avg:.1f}kg{trend}\n- 最新: {latest:.1f}kg\n- 记录 {len(values)} 次\n")

    # Sleep
    sleep_data = store.get_recent_metrics(person_id, "sleep_hours", days=7)
    if sleep_data:
        values = [d["value"] for d in sleep_data]
        avg = sum(values) / len(values)
        minimum = min(values)
        sections.append(f"### 睡眠\n- 本周均值: {avg:.1f}h\n- 最低: {minimum:.1f}h\n")
        if avg < 7:
            sections.append("- ⚠️ 睡眠不足7小时，注意休息\n")

    # Steps
    steps_data = store.get_recent_metrics(person_id, "steps", days=7)
    if steps_data:
        values = [d["value"] for d in steps_data]
        avg = sum(values) / len(values)
        sections.append(f"### 步数\n- 本周日均: {avg:,.0f} 步\n")

    # Heart rate
    hr_data = store.get_recent_metrics(person_id, "heart_rate", days=7)
    if hr_data:
        values = [d["value"] for d in hr_data]
        avg = sum(values) / len(values)
        sections.append(f"### 静息心率\n- 本周均值: {avg:.0f} bpm\n")

    # Body fat (Renpho)
    bf_data = store.get_recent_metrics(person_id, "body_fat", days=7)
    if bf_data:
        values = [d["value"] for d in bf_data]
        avg = sum(values) / len(values)
        sections.append(f"### 体脂率\n- 本周均值: {avg:.1f}%\n")

    # HRV (Oura)
    hrv_data = store.get_recent_metrics(person_id, "hrv", days=7)
    if hrv_data:
        values = [d["value"] for d in hrv_data]
        avg = sum(values) / len(values)
        minimum = min(values)
        sections.append(f"### HRV (心率变异性)\n- 本周均值: {avg:.0f}ms\n- 最低: {minimum:.0f}ms\n")
        if avg < 30:
            sections.append("- ⚠️ HRV 偏低，注意压力管理和休息\n")

    # Blood oxygen
    spo2_data = store.get_recent_metrics(person_id, "blood_oxygen", days=7)
    if spo2_data:
        values = [d["value"] for d in spo2_data]
        avg = sum(values) / len(values)
        sections.append(f"### 血氧\n- 本周均值: {avg:.1f}%\n")

    # Exercise
    exercise_data = store.get_recent_metrics(person_id, "exercise_minutes", days=7)
    if exercise_data:
        total = sum(d["value"] for d in exercise_data)
        sections.append(f"### 运动\n- 本周总运动: {total:.0f} 分钟\n")
        if total < 150:
            sections.append("- ℹ️ WHO 建议每周 150 分钟中等强度运动\n")

    # Workouts
    workout_data = store.get_recent_metrics(person_id, "workout", days=7)
    if workout_data:
        sections.append("### 运动记录\n")
        for w in workout_data[:7]:
            date_str = str(w.get("date", ""))[:10]
            sections.append(f"- {date_str}: {w['value']:.0f}分钟\n")

    # Notes
    notes = store.get_recent_notes(person_id, days=7)
    if notes:
        sections.append("### 健康记录\n")
        for n in notes:
            sections.append(f"- [{n['category']}] {n['date']}: {n['content']}\n")

    # Checkup tracking
    reports = store.get_recent_reports(person_id, limit=1)
    if reports:
        r = reports[0]
        sections.append(f"\n### 最近体检\n- {r['report_date']} ({r['report_type']})\n")
        if r.get("summary"):
            sections.append(f"- 摘要: {r['summary'][:200]}\n")

    if len(sections) == 1:
        sections.append('暂无健康数据。开始记录: 发送 "记录体重 72" 或 "今天睡了6小时"。\n')

    sections.append("\n---\n*此报告由 Mira Health Agent 生成，仅供参考。重要健康问题请咨询专业医生。*")
    return "\n".join(sections)


def generate_daily_insight(store, person_id: str, model: str = "gpt5") -> str | None:
    """Generate daily health insight with GPT — comprehensive analysis and advice.

    Collects today's data + 7-day trends, sends everything to LLM,
    returns personalized, actionable advice.
    """
    today = date.today()

    # Collect all available data
    data_parts = []

    # Today's metrics — wearables + body composition
    for metric_name, label in [
        ("weight", "体重(kg)"),
        ("body_fat", "体脂(%)"),
        ("sleep_hours", "睡眠(h)"),
        ("sleep_score", "睡眠分数"),
        ("steps", "步数"),
        ("heart_rate", "静息心率(bpm)"),
        ("resting_hr_lowest", "最低心率(bpm)"),
        ("hrv", "HRV(ms)"),
        ("blood_oxygen", "血氧(%)"),
        ("respiratory_rate", "呼吸频率(brpm)"),
        ("readiness_score", "准备度分数"),
        ("activity_score", "活动分数"),
        ("active_calories", "活动消耗(kcal)"),
        ("total_calories", "总消耗(kcal)"),
        ("active_minutes", "活动时间(min)"),
        ("sedentary_hours", "久坐时间(h)"),
        ("inactivity_alerts", "久坐提醒次数"),
        ("stress_high", "高压力时间(min)"),
        ("recovery_high", "恢复时间(min)"),
        ("stress_level", "压力等级(1恢复/2正常/3高压)"),
        ("resilience_level", "韧性等级(1-5)"),
        ("sleep_recovery", "睡眠恢复度"),
        ("daytime_recovery", "日间恢复度"),
        ("temperature_deviation", "体温偏差"),
        ("workout", "锻炼时长(min)"),
        ("workout_calories", "锻炼消耗(kcal)"),
    ]:
        latest = store.get_latest_metric(person_id, metric_name)
        if latest:
            data_parts.append(f"- {label}: {latest['value']:.1f} (日期: {latest.get('date', today)})")

    if not data_parts:
        return None

    # 7-day trends
    trend_parts = []
    for metric_name, label in [
        ("weight", "体重"),
        ("sleep_hours", "睡眠"),
        ("sleep_score", "睡眠分数"),
        ("hrv", "HRV"),
        ("heart_rate", "心率"),
        ("blood_oxygen", "血氧"),
        ("steps", "步数"),
        ("active_minutes", "活动时间"),
        ("stress_high", "高压力"),
        ("recovery_high", "恢复"),
        ("readiness_score", "准备度"),
    ]:
        week_data = store.get_recent_metrics(person_id, metric_name, days=7)
        if len(week_data) >= 3:
            values = [d["value"] for d in week_data]
            avg = sum(values) / len(values)
            trend = "↑" if values[0] > avg else "↓" if values[0] < avg else "→"
            dates_vals = [f"{str(d.get('date',''))[:10]}:{d['value']:.1f}" for d in week_data[:7]]
            trend_parts.append(f"- {label} 7天: {trend} 均值{avg:.1f} | {', '.join(dates_vals)}")

    # Recent symptoms/notes
    notes = store.get_recent_notes(person_id, days=3)
    note_parts = []
    for n in notes[:5]:
        note_parts.append(f"- [{n.get('category', '')}] {n.get('date', '')}: {n.get('content', '')}")

    # Most recent checkup report
    checkup_text = "无"
    reports = store.get_recent_reports(person_id, limit=1)
    if reports:
        r = reports[0]
        checkup_text = f"日期: {r['report_date']} ({r['report_type']})\n{r.get('summary', '')}"
        # Include flagged items from parsed JSON
        parsed = r.get("parsed_json")
        if isinstance(parsed, str):
            import json

            try:
                parsed = json.loads(parsed)
            except Exception:
                parsed = None
        if parsed and isinstance(parsed, dict):
            flagged = parsed.get("flagged_high", [])
            if flagged:
                checkup_text += f"\n异常项: {', '.join(flagged)}"
            # Include key panels
            panels = parsed.get("panels", {})
            for panel_name in ["Lipid", "HBV", "A1c", "Hepatic"]:
                panel = panels.get(panel_name, {})
                for test, info in panel.items():
                    if isinstance(info, dict) and info.get("flag"):
                        checkup_text += (
                            f"\n  {test}: {info['value']} {info.get('unit','')} (参考: {info.get('ref','')}) ⚠️"
                        )
                    elif isinstance(info, dict) and "prev" in info:
                        checkup_text += f"\n  {test}: {info['value']} (上次: {info['prev']})"

    # Build prompt
    data_text = "\n".join(data_parts)
    trend_text = "\n".join(trend_parts) if trend_parts else "暂无足够趋势数据"
    notes_text = "\n".join(note_parts) if note_parts else "无"

    prompt = f"""你是一个专业的私人健康顾问。以下是用户 {person_id} 的健康数据，请给出今日健康洞察。

## 最新穿戴设备数据
{data_text}

## 7天趋势
{trend_text}

## 最近体检报告
{checkup_text}

## 近期症状/备注
{notes_text}

## 要求
1. 先给一句整体评价（好/一般/需注意）
2. 结合穿戴设备数据和体检报告，指出最值得关注的 1-2 个点
3. 给出 2-3 条具体可执行的建议（今天可以做的事，不要空话）
4. 如果体检有异常项（如高LDL、高ApoB），结合日常数据给出针对性建议（如饮食、运动）
5. 如果某项指标连续恶化，直接警告
6. 语气像一个关心你的朋友，不要像医生写报告
7. 用中文，简洁，总共不超过 300 字
"""

    try:
        from llm import model_think

        result = model_think(prompt, model_name=model, timeout=60)
        if result and len(result.strip()) > 30:
            return result.strip()
    except Exception as e:
        log.warning("Daily health insight failed: %s", e)
    return None
