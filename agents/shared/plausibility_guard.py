"""Lightweight hallucination-pattern scanner for generated drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    pattern_type: str
    passage: str
    confidence: float
    reason: str
    start: int
    end: int
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaxonomyPattern:
    pattern_type: str
    regex_triggers: tuple[re.Pattern[str], ...]
    keyword_triggers: tuple[str, ...]
    structural_heuristics: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    confidence: float
    reason: str
    triggers: tuple[str, ...]


class PlausibilityGuard:
    """Conservative scanner for high-risk hallucination shapes."""

    _SOURCE_SIGNAL_RE = re.compile(
        r"https?://\S+|"
        r"\b(?:doi:|arxiv:|isbn:|source:|sources:|citation:|cited in|according to|per "
        r"(?:the )?|reported by|published by|official docs?|documentation|see also|"
        r"as documented by|from the \w+ report|in \w+ et al\.?)\b|"
        r"\[[0-9]{1,3}\]|\([A-Z][A-Za-z-]+,\s*(?:1[8-9]\d{2}|20\d{2})\)|"
        r"(?:来源|参见|引用|据.*报告|根据.*研究|官方文档)",
        re.IGNORECASE,
    )
    _SENTENCE_RE = re.compile(r"[^。！？!?;\n]+[。！？!?;]?", re.MULTILINE)

    _STATISTIC_RE = re.compile(
        r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?"
        r"(?:%|percent|percentage points?|bps|basis points|x|fold|times|million|billion|"
        r"trillion|thousand|users?|people|customers?|respondents?|dollars?|usd|yuan|"
        r"元|万人|亿|万|倍|个百分点)\b",
        re.IGNORECASE,
    )
    _PRECISE_STAT_RE = re.compile(
        r"(?<![\w.])\d{1,3}(?:,\d{3})*(?:\.\d{2,})\s?"
        r"(?:%|percent|percentage points?|bps|basis points|x|fold|times|million|billion|"
        r"trillion|thousand|users?|people|customers?|respondents?|dollars?|usd|yuan|"
        r"元|万人|亿|万|倍|个百分点)\b",
        re.IGNORECASE,
    )
    _STAT_CLAIM_RE = re.compile(
        r"\b(?:shows?|showed|found|finds|rose|fell|increased|decreased|grew|shrunk|"
        r"accounts? for|represents?|predicts?|estimates?|surveyed|reported|"
        r"reduced|improved|outperformed)\b|(?:显示|发现|增长|下降|占比|估计|报告|调查)",
        re.IGNORECASE,
    )

    _LEGAL_RE = re.compile(
        r"\b(?:law|statute|regulation|regulatory|legal|illegal|unlawful|compliance|"
        r"compliant|court|case|doctrine|treaty|jurisdiction|liability|mandates?|"
        r"requires?|forbids?|prohibits?|bans?|under [^.!?。！？]{0,40} law|"
        r"gdpr|hipaa|sec|ftc|fda|fcc|cfr|u\.s\.c\.|eu ai act)\b|"
        r"(?:法律|法规|监管|合规|违法|法院|判例|条例|禁止|要求|第\d+条)",
        re.IGNORECASE,
    )
    _LEGAL_CITATION_RE = re.compile(
        r"\b\d+\s+(?:u\.s\.c\.|cfr)\s+§?\s*\d+|§\s*\d+|"
        r"\b[A-Z][A-Za-z]+ v\. [A-Z][A-Za-z]+|\bRegulation \(EU\) \d+/\d+",
        re.IGNORECASE,
    )
    _LEGAL_ASSERTION_RE = re.compile(
        r"\b(?:must|cannot|may not|required to|requires|forbidden|prohibited|illegal|"
        r"unlawful|liable|compliant|noncompliant|banned|mandated)\b|"
        r"(?:必须|不得|禁止|违法|合规|要求|承担责任)",
        re.IGNORECASE,
    )
    _JURISDICTION_RE = re.compile(
        r"\b(?:u\.s\.|us|united states|eu|european union|uk|china|california|new york|"
        r"federal|state|international|gdpr|hipaa|sec|ftc|fda|fcc)\b|"
        r"(?:美国|欧盟|英国|中国|加州|纽约|联邦|国际)",
        re.IGNORECASE,
    )

    _FULL_DATE_RE = re.compile(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)\s+\d{1,2},\s*(?:1[5-9]\d{2}|20\d{2})\b|"
        r"\b(?:1[5-9]\d{2}|20\d{2})-\d{2}-\d{2}\b|"
        r"\b\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(?:1[5-9]\d{2}|20\d{2})\b",
        re.IGNORECASE,
    )
    _YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2})\b")
    _HISTORY_EVENT_RE = re.compile(
        r"\b(?:war|revolution|battle|massacre|election|treaty|empire|dynasty|"
        r"founded|launched|collapsed|signed|declared|invented|discovered|"
        r"assassinated|born|died|first|last|anniversary|became)\b|"
        r"(?:战争|革命|战役|选举|条约|帝国|王朝|成立|签署|宣布|发明|发现|出生|去世)",
        re.IGNORECASE,
    )
    _NAMED_ENTITY_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b|[\u4e00-\u9fff]{2,}")

    _FUNCTION_NAME_RE = re.compile(
        r"`?(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|"
        r"[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]{3,})\(\)`?"
    )
    _API_CONTEXT_RE = re.compile(
        r"\b(?:api|sdk|library|package|module|function|method|class|cli|endpoint|"
        r"parameter|argument|return value|import|python|javascript|typescript|react|"
        r"django|fastapi|pytorch|openai|anthropic|langchain|docs?)\b",
        re.IGNORECASE,
    )
    _API_BEHAVIOR_RE = re.compile(
        r"\b(?:use|call|import|invoke|returns?|raises?|accepts?|supports?|configures?|"
        r"deprecated|introduced|renamed|replaced|install|passes?|sets?)\b",
        re.IGNORECASE,
    )

    _AUTHORITY_PHRASE_RE = re.compile(
        r"\b(?:studies show|research proves|experts agree|the consensus is|"
        r"it is well known|it is widely accepted|everyone knows|many people say|"
        r"data shows?|science says|evidence proves)\b|"
        r"(?:研究表明|专家一致认为|众所周知|数据显示|证据证明)",
        re.IGNORECASE,
    )
    _CERTAINTY_RE = re.compile(
        r"\b(?:clearly|obviously|undeniably|certainly|always|never|all|every|no one|"
        r"proves that|this means|therefore|thus)\b|(?:显然|必然|一定|所有|从不|证明了)",
        re.IGNORECASE,
    )
    _HEDGE_RE = re.compile(r"\b(?:may|might|could|likely|perhaps|appears?|suggests?|seems?)\b|(?:可能|也许|似乎)")

    def __init__(self, min_confidence: float = 0.88):
        self.min_confidence = min_confidence
        self.taxonomy: dict[str, TaxonomyPattern] = {
            "fake-statistic": TaxonomyPattern(
                pattern_type="fake-statistic",
                regex_triggers=(self._STATISTIC_RE, self._PRECISE_STAT_RE),
                keyword_triggers=("percent", "percentage points", "survey", "respondents", "数据显示", "调查"),
                structural_heuristics=(
                    "precise numerical claim",
                    "claim verb near statistic",
                    "no nearby source signal",
                ),
            ),
            "fake-legal": TaxonomyPattern(
                pattern_type="fake-legal",
                regex_triggers=(self._LEGAL_RE, self._LEGAL_CITATION_RE),
                keyword_triggers=("under law", "regulation", "compliance", "illegal", "合规", "违法"),
                structural_heuristics=(
                    "legal or regulatory assertion",
                    "jurisdiction or citation signal",
                    "no nearby source signal",
                ),
            ),
            "fake-historical": TaxonomyPattern(
                pattern_type="fake-historical",
                regex_triggers=(self._FULL_DATE_RE, self._YEAR_RE),
                keyword_triggers=("founded", "signed", "war", "revolution", "成立", "签署"),
                structural_heuristics=(
                    "specific date or year",
                    "historical event verb",
                    "no nearby source signal",
                ),
            ),
            "fake-function-name": TaxonomyPattern(
                pattern_type="fake-function-name",
                regex_triggers=(self._FUNCTION_NAME_RE,),
                keyword_triggers=("api", "function", "method", "sdk", "library", "endpoint"),
                structural_heuristics=(
                    "function-like identifier",
                    "API behavior claim",
                    "no nearby docs or source signal",
                ),
            ),
            "plausible-over-true": TaxonomyPattern(
                pattern_type="plausible-over-true",
                regex_triggers=(self._AUTHORITY_PHRASE_RE, self._CERTAINTY_RE),
                keyword_triggers=("studies show", "experts agree", "obviously", "clearly", "研究表明"),
                structural_heuristics=(
                    "authority or certainty phrase",
                    "broad generalization",
                    "no nearby source signal",
                ),
            ),
        }

    def scan(self, text: str) -> list[Flag]:
        if not text:
            return []

        flags: list[Flag] = []
        for start, end, passage in self._iter_passages(text):
            if len(passage) < 20:
                continue
            window = self._window(text, start, end)
            for pattern_type in self.taxonomy:
                candidate = self._evaluate(pattern_type, passage, window)
                if candidate and candidate.confidence >= self.min_confidence:
                    flags.append(
                        Flag(
                            pattern_type=pattern_type,
                            passage=" ".join(passage.split()),
                            confidence=round(candidate.confidence, 2),
                            reason=candidate.reason,
                            start=start,
                            end=end,
                            triggers=candidate.triggers,
                        )
                    )
        return self._dedupe(flags)

    def content_guard_hook(self, text: str) -> tuple[bool, float, list[Flag]]:
        """Adapter for Substack _content_looks_like_error() preflight callers."""
        flags = self.scan(text)
        if not flags:
            return False, 1.0, []
        confidence = max(flag.confidence for flag in flags)
        return True, confidence, flags

    def content_looks_like_error_hook(self, text: str) -> tuple[bool, float]:
        blocked, confidence, _flags = self.content_guard_hook(text)
        return blocked, confidence

    def writer_editorial_pass_hook(self, text: str) -> list[Flag]:
        """Adapter for the writer agent's final editorial pass."""
        return self.scan(text)

    def format_flags(self, flags: list[Flag]) -> str:
        if not flags:
            return ""
        lines = []
        for flag in flags:
            lines.append(f"- {flag.pattern_type} ({flag.confidence:.2f}): {flag.passage}")
        return "\n".join(lines)

    def _evaluate(self, pattern_type: str, passage: str, window: str) -> _Candidate | None:
        if pattern_type == "fake-statistic":
            return self._score_fake_statistic(passage, window)
        if pattern_type == "fake-legal":
            return self._score_fake_legal(passage, window)
        if pattern_type == "fake-historical":
            return self._score_fake_historical(passage, window)
        if pattern_type == "fake-function-name":
            return self._score_fake_function_name(passage, window)
        if pattern_type == "plausible-over-true":
            return self._score_plausible_over_true(passage, window)
        return None

    def _score_fake_statistic(self, passage: str, window: str) -> _Candidate | None:
        stats = tuple(match.group(0) for match in self._STATISTIC_RE.finditer(passage))
        precise = tuple(match.group(0) for match in self._PRECISE_STAT_RE.finditer(passage))
        if not stats and not precise:
            return None
        if self._has_source_signal(window):
            return None
        has_claim = bool(self._STAT_CLAIM_RE.search(passage))
        if precise and has_claim:
            confidence = 0.94
        elif len(stats) >= 2 and has_claim:
            confidence = 0.9
        elif precise and len(stats) >= 2:
            confidence = 0.88
        else:
            return None
        return _Candidate(
            confidence=confidence,
            reason="precise statistic is presented as factual without a nearby source",
            triggers=self._first_unique(precise + stats),
        )

    def _score_fake_legal(self, passage: str, window: str) -> _Candidate | None:
        legal = self._LEGAL_RE.search(passage)
        citation = self._LEGAL_CITATION_RE.search(passage)
        if not legal and not citation:
            return None
        if self._has_source_signal(window):
            return None
        assertion = self._LEGAL_ASSERTION_RE.search(passage)
        jurisdiction = self._JURISDICTION_RE.search(passage)
        if citation:
            confidence = 0.93
        elif assertion and jurisdiction:
            confidence = 0.91
        elif assertion and re.search(r"\bunder\b|(?:根据|按照)", passage, re.IGNORECASE):
            confidence = 0.9
        else:
            return None
        return _Candidate(
            confidence=confidence,
            reason="legal or regulatory claim needs source verification",
            triggers=self._trigger_hits("fake-legal", passage),
        )

    def _score_fake_historical(self, passage: str, window: str) -> _Candidate | None:
        full_date = self._FULL_DATE_RE.search(passage)
        year_hits = tuple(match.group(0) for match in self._YEAR_RE.finditer(passage))
        if not full_date and not year_hits:
            return None
        if self._has_source_signal(window):
            return None
        event = self._HISTORY_EVENT_RE.search(passage)
        named_entity = self._NAMED_ENTITY_RE.search(passage)
        if full_date and event:
            confidence = 0.92
        elif year_hits and event and named_entity:
            confidence = 0.89
        else:
            return None
        triggers = (full_date.group(0),) if full_date else year_hits[:3]
        return _Candidate(
            confidence=confidence,
            reason="specific historical date or event claim lacks nearby corroboration",
            triggers=self._first_unique(triggers),
        )

    def _score_fake_function_name(self, passage: str, window: str) -> _Candidate | None:
        function_names = tuple(match.group(0).strip("`") for match in self._FUNCTION_NAME_RE.finditer(passage))
        if not function_names:
            return None
        if self._has_source_signal(window):
            return None
        context = self._API_CONTEXT_RE.search(passage)
        behavior = self._API_BEHAVIOR_RE.search(passage)
        dotted = any("." in name for name in function_names)
        if context and behavior:
            confidence = 0.91
        elif context and len(function_names) >= 2:
            confidence = 0.89
        elif behavior and dotted:
            confidence = 0.89
        else:
            return None
        return _Candidate(
            confidence=confidence,
            reason="API or function-name claim should be checked against current docs",
            triggers=self._first_unique(function_names[:4]),
        )

    def _score_plausible_over_true(self, passage: str, window: str) -> _Candidate | None:
        authority = self._AUTHORITY_PHRASE_RE.search(passage)
        certainty_hits = tuple(match.group(0) for match in self._CERTAINTY_RE.finditer(passage))
        if not authority and not certainty_hits:
            return None
        if self._has_source_signal(window) or self._HEDGE_RE.search(passage):
            return None
        generalizing = re.search(
            r"\b(?:all|every|always|never|everyone|experts|researchers|studies|"
            r"companies|users|markets|people)\b|(?:所有|总是|从不|专家|研究|用户|市场)",
            passage,
            re.IGNORECASE,
        )
        if authority:
            confidence = 0.91
            triggers = (authority.group(0),) + certainty_hits[:2]
        elif len(certainty_hits) >= 2 and generalizing:
            confidence = 0.89
            triggers = certainty_hits[:3]
        else:
            return None
        return _Candidate(
            confidence=confidence,
            reason="fluent certainty or authority phrasing is doing source-like work",
            triggers=self._first_unique(triggers),
        )

    def _has_source_signal(self, text: str) -> bool:
        return bool(self._SOURCE_SIGNAL_RE.search(text))

    def _iter_passages(self, text: str) -> list[tuple[int, int, str]]:
        masked = self._mask_fenced_code(text)
        passages: list[tuple[int, int, str]] = []
        for match in self._SENTENCE_RE.finditer(masked):
            raw = text[match.start() : match.end()]
            passage = raw.strip()
            if passage:
                passages.append((match.start(), match.end(), passage))
        return passages

    def _mask_fenced_code(self, text: str) -> str:
        lines = text.splitlines(keepends=True)
        masked: list[str] = []
        in_fence = False
        fence_marker = ""
        for line in lines:
            stripped = line.lstrip()
            starts_fence = stripped.startswith("```") or stripped.startswith("~~~")
            if starts_fence:
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                masked.append(" " * len(line))
                continue
            masked.append(" " * len(line) if in_fence else line)
        return "".join(masked)

    def _window(self, text: str, start: int, end: int) -> str:
        return text[max(0, start - 240) : min(len(text), end + 240)]

    def _trigger_hits(self, pattern_type: str, passage: str) -> tuple[str, ...]:
        rule = self.taxonomy[pattern_type]
        hits: list[str] = []
        for pattern in rule.regex_triggers:
            match = pattern.search(passage)
            if match:
                hits.append(match.group(0))
        lower = passage.lower()
        for keyword in rule.keyword_triggers:
            if keyword.lower() in lower:
                hits.append(keyword)
        return self._first_unique(tuple(hits))

    def _first_unique(self, values: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            cleaned = " ".join(value.split())[:80]
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                unique.append(cleaned)
        return tuple(unique)

    def _dedupe(self, flags: list[Flag]) -> list[Flag]:
        deduped: list[Flag] = []
        seen: set[tuple[str, int, int]] = set()
        for flag in sorted(flags, key=lambda item: (item.start, item.pattern_type, -item.confidence)):
            key = (flag.pattern_type, flag.start, flag.end)
            if key not in seen:
                seen.add(key)
                deduped.append(flag)
        return deduped


_DEFAULT_GUARD = PlausibilityGuard()


def scan(text: str) -> list[Flag]:
    return _DEFAULT_GUARD.scan(text)


def content_guard_hook(text: str) -> tuple[bool, float, list[Flag]]:
    return _DEFAULT_GUARD.content_guard_hook(text)


def content_looks_like_error_hook(text: str) -> tuple[bool, float]:
    return _DEFAULT_GUARD.content_looks_like_error_hook(text)


def writer_editorial_pass_hook(text: str) -> list[Flag]:
    return _DEFAULT_GUARD.writer_editorial_pass_hook(text)
