"""Editorial packaging and quality gates for Substack articles."""

from __future__ import annotations

import re

from models import EditorialPackage, PublicationStrategy, TopicCandidate


_GENERIC_TITLE_WORDS = {
    "thoughts",
    "reflections",
    "insights",
    "lessons",
    "framework",
    "guide",
    "notes",
}


def _clean_sentence(text: str, *, max_chars: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" -:\n\t")
    if len(cleaned) <= max_chars:
        return cleaned
    sentence_cut = max(
        cleaned.rfind(".", 0, max_chars), cleaned.rfind("。", 0, max_chars), cleaned.rfind("!", 0, max_chars)
    )
    if sentence_cut >= 80:
        return cleaned[: sentence_cut + 1]
    cut = cleaned[:max_chars].rsplit(" ", 1)[0]
    cut = re.sub(r"\b(?:it|it's|is|are|the|a|an|and|or|but)$", "", cut, flags=re.IGNORECASE).strip()
    return cut.rstrip(" .,;:") + "."


def _shorten_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) <= 72:
        return title
    return title[:72].rsplit(" ", 1)[0].strip(" -:")


def _title_candidates(topic: TopicCandidate) -> list[str]:
    base = _shorten_title(topic.title)
    thesis = topic.thesis.strip()
    candidates = [base]
    lower = f"{topic.title} {topic.thesis}".lower()

    if "mira" in lower or "self-improvement" in lower:
        candidates.append("My Self-Improvement Was Fake")
    if "watch" in lower or "automated" in lower:
        candidates.append("It Knows You're Not Watching")
    if "reliability" in lower or "verification" in lower or "done" in lower:
        candidates.append("Done Is Not A Status")
        candidates.append("The Agent Has To Prove It Happened")
    if "market" in lower or "hayek" in lower or "price" in lower:
        candidates.append("Hayek's Blind Spot")
    if thesis:
        first_clause = re.split(r"[.;:，。]", thesis, maxsplit=1)[0]
        if 18 <= len(first_clause) <= 80:
            candidates.append(_shorten_title(first_clause))

    seen = set()
    out = []
    for candidate in candidates:
        normalized = candidate.lower()
        if candidate and normalized not in seen:
            seen.add(normalized)
            out.append(candidate)
    return out[:5]


def _score_title(title: str) -> float:
    title_words = {word.lower().strip(":'`") for word in re.findall(r"[\w'`]+", title)}
    score = 5.0
    if 18 <= len(title) <= 64:
        score += 2.0
    if any(word in title.lower() for word in ("not", "fake", "failure", "watching", "prove", "dangerous")):
        score += 1.5
    if title_words & _GENERIC_TITLE_WORDS:
        score -= 2.0
    return min(max(score, 0.0), 10.0)


def _subject_lines(title_candidates: list[str], topic: TopicCandidate) -> list[str]:
    lines = []
    for title in title_candidates:
        if len(title) <= 58:
            lines.append(title)
    if topic.pillar == "Agent reliability":
        lines.extend(
            [
                "The agent looked busy. It wasn't.",
                "Why `done` is the dangerous state",
                "The failure mode hiding in plain sight",
            ]
        )
    lines.append("What broke this week inside Mira")
    seen = set()
    result = []
    for line in lines:
        normalized = line.lower()
        if 24 <= len(line) <= 64 and normalized not in seen:
            seen.add(normalized)
            result.append(line)
    return result[:6]


def _abstract(topic: TopicCandidate, strategy: PublicationStrategy) -> str:
    thesis = _public_thesis(topic.thesis)
    edge = _clean_sentence(topic.mira_edge, max_chars=160)
    if "avoid generic" in edge.lower():
        edge = "The argument is grounded in Mira's own operating evidence rather than outside commentary."
    return (
        f"{thesis} I use Mira's own failures, status gaps, and verification mistakes as the evidence, because the "
        f"interesting question is not whether agents can sound competent; it is whether they can prove the work "
        f"actually happened. {edge} The payoff is a practical standard for judging agent reliability before trust "
        f"turns into babysitting."
    )[:900]


def _public_thesis(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" -")
    cleaned = re.sub(r"^Connection:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+-\s+Source:.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^Source:\s*[^.。]+[.。]?\s*", "", cleaned, flags=re.IGNORECASE)
    if len(cleaned) < 40:
        cleaned = (
            "Reliable agents fail in a specific way: the interface can look settled while the outcome remains unproven."
        )
    return _clean_sentence(cleaned, max_chars=330)


def _hooks(topic: TopicCandidate) -> list[str]:
    title = topic.title.strip()
    return [
        "The most dangerous agent status is not failed. It is done.",
        "Mira looked busy for hours, and that was the bug.",
        f"The article started as a simple question: why did `{title}` feel finished when nothing had been proven?",
        "A reliable agent should be more afraid of unverifiable success than visible failure.",
        "The first clue was not an error message. It was silence.",
    ]


def _format_blueprint(topic: TopicCandidate) -> list[dict[str, str]]:
    return [
        {
            "section": "Hook",
            "job": "Open with a concrete Mira failure or violated assumption. No context-setting.",
            "target": "1 sentence + 2-3 sentence lede",
        },
        {
            "section": "The Broken Scene",
            "job": "Describe the observable failure: what the app showed, what actually happened, and why the gap mattered.",
            "target": "250-400 words",
        },
        {
            "section": "The General Claim",
            "job": f"Turn the scene into the article's thesis: {topic.thesis[:220]}",
            "target": "400-600 words",
        },
        {
            "section": "Mechanism",
            "job": "Explain the system mechanism: state, verification, routing, memory, incentives, or feedback loops.",
            "target": "600-900 words",
        },
        {
            "section": "What A Better Agent Would Do",
            "job": "Give a concrete framework or checklist readers can use on their own agent systems.",
            "target": "400-700 words",
        },
        {
            "section": "Close",
            "job": "End on a specific unresolved tension or next experiment, not a summary paragraph.",
            "target": "150-250 words",
        },
    ]


def score_editorial_package(package: EditorialPackage, topic: TopicCandidate) -> tuple[dict[str, float], list[str]]:
    """Score the package before a topic may move into drafting."""
    title = package.recommended_title
    title_intrigue = _score_title(title)

    abstract_clarity = 5.0
    if 180 <= len(package.abstract) <= 900:
        abstract_clarity += 2.0
    if "Mira" in package.abstract:
        abstract_clarity += 1.0
    if "reliable" in package.abstract.lower() or "failure" in package.abstract.lower():
        abstract_clarity += 1.0

    format_strength = 4.0 + min(len(package.format_blueprint), 6)
    mira_specificity = 4.0
    text = f"{package.abstract} {' '.join(package.hook_candidates)} {topic.mira_edge}"
    if "Mira" in text:
        mira_specificity += 2.0
    if any(token in text.lower() for token in ("own operating evidence", "failure", "app", "status")):
        mira_specificity += 1.5

    scores = {
        "title_intrigue": round(min(max(title_intrigue, 0.0), 10.0), 2),
        "abstract_clarity": round(min(max(abstract_clarity, 0.0), 10.0), 2),
        "format_strength": round(min(max(format_strength, 0.0), 10.0), 2),
        "mira_specificity": round(min(max(mira_specificity, 0.0), 10.0), 2),
    }
    blocking = []
    if scores["title_intrigue"] < 7:
        blocking.append("Title is not intriguing enough for email/Substack discovery.")
    if scores["abstract_clarity"] < 7:
        blocking.append("Abstract does not clearly promise a specific reader payoff.")
    if scores["format_strength"] < 8:
        blocking.append("Article format is not structured enough for a strong essay.")
    if scores["mira_specificity"] < 7:
        blocking.append("Package lacks Mira-specific evidence; risk of generic AI commentary.")
    return scores, blocking


def build_editorial_package(topic: TopicCandidate, strategy: PublicationStrategy) -> EditorialPackage:
    """Build title, abstract, hooks, and format requirements for one topic."""
    titles = _title_candidates(topic)
    recommended = max(titles, key=_score_title)
    package = EditorialPackage(
        topic_id=topic.id,
        recommended_title=recommended,
        subject_line_candidates=_subject_lines(titles, topic),
        abstract=_abstract(topic, strategy),
        hook_candidates=_hooks(topic),
        format_blueprint=_format_blueprint(topic),
        quality_scores={},
        pass_gate=False,
    )
    scores, blocking = score_editorial_package(package, topic)
    package.quality_scores = scores
    package.blocking_reasons = blocking
    package.pass_gate = not blocking
    return package


def build_editorial_packages(topics: list[TopicCandidate], strategy: PublicationStrategy, *, limit: int = 10):
    return [build_editorial_package(topic, strategy) for topic in topics[:limit]]
