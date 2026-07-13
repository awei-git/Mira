"""Briefing output helpers for the explorer agent."""

import logging
import re
from collections import Counter
from importlib import util as importlib_util
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("mira")

_SHARED_CONFIG_PATH = Path(__file__).resolve().parent.parent / "shared" / "config.py"
_spec = importlib_util.spec_from_file_location("_mira_shared_config", _SHARED_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load config from {_SHARED_CONFIG_PATH}")
_shared_config = importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_shared_config)
EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES = _shared_config.EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES
EXPLORER_NARRATIVE_SOURCE_MIN_TYPES = _shared_config.EXPLORER_NARRATIVE_SOURCE_MIN_TYPES
EXPLORER_CORPORATE_PR_MAX_RATIO = _shared_config.EXPLORER_CORPORATE_PR_MAX_RATIO
SOURCE_TRUST_TIERS = getattr(_shared_config, "SOURCE_TRUST_TIERS", {})
ENABLE_EPISTEMIC_FILTER = getattr(_shared_config, "ENABLE_EPISTEMIC_FILTER", True)
EPISTEMIC_CONFIDENCE_THRESHOLD = getattr(_shared_config, "EPISTEMIC_CONFIDENCE_THRESHOLD", "medium")
TRUST_CAVEAT = "community-aggregated signal — verify against primary source"


_AI_TECH_KEYWORDS = (
    "ai",
    "artificial intelligence",
    "agent",
    "alignment",
    "anthropic",
    "benchmark",
    "chatgpt",
    "claude",
    "compute",
    "deep learning",
    "gemini",
    "gpu",
    "language model",
    "llm",
    "machine learning",
    "model",
    "neural",
    "openai",
    "robotics",
    "software",
    "technology",
)

_AI_TECH_SOURCE_HINTS = (
    "ai",
    "arxiv",
    "devto",
    "github",
    "hackernews",
    "huggingface",
    "lobsters",
    "machinelearning",
    "tech",
)
_AI_TECH_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(keyword) for keyword in _AI_TECH_KEYWORDS) + r")\b")
_AI_FUTURE_TERMS = (
    "could",
    "future",
    "going to",
    "likely to",
    "may",
    "might",
    "over time",
    "will",
    "会",
    "可能",
    "将",
    "未来",
)
_AI_EFFECT_TERMS = (
    "adoption",
    "automation",
    "displace",
    "economic",
    "economy",
    "education",
    "employment",
    "impact",
    "inequality",
    "job",
    "labor",
    "productivity",
    "regulation",
    "replace",
    "risk",
    "safety",
    "social",
    "society",
    "wage",
    "workforce",
    "社会",
    "经济",
    "就业",
    "工作",
    "劳动力",
    "生产力",
    "影响",
    "工资",
    "不平等",
    "监管",
    "教育",
    "风险",
    "安全",
    "取代",
    "自动化",
)
_AI_FUTURE_EFFECT_PATTERN = re.compile(
    r"\b(?:ai|artificial intelligence|llm|agent|model|automation)\b|人工智能|大模型|模型"
)
_URL_PATTERN = re.compile(r"https?://[^\s)\]>]+")
_PROVENANCE_LABEL_PATTERN = re.compile(r"^\s*(?:[-*]\s*)?\[(?:HARD|SOFT):[^\]]+\]", re.IGNORECASE)
BENCHMARK_COMPARISON_WARNING = "Note: benchmark comparisons may not be valid — split methods differ or unspecified."
BENCHMARK_RESULT_SCHEMA = {
    "paper": "string",
    "model": "string",
    "benchmark": "string",
    "metric": "string",
    "value": "string",
    "split_method": "iid_random (IID random split)|temporal (temporal/sliding-window split)|task_held_out|cross_dataset|unreported",
}
_BENCHMARK_SPLIT_METHOD_PATTERN = re.compile(r'"?\bsplit_method\b"?\s*:\s*"?([^"\n,}\]]+)', re.IGNORECASE)
_BENCHMARK_RESULT_HEADING_PATTERN = re.compile(r"\bbenchmark_(?:result|claim)s?\b", re.IGNORECASE)
_BENCHMARK_CLAIM_PATTERN = re.compile(
    r"\b(?:achieves?|scores?|reaches?|gets?|beats?|outperforms?|accuracy|f1|auc|bleu|mmlu|swe-bench|benchmark)\b"
    r".{0,120}\b\d+(?:\.\d+)?\s*%?",
    re.IGNORECASE,
)
_PRICE_FACT_PATTERN = re.compile(
    r"(?<!\w)(?:[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|bn|million|billion|trillion))?|"
    r"\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY|CNY|dollars?|euros?|yuan))\b",
    re.IGNORECASE,
)
_DATE_FACT_PATTERN = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?|"
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4]\s+(?:19|20)\d{2}|(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_NUMBER_FACT_PATTERN = re.compile(
    r"(?<![\w$€£¥])\d[\d,]*(?:\.\d+)?(?:\s?(?:%|percent|bps|x|k|m|bn|million|billion|trillion|thousand))?",
    re.IGNORECASE,
)
_CAPITALIZED_FACT_PATTERN = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&'.-]+|[A-Z]{2,})"
    r"(?:\s+(?:[A-Z][A-Za-z0-9&'.-]+|[A-Z]{2,}|of|and|the|for|in|on|at|to|de|la|&)){0,5}\b"
)
_CAPITALIZED_FACT_STOPWORDS = {
    "A",
    "An",
    "And",
    "But",
    "For",
    "From",
    "If",
    "In",
    "It",
    "Its",
    "Of",
    "On",
    "Or",
    "That",
    "The",
    "This",
    "To",
    "With",
}
_RAW_ARTICLE_TEXT_FIELDS = ("raw_text", "raw_extracted_text", "extracted_text", "article_text")
_ARTICLE_SUMMARY_FIELDS = ("summary_text", "vlm_summary", "summary")
_TEMPORAL_SPLIT_PATTERN = re.compile(
    r"\b(?:temporal split|time-based split|chronological split|sequential split)\b",
    re.IGNORECASE,
)
_BENCHMARK_SPLIT_FLAG_PATTERN = re.compile(r"\[split:(?:temporal|unspecified)\]", re.IGNORECASE)

SOFT_SIGNAL_SOURCES = ["github.com/trending", "huggingface.co/trending", "reddit.com"]
HARD_SIGNAL_SOURCES = ["arxiv.org", "paperswithcode.com/sota"]

_CORPORATE_DOMAINS = (
    "about.fb.com",
    "ai.googleblog.com",
    "amazon.science",
    "anthropic.com",
    "apple.com",
    "blog.google",
    "deepmind.google",
    "github.blog",
    "google.com",
    "huggingface.co",
    "ibm.com",
    "meta.com",
    "microsoft.com",
    "nvidia.com",
    "openai.com",
    "salesforce.com",
    "stability.ai",
    "tesla.com",
)
_INDEPENDENT_RESEARCH_DOMAINS = (
    "arxiv.org",
    "biorxiv.org",
    "doi.org",
    "medrxiv.org",
    "openreview.net",
    "papers.ssrn.com",
    "pubmed.ncbi.nlm.nih.gov",
    "researchgate.net",
    "semanticscholar.org",
)
_JOURNALISM_DOMAINS = (
    "404media.co",
    "apnews.com",
    "axios.com",
    "bbc.com",
    "bloomberg.com",
    "businessinsider.com",
    "cnbc.com",
    "economist.com",
    "ft.com",
    "garbage-day.email",
    "ieee.org",
    "latimes.com",
    "nytimes.com",
    "platformer.news",
    "reuters.com",
    "semafor.com",
    "technologyreview.com",
    "techcrunch.com",
    "theatlantic.com",
    "theinformation.com",
    "theverge.com",
    "vox.com",
    "washingtonpost.com",
    "wired.com",
)
_THINK_TANK_DOMAINS = (
    "brookings.edu",
    "carnegieendowment.org",
    "cfr.org",
    "csis.org",
    "hoover.org",
    "itif.org",
    "nber.org",
    "rand.org",
    "rstreet.org",
)
HEURISTIC_SINGLE_EXEMPLAR = "HEURISTIC_SINGLE_EXEMPLAR"
HEURISTIC_SOURCE_INCENTIVE = "HEURISTIC_SOURCE_INCENTIVE"
HEURISTIC_CORROBORATION = "HEURISTIC_CORROBORATION"
BIAS_SINGLE_ANECDOTE_AS_UNIVERSAL = "single_anecdote_as_universal"
BIAS_MISSING_BASE_RATE_LANGUAGE = "missing_base_rate_language"
BIAS_PLATFORM_VENDOR_MARKETING = "platform_vendor_marketing"
_PLATFORM_VENDOR_DOMAINS = _CORPORATE_DOMAINS + (
    "airtable.com",
    "canva.com",
    "figma.com",
    "framer.com",
    "make.com",
    "notion.com",
    "notion.so",
    "replit.com",
    "webflow.com",
    "zapier.com",
)
_PLATFORM_VENDOR_NAMES = tuple(domain.split(".", 1)[0] for domain in _PLATFORM_VENDOR_DOMAINS)
_TREND_CLAIM_TERMS = (
    "adoption",
    "actionable",
    "case study",
    "changes everything",
    "everyone is",
    "generalizes",
    "new pattern",
    "proves",
    "shift",
    "skills don't matter",
    "trend",
    "will",
    "winning",
    "可操作",
    "趋势",
    "证明",
    "都会",
    "不重要",
    "正在变成",
)
_SINGLE_EXEMPLAR_PATTERN = re.compile(
    r"(?:case study|example|like|such as|e\.g\.|例如|比如|像)\s+['\"“”]?([A-Z][A-Za-z0-9_-]{2,})"
)
_SINGLE_ANECDOTE_UNIVERSAL_PATTERN = re.compile(
    r"\b(?:how i|how we)\b|"
    r"\b(?:i|we)\s+(?:did|built|launched|used|tried|started|grew|made|earned|sold|created)\b"
    r".{0,140}\b(?:made|earned|grew|hit|reached|got|generated|sold|landed|revenue|users|followers|subscribers|\$)\b|"
    r"(?:我是|我们|我用|我靠|我的).{0,60}(?:赚|增长|做到|卖出|获得|粉丝|用户|收入)",
    re.IGNORECASE | re.DOTALL,
)
_UNIVERSAL_NO_BASE_RATE_PATTERN = re.compile(
    r"\b(?:anyone can|everyone can|everybody can|just (?:do|use|start|post|ship|build|ask)|"
    r"all you need|no experience required|no audience needed|works for anyone)\b|"
    r"(?:人人都能|人人可以|任何人都能|只要.{0,20}就|照着做|一招|普通人也能)",
    re.IGNORECASE | re.DOTALL,
)
_BASE_RATE_EVIDENCE_PATTERN = re.compile(
    r"\b(?:base rate|baseline|sample size|sample|survey|cohort|study|dataset|participants|respondents|"
    r"median|average|distribution|percentile|n\s*=|conversion rate|success rate|failure rate)\b|"
    r"(?:样本|基准率|基线|调查|研究|数据集|中位数|平均|分布|成功率|失败率)",
    re.IGNORECASE,
)
_PLATFORM_VENDOR_MARKETING_PATTERN = re.compile(
    r"\b(?:democratize|unlock|effortless|seamless|frictionless|turnkey|10x|no-code|without code|"
    r"in minutes|scale your|monetize|creator economy|growth playbook|powered by|passive income|"
    r"make money online|transform your workflow|build faster|ship faster)\b|"
    r"(?:赋能|一键|闭环|私域|变现|无需代码|零门槛|人人都是|轻松赚钱|快速起号)",
    re.IGNORECASE,
)


def _is_ai_tech_item(item: dict) -> bool:
    source = str(item.get("source", "")).lower()
    if any(hint in source for hint in _AI_TECH_SOURCE_HINTS):
        return True

    text = " ".join(str(item.get(field, "")) for field in ("title", "summary", "description", "tags", "query")).lower()
    return bool(_AI_TECH_PATTERN.search(text))


def _source_entity(item: dict) -> str:
    for field in ("publisher", "domain"):
        value = str(item.get(field, "")).strip().lower()
        if value:
            return value.removeprefix("www.")

    url = str(item.get("url", "")).strip()
    if url:
        host = urlparse(url).netloc.lower()
        if host:
            return host.removeprefix("www.")

    return str(item.get("source", "unknown")).strip().lower() or "unknown"


def _domain_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _source_text(item: dict) -> str:
    return " ".join(str(item.get(field, "")) for field in ("source", "url", "title", "summary", "description")).lower()


def _signal_provenance_label(item: dict) -> str | None:
    text = _source_text(item)
    source_name = str(item.get("source", "")).strip().lower()
    url = str(item.get("url", "")).strip()
    host = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
    path = urlparse(url).path.strip("/") if url else ""

    if (
        any(source in text for source in SOFT_SIGNAL_SOURCES)
        or source_name in {"github_trending", "huggingface"}
        or "reddit.com" in host
    ):
        if "github" in text:
            return "[SOFT: GitHub trending]"
        if "huggingface" in text:
            return "[SOFT: HuggingFace trending]"
        if "reddit" in text or "reddit.com" in host:
            return "[SOFT: Reddit]"
        return "[SOFT: popularity signal]"

    if any(source in text for source in HARD_SIGNAL_SOURCES) or source_name == "arxiv" or host.endswith("arxiv.org"):
        if host.endswith("arxiv.org"):
            paper_id = path.rsplit("/", 1)[-1] if path else ""
            return f"[HARD: arxiv {paper_id}]" if paper_id else "[HARD: arxiv]"
        if "paperswithcode.com/sota" in text:
            return "[HARD: Papers with Code SOTA]"
        return "[HARD: research signal]"

    return None


def _source_trust_tier(item: dict) -> str | None:
    source = str(item.get("source", "")).strip().lower()
    normalized = source.replace(" ", "_")
    candidates = [normalized]
    if normalized.startswith("r/"):
        candidates.append("reddit")
    if normalized == "hackernews":
        candidates.append("hacker_news")
    if normalized == "hacker_news":
        candidates.append("hackernews")

    for candidate in candidates:
        tier = SOURCE_TRUST_TIERS.get(candidate)
        if tier:
            return tier
    return None


def _trust_caveat(item: dict) -> str | None:
    tier = _source_trust_tier(item)
    if tier in ("community", "aggregator"):
        return TRUST_CAVEAT
    return None


def _annotate_signal_provenance(briefing: str, feed_items: list) -> str:
    labels = []
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        label = _signal_provenance_label(item)
        caveat = _trust_caveat(item)
        if not label and not caveat:
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        labels.append((label, caveat, title, url))

    if not labels:
        return briefing

    source_lines = briefing.splitlines()
    lines = []
    for index, line in enumerate(source_lines):
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        has_provenance_label = bool(_PROVENANCE_LABEL_PATTERN.search(line))

        label = None
        caveat = None
        for candidate_label, candidate_caveat, title, url in labels:
            if (url and url in line) or (title and title in line):
                label = candidate_label
                caveat = candidate_caveat
                break

        prefix = line[: len(line) - len(line.lstrip())]
        body = line[len(prefix) :]
        if label and not has_provenance_label:
            if body.startswith("- "):
                line = f"{prefix}- {label} {body[2:]}"
            elif body.startswith("* "):
                line = f"{prefix}* {label} {body[2:]}"
            else:
                line = f"{prefix}{label} {body}"
        lines.append(line)
        if caveat and "trust_caveat:" not in line:
            next_line = source_lines[index + 1].strip() if index + 1 < len(source_lines) else ""
            if not next_line.startswith("trust_caveat:"):
                caveat_prefix = f"{prefix}    " if label or body.startswith(("- ", "* ")) else prefix
                lines.append(f"{caveat_prefix}trust_caveat: {caveat}")

    return "\n".join(lines)


def _is_platform_vendor_item(item: dict) -> bool:
    host = _source_entity(item)
    source_name = host.split("/", 1)[0].split(".", 1)[0]
    return _domain_matches(host, _PLATFORM_VENDOR_DOMAINS) or source_name in _PLATFORM_VENDOR_NAMES


def _is_trend_claim(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _TREND_CLAIM_TERMS)


def _independent_non_platform_sources(feed_items: list) -> set[str]:
    sources = set()
    for item in feed_items:
        if not isinstance(item, dict) or _is_platform_vendor_item(item):
            continue
        source = _source_entity(item)
        if classify_narrative_source(item.get("url", ""), item.get("title", "")) != "corporate_pr":
            sources.add(source)
    return sources


def _downgrade_confidence(confidence: str) -> str:
    if confidence == "high":
        return "medium"
    if confidence == "medium":
        return "low"
    return "low"


def _lowest_confidence(*values: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return min((value if value in order else "high" for value in values), key=lambda value: order[value])


def detect_survivorship_bias(content: str) -> dict:
    if not ENABLE_EPISTEMIC_FILTER:
        return {"confidence": "high", "flags": []}

    text = str(content or "")
    flags = []
    has_base_rate_evidence = bool(_BASE_RATE_EVIDENCE_PATTERN.search(text))

    if _SINGLE_ANECDOTE_UNIVERSAL_PATTERN.search(text) and not has_base_rate_evidence:
        flags.append(BIAS_SINGLE_ANECDOTE_AS_UNIVERSAL)
    if _UNIVERSAL_NO_BASE_RATE_PATTERN.search(text) and not has_base_rate_evidence:
        flags.append(BIAS_MISSING_BASE_RATE_LANGUAGE)
    if _PLATFORM_VENDOR_MARKETING_PATTERN.search(text):
        flags.append(BIAS_PLATFORM_VENDOR_MARKETING)

    flags = sorted(set(flags))
    if not flags:
        confidence = "high"
    elif len(flags) == 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {"confidence": confidence, "flags": flags}


def _format_yaml_field(key: str, values: list[str]) -> str:
    if not values:
        return f"{key}: []"
    return f"{key}:\n" + "\n".join(f"  - {value}" for value in values)


def _extract_facts(text) -> list[str]:
    facts = []
    seen = set()
    for pattern in (_PRICE_FACT_PATTERN, _DATE_FACT_PATTERN, _NUMBER_FACT_PATTERN, _CAPITALIZED_FACT_PATTERN):
        for match in pattern.finditer(str(text or "")):
            fact = re.sub(r"\s+", " ", match.group(0)).strip(" \t\r\n.,;:()[]{}")
            if not fact or fact in _CAPITALIZED_FACT_STOPWORDS:
                continue
            key = fact.lower()
            if key in seen or any(key in existing for existing in seen):
                continue
            seen.add(key)
            facts.append(fact)
    return facts


def _verify_completeness(raw_text, summary_text) -> list[str]:
    summary_lower = str(summary_text or "").lower()
    return [fact for fact in _extract_facts(raw_text) if fact.lower() not in summary_lower]


def _first_text_field(item: dict, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _append_metadata_warning(content: str, warning: str) -> str:
    warning_line = f"  - {_yaml_quote(warning)}"
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            lines = content[4:end].rstrip().splitlines()
            for index, line in enumerate(lines):
                if line == "completeness_warnings: []":
                    lines[index] = "completeness_warnings:"
                    lines.insert(index + 1, warning_line)
                    frontmatter = "\n".join(lines)
                    return f"---\n{frontmatter}\n---{content[end + 4:]}"
                if line == "completeness_warnings:":
                    insert_at = index + 1
                    while insert_at < len(lines) and lines[insert_at].startswith("  - "):
                        insert_at += 1
                    lines.insert(insert_at, warning_line)
                    frontmatter = "\n".join(lines)
                    return f"---\n{frontmatter}\n---{content[end + 4:]}"

            frontmatter = "\n".join(lines).strip()
            frontmatter = (
                f"{frontmatter}\ncompleteness_warnings:\n{warning_line}"
                if frontmatter
                else f"completeness_warnings:\n{warning_line}"
            )
            return f"---\n{frontmatter}\n---{content[end + 4:]}"

    return f"---\ncompleteness_warnings:\n{warning_line}\n---\n\n{content}"


def _completeness_warnings(feed_items: list) -> list[str]:
    warnings = []
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        raw_text = _first_text_field(item, _RAW_ARTICLE_TEXT_FIELDS)
        summary_text = _first_text_field(item, _ARTICLE_SUMMARY_FIELDS)
        if not raw_text or not summary_text:
            continue
        missing = _verify_completeness(raw_text, summary_text)
        if not missing:
            continue
        title = str(item.get("title") or item.get("url") or "untitled article").strip()
        url = str(item.get("url") or "").strip()
        shown = missing[:12]
        suffix = f"; +{len(missing) - len(shown)} more" if len(missing) > len(shown) else ""
        warning = f"VLM summary completeness warning for {title[:120]}: missing facts: {', '.join(shown)}{suffix}"
        warnings.append(warning)
        log.warning(
            "VLM summary completeness warning: title=%s url=%s missing_facts=%s",
            title[:120],
            url,
            ", ".join(shown),
        )
    return warnings


def _set_epistemic_frontmatter(frontmatter: str, audit: dict) -> str:
    lines = []
    skip_bias_items = False
    for line in frontmatter.splitlines():
        if skip_bias_items and line.startswith("  - "):
            continue
        skip_bias_items = False
        if line.startswith("epistemic_confidence:"):
            continue
        if line.startswith("bias_flags:"):
            skip_bias_items = True
            continue
        lines.append(line)

    lines.append(f"epistemic_confidence: {audit['confidence']}")
    lines.append(_format_yaml_field("bias_flags", audit["flags"]))
    return "\n".join(lines).strip()


def annotate_epistemic_metadata(content: str, audit: dict | None = None) -> str:
    if not ENABLE_EPISTEMIC_FILTER:
        return content

    audit = audit or detect_survivorship_bias(content)
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            frontmatter = _set_epistemic_frontmatter(content[4:end], audit)
            return f"---\n{frontmatter}\n---{content[end + 4:]}"

    return (
        "---\n"
        f"epistemic_confidence: {audit['confidence']}\n"
        f"{_format_yaml_field('bias_flags', audit['flags'])}\n"
        "---\n\n"
        f"{content}"
    )


def screen_selection_bias(claim_text: str, feed_items: list | None = None) -> dict:
    """Lightweight selection-bias screen for briefing and skill extraction claims."""
    feed_items = feed_items or []
    reasons = []
    confidence = "high"
    independent_sources = _independent_non_platform_sources(feed_items)
    is_trend = _is_trend_claim(claim_text)
    named_cases = set(_SINGLE_EXEMPLAR_PATTERN.findall(claim_text))

    if is_trend and len(named_cases) == 1:
        reasons.append(HEURISTIC_SINGLE_EXEMPLAR)
        confidence = "low"

    if any(isinstance(item, dict) and _is_platform_vendor_item(item) for item in feed_items):
        reasons.append(HEURISTIC_SOURCE_INCENTIVE)
        confidence = _downgrade_confidence(confidence)

    if is_trend and len(independent_sources) < 2:
        reasons.append(HEURISTIC_CORROBORATION)
        confidence = "low"

    survivorship_audit = detect_survivorship_bias(claim_text)
    if survivorship_audit["flags"]:
        reasons.extend(survivorship_audit["flags"])
        confidence = _lowest_confidence(confidence, survivorship_audit["confidence"])

    return {
        "flagged": bool(reasons),
        "epistemic_confidence": confidence,
        "reasons": sorted(set(reasons)),
        "independent_non_platform_sources": len(independent_sources),
        "single_exemplars": sorted(named_cases),
    }


def classify_narrative_source(source_url, claim) -> str:
    """Bucket a source behind an AI-future claim by institutional source type."""
    url = str(source_url or "").strip()
    host = urlparse(url).netloc.lower().removeprefix("www.")
    text = f"{host} {claim or ''}".lower()

    if _domain_matches(host, _THINK_TANK_DOMAINS):
        return "think_tank"
    if host.endswith(".edu") or ".edu/" in text or _domain_matches(host, _INDEPENDENT_RESEARCH_DOMAINS):
        return "independent_research"
    if _domain_matches(host, _CORPORATE_DOMAINS):
        return "corporate_pr"
    if _domain_matches(host, _JOURNALISM_DOMAINS):
        return "journalism"
    if any(hint in text for hint in ("company blog", "press release", "newsroom", "corporate blog")):
        return "corporate_pr"
    if any(hint in text for hint in ("arxiv", "university", "journal", "paper", "study")):
        return "independent_research"
    if any(hint in text for hint in ("reuters", "associated press", "newspaper", "magazine", "journalist")):
        return "journalism"
    if any(hint in text for hint in ("think tank", "policy institute", "foundation", "council")):
        return "think_tank"
    return "other"


def _contains_ai_future_effect_claim(text: str) -> bool:
    lowered = text.lower()
    return (
        bool(_AI_FUTURE_EFFECT_PATTERN.search(lowered))
        and any(term in lowered for term in _AI_FUTURE_TERMS)
        and any(term in lowered for term in _AI_EFFECT_TERMS)
    )


def _briefing_source_claims(briefing: str, feed_items: list) -> list[tuple[str, str]]:
    claims = []
    for line in briefing.splitlines():
        urls = _URL_PATTERN.findall(line)
        if urls:
            claim = line.strip()
            claims.extend((url.rstrip(".,;:"), claim) for url in urls)

    if claims:
        return claims

    for item in feed_items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        claim = " ".join(str(item.get(field, "")) for field in ("source", "title", "summary", "description", "query"))
        claims.append((url, claim))
    return claims


def _audit_source_diversity(feed_items: list) -> dict:
    counts = Counter(_source_entity(item) for item in feed_items if isinstance(item, dict) and _is_ai_tech_item(item))
    unique_sources = len(counts)
    return {
        "unique_sources": unique_sources,
        "concentration_risk": unique_sources < EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES,
        "dominant_sources": [{"source": source, "count": count} for source, count in counts.most_common(3)],
    }


def _audit_narrative_source_diversity(briefing: str, feed_items: list) -> dict:
    if not _contains_ai_future_effect_claim(briefing):
        return {"applies": False, "flagged": False}

    source_claims = _briefing_source_claims(briefing, feed_items)
    counts = Counter(classify_narrative_source(url, claim) for url, claim in source_claims)
    total = sum(counts.values())
    corporate_pr_ratio = (counts.get("corporate_pr", 0) / total) if total else 0.0
    distinct_types = len(counts)
    reasons = []
    if corporate_pr_ratio > EXPLORER_CORPORATE_PR_MAX_RATIO:
        reasons.append("corporate_pr_ratio")
    if distinct_types < EXPLORER_NARRATIVE_SOURCE_MIN_TYPES:
        reasons.append("source_type_count")

    return {
        "applies": True,
        "flagged": bool(reasons),
        "reasons": reasons,
        "source_type_counts": dict(counts),
        "distinct_types": distinct_types,
        "corporate_pr_ratio": corporate_pr_ratio,
        "total_sources": total,
    }


def _format_source_type_counts(counts: dict) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{source_type}={count}" for source_type, count in sorted(counts.items()))


def _benchmark_claim_context(line: str, feed_items: list) -> str:
    context = [line]
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if (title and title in line) or (url and url in line):
            context.append(
                " ".join(str(item.get(field, "")) for field in ("source", "title", "summary", "description", "query"))
            )
            break
    return " ".join(context)


def _benchmark_split_flag(text: str) -> str:
    if _TEMPORAL_SPLIT_PATTERN.search(text):
        return "[split:temporal]"
    return "[split:unspecified]"


def _annotate_benchmark_claim_splits(briefing: str, feed_items: list) -> str:
    lines = []
    for line in briefing.splitlines():
        if _BENCHMARK_CLAIM_PATTERN.search(line) and not _BENCHMARK_SPLIT_FLAG_PATTERN.search(line):
            split_flag = _benchmark_split_flag(_benchmark_claim_context(line, feed_items))
            line = f"{line} {split_flag}"
        lines.append(line)
    return "\n".join(lines)


def _normalize_split_method(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not normalized:
        return "unreported"
    if normalized in {"iid", "iid_random", "random", "random_split", "random_iid", "iid_random_split"}:
        return "iid_random"
    if normalized in {"temporal", "temporal_split", "time", "time_based", "time_based_split", "sliding_window"}:
        return "temporal"
    if normalized in {
        "task_held_out",
        "task_held_out_split",
        "task_heldout",
        "held_out_task",
        "heldout_task",
        "task_holdout",
    }:
        return "task_held_out"
    if normalized in {"cross_dataset", "cross_dataset_split", "dataset_held_out", "held_out_dataset"}:
        return "cross_dataset"
    if normalized in {"unreported", "not_reported", "unspecified", "unknown", "not_specified"}:
        return "unreported"
    return normalized


def _benchmark_split_method_audit(briefing: str) -> dict:
    methods = [
        _normalize_split_method(match.group(1).strip(" '\"`"))
        for match in _BENCHMARK_SPLIT_METHOD_PATTERN.finditer(briefing)
    ]
    benchmark_claims = _BENCHMARK_CLAIM_PATTERN.findall(briefing)
    has_benchmark_aggregation = (
        len(methods) > 1 or len(benchmark_claims) > 1 or bool(_BENCHMARK_RESULT_HEADING_PATTERN.search(briefing))
    )
    if not has_benchmark_aggregation:
        return {"flagged": False, "methods": methods}

    unique_methods = set(methods)
    flagged = not methods or "unreported" in unique_methods or len(unique_methods) > 1
    return {"flagged": flagged, "methods": methods}


def apply_source_diversity_note(briefing: str, feed_items: list) -> str:
    briefing = _annotate_signal_provenance(briefing, feed_items)
    briefing = _annotate_benchmark_claim_splits(briefing, feed_items)
    completeness_warnings = _completeness_warnings(feed_items)
    audit = _audit_source_diversity(feed_items)
    narrative_audit = _audit_narrative_source_diversity(briefing, feed_items)
    selection_bias_audit = screen_selection_bias(briefing, feed_items)
    benchmark_audit = _benchmark_split_method_audit(briefing)
    epistemic_audit = detect_survivorship_bias(briefing)
    notes = []

    if audit["concentration_risk"]:
        dominant = ", ".join(f"{entry['source']} ({entry['count']})" for entry in audit["dominant_sources"])
        if not dominant:
            dominant = "none"

        notes.append(
            "⚠️ Source Diversity Note\n\n"
            f"AI/tech items draw from {audit['unique_sources']} distinct source(s), "
            f"below the configured threshold of {EXPLORE_SOURCE_DIVERSITY_MIN_ENTITIES}. "
            f"Dominant sources: {dominant}."
        )

    if narrative_audit["flagged"]:
        counts = _format_source_type_counts(narrative_audit["source_type_counts"])
        metadata = (
            "[NARRATIVE_DIVERSITY_FLAG] "
            f"corporate_pr_ratio={narrative_audit['corporate_pr_ratio']:.2f}; "
            f"distinct_source_types={narrative_audit['distinct_types']}; "
            f"source_type_counts={counts}; "
            f"reasons={','.join(narrative_audit['reasons'])}"
        )
        notes.append(
            f"{metadata}\n\n"
            "Narrative diversity flag: this briefing contains forward-looking claims about AI's "
            "economic or social effects, and its source mix is concentrated. "
            f"Minimum source types: {EXPLORER_NARRATIVE_SOURCE_MIN_TYPES}; "
            f"maximum corporate/vested-interest ratio: {EXPLORER_CORPORATE_PR_MAX_RATIO:.2f}."
        )

    if selection_bias_audit["flagged"]:
        exemplars = ", ".join(selection_bias_audit["single_exemplars"]) or "none"
        notes.append(
            "[SELECTION_BIAS_SCREEN] "
            f"epistemic_confidence={selection_bias_audit['epistemic_confidence']}; "
            f"heuristics={','.join(selection_bias_audit['reasons'])}; "
            f"independent_non_platform_sources={selection_bias_audit['independent_non_platform_sources']}; "
            f"single_exemplars={exemplars}\n\n"
            "Selection-bias screen: treat flagged platform narratives or single-case trend claims as "
            "reported anecdotes unless corroborated by at least two independent, non-platform-affiliated sources."
        )

    if epistemic_audit["flags"]:
        notes.append(
            "[EPISTEMIC_FILTER] "
            f"epistemic_confidence={epistemic_audit['confidence']}; "
            f"bias_flags={','.join(epistemic_audit['flags'])}\n\n"
            "Epistemic integrity check: this content may lean on survivorship-biased evidence. "
            "Treat practical claims as hypotheses unless base rates or independent corroboration are present."
        )

    if benchmark_audit["flagged"] and BENCHMARK_COMPARISON_WARNING not in briefing:
        notes.append(BENCHMARK_COMPARISON_WARNING)

    if not notes:
        content = annotate_epistemic_metadata(briefing, epistemic_audit)
    else:
        content = annotate_epistemic_metadata("\n\n".join(notes) + "\n\n" + briefing, epistemic_audit)

    for warning in completeness_warnings:
        content = _append_metadata_warning(content, warning)
    return content
