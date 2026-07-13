"""Friction tracker — records wall time for guard layers, categorized as good or bad friction.

Good friction: value-added filtering or verification that catches real problems.
Bad friction: pure infrastructure cost with no filtering benefit.

Usage:
    from friction_monitor import track_friction, get_friction_profile, write_profile

    @track_friction(category='good', label='my_guard')
    def my_guard_function(...): ...
"""

import functools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "friction_profile.json"


class FrictionTracker:
    def __init__(self):
        self._entries: list[dict] = []

    def record(self, category: str, label: str, duration_s: float) -> None:
        self._entries.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "category": category,
                "label": label,
                "duration_s": round(duration_s, 6),
            }
        )

    def get_friction_profile(self) -> dict:
        stats: dict[str, dict] = {}
        for entry in self._entries:
            cat = entry["category"]
            if cat not in stats:
                stats[cat] = {"count": 0, "total_s": 0.0}
            stats[cat]["count"] += 1
            stats[cat]["total_s"] = round(stats[cat]["total_s"] + entry["duration_s"], 6)
        return {
            "entries": list(self._entries),
            "by_category": stats,
        }

    def write_profile(self, path: Path | None = None) -> None:
        target = Path(path) if path else _DEFAULT_LOG_PATH
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            profile = self.get_friction_profile()
            profile["written_at"] = datetime.now(timezone.utc).isoformat()
            target.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


_tracker = FrictionTracker()


def track_friction(category: str, label: str | None = None):
    """Decorator that records wall time and category for a guard function."""

    def decorator(func):
        _label = label or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                _tracker.record(category, _label, time.perf_counter() - t0)

        return wrapper

    return decorator


def get_friction_profile() -> dict:
    return _tracker.get_friction_profile()


def write_profile(path: Path | None = None) -> None:
    _tracker.write_profile(path)
