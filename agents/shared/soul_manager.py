"""Manage the agent's soul: identity, memory, interests, skills."""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import (
    IDENTITY_FILE, MEMORY_FILE, INTERESTS_FILE, WORLDVIEW_FILE,
    READING_NOTES_DIR, SKILLS_DIR, SKILLS_INDEX, SKILLS_FILE,
    MAX_MEMORY_LINES, MIRA_ROOT,
)

log = logging.getLogger("mira")


def load_soul() -> dict:
    """Load the full soul context."""
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
    except Exception:
        pass

    return "".join(parts)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def append_memory(entry: str):
    """Append a timestamped entry to memory. Enforces MAX_MEMORY_LINES."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {entry}\n"

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8")
    else:
        text = "# Memory\n\n"

    text += line

    # Enforce line limit — trim oldest entries if over MAX_MEMORY_LINES
    lines = text.split("\n")
    if len(lines) > MAX_MEMORY_LINES:
        header = lines[:2]
        entries = lines[2:]
        trimmed = entries[-(MAX_MEMORY_LINES - 2):]
        text = "\n".join(header + trimmed)
        log.info("Memory trimmed to %d lines", MAX_MEMORY_LINES)

    MEMORY_FILE.write_text(text, encoding="utf-8")
    log.info("Memory +: %s", entry[:80])


def update_memory(new_content: str):
    """Replace memory file with new content (used by reflect mode)."""
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(new_content, encoding="utf-8")
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
    INTERESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INTERESTS_FILE.write_text(new_content, encoding="utf-8")
    log.info("Interests updated")


# ---------------------------------------------------------------------------
# Worldview
# ---------------------------------------------------------------------------

def update_worldview(new_content: str):
    """Replace worldview file (used by reflect)."""
    WORLDVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORLDVIEW_FILE.write_text(new_content, encoding="utf-8")
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
    path.write_text(
        f"# Reading Note: {title}\n\n*{today}*\n\n{reflection}",
        encoding="utf-8",
    )
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
    ]

    for patterns, category in checks:
        for pattern in patterns:
            matches = re.findall(pattern, combined, re.IGNORECASE)
            if matches:
                # Get first match for reporting
                sample = matches[0] if isinstance(matches[0], str) else str(matches[0])
                violations.append(f"[{category}] Pattern '{pattern}' matched: '{sample[:80]}'")

    passed = len(violations) == 0
    if not passed:
        log.warning("Skill '%s' FAILED security audit: %d violation(s)", name, len(violations))
        for v in violations:
            log.warning("  - %s", v)
    else:
        log.info("Skill '%s' passed security audit", name)

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
    path.write_text(content, encoding="utf-8")

    # Update index
    index = []
    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Remove old entry with same name if exists
    index = [s for s in index if s["name"] != name]
    index.append({
        "name": name,
        "description": description,
        "file": f"{slug}.md",
        "created": datetime.now().isoformat(),
    })

    SKILLS_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
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

    SKILLS_FILE.write_text("\n".join(lines), encoding="utf-8")
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
    Path(__file__).parent.parent / "math" / "skills" / "index.json",
    Path(__file__).parent.parent / "coder" / "skills" / "index.json",
    Path(__file__).parent.parent / "general" / "skills" / "index.json",
    Path(__file__).parent.parent / "analyst" / "skills" / "index.json",
    Path(__file__).parent.parent / "explorer" / "skills" / "index.json",
    Path(__file__).parent.parent / "photo" / "skills" / "index.json",
    Path(__file__).parent.parent / "video" / "skills" / "index.json",
    Path(__file__).parent.parent / "podcast" / "skills" / "index.json",
    Path(__file__).parent.parent / "socialmedia" / "skills" / "index.json",
    Path(__file__).parent.parent / "writer" / "skills" / "index.json",
    Path(__file__).parent.parent / "researcher" / "skills" / "index.json",
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

    _CLAUDE_MD.write_text(new_content, encoding="utf-8")
    log.info("Synced %d actionable skills to CLAUDE.md", len(actionable))


# ---------------------------------------------------------------------------
# Semantic memory search (via memory_index)
# ---------------------------------------------------------------------------

def search_memory(query: str, top_k: int = 5) -> str:
    """Search across all soul files using vector + keyword hybrid search.

    Returns formatted results for injection into prompts.
    Falls back gracefully if the index hasn't been built yet.
    """
    try:
        from memory_index import search_formatted
        return search_formatted(query, top_k=top_k)
    except Exception as e:
        log.warning("Memory search failed (index may not exist yet): %s", e)
        return ""


def rebuild_memory_index(force: bool = False) -> int:
    """Rebuild the semantic memory index. Call after major memory changes."""
    try:
        from memory_index import rebuild_index
        return rebuild_index(force=force)
    except Exception as e:
        log.warning("Memory index rebuild failed: %s", e)
        return 0


def auto_flush(context_summary: str):
    """Save important context before it's lost (e.g. before context compaction).

    Call this when an agent session is winding down or context is large.
    Appends a compressed summary to memory and triggers index rebuild.
    """
    if not context_summary or len(context_summary.strip()) < 50:
        return

    # Save as a memory entry
    append_memory(f"[auto-flush] {context_summary[:300]}")

    # Trigger async index rebuild (non-blocking)
    try:
        rebuild_memory_index()
    except Exception:
        pass  # Best-effort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_or_default(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return default
