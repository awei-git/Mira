"""Coder agent — debug, review, and short coding tasks.

Focus: find bugs, review code for problems, quick fixes, small scripts.
Long projects and architecture work happen in Claude Code sessions, not here.

The coder agent:
- Has full workspace read/write + command execution via claude_act
- Injects coding-specific skills (debugging, review, error handling, etc.)
- Validates output: checks for syntax errors in generated/modified code
- Prioritizes finding and fixing problems over writing new code
"""

import difflib
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from config import MIN_DIFF_REVIEW_SECONDS, TASKS_DIR, _cfg
except ImportError:
    from config import TASKS_DIR, _cfg

    MIN_DIFF_REVIEW_SECONDS = 30
_SHARED_DIR = Path(__file__).resolve().parent.parent / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
from diff_trust_guard import score_diff_surface_quality
from memory.soul import load_soul, format_soul, load_skills_for_task
from llm import claude_act, claude_think

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
_COSMETIC_CLAIM_RE = re.compile(
    r"\b(format|formatting|style|whitespace|comment|rename|renaming|refactor|docs?|documentation)\b", re.I
)
_DIFF_TRUST_DEFAULT_THRESHOLD = 0.7
_DIFF_TRUST_WARNING = (
    "⚠️  This diff reads smoothly; trust inflation risk — verify logical correctness, " "not just surface readability."
)
_NO_TEST_DIFF_WARNING = (
    "CAUTION: This code change does not modify any test files. The diff may appear coherent "
    "but could contain subtle errors. Please review thoroughly."
)
_MIRA_ROOT = Path(__file__).resolve().parents[2]
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

## Output format
For debug/review tasks, structure your response as:
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
"""


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

    prompt = f"""{_CODER_SYSTEM}

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
    python_snapshot = _snapshot_python_files(workspace)
    result = claude_act(prompt, cwd=workspace, tier=tier, agent_id=agent_id)
    modified_snapshot = _snapshot_workspace(workspace)

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

    # Try to read output.md if claude_act wrote one
    output_file = workspace / "output.md"
    if output_file.exists():
        output_content = output_file.read_text(encoding="utf-8")
        if len(output_content) > len(result):
            result = output_content

    # Post-validation: check written Python files for syntax errors
    syntax_error = _validate_python_files(workspace, python_snapshot)
    if syntax_error:
        _write_failed_result(workspace, task_id, syntax_error)
        return f"FAILED: {syntax_error}"
    if _looks_like_diff_presentation(result):
        affected_paths = _extract_diff_paths(result)
        surface_quality_score = score_diff_surface_quality(result)
        if surface_quality_score > _diff_trust_threshold() and _DIFF_TRUST_WARNING not in result:
            result = f"{_DIFF_TRUST_WARNING}\n\n{result}"
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
    """Check written .py files in workspace for syntax errors."""
    before_snapshot = before_snapshot or {}
    for file_path in sorted(workspace.rglob("*.py")):
        if not file_path.is_file():
            continue
        try:
            current = file_path.read_bytes()
        except OSError:
            current = None
        if before_snapshot.get(file_path.resolve()) == current:
            continue

        try:
            proc = subprocess.run(["python", "-m", "py_compile", str(file_path)], capture_output=True, text=True)
        except FileNotFoundError:
            proc = subprocess.run([sys.executable, "-m", "py_compile", str(file_path)], capture_output=True, text=True)
        if proc.returncode != 0:
            error = (proc.stderr or proc.stdout or "unknown py_compile failure").strip()
            message = f"Syntax check failed for {file_path}: {error}"
            log.error(message)
            return message
    return None


def _write_failed_result(workspace: Path, task_id: str, message: str) -> None:
    payload = {
        "task_id": task_id,
        "status": "failed",
        "summary": message,
        "error_message": message,
        "failure_class": "syntax_check_failed",
    }
    (workspace / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
