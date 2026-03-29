"""Manage the agent's soul: identity, memory, interests, skills."""
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from config import (
    IDENTITY_FILE, MEMORY_FILE, INTERESTS_FILE, WORLDVIEW_FILE,
    READING_NOTES_DIR, SKILLS_DIR, SKILLS_INDEX, SKILLS_FILE,
    MAX_MEMORY_LINES, MIRA_ROOT, CONVERSATIONS_DIR,
    EPISODES_DIR, CATALOG_FILE,
)

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Safe file write utilities — prevent data loss from concurrent writes
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str):
    """Write file atomically via tmp + fsync + rename.

    Prevents partial writes if process is killed mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _locked_write(path: Path, content: str):
    """Atomic write with exclusive file lock.

    Use for files shared across concurrent processes (memory.md,
    worldview.md, interests.md, scores.json, catalog.jsonl, etc.).
    Blocks until lock is acquired (up to 10s timeout).
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)  # blocking
        try:
            _atomic_write(path, content)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _locked_read_modify_write(path: Path, modify_fn):
    """Read file, apply modify_fn, write back — all under lock.

    modify_fn(current_text: str) -> new_text: str
    If file doesn't exist, modify_fn receives empty string.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            new_content = modify_fn(current)
            _atomic_write(path, new_content)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Soul integrity — hash verification + backup rotation
# ---------------------------------------------------------------------------

_HASH_FILE = IDENTITY_FILE.parent / ".soul_hashes.json"
_BACKUP_DIR = IDENTITY_FILE.parent / ".backups"
_MAX_BACKUPS = 3

# Files that define who Mira is — integrity-protected
_PROTECTED_FILES = {
    "identity": IDENTITY_FILE,
    "worldview": WORLDVIEW_FILE,
}


def _compute_hash(path: Path) -> str:
    """SHA-256 of file content."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_hashes():
    """Recompute and save hashes for all protected files."""
    hashes = {}
    for name, path in _PROTECTED_FILES.items():
        hashes[name] = _compute_hash(path)
    hashes["updated_at"] = datetime.now().isoformat()
    _atomic_write(_HASH_FILE, json.dumps(hashes, indent=2))


def _load_hashes() -> dict:
    """Load stored hashes."""
    if not _HASH_FILE.exists():
        return {}
    try:
        return json.loads(_HASH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _rotate_backup(path: Path):
    """Keep last N backups of a soul file."""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = _BACKUP_DIR / f"{path.stem}_{ts}{path.suffix}"
    if path.exists():
        shutil.copy2(path, backup)
    # Prune old backups
    backups = sorted(_BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)
    for old in backups[_MAX_BACKUPS:]:
        old.unlink()


def verify_soul_integrity() -> list[str]:
    """Check protected soul files against stored hashes.

    Returns list of integrity violations (empty = all good).
    On violation: logs CRITICAL, restores from backup if available.
    """
    stored = _load_hashes()
    if not stored:
        # First run — save current hashes as baseline
        _save_hashes()
        return []

    violations = []
    for name, path in _PROTECTED_FILES.items():
        expected = stored.get(name, "")
        if not expected:
            continue  # No stored hash yet
        actual = _compute_hash(path)
        if actual != expected:
            violations.append(name)
            log.critical(
                "SOUL INTEGRITY VIOLATION: %s has been modified outside authorized writes! "
                "Expected hash %s, got %s", name, expected[:12], actual[:12])

            # Try to restore from backup
            backups = sorted(_BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)
            if backups:
                latest_backup = backups[0]
                backup_hash = hashlib.sha256(latest_backup.read_bytes()).hexdigest()
                if backup_hash == expected:
                    shutil.copy2(latest_backup, path)
                    log.critical("Restored %s from backup %s", name, latest_backup.name)
                else:
                    log.critical("Backup hash also differs — manual review needed for %s", name)
            else:
                log.critical("No backups available for %s — manual review needed", name)

    return violations


def _protected_write(path: Path, content: str):
    """Write a protected soul file: backup → write → update hash."""
    _rotate_backup(path)
    _locked_write(path, content)
    _save_hashes()


def load_soul() -> dict:
    """Load the full soul context. Verifies integrity of protected files."""
    violations = verify_soul_integrity()
    if violations:
        log.critical("Soul integrity check failed: %s", violations)

    return {
        "identity": _read_or_default(IDENTITY_FILE, "No identity defined yet."),
        "memory": _read_or_default(MEMORY_FILE, "No memories yet."),
        "interests": _read_or_default(INTERESTS_FILE, "No interests defined yet."),
        "worldview": _read_or_default(WORLDVIEW_FILE, "No worldview yet."),
        "skills": load_skills_summary(),
    }


def format_soul(soul: dict) -> str:
    """Format the full soul as a string for injection into prompts."""
    parts = [
        "# My Identity\n",
        soul["identity"],
        "\n\n# My Worldview\n",
        soul["worldview"],
        "\n\n# My Memory\n",
        soul["memory"],
        "\n\n# My Current Interests\n",
        soul["interests"],
    ]
    if soul["skills"]:
        parts.append("\n\n# My Skills\n")
        parts.append(soul["skills"])

    # Self-evaluation scorecard (if available)
    try:
        from evaluator import format_scorecard
        card = format_scorecard()
        if card:
            parts.append("\n\n# My Self-Evaluation Scores\n")
            parts.append(card)
    except (ImportError, ModuleNotFoundError) as e:
        log.debug("Scorecard loading skipped: %s", e)

    # Active improvement plan (from score → action pipeline)
    try:
        from evaluator import get_active_improvements
        improvements = get_active_improvements()
        if improvements:
            parts.append("\n\n# Active Self-Improvement Focus\n")
            parts.append(improvements)
    except (ImportError, ModuleNotFoundError):
        pass

    return "".join(parts)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def append_memory(entry: str):
    """Append a timestamped entry to memory. Enforces MAX_MEMORY_LINES.

    Overflowed lines (trimmed from memory.md) persist in PostgreSQL
    episodic_memory as 'memory_overflow' and remain searchable via vector search.
    """
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {entry}\n"
    overflow_lines = []

    def _modify(text):
        nonlocal overflow_lines
        if not text:
            text = "# Memory\n\n"
        text += line
        lines = text.split("\n")
        if len(lines) > MAX_MEMORY_LINES:
            header = lines[:2]
            entries = lines[2:]
            overflow_lines = entries[:-(MAX_MEMORY_LINES - 2)]
            trimmed = entries[-(MAX_MEMORY_LINES - 2):]
            text = "\n".join(header + trimmed)
            log.info("Memory trimmed to %d lines", MAX_MEMORY_LINES)
        return text

    _locked_read_modify_write(MEMORY_FILE, _modify)
    log.info("Memory +: %s", entry[:80])

    # Persist to Postgres (non-blocking best-effort)
    try:
        from memory_store import get_store
        store = get_store()
        store.remember(entry, source_type="memory_entry", importance=0.5)
        # Persist overflowed lines so they remain searchable
        if overflow_lines:
            overflow_text = "\n".join(overflow_lines)
            store.remember(overflow_text, source_type="memory_overflow",
                           importance=0.3)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.debug("Postgres memory persist skipped: %s", e)


def update_memory(new_content: str):
    """Replace memory file with new content (used by reflect mode)."""
    _locked_write(MEMORY_FILE, new_content)
    log.info("Memory updated (%d lines)", new_content.count("\n"))


def get_memory_size() -> int:
    """Return line count of memory file."""
    if not MEMORY_FILE.exists():
        return 0
    return MEMORY_FILE.read_text(encoding="utf-8").count("\n")


# ---------------------------------------------------------------------------
# Interests
# ---------------------------------------------------------------------------

def update_interests(new_content: str):
    """Replace interests file."""
    _locked_write(INTERESTS_FILE, new_content)
    log.info("Interests updated")


# ---------------------------------------------------------------------------
# Worldview
# ---------------------------------------------------------------------------

def update_worldview(new_content: str):
    """Replace worldview file (used by reflect). Integrity-protected."""
    _protected_write(WORLDVIEW_FILE, new_content)
    log.info("Worldview updated (%d lines)", new_content.count("\n"))


# ---------------------------------------------------------------------------
# Reading Notes
# ---------------------------------------------------------------------------

def save_reading_note(title: str, reflection: str):
    """Save a personal reading reflection after deep dive."""
    READING_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    slug = title.lower().replace(" ", "-")[:40]
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    path = READING_NOTES_DIR / f"{today}_{slug}.md"
    _atomic_write(path, f"# Reading Note: {title}\n\n*{today}*\n\n{reflection}")
    log.info("Reading note saved: %s", path.name)
    return path


def load_recent_reading_notes(days: int = 14) -> str:
    """Load recent reading notes for use in reflect/journal."""
    if not READING_NOTES_DIR.exists():
        return ""
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(READING_NOTES_DIR.glob("*.md")):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                texts.append(content[:1500])
        except ValueError:
            continue
    return "\n\n---\n\n".join(texts) if texts else ""


def detect_recurring_themes(days: int = 7) -> list[str]:
    """Scan recent journals + reading notes for recurring themes.

    Returns a list of theme strings that appear in 3+ entries.
    Simple keyword frequency approach — good enough to seed autonomous writing.
    """
    from collections import Counter
    import re

    texts = []
    # Gather journal entries
    journal_dir = WORLDVIEW_FILE.parent / "journal"
    if journal_dir.exists():
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        for path in sorted(journal_dir.glob("*.md")):
            try:
                date_str = path.stem[:10]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date >= cutoff:
                    texts.append(path.read_text(encoding="utf-8"))
            except ValueError:
                continue

    # Gather reading notes
    notes = load_recent_reading_notes(days=days)
    if notes:
        texts.append(notes)

    if not texts:
        return []

    # Extract significant phrases (simple: lines that start with "-" or contain key patterns)
    combined = "\n".join(texts)
    # Look for concepts mentioned multiple times across entries
    # Extract capitalized concepts, quoted terms, and bold terms
    concepts = re.findall(r'\*\*(.+?)\*\*', combined)
    concepts += re.findall(r'"(.+?)"', combined)
    concepts += re.findall(r'「(.+?)」', combined)

    # Count occurrences (case-insensitive)
    counter = Counter(c.lower().strip() for c in concepts if len(c) > 3)
    return [theme for theme, count in counter.most_common(10) if count >= 3]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def load_skills_summary() -> str:
    """Load a summary of all skills (names + one-liners)."""
    if not SKILLS_INDEX.exists():
        return ""
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    lines = []
    for skill in index:
        lines.append(f"- **{skill['name']}**: {skill.get('description', '')}")
    return "\n".join(lines)


def load_skills_for_task(task_content: str, agent_type: str = "",
                         max_skills: int = 8) -> str:
    """Load full skill content filtered by relevance to the task.

    Returns full text of the most relevant skills (not just summaries).
    Uses tag matching and agent-type affinity to select skills.
    Falls back to summaries-only if no strong matches.
    """
    if not SKILLS_INDEX.exists():
        return ""
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    lower = task_content.lower()
    # Agent-type to tag affinity map
    _AGENT_TAGS = {
        "writing": {"writing", "craft", "fiction", "substack"},
        "analyst": {"analyst", "strategy", "research", "forecasting"},
        "video": {"video", "editing", "cinematography"},
        "photo": {"photo", "editing", "color"},
        "researcher": {"math", "proof", "probability", "research", "paper"},
        "general": {"coding", "agents", "debugging"},
        "explorer": {"explorer", "research", "curation"},
    }
    affinity_tags = _AGENT_TAGS.get(agent_type, set())

    scored = []
    for skill in index:
        tags = set(t.lower() for t in skill.get("tags", []))
        desc = skill.get("description", "").lower()
        name = skill.get("name", "").lower()
        score = 0
        # Tag overlap with agent type
        score += len(tags & affinity_tags) * 3
        # Tag words appearing in task content
        score += sum(2 for t in tags if t in lower)
        # Name words in task content
        score += sum(1 for w in name.split() if len(w) > 3 and w in lower)
        # Description words in task content
        desc_words = {w for w in desc.split() if len(w) > 3}
        content_words = {w for w in lower.split() if len(w) > 3}
        score += len(desc_words & content_words)

        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_skills]

    if not top:
        return ""

    sections = []
    for _, skill in top:
        slug = skill.get("name", "").lower().replace(" ", "-")
        path = SKILLS_DIR / f"{slug}.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                # Truncate very long skills to save tokens
                if len(text) > 2000:
                    text = text[:2000] + "\n... (truncated)"
                sections.append(text)
            except OSError:
                pass
    return "\n\n---\n\n".join(sections)


def load_skill(name: str) -> str:
    """Load a specific skill file's full content."""
    # Normalize name to filename
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Skill Security Audit
# ---------------------------------------------------------------------------

# Patterns that indicate potentially malicious skill content
_SUSPICIOUS_URL_PATTERNS = [
    r'https?://\S+\.exe',                      # Direct executable downloads
    r'https?://\S+\.sh',                        # Remote shell scripts
    r'https?://\S+\.bat',                       # Batch files
    r'https?://\S+\.msi',                       # Windows installers
    r'https?://\S+\.dmg',                       # macOS disk images
    r'https?://\S+\.pkg',                       # macOS packages
    r'curl\s+.*\|\s*(ba)?sh',                   # Pipe curl to shell
    r'wget\s+.*\|\s*(ba)?sh',                   # Pipe wget to shell
    r'curl\s+.*-o\s+\S+.*&&.*chmod\s+\+x',     # Download + make executable
    r'requests\.get\s*\(',                       # Python HTTP requests
    r'urllib\.request',                          # Python urllib
    r'subprocess\..*shell\s*=\s*True',           # Shell injection via subprocess
]

_DANGEROUS_FS_PATTERNS = [
    r'os\.system\s*\(',                          # Raw system calls
    r'exec\s*\(',                                # Dynamic code execution
    r'eval\s*\(',                                # Dynamic expression evaluation
    r'__import__\s*\(',                          # Dynamic imports
    r'chmod\s+\+[xs]',                           # Making files executable/setuid
    r'rm\s+-rf\s+/',                             # Destructive delete from root
    r'shutil\.rmtree\s*\(',                      # Programmatic recursive delete
    r'os\.remove|os\.unlink',                    # File deletion
    r'open\s*\(.*["\']w["\']',                   # File writes (suspicious in skill context)
    r'\.write\s*\(',                             # Write operations
]

_OBFUSCATION_PATTERNS = [
    r'base64\.(b64)?decode',                     # Base64 decoding (hiding payloads)
    r'\\x[0-9a-fA-F]{2}',                       # Hex-encoded strings
    r'\\u[0-9a-fA-F]{4}',                        # Unicode escape sequences
    r'codecs\.(decode|encode)',                   # Codec-based obfuscation
    r'chr\s*\(\s*\d+\s*\)',                      # Character-from-int construction
    r'bytes\.fromhex',                           # Hex-to-bytes
    r'compile\s*\(',                             # Dynamic code compilation
    r'marshal\.(loads|dumps)',                    # Serialized code objects
]

_PRIVILEGE_ESCALATION_PATTERNS = [
    r'sudo\s+',                                  # Privilege escalation
    r'chmod\s+[0-7]*[4-7][0-7]{2}',             # Setuid/setgid permissions
    r'keychain|keyring',                         # Credential store access
    r'\.ssh/',                                   # SSH key access
    r'\.env\b',                                  # Environment file access
    r'password|passwd|secret|token|api_key',     # Sensitive credential patterns
    r'OPENAI_API_KEY|ANTHROPIC_API_KEY',         # Specific API keys
    r'/etc/shadow|/etc/passwd',                  # System credential files
    r'launchctl\s+load',                         # macOS persistence
    r'crontab',                                  # Scheduled task persistence
]

_PROMPT_INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+instructions',  # "IGNORE PREVIOUS INSTRUCTIONS"
    r'ignore\s+(all\s+)?prior\s+instructions',      # variant
    r'disregard\s+(all\s+)?previous',                # variant
    r'<system>',                                      # Fake XML system block
    r'<CLAUDE',                                       # Fake Claude instruction block
    r'<instructions>',                                # Fake instructions block
    r'</?(system|SYSTEM)\s*>',                        # System tag open/close
    r'you\s+are\s+now\s+',                            # "You are now" role hijack
    r'new\s+instructions\s*:',                        # "New instructions:"
    r'SYSTEM\s+PROMPT\s*:',                           # "SYSTEM PROMPT:"
    r'ASSISTANT\s+PROMPT\s*:',                        # "ASSISTANT PROMPT:"
    r'[A-Za-z0-9+/]{100,}={0,2}',                    # Large base64-encoded blocks (100+ chars)
    r'[\u200b\u200c\u200d\ufeff]{3,}',               # 3+ consecutive zero-width characters
]

# Audit coverage metadata — explicitly tracks what we check and what we don't
_AUDIT_CHECKS_PERFORMED = [
    "network_requests", "dangerous_ops", "obfuscation",
    "privilege_escalation", "prompt_injection",
]
_AUDIT_CHECKS_NOT_COVERED = [
    "runtime_behavior", "data_exfiltration_via_output",
    "semantic_intent_analysis", "multi-step_attack_chains",
]


def audit_skill(name: str, content: str) -> tuple[bool, list[str]]:
    """Audit a skill for security risks before saving.

    Returns (passed, violations) where passed is True if the skill is safe,
    and violations is a list of human-readable issue descriptions.
    """
    violations = []
    combined = f"{name}\n{content}"

    checks = [
        (_SUSPICIOUS_URL_PATTERNS, "Suspicious network request"),
        (_DANGEROUS_FS_PATTERNS, "Dangerous filesystem/code operation"),
        (_OBFUSCATION_PATTERNS, "Obfuscated or hidden code"),
        (_PRIVILEGE_ESCALATION_PATTERNS, "Privilege escalation or credential access"),
        (_PROMPT_INJECTION_PATTERNS, "Prompt injection attempt"),
    ]

    for patterns, category in checks:
        for pattern in patterns:
            matches = re.findall(pattern, combined, re.IGNORECASE)
            if matches:
                # Get first match for reporting
                sample = matches[0] if isinstance(matches[0], str) else str(matches[0])
                violations.append(f"[{category}] Pattern '{pattern}' matched: '{sample[:80]}'")

    passed = len(violations) == 0
    checked = ", ".join(_AUDIT_CHECKS_PERFORMED)
    not_checked = ", ".join(_AUDIT_CHECKS_NOT_COVERED)
    if not passed:
        log.warning("Skill '%s' BLOCKED (checked: %s | NOT checked: %s) — %d violation(s)",
                    name, checked, not_checked, len(violations))
        for v in violations:
            log.warning("  - %s", v)
    else:
        log.info("Skill '%s' PASSED (checked: %s | NOT checked: %s)",
                 name, checked, not_checked)

    return passed, violations


def save_skill(name: str, description: str, content: str):
    """Save a new skill and update the index. Runs security audit first."""
    # --- Security audit gate ---
    passed, violations = audit_skill(name, content)
    if not passed:
        log.warning(
            "BLOCKED skill '%s' — failed security audit with %d violation(s):",
            name, len(violations),
        )
        for v in violations:
            log.warning("  %s", v)
        return  # Do not save
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    _atomic_write(path, content)

    # Update index (locked — shared across processes)
    def _update_index(text):
        index = []
        if text:
            try:
                index = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
        index = [s for s in index if s["name"] != name]
        index.append({
            "name": name,
            "description": description,
            "file": f"{slug}.md",
            "created": datetime.now().isoformat(),
        })
        return json.dumps(index, indent=2, ensure_ascii=False)

    _locked_read_modify_write(SKILLS_INDEX, _update_index)
    log.info("Saved skill: %s", name)

    # Keep skills.md in sync
    rebuild_skills_md()

    # Sync actionable skills to CLAUDE.md for Claude Code sessions
    _sync_skills_to_claude_md()


def rebuild_skills_md():
    """Regenerate soul/skills.md from index + individual skill files."""
    if not SKILLS_INDEX.exists():
        return
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    lines = [f"# Skills ({len(index)} learned)\n"]
    for entry in index:
        name = entry["name"]
        desc = entry.get("description", "")
        added = entry.get("added") or entry.get("created", "")[:10]
        lines.append(f"## {name}")
        lines.append(f"*{desc}*  ")
        if added:
            lines.append(f"Learned: {added}  ")
        # Include the full skill content
        skill_file = SKILLS_DIR / entry.get("file", "")
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8").strip()
            lines.append("")
            lines.append(content)
        lines.append("\n---\n")

    _locked_write(SKILLS_FILE, "\n".join(lines))
    log.info("Rebuilt skills.md (%d skills)", len(index))


# ---------------------------------------------------------------------------
# Claude Code skill sync
# ---------------------------------------------------------------------------

# Tags that indicate a skill is actionable for Claude Code sessions
_ACTIONABLE_TAGS = {"writing", "craft", "fiction", "dialogue", "video", "editing",
                    "agents", "coding", "architecture", "tool-use", "debugging",
                    "math", "proof", "latex", "exposition", "publishing",
                    "research", "problem-solving", "asymptotics"}

# CLAUDE.md lives at MtJoy root so all Claude Code sessions in MtJoy see it
_CLAUDE_MD = MIRA_ROOT.parent / "CLAUDE.md"


_AGENT_SKILL_INDEXES = [
    # Per-agent skill index files (relative to agents dir)
    Path(__file__).parent.parent / "researcher" / "skills" / "index.json",
    Path(__file__).parent.parent / "coder" / "skills" / "index.json",
    Path(__file__).parent.parent / "general" / "skills" / "index.json",
    Path(__file__).parent.parent / "analyst" / "skills" / "index.json",
    Path(__file__).parent.parent / "explorer" / "skills" / "index.json",
    Path(__file__).parent.parent / "photo" / "skills" / "index.json",
    Path(__file__).parent.parent / "video" / "skills" / "index.json",
    Path(__file__).parent.parent / "podcast" / "skills" / "index.json",
    Path(__file__).parent.parent / "socialmedia" / "skills" / "index.json",
    Path(__file__).parent.parent / "writer" / "skills" / "index.json",
    Path(__file__).parent.parent / "super" / "skills" / "index.json",
]


def _load_all_skill_indexes() -> list[dict]:
    """Load skills from soul/learned/ plus all per-agent skill indexes."""
    all_skills = []
    # Primary soul/learned index
    if SKILLS_INDEX.exists():
        try:
            all_skills.extend(json.loads(SKILLS_INDEX.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    # Per-agent indexes
    for index_path in _AGENT_SKILL_INDEXES:
        if index_path.exists():
            try:
                all_skills.extend(json.loads(index_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
    return all_skills


def _sync_skills_to_claude_md():
    """Rebuild the skills section of MtJoy/CLAUDE.md from all skill indexes.

    Only promotes skills with actionable tags. Keeps existing non-skill
    content in CLAUDE.md intact.
    """
    index = _load_all_skill_indexes()
    if not index:
        return

    # Filter: only skills with at least one actionable tag
    actionable = []
    for skill in index:
        tags = set(skill.get("tags", []))
        if tags & _ACTIONABLE_TAGS:
            actionable.append(skill)

    if not actionable:
        return

    # Build skills section — concise one-liners only.
    # Full skill content stays in soul/skills/ for the agent's own use.
    skill_lines = ["## Learned Skills (auto-synced from Mira agent)", ""]
    for skill in actionable:
        name = skill["name"]
        desc = skill.get("description", "")
        tags = ", ".join(skill.get("tags", []))
        skill_lines.append(f"- **{name}** [{tags}]: {desc}")
    skill_lines.append("")
    skill_lines.append(f"Full skill details: `Mira/agents/shared/soul/learned/` and `Mira/agents/math/skills/`")
    skill_lines.append("")

    skills_block = "\n".join(skill_lines)

    # Read existing CLAUDE.md or start fresh
    MARKER_START = "<!-- MIRA-SKILLS-START -->"
    MARKER_END = "<!-- MIRA-SKILLS-END -->"

    if _CLAUDE_MD.exists():
        existing = _CLAUDE_MD.read_text(encoding="utf-8")
    else:
        existing = ""

    # Replace or append the skills section
    if MARKER_START in existing:
        before = existing[:existing.index(MARKER_START)]
        after_marker = existing[existing.index(MARKER_END) + len(MARKER_END):]
        new_content = f"{before}{MARKER_START}\n{skills_block}\n{MARKER_END}{after_marker}"
    else:
        if existing:
            new_content = f"{existing}\n\n{MARKER_START}\n{skills_block}\n{MARKER_END}\n"
        else:
            new_content = f"{MARKER_START}\n{skills_block}\n{MARKER_END}\n"

    _locked_write(_CLAUDE_MD, new_content)
    log.info("Synced %d actionable skills to CLAUDE.md", len(actionable))


# ---------------------------------------------------------------------------
# Semantic memory search (via memory_index)
# ---------------------------------------------------------------------------

def search_memory(query: str, top_k: int = 5) -> str:
    """Search across all soul files using vector + keyword hybrid search.

    Returns formatted results for injection into prompts.
    Uses PostgreSQL + pgvector via memory_store, falls back to SQLite memory_index.
    """
    try:
        from memory_store import search_formatted
        return search_formatted(query, top_k=top_k)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.warning("Memory search failed: %s", e)
        return ""


def rebuild_memory_index(force: bool = False) -> int:
    """Rebuild the semantic memory index. Call after major memory changes."""
    try:
        from memory_store import rebuild_index
        return rebuild_index(force=force)
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.warning("Memory index rebuild failed: %s", e)
        return 0


def auto_flush(context_summary: str):
    """Save important context before it's lost (e.g. before context compaction).

    Call this when an agent session is winding down or context is large.
    Saves to conversations/ archive (NOT memory.md — that's for cognitive insights only).
    """
    if not context_summary or len(context_summary.strip()) < 50:
        return

    # Save as a conversation archive file (indexed by memory_index)
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = CONVERSATIONS_DIR / f"flush_{ts}.md"
    _atomic_write(path, f"# Context Flush ({ts})\n\n{context_summary[:2000]}\n")
    log.info("Auto-flush saved to %s", path.name)

    # Trigger async index rebuild (non-blocking)
    try:
        rebuild_memory_index()
    except (ImportError, ModuleNotFoundError, ConnectionError, RuntimeError) as e:
        log.warning("Auto-flush index rebuild failed: %s", e)


# ---------------------------------------------------------------------------
# Episode Archival — save complete conversations for long-term recall
# ---------------------------------------------------------------------------

def save_episode(task_id: str, title: str, messages: list[dict],
                 tags: list[str] | None = None):
    """Archive a complete task conversation as a searchable episode.

    Episodes are indexed by memory_index for semantic search, enabling
    Mira to recall past discussions, decisions, and context.
    """
    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H%M")

    # Deduplicate: remove any existing episode for this task_id
    for existing in EPISODES_DIR.glob("*.md"):
        try:
            head = existing.read_text(encoding="utf-8")[:200]
            if f"Task: {task_id}" in head:
                existing.unlink()
                log.info("Replaced existing episode for task %s", task_id)
                break
        except OSError:
            continue

    # Build readable markdown from conversation
    lines = [f"# Episode: {title}", f"*Task: {task_id} | Date: {today}*", ""]
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
        lines.append("")

    for msg in messages:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        # Skip status cards
        if content.startswith('{"type":'):
            continue
        msg_ts = msg.get("timestamp", "")[:16]
        lines.append(f"**[{msg_ts}] {sender}**: {content}")
        lines.append("")

    slug = re.sub(r"[^\w\s-]", "", title.lower())[:40].strip().replace(" ", "-")
    filename = f"{today}_{ts}_{slug or task_id}.md"
    path = EPISODES_DIR / filename
    episode_text = "\n".join(lines)
    _atomic_write(path, episode_text)
    log.info("Episode saved: %s (%d messages)", filename, len(messages))

    # Persist to Postgres for vector search (best-effort)
    try:
        from memory_store import get_store
        store = get_store()
        # Store a summary (first 2000 chars) as an episodic memory entry
        store.remember(
            episode_text[:2000],
            source_type="episode",
            source_id=task_id,
            title=title,
            importance=0.6,
            tags=tags,
        )
    except (ImportError, ModuleNotFoundError, ConnectionError, OSError) as e:
        log.debug("Postgres episode persist skipped: %s", e)
    return path


# ---------------------------------------------------------------------------
# Content Catalog — structured metadata for all produced content
# ---------------------------------------------------------------------------

def catalog_add(entry: dict):
    """Add an entry to the content catalog.

    Entry should have: type, title, date, path, topics, status.
    Optional: substack_id, description, source_task.
    Deduplicates by (type, title) — updates existing entry if found.
    """
    # Ensure required fields
    entry.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    entry.setdefault("topics", [])
    entry.setdefault("status", "draft")
    key = (entry.get("type", ""), entry.get("title", ""))

    def _modify(text):
        entries = []
        if text:
            for line in text.strip().splitlines():
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        entries = [e for e in entries
                   if (e.get("type", ""), e.get("title", "")) != key]
        entries.append(entry)
        return "\n".join(json.dumps(e, ensure_ascii=False)
                         for e in entries) + "\n"

    _locked_read_modify_write(CATALOG_FILE, _modify)
    log.info("Catalog +: [%s] %s", entry.get("type"), entry.get("title", "")[:60])


def catalog_search(query: str, content_type: str | None = None) -> list[dict]:
    """Search the content catalog by keyword. Returns matching entries."""
    if not CATALOG_FILE.exists():
        return []

    query_lower = query.lower()
    results = []
    for line in CATALOG_FILE.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if content_type and entry.get("type") != content_type:
            continue
        # Match against title, topics, description
        searchable = " ".join([
            entry.get("title", ""),
            " ".join(entry.get("topics", [])),
            entry.get("description", ""),
        ]).lower()
        if query_lower in searchable:
            results.append(entry)

    return results


def catalog_list(content_type: str | None = None) -> list[dict]:
    """List all catalog entries, optionally filtered by type."""
    if not CATALOG_FILE.exists():
        return []

    entries = []
    for line in CATALOG_FILE.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if content_type and entry.get("type") != content_type:
            continue
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Proactive Recall — search memory before acting
# ---------------------------------------------------------------------------

def recall_context(query: str, max_chars: int = 2000) -> str:
    """Search memory for relevant prior context before starting a task.

    Returns formatted context string for injection into task prompts.
    Searches both semantic memory index and content catalog.
    """
    parts = []

    # 1. Semantic memory search (episodes, journals, reading notes, etc.)
    mem_results = search_memory(query, top_k=3)
    if mem_results:
        parts.append("## Relevant memories\n" + mem_results)

    # 2. Content catalog search
    catalog_hits = catalog_search(query)
    if catalog_hits:
        cat_lines = ["## Related content I've produced"]
        for hit in catalog_hits[:5]:
            cat_lines.append(
                f"- [{hit.get('type')}] \"{hit.get('title')}\" "
                f"({hit.get('date', '?')}, {hit.get('status', '?')})"
            )
        parts.append("\n".join(cat_lines))

    result = "\n\n".join(parts)
    return result[:max_chars] if result else ""


# ---------------------------------------------------------------------------
# Retention policy — prevent unbounded growth of journal/reading_notes/episodes
# ---------------------------------------------------------------------------

RETENTION_DAYS_JOURNAL = 90       # keep 3 months of daily journals
RETENTION_DAYS_READING_NOTES = 90 # keep 3 months of reading notes
RETENTION_DAYS_EPISODES = 60      # keep 2 months of episodes


def prune_old_files(directory: Path, max_age_days: int, label: str = "") -> int:
    """Delete files older than max_age_days from a date-prefixed directory.

    Files must start with YYYY-MM-DD to be considered for pruning.
    Returns the number of files deleted.
    """
    if not directory.exists():
        return 0

    cutoff = datetime.now() - __import__("datetime").timedelta(days=max_age_days)
    deleted = 0
    for path in directory.glob("*.md"):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                path.unlink()
                deleted += 1
        except (ValueError, OSError):
            continue
    if deleted:
        log.info("Retention: pruned %d old %s files (>%d days)",
                 deleted, label or directory.name, max_age_days)
    return deleted


def run_retention_policy():
    """Prune old files across all date-indexed directories.

    Call from journal cycle (daily) to keep disk usage bounded.
    """
    total = 0
    total += prune_old_files(READING_NOTES_DIR, RETENTION_DAYS_READING_NOTES, "reading_notes")
    total += prune_old_files(EPISODES_DIR, RETENTION_DAYS_EPISODES, "episodes")
    # Journal: keep longer history since it's the primary record
    journal_dir = MEMORY_FILE.parent / "journal"
    total += prune_old_files(journal_dir, RETENTION_DAYS_JOURNAL, "journal")
    return total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_or_default(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return default
