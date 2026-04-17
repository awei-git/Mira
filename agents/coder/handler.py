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
import logging
import re
from pathlib import Path

from memory.soul import load_soul, format_soul, load_skills_for_task
from llm import claude_act, claude_think

log = logging.getLogger("coder_agent")

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

## Output format
For debug/review tasks, structure your response as:
- **Problem**: What's wrong (with file:line)
- **Root cause**: Why it happens
- **Fix**: What you changed (or recommend)
- **Verification**: How you confirmed it works
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
) -> str | None:
    """Handle a coding task. Returns output text or None on failure."""
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
    result = claude_act(prompt, cwd=workspace, tier=tier, agent_id=agent_id)

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

    # Post-validation: check generated Python files for syntax errors
    _validate_python_files(workspace)

    log.info("Coder agent completed task %s (%d chars output)", task_id, len(result))
    return result


def _validate_python_files(workspace: Path):
    """Check all .py files in workspace for syntax errors. Log warnings."""
    for py_file in workspace.rglob("*.py"):
        try:
            ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError as e:
            log.warning("Syntax error in %s: %s (line %d)", py_file.name, e.msg, e.lineno or 0)
