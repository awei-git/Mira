"""Centralized logging configuration for Mira agent system.

Provides structured JSON logging for machine parsing alongside
human-readable console output.

Usage:
    from log_config import setup_logging
    setup_logging()  # call once at process entry point
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Attach structured context if present
        if hasattr(record, "agent"):
            entry["agent"] = record.agent
        if hasattr(record, "task_id"):
            entry["task_id"] = record.task_id
        if hasattr(record, "user_id"):
            entry["user_id"] = record.user_id
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """Human-readable console format."""

    def __init__(self):
        super().__init__("%(asctime)s [%(levelname)s] %(message)s")


def setup_logging(
    logs_dir: Path | None = None,
    level: int = logging.INFO,
    json_logs: bool = True,
) -> None:
    """Configure root logging with console + file handlers.

    Args:
        logs_dir: Directory for log files. If None, file logging is skipped.
        level: Minimum log level.
        json_logs: If True, file logs are JSON-formatted for machine parsing.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    root.handlers.clear()

    # Console — always human-readable
    console = logging.StreamHandler()
    console.setFormatter(_ConsoleFormatter())
    root.addHandler(console)

    # File — JSON or human-readable
    if logs_dir:
        logs_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")

        # Human-readable log (backward compatible)
        file_handler = logging.FileHandler(logs_dir / f"{today}.log", encoding="utf-8")
        file_handler.setFormatter(_ConsoleFormatter())
        root.addHandler(file_handler)

        # JSON log (new, for machine parsing)
        if json_logs:
            json_handler = logging.FileHandler(logs_dir / f"{today}.jsonl", encoding="utf-8")
            json_handler.setFormatter(_JsonFormatter())
            root.addHandler(json_handler)


def with_context(logger: logging.Logger, **kwargs) -> logging.LoggerAdapter:
    """Create a logger adapter with structured context fields.

    Usage:
        log = with_context(logging.getLogger("mira"), agent="writer", task_id="abc123")
        log.info("Starting task")  # JSON output includes agent + task_id fields
    """
    return logging.LoggerAdapter(logger, kwargs)
