"""Secret agent — handles privacy-sensitive tasks using LOCAL LLM only.

Nothing leaves localhost. No cloud API calls. No web requests.
Uses Ollama (qwen2.5:32b) for reasoning.

Capabilities:
- Text Q&A (fully private)
- Read local/iCloud files referenced in the message → inject into prompt
- All file content stays on localhost, never sent to cloud

Route here for: personal finance, health, legal, passwords, family matters,
anything the user wouldn't want sent to OpenAI/Anthropic/DeepSeek servers.
"""
import logging
import re
from pathlib import Path

from config import OLLAMA_DEFAULT_MODEL
from sub_agent import _ollama_call

log = logging.getLogger("secret_agent")

_SYSTEM_PROMPT = """You are Mira, a private AI assistant running entirely on local hardware.
This conversation NEVER leaves this machine. No cloud APIs, no network requests.
Be thorough and helpful. The user chose the private channel because this is sensitive.
Respond in the same language the user writes in."""

# Where to look for user files
_SEARCH_DIRS = [
    Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs",  # iCloud Drive
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
]

# File types we can safely read as text
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".html", ".xml", ".log",
    ".rtf", ".tex",
}

_SPREADSHEET_EXTENSIONS = {".csv", ".tsv"}


def _find_files(query: str) -> list[Path]:
    """Find files matching user's description. Local search only."""
    # Extract quoted filenames or obvious file references
    patterns = re.findall(r'["\']([^"\']+)["\']', query)
    # Also look for common file-like strings
    patterns += re.findall(r'(\S+\.(?:pdf|csv|txt|xlsx?|docx?|md|json))', query, re.IGNORECASE)
    # And Chinese file descriptions
    keywords = re.findall(r'(?:文件|file|document|表格|报表)\s*[：:]*\s*(\S+)', query, re.IGNORECASE)
    patterns += keywords

    found = []
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        # Direct path
        p = Path(pattern).expanduser()
        if p.exists() and p.is_file():
            found.append(p)
            continue
        # Search in known dirs
        for base in _SEARCH_DIRS:
            if not base.exists():
                continue
            for match in base.rglob(f"*{pattern}*"):
                if match.is_file() and len(found) < 5:
                    found.append(match)

    return found[:5]  # cap at 5 files


def _read_file(path: Path, max_chars: int = 8000) -> str:
    """Read a file's content. Only reads text-safe formats."""
    suffix = path.suffix.lower()

    if suffix in _TEXT_EXTENSIONS:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n\n... [truncated, {len(content)} total chars]"
            return content
        except OSError as e:
            return f"[Error reading {path.name}: {e}]"

    if suffix == ".pdf":
        # Try to extract text from PDF
        try:
            import subprocess
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                content = result.stdout[:max_chars]
                return content
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return f"[PDF file: {path.name} — could not extract text]"

    if suffix in (".xlsx", ".xls"):
        return f"[Spreadsheet: {path.name} — convert to CSV for analysis]"

    return f"[Unsupported file type: {path.suffix}]"


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a privacy-sensitive request using only local Ollama.

    If the message references files, reads them locally and includes
    content in the prompt. All data stays on localhost.
    """
    extra = ""
    if thread_history:
        extra += f"\n\nPrevious conversation:\n{thread_history}"

    # Find and read referenced files — ALL LOCAL, no network
    files = _find_files(content)
    if files:
        file_sections = []
        for f in files:
            file_content = _read_file(f)
            file_sections.append(f"### File: {f.name}\nPath: {f}\n\n{file_content}")
            log.info("Secret agent: read local file %s (%d chars)", f.name, len(file_content))
        extra += "\n\n## Referenced Files\n\n" + "\n\n---\n\n".join(file_sections)

    prompt = content + extra
    log.info("Secret agent: task %s, %d chars (incl. %d files), model=%s",
             task_id, len(prompt), len(files), OLLAMA_DEFAULT_MODEL)

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, system=_SYSTEM_PROMPT, timeout=300)

    if result:
        # Do NOT write output.md — private content should not persist on disk.
        return result

    log.error("Secret agent: Ollama returned empty for task %s", task_id)
    return None
