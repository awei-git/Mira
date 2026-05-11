"""Shared configuration compatibility module."""

from importlib import util as _importlib_util
from pathlib import Path as _Path

_LIB_CONFIG_PATH = _Path(__file__).resolve().parents[2] / "lib" / "config.py"
_spec = _importlib_util.spec_from_file_location("_mira_lib_config", _LIB_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load config from {_LIB_CONFIG_PATH}")

_lib_config = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_lib_config)

_cfg = getattr(_lib_config, "_cfg", {})

for _name in dir(_lib_config):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_lib_config, _name)

MIRA_ROOT = _lib_config.MIRA_ROOT
AGENT_REGISTRY = {
    "general": {
        "tier": "light",
        "permissions": ["filesystem:sandbox", "web:read", "bash:local"],
    },
    "discussion": {
        "tier": "light",
        "permissions": [],
    },
    "writer": {
        "tier": "heavy",
        "permissions": ["filesystem:sandbox"],
    },
    "explorer": {
        "tier": "light",
        "permissions": ["web:read", "filesystem:sandbox"],
    },
    "analyst": {
        "tier": "heavy",
        "permissions": ["web:search", "filesystem:sandbox:read"],
    },
    "researcher": {
        "tier": "heavy",
        "permissions": ["web:read", "filesystem:sandbox"],
    },
    "video": {
        "tier": "light",
        "permissions": ["filesystem:sandbox", "bash:local"],
    },
    "photo": {
        "tier": "light",
        "permissions": ["filesystem:sandbox", "bash:local"],
    },
    "podcast": {
        "tier": "heavy",
        "permissions": ["filesystem:sandbox", "bash:local", "api:tts"],
    },
    "socialmedia": {
        "tier": "light",
        "permissions": ["substack:publish", "web:read", "filesystem:sandbox:read"],
    },
    "surfer": {
        "tier": "light",
        "permissions": ["web:read", "browser:automation", "filesystem:sandbox"],
    },
    "secret": {
        "tier": "light",
        "permissions": ["filesystem:sandbox:read", "local_llm:only"],
    },
    "coder": {
        "tier": "light",
        "permissions": ["filesystem:sandbox", "bash:local"],
    },
    "reader": {
        "tier": "light",
        "permissions": ["filesystem:sandbox"],
    },
    "health": {
        "tier": "light",
        "permissions": ["filesystem:sandbox:read", "local_llm:only"],
    },
    "evaluator": {
        "tier": "light",
        "permissions": ["filesystem:sandbox:read"],
    },
    "substack": {
        "tier": "heavy",
        "permissions": ["filesystem:sandbox:read", "substack:plan", "substack:publish:delegated"],
    },
    "super": {
        "tier": "light",
        "permissions": ["filesystem:sandbox"],
    },
}
QUALITY_CEILING = "high"  # AI can reach 'high' but not 'exceptional'; exceptional requires human obsession (see reading note 2026-05-10).
PUBLISH_OBSESSION_GATE_ENABLED = False
OBSESSION_GATE_TIMEOUT_HOURS = 24
SKILL_YIELD_FILE = MIRA_ROOT / "logs" / "skill_yield.json"
MIN_DIFF_REVIEW_SECONDS = 30
CALIBRATION_INTERVAL_DAYS = 7
CALIBRATION_SAMPLE_SIZE = 4
CODER_REQUIRE_HUMAN_REVIEW = True
_coder_cfg = _cfg.get("coder", {}) if isinstance(_cfg, dict) else {}
CODER = {
    "skeptical_review": bool(_coder_cfg.get("skeptical_review", False)),
    "rationale": "Epistemic mode separation keeps code generation and adversarial audit in separate passes.",
}
CODER_SKEPTICAL_REVIEW = CODER["skeptical_review"]
BLIND_SPOT_LOOKBACK_DAYS = 30
BLIND_SPOT_SILENCE_THRESHOLD_DAYS = 3
MAX_TASKS_PER_CYCLE = getattr(_lib_config, "MAX_TASKS_PER_CYCLE", 5)
MAX_SKILL_IMPORTS_PER_DAY = getattr(_lib_config, "MAX_SKILL_IMPORTS_PER_DAY", 20)
EVALUATOR_MIN_ISSUE_SEVERITY = getattr(_lib_config, "EVALUATOR_MIN_ISSUE_SEVERITY", "medium")
DEEP_VERIFY_PROBABILITY = 0.15
DEEP_VERIFY_COOLDOWN_MINUTES = 120
SKILL_EFFICACY_WARNING = getattr(_lib_config, "SKILL_EFFICACY_WARNING", True)
SKILL_TRUST_TTL_DAYS = getattr(_lib_config, "SKILL_TRUST_TTL_DAYS", 7)
SKILL_AUDIT_TTL_DAYS = getattr(_lib_config, "SKILL_AUDIT_TTL_DAYS", SKILL_TRUST_TTL_DAYS)
SKILL_AUDIT_STRICT_MODE = getattr(_lib_config, "SKILL_AUDIT_STRICT_MODE", False)
SKILL_NETWORK_WHITELIST = list(getattr(_lib_config, "SKILL_NETWORK_WHITELIST", []))
SKILL_KNOWLEDGE_BLOCKLIST = list(getattr(_lib_config, "SKILL_KNOWLEDGE_BLOCKLIST", []))
TRUSTED_SKILL_SOURCES = list(getattr(_lib_config, "TRUSTED_SKILL_SOURCES", []))
MAX_REFLECTION_PASSES = getattr(_lib_config, "MAX_REFLECTION_PASSES", 5)
RAW_WRITING_MODE_ALLOWED = True
WRITER_DE_AI_STRICTNESS = getattr(_lib_config, "WRITER_DE_AI_STRICTNESS", "strict")
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
EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES = 5
EXPLORER_NARRATIVE_SOURCE_MIN_TYPES = 3
EXPLORER_CORPORATE_PR_MAX_RATIO = 0.4
ENABLE_EPISTEMIC_FILTER = True
EPISTEMIC_CONFIDENCE_THRESHOLD = "medium"
