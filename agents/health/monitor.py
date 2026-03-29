"""Health anomaly detection — daily checks and alerts."""
import logging
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("health_monitor")

# Anomaly thresholds
RULES = {
    "weight": {
        "daily_change_kg": 1.5,      # > 1.5kg in 24h
        "weekly_change_kg": 3.0,     # > 3kg in 7 days
    },
    "sleep_hours": {
        "min_hours": 4.5,            # < 4.5h is concerning
        "max_hours": 12.0,           # > 12h is concerning
        "week_avg_min": 6.0,         # weekly avg < 6h
    },
    "heart_rate": {
        "resting_max_bpm": 100,      # tachycardia threshold
        "resting_min_bpm": 40,       # bradycardia threshold
    },
    "blood_pressure_sys": {
        "high": 140,                 # stage 1 hypertension
        "low": 90,
    },
    "blood_sugar": {
        "fasting_high": 7.0,         # mmol/L
        "fasting_low": 3.9,
    },
    "blood_oxygen": {
        "low": 95,                   # SpO2 < 95% is concerning
        "critical": 90,              # SpO2 < 90% needs attention
    },
    "hrv": {
        "low_ms": 20,               # very low HRV = stress/fatigue
    },
    "body_fat": {
        "male_high": 25,            # % — adjust per user
        "male_low": 5,
    },
}


def check_person(store, person_id: str) -> list[dict]:
    """Run all anomaly checks for one person. Returns list of alerts."""
    alerts = []

    # Weight check
    alerts.extend(_check_weight(store, person_id))

    # Sleep check
    alerts.extend(_check_sleep(store, person_id))

    # Heart rate check
    alerts.extend(_check_heart_rate(store, person_id))

    # Blood pressure check
    alerts.extend(_check_blood_pressure(store, person_id))

    # Blood sugar check
    alerts.extend(_check_blood_sugar(store, person_id))

    # Blood oxygen (Apple Watch / Oura)
    alerts.extend(_check_blood_oxygen(store, person_id))

    # HRV (Oura / Apple Watch)
    alerts.extend(_check_hrv(store, person_id))

    # Recent symptoms check
    alerts.extend(_check_symptoms(store, person_id))

    return alerts


def check_all_users(store, user_ids: list[str]) -> dict[str, list[dict]]:
    """Run checks for all users. Returns {person_id: [alerts]}."""
    results = {}
    for uid in user_ids:
        alerts = check_person(store, uid)
        if alerts:
            results[uid] = alerts
            log.info("Alerts for %s: %d", uid, len(alerts))
        else:
            log.info("All clear for %s", uid)
    return results


def format_alerts(person_id: str, alerts: list[dict]) -> str:
    """Format alerts into a readable message."""
    if not alerts:
        return f"{person_id}: 一切正常"

    severity_emoji = {"warning": "⚠️", "critical": "🚨", "info": "ℹ️"}
    lines = [f"## {person_id} 健康提醒\n"]
    for alert in sorted(alerts, key=lambda a: a.get("severity", "info") == "critical", reverse=True):
        emoji = severity_emoji.get(alert.get("severity", "info"), "ℹ️")
        lines.append(f"{emoji} **{alert['title']}**: {alert['message']}")
    return "\n".join(lines)


# ---- Individual checks ----

def _check_weight(store, person_id: str) -> list[dict]:
    alerts = []
    data = store.get_recent_metrics(person_id, "weight", days=7)
    if len(data) < 2:
        return alerts

    latest = data[0]["value"]
    prev = data[1]["value"]
    daily_change = abs(latest - prev)

    if daily_change > RULES["weight"]["daily_change_kg"]:
        direction = "增加" if latest > prev else "减少"
        alerts.append({
            "title": "体重突变",
            "message": f"体重{direction} {daily_change:.1f}kg ({prev:.1f} → {latest:.1f}kg)",
            "severity": "warning",
            "metric": "weight",
        })

    # Weekly change
    if len(data) >= 3:
        oldest = data[-1]["value"]
        weekly_change = abs(latest - oldest)
        if weekly_change > RULES["weight"]["weekly_change_kg"]:
            direction = "增加" if latest > oldest else "减少"
            alerts.append({
                "title": "体重周变化大",
                "message": f"本周体重{direction} {weekly_change:.1f}kg",
                "severity": "warning",
                "metric": "weight",
            })
    return alerts


def _check_sleep(store, person_id: str) -> list[dict]:
    alerts = []
    data = store.get_recent_metrics(person_id, "sleep_hours", days=7)
    if not data:
        return alerts

    latest = data[0]["value"]
    rules = RULES["sleep_hours"]

    if latest < rules["min_hours"]:
        alerts.append({
            "title": "睡眠严重不足",
            "message": f"昨晚只睡了 {latest:.1f} 小时",
            "severity": "critical" if latest < 4 else "warning",
            "metric": "sleep_hours",
        })
    elif latest > rules["max_hours"]:
        alerts.append({
            "title": "睡眠时间过长",
            "message": f"昨晚睡了 {latest:.1f} 小时，可能需要关注",
            "severity": "info",
            "metric": "sleep_hours",
        })

    # Weekly average
    if len(data) >= 3:
        avg = sum(d["value"] for d in data) / len(data)
        if avg < rules["week_avg_min"]:
            alerts.append({
                "title": "本周睡眠不足",
                "message": f"本周平均睡眠 {avg:.1f} 小时，低于 {rules['week_avg_min']} 小时",
                "severity": "warning",
                "metric": "sleep_hours",
            })
    return alerts


def _check_heart_rate(store, person_id: str) -> list[dict]:
    alerts = []
    latest = store.get_latest_metric(person_id, "heart_rate")
    if not latest:
        return alerts

    hr = latest["value"]
    rules = RULES["heart_rate"]
    if hr > rules["resting_max_bpm"]:
        alerts.append({
            "title": "静息心率偏高",
            "message": f"静息心率 {hr:.0f} bpm (正常 < {rules['resting_max_bpm']})",
            "severity": "warning",
            "metric": "heart_rate",
        })
    elif hr < rules["resting_min_bpm"]:
        alerts.append({
            "title": "静息心率偏低",
            "message": f"静息心率 {hr:.0f} bpm (正常 > {rules['resting_min_bpm']})",
            "severity": "warning",
            "metric": "heart_rate",
        })
    return alerts


def _check_blood_pressure(store, person_id: str) -> list[dict]:
    alerts = []
    latest = store.get_latest_metric(person_id, "blood_pressure_sys")
    if not latest:
        return alerts

    sys_bp = latest["value"]
    rules = RULES["blood_pressure_sys"]
    if sys_bp >= rules["high"]:
        alerts.append({
            "title": "血压偏高",
            "message": f"收缩压 {sys_bp:.0f} mmHg (正常 < {rules['high']})",
            "severity": "critical" if sys_bp >= 160 else "warning",
            "metric": "blood_pressure",
        })
    elif sys_bp < rules["low"]:
        alerts.append({
            "title": "血压偏低",
            "message": f"收缩压 {sys_bp:.0f} mmHg (正常 > {rules['low']})",
            "severity": "warning",
            "metric": "blood_pressure",
        })
    return alerts


def _check_blood_sugar(store, person_id: str) -> list[dict]:
    alerts = []
    latest = store.get_latest_metric(person_id, "blood_sugar")
    if not latest:
        return alerts

    bs = latest["value"]
    rules = RULES["blood_sugar"]
    if bs >= rules["fasting_high"]:
        alerts.append({
            "title": "血糖偏高",
            "message": f"血糖 {bs:.1f} mmol/L (空腹正常 < {rules['fasting_high']})",
            "severity": "critical" if bs >= 11.1 else "warning",
            "metric": "blood_sugar",
        })
    elif bs < rules["fasting_low"]:
        alerts.append({
            "title": "血糖偏低",
            "message": f"血糖 {bs:.1f} mmol/L (正常 > {rules['fasting_low']})",
            "severity": "critical",
            "metric": "blood_sugar",
        })
    return alerts


def _check_blood_oxygen(store, person_id: str) -> list[dict]:
    alerts = []
    latest = store.get_latest_metric(person_id, "blood_oxygen")
    if not latest:
        return alerts
    spo2 = latest["value"]
    if spo2 < RULES["blood_oxygen"]["critical"]:
        alerts.append({
            "title": "血氧严重偏低",
            "message": f"血氧 {spo2:.0f}% (正常 > 95%)",
            "severity": "critical",
            "metric": "blood_oxygen",
        })
    elif spo2 < RULES["blood_oxygen"]["low"]:
        alerts.append({
            "title": "血氧偏低",
            "message": f"血氧 {spo2:.0f}% (正常 > 95%)",
            "severity": "warning",
            "metric": "blood_oxygen",
        })
    return alerts


def _check_hrv(store, person_id: str) -> list[dict]:
    alerts = []
    data = store.get_recent_metrics(person_id, "hrv", days=7)
    if not data:
        return alerts
    latest = data[0]["value"]
    if latest < RULES["hrv"]["low_ms"]:
        alerts.append({
            "title": "HRV 偏低",
            "message": f"心率变异性 {latest:.0f}ms，可能压力/疲劳较大",
            "severity": "warning",
            "metric": "hrv",
        })
    # Trend: HRV dropping significantly
    if len(data) >= 5:
        avg = sum(d["value"] for d in data) / len(data)
        if latest < avg * 0.7:
            alerts.append({
                "title": "HRV 明显下降",
                "message": f"当前 {latest:.0f}ms vs 7天均值 {avg:.0f}ms (下降 {((avg-latest)/avg*100):.0f}%)",
                "severity": "warning",
                "metric": "hrv",
            })
    return alerts


def _check_symptoms(store, person_id: str) -> list[dict]:
    """Check if there are recent symptoms that deserve attention."""
    alerts = []
    notes = store.get_recent_notes(person_id, days=3)
    symptom_notes = [n for n in notes if n.get("category") == "symptom"]

    if len(symptom_notes) >= 3:
        alerts.append({
            "title": "近期多次报告症状",
            "message": f"过去3天报告了 {len(symptom_notes)} 次症状，建议关注",
            "severity": "info",
            "metric": "symptoms",
        })
    return alerts
