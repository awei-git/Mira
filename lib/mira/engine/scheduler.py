"""Minimal V3 scheduler registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pipeline import Pipeline


@dataclass
class PipelineScheduler:
    pipelines: dict[str, Pipeline] = field(default_factory=dict)
    enabled: set[str] = field(default_factory=set)

    def register(self, pipeline: Pipeline, enabled: bool = True) -> None:
        self.pipelines[pipeline.name] = pipeline
        if enabled:
            self.enabled.add(pipeline.name)

    def due_for_trigger(self, trigger_type: str) -> list[Pipeline]:
        return [p for name, p in self.pipelines.items() if name in self.enabled and p.trigger.type == trigger_type]
