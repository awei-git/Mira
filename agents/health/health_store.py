"""Health data store — PostgreSQL access layer for health monitoring.

All queries include person_id filtering for per-user isolation.
This module is the ONLY code path that reads/writes health tables.
"""

import logging
from datetime import datetime, timedelta, timezone, date

import psycopg2
import psycopg2.extras

log = logging.getLogger("health_store")


class HealthStore:
    def __init__(self, db_url: str):
        self._db_url = db_url
        self._conn = None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._db_url)
            self._conn.autocommit = True
        return self._conn

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

    # ---- Metrics (weight, sleep, steps, heart rate, etc.) ----

    def insert_metric(
        self,
        person_id: str,
        metric_type: str,
        value: float,
        unit: str = "",
        source: str = "manual",
        recorded_at: datetime | None = None,
    ):
        """Insert a single health metric."""
        if recorded_at is None:
            recorded_at = datetime.now(timezone.utc)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_metrics (person_id, metric_type, value, unit, source, recorded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (person_id, metric_type, value, unit, source, recorded_at),
            )
        log.info("Metric: %s %s=%s%s (%s)", person_id, metric_type, value, unit, source)

    def insert_metrics_batch(self, person_id: str, metrics: list[dict], source: str = "apple_health"):
        """Batch insert metrics. Each dict: {type, value, unit, date}."""
        if not metrics:
            return
        with self.conn.cursor() as cur:
            for m in metrics:
                # Deduplicate: skip if exact same person+type+date exists
                cur.execute(
                    "SELECT 1 FROM health_metrics WHERE person_id=%s AND metric_type=%s " "AND recorded_at=%s LIMIT 1",
                    (person_id, m["type"], m["date"]),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO health_metrics (person_id, metric_type, value, unit, source, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (person_id, m["type"], m["value"], m.get("unit", ""), source, m["date"]),
                )
        log.info("Batch insert: %s %d metrics from %s", person_id, len(metrics), source)

    def get_recent_metrics(self, person_id: str, metric_type: str, days: int = 30) -> list[dict]:
        """Get recent metrics for a person, sorted by date descending."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT value, unit, recorded_at as date, source FROM health_metrics "
                "WHERE person_id=%s AND metric_type=%s AND recorded_at >= %s "
                "ORDER BY recorded_at DESC",
                (person_id, metric_type, since),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def get_latest_metric(self, person_id: str, metric_type: str) -> dict | None:
        """Get the most recent value for a metric."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT value, unit, recorded_at as date FROM health_metrics "
                "WHERE person_id=%s AND metric_type=%s "
                "ORDER BY recorded_at DESC LIMIT 1",
                (person_id, metric_type),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def get_all_metric_types(self, person_id: str) -> list[str]:
        """List all metric types with data for a person."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT metric_type FROM health_metrics " "WHERE person_id=%s ORDER BY metric_type",
                (person_id,),
            )
            return [r[0] for r in cur.fetchall()]

    # ---- Notes (symptoms, medication, observations) ----

    def insert_note(self, person_id: str, category: str, content: str, note_date: date | None = None):
        """Insert a health note."""
        if note_date is None:
            note_date = date.today()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_notes (person_id, note_date, category, content) " "VALUES (%s, %s, %s, %s)",
                (person_id, note_date, category, content),
            )
        log.info("Note: %s [%s] %s", person_id, category, content[:60])

    def get_recent_notes(self, person_id: str, days: int = 30) -> list[dict]:
        """Get recent health notes."""
        since = date.today() - timedelta(days=days)
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT note_date as date, category, content FROM health_notes "
                "WHERE person_id=%s AND note_date >= %s "
                "ORDER BY note_date DESC",
                (person_id, since),
            )
            return [dict(r) for r in cur.fetchall()]

    # ---- Reports (parsed checkup PDFs) ----

    def insert_report(
        self,
        person_id: str,
        report_date: date,
        parsed_json: dict,
        summary: str = "",
        report_type: str = "annual_checkup",
        source_file: str = "",
    ):
        """Insert a parsed checkup report."""
        import json

        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_reports (person_id, report_date, report_type, "
                "source_file, parsed_json, summary) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    person_id,
                    report_date,
                    report_type,
                    source_file,
                    json.dumps(parsed_json, ensure_ascii=False),
                    summary,
                ),
            )
        log.info("Report: %s %s %s", person_id, report_date, report_type)

    def get_recent_reports(self, person_id: str, limit: int = 5) -> list[dict]:
        """Get recent checkup reports."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT report_date, report_type, summary, parsed_json "
                "FROM health_reports WHERE person_id=%s "
                "ORDER BY report_date DESC LIMIT %s",
                (person_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    # ---- Insights (daily/weekly/alert — generated content) ----

    def upsert_insight(self, person_id: str, insight_date: date, insight_type: str, content: str, model: str = ""):
        """Insert or replace a health insight for a given date+type."""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO health_insights (person_id, insight_date, insight_type, content, model) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (person_id, insight_date, insight_type) "
                "DO UPDATE SET content = EXCLUDED.content, model = EXCLUDED.model",
                (person_id, insight_date, insight_type, content, model),
            )
        log.info("Insight upserted: %s %s %s", person_id, insight_date, insight_type)

    def get_latest_insight(self, person_id: str, insight_type: str = "daily") -> dict | None:
        """Get the most recent insight of a given type."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT insight_date, insight_type, content, model, created_at "
                "FROM health_insights WHERE person_id=%s AND insight_type=%s "
                "ORDER BY insight_date DESC LIMIT 1",
                (person_id, insight_type),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def get_recent_insights(self, person_id: str, insight_type: str = "daily", limit: int = 7) -> list[dict]:
        """Get recent insights for history."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT insight_date, insight_type, content, model, created_at "
                "FROM health_insights WHERE person_id=%s AND insight_type=%s "
                "ORDER BY insight_date DESC LIMIT %s",
                (person_id, insight_type, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    # ---- Monitoring (anomaly detection helpers) ----

    def get_metric_stats(self, person_id: str, metric_type: str, days: int = 7) -> dict | None:
        """Get statistics for a metric over recent days."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) as count, AVG(value) as avg, "
                "MIN(value) as min, MAX(value) as max, STDDEV(value) as stddev "
                "FROM health_metrics WHERE person_id=%s AND metric_type=%s "
                "AND recorded_at >= %s",
                (person_id, metric_type, since),
            )
            row = cur.fetchone()
        if row and row["count"] > 0:
            return dict(row)
        return None
