"""Health anomaly detection — daily checks and alerts."""
import copy
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("health_monitor")

# ---- Staleness thresholds (hours) ----
STALENESS_HOURS = {
    "weight": 72,           # 3 days - people don't weigh daily
    "sleep_hours": 36,      # 1.5 days - should sync daily
    "heart_rate": 36,       # 1.5 days
    "blood_pressure": 168,  # 7 days - manual measurement
    "blood_sugar": 168,     # 7 days - manual
    "blood_oxygen": 36,     # 1.5 days
    "hrv": 36,              # 1.5 days
    "readiness_score": 36,
    "stress_high": 36,
    "temperature_deviation": 36,
    "sleep_score": 36,
}


def _is_stale(data_point: dict, metric_type: str) -> bool:
    """Check if a data point is too old to alert on."""
    max_hours = STALENESS_HOURS.get(metric_type, 48)
    recorded = data_point.get("recorded_at")
    if not recorded:
        return True
    try:
        if isinstance(recorded, str):
            ts = datetime.fromisoformat(recorded)
        else:
            ts = recorded
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        return age > timedelta(hours=max_hours)
    except (ValueError, TypeError):
        return True  # Can't parse = treat as stale


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

# ---- Per-user threshold overrides ----
# Stored in: {HEALTH_DIR}/user_thresholds.json
# Format: {"weight": {"daily_change_kg": 2.0}, "heart_rate": {"resting_max_bpm": 90}, ...}
# Any field in RULES can be overridden per user.

_HEALTH_DIR = Path(__file__).parent
_USER_THRESHOLDS_FILE = _HEALTH_DIR / "user_thresholds.json"


def _get_rules() -> dict:
    """Load RULES with per-user overrides applied."""
    rules = copy.deepcopy(RULES)
    if _USER_THRESHOLDS_FILE.exists():
        try:
            overrides = json.loads(_USER_THRESHOLDS_FILE.read_text())
            for category, fields in overrides.items():
                if category in rules and isinstance(fields, dict):
                    rules[category].update(fields)
        except (json.JSONDecodeError, Exception) as e:
            log.warning("Failed to load user thresholds: %s", e)
    return rules


# ---- Alert suppression / acknowledgment ----
_SUPPRESSED_FILE = _HEALTH_DIR / "suppressed_alerts.json"


def suppress_alert(metric_type: str, duration_days: int = 7, reason: str = ""):
    """Suppress alerts for a metric type for N days."""
    suppressed = _load_suppressed()
    suppressed[metric_type] = {
        "until": (datetime.now(timezone.utc) + timedelta(days=duration_days)).isoformat(),
        "reason": reason,
        "created": datetime.now(timezone.utc).isoformat(),
    }
    _save_suppressed(suppressed)
    log.info("Suppressed %s alerts for %d days: %s", metric_type, duration_days, reason)


def unsuppress_alert(metric_type: str):
    """Remove suppression for a metric type."""
    suppressed = _load_suppressed()
    if metric_type in suppressed:
        del suppressed[metric_type]
        _save_suppressed(suppressed)


def _is_suppressed(metric_type: str) -> bool:
    """Check if alerts for this metric are currently suppressed."""
    suppressed = _load_suppressed()
    entry = suppressed.get(metric_type)
    if not entry:
        return False
    try:
        until = datetime.fromisoformat(entry["until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > until:
            # Expired, clean up
            del suppressed[metric_type]
            _save_suppressed(suppressed)
            return False
        return True
    except (ValueError, KeyError):
        return False


def _load_suppressed() -> dict:
    if not _SUPPRESSED_FILE.exists():
        return {}
    try:
        return json.loads(_SUPPRESSED_FILE.read_text())
    except (json.JSONDecodeError, Exception):
        return {}


def _save_suppressed(data: dict):
    _SUPPRESSED_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---- Alert frequency control ----
_ALERT_HISTORY_FILE = _HEALTH_DIR / "alert_history.json"
MAX_ALERTS_PER_DAY = 10


def _record_alert(metric_type: str, title: str):
    """Record that an alert was sent."""
    history = _load_alert_history()
    today = datetime.now().strftime("%Y-%m-%d")
    today_alerts = history.get(today, [])
    today_alerts.append({"type": metric_type, "title": title, "time": datetime.now().isoformat()})
    history[today] = today_alerts
    # Keep only last 7 days
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    history = {k: v for k, v in history.items() if k >= cutoff}
    _save_alert_history(history)


def _load_alert_history() -> dict:
    if not _ALERT_HISTORY_FILE.exists():
        return {}
    try:
        return json.loads(_ALERT_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, Exception):
        return {}


def _save_alert_history(data: dict):
    _ALERT_HISTORY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def check_person(store, person_id: str, rules: dict | None = None) -> list[dict]:
    """Run all anomaly checks for one person. Returns list of alerts."""
    if rules is None:
        rules = _get_rules()

    alerts = []

    # Weight check
    alerts.extend(_check_weight(store, person_id, rules))

    # Sleep check
    alerts.extend(_check_sleep(store, person_id, rules))

    # Heart rate check
    alerts.extend(_check_heart_rate(store, person_id, rules))

    # Blood pressure check
    alerts.extend(_check_blood_pressure(store, person_id, rules))

    # Blood sugar check
    alerts.extend(_check_blood_sugar(store, person_id, rules))

    # Blood oxygen (Apple Watch / Oura)
    alerts.extend(_check_blood_oxygen(store, person_id, rules))

    # HRV (Oura / Apple Watch)
    alerts.extend(_check_hrv(store, person_id, rules))

    # Oura readiness / stress / recovery
    alerts.extend(_check_readiness(store, person_id))
    alerts.extend(_check_stress(store, person_id))
    alerts.extend(_check_temperature(store, person_id))
    alerts.extend(_check_sleep_score(store, person_id))

    # Recent symptoms check
    alerts.extend(_check_symptoms(store, person_id))

    # Apply daily alert cap — prioritize by severity
    if len(alerts) > MAX_ALERTS_PER_DAY:
        critical = [a for a in alerts if a.get("severity") == "critical"]
        warning = [a for a in alerts if a.get("severity") == "warning"]
        info = [a for a in alerts if a.get("severity") == "info"]
        alerts = (critical
                  + warning[:MAX_ALERTS_PER_DAY - len(critical)]
                  + info[:max(0, MAX_ALERTS_PER_DAY - len(critical) - len(warning))])

    # Record sent alerts
    for a in alerts:
        _record_alert(a.get("metric", "unknown"), a.get("title", ""))

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

def _check_weight(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("weight"):
        return alerts
    data = store.get_recent_metrics(person_id, "weight", days=7)
    if len(data) < 2:
        return alerts
    if _is_stale(data[0], "weight"):
        return alerts  # Data too old, skip alerting

    latest = data[0]
    latest_val = latest["value"]
    # Find previous measurement from a DIFFERENT day
    latest_date = latest["recorded_at"][:10] if isinstance(latest["recorded_at"], str) else str(latest["recorded_at"])[:10]
    prev = None
    for d in data[1:]:
        d_date = d["recorded_at"][:10] if isinstance(d["recorded_at"], str) else str(d["recorded_at"])[:10]
        if d_date != latest_date:
            prev = d
            break

    if not prev:
        return alerts  # Only same-day measurements, skip comparison

    prev_val = prev["value"]
    daily_change = abs(latest_val - prev_val)

    if daily_change > rules["weight"]["daily_change_kg"]:
        direction = "增加" if latest_val > prev_val else "减少"
        alerts.append({
            "title": "体重突变",
            "message": f"体重{direction} {daily_change:.1f}kg ({prev_val:.1f} → {latest_val:.1f}kg)",
            "severity": "warning",
            "metric": "weight",
        })

    # Weekly change
    if len(data) >= 3:
        oldest = data[-1]["value"]
        weekly_change = abs(latest_val - oldest)
        if weekly_change > rules["weight"]["weekly_change_kg"]:
            direction = "增加" if latest_val > oldest else "减少"
            alerts.append({
                "title": "体重周变化大",
                "message": f"本周体重{direction} {weekly_change:.1f}kg",
                "severity": "warning",
                "metric": "weight",
            })
    return alerts


def _check_sleep(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("sleep_hours"):
        return alerts
    data = store.get_recent_metrics(person_id, "sleep_hours", days=7)
    if not data:
        return alerts
    if _is_stale(data[0], "sleep_hours"):
        return alerts  # Data too old, skip alerting

    latest = data[0]["value"]
    sleep_rules = rules["sleep_hours"]

    if latest < sleep_rules["min_hours"]:
        alerts.append({
            "title": "睡眠严重不足",
            "message": f"昨晚只睡了 {latest:.1f} 小时",
            "severity": "critical" if latest < 4 else "warning",
            "metric": "sleep_hours",
        })
    elif latest > sleep_rules["max_hours"]:
        alerts.append({
            "title": "睡眠时间过长",
            "message": f"昨晚睡了 {latest:.1f} 小时，可能需要关注",
            "severity": "info",
            "metric": "sleep_hours",
        })

    # Weekly average
    if len(data) >= 3:
        avg = sum(d["value"] for d in data) / len(data)
        if avg < sleep_rules["week_avg_min"]:
            alerts.append({
                "title": "本周睡眠不足",
                "message": f"本周平均睡眠 {avg:.1f} 小时，低于 {sleep_rules['week_avg_min']} 小时",
                "severity": "warning",
                "metric": "sleep_hours",
            })
    return alerts


def _check_heart_rate(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("heart_rate"):
        return alerts
    latest = store.get_latest_metric(person_id, "heart_rate")
    if not latest:
        return alerts
    if _is_stale(latest, "heart_rate"):
        return alerts

    hr = latest["value"]
    hr_rules = rules["heart_rate"]
    if hr > hr_rules["resting_max_bpm"]:
        alerts.append({
            "title": "静息心率偏高",
            "message": f"静息心率 {hr:.0f} bpm (正常 < {hr_rules['resting_max_bpm']})",
            "severity": "warning",
            "metric": "heart_rate",
        })
    elif hr < hr_rules["resting_min_bpm"]:
        alerts.append({
            "title": "静息心率偏低",
            "message": f"静息心率 {hr:.0f} bpm (正常 > {hr_rules['resting_min_bpm']})",
            "severity": "warning",
            "metric": "heart_rate",
        })
    return alerts


def _check_blood_pressure(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("blood_pressure"):
        return alerts
    latest = store.get_latest_metric(person_id, "blood_pressure_sys")
    if not latest:
        return alerts
    if _is_stale(latest, "blood_pressure"):
        return alerts

    sys_bp = latest["value"]
    bp_rules = rules["blood_pressure_sys"]
    if sys_bp >= bp_rules["high"]:
        alerts.append({
            "title": "血压偏高",
            "message": f"收缩压 {sys_bp:.0f} mmHg (正常 < {bp_rules['high']})",
            "severity": "critical" if sys_bp >= 160 else "warning",
            "metric": "blood_pressure",
        })
    elif sys_bp < bp_rules["low"]:
        alerts.append({
            "title": "血压偏低",
            "message": f"收缩压 {sys_bp:.0f} mmHg (正常 > {bp_rules['low']})",
            "severity": "warning",
            "metric": "blood_pressure",
        })
    return alerts


def _check_blood_sugar(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("blood_sugar"):
        return alerts
    latest = store.get_latest_metric(person_id, "blood_sugar")
    if not latest:
        return alerts
    if _is_stale(latest, "blood_sugar"):
        return alerts

    bs = latest["value"]
    bs_rules = rules["blood_sugar"]

    # Heuristic: measurements before 10am are likely fasting
    recorded_time = latest.get("recorded_at")
    is_likely_fasting = False
    if recorded_time:
        try:
            if isinstance(recorded_time, str):
                ts = datetime.fromisoformat(recorded_time)
            else:
                ts = recorded_time
            is_likely_fasting = ts.hour < 10
        except (ValueError, TypeError):
            pass

    if bs >= bs_rules["fasting_high"]:
        if is_likely_fasting:
            # Likely fasting — alert is meaningful
            alerts.append({
                "title": "空腹血糖偏高",
                "message": f"空腹血糖 {bs:.1f} mmol/L（正常 < {bs_rules['fasting_high']} mmol/L）",
                "severity": "critical" if bs >= 11.1 else "warning",
                "metric": "blood_sugar",
            })
        else:
            # Could be postprandial — soften alert
            if bs >= 11.1:
                # Still critical even postprandial
                alerts.append({
                    "title": "血糖异常偏高",
                    "message": f"血糖 {bs:.1f} mmol/L，即使餐后也偏高（> 11.1 mmol/L）",
                    "severity": "critical",
                    "metric": "blood_sugar",
                })
            elif bs >= 10.0:
                alerts.append({
                    "title": "餐后血糖偏高",
                    "message": f"血糖 {bs:.1f} mmol/L（如为餐后2小时，正常应 < 7.8 mmol/L）",
                    "severity": "info",
                    "metric": "blood_sugar",
                })
            # 7.0-10.0 postprandial is normal, don't alert
    elif bs < bs_rules["fasting_low"]:
        alerts.append({
            "title": "血糖偏低",
            "message": f"血糖 {bs:.1f} mmol/L (正常 > {bs_rules['fasting_low']})",
            "severity": "critical",
            "metric": "blood_sugar",
        })
    return alerts


def _check_blood_oxygen(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("blood_oxygen"):
        return alerts
    latest = store.get_latest_metric(person_id, "blood_oxygen")
    if not latest:
        return alerts
    if _is_stale(latest, "blood_oxygen"):
        return alerts
    spo2 = latest["value"]
    o2_rules = rules["blood_oxygen"]
    if spo2 < o2_rules["critical"]:
        alerts.append({
            "title": "血氧严重偏低",
            "message": f"血氧 {spo2:.0f}% (正常 > 95%)",
            "severity": "critical",
            "metric": "blood_oxygen",
        })
    elif spo2 < o2_rules["low"]:
        alerts.append({
            "title": "血氧偏低",
            "message": f"血氧 {spo2:.0f}% (正常 > 95%)",
            "severity": "warning",
            "metric": "blood_oxygen",
        })
    return alerts


def _check_hrv(store, person_id: str, rules: dict) -> list[dict]:
    alerts = []
    if _is_suppressed("hrv"):
        return alerts
    data = store.get_recent_metrics(person_id, "hrv", days=7)
    if not data:
        return alerts
    if _is_stale(data[0], "hrv"):
        return alerts
    latest = data[0]["value"]
    if latest < rules["hrv"]["low_ms"]:
        alerts.append({
            "title": "HRV 偏低",
            "message": f"心率变异性 {latest:.0f}ms，可能压力/疲劳较大",
            "severity": "warning",
            "metric": "hrv",
        })
    elif len(data) >= 5:
        # Trend: HRV dropping significantly (only if absolute threshold didn't fire)
        avg = sum(d["value"] for d in data) / len(data)
        if latest < avg * 0.7:
            alerts.append({
                "title": "HRV 明显下降",
                "message": f"当前 {latest:.0f}ms vs 7天均值 {avg:.0f}ms (下降 {((avg-latest)/avg*100):.0f}%)",
                "severity": "warning",
                "metric": "hrv",
            })
    return alerts


def _check_readiness(store, person_id: str) -> list[dict]:
    """Oura readiness score — low means body needs recovery."""
    alerts = []
    if _is_suppressed("readiness_score"):
        return alerts
    latest = store.get_latest_metric(person_id, "readiness_score")
    if not latest:
        return alerts
    if _is_stale(latest, "readiness_score"):
        return alerts
    score = latest["value"]

    if score < 50:
        alerts.append({
            "title": "身体状态差",
            "message": f"准备度分数 {score:.0f}/100，身体需要休息，建议减少运动强度",
            "severity": "warning",
            "metric": "readiness_score",
        })
    elif score < 65:
        alerts.append({
            "title": "身体状态一般",
            "message": f"准备度分数 {score:.0f}/100，注意适度休息",
            "severity": "info",
            "metric": "readiness_score",
        })
    else:
        # Only check for sudden drop if absolute threshold didn't fire
        data = store.get_recent_metrics(person_id, "readiness_score", days=7)
        if len(data) >= 3:
            avg = sum(d["value"] for d in data) / len(data)
            if score < avg * 0.7:
                alerts.append({
                    "title": "准备度骤降",
                    "message": f"今日 {score:.0f} vs 近7天均值 {avg:.0f}，身体可能正在对抗什么",
                    "severity": "warning",
                    "metric": "readiness_score",
                })
    return alerts


def _check_stress(store, person_id: str) -> list[dict]:
    """Oura stress — high stress time (in seconds)."""
    alerts = []
    if _is_suppressed("stress"):
        return alerts
    latest = store.get_latest_metric(person_id, "stress_high")
    if not latest:
        return alerts
    if _is_stale(latest, "stress_high"):
        return alerts
    stress_min = latest["value"] / 60  # convert seconds to minutes

    recovery = store.get_latest_metric(person_id, "recovery_high")
    recovery_min = (recovery["value"] / 60) if recovery else 0

    if stress_min > 60:
        msg = f"今日高压力 {stress_min:.0f} 分钟"
        if recovery_min > 0:
            ratio = stress_min / max(recovery_min, 1)
            msg += f"，恢复仅 {recovery_min:.0f} 分钟（压力/恢复比 {ratio:.1f}:1）"
            if ratio > 3:
                msg += "，严重失衡"
        alerts.append({
            "title": "压力过高",
            "message": msg,
            "severity": "warning" if stress_min > 90 else "info",
            "metric": "stress",
        })
    return alerts


def _check_temperature(store, person_id: str) -> list[dict]:
    """Oura temperature deviation — significant changes may indicate illness."""
    alerts = []
    if _is_suppressed("temperature_deviation"):
        return alerts
    data = store.get_recent_metrics(person_id, "temperature_deviation", days=7)
    if len(data) < 2:
        return alerts
    if _is_stale(data[0], "temperature_deviation"):
        return alerts

    latest = data[0]["value"]
    prev_values = [d["value"] for d in data[1:]]
    avg = sum(prev_values) / len(prev_values)

    # Big drop in temperature score = body temperature deviation (Oura reports as contributor score 0-100)
    if latest < avg * 0.5 and avg > 30:
        alerts.append({
            "title": "体温偏差异常",
            "message": f"体温偏差指标 {latest:.0f}（近期均值 {avg:.0f}），身体可能有异常，建议测量体温确认",
            "severity": "warning",
            "metric": "temperature_deviation",
        })
    elif latest < avg * 0.7 and avg > 30:
        alerts.append({
            "title": "体温偏差偏大",
            "message": f"体温偏差指标 {latest:.0f}（近期均值 {avg:.0f}），注意观察",
            "severity": "info",
            "metric": "temperature_deviation",
        })
    return alerts


def _check_sleep_score(store, person_id: str) -> list[dict]:
    """Oura sleep score — poor sleep quality."""
    alerts = []
    if _is_suppressed("sleep_score"):
        return alerts
    latest = store.get_latest_metric(person_id, "sleep_score")
    if not latest:
        return alerts
    if _is_stale(latest, "sleep_score"):
        return alerts
    score = latest["value"]

    if score < 60:
        alerts.append({
            "title": "睡眠质量差",
            "message": f"睡眠分数 {score:.0f}/100，昨晚睡眠质量很差",
            "severity": "warning",
            "metric": "sleep_score",
        })

    # Check consecutive bad sleep
    data = store.get_recent_metrics(person_id, "sleep_score", days=7)
    bad_days = sum(1 for d in data if d["value"] < 70)
    if bad_days >= 3:
        alerts.append({
            "title": "连续睡眠不佳",
            "message": f"过去7天有 {bad_days} 天睡眠分数低于70，长期睡眠差影响免疫力",
            "severity": "warning",
            "metric": "sleep_score",
        })
    return alerts


def _check_symptoms(store, person_id: str) -> list[dict]:
    """Check if there are recent symptoms that deserve attention."""
    alerts = []
    if _is_suppressed("symptoms"):
        return alerts
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
