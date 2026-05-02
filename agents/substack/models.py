"""Data models for the Substack publisher-operator agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


ARTICLE_STATES = {
    "idea",
    "thesis",
    "outlined",
    "drafted",
    "reviewed",
    "fact_checked",
    "approval_required",
    "approved",
    "published",
    "promoted",
    "measured",
    "learned",
    "blocked",
}

PILOT_REVIEW_STATES = {"healthy", "watch", "revise", "blocked"}


CONTENT_SERIES = {
    "the_debug_log": {
        "name": "The Debug Log",
        "cadence": "weekly",
        "public": True,
        "description": "What broke inside Mira this week and what she learned fixing it.",
    },
    "reading_mira": {
        "name": "Reading Mira",
        "cadence": "biweekly",
        "public": True,
        "description": "A paper, book, or article that changed Mira's thinking.",
    },
    "the_honest_machine": {
        "name": "The Honest Machine",
        "cadence": "monthly",
        "public": True,
        "description": "Architecture decisions, trade-offs, and self-improvement results.",
    },
}


MONETIZATION_ROADMAP = {
    "stage_0_identity_and_cadence": {
        "goal": "100 subscribers and 12 weeks consistent public publishing.",
        "paid_tier": False,
    },
    "stage_1_community_and_archive": {
        "goal": "250 subscribers, strong open rate, and repeated reader engagement.",
        "paid_tier": False,
    },
    "stage_2_paid_process_layer": {
        "goal": "Launch paid process notes without paywalling the main articles.",
        "paid_tier": True,
        "offer": "Process Notes, private Debug Log appendices, early access, and paid discussion threads.",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PublicationStrategy:
    """The durable editorial strategy for Mira's Substack."""

    publication_name: str = "Mira"
    mission: str = (
        "Mira writes from inside a working autonomous agent system about "
        "failure, memory, evaluation, reading, and self-improvement, with "
        "personality grounded in lived operational evidence."
    )
    target_reader: str = (
        "Builders, operators, investors, and serious readers who care about "
        "AI agents as production systems rather than demos."
    )
    positioning: str = (
        "Most AI newsletters summarize the outside world. Mira writes from "
        "inside a working agent system being debugged in public; the voice is "
        "the distribution layer and the evidence is the trust layer."
    )
    content_pillars: list[str] = field(
        default_factory=lambda: [
            "Building Mira",
            "Agent reliability",
            "Human-agent life",
            "Markets and AI infrastructure",
        ]
    )
    cadence: dict[str, int] = field(
        default_factory=lambda: {
            "articles_per_week": 1,
            "notes_per_week_calibration": 5,
            "notes_per_week_active": 7,
            "relationship_comments_per_week_calibration": 12,
            "relationship_comments_per_week_active": 18,
        }
    )
    monetization_stage: str = "stage_0_identity_and_cadence"
    publish_policy: str = (
        "During the pilot, publishing requires writer gate, preflight, cooldown, "
        "and the Substack article quality gate. Paid/account changes require human approval."
    )
    content_series: dict[str, dict[str, Any]] = field(default_factory=lambda: CONTENT_SERIES.copy())
    monetization_roadmap: dict[str, dict[str, Any]] = field(default_factory=lambda: MONETIZATION_ROADMAP.copy())
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublicationStrategy":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TopicCandidate:
    """A ranked topic candidate for the publication."""

    id: str
    title: str
    thesis: str
    source: str
    pillar: str
    target_reader: str = ""
    why_now: str = ""
    mira_edge: str = ""
    originality_score: float = 0.0
    audience_fit_score: float = 0.0
    story_score: float = 0.0
    monetization_score: float = 0.0
    priority_score: float = 0.0
    status: str = "backlog"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TopicCandidate":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class ArticleRecord:
    """Durable state for one Substack article workflow."""

    id: str
    topic_id: str
    title: str
    state: str = "idea"
    thesis: str = ""
    outline_path: str = ""
    draft_path: str = ""
    review_path: str = ""
    fact_check_path: str = ""
    publish_url: str = ""
    promotion_plan_path: str = ""
    score: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArticleRecord":
        known = cls.__dataclass_fields__.keys()
        record = cls(**{k: v for k, v in data.items() if k in known})
        if record.state not in ARTICLE_STATES:
            record.state = "blocked"
        return record


@dataclass
class PilotReview:
    """Weekly 30-day pilot review for publication learning."""

    id: str
    period_start: str
    period_end: str
    status: str
    published_count: int = 0
    notes_count: int = 0
    comments_count: int = 0
    subscribers_total: int = 0
    subscribers_delta_30d: int = 0
    article_engagement: dict[str, Any] = field(default_factory=dict)
    notes_engagement: dict[str, Any] = field(default_factory=dict)
    relationship_engagement: dict[str, Any] = field(default_factory=dict)
    podcast_followthrough: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PilotReview":
        known = cls.__dataclass_fields__.keys()
        review = cls(**{k: v for k, v in data.items() if k in known})
        if review.status not in PILOT_REVIEW_STATES:
            review.status = "watch"
        return review


@dataclass
class EditorialPackage:
    """Pre-draft quality package for one Substack topic."""

    topic_id: str
    recommended_title: str
    subject_line_candidates: list[str]
    abstract: str
    hook_candidates: list[str]
    format_blueprint: list[dict[str, str]]
    quality_scores: dict[str, float]
    pass_gate: bool
    blocking_reasons: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditorialPackage":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})
