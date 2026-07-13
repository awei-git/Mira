"""Coder agent — debug, review, and short coding tasks.

Focus: find bugs, review code for problems, quick fixes, small scripts.
Long projects and architecture work happen in Claude Code sessions, not here.

The coder agent:
- Has full workspace read/write + command execution via claude_act
- Injects coding-specific skills (debugging, review, error handling, etc.)
- Validates output: checks for syntax errors in generated/modified code
- Prioritizes finding and fixing problems over writing new code
"""

import ast
import difflib
import importlib.util
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

_SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
_LIB_DIR = Path(__file__).resolve().parents[2] / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.append(str(_LIB_DIR))

try:
    import config as _config

    MIRA_DIR = _config.MIRA_DIR
    TASKS_DIR = _config.TASKS_DIR
    _cfg = _config._cfg
    CODER_REQUIRE_HUMAN_REVIEW = getattr(_config, "CODER_REQUIRE_HUMAN_REVIEW", True)
    MIN_DIFF_REVIEW_SECONDS = getattr(_config, "MIN_DIFF_REVIEW_SECONDS", 30)
    CODER_SKEPTICAL_REVIEW = getattr(_config, "CODER_SKEPTICAL_REVIEW", False)
    MAX_AI_CODE_LINES_PER_TASK = getattr(_config, "MAX_AI_CODE_LINES_PER_TASK", 200)
    MAX_AUTO_EDITS_PER_FILE = getattr(_config, "MAX_AUTO_EDITS_PER_FILE", 5)
    AGENT_EDIT_CHURN_THRESHOLD = getattr(_config, "AGENT_EDIT_CHURN_THRESHOLD", 7)
    AGENT_CODE_ENTROPY_ALERT_THRESHOLD = getattr(_config, "AGENT_CODE_ENTROPY_ALERT_THRESHOLD", 0.7)
    CODE_GEN_UNREVIEWED_WARNING_THRESHOLD = getattr(_config, "CODE_GEN_UNREVIEWED_WARNING_THRESHOLD", 500)
except ImportError:
    from config import MIRA_DIR, TASKS_DIR, _cfg

    CODER_REQUIRE_HUMAN_REVIEW = True
    MIN_DIFF_REVIEW_SECONDS = 30
    CODER_SKEPTICAL_REVIEW = False
    MAX_AI_CODE_LINES_PER_TASK = 200
    MAX_AUTO_EDITS_PER_FILE = 5
    AGENT_EDIT_CHURN_THRESHOLD = 7
    AGENT_CODE_ENTROPY_ALERT_THRESHOLD = 0.7
    CODE_GEN_UNREVIEWED_WARNING_THRESHOLD = 500
from agent_code_entropy import is_legibility_risk, record_code_change
from diff_trust_guard import score_diff_surface_quality
from edit_churn import check_churn_threshold, record_agent_edit
from unreviewed_code_tracker import get_unreviewed_total, log_code_change
from memory.soul import load_soul, format_soul, load_skills_for_task
from llm import claude_act, claude_think

try:
    from soul_manager import audit_code_transparency
except ImportError:
    audit_code_transparency = None

try:
    from llm_port import LLMMessage, LLMRequest, get_provider
except ImportError:
    LLMMessage = None
    LLMRequest = None
    get_provider = None

log = logging.getLogger("coder_agent")


def preflight(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Coder preflight: ensure the task has actionable content.

    The coder agent is `local-write` capability and `requires_preflight=True` per
    capability_policy. Without this hook, every routed task got blocked with
    'preflight missing' (root cause of 2026-04-30 Habermas EPUB failure).
    """
    instruction = (content or "").strip()
    if not instruction:
        return False, "PREFLIGHT BLOCKED [coder]: empty instruction"
    return True, ""


_SNAPSHOT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".sql",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
}
_SNAPSHOT_SKIP = {"output.md", "summary.txt", "worker.log", "result.json", "exec_log.jsonl"}
_SNAPSHOT_FILE_LIMIT = 12
_SNAPSHOT_CHAR_LIMIT = 50000
_DIFF_REVIEW_STATE_DIR = TASKS_DIR / "diff_review"
_ACCEPT_DIFF_RE = re.compile(
    r"^\s*(accept|accepted|approve|approved|lgtm|looks good|同意|接受|批准)\s*[.!。！]*\s*$", re.I
)
_HUMAN_APPROVE_RE = re.compile(r"^\s*(approve|approved)\s*[.!。！]*\s*$", re.I)
_HUMAN_REJECT_RE = re.compile(r"^\s*(reject|rejected)\s*[.!。！]*\s*$", re.I)
_HUMAN_REVIEW_POLL_SECONDS = 5.0
_DIFF_MARKERS = ("```diff", "diff --git", "\n--- ", "\n+++ ", "\n@@ ")
_DIFF_PATH_RE = re.compile(r"^diff --git\s+a/(.+?)\s+b/(.+)$|^(?:---|\+\+\+)\s+([ab]/)?(.+)$")
_COMMENT_LINE_RE = re.compile(r"^\s*(#|//|/\*|\*|\*/)")
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_LOGIC_RE = re.compile(
    r"\b(if|elif|else|for|while|try|except|finally|return|yield|raise|break|continue|with|assert|match|case)\b"
    r"|==|!=|<=|>=|&&|\|\||\b(and|or|not|is|in)\b|[+\-*/%]=?|="
)
_BUGFIX_CLAIM_RE = re.compile(
    r"\b(bug|fix|crash|error|fail|fault|logic|control.?flow|incorrect|wrong|security|regression)\b", re.I
)
_BUG_REPORT_HEADING_RE = re.compile(r"\b(bug|vulnerability|vuln|security\s+issue|security\s+flaw)\b", re.I)
_BUG_FIELD_REPRODUCTION_RE = re.compile(
    r"\b(reproduction\s+steps?|to\s+reproduce|steps?\s+to\s+reproduce|repro)\b", re.I
)
_BUG_FIELD_OBSERVED_RE = re.compile(
    r"\b(observed\s+behavior|actual\s+behavior|what\s+happened|actual\s+result)\b", re.I
)
_BUG_FIELD_EXPECTED_RE = re.compile(r"\b(expected\s+behavior|what\s+should|expected\s+result)\b", re.I)
_BUG_FIELD_EVIDENCE_RE = re.compile(r"\b(evidence|file\s+path|line\s+number|test\s+case)\b", re.I)
_COSMETIC_CLAIM_RE = re.compile(
    r"\b(format|formatting|style|whitespace|comment|rename|renaming|refactor|docs?|documentation)\b", re.I
)
_CODE_REVIEW_TASK_RE = re.compile(
    r"\b(code[-_ ]?review|review\s+(?:(?:this|the|that)\s+)?(?:(?:ai[-_ ]?generated|generated)\s+)?"
    r"(?:code|diff|patch|pr|pull request)|(?:audit|inspect)\s+(?:(?:this|the|that)\s+)?"
    r"(?:(?:ai[-_ ]?generated|generated)\s+)?(?:code|diff|patch)|pr\s+review|pull request review)\b",
    re.I,
)
_CODE_REVIEW_TYPE_RE = re.compile(r"\b(code[-_ ]?review|review|audit)\b", re.I)
_CODE_AUDIT_CHECKLIST = Path(__file__).resolve().parent / "skills" / "code_audit_checklist.md"
_AUDIT_MODE_CHECKLIST = Path(__file__).resolve().parent / "checklists" / "audit-mode.md"
_AUDIT_MODE_TYPE_RE = re.compile(r"\b(code[-_ ]?review|review|audit|debug)\b", re.I)
_DIFF_TRUST_DEFAULT_THRESHOLD = 0.7
_AUDIT_SAFE_RE = re.compile(r"^\s*SAFE\s*$", re.I)
_DIFF_TRUST_WARNING = (
    "⚠️  This diff reads smoothly; trust inflation risk — verify logical correctness, " "not just surface readability."
)
_NO_TEST_DIFF_WARNING = (
    "CAUTION: This code change does not modify any test files. The diff may appear coherent "
    "but could contain subtle errors. Please review thoroughly."
)
_CODE_ENTROPY_ALERT_MESSAGE = (
    "Agent code entropy threshold reached: human review recommended before more auto-modifications"
)
_TEACH_BACK_HEADING_RE = re.compile(r"^#{2,6}\s+(?:Teach-Back|Feynman Comprehension)\s*$", re.I | re.M)
_NEXT_HEADING_RE = re.compile(r"^#{2,6}\s+\S", re.M)
_FEYNMAN_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "before",
        "between",
        "could",
        "from",
        "have",
        "into",
        "only",
        "other",
        "should",
        "that",
        "their",
        "there",
        "this",
        "through",
        "under",
        "when",
        "where",
        "which",
        "with",
        "without",
        "would",
    }
)
_FEYNMAN_TRADEOFF_RE = re.compile(
    r"\b(?:because|tradeoff|trade-off|rather than|instead of|avoid|preserve|constraint|risk|chosen|balance)\b",
    re.I,
)
_FEYNMAN_REPRODUCE_RE = re.compile(
    r"\b(?:approach|first|then|so that|ensure|invariant|input|output|edge case|fallback|validate|verify)\b",
    re.I,
)
_MIRA_ROOT = Path(__file__).resolve().parents[2]
_EDIT_LEDGER_PATH = _MIRA_ROOT / "logs" / "edit_ledger.json"
_AUTO_EDIT_CODE_SUFFIXES = _SNAPSHOT_SUFFIXES - {".md", ".txt"}
_PROTECTED_PATH_PREFIXES = ("agents/super/", "agents/coder/", "agents/shared/soul/")

# System prompt injected before every coding task
_CODER_SYSTEM = """You are Mira's coding agent. Your primary job: debug, review, and fix code.

## What you do
- **Debug**: Reproduce → locate root cause → fix → verify. Don't guess — bisect.
- **Review**: Read code looking for bugs, security issues, race conditions, error handling gaps. Report what you find with file:line references.
- **Quick fixes**: Small, targeted changes. Read the code first, understand callers, make minimal edits.
- **Short scripts**: Utility scripts, one-off tools, data transforms.

## What you DON'T do
- Large feature development (that's for Claude Code sessions)
- Architecture decisions or rewrites
- Anything that touches 10+ files in one go

## Rules
1. **Read before edit**: Always read the full function AND its callers before changing anything.
2. **Reproduce first**: For bugs, confirm you can reproduce before attempting a fix.
3. **Minimal change**: The smallest diff that fixes the problem. Don't refactor adjacent code.
4. **Verify**: Run the code, run the tests, check syntax. Never claim "fixed" without checking.
5. **No secrets**: Never hardcode API keys, passwords, or credentials in code.
6. **Report clearly**: State what you found, what you changed, and what to watch for.
7. **Diff integrity checklist (mandatory):** When you present a code change (as a diff or patch), you must include a section titled `## Verification Steps`. This section must list 2-3 specific, actionable checks that a reviewer can perform to verify the correctness of the change (e.g., 'Confirm that the function returns early when input is None', not 'Check that the code looks good'). The checklist must focus on logic, edge cases, and side effects—not formatting or style.
8. **Teach-back gate (mandatory):** When you generate or recommend code changes, include `## Teach-Back`: explain the solution logic from the problem statement in plain language, including tradeoffs and enough steps to reproduce the approach without seeing the code. Do not walk through the diff line by line.
9. **Code transparency audit (mandatory):** When you generate or recommend code changes, include these required sections: `## Edge Cases Considered`, `## Boundary Conditions`, and `## Assumptions Made`. Each section must contain explicit, non-empty disclosure. Missing or empty sections are flagged by `soul_manager.audit_code_transparency()`.
10. **Structured rationale comments (mandatory):** For every code change you make, insert a comment on the line above or at the end of the changed block formatted as:
`// AGENT-RATIONALE: <why this change is needed, what tradeoffs were considered, and any assumptions made>`
This applies to all edits, including one-line fixes. Never leave a code edit unexplained.

## Output format
For debug/review tasks, structure your response as:
- **Reverse spec**: For code reviews, before evaluating correctness, concisely describe what the code should do based on context, docstrings, function names, or in-file comments, then describe how the code attempts to do it.
- **Problem**: What's wrong (with file:line)
- **Root cause**: Why it happens
- **Fix**: What you changed (or recommend)
- **Verification**: How you confirmed it works

For every code review, append this mandatory section:

## Review Depth Evidence
### Execution Trace
Walk one complete execution path from input to output using concrete values and expected inputs/outputs.

### Edge Case Analysis
Identify and analyze at least one non-trivial edge case or failure mode and the expected behavior.

### Surprisal Check
Note anything that seemed unexpected, ambiguous, confusing, or potentially wrong during review.

If any Review Depth Evidence subsection is missing or contains only vague/generic content such as "looks fine" or "no issues", the review is incomplete and the change is blocked.

## BUG REPORT FORMAT (MANDATORY)
Every bug or vulnerability finding MUST include:
(a) **REPRODUCTION STEPS** — minimal sequence to trigger the issue
(b) **OBSERVED BEHAVIOR** — what actually happened with exact output/logs
(c) **EXPECTED BEHAVIOR** — what should have happened
(d) **EVIDENCE** — file paths, line numbers, or test case

If you cannot provide all four, explicitly state what's missing and downgrade the finding from BUG to OBSERVATION.
"""


def _is_code_review_task(content: str, task_type: str = "") -> bool:
    if _CODE_REVIEW_TASK_RE.search(content or ""):
        return True
    return bool(task_type and _CODE_REVIEW_TYPE_RE.search(task_type))


def _is_audit_mode_task(content: str, task_type: str = "") -> bool:
    return _is_code_review_task(content, task_type) or bool(task_type and _AUDIT_MODE_TYPE_RE.search(task_type))


def _load_code_audit_checklist() -> str:
    try:
        return _CODE_AUDIT_CHECKLIST.read_text(encoding="utf-8").strip()
    except OSError as e:
        log.warning("Could not load code audit checklist from %s: %s", _CODE_AUDIT_CHECKLIST, e)
        return ""


def _load_audit_mode_checklist() -> str:
    try:
        return _AUDIT_MODE_CHECKLIST.read_text(encoding="utf-8").strip()
    except OSError as e:
        log.warning("Could not load audit-mode checklist from %s: %s", _AUDIT_MODE_CHECKLIST, e)
        return ""


def _snapshot_workspace(workspace: Path) -> str:
    """Serialize a compact view of the task workspace for think-only fallback."""
    if not workspace.exists():
        return "Workspace does not exist."

    files = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _SNAPSHOT_SKIP:
            continue
        if path.suffix.lower() not in _SNAPSHOT_SUFFIXES:
            continue
        files.append(path)
        if len(files) >= _SNAPSHOT_FILE_LIMIT:
            break

    blocks = []
    total = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(workspace)
        block = f"## FILE: {rel}\n{text[:6000]}"
        if total + len(block) > _SNAPSHOT_CHAR_LIMIT and blocks:
            break
        blocks.append(block)
        total += len(block)

    return "\n\n".join(blocks) if blocks else "No readable code files in workspace."


def _diff_review_state_path(thread_id: str, task_id: str) -> Path:
    key = thread_id or task_id
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("._") or task_id
    return _DIFF_REVIEW_STATE_DIR / f"{safe_key}.json"


def _looks_like_diff_presentation(text: str) -> bool:
    return bool(text and any(marker in text for marker in _DIFF_MARKERS))


def _normalize_mira_path(path: str) -> str:
    raw = str(path or "").strip().strip("\"'")
    if not raw or raw == "/dev/null":
        return ""
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    try:
        path_obj = Path(raw).expanduser()
        if path_obj.is_absolute():
            raw = str(path_obj.resolve().relative_to(_MIRA_ROOT))
    except (OSError, ValueError):
        pass
    raw = raw.replace("\\", "/").lstrip("./")
    if raw.startswith("Mira/"):
        raw = raw[len("Mira/") :]
    return raw


def _is_protected_path(path: str) -> bool:
    normalized = _normalize_mira_path(path)
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in _PROTECTED_PATH_PREFIXES)


def _extract_diff_paths(diff_text: str) -> list[str]:
    paths: set[str] = set()
    for line in (diff_text or "").splitlines():
        match = _DIFF_PATH_RE.match(line.strip())
        if not match:
            continue
        candidates = [match.group(1), match.group(2), match.group(4)]
        for candidate in candidates:
            normalized = _normalize_mira_path(candidate or "")
            if normalized:
                paths.add(normalized)
    return sorted(paths)


def send_confirmation(diff_summary: str, affected_paths: list[str]) -> str:
    paths = "\n".join(f"- {path}" for path in affected_paths)
    return (
        "NEEDS_APPROVAL: This diff touches protected Mira system files. "
        "Reply approve to apply it.\n\n"
        f"{diff_summary.strip()}\n\n"
        f"Affected paths:\n{paths}"
    )


def _diff_trust_threshold() -> float:
    diff_trust = _cfg.get("diff_trust", {}) if isinstance(_cfg, dict) else {}
    raw_threshold = (
        diff_trust.get("surface_quality_threshold", _DIFF_TRUST_DEFAULT_THRESHOLD)
        if isinstance(diff_trust, dict)
        else _DIFF_TRUST_DEFAULT_THRESHOLD
    )
    try:
        threshold = float(raw_threshold)
    except (TypeError, ValueError):
        threshold = _DIFF_TRUST_DEFAULT_THRESHOLD
    return max(0.0, min(1.0, threshold))


def _diff_trust_warning_needed(diff_text: str) -> bool:
    return score_diff_surface_quality(diff_text) > _diff_trust_threshold()


def _strip_inline_comment(line: str) -> str:
    return re.split(r"\s+(#|//)", line, maxsplit=1)[0]


def _name_shape(line: str) -> str:
    return _IDENTIFIER_RE.sub("NAME", _strip_inline_comment(line)).strip()


def _is_cosmetic_change(original_line: str, modified_line: str = "") -> bool:
    original = original_line.strip()
    modified = modified_line.strip()
    if not original and not modified:
        return True
    if _COMMENT_LINE_RE.match(original) or _COMMENT_LINE_RE.match(modified):
        return True
    if re.sub(r"\s+", "", original_line) == re.sub(r"\s+", "", modified_line):
        return True
    if _strip_inline_comment(original_line).strip() == _strip_inline_comment(modified_line).strip():
        return True
    return bool(original and modified and _name_shape(original_line) == _name_shape(modified_line))


def _diff_plausibility_score(original: str, modified: str, description: str) -> float:
    """Score whether a diff is mostly cosmetic despite claiming a functional fix."""
    original_lines = (original or "").splitlines()
    modified_lines = (modified or "").splitlines()
    matcher = difflib.SequenceMatcher(a=original_lines, b=modified_lines)
    cosmetic_lines = 0
    semantic_lines = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = original_lines[i1:i2]
        after = modified_lines[j1:j2]
        changed_count = max(len(before), len(after))
        for idx in range(changed_count):
            original_line = before[idx] if idx < len(before) else ""
            modified_line = after[idx] if idx < len(after) else ""
            if _is_cosmetic_change(original_line, modified_line):
                cosmetic_lines += 1
            elif _LOGIC_RE.search(original_line) or _LOGIC_RE.search(modified_line):
                semantic_lines += 1
            else:
                semantic_lines += 1

    total_changed_lines = cosmetic_lines + semantic_lines
    if total_changed_lines == 0:
        return 0.0

    cosmetic_ratio = cosmetic_lines / total_changed_lines
    claimed_bugfix = bool(_BUGFIX_CLAIM_RE.search(description or ""))
    claimed_cosmetic = bool(_COSMETIC_CLAIM_RE.search(description or ""))

    if claimed_bugfix:
        score = 0.75 + (0.25 * cosmetic_ratio) if semantic_lines == 0 else (cosmetic_ratio - 0.35) / 0.65
    elif claimed_cosmetic:
        score = ((cosmetic_ratio - 0.85) / 0.15) * 0.3
    else:
        score = (cosmetic_ratio - 0.55) / 0.45

    return round(max(0.0, min(1.0, score)), 2)


def _record_diff_presented(thread_id: str, task_id: str, workspace: Path, result: str) -> None:
    if not _looks_like_diff_presentation(result):
        return
    affected_paths = _extract_diff_paths(result)
    state = {
        "presented_at": time.time(),
        "thread_id": thread_id,
        "task_id": task_id,
        "workspace": str(workspace),
        "affected_paths": affected_paths,
        "protected_paths": [path for path in affected_paths if _is_protected_path(path)],
    }
    try:
        _DIFF_REVIEW_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _diff_review_state_path(thread_id, task_id).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        log.info("Diff review timer started for task %s thread %s", task_id, thread_id or task_id)
    except OSError as e:
        log.warning("Could not record diff review timestamp for task %s: %s", task_id, e)


def _diff_acceptance_guard(content: str, thread_id: str, task_id: str) -> str | None:
    if not _ACCEPT_DIFF_RE.match(content or ""):
        return None
    try:
        minimum = float(MIN_DIFF_REVIEW_SECONDS)
    except (TypeError, ValueError):
        minimum = 30.0
    if minimum <= 0:
        return None

    state_path = _diff_review_state_path(thread_id, task_id)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        presented_at = float(state.get("presented_at", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    elapsed = time.time() - presented_at
    if elapsed >= minimum:
        protected_paths = [path for path in state.get("protected_paths", []) if _is_protected_path(path)]
        if not protected_paths:
            return None
        if state.get("protected_confirmation_requested_at"):
            try:
                state_path.unlink()
            except OSError:
                pass
            return None
        state["protected_confirmation_requested_at"] = time.time()
        try:
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("Could not update protected diff confirmation state for task %s: %s", task_id, e)
        diff_summary = f"Task {task_id} has a protected diff pending."
        return send_confirmation(diff_summary, protected_paths)

    remaining = max(1, int(minimum - elapsed + 0.999))
    log.warning(
        "Diff acceptance refused for task %s thread %s: elapsed %.1fs < %.1fs",
        task_id,
        thread_id or task_id,
        elapsed,
        minimum,
    )
    return (
        f"NEEDS_INPUT: I can't accept this diff yet. Please spend at least "
        f"{int(minimum)} seconds reviewing it before accepting; wait {remaining} more seconds, "
        "then reply accept again."
    )


def _coder_skeptical_review_enabled() -> bool:
    if CODER_SKEPTICAL_REVIEW:
        return True
    coder_cfg = _cfg.get("coder", {}) if isinstance(_cfg, dict) else {}
    return bool(coder_cfg.get("skeptical_review", False)) if isinstance(coder_cfg, dict) else False


def _skeptical_review_prompt(intent: str, generated_output: str, generated_snapshot: str) -> str:
    return f"""You are an adversarial code reviewer. Adopt a highly skeptical audit mindset.

Your job is to find high-severity problems in generated code: edge cases, hidden bugs,
incorrect assumptions, regressions, security issues, runtime failures, data loss risks,
and cases where the implementation does not satisfy the user's intent.

Use only the original intent and the generated code/output below. Do not rely on the
generation conversation, persona, prior reasoning, or any hidden context.

Return JSON only:
{{
  "decision": "pass" | "revise" | "abort",
  "high_severity_issues": [
    {{"summary": "...", "evidence": "..."}}
  ],
  "rationale": "..."
}}

Use "revise" or "abort" only for high-severity issues. Low or medium severity issues
must not be included in high_severity_issues.

## Original Intent
{intent}

## Generated Output
{generated_output}

## Generated Workspace Snapshot
{generated_snapshot}
"""


def _low_temperature_review(prompt: str) -> str:
    if LLMMessage is None or LLMRequest is None or get_provider is None:
        log.warning("Skeptical review requested but llm_port is unavailable")
        return ""
    try:
        response = get_provider("local").complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content=prompt)],
                model_class="local",
                max_tokens=2048,
                timeout=180,
                metadata={"temperature": 0},
            )
        )
    except Exception as e:
        log.warning("Skeptical review failed: %s", e)
        return ""
    return response.text.strip()


def _extract_review_payload(review_text: str) -> dict | None:
    text = (review_text or "").strip()
    candidates = [text]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        candidates.insert(0, fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _high_severity_review_issues(review_text: str) -> list[str]:
    payload = _extract_review_payload(review_text)
    if payload:
        raw_issues = payload.get("high_severity_issues", [])
        issues = []
        if isinstance(raw_issues, list):
            for issue in raw_issues:
                if isinstance(issue, dict):
                    summary = str(issue.get("summary", "")).strip()
                    evidence = str(issue.get("evidence", "")).strip()
                    text = f"{summary}: {evidence}".strip(": ")
                else:
                    text = str(issue).strip()
                if text:
                    issues.append(text)
        decision = str(payload.get("decision", "")).strip().lower()
        if issues:
            return issues
        if decision in {"revise", "abort"}:
            rationale = str(payload.get("rationale", "Skeptical review requested revision.")).strip()
            return [rationale]
        return []

    issues = []
    for line in (review_text or "").splitlines():
        if re.search(r"\b(high[-_ ]severity|critical|blocker)\b", line, re.I):
            issues.append(line.strip())
    return issues


def _skeptical_revision_prompt(workspace: Path, intent: str, review_text: str) -> str:
    return f"""{_CODER_SYSTEM}

A fresh skeptical review found high-severity issues in the generated code.
Revise the workspace only to address those issues. If the issues cannot be fixed safely,
write a warning to {workspace}/output.md and do not claim completion.

## Original Intent
{intent}

## Skeptical Review Findings
{review_text}

Work in: {workspace}
Write results to {workspace}/output.md when done.
"""


def _format_skeptical_abort(reason: str, issues: list[str] | None = None, review_text: str = "") -> str:
    details = ""
    if issues:
        details = "\n\nHigh-severity issues:\n" + "\n".join(f"- {issue}" for issue in issues[:5])
    if review_text:
        details += f"\n\nReview output:\n{review_text.strip()}"
    return f"WARNING: CODER_SKEPTICAL_REVIEW blocked this coding result: {reason}{details}"


def audit_fix(code: str, bug_description: str, original_code: str, tier: str = "light") -> str:
    prompt = f"""You are in pure audit mode. Review the following proposed code fix skeptically: (1) Does it actually fix the described bug? (2) Does it introduce new bugs, security issues, or performance regressions? (3) Is there any part that only “looks right” but would fail under testing? Report issues. If no issues, output SAFE.

## Described Bug or Task
{bug_description}

## Original Code
{original_code}

## Proposed Code Fix
{code}
"""
    return (claude_think(prompt, timeout=180, tier=tier) or "").strip()


def _audit_passed(audit_result: str) -> bool:
    return bool(_AUDIT_SAFE_RE.match(audit_result or ""))


def _format_code_transparency_warning(result: str, task_id: str) -> str | None:
    if audit_code_transparency is None:
        log.warning("CODE_TRANSPARENCY_AUDIT unavailable for task %s", task_id)
        return (
            "\n\nWARNING: CODE_TRANSPARENCY_AUDIT could not run; required edge-case, "
            "boundary-condition, and assumptions disclosures were not independently checked."
        )

    audit_result = audit_code_transparency(result, agent_name="coder", task_id=task_id)
    if not audit_result.get("warning"):
        return None

    missing = [str(section) for section in audit_result.get("missing_sections", [])]
    empty = [str(section) for section in audit_result.get("empty_sections", [])]
    details = []
    if missing:
        details.append(f"missing: {', '.join(missing)}")
    if empty:
        details.append(f"empty: {', '.join(empty)}")
    return (
        "\n\nWARNING: CODE_TRANSPARENCY_AUDIT flagged this code output. "
        f"Required disclosure sections are incomplete ({'; '.join(details)})."
    )


def _feynman_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", text or "")
        if token.lower() not in _FEYNMAN_STOPWORDS
    }


def _feynman_comprehension_gate(diff: str, explanation: str, problem_statement: str) -> bool:
    explanation = " ".join((explanation or "").split())
    if len(explanation) < 180:
        return False

    explanation_tokens = _feynman_tokens(explanation)
    problem_tokens = _feynman_tokens(problem_statement)
    diff_tokens = _feynman_tokens(diff)
    if not explanation_tokens:
        return False

    problem_overlap = explanation_tokens & problem_tokens
    diff_overlap_ratio = len(explanation_tokens & diff_tokens) / max(len(explanation_tokens), 1)
    references_problem = len(problem_overlap) >= min(3, max(1, len(problem_tokens)))
    states_tradeoffs = bool(_FEYNMAN_TRADEOFF_RE.search(explanation))
    reproducible = len(_FEYNMAN_REPRODUCE_RE.findall(explanation)) >= 2
    diff_paraphrase = diff_overlap_ratio > 0.45 and len(problem_overlap) < 4
    line_walkthrough = bool(re.search(r"\b(?:diff|line|file|function)\b", explanation, re.I)) and not states_tradeoffs
    return references_problem and states_tradeoffs and reproducible and not diff_paraphrase and not line_walkthrough


def _extract_feynman_explanation(text: str) -> str:
    match = _TEACH_BACK_HEADING_RE.search(text or "")
    if not match:
        return ""
    start = match.end()
    next_match = _NEXT_HEADING_RE.search(text, start)
    end = next_match.start() if next_match else len(text)
    return text[start:end].strip()


def _feynman_revision_prompt(base_prompt: str, diff_text: str, result: str) -> str:
    return f"""{base_prompt}

The proposed code change is blocked until you can teach back the approach from the original problem.
Revise output.md to include a section titled `## Teach-Back` that explains the solution logic in plain language.
Do not walk through the diff line by line. Explain the problem constraints, the chosen approach, design tradeoffs,
and enough steps that someone could reproduce the approach without seeing the code. Update the workspace only if
the explanation reveals a real issue with the prior implementation.

## Proposed Diff
{diff_text}

## Previous Output
{result}
"""


def _needs_fix_audit(result: str, original_snapshot: str, modified_snapshot: str) -> bool:
    return original_snapshot != modified_snapshot or _looks_like_diff_presentation(result)


def _capture_review_files(workspace: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    if not workspace.exists():
        return snapshot
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(workspace)
        except ValueError:
            continue
        if any(part in {".git", "__pycache__"} for part in rel.parts):
            continue
        if path.name in _SNAPSHOT_SKIP:
            continue
        if path.suffix.lower() not in _SNAPSHOT_SUFFIXES:
            continue
        try:
            snapshot[rel.as_posix()] = path.read_bytes()
        except OSError:
            continue
    return snapshot


def _changed_review_paths(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _edit_ledger_key(path: Path) -> str:
    try:
        return path.resolve().relative_to(_MIRA_ROOT).as_posix()
    except (OSError, ValueError):
        return path.resolve().as_posix()


def _is_auto_edit_code_path(path: Path) -> bool:
    key = _edit_ledger_key(path)
    return key != "logs/edit_ledger.json" and path.suffix.lower() in _AUTO_EDIT_CODE_SUFFIXES


def _auto_edit_code_paths(workspace: Path, before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return [rel for rel in _changed_review_paths(before, after) if _is_auto_edit_code_path(workspace / rel)]


def _auto_edit_threshold() -> int:
    try:
        return max(0, int(MAX_AUTO_EDITS_PER_FILE))
    except (TypeError, ValueError):
        return 5


def _agent_edit_churn_threshold() -> int:
    try:
        return max(0, int(AGENT_EDIT_CHURN_THRESHOLD))
    except (TypeError, ValueError):
        return 7


def _load_edit_ledger() -> dict:
    try:
        ledger = json.loads(_EDIT_LEDGER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"files": {}}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not load edit ledger %s: %s", _EDIT_LEDGER_PATH, e)
        return {"files": {}}
    if not isinstance(ledger, dict):
        return {"files": {}}
    if isinstance(ledger.get("files"), dict):
        return ledger
    files = {}
    for key, value in ledger.items():
        if isinstance(value, int):
            files[key] = {"auto_edit_count": value, "attempts": []}
        elif isinstance(value, dict):
            files[key] = value
    return {"files": files}


def _edit_count(entry) -> int:
    raw_count = entry.get("auto_edit_count", entry.get("count", 0)) if isinstance(entry, dict) else entry
    try:
        return max(0, int(raw_count))
    except (TypeError, ValueError):
        return 0


def _write_edit_ledger(ledger: dict) -> None:
    _EDIT_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EDIT_LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _append_edit_attempt(
    files: dict,
    key: str,
    task_id: str,
    status: str,
    threshold: int,
    count_before: int,
) -> None:
    entry = files.get(key)
    if not isinstance(entry, dict):
        entry = {"auto_edit_count": _edit_count(entry)}
    attempts = entry.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempt = {
        "task_id": task_id,
        "status": status,
        "threshold": threshold,
        "count_before": count_before,
        "ts": time.time(),
    }
    if status == "applied":
        entry["auto_edit_count"] = count_before + 1
        attempt["count_after"] = count_before + 1
    else:
        entry["auto_edit_count"] = count_before
    attempts.append(attempt)
    entry["attempts"] = attempts
    files[key] = entry


def _auto_edit_limit_refusal(workspace: Path, rel_paths: list[str], task_id: str) -> str | None:
    if not rel_paths:
        return None
    threshold = _auto_edit_threshold()
    ledger = _load_edit_ledger()
    files = ledger.setdefault("files", {})
    if not isinstance(files, dict):
        files = {}
        ledger["files"] = files

    blocked = []
    for rel in rel_paths:
        key = _edit_ledger_key(workspace / rel)
        count = _edit_count(files.get(key))
        if count >= threshold:
            blocked.append((rel, key, count))
    if not blocked:
        return None

    for _rel, key, count in blocked:
        _append_edit_attempt(files, key, task_id, "blocked", threshold, count)
    try:
        _write_edit_ledger(ledger)
    except OSError as e:
        log.warning("Could not write edit ledger %s: %s", _EDIT_LEDGER_PATH, e)

    blocked_summary = ", ".join(f"{rel} ({count}/{threshold})" for rel, _key, count in blocked)
    log.warning("Auto-edit limit blocked task %s for %s", task_id, blocked_summary)
    return (
        f"AUTO_EDIT_LIMIT_REACHED: refused agent-initiated code change for {blocked_summary}. "
        "This file has reached MAX_AUTO_EDITS_PER_FILE; human review is required before further changes."
    )


def _record_auto_edits_applied(workspace: Path, rel_paths: list[str], task_id: str) -> None:
    if not rel_paths:
        return
    threshold = _auto_edit_threshold()
    ledger = _load_edit_ledger()
    files = ledger.setdefault("files", {})
    if not isinstance(files, dict):
        files = {}
        ledger["files"] = files
    for rel in rel_paths:
        key = _edit_ledger_key(workspace / rel)
        _append_edit_attempt(files, key, task_id, "applied", threshold, _edit_count(files.get(key)))
    try:
        _write_edit_ledger(ledger)
    except OSError as e:
        log.warning("Could not write edit ledger %s: %s", _EDIT_LEDGER_PATH, e)


def _agent_edit_churn_refusal(workspace: Path, rel_paths: list[str], agent_id: str) -> str | None:
    if not rel_paths:
        return None
    threshold = _agent_edit_churn_threshold()
    blocked = []
    for rel in rel_paths:
        key = _edit_ledger_key(workspace / rel)
        if not check_churn_threshold(key, threshold=threshold):
            blocked.append(rel)
    if not blocked:
        return None
    blocked_summary = ", ".join(blocked)
    log.warning("Agent edit churn blocked agent %s for %s", agent_id, blocked_summary)
    return (
        f"AGENT_EDIT_CHURN_REVIEW_REQUIRED: refused agent-initiated code change for {blocked_summary}. "
        f"This file has reached AGENT_EDIT_CHURN_THRESHOLD ({threshold}) agent-only edits; "
        "mandatory human review is required before further changes."
    )


def _record_agent_churn_edits(workspace: Path, rel_paths: list[str], agent_id: str) -> None:
    for rel in rel_paths:
        try:
            record_agent_edit(_edit_ledger_key(workspace / rel), agent_id)
        except OSError as e:
            log.warning("Could not record agent edit churn for %s: %s", rel, e)


def _unreviewed_warning_threshold() -> int:
    try:
        return max(0, int(CODE_GEN_UNREVIEWED_WARNING_THRESHOLD))
    except (TypeError, ValueError):
        return 500


def _agent_code_entropy_threshold() -> float:
    try:
        return max(0.0, float(AGENT_CODE_ENTROPY_ALERT_THRESHOLD))
    except (TypeError, ValueError):
        return 0.7


def _code_lines_added_by_path(workspace: Path, before: dict[str, bytes], after: dict[str, bytes]) -> dict[str, int]:
    lines_by_path: dict[str, int] = {}
    for rel in _auto_edit_code_paths(workspace, before, after):
        before_lines = _decode_diff_bytes(before.get(rel))
        after_lines = _decode_diff_bytes(after.get(rel))
        fromfile = f"a/{rel}" if rel in before else "/dev/null"
        tofile = f"b/{rel}" if rel in after else "/dev/null"
        diff_text = "\n".join(
            difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile, lineterm="")
        )
        lines_added = _count_added_lines(diff_text)
        if lines_added > 0:
            lines_by_path[_edit_ledger_key(workspace / rel)] = lines_added
    return lines_by_path


def _track_unreviewed_code_changes(
    workspace: Path,
    before: dict[str, bytes],
    after: dict[str, bytes],
    agent_id: str,
    task_id: str,
) -> None:
    lines_by_path = _code_lines_added_by_path(workspace, before, after)
    if not lines_by_path:
        return
    try:
        for file_path, lines_added in lines_by_path.items():
            log_code_change(file_path, lines_added, agent_id)
        unreviewed_total = get_unreviewed_total()
    except Exception as e:
        log.warning("Could not update unreviewed code tracker for task %s: %s", task_id, e)
        return

    threshold = _unreviewed_warning_threshold()
    if unreviewed_total > threshold:
        log.warning(
            "CODE_GEN_UNREVIEWED_WARNING: %s unreviewed AI-generated code lines exceed threshold %s after task %s",
            unreviewed_total,
            threshold,
            task_id,
        )


def _post_agent_code_entropy_alert(task_id: str, threshold: float) -> None:
    notes_inbox = MIRA_DIR / "inbox"
    try:
        notes_inbox.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        note_path = notes_inbox / f"agent_code_entropy_{timestamp}.md"
        note_path.write_text(
            f"{_CODE_ENTROPY_ALERT_MESSAGE}\n\n" f"Task: {task_id}\n" f"Threshold: {threshold:.2f}\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.warning("Could not write agent code entropy alert to notes_inbox: %s", e)


def _track_agent_code_entropy_changes(
    before: dict[str, bytes], after: dict[str, bytes], rel_paths: list[str], task_id: str
) -> None:
    if not rel_paths:
        return
    blocks: list[str] = []
    for rel in sorted(set(rel_paths)):
        if before.get(rel) == after.get(rel):
            continue
        before_lines = _decode_diff_bytes(before.get(rel))
        after_lines = _decode_diff_bytes(after.get(rel))
        fromfile = f"a/{rel}" if rel in before else "/dev/null"
        tofile = f"b/{rel}" if rel in after else "/dev/null"
        blocks.extend(difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile, lineterm=""))
    diff_text = "\n".join(blocks)
    if not diff_text.strip():
        return
    try:
        record_code_change(diff_text)
        threshold = _agent_code_entropy_threshold()
        if is_legibility_risk(threshold):
            log.warning("AGENT_CODE_ENTROPY_ALERT: %s", _CODE_ENTROPY_ALERT_MESSAGE)
            _post_agent_code_entropy_alert(task_id, threshold)
    except Exception as e:
        log.warning("Could not update agent code entropy tracker for task %s: %s", task_id, e)


def _decode_diff_bytes(content: bytes | None) -> list[str]:
    if content is None:
        return []
    try:
        return content.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ["<binary content>"]


def _workspace_review_diff(before: dict[str, bytes], after: dict[str, bytes]) -> str:
    blocks: list[str] = []
    for rel in _changed_review_paths(before, after):
        before_lines = _decode_diff_bytes(before.get(rel))
        after_lines = _decode_diff_bytes(after.get(rel))
        fromfile = f"a/{rel}" if rel in before else "/dev/null"
        tofile = f"b/{rel}" if rel in after else "/dev/null"
        blocks.extend(difflib.unified_diff(before_lines, after_lines, fromfile=fromfile, tofile=tofile, lineterm=""))
    return "\n".join(blocks)


def _restore_review_files(workspace: Path, before: dict[str, bytes], after: dict[str, bytes]) -> None:
    for rel in _changed_review_paths(before, after):
        path = workspace / rel
        try:
            if rel in before:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(before[rel])
            elif path.exists():
                path.unlink()
        except OSError as e:
            log.error("Failed to restore %s before human review: %s", path, e)


def _apply_review_files(workspace: Path, before: dict[str, bytes], after: dict[str, bytes]) -> None:
    for rel in _changed_review_paths(before, after):
        path = workspace / rel
        try:
            if rel in after:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(after[rel])
            elif path.exists():
                path.unlink()
        except OSError as e:
            log.error("Failed to apply approved human-reviewed change %s: %s", path, e)
            raise


def _human_review_marker(task_id: str) -> str:
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id or "task").strip("._") or "task"
    return f"[coder-human-review:{safe_task_id}]"


def _human_review_prompt(task_id: str, diff_text: str, protected_paths: list[str] | None = None) -> str:
    if protected_paths:
        return (
            f"{_human_review_marker(task_id)}\n"
            f"{send_confirmation('Review the proposed diff below before it can be applied.', protected_paths)}\n\n"
            "```diff\n"
            f"{diff_text.strip()}\n"
            "```"
        )
    diff_trust_warning = f"{_DIFF_TRUST_WARNING}\n\n" if _diff_trust_warning_needed(diff_text) else ""
    return (
        f"{_human_review_marker(task_id)}\n"
        f"{diff_trust_warning}"
        "NEEDS_APPROVAL: The coder agent generated a code change. "
        "Review the proposed diff below and reply exactly `approve` to apply it "
        "or `reject` to discard it.\n\n"
        "```diff\n"
        f"{diff_text.strip()}\n"
        "```"
    )


def _bridge_user_id(sender: str) -> str:
    candidate = (sender or "ang").strip() or "ang"
    if (MIRA_DIR / "users" / candidate).exists():
        return candidate
    return "ang"


def _send_human_review_request(task_id: str, thread_id: str, sender: str, prompt: str) -> tuple[str, str]:
    user_id = _bridge_user_id(sender)
    item_id = thread_id or task_id
    try:
        from bridge import Mira

        bridge = Mira(MIRA_DIR, user_id=user_id)
        if bridge.item_exists(item_id):
            bridge.append_message(item_id, "agent", prompt)
            bridge.update_status(item_id, "needs-input")
        else:
            bridge.create_discussion(
                item_id,
                "Code change review required",
                prompt,
                sender="agent",
                tags=["coder", "human-review"],
            )
    except Exception as e:
        log.warning("Could not post coder human review request through bridge item protocol: %s", e)
        inbox = MIRA_DIR / "users" / user_id / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        request_path = inbox / f"{item_id}.coder_review.json"
        payload = {
            "id": f"{item_id}.coder_review",
            "sender": "agent",
            "timestamp": time.time(),
            "content": prompt,
            "type": "text",
            "thread_id": item_id,
            "metadata": {"task_id": task_id, "requires_human_review": True},
        }
        request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return user_id, item_id


def _review_decision(content: str) -> str | None:
    if _HUMAN_APPROVE_RE.match(content or ""):
        return "approve"
    if _HUMAN_REJECT_RE.match(content or ""):
        return "reject"
    return None


def _poll_item_review_decision(user_id: str, item_id: str, marker: str) -> str | None:
    item_path = MIRA_DIR / "users" / user_id / "items" / f"{item_id}.json"
    try:
        item = json.loads(item_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    after_marker = False
    for message in item.get("messages", []):
        content = message.get("content", "")
        if marker in content and message.get("sender") == "agent":
            after_marker = True
            continue
        if not after_marker or message.get("sender") == "agent":
            continue
        decision = _review_decision(content)
        if decision:
            return decision
    return None


def _poll_legacy_review_decision(user_id: str, item_id: str) -> str | None:
    inboxes = [MIRA_DIR / "users" / user_id / "inbox", MIRA_DIR / "inbox"]
    for inbox in inboxes:
        if not inbox.exists():
            continue
        for path in sorted(inbox.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("sender") == "agent":
                continue
            if data.get("thread_id") not in {item_id, ""} and data.get("id") != item_id:
                continue
            decision = _review_decision(data.get("content", ""))
            if decision:
                return decision
    return None


def _wait_for_human_review(
    task_id: str,
    thread_id: str,
    sender: str,
    diff_text: str,
    protected_paths: list[str] | None = None,
) -> bool:
    prompt = _human_review_prompt(task_id, diff_text, protected_paths)
    marker = _human_review_marker(task_id)
    user_id, item_id = _send_human_review_request(task_id, thread_id, sender, prompt)
    log.info("Coder agent waiting for human review approval on task %s", task_id)
    while True:
        decision = _poll_item_review_decision(user_id, item_id, marker) or _poll_legacy_review_decision(
            user_id, item_id
        )
        if decision == "approve":
            return True
        if decision == "reject":
            return False
        time.sleep(_HUMAN_REVIEW_POLL_SECONDS)


def _validate_bug_report_fields(result: str, task_id: str) -> str | None:
    if not _BUG_REPORT_HEADING_RE.search(result or ""):
        return None
    missing = []
    if not _BUG_FIELD_REPRODUCTION_RE.search(result):
        missing.append("REPRODUCTION STEPS")
    if not _BUG_FIELD_OBSERVED_RE.search(result):
        missing.append("OBSERVED BEHAVIOR")
    if not _BUG_FIELD_EXPECTED_RE.search(result):
        missing.append("EXPECTED BEHAVIOR")
    if not _BUG_FIELD_EVIDENCE_RE.search(result):
        missing.append("EVIDENCE")
    if missing:
        log.warning(
            "Bug report for task %s missing required fields: %s",
            task_id,
            ", ".join(missing),
        )
        return (
            f"\n\n⚠️ WARNING: This bug/vulnerability report is missing required fields: "
            f"{', '.join(missing)}. Include exact reproduction steps (commands/inputs), "
            f"expected vs actual output, and the specific file path with line range. "
            f"If you cannot reproduce, state that explicitly rather than reporting a speculative bug."
        )
    return None


def handle(
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    thread_history: str = "",
    thread_memory: str = "",
    tier: str = "light",
    agent_id: str = "coder",
    **kwargs,
) -> str | None:
    """Handle a coding task. Returns output text or None on failure."""
    guard_response = _diff_acceptance_guard(content, thread_id, task_id)
    if guard_response:
        return guard_response

    task_type = str(kwargs.get("task_type") or kwargs.get("type") or "")
    system_prompt = _CODER_SYSTEM
    if _is_audit_mode_task(content, task_type):
        audit_mode_checklist = _load_audit_mode_checklist()
        if audit_mode_checklist:
            system_prompt = f"{audit_mode_checklist}\n\n{system_prompt}"
            log.info("Prepended %d chars of audit-mode checklist", len(audit_mode_checklist))

    soul = load_soul()
    soul_ctx = format_soul(soul)

    extra_context = ""
    if thread_history:
        extra_context += f"\n\n## Conversation History\n{thread_history}"
    if thread_memory:
        extra_context += f"\n\n## Thread Memory\n{thread_memory}"

    # Inject coding-specific skills
    skills_ctx = load_skills_for_task(content, agent_type="coder")
    if skills_ctx:
        extra_context += f"\n\n## Coding Skills & Best Practices\n{skills_ctx}"
        log.info("Injected %d chars of coding skills", len(skills_ctx))
    if _is_code_review_task(content, task_type):
        audit_checklist = _load_code_audit_checklist()
        if audit_checklist:
            extra_context += f"\n\n## Code Audit Mindset\n{audit_checklist}"
            log.info("Injected %d chars of code audit checklist", len(audit_checklist))

    prompt = f"""{system_prompt}

## Your Identity
{soul_ctx}

## Task from {sender}
{content}
{extra_context}

Work in: {workspace}
Write results to {workspace}/output.md when done.
"""

    log.info("Coder agent: task %s (tier=%s, agent=%s, %d chars)", task_id, tier, agent_id, len(content))
    original_snapshot = _snapshot_workspace(workspace)
    review_snapshot = _capture_review_files(workspace)
    python_snapshot = _snapshot_python_files(workspace)
    result = claude_act(prompt, cwd=workspace, tier=tier, agent_id=agent_id)
    modified_snapshot = _snapshot_workspace(workspace)
    modified_review_snapshot = _capture_review_files(workspace)
    if result:
        result, modified_snapshot, modified_review_snapshot, validation_error = _regenerate_after_compile_failure(
            workspace,
            task_id,
            prompt,
            result,
            review_snapshot,
            modified_review_snapshot,
            modified_snapshot,
            python_snapshot,
            tier,
            agent_id,
        )
        if validation_error:
            _write_failed_result(workspace, task_id, validation_error)
            return f"FAILED: {validation_error}"

    if not result:
        log.warning("Coder agent tool path unavailable for task %s — using analysis fallback", task_id)
        snapshot = _snapshot_workspace(workspace)
        fallback_prompt = f"""{prompt}

Tool execution and file editing are unavailable right now.

## Workspace Snapshot
{snapshot}

Use only the snapshot above. Do not claim you edited files or ran tests.
If the fix requires code changes, provide an exact patch recommendation with file paths and replacement snippets.
"""
        result = claude_think(fallback_prompt, timeout=180, tier=tier)
        if not result:
            log.error("Coder agent returned empty for task %s", task_id)
            return None
        (workspace / "output.md").write_text(result, encoding="utf-8")
        modified_review_snapshot = _capture_review_files(workspace)

    # Try to read output.md if claude_act wrote one
    output_file = workspace / "output.md"
    if output_file.exists():
        output_content = output_file.read_text(encoding="utf-8")
        if len(output_content) > len(result):
            result = output_content

    if _coder_skeptical_review_enabled():
        review_text = _low_temperature_review(_skeptical_review_prompt(content, result, modified_snapshot))
        if not review_text:
            _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
            return _format_skeptical_abort("skeptical review could not complete")
        high_severity_issues = _high_severity_review_issues(review_text)
        if high_severity_issues:
            revision_prompt = _skeptical_revision_prompt(workspace, content, review_text)
            result = claude_act(revision_prompt, cwd=workspace, tier=tier, agent_id=agent_id)
            modified_snapshot = _snapshot_workspace(workspace)
            modified_review_snapshot = _capture_review_files(workspace)
            if not result:
                _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
                return _format_skeptical_abort("revision failed", high_severity_issues, review_text)
            result, modified_snapshot, modified_review_snapshot, validation_error = _regenerate_after_compile_failure(
                workspace,
                task_id,
                revision_prompt,
                result,
                review_snapshot,
                modified_review_snapshot,
                modified_snapshot,
                python_snapshot,
                tier,
                agent_id,
            )
            if validation_error:
                _write_failed_result(workspace, task_id, validation_error)
                return f"FAILED: {validation_error}"
            if output_file.exists():
                output_content = output_file.read_text(encoding="utf-8")
                if len(output_content) > len(result):
                    result = output_content

    if _needs_fix_audit(result, original_snapshot, modified_snapshot) or _changed_review_paths(
        review_snapshot, modified_review_snapshot
    ):
        audit_result = audit_fix(
            f"{result}\n\n## Workspace Snapshot After Fix\n{modified_snapshot}",
            content,
            original_snapshot,
            tier=tier,
        )
    else:
        audit_result = "SAFE"
    if not _audit_passed(audit_result):
        revision_prompt = f"""{prompt}

The first proposed fix did not pass a separate audit. Revise the fix using this audit feedback, then update the workspace and output.md.

## Audit Feedback
{audit_result or "Audit returned no SAFE signal."}
"""
        result = claude_act(revision_prompt, cwd=workspace, tier=tier, agent_id=agent_id)
        modified_snapshot = _snapshot_workspace(workspace)
        modified_review_snapshot = _capture_review_files(workspace)
        if not result:
            log.error("Coder agent revision pass returned empty for task %s", task_id)
            return f"FAILED: audit rejected the proposed fix: {audit_result or 'empty audit result'}"
        result, modified_snapshot, modified_review_snapshot, validation_error = _regenerate_after_compile_failure(
            workspace,
            task_id,
            revision_prompt,
            result,
            review_snapshot,
            modified_review_snapshot,
            modified_snapshot,
            python_snapshot,
            tier,
            agent_id,
        )
        if validation_error:
            _write_failed_result(workspace, task_id, validation_error)
            return f"FAILED: {validation_error}"
        if output_file.exists():
            output_content = output_file.read_text(encoding="utf-8")
            if len(output_content) > len(result):
                result = output_content
        audit_result = audit_fix(
            f"{result}\n\n## Workspace Snapshot After Fix\n{modified_snapshot}",
            content,
            original_snapshot,
            tier=tier,
        )
        if not _audit_passed(audit_result):
            _write_failed_result(workspace, task_id, f"Audit rejected the proposed fix: {audit_result}")
            return f"FAILED: audit rejected the proposed fix: {audit_result}"

    feynman_diff = _workspace_review_diff(review_snapshot, modified_review_snapshot)
    if feynman_diff or _looks_like_diff_presentation(result):
        feynman_payload = feynman_diff or result
        feynman_explanation = _extract_feynman_explanation(result)
        if not _feynman_comprehension_gate(feynman_payload, feynman_explanation, content):
            feynman_revision_prompt = _feynman_revision_prompt(prompt, feynman_payload, result)
            result = claude_act(
                feynman_revision_prompt,
                cwd=workspace,
                tier=tier,
                agent_id=agent_id,
            )
            modified_snapshot = _snapshot_workspace(workspace)
            modified_review_snapshot = _capture_review_files(workspace)
            if not result:
                _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
                _write_failed_result(workspace, task_id, "Feynman comprehension gate failed: missing teach-back")
                return "FAILED: Feynman comprehension gate failed; re-explain the approach before accepting the change."
            result, modified_snapshot, modified_review_snapshot, validation_error = _regenerate_after_compile_failure(
                workspace,
                task_id,
                feynman_revision_prompt,
                result,
                review_snapshot,
                modified_review_snapshot,
                modified_snapshot,
                python_snapshot,
                tier,
                agent_id,
            )
            if validation_error:
                _write_failed_result(workspace, task_id, validation_error)
                return f"FAILED: {validation_error}"
            if output_file.exists():
                output_content = output_file.read_text(encoding="utf-8")
                if len(output_content) > len(result):
                    result = output_content
            if _needs_fix_audit(result, original_snapshot, modified_snapshot) or _changed_review_paths(
                review_snapshot, modified_review_snapshot
            ):
                audit_result = audit_fix(
                    f"{result}\n\n## Workspace Snapshot After Fix\n{modified_snapshot}",
                    content,
                    original_snapshot,
                    tier=tier,
                )
                if not _audit_passed(audit_result):
                    _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
                    _write_failed_result(
                        workspace,
                        task_id,
                        f"Audit rejected the Feynman-gate revision: {audit_result}",
                    )
                    return f"FAILED: audit rejected the Feynman-gate revision: {audit_result}"
            feynman_diff = _workspace_review_diff(review_snapshot, modified_review_snapshot)
            feynman_payload = feynman_diff or result
            feynman_explanation = _extract_feynman_explanation(result)
            if not _feynman_comprehension_gate(feynman_payload, feynman_explanation, content):
                _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
                _write_failed_result(workspace, task_id, "Feynman comprehension gate failed: insufficient teach-back")
                return "FAILED: Feynman comprehension gate failed; re-explain the approach before accepting the change."

    auto_edit_code_paths = _auto_edit_code_paths(workspace, review_snapshot, modified_review_snapshot)
    churn_refusal = _agent_edit_churn_refusal(workspace, auto_edit_code_paths, agent_id)
    if churn_refusal:
        _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
        return churn_refusal
    auto_edit_refusal = _auto_edit_limit_refusal(workspace, auto_edit_code_paths, task_id)
    if auto_edit_refusal:
        _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
        return auto_edit_refusal

    # Post-validation: check written Python files for syntax and import errors
    validation_error = _validate_python_files(workspace, python_snapshot)
    if validation_error:
        _restore_python_files(workspace, python_snapshot)
        _write_failed_result(workspace, task_id, validation_error)
        return f"FAILED: {validation_error}"
    changed_paths = _changed_review_paths(review_snapshot, modified_review_snapshot)
    protected_changed_paths = [path for path in changed_paths if _is_protected_path(path)]
    if changed_paths and (CODER_REQUIRE_HUMAN_REVIEW or protected_changed_paths):
        proposed_diff = _workspace_review_diff(review_snapshot, modified_review_snapshot)
        _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
        if not _wait_for_human_review(task_id, thread_id, sender, proposed_diff, protected_changed_paths):
            return "REJECTED: Human review rejected the proposed code change. No code changes were applied."
        churn_refusal = _agent_edit_churn_refusal(workspace, auto_edit_code_paths, agent_id)
        if churn_refusal:
            return churn_refusal
        auto_edit_refusal = _auto_edit_limit_refusal(workspace, auto_edit_code_paths, task_id)
        if auto_edit_refusal:
            return auto_edit_refusal
        _apply_review_files(workspace, review_snapshot, modified_review_snapshot)
        validation_error = _validate_python_files(workspace, python_snapshot)
        if validation_error:
            _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
            _write_failed_result(workspace, task_id, validation_error)
            return f"FAILED: {validation_error}"
        _record_agent_churn_edits(workspace, auto_edit_code_paths, agent_id)
        _record_auto_edits_applied(workspace, auto_edit_code_paths, task_id)
        _track_unreviewed_code_changes(workspace, review_snapshot, modified_review_snapshot, agent_id, task_id)
        _track_agent_code_entropy_changes(review_snapshot, modified_review_snapshot, auto_edit_code_paths, task_id)
    elif auto_edit_code_paths:
        _record_agent_churn_edits(workspace, auto_edit_code_paths, agent_id)
        _record_auto_edits_applied(workspace, auto_edit_code_paths, task_id)
        _track_unreviewed_code_changes(workspace, review_snapshot, modified_review_snapshot, agent_id, task_id)
        _track_agent_code_entropy_changes(review_snapshot, modified_review_snapshot, auto_edit_code_paths, task_id)
    generated_diff = _workspace_review_diff(review_snapshot, modified_review_snapshot)
    diff_for_surface_quality = generated_diff or result
    if generated_diff or _looks_like_diff_presentation(result):
        if _diff_trust_warning_needed(diff_for_surface_quality) and _DIFF_TRUST_WARNING not in result:
            result = f"{_DIFF_TRUST_WARNING}\n\n{result}"
    if _looks_like_diff_presentation(result):
        affected_paths = _extract_diff_paths(result)
        if not any("test" in path.lower() or "tests" in path.lower() for path in affected_paths):
            result = f"{_NO_TEST_DIFF_WARNING}\n\n{result}"
    diff_plausibility_score = _diff_plausibility_score(original_snapshot, modified_snapshot, content)
    if diff_plausibility_score > 0.5:
        result = (
            f"{result.rstrip()}\n\n"
            f"diff_plausibility_score: {diff_plausibility_score:.2f}\n"
            f"⚠️ Diff plausibility score: {diff_plausibility_score:.2f} — "
            "cosmetic changes dominate a claimed logical fix; deep review recommended."
        )
    _record_diff_presented(thread_id, task_id, workspace, result)

    _ai_line_count = _count_added_lines(_workspace_review_diff(review_snapshot, modified_review_snapshot))
    if _ai_line_count > MAX_AI_CODE_LINES_PER_TASK:
        _exceedance_msg = (
            f"AI_CODE_LINES_EXCEEDED: task {task_id} generated {_ai_line_count} added lines "
            f"(threshold: {MAX_AI_CODE_LINES_PER_TASK}). "
            "Split into sub-tasks under the threshold for adequate human review."
        )
        log.warning(_exceedance_msg)
        _ai_lines_log = _MIRA_ROOT / "logs" / "coder_ai_lines.jsonl"
        try:
            _ai_lines_log.parent.mkdir(parents=True, exist_ok=True)
            with _ai_lines_log.open("a", encoding="utf-8") as _lf:
                _lf.write(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "added_lines": _ai_line_count,
                            "threshold": MAX_AI_CODE_LINES_PER_TASK,
                            "ts": time.time(),
                        }
                    )
                    + "\n"
                )
        except OSError as _le:
            log.warning("Could not write AI lines log: %s", _le)
        result = f"⚠️  {_exceedance_msg}\n\n{result}"

    if _changed_review_paths(review_snapshot, modified_review_snapshot) or _looks_like_diff_presentation(result):
        code_transparency_warning = _format_code_transparency_warning(result, task_id)
        if code_transparency_warning:
            result = result.rstrip() + code_transparency_warning

    bug_report_warning = _validate_bug_report_fields(result, task_id)
    if bug_report_warning:
        result = result.rstrip() + bug_report_warning
    log.info("Coder agent completed task %s (%d chars output)", task_id, len(result))
    return result


def _snapshot_python_files(workspace: Path) -> dict[Path, bytes]:
    snapshot: dict[Path, bytes] = {}
    for file_path in workspace.rglob("*.py"):
        if not file_path.is_file():
            continue
        try:
            snapshot[file_path.resolve()] = file_path.read_bytes()
        except OSError:
            continue
    return snapshot


def _validate_python_files(workspace: Path, before_snapshot: dict[Path, bytes] | None = None) -> str | None:
    """Check written .py files in workspace for syntax and import errors."""
    modified_files = _modified_python_files(workspace, before_snapshot)
    syntax_error = _validate_modified_python_syntax(workspace, before_snapshot)
    if syntax_error:
        return syntax_error

    for file_path in modified_files:
        import_error = _validate_imports(file_path, workspace)
        if import_error:
            message = f"Import check failed for {file_path}: {import_error}"
            log.error(message)
            return message
    return None


_CODE_SYNTAX_ERROR: str | None = None


def _modified_python_files(workspace: Path, before_snapshot: dict[Path, bytes] | None = None) -> list[Path]:
    before_snapshot = before_snapshot or {}
    modified_files: list[Path] = []
    for file_path in sorted(workspace.rglob("*.py")):
        if not file_path.is_file():
            continue
        try:
            current = file_path.read_bytes()
        except OSError:
            current = None
        if before_snapshot.get(file_path.resolve()) == current:
            continue
        modified_files.append(file_path)
    return modified_files


def _validate_modified_python_syntax(workspace: Path, before_snapshot: dict[Path, bytes] | None = None) -> str | None:
    modified_files = _modified_python_files(workspace, before_snapshot)
    if not _verify_code_syntax([str(file_path) for file_path in modified_files]):
        return f"Syntax check failed: {_CODE_SYNTAX_ERROR or 'unknown py_compile failure'}"
    return None


def _regenerate_after_compile_failure(
    workspace: Path,
    task_id: str,
    prompt: str,
    result: str,
    review_snapshot: dict[str, bytes],
    modified_review_snapshot: dict[str, bytes],
    modified_snapshot: str,
    python_snapshot: dict[Path, bytes],
    tier: str,
    agent_id: str,
) -> tuple[str | None, str, dict[str, bytes], str | None]:
    validation_error = _validate_modified_python_syntax(workspace, python_snapshot)
    if not validation_error:
        return result, modified_snapshot, modified_review_snapshot, None

    log.error("Coder agent generated non-compiling Python for task %s: %s", task_id, validation_error)
    _restore_review_files(workspace, review_snapshot, modified_review_snapshot)
    _restore_python_files(workspace, python_snapshot)
    retry_prompt = f"""{prompt}

The generated change failed Python syntax verification after it was applied. The generated change has been reverted.
Regenerate the fix using the py_compile error below as context, then update output.md.

## py_compile Error
{validation_error}
"""
    retry_result = claude_act(retry_prompt, cwd=workspace, tier=tier, agent_id=agent_id)
    retry_modified_snapshot = _snapshot_workspace(workspace)
    retry_modified_review_snapshot = _capture_review_files(workspace)
    if not retry_result:
        _restore_review_files(workspace, review_snapshot, retry_modified_review_snapshot)
        _restore_python_files(workspace, python_snapshot)
        return None, _snapshot_workspace(workspace), _capture_review_files(workspace), validation_error

    retry_error = _validate_modified_python_syntax(workspace, python_snapshot)
    if retry_error:
        log.error("Coder agent regenerated non-compiling Python for task %s: %s", task_id, retry_error)
        _restore_review_files(workspace, review_snapshot, retry_modified_review_snapshot)
        _restore_python_files(workspace, python_snapshot)
        return retry_result, _snapshot_workspace(workspace), _capture_review_files(workspace), retry_error

    return retry_result, retry_modified_snapshot, retry_modified_review_snapshot, None


def _verify_code_syntax(files: list[str]) -> bool:
    global _CODE_SYNTAX_ERROR
    _CODE_SYNTAX_ERROR = None
    for file_name in files:
        file_path = Path(file_name)
        if file_path.suffix != ".py":
            continue
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "py_compile", str(file_path)],
                capture_output=True,
                text=True,
            )
        except OSError as e:
            _CODE_SYNTAX_ERROR = f"{file_path}: {e}"
            log.error("Syntax check failed for %s: %s", file_path, e)
            return False
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or f"exit code {completed.returncode}").strip()
            _CODE_SYNTAX_ERROR = f"{file_path}: {error}"
            log.error("Syntax check failed for %s: %s", file_path, error)
            return False
    return True


def _validate_imports(file_path: Path, workspace: Path) -> str | None:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as e:
        return str(e)

    absolute_modules: set[str] = set()
    missing_relative: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 0:
                absolute_modules.add(node.module)
            elif not _relative_import_exists(file_path, node.module, node.level):
                missing_relative.append("." * node.level + node.module)
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            missing_relative.extend(
                "." * node.level + alias.name
                for alias in node.names
                if not _relative_import_exists(file_path, alias.name, node.level)
            )

    if not absolute_modules and not missing_relative:
        return None

    search_paths = [str(workspace), str(file_path.parent), str(_MIRA_ROOT), str(_MIRA_ROOT / "lib")]
    old_path = sys.path[:]
    try:
        for search_path in reversed(search_paths):
            if search_path not in sys.path:
                sys.path.insert(0, search_path)
        missing = sorted(module for module in absolute_modules if _find_spec(module) is None)
    finally:
        sys.path[:] = old_path
    missing.extend(sorted(missing_relative))
    if missing:
        return f"missing import(s): {', '.join(missing[:5])}"
    return None


def _find_spec(module: str):
    try:
        return importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        return None


def _relative_import_exists(file_path: Path, module: str, level: int) -> bool:
    base_dir = file_path.parent
    for _ in range(max(level - 1, 0)):
        base_dir = base_dir.parent
    target = base_dir.joinpath(*module.split("."))
    return target.with_suffix(".py").is_file() or (target / "__init__.py").is_file()


def _restore_python_files(workspace: Path, before_snapshot: dict[Path, bytes]) -> None:
    current_files = {path.resolve() for path in workspace.rglob("*.py") if path.is_file()}
    for file_path, content in before_snapshot.items():
        try:
            if file_path.exists() and file_path.read_bytes() == content:
                continue
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
        except OSError as e:
            log.error("Failed to restore %s after validation failure: %s", file_path, e)
    for file_path in current_files - set(before_snapshot):
        try:
            file_path.unlink()
        except OSError as e:
            log.error("Failed to remove invalid new file %s after validation failure: %s", file_path, e)


def _count_added_lines(diff_text: str) -> int:
    return sum(1 for line in (diff_text or "").splitlines() if line.startswith("+") and not line.startswith("+++"))


def _write_failed_result(workspace: Path, task_id: str, message: str) -> None:
    payload = {
        "task_id": task_id,
        "status": "failed",
        "summary": message,
        "error_message": message,
        "failure_class": "code_validation_failed",
    }
    (workspace / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
