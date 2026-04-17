"""Typed boundary schemas — contract between super, worker, bridge, and memory.

Introduced by Phase 0 pillar 2 (typing) and Phase 1 (trajectory loop).
Each schema represents a value crossing a module boundary; internal
structures stay as dicts. Keep this package lean.
"""

from .trajectory import (
    Turn,
    ToolStat,
    TrajectoryRecord,
)
from .boundaries import (
    BridgeItem,
    TaskRequest,
    TaskResult,
    AgentState,
)

__all__ = [
    "Turn",
    "ToolStat",
    "TrajectoryRecord",
    "BridgeItem",
    "TaskRequest",
    "TaskResult",
    "AgentState",
]
