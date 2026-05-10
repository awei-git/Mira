"""Surface-quality scoring for generated diffs."""

from __future__ import annotations

import keyword
import re

_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_COMMENT_RE = re.compile(r"^\s*(#|//|/\*|\*|\*/)")
_LOGIC_RE = re.compile(
    r"\b(if|elif|else|for|while|try|except|finally|return|yield|raise|with|assert|match|case|switch)\b"
    r"|==|!=|<=|>=|&&|\|\||\b(and|or|not|is|in)\b"
)
_STRUCTURE_RE = re.compile(r"\b(def|class|function|const|let|var|return|import|from)\b|[{}:]")
_RESERVED = set(keyword.kwlist) | {
    "true",
    "false",
    "null",
    "none",
    "undefined",
    "const",
    "let",
    "var",
    "function",
    "return",
    "import",
    "from",
}


def _changed_lines(diff_text: str) -> list[str]:
    lines: list[str] = []
    for line in (diff_text or "").splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
    if lines:
        return [line for line in lines if line.strip()]
    return [line for line in (diff_text or "").splitlines() if line.strip()]


def _indentation_score(lines: list[str]) -> float:
    code_lines = [line for line in lines if line.strip() and not _COMMENT_RE.match(line)]
    if not code_lines:
        return 0.0

    indents = [len(line) - len(line.lstrip(" ")) for line in code_lines if line.startswith(" ")]
    tabbed = sum(1 for line in code_lines if line.startswith("\t"))
    trailing = sum(1 for line in code_lines if line.rstrip() != line)
    if not indents and not tabbed:
        return 0.6

    positive = [indent for indent in indents if indent > 0]
    unit = min(positive) if positive else 4
    if unit <= 0:
        unit = 4
    inconsistent = sum(1 for indent in positive if indent % unit != 0)
    mixed = tabbed if indents and tabbed else 0
    penalties = inconsistent + mixed + trailing
    return max(0.0, min(1.0, 1.0 - (penalties / max(1, len(code_lines)))))


def _identifier_score(lines: list[str]) -> float:
    identifiers: list[str] = []
    for line in lines:
        for token in _IDENTIFIER_RE.findall(line):
            lower = token.lower()
            if lower not in _RESERVED and not token.isupper():
                identifiers.append(token)
    if not identifiers:
        return 0.0

    descriptive = 0
    for token in identifiers:
        if "_" in token and len(token.replace("_", "")) >= 4:
            descriptive += 1
        elif len(token) >= 5:
            descriptive += 1
        elif len(token) >= 3 and not token.lower() in {"tmp", "foo", "bar", "baz"}:
            descriptive += 0.5
    return max(0.0, min(1.0, descriptive / len(identifiers)))


def _comment_density_score(lines: list[str]) -> float:
    meaningful = [line for line in lines if line.strip()]
    if not meaningful:
        return 0.0
    comments = sum(1 for line in meaningful if _COMMENT_RE.match(line))
    ratio = comments / len(meaningful)
    if ratio == 0:
        return 0.45
    if ratio <= 0.25:
        return 0.75 + ratio
    return max(0.0, 1.0 - ((ratio - 0.25) / 0.5))


def _structure_score(lines: list[str]) -> float:
    if not lines:
        return 0.0
    logic_hits = sum(1 for line in lines if _LOGIC_RE.search(line))
    structure_hits = sum(1 for line in lines if _STRUCTURE_RE.search(line))
    blank_separators = sum(1 for line in lines if not line.strip())
    density = min(1.0, (logic_hits + structure_hits) / max(1, len(lines) * 0.35))
    grouping = min(1.0, blank_separators / max(1, len(lines) * 0.12))
    return (density * 0.8) + (grouping * 0.2)


def score_diff_surface_quality(diff_text: str) -> float:
    lines = _changed_lines(diff_text)
    if not lines:
        return 0.0

    score = (
        _indentation_score(lines) * 0.3
        + _identifier_score(lines) * 0.3
        + _comment_density_score(lines) * 0.15
        + _structure_score(lines) * 0.25
    )
    return round(max(0.0, min(1.0, score)), 2)
