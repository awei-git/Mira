"""Bridge staleness monitoring for the notes/iPhone messaging layer."""

import logging
import time
from pathlib import Path

log = logging.getLogger("mira")


def check_bridge_staleness(threshold_seconds: int = 180) -> bool:
    """Return True if the bridge heartbeat file is older than threshold_seconds.

    Reads the mtime of the iCloud Mira-Bridge heartbeat file (heartbeat.json,
    or heartbeat as fallback). Returns False if neither file exists, to avoid
    false positives on first boot or clean-slate setups.
    """
    from config import MIRA_DIR

    for name in ("heartbeat.json", "heartbeat"):
        candidate = Path(MIRA_DIR) / name
        if candidate.exists():
            try:
                age = time.time() - candidate.stat().st_mtime
                return age > threshold_seconds
            except OSError:
                return False
    return False
