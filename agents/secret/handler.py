"""Secret agent — handles privacy-sensitive tasks using LOCAL LLM only.

Nothing leaves localhost. No cloud API calls. No web requests.
Uses Ollama (qwen2.5:32b) for reasoning.

Capabilities:
- Text Q&A (fully private)
- Read local/iCloud files — uses local LLM to parse natural language paths
- All file content stays on localhost, never sent to cloud

Route here for: personal finance, health, legal, passwords, family matters,
anything the user wouldn't want sent to OpenAI/Anthropic/DeepSeek servers.
"""
import json
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
    ".rtf", ".tex", ".tsv",
}

# Max total chars across all files to inject into prompt
# 32B model handles ~16K context well, 32K is limit
_MAX_TOTAL_FILE_CHARS = 12000
_MAX_FILES = 5


def _parse_file_intent(query: str) -> dict:
    """Use local LLM to understand what files the user is referring to.

    Returns: {"directory_hints": [...], "file_patterns": [...], "year": "..."}
    All local — Ollama only.
    """
    parse_prompt = f"""The user mentions files on their computer:

"{query[:300]}"

Extract file location info. Return JSON only:
{{"directory_hints": ["keywords for directory path"],
  "file_patterns": ["filename patterns or extensions"],
  "year": "year if mentioned"}}

JSON only."""

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, parse_prompt,
                         system="Extract file paths from natural language. JSON only.",
                         timeout=30)
    if not result:
        return {"directory_hints": [], "file_patterns": [], "year": ""}

    try:
        clean = result.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        return {"directory_hints": [], "file_patterns": [], "year": ""}


def _find_files(query: str) -> list[Path]:
    """Find files matching user's description using local LLM + filesystem search."""
    found = []

    # Step 1: Direct pattern extraction (fast, no LLM)
    for m in re.findall(r'["\']([^"\']+)["\']', query):
        p = Path(m).expanduser()
        if p.exists() and p.is_file():
            found.append(p)
    for m in re.findall(r'(\S+\.(?:pdf|csv|txt|xlsx?|docx?|md|json))', query, re.IGNORECASE):
        p = Path(m).expanduser()
        if p.exists() and p.is_file():
            found.append(p)

    if found:
        return found[:_MAX_FILES]

    # Step 2: LLM-assisted parsing
    intent = _parse_file_intent(query)
    dir_hints = intent.get("directory_hints", [])
    file_patterns = intent.get("file_patterns", [])
    year = intent.get("year", "")

    # Split multi-word hints into individual words for hierarchical navigation
    # e.g. "important tax" → ["important", "tax"]
    split_hints = []
    for h in dir_hints:
        split_hints.extend(h.lower().split())
    # Add year as a directory hint too (e.g. Documents/important/Tax/2025/)
    if year and year not in split_hints:
        split_hints.append(year)
    # Deduplicate while preserving order
    seen = set()
    dir_hints_clean = []
    for h in split_hints:
        if h not in seen and h not in ("files", "folder", "directory", "文件", "文件夹"):
            seen.add(h)
            dir_hints_clean.append(h)

    log.info("File intent: dirs=%s patterns=%s year=%s", dir_hints_clean, file_patterns, year)

    # Step 3: Navigate filesystem using parsed hints — drill down one level at a time
    for base in _SEARCH_DIRS:
        if not base.exists():
            continue

        # Navigate: for each hint, find matching subdirectories
        candidates = [base]
        for hint in dir_hints_clean:
            new_candidates = []
            for cand in candidates:
                if not cand.is_dir():
                    continue
                try:
                    for sub in cand.iterdir():
                        if sub.is_dir() and hint in sub.name.lower():
                            new_candidates.append(sub)
                except PermissionError:
                    continue
            if new_candidates:
                candidates = new_candidates
            # If no match for this hint, keep current candidates (don't reset)

        # Search in candidate dirs
        for cand in candidates:
            try:
                for f in cand.rglob("*"):
                    if not f.is_file():
                        continue
                    # Skip hidden files
                    if f.name.startswith("."):
                        continue
                    name_lower = f.name.lower()

                    matched = False
                    # Match by file pattern
                    for pat in file_patterns:
                        pat_lower = pat.lower().replace("*", "").replace(".", "")
                        if pat_lower and pat_lower in name_lower:
                            matched = True
                            break
                    # Match by year
                    if year and year in f.name:
                        matched = True
                    # Match by dir hints in full path
                    if not matched and dir_hints:
                        path_lower = str(f).lower()
                        if all(h.lower() in path_lower for h in dir_hints):
                            matched = True

                    if matched and len(found) < _MAX_FILES:
                        found.append(f)
            except PermissionError:
                continue

    # Prioritize: year-matching files first, then by size (larger = more content)
    if year:
        found.sort(key=lambda f: (year not in f.name, -f.stat().st_size))

    return found[:_MAX_FILES]


def _read_file(path: Path, max_chars: int = 4000) -> str:
    """Read a file's content. Only reads text-safe formats."""
    suffix = path.suffix.lower()

    if suffix in _TEXT_EXTENSIONS:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_chars:
                content = content[:max_chars] + f"\n... [truncated, {len(content)} total chars]"
            return content
        except OSError as e:
            return f"[Error reading {path.name}: {e}]"

    if suffix == ".pdf":
        try:
            import subprocess
            # Try pdftotext first (better quality), fall back to textutil
            for cmd in [
                ["pdftotext", str(path), "-"],
                ["textutil", "-convert", "txt", "-stdout", str(path)],
            ]:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    if result.returncode == 0 and result.stdout.strip():
                        content = result.stdout[:max_chars]
                        return content
                except FileNotFoundError:
                    continue
        except subprocess.TimeoutExpired:
            pass
        return f"[PDF file: {path.name} — could not extract text. Try converting to CSV/TXT first.]"

    if suffix in (".xlsx", ".xls"):
        return f"[Spreadsheet: {path.name} — please export as CSV for analysis]"

    return f"[Unsupported file type: {path.suffix}]"


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a privacy-sensitive request using only local Ollama.

    If the message references files, uses local LLM to parse the description,
    then reads files locally and includes content in the prompt.
    All data stays on localhost.
    """
    extra = ""
    if thread_history:
        extra += f"\n\nPrevious conversation:\n{thread_history}"

    # Find and read referenced files — ALL LOCAL, no network
    files = _find_files(content)
    if files:
        file_sections = []
        total_chars = 0
        for f in files:
            remaining = _MAX_TOTAL_FILE_CHARS - total_chars
            if remaining <= 500:
                file_sections.append(f"### File: {f.name}\n[Skipped — prompt size limit reached]")
                continue
            file_content = _read_file(f, max_chars=min(remaining, 4000))
            file_sections.append(f"### File: {f.name}\nPath: {f}\n\n{file_content}")
            total_chars += len(file_content)
            log.info("Secret agent: read %s (%d chars)", f.name, len(file_content))
        extra += "\n\n## Referenced Files\n\n" + "\n\n---\n\n".join(file_sections)
    else:
        extra += "\n\n[No files found matching the description. Ask the user for the exact path or filename.]"

    prompt = content + extra
    prompt_len = len(prompt)
    log.info("Secret agent: task %s, %d chars (incl. %d files), model=%s",
             task_id, prompt_len, len(files), OLLAMA_DEFAULT_MODEL)

    # Adjust timeout based on prompt size
    timeout = min(600, max(120, prompt_len // 50))

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, system=_SYSTEM_PROMPT, timeout=timeout)

    if result:
        return result

    log.error("Secret agent: Ollama returned empty for task %s", task_id)
    return None
