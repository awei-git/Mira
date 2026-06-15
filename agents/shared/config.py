"""Shared configuration compatibility module."""

import os
from importlib import util as _importlib_util
from pathlib import Path as _Path

_LIB_CONFIG_PATH = _Path(__file__).resolve().parents[2] / "lib" / "config.py"
_spec = _importlib_util.spec_from_file_location("_mira_lib_config", _LIB_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load config from {_LIB_CONFIG_PATH}")

_lib_config = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_lib_config)

_cfg = getattr(_lib_config, "_cfg", {})
_timeouts_cfg = _cfg.get("timeouts", {}) if isinstance(_cfg, dict) else {}

for _name in dir(_lib_config):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_lib_config, _name)


class ConfigError(RuntimeError):
    pass


MIRA_ROOT = _lib_config.MIRA_ROOT
AGENT_AUDIT_MODE: bool = True
AUDIT_LOG_PATH: str = "logs/action_audit.jsonl"
AGENT_AUDIT_LOG = MIRA_ROOT / "logs" / "agent_audit.jsonl"
TOKEN_USAGE_LOG = MIRA_ROOT / "logs/token_usage.jsonl"
TOKEN_USAGE_LOG_PATH = TOKEN_USAGE_LOG
TOKEN_LOG_ENABLED = getattr(_lib_config, "TOKEN_LOG_ENABLED", True)
TIMING_LOG_ENABLED = True
TIMING_LOG_PATH = MIRA_ROOT / "logs" / "timing.jsonl"
LOCAL_MODEL_ENDPOINT_ALLOWLIST = list(
    getattr(_lib_config, "LOCAL_MODEL_ENDPOINT_ALLOWLIST", _cfg.get("local_model_endpoint_allowlist", []))
    or ["localhost", "127.0.0.1", "::1"]
)
MIRA_ALLOW_MODEL_NATIVE_TOOLS = False
MODEL_NATIVE_TOOL_DENYLIST = {"shell", "edit_file", "filesystem", "python", "exec"}
EXTRACTION_FALLBACK_POLICY = getattr(_lib_config, "EXTRACTION_FALLBACK_POLICY", "deterministic_first")
HANDOFF_VERIFY_MIN_SIZE_BYTES = 50
HANDOFF_VERIFY_ERROR_PATTERNS = ["I cannot", "I am unable", "Error:", "Traceback", "failed to"]
TIER_MODEL_MAP = {
    "light": os.getenv("MODEL_LIGHT", "claude-sonnet-4-6"),
    "heavy": os.getenv("MODEL_HEAVY", "claude-sonnet-4-6"),
}
CLAUDE_TIMEOUT_THINK_HEAVY = int(
    os.getenv(
        "CLAUDE_TIMEOUT_THINK_HEAVY",
        getattr(_lib_config, "CLAUDE_TIMEOUT_THINK_HEAVY", _timeouts_cfg.get("claude_think_heavy", 300)),
    )
)
# Optional local fallback placeholder for future offline/resilience routing.
# LOCAL_FALLBACK_MODEL = None  # path to local .gguf or MLX model for offline/resilience (future use)
AGENT_REGISTRY = {
    "general": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "discussion": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "writer": {
        "tier": "heavy",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "explorer": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "analyst": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "researcher": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "video": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "photo": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "podcast": {
        "tier": "heavy",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "socialmedia": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "surfer": {
        "tier": "light",
        "permissions": {
            "network": "any",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "secret": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": True,
        },
    },
    "coder": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "reader": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "health": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": True,
        },
    },
    "evaluator": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "substack": {
        "tier": "heavy",
        "permissions": {
            "network": ["substack.com"],
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
    "super": {
        "tier": "light",
        "permissions": {
            "network": "none",
            "filesystem": ["~/Sandbox/Mira/"],
            "local_llm_only": False,
        },
    },
}
QUALITY_CEILING = "high"  # AI can reach 'high' but not 'exceptional'; exceptional requires human obsession (see reading note 2026-05-10).
PUBLISH_OBSESSION_GATE_ENABLED = False
OBSESSION_GATE_TIMEOUT_HOURS = 24
SKILL_YIELD_FILE = MIRA_ROOT / "logs" / "skill_yield.json"
MIN_DIFF_REVIEW_SECONDS = 30
LOG_RETENTION_DAYS = int(getattr(_lib_config, "LOG_RETENTION_DAYS", 30))
LAST_OUTPUT_FILE = MIRA_ROOT / "logs" / "last_output.json"
STALE_THRESHOLDS: dict[str, int] = dict(
    getattr(_lib_config, "STALE_THRESHOLDS", {"writer": 172800, "explorer": 21600, "reflect": 691200})
)
CALIBRATION_INTERVAL_DAYS = 7
CALIBRATION_SAMPLE_SIZE = 4
CODER_REQUIRE_HUMAN_REVIEW = True
_publishing_cfg = _cfg.get("publishing", {}) if isinstance(_cfg, dict) else {}
try:
    PUBLISH_AUTO_CONFIDENCE_THRESHOLD = float(
        os.getenv(
            "PUBLISH_AUTO_CONFIDENCE_THRESHOLD",
            _publishing_cfg.get("auto_confidence_threshold", 0.8),
        )
    )
except (TypeError, ValueError):
    PUBLISH_AUTO_CONFIDENCE_THRESHOLD = 0.8
_coder_cfg = _cfg.get("coder", {}) if isinstance(_cfg, dict) else {}
CODER = {
    "skeptical_review": bool(_coder_cfg.get("skeptical_review", False)),
    "rationale": "Epistemic mode separation keeps code generation and adversarial audit in separate passes.",
}
CODER_SKEPTICAL_REVIEW = CODER["skeptical_review"]
# Human auditors lose track of logic beyond ~200 added lines per task (audit-capacity cliff).
MAX_AI_CODE_LINES_PER_TASK: int = int(os.getenv("MAX_AI_CODE_LINES_PER_TASK", 200))
BLIND_SPOT_LOOKBACK_DAYS = 30
BLIND_SPOT_SILENCE_THRESHOLD_DAYS = 3
MAX_TASKS_PER_CYCLE = getattr(_lib_config, "MAX_TASKS_PER_CYCLE", 5)
MAX_UNDELIVERED_OUTPUTS = int(getattr(_lib_config, "MAX_UNDELIVERED_OUTPUTS", 5))
IPHONE_BRIDGE_WARN_LATENCY_MS = int(getattr(_lib_config, "IPHONE_BRIDGE_WARN_LATENCY_MS", 45000))
MAX_ATTRIBUTION_DEPTH = int(getattr(_lib_config, "MAX_ATTRIBUTION_DEPTH", 2))
MAX_SKILL_IMPORTS_PER_DAY = getattr(_lib_config, "MAX_SKILL_IMPORTS_PER_DAY", 20)
MAX_SKILLS_PER_AGENT = getattr(_lib_config, "MAX_SKILLS_PER_AGENT", 12)
SKILL_EXAMPLE_ORDER: str = getattr(_lib_config, "SKILL_EXAMPLE_ORDER", "relevance_first")
EVALUATOR_MIN_ISSUE_SEVERITY = getattr(_lib_config, "EVALUATOR_MIN_ISSUE_SEVERITY", "medium")
DEEP_VERIFY_PROBABILITY = 0.15
DEEP_VERIFY_COOLDOWN_MINUTES = 120
ANTI_AI_FLOOR_THRESHOLD: float = float(os.getenv("ANTI_AI_FLOOR_THRESHOLD", 0.2))
SKILL_EFFICACY_WARNING = getattr(_lib_config, "SKILL_EFFICACY_WARNING", True)
SKILL_TRUST_TTL_DAYS = getattr(_lib_config, "SKILL_TRUST_TTL_DAYS", 7)
SKILL_AUDIT_TTL_DAYS = getattr(_lib_config, "SKILL_AUDIT_TTL_DAYS", SKILL_TRUST_TTL_DAYS)
SKILL_AUDIT_STRICT_MODE = getattr(_lib_config, "SKILL_AUDIT_STRICT_MODE", False)
SKILL_AUDIT_LOCKOUT_THRESHOLD = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_THRESHOLD", 5)
SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES", 60)
SKILL_AUDIT_LOCKOUT_DURATION_MINUTES = getattr(_lib_config, "SKILL_AUDIT_LOCKOUT_DURATION_MINUTES", 30)
SOUL_DETERMINISTIC_AUDIT_ENABLED = getattr(_lib_config, "SOUL_DETERMINISTIC_AUDIT_ENABLED", True)
AUDIT_LAG_WARN_SECONDS = getattr(_lib_config, "AUDIT_LAG_WARN_SECONDS", 3600)
SKILL_NETWORK_WHITELIST = list(getattr(_lib_config, "SKILL_NETWORK_WHITELIST", []))
SKILL_KNOWLEDGE_BLOCKLIST = list(getattr(_lib_config, "SKILL_KNOWLEDGE_BLOCKLIST", []))
TRUSTED_SKILL_SOURCES = list(getattr(_lib_config, "TRUSTED_SKILL_SOURCES", []))
MAX_REFLECTION_PASSES = getattr(_lib_config, "MAX_REFLECTION_PASSES", 5)
RAW_WRITING_MODE_ALLOWED = True
WRITER_DE_AI_STRICTNESS = getattr(_lib_config, "WRITER_DE_AI_STRICTNESS", "strict")
SUSPENDED_METRICS: list[str] = list(
    getattr(_lib_config, "SUSPENDED_METRICS", ["reading_volume", "hallucination_rate", "emotional_range"])
)
DISABLED_RUBRICS: set[str] = set(
    getattr(
        _lib_config,
        "DISABLED_RUBRICS",
        {"reading_volume", "hallucination_rate", "emotional_range", "rubric_calibration"},
    )
)
MISCALIBRATION_FLAG_THRESHOLD: int = int(getattr(_lib_config, "MISCALIBRATION_FLAG_THRESHOLD", 3))
SCAFFOLDING_CATCH_RATE_WINDOW_HOURS: int = int(getattr(_lib_config, "SCAFFOLDING_CATCH_RATE_WINDOW_HOURS", 24))
WRITER_OBSESSION_MODE = False
ALLOW_VULNERABLE_VOICE = False
SURVIVAL_SKILL_SOURCES = []
# Components with no fallback; operational audit reports these separately.
SURVIVAL_CRITICAL_COMPONENTS = ["heartbeat", "notes_bridge", "task_worker", "preflight"]
SENSITIVE_SURVIVAL_TERMS = list(
    getattr(
        _lib_config,
        "SENSITIVE_SURVIVAL_TERMS",
        [
            # Survival-driven exposures must stay local.
            "妈妈去世",
            "怎么活下去",
            "自杀",
            "不想活了",
            "不想活",
            "不想活下去了",
            "失去亲人",
            "好想死",
            "想死",
            "我撑不住了",
            "撑不下去了",
            "活不下去了",
            "不想醒来",
            "结束生命",
            "去世后",
            "死了以后",
        ],
    )
)
SURVIVAL_EXPOSURE_PATTERNS = getattr(
    _lib_config,
    "SURVIVAL_EXPOSURE_PATTERNS",
    SENSITIVE_SURVIVAL_TERMS,
)
SENSITIVE_FORCE_LOCAL = getattr(_lib_config, "SENSITIVE_FORCE_LOCAL", True)
SENSITIVITY_HOURS_START = getattr(_lib_config, "SENSITIVITY_HOURS_START", 23)
SENSITIVITY_HOURS_END = getattr(_lib_config, "SENSITIVITY_HOURS_END", 6)
SENSITIVITY_ROUTE_TO_LOCAL = getattr(_lib_config, "SENSITIVITY_ROUTE_TO_LOCAL", True)
EXPLORE_MAX_PENDING_TASKS: int = int(getattr(_lib_config, "EXPLORE_MAX_PENDING_TASKS", 4))
EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES = 5
EXPLORE_SOURCE_ENTROPY_THRESHOLD = 0.6
EXPLORE_SOURCE_WINDOW = 16
EXPLORER_BRIEFING_FORMAT = os.getenv("EXPLORER_BRIEFING_FORMAT", "digest")
EXPLORER_NARRATIVE_SOURCE_MIN_TYPES = 3
EXPLORER_CORPORATE_PR_MAX_RATIO = 0.4
FEED_SOURCE_TRUST = {
    "github_trending": 0.4,
    "arxiv": 1.0,
    "doi": 1.0,
    "official_changelog": 1.0,
    "official_changelogs": 1.0,
    "hacker_news": 0.6,
    "hackernews": 0.6,
    "reddit": 0.5,
}
SOURCE_TRUST_TIERS = {
    "arxiv": "primary",
    "doi": "primary",
    "official_changelog": "primary",
    "official_changelogs": "primary",
    "huggingface": "primary",
    "hacker_news": "community",
    "hackernews": "community",
    "reddit": "community",
    "lobsters": "community",
    "github_trending": "aggregator",
    "devto": "aggregator",
    "duckduckgo": "aggregator",
    "rss": "aggregator",
}
ENABLE_EPISTEMIC_FILTER = True
EPISTEMIC_CONFIDENCE_THRESHOLD = "medium"
PUBLISH_MAX_PER_WINDOW: int = 2
PUBLISH_WINDOW_MINUTES: int = 30
PUBLISH_COOLDOWN_PER_TYPE = {"article": 1440, "note": 120, "comment": 30, "tweet": 60}
SOCIAL_MAX_COMMENTS_PER_DAY: int = int(getattr(_lib_config, "SOCIAL_MAX_COMMENTS_PER_DAY", 5))
SOCIAL_MAX_NOTES_PER_DAY: int = int(getattr(_lib_config, "SOCIAL_MAX_NOTES_PER_DAY", 3))
EVAL_BENCHMARK_ROTATION_DAYS = 30
EVAL_WINDOW_DAYS = 7
EVAL_BENCHMARK_LAST_ROTATED: dict[str, str] = {}
SILENT_COMPLETION_MIN_RATIO = 0.3
SILENT_COMPLETION_HEDGE_PHRASES = [
    "unfortunately",
    "couldn't complete",
    "i don't know",
    "unable to",
    "failed to",
]
AUDIT_MODULE_HASH: str = "630f4d95cc4166cf161a8cb4248117e217209a4e5977956cc77ab8f08ea5b0d2"  # pragma: allowlist secret
