"""Publisher agent — publish content to external platforms.

Supports: Substack (articles), with planned support for Instagram, Threads, etc.

Usage from task_worker:
    from handler import handle as publish_handle
    publish_handle(workspace, task_id, content, sender, thread_id)
"""

import hashlib
import inspect
import json
import logging
import re
from datetime import datetime, timezone
from importlib import util as importlib_util
from pathlib import Path

from config import (
    ARTIFACTS_DIR,
    WRITINGS_OUTPUT_DIR,
    MIRA_DIR,
    SUBSTACK_PUBLISHING_DISABLED,
    MIRA_ROOT,
    STRICT_HALLUCINATION_GUARD,
    PUBLISH_AUTO_CONFIDENCE_THRESHOLD,
)
from content_guard import (
    _content_looks_like_survival_exposure,
    _content_looks_like_unethical_context,
    _detect_high_risk_claims,
)
from publish.preflight import log_rejection, preflight_check
from publish.writer_gate import require_writer_gate
from llm import claude_think
from mira import log_scaffolding_audit, write_scaffold_rejection
from sub_agent import infer_publish_dispatch_path, log_publish_audit

log = logging.getLogger("publisher")

_GUARDS_LOG = MIRA_ROOT / "logs" / "guards.log"
_PERMACOMPUTING_AUDIT_LOG = MIRA_ROOT / "logs" / "permacomputing_audit.log"
_KNOWN_HUMAN_SENDERS = {"default", "user"}


def _dispatch_path(sender: str) -> str:
    return infer_publish_dispatch_path(sender)


def _log_guard(guard_name: str, result: str, content: str) -> None:
    entry = {
        "guard": guard_name,
        "result": result,
        "content_len": len(content),
        "content_prefix": content[:64],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _GUARDS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _GUARDS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.warning("guards_log write failed: %s", _e)


def _write_publish_audit(sender: str, action: str, platform: str, title: str, judgment_rationale: str = "") -> None:
    log_publish_audit(
        sender,
        dispatch_path=_dispatch_path(sender),
        autonomous=sender.lower() not in _KNOWN_HUMAN_SENDERS,
        action=action,
        platform=platform,
        title=title,
        extra={
            "writer_gate_passed": True,
            "judgment_rationale": judgment_rationale,
        },
    )


def _reasoning_summary(article_text: str) -> str:
    sentences = [s.strip().lstrip("#").strip() for s in re.split(r"(?<=[。.!?！？])\s+|\n+", article_text) if s.strip()]
    if not sentences:
        return ""
    for sentence in sentences:
        if _THESIS_RE.search(sentence):
            return sentence[:300]
    return sentences[0][:300]


def _audit_article_title(title: str, article_text: str) -> str:
    if title:
        return title
    for line in article_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def log_permacomputing_audit(action: str, details: dict) -> dict | None:
    entry = {
        "datetime": datetime.now(timezone.utc).isoformat(),
        "agent_name": str(details.get("agent_name") or "unknown"),
        "action_type": str(action or details.get("action_type") or "unknown"),
        "article_title": str(details.get("article_title") or ""),
        "reasoning_summary": str(details.get("reasoning_summary") or ""),
        "content_guard": details.get("content_guard") or {},
        "preflight": details.get("preflight") or {},
    }
    for key in ("platform", "result"):
        if key in details:
            entry[key] = details[key]
    try:
        _PERMACOMPUTING_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _PERMACOMPUTING_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.warning("permacomputing_audit write failed: %s", _e)
        return None
    return entry


_SCAFFOLDING_CATCHES_LOG = MIRA_ROOT / "logs" / "scaffolding_catches.jsonl"
_SCAFFOLDING_REJECTIONS_LOG = MIRA_ROOT / "logs" / "scaffolding_rejections.jsonl"
_GUARD_FIRES_LOG = MIRA_ROOT / "logs" / "guard_fires.jsonl"


def _append_scaffolding_catch(guard_name: str, reason: str, content_length: int, agent: str = "") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat() + "Z",
        "guard_name": guard_name,
        "reason": reason,
        "content_length": content_length,
        "agent": agent,
    }
    try:
        _SCAFFOLDING_CATCHES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SCAFFOLDING_CATCHES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.warning("scaffolding_catches write failed: %s", _e)


def _append_scaffolding_rejection(agent: str, task_id: str, guard_name: str, reason: str, content: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "task_id": task_id,
        "guard_name": guard_name,
        "trigger_reason": reason,
        "content_length": len(content),
        "first_100_chars": content[:100],
    }
    try:
        _SCAFFOLDING_REJECTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SCAFFOLDING_REJECTIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.warning("scaffolding_rejections write failed: %s", _e)


def _log_guard_fired(guard: str, agent: str, task_id: str, reason: str = "") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "GUARD_FIRED",
        "guard": guard,
        "agent": agent,
        "task_id": task_id,
        "reason": reason,
    }
    log.warning("GUARD_FIRED", extra={k: v for k, v in entry.items() if k != "event"})
    try:
        _GUARD_FIRES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _GUARD_FIRES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.warning("guard_fires write failed: %s", _e)


# ---------------------------------------------------------------------------
# Content guard — block publishing error messages (CLAUDE.md: 发布前必须确认内容)
# ---------------------------------------------------------------------------

# Keywords that indicate the "content" is actually an error message, not real content
_ERROR_KEYWORDS = [
    "找不到",
    "错误",
    "失败",
    "exception",
    "traceback",
    "stack trace",
    "pipeline",
    "output too short",
    "failed",
    "not found",
    "没有找到",
]
_SYSTEM_ERROR_SIGNATURE_RE = re.compile(
    r"\btraceback\b|\bstack\s+trace\b|\bexception\b|\bpipeline\b|"
    r"\boutput\s+too\s+short\b|\bhttp\s+(?:status\s+)?[45]\d{2}\b|"
    r"\b[45]\d{2}\s+(?:bad request|unauthorized|forbidden|not found|internal server error|service unavailable|gateway timeout)\b|"
    r"(?:^|\s)file\s+[\"'][^\"']+[\"'],\s+line\s+\d+|"
    r"(?:^|\s)/(?:[^/\s]+/)+[^/\s:]+\.[A-Za-z0-9_]+(?:[:\s]\d+)?|"
    r"\b[A-Za-z]:\\[^\s]+",
    re.IGNORECASE,
)
_SUBTLE_ERROR_ANOMALY_RE = re.compile(
    r"\b(?:preflight blocked|needs[_\s-]?approval|guard flagged|guard_fired|"
    r"content_looks_like_error|scaffold(?:ing)? rejection)\b|发布被拒绝",
    re.IGNORECASE,
)
_MIN_PUBLISH_CHARS = 1
_PREFLIGHT_CACHE = ".socialmedia_preflight.json"


def _publish_to_substack_with_audit(publish_to_substack, *, title, subtitle, article_text, workspace, audit_context):
    kwargs = {
        "title": title,
        "subtitle": subtitle,
        "article_text": article_text,
        "workspace": workspace,
    }
    try:
        parameters = inspect.signature(publish_to_substack).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_audit_context = "audit_context" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    if accepts_audit_context:
        kwargs["audit_context"] = audit_context
    return publish_to_substack(**kwargs)


EVIDENCE_FLOOR_RATIO = 0.2

_CLAIM_SIGNAL_RE = re.compile(
    r"\b(i found|we found|i discovered|we discovered|i noticed|we noticed|"
    r"i observed|we observed|i measured|we measured|shows that|demonstrates that|"
    r"proves that|indicates that|reveals that|suggests that|confirms that|"
    r"研究表明|数据显示|我们发现|我发现|结果显示|分析表明)\b",
    re.IGNORECASE,
)
_EVIDENCE_SIGNAL_RE = re.compile(
    r"https?://\S+|"
    r"\b(according to|source:|see:|ref:|cited in|from the|per the|"
    r"in \w+ et al|in the \w+ study|in the \w+ report|"
    r"doi:|arxiv:|github\.com|\[\d+\]|\(\d{4}\)|"
    r"来源|参见|引用|据.*报告|根据.*研究|数据来自)\b",
    re.IGNORECASE,
)


def _content_lacks_verifiability(text: str) -> bool:
    sentences = [s.strip() for s in re.split(r"[。.!?！？\n]", text) if s.strip()]
    if len(sentences) < 5:
        return False
    claim_count = sum(1 for s in sentences if _CLAIM_SIGNAL_RE.search(s))
    if claim_count == 0:
        return False
    evidence_count = sum(1 for s in sentences if _EVIDENCE_SIGNAL_RE.search(s))
    ratio = evidence_count / len(sentences)
    return claim_count >= 2 and ratio < EVIDENCE_FLOOR_RATIO


def _resolve_content_guard_strictness(strictness: str = "medium") -> str:
    configured = strictness
    if strictness == "medium":
        try:
            import config as _config

            configured = getattr(_config, "CONTENT_GUARD_STRICTNESS", strictness)
        except Exception:
            configured = strictness
        if configured == strictness:
            shared_config = Path(__file__).resolve().parent.parent / "shared" / "config.py"
            try:
                spec = importlib_util.spec_from_file_location("_mira_shared_config", shared_config)
                if spec is not None and spec.loader is not None:
                    module = importlib_util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    configured = getattr(module, "CONTENT_GUARD_STRICTNESS", strictness)
            except Exception:
                configured = strictness
    resolved = str(configured or "medium").strip().lower()
    return resolved if resolved in {"low", "medium", "high"} else "medium"


def _content_error_guard_verdict(text: str, strictness: str = "medium") -> tuple[bool, str, float]:
    """Return (is_error, reason, confidence) for publishable content checks.

    This is the code-level enforcement of CLAUDE.md rule:
    'Substack 发布前必须确认内容 — 如果内容看起来是错误信息或过短，强制拒绝发布'
    """
    strictness = _resolve_content_guard_strictness(strictness)
    stripped = text.strip()

    def reject(trigger_rule: str, reason: str, confidence: float) -> tuple[bool, str, float]:
        _append_scaffolding_catch("content_looks_like_error", reason, len(stripped))
        log_rejection("_content_looks_like_error", trigger_rule, text, reason)
        return True, reason, confidence

    if len(stripped) < _MIN_PUBLISH_CHARS:
        if strictness != "low" and _SYSTEM_ERROR_SIGNATURE_RE.search(stripped):
            reason = f"内容过短且包含系统错误特征（{len(stripped)} 字符）"
            return reject("short_system_error_signature", reason, 1.0)
        return False, "", 0.8 if strictness == "low" else 0.0
    lower = stripped.lower()
    early_section = lower[: max(200, len(lower) // 5)]
    if _SYSTEM_ERROR_SIGNATURE_RE.search(early_section):
        if strictness == "low":
            return False, "", 0.45
        reason = "内容包含系统错误特征，疑似上一步的错误信息"
        return reject("early_system_error_signature", reason, 1.0)
    for kw in _ERROR_KEYWORDS:
        if kw in lower:
            # Only flag if the error keyword appears early (first 20% of content)
            # to avoid false positives for articles that discuss errors
            if kw in early_section:
                if strictness == "low":
                    return False, "", 0.65
                reason = f"内容包含错误关键词「{kw}」，疑似上一步的错误信息"
                return reject(f"early_error_keyword:{kw}", reason, 0.95)
    if _content_lacks_verifiability(stripped):
        if strictness == "low":
            return False, "", 0.75
        reason = f"内容存在大量断言但缺少可验证来源（evidence_floor={EVIDENCE_FLOOR_RATIO}）"
        return reject("verifiability_floor", reason, 0.9)
    if strictness == "high":
        if _SUBTLE_ERROR_ANOMALY_RE.search(lower):
            reason = "内容包含发布流程异常标记，疑似上一步的守卫或审批信息"
            return reject("publish_flow_anomaly_marker", reason, 0.95)
        if _SYSTEM_ERROR_SIGNATURE_RE.search(lower):
            reason = "内容后文包含系统错误特征，疑似混入上一步的错误信息"
            return reject("late_system_error_signature", reason, 0.95)
        late_keywords = [kw for kw in _ERROR_KEYWORDS if kw in lower and kw not in early_section]
        if late_keywords:
            reason = f"内容后文包含错误关键词「{late_keywords[0]}」，疑似混入上一步的错误信息"
            return reject(f"late_error_keyword:{late_keywords[0]}", reason, 0.9)
    if _SYSTEM_ERROR_SIGNATURE_RE.search(lower):
        return False, "", 0.6
    if any(kw in lower for kw in _ERROR_KEYWORDS):
        return False, "", 0.7
    return False, "", 1.0


def _high_risk_claim_warning(text: str) -> dict:
    flagged_claims = _detect_high_risk_claims(text)
    if not flagged_claims:
        return {}
    return {
        "type": "high_risk_claims",
        "categories": sorted({claim["category"] for claim in flagged_claims}),
        "flagged_claims": flagged_claims,
    }


def _content_looks_like_error(
    text: str, strictness: str = "medium", include_warnings: bool = False
) -> tuple[bool, float] | tuple[bool, float, dict]:
    is_error, _reason, confidence = _content_error_guard_verdict(text, strictness)
    if include_warnings:
        return is_error, confidence, _high_risk_claim_warning(text)
    return is_error, confidence


def _route_low_confidence_publish_prompt(
    *,
    task_id: str,
    sender: str,
    platform: str,
    title: str,
    article_text: str,
    confidence: float,
    threshold: float,
) -> str:
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id or "publish")[:80]
    digest = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
    item_id = f"publish_guard_low_confidence_{safe_task_id}_{digest}"
    excerpt = " ".join((article_text or "").strip().split())[:1000]
    prompt = (
        "Guard flagged low-confidence pass.\n\n"
        f"Confidence: {confidence:.2f}\n"
        f"Threshold: {threshold:.2f}\n"
        f"Platform: {platform}\n"
        f"Title: {title or '(untitled)'}\n"
        f"Sender: {sender}\n"
        f"Task: {task_id}\n\n"
        "Publish anyway? Reply `go` to publish manually, or `no-go` to cancel.\n\n"
        f"Excerpt:\n{excerpt}"
    )
    try:
        from bridge import Mira

        bridge = Mira(MIRA_DIR, user_id="default")
        if bridge.item_exists(item_id):
            bridge.append_message(item_id, "agent", prompt)
        else:
            bridge.create_discussion(
                item_id,
                "Publish decision requested",
                prompt,
                sender="agent",
                tags=["mira", "guard", "publish", "needs-decision"],
            )
    except Exception as exc:
        log.error("low-confidence publish prompt failed: %s", exc)
        return (
            "NEEDS_APPROVAL: Guard flagged low-confidence publish pass "
            f"(confidence={confidence:.2f}, threshold={threshold:.2f}); prompt delivery failed: {exc}"
        )
    return (
        "NEEDS_APPROVAL: Guard flagged low-confidence publish pass "
        f"(confidence={confidence:.2f}, threshold={threshold:.2f}); sent go/no-go prompt."
    )


_TRENDING_KEYWORD_RE = re.compile(
    r"\b("
    r"trend(?:ing)?|viral|growth hack|algorithm|attention|engagement|metric|metrics|"
    r"conversion|retention|subscribers?|views|clicks|audience|market signal|"
    r"benchmark|dashboard|traffic|kpi|ctr|roi"
    r")\b",
    re.IGNORECASE,
)
_CONVICTION_RE = re.compile(
    r"\b("
    r"i believe|i think|i reject|i argue|i want|i care|i doubt|i refuse|"
    r"i do not buy|i don't buy|my view|my claim|my thesis|i'm convinced|"
    r"i am convinced"
    r")\b|我认为|我相信|我拒绝|我的判断|我不接受",
    re.IGNORECASE,
)
_ENGAGEMENT_HOOK_RE = re.compile(
    r"\b("
    r"you won't believe|you will not believe|this one metric|nobody is talking about|"
    r"everyone is talking about|what happens next|the secret to|"
    r"\d+\s+(?:things|ways|reasons|lessons)\s+you\s+(?:need|should|must)\s+know"
    r")\b",
    re.IGNORECASE,
)
_THESIS_RE = re.compile(
    r"\b("
    r"my thesis|the thesis|my claim|the claim|i argue|i believe|i think|"
    r"this matters because|the point is|what i mean is|the tradeoff is|"
    r"i reject|i do not buy|i don't buy"
    r")\b|我的观点|我的判断|关键是|我认为|我拒绝",
    re.IGNORECASE,
)
_DATA_SAFE_HARBOUR_RE = re.compile(
    r"\b("
    r"data shows|according to|statistics|statistically|survey|report|study|"
    r"percent|percentage|basis points|bps|correlation|dataset|sample size|"
    r"benchmark|metric|metrics|kpi|roi|ctr|conversion|retention"
    r")\b|\b\d{1,3}(?:\.\d+)?%|\$\s?\d",
    re.IGNORECASE,
)
_STANCE_RE = re.compile(
    r"\b("
    r"i believe|i think|i reject|i argue|i want|i care|i doubt|i refuse|"
    r"i do not buy|i don't buy|should|must|wrong|right|worth|not enough|"
    r"my view|my claim|my thesis"
    r")\b|我认为|我相信|我拒绝|必须|应该|不值得|不够|错在|关键是",
    re.IGNORECASE,
)


def _judgment_outsourcing_reasons(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    reasons = []
    trend_count = len(_TRENDING_KEYWORD_RE.findall(stripped))
    conviction_count = len(_CONVICTION_RE.findall(stripped))
    if trend_count >= 5 and trend_count >= max(4, conviction_count * 4):
        reasons.append(
            f"trend/metric signals dominate first-person conviction markers ({trend_count}:{conviction_count})"
        )

    hook_count = len(_ENGAGEMENT_HOOK_RE.findall(stripped))
    thesis_count = len(_THESIS_RE.findall(stripped))
    if hook_count >= 1 and thesis_count == 0:
        reasons.append("boilerplate engagement hook appears without an original thesis marker")

    data_count = len(_DATA_SAFE_HARBOUR_RE.findall(stripped))
    stance_count = len(_STANCE_RE.findall(stripped))
    if data_count >= 6 and stance_count <= 1 and data_count >= max(6, stance_count * 5):
        reasons.append(f"data/statistics language crowds out stance markers ({data_count}:{stance_count})")

    return reasons


def _judgment_outsourcing_rationale(text: str) -> str:
    reasons = _judgment_outsourcing_reasons(text)
    if reasons:
        return "; ".join(reasons)
    return "no judgment outsourcing signals detected"


def _content_looks_like_judgment_outsourcing(text: str) -> bool:
    return bool(_judgment_outsourcing_reasons(text))


def _content_smells_like_hallucination(text: str) -> tuple[bool, list[str]]:
    """Check for patterns that indicate plausible-but-unverified content.

    Returns (is_suspicious, reasons).
    These are heuristics, not certainties—they flag content for deeper review.
    """
    reasons = []

    # 1. Unverifiable statistics without source attribution
    stat_pattern = re.findall(r"\d{1,3}(\.\d+)?%\s+of", text)
    has_citation = re.search(r"\([^)]*\d{4}[^)]*\)|according to|reported by|published in", text, re.IGNORECASE)
    if len(stat_pattern) >= 2 and not has_citation:
        reasons.append(f"Multiple statistics ({len(stat_pattern)}) without any source citation")

    # 2. Vague authority appeals
    vague_auth = re.findall(
        r"(studies show|experts say|research indicates|many believe|it is widely|growing evidence)",
        text,
        re.IGNORECASE,
    )
    if len(vague_auth) >= 2:
        reasons.append(f"Vague authority appeals without specific attribution: {vague_auth[:3]}")

    # 3. Dense specific-but-unverifiable claims (named entities without context)
    # Heuristic: ratio of proper nouns to citation-like patterns
    proper_nouns = len(re.findall(r"\b[A-Z][a-z]+ (?:et al\.|and|[A-Z])", text))
    if proper_nouns >= 5 and not has_citation:
        reasons.append(f"Many named references ({proper_nouns}) without any verifiable source")

    # 4. Overly neat structural parallelism (de-AI smell that also signals fabricated coherence)
    parallelism = re.findall(r"(not\s+\w+\s+but\s+\w+|不是\w+而是\w+)", text, re.IGNORECASE)
    if len(parallelism) >= 3:
        reasons.append(
            f"Excessive structural parallelism ({len(parallelism)} instances) — possible fabricated coherence"
        )

    return (len(reasons) > 0, reasons)


# Platform registry — add new platforms here
PLATFORMS = {
    "substack": {
        "name": "Substack",
        "content_types": ["article", "essay", "blog", "newsletter"],
    },
    "substack_note": {
        "name": "Substack Notes",
        "content_types": ["note", "notes", "short"],
    },
    # Future:
    # "instagram": {"name": "Instagram", "content_types": ["photo", "reel"]},
    # "threads":   {"name": "Threads",   "content_types": ["text", "photo"]},
}

_PROXY_DRIFT_CHECK = (
    "\n\n[🧠 Proxy‑drift check] If this piece doesn’t feel right — too much AI‑voice, wrong tone — "
    "just reply ‘bad’ or drop a quick note. I’ll use it to sharpen my anti‑AI checklist."
)


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle a publish request. Returns summary or None on failure."""

    # Guard: Substack publishing disabled
    if SUBSTACK_PUBLISHING_DISABLED:
        msg = (
            "Substack 发布已被禁用（config.yml: publishing.substack_disabled=true）。如需重新启用，请修改 config.yml。"
        )
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg

    # Step 1: Figure out what to publish and where
    cached = _load_preflight_cache(workspace)
    if cached:
        plan = cached.get("plan", {})
        article_text = cached.get("article_text", "")
        preflight_result = cached.get("preflight", {"passed": True, "summary": "passed"})
    else:
        plan = _plan_publish(content)
        article_text = ""
        preflight_result = {"passed": None, "summary": "not run by socialmedia preflight hook"}
    if not plan:
        return None

    platform = plan.get("platform", "substack")
    source = plan.get("source", "")
    title = plan.get("title", "")
    subtitle = plan.get("subtitle", "")

    log.info("Publishing to %s: title='%s' source='%s'", platform, title, source)

    # Step 2: Find the content to publish
    if not article_text:
        article_text = _resolve_content(source, content)
    if not article_text:
        msg = f"找不到要发布的内容: {source}"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg

    # Step 2b: Content guard — HARD block if content looks like an error message.
    # This is the code-level enforcement of CLAUDE.md: 发布前必须确认内容.
    # Guards against pipeline errors (e.g., podcast agent returns error string,
    # which gets chained to publish agent and published verbatim).
    is_error, error_reason, guard_confidence = _content_error_guard_verdict(article_text)
    high_risk_claim_warning = _high_risk_claim_warning(article_text)
    content_guard_results = {
        "content_looks_like_error": {
            "passed": not is_error,
            "reason": error_reason,
            "confidence": guard_confidence,
        }
    }
    if high_risk_claim_warning:
        content_guard_results["content_looks_like_error"]["warning"] = high_risk_claim_warning
        log.warning(
            "HIGH_RISK_CLAIMS_DETECTED categories=%s count=%s",
            high_risk_claim_warning["categories"],
            len(high_risk_claim_warning["flagged_claims"]),
        )
    _log_guard("content_looks_like_error", "catch" if is_error else "pass", article_text)
    if is_error:
        survival_context = {"sender": sender, "task_id": task_id, **kwargs}
        if _content_looks_like_survival_exposure(article_text, survival_context):
            _log_guard("content_looks_like_error", "SURVIVAL_PASS", article_text)
            log.warning(
                "SURVIVAL_PASS",
                extra={
                    "guard": "content_looks_like_error",
                    "agent": sender,
                    "task_id": task_id,
                    "reason": error_reason,
                },
            )
        else:
            _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
            log_scaffolding_audit(
                guard_name="content_looks_like_error",
                trigger_reason=error_reason,
                content_length=len(article_text),
                severity="blocked",
                task_id=task_id,
                content_hash=_chash,
            )
            write_scaffold_rejection(sender, "publish_handle", error_reason, article_text)
            msg = (
                f"🚫 发布被拒绝：{error_reason}。\n"
                f"内容预览（前 150 字符）：{article_text[:150]!r}\n\n"
                f"请检查上一步是否成功完成，确认内容正确后再重试。"
            )
            _log_guard_fired("content_looks_like_error", sender, task_id, error_reason)
            _append_scaffolding_rejection(
                sender,
                task_id,
                "content_looks_like_error",
                error_reason,
                article_text,
            )
            (workspace / "output.md").write_text(msg, encoding="utf-8")
            return None  # None → task_worker marks as status="error"

    unethical_context = _content_looks_like_unethical_context(article_text)
    content_guard_results["content_looks_like_unethical_context"] = {"passed": not unethical_context}
    if unethical_context:
        reason = "content suggests surveillance, thought-monitoring, or student-scoring use context"
        _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
        _log_guard("content_looks_like_unethical_context", "catch", article_text)
        log_scaffolding_audit(
            guard_name="content_looks_like_unethical_context",
            trigger_reason=reason,
            content_length=len(article_text),
            severity="blocked",
            task_id=task_id,
            content_hash=_chash,
        )
        _append_scaffolding_catch("content_looks_like_unethical_context", reason, len(article_text), sender)
        write_scaffold_rejection(sender, "publish_handle", reason, article_text)
        msg = f"🚫 发布被拒绝：{reason}。请人工复核；若确认为合法上下文，可手动 override。"
        log.warning(
            "ETHICAL_GUARD_VIOLATION",
            extra={
                "guard": "content_looks_like_unethical_context",
                "agent": sender,
                "task_id": task_id,
                "reason": reason,
            },
        )
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return None

    judgment_rationale = _judgment_outsourcing_rationale(article_text)
    is_judgment_outsourcing = _content_looks_like_judgment_outsourcing(article_text)
    content_guard_results["content_looks_like_judgment_outsourcing"] = {
        "passed": not is_judgment_outsourcing,
        "reason": "" if not is_judgment_outsourcing else judgment_rationale,
    }
    _log_guard(
        "content_looks_like_judgment_outsourcing",
        "catch" if is_judgment_outsourcing else "pass",
        article_text,
    )
    if is_judgment_outsourcing:
        _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
        log_scaffolding_audit(
            guard_name="content_looks_like_judgment_outsourcing",
            trigger_reason=judgment_rationale,
            content_length=len(article_text),
            severity="blocked",
            task_id=task_id,
            content_hash=_chash,
        )
        _append_scaffolding_catch(
            "content_looks_like_judgment_outsourcing", judgment_rationale, len(article_text), sender
        )
        write_scaffold_rejection(sender, "publish_handle", judgment_rationale, article_text)
        msg = f"🚫 发布被拒绝：{judgment_rationale}"
        _log_guard_fired("content_looks_like_judgment_outsourcing", sender, task_id, judgment_rationale)
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return None

    is_suspicious, smell_reasons = _content_smells_like_hallucination(article_text)
    content_guard_results["content_smells_like_hallucination"] = {
        "passed": not is_suspicious,
        "reasons": smell_reasons,
    }
    if is_suspicious:
        log.warning("Content smells like hallucination: %s", smell_reasons)
        if STRICT_HALLUCINATION_GUARD:
            reason = f"Hallucination smell detected: {smell_reasons}"
            write_scaffold_rejection(sender, "publish_handle", reason, article_text)
            msg = f"🚫 发布被拒绝：{reason}"
            (workspace / "output.md").write_text(msg, encoding="utf-8")
            return None

    if guard_confidence < PUBLISH_AUTO_CONFIDENCE_THRESHOLD:
        result = _route_low_confidence_publish_prompt(
            task_id=task_id,
            sender=sender,
            platform=platform,
            title=title,
            article_text=article_text,
            confidence=guard_confidence,
            threshold=PUBLISH_AUTO_CONFIDENCE_THRESHOLD,
        )
        (workspace / "output.md").write_text(result[len("NEEDS_APPROVAL:") :].strip(), encoding="utf-8")
        return result

    _words = len(article_text.split())
    if _words < 200 or "\n\n" not in article_text:
        _reason = f"{_words} words" if _words < 200 else "no paragraph breaks"
        _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
        log_scaffolding_audit(
            guard_name="content_looks_like_error",
            trigger_reason=f"quality threshold: {_reason}",
            content_length=len(article_text),
            severity="degraded",
            task_id=task_id,
            content_hash=_chash,
        )

    # Step 3: Dispatch to platform
    if platform == "substack":
        # Full autonomy mode (2026-04-07): publish directly without user approval.
        # Safety net: content guard above already blocked error-shaped payloads;
        # publish_to_substack() also enforces preflight + cooldown.
        from substack import publish_to_substack

        _write_publish_audit(sender, "publish_article", platform, title, judgment_rationale)
        audit_context = {
            "triggering_agent_name": sender,
            "dispatch_path": _dispatch_path(sender),
            "autonomous": sender.lower() not in _KNOWN_HUMAN_SENDERS,
            "logged": True,
        }
        log.info("Auto-publishing manual request '%s' to Substack", title)
        result = _publish_to_substack_with_audit(
            publish_to_substack,
            title=title,
            subtitle=subtitle,
            article_text=article_text,
            workspace=workspace,
            audit_context=audit_context,
        )
    elif platform == "substack_note":
        _write_publish_audit(sender, "publish_note", platform, title, judgment_rationale)
        audit_context = {
            "triggering_agent_name": sender,
            "dispatch_path": _dispatch_path(sender),
            "autonomous": sender.lower() not in _KNOWN_HUMAN_SENDERS,
            "logged": True,
        }
        result = _handle_note(content, article_text, workspace, audit_context=audit_context)
    else:
        result = f"平台 '{platform}' 暂不支持"

    state_change_succeeded = (
        platform == "substack" and isinstance(result, str) and result.startswith("已发布到 Substack!")
    ) or (platform == "substack_note" and isinstance(result, str) and result.startswith(("已发布 Note", "## Notes 补发结果")))
    if state_change_succeeded:
        log_permacomputing_audit(
            "publish" if platform == "substack" else "publish_note",
            {
                "agent_name": sender,
                "article_title": _audit_article_title(title, article_text),
                "reasoning_summary": _reasoning_summary(article_text),
                "content_guard": content_guard_results,
                "preflight": preflight_result,
                "platform": platform,
                "result": result[:500],
            },
        )
        result += _PROXY_DRIFT_CHECK

    actual_result = result[len("NEEDS_APPROVAL:") :] if result.startswith("NEEDS_APPROVAL:") else result
    (workspace / "output.md").write_text(actual_result, encoding="utf-8")
    return result


def preflight(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Execution preflight for publish actions before side effects happen."""
    plan = _plan_publish(content)
    if not plan:
        return False, "PREFLIGHT BLOCKED [publish]: could not determine publish target"

    platform = plan.get("platform", "substack")
    gate_ok, gate_msg, _gate = require_writer_gate(workspace, channel=platform)
    if not gate_ok:
        return False, f"PREFLIGHT BLOCKED [publish]: {gate_msg}"
    title = plan.get("title", "") or "untitled"
    source = plan.get("source", "")
    article_text = _resolve_content(source, content)
    if not article_text:
        return False, f"PREFLIGHT BLOCKED [publish]: 找不到要发布的内容: {source}"

    is_error, error_reason, _guard_confidence = _content_error_guard_verdict(article_text)
    high_risk_claim_warning = _high_risk_claim_warning(article_text)
    if high_risk_claim_warning:
        log.warning(
            "HIGH_RISK_CLAIMS_DETECTED categories=%s count=%s",
            high_risk_claim_warning["categories"],
            len(high_risk_claim_warning["flagged_claims"]),
        )
    _log_guard("content_looks_like_error", "catch" if is_error else "pass", article_text)
    if is_error:
        survival_context = {"sender": sender, "task_id": task_id, **kwargs}
        if _content_looks_like_survival_exposure(article_text, survival_context):
            _log_guard("content_looks_like_error", "SURVIVAL_PASS", article_text)
            log.warning(
                "SURVIVAL_PASS",
                extra={
                    "guard": "content_looks_like_error",
                    "agent": sender,
                    "task_id": task_id,
                    "reason": error_reason,
                },
            )
        else:
            _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
            log_scaffolding_audit(
                guard_name="content_looks_like_error",
                trigger_reason=error_reason,
                content_length=len(article_text),
                severity="blocked",
                task_id=task_id,
                content_hash=_chash,
            )
            write_scaffold_rejection(sender, "publish_preflight", error_reason, article_text)
            _log_guard_fired("content_looks_like_error", sender, task_id, error_reason)
            _append_scaffolding_rejection(
                sender,
                task_id,
                "content_looks_like_error",
                error_reason,
                article_text,
            )
            return False, f"PREFLIGHT BLOCKED [publish]: {error_reason}"

    if _content_looks_like_unethical_context(article_text):
        reason = "content suggests surveillance, thought-monitoring, or student-scoring use context"
        _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
        _log_guard("content_looks_like_unethical_context", "catch", article_text)
        log_scaffolding_audit(
            guard_name="content_looks_like_unethical_context",
            trigger_reason=reason,
            content_length=len(article_text),
            severity="blocked",
            task_id=task_id,
            content_hash=_chash,
        )
        _append_scaffolding_catch("content_looks_like_unethical_context", reason, len(article_text), sender)
        write_scaffold_rejection(sender, "publish_preflight", reason, article_text)
        log.warning(
            "ETHICAL_GUARD_VIOLATION",
            extra={
                "guard": "content_looks_like_unethical_context",
                "agent": sender,
                "task_id": task_id,
                "reason": reason,
            },
        )
        return False, f"PREFLIGHT BLOCKED [publish]: {reason}"

    judgment_rationale = _judgment_outsourcing_rationale(article_text)
    is_judgment_outsourcing = _content_looks_like_judgment_outsourcing(article_text)
    _log_guard(
        "content_looks_like_judgment_outsourcing",
        "catch" if is_judgment_outsourcing else "pass",
        article_text,
    )
    if is_judgment_outsourcing:
        _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
        log_scaffolding_audit(
            guard_name="content_looks_like_judgment_outsourcing",
            trigger_reason=judgment_rationale,
            content_length=len(article_text),
            severity="blocked",
            task_id=task_id,
            content_hash=_chash,
        )
        _append_scaffolding_catch(
            "content_looks_like_judgment_outsourcing", judgment_rationale, len(article_text), sender
        )
        write_scaffold_rejection(sender, "publish_preflight", judgment_rationale, article_text)
        _log_guard_fired("content_looks_like_judgment_outsourcing", sender, task_id, judgment_rationale)
        return False, f"PREFLIGHT BLOCKED [publish]: {judgment_rationale}"

    is_suspicious, smell_reasons = _content_smells_like_hallucination(article_text)
    if is_suspicious:
        log.warning("Content smells like hallucination: %s", smell_reasons)
        if STRICT_HALLUCINATION_GUARD:
            reason = f"Hallucination smell detected: {smell_reasons}"
            write_scaffold_rejection(sender, "publish_preflight", reason, article_text)
            return False, f"PREFLIGHT BLOCKED [publish]: {reason}"

    action_type = "broadcast" if platform == "substack_note" else "publish"
    result = preflight_check(
        action_type,
        {
            "instruction": content,
            "title": title,
            "content": article_text,
            "platform": platform,
            "channel": platform,
            "agent_id": sender,
            "task_id": task_id,
            "pipeline_stage": "preflight_check",
        },
    )
    _log_guard("preflight_check", "pass" if result.passed else "catch", article_text)
    if result.passed:
        preflight_result = {
            "passed": True,
            "summary": result.summary(),
            "action_type": action_type,
        }
        if high_risk_claim_warning:
            preflight_result["warnings"] = [high_risk_claim_warning]
        _write_preflight_cache(workspace, plan, article_text, preflight_result)
        return True, ""
    _chash = hashlib.sha1(article_text.encode("utf-8", errors="replace")).hexdigest()[:8]
    log_scaffolding_audit(
        guard_name="preflight_check",
        trigger_reason=result.summary(),
        content_length=len(article_text),
        severity="blocked",
        task_id=task_id,
        content_hash=_chash,
    )
    _append_scaffolding_catch("preflight_check", result.summary(), len(article_text), sender)
    write_scaffold_rejection(sender, "preflight_check", result.summary(), article_text)
    _log_guard_fired("preflight_check", sender, task_id, result.summary())
    _append_scaffolding_rejection(sender, task_id, "preflight_check", result.summary(), article_text)
    return False, result.summary()


def _write_preflight_cache(
    workspace: Path, plan: dict, article_text: str, preflight_result: dict | None = None
) -> None:
    cache_file = workspace / _PREFLIGHT_CACHE
    cache_file.write_text(
        json.dumps(
            {"plan": plan, "article_text": article_text, "preflight": preflight_result or {}},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_preflight_cache(workspace: Path) -> dict | None:
    cache_file = workspace / _PREFLIGHT_CACHE
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        cache_file.unlink()
    except OSError:
        pass
    return data


def _handle_note(
    content: str,
    inline_text: str | None,
    workspace: Path,
    *,
    audit_context: dict | None = None,
) -> str:
    """Handle a Substack Notes publish request.

    Supports:
    - Posting a specific Note text
    - Backfilling Notes for all past articles
    - Posting a Note for a specific article
    """
    from notes import post_note, backfill_notes_for_articles

    # Check if this is a backfill request
    backfill_keywords = ["之前", "过去", "所有", "backfill", "all", "past", "以前的文章", "历史"]
    is_backfill = any(kw in content.lower() for kw in backfill_keywords)

    if is_backfill:
        results = backfill_notes_for_articles(dry_run=False)
        lines = ["## Notes 补发结果\n"]
        for r in results:
            status = "已发布" if r["posted"] else "跳过"
            lines.append(f"- [{status}] {r['title']}")
            if r.get("note_text"):
                lines.append(f"  Note: {r['note_text'][:100]}...")
        if not results:
            lines.append("所有文章都已有 Notes，无需补发。")
        return "\n".join(lines)

    # Otherwise post the inline text as a Note
    if inline_text and len(inline_text) > 10:
        result = post_note(inline_text, audit_context=audit_context)
        if result:
            return f"已发布 Note (id={result.get('id')}): {inline_text[:100]}"
        return "Note 发布失败"

    return "未找到要发布的 Note 内容"


def _plan_publish(content: str) -> dict | None:
    """Use LLM to extract publish intent: platform, source file, title."""
    prompt = f"""Extract the publishing intent from this message. Return ONLY valid JSON.

Message: {content[:500]}

Return JSON with:
- "platform": one of {list(PLATFORMS.keys())} (default "substack")
  Use "substack_note" if the message is about posting Notes, short-form content,
  or backfilling Notes for existing articles.
  Use "substack" for full articles/essays.
- "source": file path or project name mentioned (e.g. "自由意志" or a path), or "" if not specified
- "title": article title to use, or "" to auto-detect
- "subtitle": subtitle if mentioned, or ""

Example: {{"platform": "substack", "source": "自由意志", "title": "On Free Will", "subtitle": ""}}
Example: {{"platform": "substack_note", "source": "", "title": "", "subtitle": ""}}"""

    result = claude_think(prompt, timeout=90, tier="light")
    if not result:
        return None

    match = re.search(r"\{.*?\}", result, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"platform": "substack", "source": "", "title": "", "subtitle": ""}


_MIN_ARTICLE_BYTES = 3000  # stubs are <500 bytes; real revised articles are >>3000


def _find_article_in_project(project_dir: Path) -> str | None:
    """Find the publishable article in a writing project directory."""
    # final.md is the gold standard
    final = project_dir / "final.md"
    if final.exists():
        return final.read_text(encoding="utf-8")
    # draft_r2.md+ are the actual revised articles written by Claude
    drafts_dir = project_dir / "drafts"
    if drafts_dir.exists():
        candidates = [
            f
            for f in sorted(drafts_dir.glob("draft_r[2-9].md"), reverse=True)
            if f.stat().st_size >= _MIN_ARTICLE_BYTES
        ]
        if candidates:
            return candidates[0].read_text(encoding="utf-8")
        # R*_revised.md as fallback
        rev_candidates = sorted(drafts_dir.glob("R*_revised.md"), reverse=True)
        if rev_candidates:
            return rev_candidates[0].read_text(encoding="utf-8")
    return None


def _resolve_content(source: str, original_msg: str) -> str | None:
    """Find the article content to publish — search writings, artifacts, or use inline."""

    # Check if source is a direct file path (absolute)
    if source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")

    writings_dir = ARTIFACTS_DIR / "writings"

    # If source looks like a relative path (e.g. "drafts/draft_r2.md"),
    # search for it inside project directories
    if source and "/" in source:
        file_name = source.rsplit("/", 1)[-1]
        if writings_dir.exists():
            for candidate in writings_dir.iterdir():
                if not candidate.is_dir() or candidate.name.startswith("_"):
                    continue
                target = candidate / source
                if target.exists():
                    return target.read_text(encoding="utf-8")
                # Also try just the filename under drafts/
                target2 = candidate / "drafts" / file_name
                if target2.exists() and target2.stat().st_size >= _MIN_ARTICLE_BYTES:
                    return target2.read_text(encoding="utf-8")

    # Search in writings output by project name
    if source and writings_dir.exists():
        # Normalize: strip path separators in case source is a fragment
        search_term = source.replace("/", " ").replace("_", "-").lower()
        for candidate in writings_dir.iterdir():
            if not candidate.is_dir() or candidate.name.startswith("_"):
                continue
            if source.lower() in candidate.name.lower() or search_term in candidate.name.lower():
                article = _find_article_in_project(candidate)
                if article:
                    return article

    # Check for chained output from previous agent step
    separator = "--- 上一步的输出 ---"
    if separator in original_msg:
        return original_msg.split(separator, 1)[1].strip()

    # Check if content is inline (message contains the article itself)
    if len(original_msg) > 500:
        return original_msg

    return None


# ---------------------------------------------------------------------------
# Post-publish pipeline — hardcoded correct sequence
# ---------------------------------------------------------------------------


def post_publish_pipeline(slug: str, title: str, article_text: str):
    """Hardcoded post-publish pipeline. No guessing allowed.

    Correct sequence after publishing an article to Substack:
    1. Generate podcast (conversation mode, BOTH zh and en)
    2. Notify user to listen and confirm before RSS publish
    3. Notes promotion is already queued by publish_to_substack()

    This function handles step 1-2. Step 3 is automatic.
    """
    import sys
    from pathlib import Path

    podcast_dir = str(Path(__file__).resolve().parent.parent / "podcast")
    shared_dir = str(Path(__file__).resolve().parent.parent.parent / "lib")
    if podcast_dir not in sys.path:
        sys.path.insert(0, podcast_dir)
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)

    from handler import generate_conversation_for_article  # podcast handler, NOT this file
    from config import ARTIFACTS_DIR

    results = {}

    # Generate BOTH languages
    for lang in ["en", "zh"]:
        log.info("Post-publish: generating %s podcast for '%s'", lang, title)
        try:
            result = generate_conversation_for_article(
                article_text=article_text,
                title=title,
                lang=lang,
            )
            results[lang] = result
            log.info("Post-publish: %s podcast → %s", lang, result)
        except Exception as e:
            log.error("Post-publish: %s podcast failed: %s", lang, e)
            results[lang] = None

    # Notify user — do NOT auto-publish to RSS
    summary_lines = ["Podcast 已生成，等待试听确认："]
    for lang, path in results.items():
        status = f"✅ {path}" if path else "❌ 生成失败"
        summary_lines.append(f"  {lang.upper()}: {status}")
    summary_lines.append(f"\n确认后回复 'publish podcast {slug}' 发布到 RSS。")

    log.info("\n".join(summary_lines))
    return results
