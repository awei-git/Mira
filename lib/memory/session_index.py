"""SQLite FTS5 session transcript index (Phase 2).

Independent from the PostgreSQL episodic_memory store — FTS5 serves a
different purpose: **exact-phrase recall of past conversations**,
turn-level granularity, zero external-service dependency.

Schema (FTS5 virtual table):
    session_turns(task_id UNINDEXED, agent UNINDEXED, ts UNINDEXED,
                  role UNINDEXED, text)

Retention: 90 days rolling, pruned via `prune_older_than`.

Writes from `index_trajectory(TrajectoryRecord)` are best-effort
(soft-fail on IO error, never raise) so they stay off the critical
task path.

Reads via `search(query, k, agent=None, since=None)` return ranked
`Snippet` objects suitable for prompt injection.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import SOUL_DIR
from schemas.trajectory import TrajectoryRecord

log = logging.getLogger("mira.memory.session_index")

DB_FILE = SOUL_DIR / "session_index.db"

RETENTION_DAYS = 90


@dataclass
class Snippet:
    """One match returned by FTS5 search."""

    task_id: str
    agent: str
    ts: str
    role: str
    text: str
    rank: float  # lower is better (bm25 default)


# --- Connection helpers ---------------------------------------------------


@contextmanager
def _connect(path: Path | None = None):
    """Open DB (creating schema on first use), yield conn, close on exit."""
    target = path or DB_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS session_turns USING fts5(
            task_id UNINDEXED,
            agent UNINDEXED,
            ts UNINDEXED,
            role UNINDEXED,
            text,
            tokenize = 'unicode61 remove_diacritics 0'
        );
        """
    )


# --- Public API -----------------------------------------------------------


def index_trajectory(trajectory: TrajectoryRecord, *, path: Path | None = None) -> int:
    """Insert all turns of a trajectory into the index.

    Returns number of rows inserted. Never raises on IO failure.
    """
    try:
        with _connect(path) as conn:
            cur = conn.cursor()
            rows = []
            ts = trajectory.timestamp.isoformat()
            for turn in trajectory.conversations:
                text = (turn.content or "").strip()
                if turn.tool_result_preview:
                    text = f"{text}\n[tool_result] {turn.tool_result_preview}".strip()
                if not text:
                    continue
                rows.append((trajectory.task_id, trajectory.agent, ts, turn.role, text))
            if rows:
                cur.executemany(
                    "INSERT INTO session_turns(task_id, agent, ts, role, text) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
            return len(rows)
    except sqlite3.Error as e:
        log.warning("index_trajectory failed (task=%s): %s", trajectory.task_id, e)
        return 0


def search(
    query: str,
    *,
    k: int = 5,
    agent: str | None = None,
    since: datetime | None = None,
    path: Path | None = None,
) -> list[Snippet]:
    """Run a FTS5 query. Optional agent filter and since-timestamp filter.

    Returns up to `k` snippets ranked by BM25 (best first).
    """
    if not query or not query.strip():
        return []
    try:
        with _connect(path) as conn:
            cur = conn.cursor()
            # Sanitize query for FTS5 — prefix wildcard each token.
            safe_query = _normalize_query(query)
            if not safe_query:
                return []
            args: list = [safe_query]
            where = ["session_turns MATCH ?"]
            if agent:
                where.append("agent = ?")
                args.append(agent)
            if since:
                where.append("ts >= ?")
                args.append(since.isoformat())
            sql = (
                "SELECT task_id, agent, ts, role, text, rank FROM session_turns "
                f"WHERE {' AND '.join(where)} ORDER BY rank LIMIT ?"
            )
            args.append(k)
            cur.execute(sql, args)
            return [Snippet(*row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        log.debug("session_index.search failed: %s", e)
        return []


def _normalize_query(query: str) -> str:
    """Escape FTS5 special characters and require each term (prefix match).

    FTS5 reserves `" * ( )`. Strip them; keep alphanumerics + whitespace +
    CJK. Each remaining token gets a trailing `*` for prefix matching,
    which helps recall on partial memory.
    """
    cleaned = "".join(ch for ch in query if ch.isalnum() or ch.isspace() or 0x4E00 <= ord(ch) <= 0x9FFF)
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return ""
    return " ".join(f"{t}*" for t in tokens)


def prune_older_than(days: int = RETENTION_DAYS, *, path: Path | None = None) -> int:
    """Delete rows older than `days`. Returns number deleted."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with _connect(path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM session_turns WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except sqlite3.Error as e:
        log.warning("session_index.prune failed: %s", e)
        return 0


def row_count(*, path: Path | None = None) -> int:
    """Diagnostic: total rows currently indexed."""
    try:
        with _connect(path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM session_turns")
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def format_soul_recall(
    query: str,
    *,
    k: int = 3,
    max_chars_per_snippet: int = 200,
    path: Path | None = None,
) -> str:
    """Render top-k FTS5 hits as a markdown block for soul prompt injection.

    Returns empty string when nothing matches so the caller can append
    unconditionally.
    """
    snippets = search(query, k=k, path=path)
    if not snippets:
        return ""
    lines = ["## Relevant past conversations"]
    for s in snippets:
        preview = s.text.strip().replace("\n", " ")
        if len(preview) > max_chars_per_snippet:
            preview = preview[:max_chars_per_snippet] + "…"
        lines.append(f"- `{s.agent}:{s.role}` ({s.ts[:10]}): {preview}")
    return "\n".join(lines) + "\n"
