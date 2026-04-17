"""Worker supervisor — Phase 0 pillar 1.

Detection-only at this stage: exposes `write_heartbeat` (for workers
to call) and `WorkerSupervisor.scan` (for core.py to call each tick).
Live enforcement (SIGTERM / SIGKILL / retry budget) is a follow-up;
detection alone already gives Phase 1 reward a `crashes.jsonl` source.
"""

from .worker_supervisor import (
    CrashReport,
    WorkerSupervisor,
    write_heartbeat,
    clear_heartbeat,
)

__all__ = ["CrashReport", "WorkerSupervisor", "write_heartbeat", "clear_heartbeat"]
