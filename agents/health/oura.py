"""Oura Ring API v2 integration — fetch heart rate, HRV, sleep, SpO2, temperature.

Runs on Mac Studio, writes directly to PostgreSQL. No Apple Health dependency.
API docs: https://cloud.ouraring.com/v2/docs
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

log = logging.getLogger("health_oura")

BASE_URL = "https://api.ouraring.com/v2/usercollection"


class OuraClient:
    def __init__(self, access_token: str):
        self.token = access_token
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_heart_rate(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get heart rate samples. Returns list of {bpm, source, timestamp}."""
        params = {"start_datetime": f"{start_date}T00:00:00+00:00"}
        if end_date:
            params["end_datetime"] = f"{end_date}T23:59:59+00:00"
        data = self._get("heartrate", params)
        return data.get("data", [])

    def get_daily_sleep(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get daily sleep summaries. Includes HRV (rMSSD)."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        data = self._get("daily_sleep", params)
        return data.get("data", [])

    def get_sleep(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get detailed sleep sessions. Includes HRV, HR, temperature."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        data = self._get("sleep", params)
        return data.get("data", [])

    def get_daily_readiness(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get readiness scores. Includes HRV balance, resting HR."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        data = self._get("daily_readiness", params)
        return data.get("data", [])

    def get_daily_spo2(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get daily SpO2 (blood oxygen) averages."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        try:
            data = self._get("daily_spo2", params)
            return data.get("data", [])
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                log.info("SpO2 endpoint not available for this account")
                return []
            raise

    def get_daily_activity(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get daily activity: steps, calories, active time, score."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        data = self._get("daily_activity", params)
        return data.get("data", [])

    def get_daily_stress(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get daily stress data: stress_high, recovery_high, day_summary."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        try:
            data = self._get("daily_stress", params)
            return data.get("data", [])
        except requests.HTTPError:
            log.info("Stress endpoint not available")
            return []

    def get_workout(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get workout sessions: activity type, calories, distance, duration."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        data = self._get("workout", params)
        return data.get("data", [])

    def get_daily_resilience(self, start_date: str, end_date: str = "") -> list[dict]:
        """Get resilience data: sleep_recovery, daytime_recovery, stress."""
        params = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        try:
            data = self._get("daily_resilience", params)
            return data.get("data", [])
        except requests.HTTPError:
            log.info("Resilience endpoint not available")
            return []


def fetch_and_store(store, access_token: str, person_id: str, days_back: int = 1) -> int:
    """Fetch Oura data and store in PostgreSQL. Returns number of metrics stored."""
    client = OuraClient(access_token)
    start = (date.today() - timedelta(days=days_back)).isoformat()
    end = date.today().isoformat()
    count = 0

    # 1. Heart rate (resting — pick the lowest from rest/sleep sources)
    try:
        hr_data = client.get_heart_rate(start, end)
        rest_hrs = [h for h in hr_data if h.get("source") in ("rest", "sleep")]
        if rest_hrs:
            # Use the most recent resting HR
            latest = rest_hrs[-1]
            store.insert_metric(
                person_id,
                "heart_rate",
                float(latest["bpm"]),
                unit="bpm",
                source="oura",
                recorded_at=datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00")),
            )
            count += 1
    except Exception as e:
        log.warning("Oura heart rate fetch failed: %s", e)

    # 2. Sleep + HRV from daily_sleep
    try:
        sleep_data = client.get_daily_sleep(start, end)
        for s in sleep_data:
            day = s.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T08:00:00+00:00")

            # Sleep score
            if s.get("score"):
                store.insert_metric(
                    person_id, "sleep_score", float(s["score"]), unit="", source="oura", recorded_at=recorded
                )
                count += 1

            # HRV (from contributors)
            contributors = s.get("contributors", {})
            if contributors.get("resting_heart_rate"):
                store.insert_metric(
                    person_id,
                    "heart_rate",
                    float(contributors["resting_heart_rate"]),
                    unit="bpm",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
    except Exception as e:
        log.warning("Oura daily sleep fetch failed: %s", e)

    # 3. Detailed sleep — HRV average
    # Oura returns multiple sleep sessions per day (long_sleep + naps). Only the
    # long_sleep session is the real nightly sleep — naps must NOT overwrite it
    # for sleep_hours/HRV/respiratory rate. Group by day, prefer long_sleep,
    # fall back to the longest session if no long_sleep present.
    try:
        sleep_detail = client.get_sleep(start, end)
        # Group sessions by day, pick best per day
        by_day: dict[str, dict] = {}
        for s in sleep_detail:
            day = s.get("day", start)
            stype = s.get("type", "")
            duration = s.get("total_sleep_duration") or 0
            current = by_day.get(day)
            # Prefer long_sleep; otherwise prefer the longest session
            if (
                current is None
                or (stype == "long_sleep" and current.get("type") != "long_sleep")
                or (current.get("type") != "long_sleep" and duration > (current.get("total_sleep_duration") or 0))
            ):
                by_day[day] = s

        for day, s in by_day.items():
            recorded = datetime.fromisoformat(f"{day}T08:00:00+00:00")

            hrv = s.get("average_hrv")
            if hrv:
                store.insert_metric(person_id, "hrv", float(hrv), unit="ms", source="oura", recorded_at=recorded)
                count += 1

            total_seconds = s.get("total_sleep_duration")
            if total_seconds:
                hours = total_seconds / 3600.0
                store.insert_metric(
                    person_id, "sleep_hours", round(hours, 2), unit="hours", source="oura", recorded_at=recorded
                )
                count += 1

            if s.get("average_breath"):
                store.insert_metric(
                    person_id,
                    "respiratory_rate",
                    float(s["average_breath"]),
                    unit="brpm",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1

            if s.get("lowest_heart_rate"):
                store.insert_metric(
                    person_id,
                    "resting_hr_lowest",
                    float(s["lowest_heart_rate"]),
                    unit="bpm",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
    except Exception as e:
        log.warning("Oura sleep detail fetch failed: %s", e)

    # 4. Readiness score
    try:
        readiness = client.get_daily_readiness(start, end)
        for r in readiness:
            day = r.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T08:00:00+00:00")
            if r.get("score"):
                store.insert_metric(
                    person_id, "readiness_score", float(r["score"]), unit="", source="oura", recorded_at=recorded
                )
                count += 1

            # Temperature deviation from readiness
            contributors = r.get("contributors", {})
            temp = contributors.get("body_temperature")
            if temp is not None:
                store.insert_metric(
                    person_id, "temperature_deviation", float(temp), unit="°C", source="oura", recorded_at=recorded
                )
                count += 1
    except Exception as e:
        log.warning("Oura readiness fetch failed: %s", e)

    # 5. SpO2
    try:
        spo2_data = client.get_daily_spo2(start, end)
        for s in spo2_data:
            day = s.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T08:00:00+00:00")
            avg = s.get("spo2_percentage", {}).get("average")
            if avg:
                store.insert_metric(
                    person_id, "blood_oxygen", float(avg), unit="%", source="oura", recorded_at=recorded
                )
                count += 1
    except Exception as e:
        log.warning("Oura SpO2 fetch failed: %s", e)

    # 6. Daily Activity — steps, calories, active time, activity score
    try:
        activity_data = client.get_daily_activity(start, end)
        for a in activity_data:
            day = a.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T20:00:00+00:00")

            if a.get("score"):
                store.insert_metric(
                    person_id, "activity_score", float(a["score"]), unit="", source="oura", recorded_at=recorded
                )
                count += 1
            if a.get("steps"):
                store.insert_metric(
                    person_id, "steps", float(a["steps"]), unit="steps", source="oura", recorded_at=recorded
                )
                count += 1
            if a.get("active_calories"):
                store.insert_metric(
                    person_id,
                    "active_calories",
                    float(a["active_calories"]),
                    unit="kcal",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if a.get("total_calories"):
                store.insert_metric(
                    person_id,
                    "total_calories",
                    float(a["total_calories"]),
                    unit="kcal",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            # Active time in minutes
            high = a.get("high_activity_time", 0) or 0
            medium = a.get("medium_activity_time", 0) or 0
            if high + medium > 0:
                store.insert_metric(
                    person_id,
                    "active_minutes",
                    float((high + medium) / 60),
                    unit="min",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if a.get("sedentary_time"):
                store.insert_metric(
                    person_id,
                    "sedentary_hours",
                    float(a["sedentary_time"]) / 3600,
                    unit="hours",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if a.get("inactivity_alerts"):
                store.insert_metric(
                    person_id,
                    "inactivity_alerts",
                    float(a["inactivity_alerts"]),
                    unit="",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
    except Exception as e:
        log.warning("Oura activity fetch failed: %s", e)

    # 7. Daily Stress
    try:
        stress_data = client.get_daily_stress(start, end)
        for s in stress_data:
            day = s.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T20:00:00+00:00")

            if s.get("stress_high") is not None:
                store.insert_metric(
                    person_id, "stress_high", float(s["stress_high"]), unit="min", source="oura", recorded_at=recorded
                )
                count += 1
            if s.get("recovery_high") is not None:
                store.insert_metric(
                    person_id,
                    "recovery_high",
                    float(s["recovery_high"]),
                    unit="min",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if s.get("day_summary"):
                # Store as categorical: "restored"=1, "normal"=2, "stressful"=3
                summary_map = {"restored": 1, "normal": 2, "stressful": 3}
                val = summary_map.get(s["day_summary"], 2)
                store.insert_metric(person_id, "stress_level", float(val), unit="", source="oura", recorded_at=recorded)
                count += 1
    except Exception as e:
        log.warning("Oura stress fetch failed: %s", e)

    # 8. Workouts
    try:
        workout_data = client.get_workout(start, end)
        for w in workout_data:
            day = w.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T12:00:00+00:00")
            if w.get("start_datetime"):
                try:
                    recorded = datetime.fromisoformat(w["start_datetime"].replace("Z", "+00:00"))
                except Exception:
                    pass

            # Duration in minutes
            if w.get("start_datetime") and w.get("end_datetime"):
                try:
                    s_dt = datetime.fromisoformat(w["start_datetime"].replace("Z", "+00:00"))
                    e_dt = datetime.fromisoformat(w["end_datetime"].replace("Z", "+00:00"))
                    duration_min = (e_dt - s_dt).total_seconds() / 60
                    activity = w.get("activity", "workout")
                    store.insert_metric(
                        person_id, "workout", duration_min, unit="min", source=f"oura:{activity}", recorded_at=recorded
                    )
                    count += 1
                except Exception:
                    pass

            if w.get("calories"):
                store.insert_metric(
                    person_id,
                    "workout_calories",
                    float(w["calories"]),
                    unit="kcal",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if w.get("distance"):
                store.insert_metric(
                    person_id, "workout_distance", float(w["distance"]), unit="m", source="oura", recorded_at=recorded
                )
                count += 1
            if w.get("intensity"):
                intensity_map = {"easy": 1, "moderate": 2, "hard": 3}
                store.insert_metric(
                    person_id,
                    "workout_intensity",
                    float(intensity_map.get(w["intensity"], 2)),
                    unit="",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
    except Exception as e:
        log.warning("Oura workout fetch failed: %s", e)

    # 9. Daily Resilience
    try:
        resilience_data = client.get_daily_resilience(start, end)
        for r in resilience_data:
            day = r.get("day", start)
            recorded = datetime.fromisoformat(f"{day}T20:00:00+00:00")
            if r.get("level"):
                level_map = {"limited": 1, "adequate": 2, "solid": 3, "strong": 4, "exceptional": 5}
                store.insert_metric(
                    person_id,
                    "resilience_level",
                    float(level_map.get(r["level"], 2)),
                    unit="",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            contributors = r.get("contributors", {})
            if contributors.get("sleep_recovery") is not None:
                store.insert_metric(
                    person_id,
                    "sleep_recovery",
                    float(contributors["sleep_recovery"]),
                    unit="",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
            if contributors.get("daytime_recovery") is not None:
                store.insert_metric(
                    person_id,
                    "daytime_recovery",
                    float(contributors["daytime_recovery"]),
                    unit="",
                    source="oura",
                    recorded_at=recorded,
                )
                count += 1
    except Exception as e:
        log.warning("Oura resilience fetch failed: %s", e)

    log.info("Oura: stored %d metrics for %s (days_back=%d)", count, person_id, days_back)
    return count
