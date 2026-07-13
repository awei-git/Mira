"""Weekly evaluator/content diversity drift checks."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MIRA_ROOT = Path(__file__).resolve().parents[2]
QUALITY_STORE = MIRA_ROOT / "agents" / "shared" / "soul" / "drift_log.json"
WRITINGS_OUTPUT_DIR = MIRA_ROOT / "artifacts" / "writings"
V3_ARTICLE_DIR = MIRA_ROOT / "data" / "v3" / "artifacts" / "article_creation"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _published_date_from_name(path: Path) -> datetime | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2})_", path.name)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _artifact_timestamp(path: Path) -> datetime:
    published_date = _published_date_from_name(path)
    if published_date is not None:
        return published_date

    if path.name == "article.md" and path.parent.name.startswith("article_creation_"):
        checkpoint = MIRA_ROOT / "data" / "v3" / "checkpoints" / f"{path.parent.name}.json"
        try:
            data = json.loads(checkpoint.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        evidence = data.get("outputs", {}).get("_causal_evidence", []) if isinstance(data, dict) else []
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict):
                    timestamp = _parse_datetime(item.get("timestamp"))
                    if timestamp is not None:
                        return timestamp

    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _candidate_artifact_paths() -> list[Path]:
    paths: list[Path] = []
    if WRITINGS_OUTPUT_DIR.exists():
        paths.extend((WRITINGS_OUTPUT_DIR / "_published").glob("*.md"))
        paths.extend((WRITINGS_OUTPUT_DIR / "drafts").glob("*.md"))
        paths.extend(WRITINGS_OUTPUT_DIR.glob("*/final.md"))
    if V3_ARTICLE_DIR.exists():
        paths.extend(V3_ARTICLE_DIR.glob("*/article.md"))
    return [path for path in paths if path.is_file()]


def _clean_markdown(text: str) -> str:
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n", " ", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    return text


def _type_token_ratio(text: str) -> float | None:
    tokens = [token.lower() for token in TOKEN_RE.findall(_clean_markdown(text))]
    if not tokens:
        return None
    return len(set(tokens)) / len(tokens)


def _recent_writer_artifacts(sample_size: int) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in _candidate_artifact_paths():
        try:
            timestamp = _artifact_timestamp(path)
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ratio = _type_token_ratio(text)
        if ratio is None:
            continue
        artifacts.append({"path": path, "timestamp": timestamp, "type_token_ratio": ratio})
    artifacts.sort(key=lambda item: item["timestamp"], reverse=True)
    return artifacts[: max(1, int(sample_size))]


def _weekly_average_ttr(artifacts: list[dict[str, Any]]) -> list[tuple[tuple[int, int], float]]:
    weekly: dict[tuple[int, int], list[float]] = {}
    for artifact in artifacts:
        timestamp = artifact["timestamp"]
        iso_year, iso_week, _ = timestamp.isocalendar()
        weekly.setdefault((iso_year, iso_week), []).append(float(artifact["type_token_ratio"]))
    return [(week, sum(values) / len(values)) for week, values in sorted(weekly.items()) if values]


def _normalize_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1.0:
        score = score / 10.0
    if 0.0 <= score <= 1.0:
        return score
    return None


def _recent_quality_score() -> float | None:
    try:
        data = json.loads(QUALITY_STORE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    entries = data.get("writer", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=28)
    scores: list[float] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        timestamp = _parse_datetime(entry.get("timestamp"))
        score = _normalize_score(entry.get("score"))
        if timestamp is None or score is None or timestamp < cutoff:
            continue
        scores.append(score)
    if not scores:
        return None
    return sum(scores) / len(scores)


def compute_drift_alert(sample_size=10):
    artifacts = _recent_writer_artifacts(sample_size)
    weekly_ttr = _weekly_average_ttr(artifacts)[-4:]
    if len(weekly_ttr) < 4:
        return None

    baseline_ttr = weekly_ttr[0][1]
    current_ttr = weekly_ttr[-1][1]
    if baseline_ttr <= 0:
        return None

    drop = (baseline_ttr - current_ttr) / baseline_ttr
    if drop <= 0.15:
        return None

    quality_score = _recent_quality_score()
    if quality_score is None or quality_score < 0.8:
        return None

    average_ttr = sum(float(item["type_token_ratio"]) for item in artifacts) / len(artifacts)
    first_week = f"{weekly_ttr[0][0][0]}-W{weekly_ttr[0][0][1]:02d}"
    last_week = f"{weekly_ttr[-1][0][0]}-W{weekly_ttr[-1][0][1]:02d}"
    return (
        "DRIFT_WARNING evaluator quality average remains high "
        f"(score={quality_score:.3f}) while writer vocabulary diversity declined "
        f"{drop:.1%} over the last 4 sampled weeks "
        f"({first_week} ttr={baseline_ttr:.3f} -> {last_week} ttr={current_ttr:.3f}; "
        f"avg_ttr={average_ttr:.3f}; artifacts={len(artifacts)})."
    )
