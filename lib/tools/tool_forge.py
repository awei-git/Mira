"""Tool Forge — runtime creation and discovery of executable Python tools.

When an agent encounters a task that needs a capability it doesn't have,
it can write a Python script, save it via forge_tool(), and use it immediately.
Future tasks auto-discover saved tools and can import/call them.

Tools are saved as standalone Python scripts in RUNTIME_TOOLS_DIR with a
JSON index for discovery. Each tool has:
- A clear docstring describing what it does
- A main function that can be called with arguments
- Metadata (name, description, tags, created date)

Security: runtime tools use a separate audit from learned skills.
Executable code legitimately needs urllib, file I/O, etc.
We block only truly dangerous patterns (eval, exec, sudo, credential theft).
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import MIRA_ROOT

log = logging.getLogger("mira.forge")

# Patterns that are genuinely dangerous in executable tools
_TOOL_BLOCKED_PATTERNS = [
    # Code injection / dynamic execution
    (r"\beval\s*\(", "Dynamic eval() — use ast.literal_eval if needed"),
    (r"\bexec\s*\(", "Dynamic exec()"),
    (r"__import__\s*\(", "Dynamic __import__"),
    (r"marshal\.(loads|dumps)", "Serialized code objects"),
    # Privilege escalation
    (r"\bsudo\s+", "Privilege escalation"),
    (r"/etc/shadow|/etc/passwd", "System credential files"),
    (r"\.ssh/", "SSH key access"),
    (r"keychain|keyring", "Credential store access"),
    # Persistence / stealth
    (r"curl\s+.*\|\s*(ba)?sh", "Pipe-to-shell pattern"),
    (r"wget\s+.*\|\s*(ba)?sh", "Pipe-to-shell pattern"),
    # Credential exfiltration
    (r"OPENAI_API_KEY|ANTHROPIC_API_KEY", "Hardcoded API key reference"),
]

RUNTIME_TOOLS_DIR = MIRA_ROOT / "agents" / "shared" / "runtime_tools"
TOOLS_INDEX = RUNTIME_TOOLS_DIR / "index.json"


def _ensure_dir():
    RUNTIME_TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def _audit_tool(name: str, code: str) -> tuple[bool, list[str]]:
    """Security audit for runtime tools. Lighter than learned skill audit.

    Runtime tools are executable Python — they legitimately need urllib,
    file I/O, subprocess, etc. We only block patterns that indicate
    malicious intent: eval/exec, credential theft, pipe-to-shell.
    """
    violations = []
    for pattern, description in _TOOL_BLOCKED_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            violations.append(f"[BLOCKED] {description}")
    if violations:
        log.warning("Tool '%s' FAILED audit: %s", name, violations)
    return len(violations) == 0, violations


def list_tools() -> list[dict]:
    """Return the index of all available runtime tools."""
    if not TOOLS_INDEX.exists():
        return []
    try:
        return json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def load_tools_summary(max_chars: int = 3000) -> str:
    """Format available runtime tools as a summary for prompt injection."""
    tools = list_tools()
    if not tools:
        return ""
    lines = [
        "## Available Runtime Tools\n",
        "These are Python scripts you can import and use. " f"Location: {RUNTIME_TOOLS_DIR}/\n",
    ]
    for t in tools:
        name = t.get("name", "?")
        desc = t.get("description", "")
        fname = t.get("file", "")
        usage = t.get("usage", "")
        lines.append(f"- **{name}** (`{fname}`): {desc}")
        if usage:
            lines.append(f"  Usage: `{usage}`")
    result = "\n".join(lines)
    return result[:max_chars]


def forge_tool(
    name: str, description: str, code: str, tags: list[str] | None = None, usage: str = ""
) -> tuple[bool, str]:
    """Save a new runtime tool. Returns (success, message).

    Args:
        name: Human-readable tool name (e.g., "pdf-to-text")
        description: One-line description
        code: Full Python source code (must be a valid, standalone script)
        tags: Optional categorization tags
        usage: One-line usage example (e.g., "from pdf_to_text import convert; convert('file.pdf')")

    Returns:
        (True, file_path) on success, (False, error_message) on failure.
    """
    # Security audit (tool-specific, lighter than learned skill audit)
    passed, violations = _audit_tool(name, code)
    if not passed:
        msg = f"BLOCKED tool '{name}' — security audit failed: {'; '.join(violations)}"
        log.warning(msg)
        return False, msg

    # Validate it's valid Python
    try:
        compile(code, f"{name}.py", "exec")
    except SyntaxError as e:
        msg = f"Tool '{name}' has syntax error: {e}"
        log.warning(msg)
        return False, msg

    _ensure_dir()
    slug = name.lower().replace(" ", "_").replace("-", "_")
    filename = f"{slug}.py"
    filepath = RUNTIME_TOOLS_DIR / filename

    # Write the tool
    filepath.write_text(code, encoding="utf-8")

    # Update index
    index = list_tools()
    index = [t for t in index if t["name"] != name]
    index.append(
        {
            "name": name,
            "description": description,
            "file": filename,
            "tags": tags or [],
            "usage": usage,
            "created": datetime.now().isoformat(),
        }
    )
    TOOLS_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Forged runtime tool: %s → %s", name, filepath)
    return True, str(filepath)


def delete_tool(name: str) -> bool:
    """Remove a runtime tool by name."""
    index = list_tools()
    tool = next((t for t in index if t["name"] == name), None)
    if not tool:
        return False

    filepath = RUNTIME_TOOLS_DIR / tool["file"]
    if filepath.exists():
        filepath.unlink()

    index = [t for t in index if t["name"] != name]
    _ensure_dir()
    TOOLS_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Deleted runtime tool: %s", name)
    return True
