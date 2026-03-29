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
        sections.append("暂无健康数据。开始记录: 发送 \"记录体重 72\" 或 \"今天睡了6小时\"。\n")

    sections.append("\n---\n*此报告由 Mira Health Agent 生成，仅供参考。重要健康问题请咨询专业医生。*")
    return "\n".join(sections)
