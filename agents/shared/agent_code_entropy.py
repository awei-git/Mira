"""Track cumulative agent code-change entropy."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

_MIRA_ROOT = Path(__file__).resolve().parents[2]
_LOG_PATH = _MIRA_ROOT / "logs" / "agent_code_entropy.jsonl"
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _count_added_lines(diff_text: str) -> int:
    return sum(1 for line in (diff_text or "").splitlines() if line.startswith("+") and not line.startswith("+++"))


def _count_removed_lines(diff_text: str) -> int:
    return sum(1 for line in (diff_text or "").splitlines() if line.startswith("-") and not line.startswith("---"))


def _diff_total_lines(diff_text: str) -> int:
    return sum(1 for line in (diff_text or "").splitlines() if line and not line.startswith(("diff --git", "index ")))


def _parse_hunks(diff_text: str) -> list[dict]:
    hunks: list[dict] = []
    current_path = ""
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current_path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                current_path = path[2:] if path.startswith("b/") else path
            continue
        match = _HUNK_RE.match(line)
        if not match:
            continue
        old_start = int(match.group(1))
        old_len = int(match.group(2) or "1")
        new_start = int(match.group(3))
        new_len = int(match.group(4) or "1")
        hunks.append(
            {
                "path": current_path,
                "old_start": old_start,
                "old_end": old_start + max(old_len, 1) - 1,
                "new_start": new_start,
                "new_end": new_start + max(new_len, 1) - 1,
            }
        )
    return hunks


def _load_entries() -> list[dict]:
    entries: list[dict] = []
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as log_file:
            for line in log_file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except FileNotFoundError:
        return []
    return entries


def _coerce_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _ranges_overlap(first: dict, second: dict) -> bool:
    if first.get("path") != second.get("path"):
        return False
    first_old_start = _coerce_int(first.get("old_start", 0))
    first_old_end = _coerce_int(first.get("old_end", 0))
    second_old_start = _coerce_int(second.get("old_start", 0))
    second_old_end = _coerce_int(second.get("old_end", 0))
    first_new_start = _coerce_int(first.get("new_start", 0))
    first_new_end = _coerce_int(first.get("new_end", 0))
    second_new_start = _coerce_int(second.get("new_start", 0))
    second_new_end = _coerce_int(second.get("new_end", 0))
    old_overlap = first_old_start <= second_old_end and second_old_start <= first_old_end
    new_overlap = first_new_start <= second_new_end and second_new_start <= first_new_end
    return bool(old_overlap or new_overlap)


def _overlapping_patch_count(entries: list[dict]) -> int:
    count = 0
    for index, entry in enumerate(entries):
        hunks = entry.get("hunks", [])
        if not isinstance(hunks, list):
            continue
        for earlier in entries[:index]:
            earlier_hunks = earlier.get("hunks", [])
            if not isinstance(earlier_hunks, list):
                continue
            if any(_ranges_overlap(hunk, earlier_hunk) for hunk in hunks for earlier_hunk in earlier_hunks):
                count += 1
                break
    return count


def record_code_change(diff_text) -> None:
    text = str(diff_text or "")
    if not text.strip():
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "diff_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "lines_added": _count_added_lines(text),
        "lines_removed": _count_removed_lines(text),
        "diff_lines": _diff_total_lines(text),
        "hunks": _parse_hunks(text),
    }
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def is_legibility_risk(threshold) -> bool:
    entries = _load_entries()
    if not entries:
        return False
    total_new_lines = sum(_coerce_int(entry.get("lines_added", 0)) for entry in entries)
    total_changed_lines = sum(
        _coerce_int(entry.get("lines_added", 0)) + _coerce_int(entry.get("lines_removed", 0)) for entry in entries
    )
    total_diff_lines = sum(max(1, _coerce_int(entry.get("diff_lines", 0))) for entry in entries)
    edit_density = total_changed_lines / max(total_diff_lines, 1)
    overlapping_patches = _overlapping_patch_count(entries)

    new_line_score = min(total_new_lines / 500, 1.0)
    density_score = min(edit_density, 1.0)
    overlap_score = min(overlapping_patches / 10, 1.0)
    entropy_score = (new_line_score * 0.45) + (density_score * 0.30) + (overlap_score * 0.25)

    try:
        limit = float(threshold)
    except (TypeError, ValueError):
        limit = 0.7
    return entropy_score > limit
