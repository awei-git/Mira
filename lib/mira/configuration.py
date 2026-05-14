"""V3 configuration defaults for WebGUI config panels."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from mira.evals import EVAL_CRITERIA
from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES


@dataclass(frozen=True)
class ModelAssignment:
    agent: str
    model: str
    token_budget: int


@dataclass(frozen=True)
class V3Config:
    models: list[ModelAssignment] = field(default_factory=list)
    schedules: dict[str, str] = field(default_factory=dict)
    eval_thresholds: dict[str, object] = field(default_factory=dict)
    token_budgets: dict[str, int] = field(default_factory=dict)
    policy_parameters: dict[str, object] = field(default_factory=dict)
    priority_settings: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["models"] = [asdict(model) for model in self.models]
        return data


def default_v3_config() -> V3Config:
    models = [
        ModelAssignment("orchestrator", "claude-opus", 16000),
        ModelAssignment("writer", "claude-sonnet", 32000),
        ModelAssignment("explorer", "claude-sonnet", 16000),
        ModelAssignment("analyst", "claude-sonnet", 16000),
        ModelAssignment("researcher", "claude-opus", 32000),
        ModelAssignment("coder", "codex", 32000),
        ModelAssignment("social", "claude-sonnet", 8000),
        ModelAssignment("podcast", "claude-sonnet", 16000),
        ModelAssignment("evaluator", "claude-opus", 4000),
        ModelAssignment("memory_organizer", "claude-haiku", 16000),
        ModelAssignment("health", "local-omlx", 4000),
        ModelAssignment("reader", "claude-sonnet", 16000),
        ModelAssignment("discussion", "configurable", 16000),
        ModelAssignment("policy_runner", "none", 0),
        ModelAssignment("monitor", "none", 0),
    ]
    return V3Config(
        models=models,
        schedules={name: pipeline.trigger.detail for name, pipeline in PIPELINE_CATALOG.items()},
        eval_thresholds={name: spec["threshold"] for name, spec in EVAL_CRITERIA.items()},
        token_budgets={item.agent: item.token_budget for item in models},
        policy_parameters={
            "hard_policy_count": sum(len(names) for names in HARD_POLICY_NAMES.values()),
            "max_concurrent_pipelines": 5,
            "opus_concurrency": 2,
            "sonnet_concurrency": 4,
            "haiku_concurrency": 8,
            "tts_sequential": True,
        },
        priority_settings={name: pipeline.priority for name, pipeline in PIPELINE_CATALOG.items()},
    )
