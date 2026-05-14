"""Memory Organizer agent helpers."""

from __future__ import annotations

from dataclasses import dataclass

from mira.kernel.consolidation import MemoryConsolidator
from mira.kernel.schema import MemoryKernel


@dataclass
class MemoryOrganizer:
    name: str = "memory_organizer"
    model: str = "claude-haiku"
    skills: list[str] = None
    token_budget: int = 16000

    def __post_init__(self) -> None:
        if self.skills is None:
            self.skills = ["memory_consolidation", "decay", "duplication_detection"]

    def daily_maintenance(self, kernel: MemoryKernel) -> MemoryKernel:
        MemoryConsolidator().apply_decay(kernel)
        return kernel
