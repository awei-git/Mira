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


def _parse_file_intent(query: str) -> dict:
    """Use local LLM to understand what files the user is referring to.

    Returns: {"directory": "path hint", "patterns": ["file patterns"],
              "description": "what user wants"}
    All local — Ollama only.
    """
    parse_prompt = f"""The user sent this message about files on their computer:

"{query}"

Extract the file location info. Return JSON only:
{{
  "directory_hints": ["keywords for the directory path, e.g. documents, tax, important"],
  "file_patterns": ["filename patterns or extensions to search for, e.g. *.csv, W-2, 1099"],
  "year": "year mentioned if any, e.g. 2025",
  "description": "brief description of what files they want"
}}

JSON only, no explanation."""

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, parse_prompt,
                         system="Extract file paths from natural language. JSON only.",
                         timeout=30)
    if not result:
        return {"directory_hints": [], "file_patterns": [], "year": "", "description": query}

    try:
        clean = result.strip().strip("```json").strip("```").strip()
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        log.warning("File intent parse failed, falling back to keyword extraction")
        return {"directory_hints": [], "file_patterns": [], "year": "", "description": query}


def _find_files(query: str) -> list[Path]:
    """Find files matching user's description using local LLM + filesystem search."""
    found = []

    # Step 1: Direct pattern extraction (fast, no LLM)
    # Quoted filenames
    for m in re.findall(r'["\']([^"\']+)["\']', query):
        p = Path(m).expanduser()
        if p.exists() and p.is_file():
            found.append(p)
    # Explicit file extensions
    for m in re.findall(r'(\S+\.(?:pdf|csv|txt|xlsx?|docx?|md|json))', query, re.IGNORECASE):
        p = Path(m).expanduser()
        if p.exists() and p.is_file():
            found.append(p)

    if found:
        return found[:10]

    # Step 2: LLM-assisted parsing (understands natural language paths)
    intent = _parse_file_intent(query)
    dir_hints = intent.get("directory_hints", [])
    file_patterns = intent.get("file_patterns", [])
    year = intent.get("year", "")

    log.info("File intent: dirs=%s patterns=%s year=%s", dir_hints, file_patterns, year)

    # Step 3: Search filesystem using parsed hints
    for base in _SEARCH_DIRS:
        if not base.exists():
            continue

        # Try to navigate to the described directory
        candidates = [base]
        for hint in dir_hints:
            hint_lower = hint.lower()
            new_candidates = []
            for cand in candidates:
                if not cand.is_dir():
                    continue
                # Check subdirs matching this hint
                try:
                    for sub in cand.iterdir():
                        if sub.is_dir() and hint_lower in sub.name.lower():
                            new_candidates.append(sub)
                except PermissionError:
                    continue
            if new_candidates:
                candidates = new_candidates

        # Search in candidate dirs for matching files
        for cand in candidates:
            try:
                for f in cand.rglob("*"):
                    if not f.is_file():
                        continue
                    name_lower = f.name.lower()

                    # Match by file pattern
                    matched = False
                    for pat in file_patterns:
                        pat_lower = pat.lower().replace("*", "")
                        if pat_lower in name_lower:
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

                    if matched and len(found) < 10:
                        found.append(f)
            except PermissionError:
                continue

    return found[:10]


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
        try:
            import subprocess
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout[:max_chars]
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
        for f in files:
            file_content = _read_file(f)
            file_sections.append(f"### File: {f.name}\nPath: {f}\n\n{file_content}")
            log.info("Secret agent: read local file %s (%d chars)", f.name, len(file_content))
        extra += "\n\n## Referenced Files\n\n" + "\n\n---\n\n".join(file_sections)
    else:
        # Tell the LLM no files were found so it can ask the user
        extra += "\n\n[No files found matching the description. Ask the user for the exact path or filename.]"

    prompt = content + extra
    log.info("Secret agent: task %s, %d chars (incl. %d files), model=%s",
             task_id, len(prompt), len(files), OLLAMA_DEFAULT_MODEL)

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, system=_SYSTEM_PROMPT, timeout=300)

    if result:
        return result

    log.error("Secret agent: Ollama returned empty for task %s", task_id)
    return None
