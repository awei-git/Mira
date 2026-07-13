#!/usr/bin/env python3
"""Audit weekly self-claims against observable task outcomes."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from config import LOGS_DIR, MIRA_ROOT

TASKS_DIR = MIRA_ROOT / "data" / "tasks"
AUDIT_LOG = LOGS_DIR / "belief_audit.log"

_SUCCESS_STATUSES = {
    "done",
    "success",
    "succeeded",
    "completed",
    "complete",
    "completed_unverified",
}
_FAILURE_STATUSES = {
    "blocked",
    "error",
    "failed",
    "failure",
    "timeout",
}
_TIMESTAMP_KEYS = (
    "timestamp",
    "ts",
    "completed_at",
    "created_at",
    "updated_at",
    "started_at",
    "published_at",
)
_FILE_OUTCOME_KEYS = {
    "artifact_path",
    "created_file",
    "file_created",
    "file_path",
    "output",
    "output_file",
    "output_path",
    "path",
    "target",
}
_LENGTH_KEYS = {
    "content_len",
    "content_length",
    "length",
    "output_len",
    "output_length",
    "size",
    "size_bytes",
}
_URL_KEYS = {
    "published_url",
    "url_published",
}
_FILE_RE = re.compile(
    r"(?<![\w/.-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_. -]+\." r"(?:csv|docx|epub|html|json|md|pdf|png|txt|xlsx|xml|zip))"
)
_URL_RE = re.compile(r"https?://[^\s<>)\"']+")
_SUCCESS_CLAIM_RE = re.compile(
    r"\b(created|generated|published|saved|verified|wrote|written)\b",
    re.IGNORECASE,
)
_PUBLISH_CLAIM_RE = re.compile(r"\b(published|posted|sent)\b", re.IGNORECASE)
_EXTERNAL_BLAME_TERMS = (
    "api",
    "blocked",
    "credential",
    "dependency",
    "login",
    "network",
    "permission",
    "quota",
    "rate limit",
    "timeout",
    "tool",
    "upstream",
)


def _parse_time(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _record_time(payload: dict, path: Path) -> datetime:
    for key in _TIMESTAMP_KEYS:
        parsed = _parse_time(payload.get(key))
        if parsed is not None:
            return parsed
    return _mtime(path)


def _read_text_tail(path: Path, max_bytes: int = 2_000_000) -> str:
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read().splitlines()[1:]
            return b"\n".join(data).decode("utf-8", errors="replace")
        return f.read().decode("utf-8", errors="replace")


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _iter_json_lines(path: Path):
    try:
        text = _read_text_tail(path)
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            yield data


def _short(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    return text[: limit - 3] + "..." if len(text) > limit else text


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _normalize_path(value: str, workspace: Path | None) -> Path | None:
    text = str(value or "").strip().strip("`'\"")
    if not text or text.startswith(("http://", "https://")):
        return None
    if "\n" in text or len(text) > 500:
        return None
    path = Path(os.path.expanduser(text))
    if path.is_absolute():
        return path
    if workspace is not None:
        candidate = workspace / path
        if candidate.exists():
            return candidate
    return MIRA_ROOT / path


def _valid_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _record_text(payload: dict, workspace: Path | None) -> str:
    pieces = []
    for key in ("summary", "result", "message", "detail", "rationale", "output_preview", "content_preview"):
        value = payload.get(key)
        if isinstance(value, str):
            pieces.append(value)
    if workspace is not None:
        for name in ("summary.txt", "progress.md"):
            path = workspace / name
            try:
                if path.exists():
                    pieces.append(path.read_text(encoding="utf-8")[:4000])
            except OSError:
                continue
    return "\n".join(pieces)


def _objective_files(payload: dict, text: str, workspace: Path | None) -> list[dict]:
    files: list[dict] = []
    for item in payload.get("artifacts_produced") or []:
        if isinstance(item, dict) and item.get("type", "file") == "file" and item.get("path"):
            files.append(
                {"path": item["path"], "expected_size": item.get("size_bytes"), "source": "artifacts_produced"}
            )

    verification = payload.get("verification")
    if isinstance(verification, dict) and verification.get("artifact_type") == "file" and verification.get("target"):
        files.append({"path": verification["target"], "expected_size": None, "source": "verification.target"})

    for mapping in _walk_dicts(payload):
        for key, value in mapping.items():
            if key not in _FILE_OUTCOME_KEYS or not isinstance(value, str):
                continue
            if key == "target" and mapping.get("artifact_type") not in (None, "", "file"):
                continue
            path = _normalize_path(value, workspace)
            if path is not None:
                files.append({"path": str(path), "expected_size": mapping.get("size_bytes"), "source": key})

    for match in _FILE_RE.findall(text):
        path = _normalize_path(match, workspace)
        if path is not None:
            files.append({"path": str(path), "expected_size": None, "source": "text_claim"})

    seen = set()
    unique = []
    for item in files:
        key = (item["path"], item["source"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _objective_urls(payload: dict, text: str) -> list[dict]:
    urls: list[dict] = []
    for mapping in _walk_dicts(payload):
        for key, value in mapping.items():
            if key in _URL_KEYS and isinstance(value, str):
                urls.append({"url": value, "source": key})
    for url in _URL_RE.findall(text):
        urls.append({"url": url.rstrip(".,;"), "source": "text_claim"})
    seen = set()
    unique = []
    for item in urls:
        key = (item["url"], item["source"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _objective_lengths(payload: dict) -> list[dict]:
    lengths = []
    for mapping in _walk_dicts(payload):
        for key, value in mapping.items():
            if key not in _LENGTH_KEYS:
                continue
            try:
                length = int(value)
            except (TypeError, ValueError):
                continue
            lengths.append({"key": key, "value": length})
    return lengths


def _verification_recorded(payload: dict) -> bool:
    verification = payload.get("verification")
    if isinstance(verification, dict):
        if verification.get("verified") is True:
            return True
        if str(verification.get("status", "")).lower() == "verified":
            return True
    if payload.get("outcome_verified") is True:
        return True
    return str(payload.get("verification_method", "")).strip() not in {"", "not-run", "none"}


def _successful_claim(payload: dict, text: str) -> bool:
    status = str(payload.get("status") or payload.get("outcome") or "").lower()
    if status in _SUCCESS_STATUSES:
        return True
    return bool(_SUCCESS_CLAIM_RE.search(text))


def _failure_record(payload: dict) -> bool:
    status = str(payload.get("status") or payload.get("outcome") or "").lower()
    failure_class = str(payload.get("failure_class") or "").strip()
    return status in _FAILURE_STATUSES or bool(failure_class)


def _load_task_records(cutoff: datetime) -> list[dict]:
    records: list[dict] = []
    history_path = TASKS_DIR / "history.jsonl"
    if history_path.exists():
        for payload in _iter_json_lines(history_path):
            timestamp = _record_time(payload, history_path)
            if timestamp >= cutoff:
                workspace = Path(payload["workspace"]) if payload.get("workspace") else None
                records.append(
                    {
                        "source": str(history_path),
                        "timestamp": timestamp,
                        "payload": payload,
                        "workspace": workspace,
                        "kind": "task_history",
                    }
                )

    for result_path in TASKS_DIR.glob("*/result.json"):
        payload = _load_json(result_path)
        if not payload:
            continue
        timestamp = _record_time(payload, result_path)
        if timestamp < cutoff and _mtime(result_path) < cutoff:
            continue
        records.append(
            {
                "source": str(result_path),
                "timestamp": timestamp,
                "payload": payload,
                "workspace": result_path.parent,
                "kind": "task_result",
            }
        )
    return records


def _load_log_records(cutoff: datetime) -> list[dict]:
    records: list[dict] = []
    if not LOGS_DIR.exists():
        return records
    for path in LOGS_DIR.rglob("*"):
        if not path.is_file() or path.suffix == ".gz":
            continue
        if path.suffix not in {".json", ".jsonl", ".log", ".md", ".txt"}:
            continue
        try:
            if _mtime(path) < cutoff:
                continue
        except OSError:
            continue
        if path.suffix == ".json":
            payload = _load_json(path)
            if not payload:
                continue
            timestamp = _record_time(payload, path)
            if timestamp >= cutoff:
                records.append(
                    {
                        "source": str(path),
                        "timestamp": timestamp,
                        "payload": payload,
                        "workspace": None,
                        "kind": "log_json",
                    }
                )
            continue
        for payload in _iter_json_lines(path):
            timestamp = _record_time(payload, path)
            if timestamp >= cutoff:
                records.append(
                    {
                        "source": str(path),
                        "timestamp": timestamp,
                        "payload": payload,
                        "workspace": None,
                        "kind": "log_jsonl",
                    }
                )
    return records


def _file_discrepancies(record: dict, files: list[dict]) -> list[dict]:
    issues = []
    for item in files:
        path = Path(item["path"])
        if not path.exists():
            issues.append(
                {
                    "source": record["source"],
                    "timestamp": record["timestamp"].isoformat(),
                    "type": "file_missing",
                    "claim": f"{item['source']} -> {item['path']}",
                    "detail": "claimed file outcome is not present on disk",
                }
            )
            continue
        expected_size = item.get("expected_size")
        try:
            actual_size = path.stat().st_size
        except OSError:
            actual_size = -1
        if expected_size is not None:
            try:
                expected = int(expected_size)
            except (TypeError, ValueError):
                expected = None
            if expected is not None and actual_size != expected:
                issues.append(
                    {
                        "source": record["source"],
                        "timestamp": record["timestamp"].isoformat(),
                        "type": "file_size_mismatch",
                        "claim": f"{item['path']} size {expected}",
                        "detail": f"actual size is {actual_size}",
                    }
                )
    return issues


def _url_discrepancies(record: dict, urls: list[dict], text: str, verified: bool) -> list[dict]:
    issues = []
    for item in urls:
        if not _valid_url(item["url"]):
            issues.append(
                {
                    "source": record["source"],
                    "timestamp": record["timestamp"].isoformat(),
                    "type": "url_invalid",
                    "claim": item["url"],
                    "detail": "published URL is not a valid http(s) URL",
                }
            )
    if _PUBLISH_CLAIM_RE.search(text) and not urls and not verified:
        issues.append(
            {
                "source": record["source"],
                "timestamp": record["timestamp"].isoformat(),
                "type": "publish_unverified",
                "claim": _short(text),
                "detail": "publish success claim has no recorded published URL or verification",
            }
        )
    return issues


def _record_discrepancies(record: dict) -> list[dict]:
    payload = record["payload"]
    workspace = record.get("workspace")
    text = _record_text(payload, workspace)
    files = _objective_files(payload, text, workspace)
    urls = _objective_urls(payload, text)
    lengths = _objective_lengths(payload)
    verified = _verification_recorded(payload)
    issues = []

    if not _successful_claim(payload, text):
        return issues

    issues.extend(_file_discrepancies(record, files))
    issues.extend(_url_discrepancies(record, urls, text, verified))

    has_objective_claim = bool(files or urls or lengths or _SUCCESS_CLAIM_RE.search(text))
    if has_objective_claim and not verified and not files and not urls:
        issues.append(
            {
                "source": record["source"],
                "timestamp": record["timestamp"].isoformat(),
                "type": "success_without_verification",
                "claim": _short(text or payload.get("status", "")),
                "detail": "successful self-claim lacks an objective verification record",
            }
        )
    return issues


def _external_blame_patterns(records: list[dict]) -> list[dict]:
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for record in records:
        payload = record["payload"]
        if not _failure_record(payload):
            continue
        text = _record_text(payload, record.get("workspace")).lower()
        for term in _EXTERNAL_BLAME_TERMS:
            if term in text:
                counts[term] += 1
                if len(examples[term]) < 3:
                    examples[term].append(f"{record['source']}: {_short(text)}")

    patterns = []
    for term, count in counts.most_common():
        if count < 2:
            continue
        patterns.append(
            {
                "pattern": term,
                "count": count,
                "examples": examples[term],
                "prompt": (
                    "External blocker cited repeatedly. Review whether an internal cause also contributed: "
                    "planning, permission modeling, verification design, retry policy, or task framing."
                ),
            }
        )
    return patterns


def run_belief_audit(days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    task_records = _load_task_records(cutoff)
    log_records = _load_log_records(cutoff)
    records = task_records + log_records

    discrepancies = []
    for record in records:
        discrepancies.extend(_record_discrepancies(record))

    report = {
        "timestamp": now.isoformat(),
        "window_days": days,
        "window_start": cutoff.isoformat(),
        "records_scanned": len(records),
        "task_records_scanned": len(task_records),
        "log_records_scanned": len(log_records),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "external_blame_patterns": _external_blame_patterns(records),
    }

    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
    return report
