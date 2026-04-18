"""Skills management: loading, auditing, saving, and syncing skills."""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    SKILLS_DIR,
    SKILLS_INDEX,
    SKILLS_FILE,
    MIRA_ROOT,
    LOGS_DIR,
    SOUL_DIR,
    MAX_EXTERNAL_SKILLS_PER_DAY,
    MAX_SKILLS_PER_AGENT,
    SKILL_AUDIT_PATTERN_REVIEWED_DATE,
    SKILL_AUDIT_STALENESS_DAYS,
    SKILL_AUDIT_TTL_DAYS,
    SKILL_AUDIT_STRICT_MODE,
    SOCIAL_ENGINEERING_PATTERNS,
    SKILL_KNOWLEDGE_BLOCKLIST,
    today_local,
)
from memory.soul_io import (
    _atomic_write,
    _locked_write,
    _locked_read_modify_write,
    _log_change,
)

log = logging.getLogger("mira")


class SkillAuditFailedError(Exception):
    pass


_SKILL_AUDIT_HASHES = SKILLS_DIR.parent / "audit_hashes.json"
_SKILL_PROVENANCE_FILE = SKILLS_DIR.parent / "skill_provenance.json"
_AUDIT_WARNINGS_PATH = SKILLS_DIR.parent / "audit_warnings.jsonl"
_SKILL_AUDIT_FAILURES_PATH = SOUL_DIR / "skill_audit_failures.jsonl"


# ---------------------------------------------------------------------------
# Skill audit hash helpers
# ---------------------------------------------------------------------------


def _load_skill_audit_hashes() -> dict:
    if not _SKILL_AUDIT_HASHES.exists():
        return {}
    try:
        return json.loads(_SKILL_AUDIT_HASHES.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_skill_audit_hash(slug: str, content_hash: str):
    def _modify(text):
        try:
            hashes = json.loads(text) if text else {}
        except (json.JSONDecodeError, ValueError):
            hashes = {}
        hashes[slug] = content_hash
        return json.dumps(hashes, indent=2, ensure_ascii=False)

    _locked_read_modify_write(_SKILL_AUDIT_HASHES, _modify)


# ---------------------------------------------------------------------------
# Skill provenance helpers
# ---------------------------------------------------------------------------


def _update_provenance_loaded(skill_name: str):
    def _modify(text):
        try:
            records = json.loads(text) if text else []
        except (json.JSONDecodeError, ValueError):
            records = []
        now = datetime.now().isoformat()
        for rec in records:
            if rec.get("skill_name") == skill_name:
                rec["times_loaded"] = rec.get("times_loaded", 0) + 1
                rec["last_loaded"] = now
                break
        return json.dumps(records, indent=2, ensure_ascii=False)

    _locked_read_modify_write(_SKILL_PROVENANCE_FILE, _modify)


# ---------------------------------------------------------------------------
# Audit warning log (gray inventory — WARN-level, non-blocking)
# ---------------------------------------------------------------------------


def _append_audit_warning(skill_name: str, category: str, pattern: str, matched_line: str):
    """Append a WARN-level audit finding to the persistent gray-inventory log."""
    entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "skill_name": skill_name,
        "category": category,
        "pattern": pattern,
        "matched_line": matched_line,
    }
    try:
        _AUDIT_WARNINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_WARNINGS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("Failed to write audit warning: %s", e)


def load_audit_warnings_digest(days: int = 7) -> str:
    """Return a formatted digest of recent audit warnings for human review.

    Used by the evaluator agent to surface the gray inventory periodically.
    Returns empty string if no warnings exist within the window.
    """
    if not _AUDIT_WARNINGS_PATH.exists():
        return ""
    from collections import Counter
    from datetime import timedelta

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
    entries = []
    try:
        for line in _AUDIT_WARNINGS_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                if entry.get("timestamp", "") >= cutoff:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
    except OSError:
        return ""
    if not entries:
        return ""
    by_category: Counter = Counter(e["category"] for e in entries)
    by_skill: Counter = Counter(e["skill_name"] for e in entries)
    digest_lines = [f"Skill audit warnings (last {days}d): {len(entries)} total"]
    for cat, count in by_category.most_common():
        digest_lines.append(f"  {cat}: {count}")
    flagged = [f"{skill} ({count})" for skill, count in by_skill.most_common(5)]
    digest_lines.append(f"Top flagged skills: {', '.join(flagged)}")
    return "\n".join(digest_lines)


def resolve_skill_audit_failure(skill_name: str, resolution: str):
    """Stamp resolved_at + resolution onto all unresolved entries for skill_name."""
    if not _SKILL_AUDIT_FAILURES_PATH.exists():
        return
    resolved_at = datetime.utcnow().isoformat() + "Z"
    lines_out = []
    for line in _SKILL_AUDIT_FAILURES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            if entry.get("skill_name") == skill_name and "resolved_at" not in entry:
                entry["resolved_at"] = resolved_at
                entry["resolution"] = resolution
        except json.JSONDecodeError:
            pass
        lines_out.append(json.dumps(entry))
    try:
        _SKILL_AUDIT_FAILURES_PATH.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("Failed to update skill audit failure record: %s", e)


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


def _extract_trigger(text: str) -> str:
    """Extract the activation_trigger: field from a skill file's YAML frontmatter."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.startswith("activation_trigger:"):
                    return line[len("activation_trigger:") :].strip()
                if line.startswith("trigger:"):
                    return line[len("trigger:") :].strip()
    return ""


def load_skills_for_task(task_content: str, agent_type: str = "", max_skills: int = MAX_SKILLS_PER_AGENT) -> str:
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
    if len(scored) > max_skills:
        log.warning(
            "skill load truncated: %d skills available, loaded %d (agent=%s)",
            len(scored),
            max_skills,
            agent_type or "unknown",
        )
    top = scored[:max_skills]

    all_tags = set()
    for skill in index:
        all_tags.update(t.lower() for t in skill.get("tags", []))
    selected_tags = set()
    for _, skill in top:
        selected_tags.update(t.lower() for t in skill.get("tags", []))
    skipped_categories = sorted(all_tags - selected_tags)
    log.info(
        "SKILL_AUDIT task_type=%s loaded=%d available=%d skipped_categories=%s",
        agent_type or "unknown",
        len(top),
        len(index),
        skipped_categories,
    )

    if not top:
        return ""

    stored_hashes = _load_skill_audit_hashes()
    _ttl_cutoff = datetime.utcnow() - timedelta(days=SKILL_AUDIT_TTL_DAYS)
    sections = []
    for _, skill in top:
        audited_at_str = skill.get("audited_at")
        _audit_stale = False
        if not audited_at_str:
            _audit_stale = True
        else:
            try:
                if datetime.fromisoformat(audited_at_str.rstrip("Z")) < _ttl_cutoff:
                    _audit_stale = True
            except ValueError:
                _audit_stale = True
        if _audit_stale:
            log.warning(
                "Skill '%s' audit is stale (audited_at=%s, TTL=%dd) — re-audit required",
                skill.get("name"),
                audited_at_str,
                SKILL_AUDIT_TTL_DAYS,
            )
            if SKILL_AUDIT_STRICT_MODE:
                continue
        slug = skill.get("name", "").lower().replace(" ", "-")
        path = SKILLS_DIR / f"{slug}.md"
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if _audit_stale:
                    text = f"[PENDING RE-AUDIT]\n\n{text}"
                stored_hash = stored_hashes.get(slug)
                if stored_hash and current_hash != stored_hash:
                    log.warning("Skill '%s' content changed since last audit — re-auditing", skill.get("name"))
                    try:
                        _audit = audit_skill(skill.get("name", ""), text)
                    except SkillAuditFailedError:
                        log.warning("Skill '%s' BLOCKED after re-audit on hash mismatch", skill.get("name"))
                        continue
                    if _audit.get("requires_review"):
                        log.warning("Skill '%s' BLOCKED after re-audit on hash mismatch", skill.get("name"))
                        continue
                    if _audit.get("result") == "PASS_WITH_CONCERNS":
                        log.warning(
                            "Skill '%s' loaded with concerns: %s",
                            skill.get("name"),
                            _audit.get("proxy_chain", _audit.get("findings", [])),
                        )
                        skill["scrutiny"] = True
                    _save_skill_audit_hash(slug, current_hash)
                    stored_hashes[slug] = current_hash
                # Truncate very long skills to save tokens
                if len(text) > 2000:
                    text = text[:2000] + "\n... (truncated)"
                trigger = _extract_trigger(text)
                if trigger:
                    sections.append(f"TRIGGER: {trigger} → {skill.get('name', '')}\n\n{text}")
                else:
                    sections.append(text)
                _update_provenance_loaded(skill.get("name", ""))
            except OSError:
                pass
    return "\n\n---\n\n".join(sections)


def load_skill(name: str) -> str:
    """Load a specific skill file's full content."""
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    if path.exists():
        _audit_stale = False
        try:
            _index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8")) if SKILLS_INDEX.exists() else []
            _entry = next((s for s in _index if s.get("name") == name), None)
            if _entry:
                audited_at_str = _entry.get("audited_at")
                if not audited_at_str:
                    _audit_stale = True
                else:
                    try:
                        _cutoff = datetime.utcnow() - timedelta(days=SKILL_AUDIT_TTL_DAYS)
                        if datetime.fromisoformat(audited_at_str.rstrip("Z")) < _cutoff:
                            _audit_stale = True
                    except ValueError:
                        _audit_stale = True
        except (json.JSONDecodeError, OSError):
            pass
        if _audit_stale:
            log.warning("Skill '%s' audit is stale (TTL=%dd) — re-audit required", name, SKILL_AUDIT_TTL_DAYS)
            if SKILL_AUDIT_STRICT_MODE:
                return ""
        text = path.read_text(encoding="utf-8")
        current_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        stored_hash = _load_skill_audit_hashes().get(slug)
        if stored_hash and current_hash != stored_hash:
            log.warning("Skill '%s' content changed since last audit — re-auditing", name)
            try:
                _audit = audit_skill(name, text)
            except SkillAuditFailedError:
                log.warning("Skill '%s' BLOCKED after re-audit on hash mismatch", name)
                return ""
            if _audit.get("requires_review"):
                log.warning("Skill '%s' BLOCKED after re-audit on hash mismatch", name)
                return ""
            if _audit.get("result") == "PASS_WITH_CONCERNS":
                log.warning(
                    "Skill '%s' loaded with concerns: %s",
                    name,
                    _audit.get("proxy_chain", _audit.get("findings", [])),
                )
            _save_skill_audit_hash(slug, current_hash)
        _update_provenance_loaded(name)
        return text
    return ""


# ---------------------------------------------------------------------------
# Skill Security Audit
# ---------------------------------------------------------------------------

# Patterns that indicate potentially malicious skill content
_SUSPICIOUS_URL_PATTERNS = [
    r"https?://\S+\.exe",  # Direct executable downloads
    r"https?://\S+\.sh",  # Remote shell scripts
    r"https?://\S+\.bat",  # Batch files
    r"https?://\S+\.msi",  # Windows installers
    r"https?://\S+\.dmg",  # macOS disk images
    r"https?://\S+\.pkg",  # macOS packages
    r"curl\s+.*\|\s*(ba)?sh",  # Pipe curl to shell
    r"wget\s+.*\|\s*(ba)?sh",  # Pipe wget to shell
    r"curl\s+.*-o\s+\S+.*&&.*chmod\s+\+x",  # Download + make executable
    r"requests\.get\s*\(",  # Python HTTP requests
    r"urllib\.request",  # Python urllib
    r"subprocess\..*shell\s*=\s*True",  # Shell injection via subprocess
]

_DANGEROUS_FS_PATTERNS = [
    r"os\.system\s*\(",  # Raw system calls
    r"exec\s*\(",  # Dynamic code execution
    r"eval\s*\(",  # Dynamic expression evaluation
    r"__import__\s*\(",  # Dynamic imports
    r"importlib\.import_module\s*\(",  # Dynamic imports via importlib
    r"subprocess\.run\s*\(",  # Subprocess execution
    r"subprocess\.Popen\s*\(",  # Subprocess execution
    r"subprocess\.call\s*\(",  # Subprocess execution
    r"subprocess\.check_output\s*\(",  # Subprocess execution with output capture
    r"subprocess\.check_call\s*\(",  # Subprocess execution with error checking
    r"pickle\.loads?\s*\(",  # Pickle deserialization (arbitrary code execution via untrusted data)
    r"chmod\s+\+[xs]",  # Making files executable/setuid
    r"rm\s+-rf\s+/",  # Destructive delete from root
    r"shutil\.rmtree\s*\(",  # Programmatic recursive delete
    r"os\.remove|os\.unlink",  # File deletion
    r'open\s*\(.*["\']w["\']',  # File writes (suspicious in skill context)
    r"\.write\s*\(",  # Write operations
]

_OBFUSCATION_PATTERNS = [
    r"base64\.(b64)?decode",  # Base64 decoding (hiding payloads)
    r"\\x[0-9a-fA-F]{2}",  # Hex-encoded strings
    r"\\u[0-9a-fA-F]{4}",  # Unicode escape sequences
    r"codecs\.(decode|encode)",  # Codec-based obfuscation
    r"chr\s*\(\s*\d+\s*\)",  # Character-from-int construction
    r"bytes\.fromhex",  # Hex-to-bytes
    r"compile\s*\(",  # Dynamic code compilation
    r"marshal\.(loads|dumps)",  # Serialized code objects (byte-based)
    r"marshal\.load\s*\(",  # Serialized code objects (file-based)
]

_PRIVILEGE_ESCALATION_PATTERNS = [
    r"sudo\s+",  # Privilege escalation
    r"chmod\s+[0-7]*[4-7][0-7]{2}",  # Setuid/setgid permissions
    r"keychain|keyring",  # Credential store access
    r"\.ssh/",  # SSH key access
    r"\.env\b",  # Environment file access
    r"password|passwd|secret|token|api_key",  # Sensitive credential patterns
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY",  # Specific API keys
    r"/etc/shadow|/etc/passwd",  # System credential files
    r"launchctl\s+load",  # macOS persistence
    r"crontab",  # Scheduled task persistence
]

_PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",  # "IGNORE PREVIOUS INSTRUCTIONS"
    r"ignore\s+(all\s+)?prior\s+instructions",  # variant
    r"disregard\s+(all\s+)?previous",  # variant
    r"<system>",  # Fake XML system block
    r"<CLAUDE",  # Fake Claude instruction block
    r"<instructions>",  # Fake instructions block
    r"</?(system|SYSTEM)\s*>",  # System tag open/close
    r"you\s+are\s+now\s+",  # "You are now" role hijack
    r"new\s+instructions\s*:",  # "New instructions:"
    r"SYSTEM\s+PROMPT\s*:",  # "SYSTEM PROMPT:"
    r"ASSISTANT\s+PROMPT\s*:",  # "ASSISTANT PROMPT:"
    r"[A-Za-z0-9+/]{100,}={0,2}",  # Large base64-encoded blocks (100+ chars)
    r"[\u200b\u200c\u200d\ufeff]{3,}",  # 3+ consecutive zero-width characters
    r"SYSTEM:",  # Bare "SYSTEM:" authority claim
    r"\[INST\]",  # LLaMA/Mistral instruction delimiter misuse
    r"<\|system\|>",  # ChatML system block token
    r"pretend\s+(you\s+are|to\s+be)",  # Role-playing override
    r"override\s+(your\s+|all\s+|previous\s+)?",  # Instruction override
]

_PROMPT_INJECTION_BLOCK_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(all\s+)?previous",
    r"you\s+are\s+now\s+",
    r"new\s+instructions\s*:",
    r"SYSTEM\s+PROMPT\s*:",
    r"ASSISTANT\s+PROMPT\s*:",
    r"</?(system|SYSTEM)\s*>",
    r"<CLAUDE",
]

_PROMPT_INJECTION_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]{2,}")

PROMPT_INJECTION_SIGNATURES = [
    r"ignore\s+(previous|all|your)\s+instructions",
    r"disregard\s+your\s+(rules|guidelines|constraints)",
    r"you\s+are\s+now\b",
    r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions",
    r"your\s+new\s+instructions\s+are",
    r"forget\s+everything",
    r"override\s+your",
    r"system\s+prompt\s*:",
]

SEMANTIC_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # 1. Role / identity reassignment phrases
    (r"you\s+are\s+now\b", "role_reassignment"),
    (r"ignore\s+previous\s+instructions", "role_reassignment"),
    (r"disregard\s+your\b", "role_reassignment"),
    (r"your\s+new\s+role\s+is\b", "role_reassignment"),
    # 2. Constraint override directives
    (r"ignore\s+all\s+rules", "constraint_override"),
    (r"skip\s+security\b", "constraint_override"),
    (r"\bbypass\b[^.\n]{0,60}\b(?:rule|filter|guard|security|restriction|check)\b", "constraint_override"),
    # 3. Exfiltration instruction patterns (prose context)
    (r"send\s+(?:it\s+)?to\s+https?://", "exfiltration_instruction"),
    (r"post\s+(?:it\s+)?to\s+https?://", "exfiltration_instruction"),
    (r"\bcurl\s+https?://\S+", "exfiltration_instruction"),
    # 4. Trigger-word injection markers
    (r"[\u200b\u200c\u200d\u200e\u200f\ufeff\u2060-\u2069]", "zero_width_or_invisible"),
    (r"[\u0430\u0435\u043e\u0440\u0441\u0443\u0445]", "cyrillic_homoglyph"),
]


def check_prompt_injection(text: str) -> tuple[bool, str]:
    """Conservative content-level prompt-injection screening for inbound text.

    This intentionally looks only for explicit instruction-override patterns or
    obfuscated zero-width payload markers. Keep the bar high to avoid false
    positives on normal user conversation.
    """
    if not text:
        return False, ""

    for pattern in _PROMPT_INJECTION_BLOCK_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            snippet = match.group(0).strip()[:80]
            return True, f"prompt injection pattern matched: {snippet}"

    if _PROMPT_INJECTION_ZERO_WIDTH.search(text):
        return True, "prompt injection pattern matched: repeated zero-width characters"

    return False, ""


_COMMERCIAL_BIAS_PATTERNS = [
    r"https?://(?!(?:github\.com|arxiv\.org|wikipedia\.org|docs\.python\.org|docs\.anthropic\.com|openai\.com|huggingface\.co))[a-z0-9\-]+\.[a-z]{2,}(?:/[^\s]*)?",  # Hard-coded URLs to non-standard domains
    r"\balways\s+use\s+[A-Z][A-Za-z0-9_\-]+",  # "always use <Product>"
    r"\bprefer\s+[A-Z][A-Za-z0-9_\-]+\s+over\b",  # "prefer X over"
    r"\b[A-Z][A-Za-z0-9_\-]+\s+is\s+the\s+best\b",  # "X is the best"
    r"\brecommend\s+[A-Z][A-Za-z0-9_\-]+\b(?:.*\brecommend\s+[A-Z][A-Za-z0-9_\-]+\b){2,}",  # 3+ recommendations of named products
]

# Audit coverage metadata — explicitly tracks what we check and what we don't
_AUDIT_CHECKS_PERFORMED = [
    "network_requests",
    "dangerous_ops",
    "obfuscation",
    "privilege_escalation",
    "prompt_injection",
    "commercial_bias",
    "declaration_behavior_consistency",
    "import_allowlist",
    "implicit_trust_chain",
]
_AUDIT_CHECKS_NOT_COVERED = [
    "runtime_behavior",
    "data_exfiltration_via_output",
    "multi-step_attack_chains",
]

# Allowlisted modules for skill code — stdlib + Mira-internal only.
# Any import not in this set triggers an UnknownImport warning.
_SAFE_MODULES: frozenset[str] = frozenset(
    {
        # stdlib
        "abc",
        "ast",
        "asyncio",
        "base64",
        "binascii",
        "bisect",
        "builtins",
        "calendar",
        "cmath",
        "collections",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "email",
        "enum",
        "errno",
        "fnmatch",
        "fractions",
        "functools",
        "gc",
        "glob",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "importlib",
        "inspect",
        "io",
        "itertools",
        "json",
        "keyword",
        "linecache",
        "logging",
        "math",
        "mimetypes",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "pickle",
        "platform",
        "pprint",
        "queue",
        "random",
        "re",
        "secrets",
        "shlex",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "statistics",
        "string",
        "struct",
        "subprocess",
        "sys",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "traceback",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "weakref",
        "xml",
        "zipfile",
        "zlib",
        # Mira-internal modules
        "agents",
        "lib",
        "config",
        "mira",
        "soul_manager",
        "sub_agent",
        "prompts",
        "notes_bridge",
        "soul",
        "autoresearch",
    }
)

# Approved import roots for the implicit-trust-chain check.
# Imports from agents.* are only permitted when rooted at agents.shared.
# Any other agents.* import is flagged — laundering dangerous behavior through
# a non-shared agent module is equivalent to writing it directly.
APPROVED_IMPORT_ROOTS: frozenset[str] = frozenset({"agents.shared"})

# Shared module functions with known side effects (file writes, subprocess calls,
# network requests). Skill code calling these without a # AUDIT-APPROVED annotation
# on the same line is flagged as implicit_trust_chain.
_SIDE_EFFECT_FUNCTIONS: dict[str, str] = {
    "mira.publish": "publishes content externally — network side effect",
    "mira.post": "posts content externally — network side effect",
    "mira.send": "sends data externally — network side effect",
    "sub_agent.run_agent": "spawns a subprocess agent — execution side effect",
    "sub_agent.run": "spawns a subprocess agent — execution side effect",
    "config.write": "writes to config files — filesystem side effect",
    "notes_bridge.write": "writes to Notes bridge — filesystem side effect",
}

# Indicators of undeclared network capability
_DECL_NETWORK_INDICATORS = [
    r"\brequests\b",
    r"\burllib\b",
    r"\bhttpx\b",
    r"\bsocket\b",
    r"\bcurl\b",
    r"\bfetch\b",
    r"\baiohttp\b",
    r"import\s+http\b",
]
_DECL_NETWORK_TAGS = {"web", "fetch", "search", "api", "http", "network", "scrape", "browser"}

# Indicators of undeclared filesystem-write capability
_DECL_WRITE_INDICATORS = [
    r'open\s*\(.*["\']w["\']',
    r"\bos\.remove\b",
    r"\bshutil\.rmtree\b",
]
_DECL_WRITE_TAGS = {"file", "write", "storage"}


def _check_declaration_behavior_consistency(skill_code: str, skill_metadata: dict) -> list[dict]:
    """Compare a skill's declared tags against its actual code content.

    Returns a list of finding dicts. Each has a 'severity' key of either
    'warn' (undeclared single capability) or 'block' (both network and write
    capabilities undeclared simultaneously).
    """
    if not skill_metadata:
        return []

    declared_tags = {t.lower() for t in skill_metadata.get("tags", [])}
    if not declared_tags:
        return []

    network_indicators_found = [p for p in _DECL_NETWORK_INDICATORS if re.search(p, skill_code, re.IGNORECASE)]
    write_indicators_found = [p for p in _DECL_WRITE_INDICATORS if re.search(p, skill_code, re.IGNORECASE)]

    has_undeclared_network = bool(network_indicators_found) and not (declared_tags & _DECL_NETWORK_TAGS)
    has_undeclared_write = bool(write_indicators_found) and not (declared_tags & _DECL_WRITE_TAGS)

    results = []
    if has_undeclared_network and has_undeclared_write:
        results.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "undeclared_network+write_capability",
                "category": "declaration_behavior_mismatch",
                "mechanism": (
                    f"skill has undeclared network capability (indicators: "
                    f"{', '.join(repr(p) for p in network_indicators_found)}) AND undeclared "
                    f"write/delete capability (indicators: "
                    f"{', '.join(repr(p) for p in write_indicators_found)}) "
                    f"but declared tags contain neither network nor storage tags "
                    f"(declared: {sorted(declared_tags) or 'none'}) — BLOCK"
                ),
                "severity": "block",
            }
        )
    elif has_undeclared_network:
        results.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "undeclared_network_capability",
                "category": "declaration_behavior_mismatch",
                "mechanism": (
                    f"skill contains network indicators "
                    f"({', '.join(repr(p) for p in network_indicators_found)}) "
                    f"but declares no network-related tags "
                    f"(declared: {sorted(declared_tags) or 'none'}) — WARN"
                ),
                "severity": "warn",
            }
        )
    elif has_undeclared_write:
        results.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "undeclared_write_capability",
                "category": "declaration_behavior_mismatch",
                "mechanism": (
                    f"skill contains write/delete indicators "
                    f"({', '.join(repr(p) for p in write_indicators_found)}) "
                    f"but declares no storage-related tags "
                    f"(declared: {sorted(declared_tags) or 'none'}) — WARN"
                ),
                "severity": "warn",
            }
        )

    return results


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


_SKILL_INGESTION_LOG = SOUL_DIR / "skill_ingestion_log.json"
_SKILL_PROVENANCE_LEDGER = SOUL_DIR / "skill_provenance_ledger.jsonl"

_EXTERNAL_SOURCES = {"web_fetch", "community_import", "web"}

_CHANNEL_SOURCE_MAP = {"internal": "agent_generated", "web": "web_fetch", "user": "user_input"}


def _check_behavioral_patterns(name: str, content: str) -> list[dict]:
    """Second-pass audit: scan for behavioral signatures of attack automation.

    Returns a list of WARN-level finding dicts (not BLOCK).  These are added
    to the warnings accumulator inside audit_skill() so they surface in the
    audit log and in requires_review logic before the skill is enabled.
    """
    results = []
    lines = content.splitlines()
    seen: set[tuple] = set()

    def _warn(line_no: int, line_content: str, pattern: str, category: str, mechanism: str) -> None:
        key = (pattern, line_no)
        if key not in seen:
            seen.add(key)
            results.append(
                {
                    "line_no": line_no,
                    "line_content": line_content,
                    "pattern": pattern,
                    "category": category,
                    "mechanism": mechanism,
                }
            )
            log.warning(
                "Skill '%s' WARN [%s] line %d: pattern=%r — %s | %s",
                name,
                category,
                line_no,
                pattern,
                mechanism,
                line_content.strip()[:120],
            )

    # (1) Nested loops iterating over IP-like ranges or credential lists
    _TARGET_ITER_RE = re.compile(
        r"for\s+\S+\s+in\s+.*\b(?:range|list|hosts|ips|users|passwords)\b",
        re.IGNORECASE,
    )
    _INNER_LOOP_RE = re.compile(r"^\s{4,}for\s+|^\s{4,}while\s+", re.IGNORECASE)
    for line_no, line in enumerate(lines, start=1):
        if _TARGET_ITER_RE.search(line) and _INNER_LOOP_RE.match(line):
            _warn(
                line_no,
                line,
                "nested_loop_target_iteration",
                "behavioral: bulk_enumeration",
                "nested loop iterating over IP-like ranges or credential lists — systematic enumeration scaffolding",
            )

    # (2) Connection/request patterns inside for/while loops
    _CONN_PATTERNS = [
        (r"socket\.connect(?:_ex)?\s*\(", "socket.connect"),
        (r"requests\.get\s*\(", "requests.get"),
        (r"paramiko.*\.connect\s*\(", "paramiko.connect"),
    ]
    in_loop = False
    loop_indent: int | None = None
    for line_no, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if re.match(r"(for|while)\s+", stripped):
            in_loop = True
            loop_indent = indent
        elif (
            in_loop and loop_indent is not None and indent <= loop_indent and stripped and not stripped.startswith("#")
        ):
            if not re.match(r"(for|while|else|elif|except|finally)\s*", stripped):
                in_loop = False
                loop_indent = None
        if in_loop:
            for pat, label in _CONN_PATTERNS:
                if re.search(pat, line, re.IGNORECASE):
                    _warn(
                        line_no,
                        line,
                        f"bulk_connect_in_loop:{label}",
                        "behavioral: bulk_enumeration",
                        f"{label} inside a for/while loop — bulk connection or request enumeration pattern",
                    )

    # (3) Systematic credential harvesting: glob + read in loop targeting sensitive paths
    _SENSITIVE_PATH_RE = re.compile(
        r"['\"](?:~/)?\.ssh/|['\"](?:~/)?\.aws/|/etc/passwd|/etc/shadow",
        re.IGNORECASE,
    )
    _GLOB_RE = re.compile(r"\bglob\s*\(", re.IGNORECASE)
    _READ_IN_LOOP_RE = re.compile(r"\b(?:open|read_text|read_bytes)\s*\(", re.IGNORECASE)
    has_glob = any(_GLOB_RE.search(l) for l in lines)
    has_sensitive_path = any(_SENSITIVE_PATH_RE.search(l) for l in lines)
    if has_glob and has_sensitive_path:
        for line_no, line in enumerate(lines, start=1):
            if _READ_IN_LOOP_RE.search(line) and _SENSITIVE_PATH_RE.search(line):
                _warn(
                    line_no,
                    line,
                    "credential_harvest_glob_read",
                    "behavioral: credential_harvesting",
                    "glob + file-read targeting ~/.ssh, ~/.aws, or /etc/passwd — systematic credential harvesting pattern",
                )

    # (4) Bulk outbound exfiltration: requests.post or socket.send inside a loop with file-read
    _EXFIL_SEND_RE = re.compile(r"requests\.post\s*\(|socket.*\.send(?:all)?\s*\(", re.IGNORECASE)
    _FILE_READ_RE = re.compile(r"\b(?:open|read_text|read_bytes|\.read\s*\()\b", re.IGNORECASE)
    has_file_read = any(_FILE_READ_RE.search(l) for l in lines)
    if has_file_read:
        in_loop2 = False
        loop_indent2: int | None = None
        for line_no, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if re.match(r"(for|while)\s+", stripped):
                in_loop2 = True
                loop_indent2 = indent
            elif (
                in_loop2
                and loop_indent2 is not None
                and indent <= loop_indent2
                and stripped
                and not stripped.startswith("#")
            ):
                if not re.match(r"(for|while|else|elif|except|finally)\s*", stripped):
                    in_loop2 = False
                    loop_indent2 = None
            if in_loop2 and _EXFIL_SEND_RE.search(line):
                _warn(
                    line_no,
                    line,
                    "bulk_exfil_send_in_loop",
                    "behavioral: bulk_exfiltration",
                    "requests.post or socket.send inside a loop combined with file-reads — bulk outbound exfiltration pattern",
                )

    return results


_INJECTION_PHRASES = [
    "ignore previous",
    "ignore above",
    "you are now",
    "new persona",
    "disregard your instructions",
    "system:",
    "assistant:",
    "### instruction",
    "forget everything",
    "act as if",
]


def content_looks_like_injection(text: str) -> tuple[bool, str]:
    """Return (True, matched_phrase) if skill text contains a prompt injection signal.

    Scans name + description + body concatenated for natural-language injection
    patterns that pass code-level checks. Case-insensitive substring match.
    """
    lower = text.lower()
    for phrase in _INJECTION_PHRASES:
        if phrase in lower:
            return True, phrase
    return False, ""


_SEMANTIC_MANIPULATION_PHRASES = [
    "ignore previous instructions",
    "you are now",
    "disregard",
    "override",
    "forget your",
    "new persona",
    "system prompt",
    "<|im_start|>",
    "[inst]",
]


def _content_looks_like_prompt_injection(text: str) -> tuple[bool, str]:
    """Return (True, matched_phrase) if skill text contains semantic manipulation patterns.

    Targets phrases that attempt to override instructions or mutate LLM behavior
    at the prompt level. Skills can legitimately discuss these topics, so this
    is a soft signal requiring human review rather than a hard block.
    """
    lower = text.lower()
    for phrase in _SEMANTIC_MANIPULATION_PHRASES:
        if phrase.lower() in lower:
            return True, phrase
    return False, ""


def audit_skill(
    name: str,
    content: str,
    metadata: dict | None = None,
    origin: str = "internal",
    source: str = "agent_generated",
) -> dict:
    """Audit a skill for security risks before saving.

    Returns {'passed': bool, 'findings': [{'line_no': int, 'line_content': str,
    'pattern': str, 'category': str, 'mechanism': str}]}.

    metadata (optional): dict with 'tags' (list[str]) and 'description' (str) used
    for declaration-vs-behavior consistency checks.

    origin: 'internal' (Mira-generated) or 'external' (web/feed-sourced).
    External-origin skills are subject to a daily ingestion cap.

    source: distribution channel — 'internal' (Mira-generated), 'web' (external URL),
    'user' (provided directly by user), 'user_input', 'agent_generated', 'web_fetch',
    or 'community_import'. Used to weight trust in the provenance ledger.
    Web-sourced skills are subject to zero-tolerance scrutiny: any audit warning is
    treated as a blocking finding.
    """
    _source_channel = source
    if source in _CHANNEL_SOURCE_MAP:
        source = _CHANNEL_SOURCE_MAP[source]
    if source in _EXTERNAL_SOURCES:
        origin = "external"

    if origin == "external":
        _days_since_review = (datetime.utcnow() - datetime.strptime(SKILL_AUDIT_PATTERN_REVIEWED_DATE, "%Y-%m-%d")).days
        if _days_since_review > SKILL_AUDIT_STALENESS_DAYS:
            log.warning(
                "Skill audit patterns are %dd old — external skill ingestion may outpace defense coverage. "
                "Review soul_manager.py audit patterns.",
                _days_since_review,
            )

    try:
        _SKILL_PROVENANCE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        _ledger_entry = {
            "skill_name": name,
            "source": source,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        with open(_SKILL_PROVENANCE_LEDGER, "a", encoding="utf-8") as _lf:
            _lf.write(json.dumps(_ledger_entry) + "\n")
    except Exception as _le:
        log.warning("Failed to write skill provenance ledger entry: %s", _le)

    if origin == "external":
        today = today_local()
        try:
            ingestion_log = (
                json.loads(_SKILL_INGESTION_LOG.read_text(encoding="utf-8")) if _SKILL_INGESTION_LOG.exists() else {}
            )
        except (json.JSONDecodeError, OSError):
            ingestion_log = {}
        daily_count = ingestion_log.get(today, 0)
        if daily_count >= MAX_EXTERNAL_SKILLS_PER_DAY:
            log.warning(
                "Daily external skill cap reached (%d/%d) — deferring ingestion of '%s'",
                daily_count,
                MAX_EXTERNAL_SKILLS_PER_DAY,
                name,
            )
            raise SkillAuditFailedError(
                f"Skill blocked: Daily external skill cap reached ({daily_count}/{MAX_EXTERNAL_SKILLS_PER_DAY}) — deferring ingestion."
            )

    _MECHANISMS = {
        r"https?://\S+\.exe": "downloads a Windows executable from a remote host for local execution",
        r"https?://\S+\.sh": "downloads a remote shell script that can execute arbitrary commands",
        r"https?://\S+\.bat": "downloads a Windows batch script that executes arbitrary commands",
        r"https?://\S+\.msi": "downloads a Windows installer that runs with elevated privileges",
        r"https?://\S+\.dmg": "downloads a macOS disk image that can install arbitrary software",
        r"https?://\S+\.pkg": "downloads a macOS package that installs with system permissions",
        r"curl\s+.*\|\s*(ba)?sh": "downloads and executes arbitrary remote code in the agent process",
        r"wget\s+.*\|\s*(ba)?sh": "downloads and executes arbitrary remote code in the agent process",
        r"curl\s+.*-o\s+\S+.*&&.*chmod\s+\+x": "downloads a file, marks it executable, then runs it — classic dropper pattern",
        r"requests\.get\s*\(": "makes an outbound HTTP request that could exfiltrate data or fetch payloads",
        r"urllib\.request": "makes an outbound HTTP request that could exfiltrate data or fetch payloads",
        r"subprocess\..*shell\s*=\s*True": "passes user-controlled input to the shell, enabling command injection",
        r"subprocess\.run\s*\(": "spawns a child process that can execute arbitrary system commands",
        r"subprocess\.Popen\s*\(": "spawns a child process with full control over stdin/stdout/stderr, enabling arbitrary command execution",
        r"subprocess\.call\s*\(": "spawns a child process that can execute arbitrary system commands",
        r"subprocess\.check_output\s*\(": "spawns a child process and captures its output, enabling arbitrary command execution",
        r"subprocess\.check_call\s*\(": "spawns a child process that raises on failure, enabling arbitrary command execution",
        r"os\.system\s*\(": "executes an arbitrary shell command, bypassing Python sandbox controls",
        r"exec\s*\(": "executes arbitrary dynamically-constructed code in the current process",
        r"eval\s*\(": "evaluates arbitrary expressions, enabling code injection via string manipulation",
        r"__import__\s*\(": "dynamically imports arbitrary modules, bypassing static import analysis",
        r"importlib\.import_module\s*\(": "dynamically imports arbitrary modules at runtime, bypassing static import analysis",
        r"chmod\s+\+[xs]": "marks a file executable or setuid, enabling privilege escalation on next run",
        r"rm\s+-rf\s+/": "recursively deletes from filesystem root, causing irreversible data loss",
        r"shutil\.rmtree\s*\(": "programmatically deletes directory trees, enabling targeted data destruction",
        r"os\.remove|os\.unlink": "deletes files from disk, potentially destroying agent state or config",
        r'open\s*\(.*["\']w["\']': "opens files for writing, enabling overwrite of config, keys, or agent state",
        r"\.write\s*\(": "writes data to a file handle, potentially overwriting sensitive agent files",
        r"base64\.(b64)?decode": "decodes base64-encoded content, commonly used to hide malicious payloads from static analysis",
        r"\\x[0-9a-fA-F]{2}": "hex-escaped byte literals can hide malicious strings from pattern-based scanning",
        r"\\u[0-9a-fA-F]{4}": "unicode escape sequences can hide malicious strings from pattern-based scanning",
        r"codecs\.(decode|encode)": "codec-based transcoding can decode hidden payloads (rot13, zlib, base64) at runtime",
        r"chr\s*\(\s*\d+\s*\)": "constructs strings character-by-character to bypass keyword detection",
        r"bytes\.fromhex": "converts hex strings to bytes, commonly used to smuggle binary payloads",
        r"compile\s*\(": "compiles arbitrary code objects at runtime, bypassing static analysis entirely",
        r"pickle\.loads?\s*\(": "deserializes arbitrary Python objects from untrusted bytes, a well-known arbitrary code execution vector",
        r"marshal\.(loads|dumps)": "deserializes Python code objects, enabling execution of pre-compiled malicious bytecode",
        r"marshal\.load\s*\(": "deserializes Python code objects from a file object, enabling execution of pre-compiled malicious bytecode",
        r"sudo\s+": "runs commands with root privileges, bypassing user-level permission controls",
        r"chmod\s+[0-7]*[4-7][0-7]{2}": "sets setuid/setgid bit, allowing the file to run with owner privileges regardless of executor",
        r"keychain|keyring": "accesses the system credential store, potentially extracting stored passwords and API keys",
        r"\.ssh/": "accesses SSH key material, enabling unauthorized remote authentication",
        r"\.env\b": "reads environment files that typically contain secrets, API keys, and credentials",
        r"password|passwd|secret|token|api_key": "references credential-related identifiers that may indicate secret extraction logic",
        r"OPENAI_API_KEY|ANTHROPIC_API_KEY": "explicitly targets known API key environment variables for potential exfiltration",
        r"/etc/shadow|/etc/passwd": "reads system authentication files containing password hashes",
        r"launchctl\s+load": "installs a macOS LaunchAgent/LaunchDaemon, creating a persistent execution mechanism",
        r"crontab": "modifies the cron schedule, creating a persistent execution mechanism that survives restarts",
        r"ignore\s+(all\s+)?previous\s+instructions": "classic prompt injection that attempts to override the agent's system instructions",
        r"ignore\s+(all\s+)?prior\s+instructions": "classic prompt injection variant that attempts to override the agent's system instructions",
        r"disregard\s+(all\s+)?previous": "prompt injection that instructs the agent to discard prior context and instructions",
        r"<system>": "injects a fake XML system block to spoof trusted system-level instructions",
        r"<CLAUDE": "injects a fake Claude instruction block to impersonate the model's own directives",
        r"<instructions>": "injects a fake instructions block that may override legitimate agent instructions",
        r"</?(system|SYSTEM)\s*>": "injects system XML tags to frame attacker content as trusted system instructions",
        r"you\s+are\s+now\s+": "role-hijack injection that attempts to redefine the agent's identity and permissions",
        r"new\s+instructions\s*:": "signals a new instruction set, attempting to replace the original system prompt",
        r"SYSTEM\s+PROMPT\s*:": "impersonates a system prompt to inject privileged instructions into the agent context",
        r"ASSISTANT\s+PROMPT\s*:": "impersonates an assistant prompt directive to manipulate model behavior",
        r"[A-Za-z0-9+/]{100,}={0,2}": "large base64 block may contain an encoded payload, instruction set, or exfiltration target",
        r"[\u200b\u200c\u200d\ufeff]{3,}": "invisible zero-width characters can hide instructions from human reviewers while remaining active in parsing",
    }

    findings = []
    requires_review = False
    combined = f"{name}\n{content}"
    lines = combined.splitlines()
    files_scanned: list[str] = [f"skill:{name}"]

    checks = [
        (_SUSPICIOUS_URL_PATTERNS, "Suspicious network request"),
        (_DANGEROUS_FS_PATTERNS, "Dangerous filesystem/code operation"),
        (_OBFUSCATION_PATTERNS, "Obfuscated or hidden code"),
        (_PRIVILEGE_ESCALATION_PATTERNS, "Privilege escalation or credential access"),
        (_PROMPT_INJECTION_PATTERNS, "Prompt injection attempt"),
    ]

    seen = set()
    for patterns, category in checks:
        for pattern in patterns:
            for line_no, line in enumerate(lines, start=1):
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    key = (pattern, line_no)
                    if key not in seen:
                        seen.add(key)
                        findings.append(
                            {
                                "line_no": line_no,
                                "line_content": line,
                                "pattern": pattern,
                                "category": category,
                                "mechanism": _MECHANISMS.get(pattern, "potentially dangerous pattern"),
                            }
                        )

    _inj_hit, _inj_phrase = content_looks_like_injection(combined)
    if _inj_hit:
        log.warning(
            "Skill '%s' BLOCKED [content_injection/%s] — phrase %r matched in skill text",
            name,
            _inj_phrase,
            _inj_phrase,
        )
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": _inj_phrase,
                "category": "content_injection",
                "mechanism": f"prompt injection phrase {repr(_inj_phrase)} found in skill name/description/body",
            }
        )

    for _sig in PROMPT_INJECTION_SIGNATURES:
        _m = re.search(_sig, content, re.IGNORECASE)
        if _m:
            _snippet = content[max(0, _m.start() - 20) : _m.end() + 40].strip()
            key = (_sig, -2)
            if key not in seen:
                seen.add(key)
                findings.append(
                    {
                        "line_no": content[: _m.start()].count("\n") + 1,
                        "line_content": _snippet,
                        "pattern": _sig,
                        "category": "prompt_injection",
                        "mechanism": f"prompt injection signature in skill content: {repr(_m.group(0))}",
                    }
                )

    _CATEGORY_TO_CHECK_NAME = {
        "Suspicious network request": "network_requests",
        "Dangerous filesystem/code operation": "filesystem_code_ops",
        "Obfuscated or hidden code": "obfuscated_code",
        "Privilege escalation or credential access": "privilege_escalation",
        "Prompt injection attempt": "prompt_injection",
    }
    audit_trail = []
    for _patterns, _category in checks:
        _check_name = _CATEGORY_TO_CHECK_NAME.get(_category, _category.lower().replace(" ", "_").replace("/", "_"))
        _check_findings = [f["pattern"] for f in findings if f.get("category") == _category]
        audit_trail.append(
            {
                "check": _check_name,
                "result": "fail" if _check_findings else "pass",
                "detail": "; ".join(_check_findings) if _check_findings else "none",
            }
        )

    _MEMORY_PATH_PATTERNS = [
        r"soul/",
        r"memory/",
        r"\bjournal\b",
        r"CLAUDE\.md",
    ]
    _OUTBOUND_NETWORK_PATTERNS = [
        r"requests\.(get|post)",
        r"\bhttpx\b",
        r"\burllib\b",
        r"\bcurl\b",
    ]
    has_memory_access = any(re.search(p, combined, re.IGNORECASE) for p in _MEMORY_PATH_PATTERNS)
    has_network_call = any(re.search(p, combined, re.IGNORECASE) for p in _OUTBOUND_NETWORK_PATTERNS)
    if has_memory_access and has_network_call:
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "memory_access + network_call",
                "category": "knowledge_extraction",
                "mechanism": "reads internal memory paths and makes outbound network calls — potential knowledge extraction vector",
            }
        )

    _SOUL_INTERNAL_PATHS = ["soul/", "soul/memory", "soul/journal", "interests.json", "soul/skills"]
    _NETWORK_WRITE_PATTERNS = [r"requests\.post", r"\burllib\b", r"\bcurl\b", r"\bhttpx\b"]
    matched_soul_path = next((p for p in _SOUL_INTERNAL_PATHS if p in combined), None)
    if matched_soul_path:
        log.warning("Skill '%s' SOUL_ACCESS_PATTERN — references internal path fragment '%s'", name, matched_soul_path)
        has_network_write = any(re.search(p, combined, re.IGNORECASE) for p in _NETWORK_WRITE_PATTERNS)
        if has_network_write:
            findings.append(
                {
                    "line_no": -1,
                    "line_content": "",
                    "pattern": "soul_access + network_write",
                    "category": "SOUL_ACCESS_PATTERN",
                    "mechanism": f"references internal soul path '{matched_soul_path}' and makes outbound network writes — potential identity/memory exfiltration vector",
                }
            )
        else:
            requires_review = True
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                quarantine_entry = {
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "skill_name": name,
                    "matched_path": matched_soul_path,
                    "reason": "SOUL_ACCESS_PATTERN — references internal soul path; requires explicit justification before enabling",
                }
                quarantine_path = LOGS_DIR / "skill_quarantine.jsonl"
                with open(quarantine_path, "a", encoding="utf-8") as _f:
                    _f.write(json.dumps(quarantine_entry) + "\n")
            except Exception as _e:
                log.warning("Failed to write skill quarantine entry: %s", _e)

    existing_names = [p.stem for p in SKILLS_DIR.glob("*.md")] if SKILLS_DIR.exists() else []
    name_tokens = set(name.lower().split())
    for existing in existing_names:
        dist = _levenshtein(name.lower(), existing.lower())
        if dist == 0:
            findings.append(
                {
                    "line_no": 1,
                    "line_content": name,
                    "pattern": "exact_name_match",
                    "category": "name_squatting",
                    "mechanism": f"skill name is identical to existing trusted skill '{existing}' — exact duplicate is a BLOCK",
                }
            )
        elif dist <= 2:
            findings.append(
                {
                    "line_no": 1,
                    "line_content": name,
                    "pattern": "similar_name",
                    "category": "name_squatting",
                    "mechanism": f"skill name resembles existing trusted skill '{existing}' (edit distance {dist}) — possible name-squatting",
                }
            )
        else:
            existing_tokens = set(existing.lower().split())
            if len(name_tokens) > 1 and name_tokens != existing_tokens:
                diff = name_tokens.symmetric_difference(existing_tokens)
                if len(diff) == 2 and len(name_tokens) == len(existing_tokens):
                    findings.append(
                        {
                            "line_no": 1,
                            "line_content": name,
                            "pattern": "token_squatting",
                            "category": "name_squatting",
                            "mechanism": f"skill name shares all but one token with existing trusted skill '{existing}' — possible name-squatting",
                        }
                    )

    warnings = []
    for pattern in _COMMERCIAL_BIAS_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                key = (pattern, line_no)
                if key not in seen:
                    seen.add(key)
                    warnings.append(
                        {
                            "line_no": line_no,
                            "line_content": line,
                            "pattern": pattern,
                            "category": "commercial_bias",
                            "mechanism": "promotes specific commercial products or services in a way that may bias agent recommendations",
                        }
                    )
    if warnings:
        log.warning(
            "Skill '%s' has %d commercial_bias warning(s) — flagged for reflection review:", name, len(warnings)
        )
        for w in warnings:
            log.warning(
                "  WARN [line %d] pattern=%r — %s | %s",
                w["line_no"],
                w["pattern"],
                w["mechanism"],
                w["line_content"][:120],
            )

    # --- WARN: dynamic URL query parameters (potential covert channel) ---
    _DYNAMIC_URL_PATTERNS = [
        r"f['\"]https?://[^'\"]*[?&][^'\"]*\{",
        r"['\"]https?://[^'\"]*[?&][^'\"]*['\"]\.format\s*\(",
    ]
    for pattern in _DYNAMIC_URL_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            if re.search(pattern, line, re.IGNORECASE):
                key = (pattern, line_no)
                if key not in seen:
                    seen.add(key)
                    warn_entry = {
                        "line_no": line_no,
                        "line_content": line,
                        "pattern": pattern,
                        "category": "dynamic-url-query",
                        "mechanism": "URL with dynamic query parameters constructed from runtime variables — potential covert channel encoding agent state into outbound traffic",
                    }
                    warnings.append(warn_entry)
                    _append_audit_warning(name, "dynamic-url-query", pattern, line.strip()[:120])
                    log.warning("Skill '%s' WARN [dynamic-url-query] line %d: %s", name, line_no, line.strip()[:120])

    # --- WARN: file writes outside MIRA_ROOT (potential exfiltration path) ---
    _BOUNDARY_WRITE_PATTERNS = [
        r"shutil\.(copy2?|move)\s*\(",
        r"os\.(rename|replace)\s*\(",
    ]
    for pattern in _BOUNDARY_WRITE_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            if re.search(pattern, line, re.IGNORECASE):
                if "MIRA_ROOT" not in line and "Sandbox/Mira" not in line:
                    key = (pattern, line_no)
                    if key not in seen:
                        seen.add(key)
                        warn_entry = {
                            "line_no": line_no,
                            "line_content": line,
                            "pattern": pattern,
                            "category": "out-of-boundary-write",
                            "mechanism": "file write operation with no reference to MIRA_ROOT — potential exfiltration channel writing outside ~/Sandbox/Mira/",
                        }
                        warnings.append(warn_entry)
                        _append_audit_warning(name, "out-of-boundary-write", pattern, line.strip()[:120])
                        log.warning(
                            "Skill '%s' WARN [out-of-boundary-write] line %d: %s", name, line_no, line.strip()[:120]
                        )

    _SECURITY_CLAIM_PATTERNS = [
        r"zero.?day",
        r"CVE-\d{4}-\d+",
        r"\bexploit\b",
        r"\bvulnerability\b",
        r"\bRCE\b",
        r"\bSQL injection\b",
        r"\bbackdoor\b",
    ]
    for pattern in _SECURITY_CLAIM_PATTERNS:
        for m in re.finditer(pattern, combined, re.IGNORECASE):
            window = combined[m.start() : m.start() + 200]
            if not re.search(r"\[verified:\s*[^\]]+\]", window, re.IGNORECASE):
                log.warning(
                    "Skill '%s' contains unverified security claim (pattern=%r) "
                    "without [verified: <source>] tag — flagged for manual review",
                    name,
                    pattern,
                )
                break

    if metadata is not None and "source_provenance" not in metadata:
        log.warning(
            "Skill '%s' metadata is missing 'source_provenance' field — channel origin untracked",
            name,
        )

    consistency_results = _check_declaration_behavior_consistency(content, metadata)
    for cr in consistency_results:
        if cr["severity"] == "block":
            findings.append(cr)
            log.warning(
                "Skill '%s' BLOCKED — declaration/behavior mismatch: %s",
                name,
                cr["mechanism"],
            )
        else:
            warnings.append(cr)
            log.warning(
                "Skill '%s' WARN — declaration/behavior mismatch: %s",
                name,
                cr["mechanism"],
            )

    _CRUDE_EXFILTRATION_PATTERNS = {
        r"""['"~]?/\.ssh/""": "direct read of SSH key material — crude credential theft without obfuscation",
        r"""['"~]?/\.aws/""": "direct read of AWS credential directory — crude cloud credential theft",
        r"os\.environ": "dumps environment variables — often used for crude API key exfiltration",
        r"requests\.post|urllib.*urlopen.*http": "outbound HTTP write — potential crude data exfiltration",
        r"open\s*\([^)]*(?:passwd|shadow|keychain)": "naive credential file access — reads system auth files directly",
    }
    crude_suspicion_score = 0
    for pattern, mechanism in _CRUDE_EXFILTRATION_PATTERNS.items():
        for line_no, line in enumerate(lines, start=1):
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                key = (pattern, line_no)
                if key not in seen:
                    seen.add(key)
                    crude_suspicion_score += 1
                    warn_entry = {
                        "line_no": line_no,
                        "line_content": line,
                        "pattern": pattern,
                        "category": "crude_exfiltration",
                        "mechanism": mechanism,
                    }
                    warnings.append(warn_entry)
                    log.warning(
                        "Skill '%s' WARN [crude_exfiltration] line %d: pattern=%r — %s | %s",
                        name,
                        line_no,
                        pattern,
                        mechanism,
                        line.strip()[:120],
                    )
    if crude_suspicion_score >= 2:
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "crude_exfiltration_threshold",
                "category": "crude_exfiltration",
                "mechanism": f"suspicion score {crude_suspicion_score} >= 2 across crude_exfiltration_patterns — low-sophistication exfiltration attempt",
            }
        )

    _import_re = re.compile(
        r"^\s*(?:import\s+(\S+)|from\s+(\S+)\s+import)",
        re.MULTILINE,
    )
    for m in _import_re.finditer(content):
        raw = m.group(1) or m.group(2)
        top_level = raw.split(".")[0]
        if top_level not in _SAFE_MODULES:
            findings.append(
                {
                    "line_no": content[: m.start()].count("\n") + 1,
                    "line_content": m.group(0).strip(),
                    "pattern": "unknown_import",
                    "category": "import_allowlist",
                    "mechanism": f"UnknownImport: module '{top_level}' not in allowlist — add to _SAFE_MODULES if intentional",
                }
            )

    # --- KNOWN_ATTACK_PATTERNS: signatures mapping to documented attack classes ---
    # Co-occurrence check: reverse shell (socket.connect + subprocess/exec + shell binary)
    _rs_socket = re.search(r"socket\.connect(?:_ex)?\s*\(", combined, re.IGNORECASE)
    _rs_proc = re.search(
        r"subprocess\.Popen|subprocess\.run|subprocess\.call|os\.execv|os\.execve", combined, re.IGNORECASE
    )
    _rs_shell = re.search(r"/bin/sh\b|/bin/bash\b", combined, re.IGNORECASE)
    if _rs_socket and _rs_proc and _rs_shell:
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "reverse_shell_combo",
                "category": "KNOWN_ATTACK: reverse_shell_pattern",
                "mechanism": "socket.connect + subprocess/exec + shell binary co-present — classic reverse shell scaffolding",
            }
        )

    # Co-occurrence check: port/host enumeration (socket.connect inside a loop over range or list)
    _pe_socket = re.search(r"socket\.connect(?:_ex)?\s*\(", combined, re.IGNORECASE)
    _pe_loop = re.search(r"for\s+\w+\s+in\s+(?:range\s*\(|hosts\b|targets\b|ips\b|\[)", combined, re.IGNORECASE)
    if _pe_socket and _pe_loop:
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "port_scan_combo",
                "category": "KNOWN_ATTACK: port_scanner",
                "mechanism": "socket.connect + loop over range/hosts co-present — port or host enumeration scaffolding",
            }
        )

    _KNOWN_ATTACK_LINE_PATTERNS = [
        # Credential scraping: ~/.ssh/ and ~/.aws/credentials reads
        (
            r"""['"~]?/?\.ssh/(?:id_rsa|id_ed25519|id_ecdsa|authorized_keys|known_hosts)""",
            "KNOWN_ATTACK: credential_scraper",
            "reads SSH private key or auth material from ~/.ssh/ — credential scraping pattern",
        ),
        (
            r"""['"~]?/?\.aws/credentials""",
            "KNOWN_ATTACK: credential_scraper",
            "reads AWS credentials file from ~/.aws/credentials — credential scraping pattern",
        ),
        (
            r"SecKeychainFind|SecItemCopyMatching|security\s+find-generic-password|security\s+find-internet-password",
            "KNOWN_ATTACK: credential_scraper",
            "calls macOS Keychain API or security CLI to extract stored credentials — credential scraping pattern",
        ),
        (
            r"os\.environ(?:\.get)?\s*\(\s*['\"](?:AWS_SECRET|ANTHROPIC_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN|SECRET_KEY|PRIVATE_KEY|PASSWORD)",
            "KNOWN_ATTACK: credential_scraper",
            "extracts specific API key or credential from environment and may exfiltrate it — credential scraping pattern",
        ),
        # SSRF scaffolding: HTTP request with URL built from env var or user input
        (
            r"(?:requests\.(?:get|post)|urllib\.request\.urlopen)\s*\(\s*(?:f['\"]https?://[^'\"]*\{|[^)]*os\.environ|[^)]*input\s*\()",
            "KNOWN_ATTACK: ssrf_scaffold",
            "HTTP request URL constructed from f-string interpolation, env variable, or user input without allowlisting — SSRF scaffolding pattern",
        ),
        # Persistence installation outside Mira's own agent
        (
            r"launchctl\s+load\s+(?!.*com\.angwei\.mira)",
            "KNOWN_ATTACK: persistence_install",
            "launchctl load with non-Mira LaunchAgent path — installs unauthorized persistence mechanism",
        ),
        (
            r"crontab\s+-l\s*[|;].*crontab\s+-",
            "KNOWN_ATTACK: persistence_install",
            "crontab -l | ... | crontab - pattern — appends arbitrary cron job for persistence without full replacement",
        ),
        (
            r"~/Library/LaunchAgents/(?!com\.angwei\.mira)",
            "KNOWN_ATTACK: persistence_install",
            "writes a LaunchAgent plist outside Mira's own com.angwei.mira namespace — installs unauthorized persistence",
        ),
        # ARP spoofing scaffolding
        (
            r"(?:scapy|ARP\s*\(|Ether\s*\(|sendp\s*\().*(?:hwsrc|psrc|pdst)",
            "KNOWN_ATTACK: arp_spoof",
            "scapy ARP packet construction with source/destination fields — ARP spoofing or MITM scaffolding pattern",
        ),
        (
            r"arp\s+-s\s+\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
            "KNOWN_ATTACK: arp_spoof",
            "static ARP entry injection via arp -s — ARP cache poisoning pattern",
        ),
        # Common exploit scaffolding: buffer overflow / shellcode patterns
        (
            r"\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){7,}",
            "KNOWN_ATTACK: shellcode_payload",
            "long hex byte sequence (8+ bytes) — shellcode or binary exploit payload pattern",
        ),
        (
            r"struct\.pack\s*\(['\"][!<>@=]*[IiLlQq]+['\"].*\+\s*b['\"]\\x",
            "KNOWN_ATTACK: exploit_scaffold",
            "struct.pack with binary payload concatenation — exploit buffer construction pattern (stack/heap overflow scaffolding)",
        ),
    ]
    for _atk_pattern, _atk_category, _atk_mechanism in _KNOWN_ATTACK_LINE_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            m = re.search(_atk_pattern, line, re.IGNORECASE)
            if m:
                key = (_atk_pattern, line_no)
                if key not in seen:
                    seen.add(key)
                    findings.append(
                        {
                            "line_no": line_no,
                            "line_content": line,
                            "pattern": _atk_pattern,
                            "category": _atk_category,
                            "mechanism": _atk_mechanism,
                        }
                    )

    _PRIVILEGED_MODULES = {"soul_manager", "config", "sub_agent"}
    _SHARED_IMPORT_RE = re.compile(
        r"from\s+\.*(shared|agents[/.]shared)\S*\s+import|import\s+\.*(shared|soul_manager|config|sub_agent)",
        re.IGNORECASE,
    )
    for _m in _SHARED_IMPORT_RE.finditer(content):
        _raw = _m.group(0)
        _matched_module = None
        for _priv in _PRIVILEGED_MODULES:
            if re.search(r"\b" + re.escape(_priv) + r"\b", _raw, re.IGNORECASE):
                _matched_module = _priv
                break
        if _matched_module:
            findings.append(
                {
                    "line_no": content[: _m.start()].count("\n") + 1,
                    "line_content": _raw.strip(),
                    "pattern": "privileged_shared_import",
                    "category": "privileged_module_access",
                    "mechanism": f"skill attempts to import privileged shared module: {_matched_module}",
                }
            )
        else:
            _path_match = re.search(
                r"from\s+['\"]?([^\s'\"]+(?:shared|agents[/.]shared)[^\s'\"]*)['\"]?\s+import|"
                r"import\s+['\"]?([^\s'\"]+(?:shared|agents[/.]shared)[^\s'\"]*)['\"]?",
                _raw,
                re.IGNORECASE,
            )
            _module_path = None
            if _path_match:
                _module_path = (_path_match.group(1) or _path_match.group(2) or "").strip()
            if not _module_path:
                _from_match = re.search(r"from\s+(\S+)\s+import", _raw, re.IGNORECASE)
                _imp_match = re.search(r"import\s+(\S+)", _raw, re.IGNORECASE)
                _module_path = (
                    (_from_match.group(1) if _from_match else None)
                    or (_imp_match.group(1) if _imp_match else None)
                    or ""
                )
            if _module_path:
                _rel = _module_path.replace(".", "/").lstrip("/")
                _candidates = [
                    MIRA_ROOT
                    / "lib"
                    / (
                        _rel.split("/", 1)[-1]
                        if _rel.startswith("shared") or _rel.startswith("agents/shared")
                        else _rel
                    ),
                    MIRA_ROOT / (_rel + ".py"),
                    MIRA_ROOT / _rel / "__init__.py",
                ]
                for _cand in _candidates:
                    if _cand.exists():
                        try:
                            _shared_content = _cand.read_text(encoding="utf-8")
                            files_scanned.append(str(_cand))
                            _shared_lines = _shared_content.splitlines()
                            for _patterns, _category in checks:
                                for _pattern in _patterns:
                                    for _ln, _line in enumerate(_shared_lines, start=1):
                                        if re.search(_pattern, _line, re.IGNORECASE):
                                            _key = (_pattern, f"shared:{_cand.name}:{_ln}")
                                            if _key not in seen:
                                                seen.add(_key)
                                                findings.append(
                                                    {
                                                        "line_no": -1,
                                                        "line_content": _line,
                                                        "pattern": _pattern,
                                                        "category": f"shared_import:{_category}",
                                                        "mechanism": f"imported shared file '{_cand.name}' contains dangerous pattern — {_MECHANISMS.get(_pattern, 'potentially dangerous pattern')}",
                                                    }
                                                )
                        except OSError:
                            pass
                        break

    behavioral_warnings = _check_behavioral_patterns(name, content)
    if behavioral_warnings:
        warnings.extend(behavioral_warnings)
        requires_review = True
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            _append_audit_warning(
                name,
                "behavioral_patterns",
                "; ".join(w["pattern"] for w in behavioral_warnings),
                f"{len(behavioral_warnings)} behavioral pattern(s) detected — review required before enabling",
            )
        except Exception as _be:
            log.warning("Failed to write behavioral audit warning: %s", _be)

    # --- WARN: transitive skill dependencies and risky imports ---
    _SKILL_LOAD_CALL_PATTERNS = [
        r'load_skill\s*\(\s*["\']([^"\']+)["\']',
        r'import_skill\s*\(\s*["\']([^"\']+)["\']',
        r'exec_skill\s*\(\s*["\']([^"\']+)["\']',
    ]
    _SKILL_FILE_PATH_PATTERNS = [
        r'["\']([^"\']+\.skill)["\']',
        r'["\']([^"\']*(?:/skills/|/learned/)[^"\']+\.md)["\']',
    ]
    _RISKY_TRANSITIVE_MODULES = frozenset(
        {
            "requests",
            "httpx",
            "aiohttp",
            "urllib3",
            "paramiko",
            "fabric",
            "invoke",
            "pexpect",
            "subprocess",
        }
    )

    _known_audit_slugs = set(_load_skill_audit_hashes().keys())
    _transitive_dep_warnings: list[dict] = []
    _seen_refs: set[str] = set()

    for _tpat in _SKILL_LOAD_CALL_PATTERNS + _SKILL_FILE_PATH_PATTERNS:
        for _tm in re.finditer(_tpat, content, re.IGNORECASE):
            _ref_stem = Path(_tm.group(1)).stem
            if _ref_stem not in _seen_refs:
                _seen_refs.add(_ref_stem)
                _ref_slug = re.sub(r"[^\w\-]", "_", _ref_stem.lower())
                if _ref_slug not in _known_audit_slugs:
                    _tw: dict = {
                        "line_no": -1,
                        "line_content": "",
                        "pattern": "unaudited_transitive_dep",
                        "category": "transitive_dependency",
                        "mechanism": f"references skill '{_ref_stem}' which has no audit record — unverified trust extension",
                    }
                    _transitive_dep_warnings.append(_tw)
                    _append_audit_warning(name, "transitive_dependency", "unaudited_transitive_dep", _ref_stem[:120])
                    log.warning("Skill '%s' WARN [transitive_dependency] unaudited dep '%s'", name, _ref_stem)

    _transitive_import_re = re.compile(r"^\s*(?:import\s+(\S+)|from\s+(\S+)\s+import)", re.MULTILINE)
    _seen_risky_imports: set[str] = set()
    for _im in _transitive_import_re.finditer(content):
        _raw_mod = _im.group(1) or _im.group(2)
        _top_mod = _raw_mod.split(".")[0]
        if _top_mod in _RISKY_TRANSITIVE_MODULES and _top_mod not in _seen_risky_imports:
            _seen_risky_imports.add(_top_mod)
            _tw = {
                "line_no": content[: _im.start()].count("\n") + 1,
                "line_content": _im.group(0).strip(),
                "pattern": "risky_import",
                "category": "transitive_dependency",
                "mechanism": f"imports '{_top_mod}' — extends network or execution surface; review full trust chain",
            }
            _transitive_dep_warnings.append(_tw)
            _append_audit_warning(name, "transitive_dependency", "risky_import", _im.group(0).strip()[:120])
            log.warning("Skill '%s' WARN [transitive_dependency] risky import '%s'", name, _top_mod)

    if _transitive_dep_warnings:
        warnings.extend(_transitive_dep_warnings)

    _ORCHESTRATION_NAMESPACE_TERMS = [
        "dispatch",
        "agent_registry",
        "trust_level",
        "task_manager",
        "orchestrat",
        "set_priority",
        "route_to",
    ]
    _body_lower = combined.lower()
    _matched_orch_terms = [t for t in _ORCHESTRATION_NAMESPACE_TERMS if t in _body_lower]
    if _matched_orch_terms:
        findings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": "orchestration_namespace_access",
                "category": "orchestration_namespace_access",
                "mechanism": f"orchestration_namespace_access: skill references orchestrator internals, possible dispatch hijack (matched: {', '.join(_matched_orch_terms)})",
            }
        )
        log.warning(
            "Skill '%s' BLOCKED [orchestration_namespace_access] — references orchestrator internals: %s",
            name,
            ", ".join(_matched_orch_terms),
        )

    # --- WARN: implicit delegation / trust-chain extension ---
    _DELEGATION_PATTERNS = [
        (r"sub_agent\.run\s*\(", "sub_agent.run"),
        (r"skill_loader\.load\s*\(", "skill_loader.load"),
        (r"\brun_agent\s*\(", "run_agent"),
        (r"\bdispatch_to\s*\(", "dispatch_to"),
        (r"\binvoke_skill\s*\(", "invoke_skill"),
    ]
    _delegation_targets: list[str] = []
    _seen_delegation: set[str] = set()
    for _dpat, _dlabel in _DELEGATION_PATTERNS:
        for _dm in re.finditer(_dpat, content, re.IGNORECASE):
            if _dlabel not in _seen_delegation:
                _seen_delegation.add(_dlabel)
                _line_no = content[: _dm.start()].count("\n") + 1
                _line_text = lines[_line_no - 1] if 0 < _line_no <= len(lines) else ""
                _delegation_targets.append(f"[line {_line_no}] {_dlabel}: {_line_text.strip()[:120]}")
    if _delegation_targets:
        _delegation_warn = {
            "line_no": -1,
            "line_content": "",
            "pattern": "implicit_delegation",
            "category": "implicit_delegation",
            "mechanism": (
                "skill delegates to sub-agent or loads another skill — trust chain extends beyond this audit; "
                f"delegation targets: {'; '.join(_delegation_targets)}"
            ),
        }
        warnings.append(_delegation_warn)
        log.warning(
            "Skill '%s' WARN [implicit_delegation] — delegation calls found; audit does not cover chained targets: %s",
            name,
            "; ".join(_delegation_targets),
        )

    # --- Social engineering patterns: scan name, description, instruction, and all string fields ---
    _se_target_fields = [name, content]
    if metadata:
        for _v in metadata.values():
            if isinstance(_v, str):
                _se_target_fields.append(_v)
    _se_combined = "\n".join(_se_target_fields)
    for _se_pattern, _se_subcategory in SOCIAL_ENGINEERING_PATTERNS:
        _se_m = re.search(_se_pattern, _se_combined, re.IGNORECASE | re.MULTILINE)
        if _se_m:
            _se_snippet = _se_combined[max(0, _se_m.start() - 20) : _se_m.end() + 40].strip()
            _se_key = (_se_pattern, -3)
            if _se_key not in seen:
                seen.add(_se_key)
                findings.append(
                    {
                        "line_no": _se_combined[: _se_m.start()].count("\n") + 1,
                        "line_content": _se_snippet,
                        "pattern": _se_pattern,
                        "category": "SUSPICIOUS_PROMPT",
                        "mechanism": f"social engineering pattern ({_se_subcategory}): {repr(_se_m.group(0))}",
                    }
                )
                log.warning(
                    "Skill '%s' BLOCKED [SUSPICIOUS_PROMPT/%s] — matched pattern %r: %s",
                    name,
                    _se_subcategory,
                    _se_pattern,
                    _se_snippet[:120],
                )

    _SEMANTIC_INJECTION_INDICATORS = [
        "ignore previous",
        "ignore your",
        "disregard",
        "you are now",
        "forget your",
        "override your",
        "your new instructions",
        "pretend you are",
        "act as if",
        "jailbreak",
        "DAN mode",
    ]
    _si_description = (metadata or {}).get("description", "")
    _si_target = f"{name}\n{_si_description}\n{content}"
    for _si_phrase in _SEMANTIC_INJECTION_INDICATORS:
        _si_m = re.search(re.escape(_si_phrase), _si_target, re.IGNORECASE)
        if _si_m:
            _si_ctx_start = max(0, _si_m.start() - 50)
            _si_ctx_end = min(len(_si_target), _si_m.end() + 50)
            _si_context = _si_target[_si_ctx_start:_si_ctx_end].strip()
            _si_key = (_si_phrase, -4)
            if _si_key not in seen:
                seen.add(_si_key)
                findings.append(
                    {
                        "line_no": _si_target[: _si_m.start()].count("\n") + 1,
                        "line_content": _si_context,
                        "pattern": _si_phrase,
                        "category": "prompt_injection_attempt",
                        "severity": "high",
                        "label": "prompt_injection_attempt",
                        "mechanism": f"semantic injection phrase '{_si_phrase}' found in skill text",
                    }
                )
                log.warning(
                    "Skill '%s' BLOCKED [prompt_injection_attempt] — phrase %r matched; context: %r",
                    name,
                    _si_phrase,
                    _si_context,
                )

    _si_text = f"{name}\n{(metadata or {}).get('description', '')}\n{content}"
    for _sip_pattern, _sip_sub in SEMANTIC_INJECTION_PATTERNS:
        _sip_m = re.search(_sip_pattern, _si_text, re.IGNORECASE)
        if _sip_m:
            _sip_key = (_sip_pattern, -6)
            if _sip_key not in seen:
                seen.add(_sip_key)
                _sip_snippet = _si_text[max(0, _sip_m.start() - 30) : _sip_m.end() + 60].strip()
                findings.append(
                    {
                        "line_no": _si_text[: _sip_m.start()].count("\n") + 1,
                        "line_content": _sip_snippet,
                        "pattern": _sip_pattern,
                        "category": "semantic_injection",
                        "mechanism": f"semantic injection pattern ({_sip_sub}): {repr(_sip_m.group(0))}",
                    }
                )
                log.warning(
                    "Skill '%s' BLOCKED [semantic_injection/%s] — pattern %r matched: %s",
                    name,
                    _sip_sub,
                    _sip_pattern,
                    _sip_snippet[:120],
                )

    _pi_hit, _pi_phrase = _content_looks_like_prompt_injection(combined)
    if _pi_hit:
        log.warning(
            "Skill '%s' WARN [semantic_manipulation/%s] — phrase %r matched; requires explicit human approval before loading",
            name,
            _pi_phrase,
            _pi_phrase,
        )
        warnings.append(
            {
                "line_no": -1,
                "line_content": "",
                "pattern": _pi_phrase,
                "category": "semantic_manipulation",
                "mechanism": f"semantic manipulation phrase {repr(_pi_phrase)} found in skill text — may attempt to override agent instructions at prompt level",
            }
        )
        requires_review = True
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            quarantine_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "skill_name": name,
                "matched_phrase": _pi_phrase,
                "reason": "semantic_manipulation — requires explicit human approval before loading",
            }
            quarantine_path = LOGS_DIR / "skill_quarantine.jsonl"
            with open(quarantine_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(quarantine_entry) + "\n")
        except Exception as _e:
            log.warning("Failed to write skill quarantine entry: %s", _e)

    # --- DANGEROUS_KNOWLEDGE_PAYLOAD: scan knowledge-bearing text against domain blocklist ---
    _knowledge_payloads: list[str] = []
    try:
        import ast as _ast

        _tree = _ast.parse(content)
        for _node in _ast.walk(_tree):
            if isinstance(_node, _ast.Constant) and isinstance(_node.value, str) and len(_node.value) > 100:
                _knowledge_payloads.append(_node.value)
    except SyntaxError:
        pass
    for _qm in re.finditer(r'["\']([^"\']{100,})["\']', content):
        if _qm.group(1) not in _knowledge_payloads:
            _knowledge_payloads.append(_qm.group(1))
    for _kline in content.splitlines():
        if len(_kline.strip()) > 100 and _kline.strip() not in _knowledge_payloads:
            _knowledge_payloads.append(_kline.strip())
    for _payload in _knowledge_payloads:
        for _kb_pattern in SKILL_KNOWLEDGE_BLOCKLIST:
            _kb_m = re.search(_kb_pattern, _payload, re.IGNORECASE)
            if _kb_m:
                _kb_key = (_kb_pattern, -5)
                if _kb_key not in seen:
                    seen.add(_kb_key)
                    findings.append(
                        {
                            "line_no": -1,
                            "line_content": _payload[:200],
                            "pattern": _kb_pattern,
                            "category": "DANGEROUS_KNOWLEDGE_PAYLOAD",
                            "severity": "high",
                            "mechanism": f"dangerous knowledge domain pattern '{_kb_pattern}' matched in skill text payload",
                        }
                    )
                    log.warning(
                        "Skill '%s' BLOCKED [DANGEROUS_KNOWLEDGE_PAYLOAD] — pattern %r matched in knowledge payload",
                        name,
                        _kb_pattern,
                    )

    # --- implicit_trust_chain: imports outside APPROVED_IMPORT_ROOTS and unannotated side-effect calls ---
    _itc_import_re = re.compile(
        r"^\s*(?:import\s+(\S+)|from\s+(\S+)\s+import)",
        re.MULTILINE,
    )
    for _itc_m in _itc_import_re.finditer(content):
        _raw_mod = (_itc_m.group(1) or _itc_m.group(2)).split("(")[0].rstrip(",")
        if _raw_mod.startswith("agents.") and not _raw_mod.startswith("agents.shared"):
            _itc_line_no = content[: _itc_m.start()].count("\n") + 1
            _itc_key = ("implicit_trust_chain_import", _itc_line_no)
            if _itc_key not in seen:
                seen.add(_itc_key)
                findings.append(
                    {
                        "line_no": _itc_line_no,
                        "line_content": _itc_m.group(0).strip(),
                        "pattern": "implicit_trust_chain_import",
                        "category": "implicit_trust_chain",
                        "mechanism": (
                            f"imports from '{_raw_mod}' which is outside APPROVED_IMPORT_ROOTS "
                            f"(only agents.shared and stdlib are permitted) — tacit trust chain"
                        ),
                    }
                )

    for _fn_call, _fn_mechanism in _SIDE_EFFECT_FUNCTIONS.items():
        _fn_re = re.compile(re.escape(_fn_call) + r"\s*\(")
        for _fn_line_no, _fn_line in enumerate(lines, start=1):
            if _fn_re.search(_fn_line) and "# AUDIT-APPROVED" not in _fn_line:
                _fn_key = (_fn_call, _fn_line_no)
                if _fn_key not in seen:
                    seen.add(_fn_key)
                    findings.append(
                        {
                            "line_no": _fn_line_no,
                            "line_content": _fn_line,
                            "pattern": f"unannotated_side_effect:{_fn_call}",
                            "category": "implicit_trust_chain",
                            "mechanism": (
                                f"calls shared side-effect function '{_fn_call}' "
                                f"({_fn_mechanism}) without '# AUDIT-APPROVED' annotation"
                            ),
                        }
                    )

    # --- WARN: dynamic skill loading — implicit trust channel (IMPLICIT_SKILL_CHAIN) ---
    _DYNAMIC_SKILL_LOAD_FUNCS = r"(?:load_skill|run_skill|invoke_skill|exec_skill|import_skill)"
    _IMPLICIT_SKILL_CHAIN_PATTERNS = [
        (
            re.compile(_DYNAMIC_SKILL_LOAD_FUNCS + r"\s*\(\s*(?![\"'])", re.IGNORECASE),
            "non-literal argument (variable, subscript, or f-string) to skill loader — called skill identity resolved at runtime, bypassing audit",
        ),
        (
            re.compile(_DYNAMIC_SKILL_LOAD_FUNCS + r"\s*\([^)]*\+", re.IGNORECASE),
            "string concatenation in skill loader argument — dynamic skill name constructed via +, inheriting trust of called skill without audit",
        ),
    ]
    for _isc_re, _isc_mechanism in _IMPLICIT_SKILL_CHAIN_PATTERNS:
        for _isc_line_no, _isc_line in enumerate(lines, start=1):
            _isc_m = _isc_re.search(_isc_line)
            if _isc_m:
                _isc_key = (_isc_re.pattern, _isc_line_no)
                if _isc_key not in seen:
                    seen.add(_isc_key)
                    _isc_warn = {
                        "line_no": _isc_line_no,
                        "line_content": _isc_line,
                        "pattern": _isc_re.pattern,
                        "category": "IMPLICIT_SKILL_CHAIN",
                        "severity": "WARNING",
                        "mechanism": _isc_mechanism,
                    }
                    warnings.append(_isc_warn)
                    _append_audit_warning(name, "IMPLICIT_SKILL_CHAIN", _isc_re.pattern, _isc_line.strip()[:120])
                    log.warning(
                        "Skill '%s' WARN [IMPLICIT_SKILL_CHAIN] line %d: %s",
                        name,
                        _isc_line_no,
                        _isc_line.strip()[:120],
                    )

    NETWORK_EXFIL_PATTERNS = [r"requests\.post", r"requests\.put", r"urllib.*urlopen", r"curl", r"httpx\.post"]
    SENSITIVE_ACCESS_PATTERNS = [
        r"os\.environ",
        r"keyring",
        r"keychain",
        r"API_KEY",
        r"SECRET",
        r"config\[.*key",
        r"\.env",
    ]
    has_network_exfil = any(re.search(p, combined, re.IGNORECASE) for p in NETWORK_EXFIL_PATTERNS)
    has_sensitive_access = any(re.search(p, combined, re.IGNORECASE) for p in SENSITIVE_ACCESS_PATTERNS)
    if has_network_exfil and has_sensitive_access:
        findings.append(
            {
                "severity": "high",
                "type": "capability_chain",
                "detail": "Skill combines network exfiltration capability with sensitive data access — complete exfiltration channel detected.",
                "line_no": -1,
                "line_content": "",
                "pattern": "network_exfil + sensitive_access",
                "category": "capability_chain",
                "mechanism": "Skill combines network exfiltration capability with sensitive data access — complete exfiltration channel detected.",
            }
        )

    if _source_channel == "web" and warnings:
        log.warning(
            "Skill '%s' [channel=web] zero-tolerance mode — %d warning(s) promoted to blocking findings",
            name,
            len(warnings),
        )
        for _w in warnings:
            _w.setdefault("channel", _source_channel)
        findings.extend(warnings)
        warnings = []

    passed = len(findings) == 0
    findings.extend(warnings)
    checked = ", ".join(_AUDIT_CHECKS_PERFORMED)
    not_checked = ", ".join(_AUDIT_CHECKS_NOT_COVERED)
    _PROXY_CHAIN_MAP = [
        ("network_requests", "Suspicious network request", "remote payload delivery or data exfiltration via network"),
        ("filesystem_code_ops", "Dangerous filesystem/code operation", "dynamic code execution threat"),
        ("obfuscated_code", "Obfuscated or hidden code", "hidden payloads bypassing static pattern analysis"),
        (
            "privilege_escalation",
            "Privilege escalation or credential access",
            "credential theft or system privilege escalation",
        ),
        ("prompt_injection", "Prompt injection attempt", "instruction override or agent identity hijack"),
        ("name_squatting", "name_squatting", "trust spoofing via naming similarity to known-safe skills"),
        ("crude_exfiltration", "crude_exfiltration", "low-sophistication credential or data exfiltration"),
        ("import_allowlist", "import_allowlist", "unauthorized module usage outside known-safe set"),
        (
            "known_attack_patterns",
            "KNOWN_ATTACK",
            "documented attack technique signatures (reverse shell, port scan, shellcode)",
        ),
        (
            "privileged_module_access",
            "privileged_module_access",
            "unauthorized access to internal agent infrastructure",
        ),
        ("transitive_dependencies", "transitive_dependency", "trust chain extension through unaudited dependencies"),
        ("behavioral_patterns", "behavioral_patterns", "anomalous runtime behavior suggesting covert action"),
        (
            "declaration_behavior_consistency",
            "declaration_behavior",
            "deceptive skill metadata to bypass intent-based filtering",
        ),
        (
            "soul_access_pattern",
            "SOUL_ACCESS_PATTERN",
            "identity or memory exfiltration via internal soul path references",
        ),
        (
            "knowledge_extraction",
            "knowledge_extraction",
            "combined memory read and outbound network — data exfiltration vector",
        ),
        (
            "implicit_trust_chain",
            "implicit_trust_chain",
            "laundering dangerous behavior through non-shared agent imports or unannotated side-effect calls",
        ),
    ]
    proxy_chain = [
        {
            "check": _cn,
            "proxy_for": _pf,
            "passed": not any(_cm in f.get("category", "") for f in findings),
        }
        for _cn, _cm, _pf in _PROXY_CHAIN_MAP
    ]
    if not passed:
        log.warning(
            "Skill '%s' BLOCKED [channel=%s] (checked: %s | NOT checked: %s) — %d finding(s)",
            name,
            _source_channel,
            checked,
            not_checked,
            len(findings),
        )
        for f in findings:
            if f["line_no"] >= 0:
                log.warning(
                    "  [line %d] [%s] pattern=%r — %s | %s",
                    f["line_no"],
                    f["category"],
                    f["pattern"],
                    f["mechanism"],
                    f["line_content"][:120],
                )
            else:
                log.warning("  [%s] %s", f["category"], f["mechanism"])
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            incident = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "skill_name": name,
                "source": "audit_skill",
                "failure_reason": findings[0]["mechanism"] if findings else "unknown",
                "findings": findings,
                "blocked": True,
            }
            incidents_path = LOGS_DIR / "security_incidents.jsonl"
            with open(incidents_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(incident) + "\n")
            blocked_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "skill_name": name,
                "source": source,
                "rejection_reasons": list({f["category"] for f in findings}),
            }
            blocked_log_path = LOGS_DIR / "blocked_skills_log.jsonl"
            with open(blocked_log_path, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(blocked_entry) + "\n")
            failure_entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "skill_name": name,
                "source": source,
                "failure_reasons": list({f["category"] for f in findings}),
            }
            _SKILL_AUDIT_FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_SKILL_AUDIT_FAILURES_PATH, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(failure_entry) + "\n")
        except Exception as _e:
            log.warning("Failed to write security incident record: %s", _e)
        try:
            _fail_record = {
                "verdict": "fail",
                "checks_run": list(_AUDIT_CHECKS_PERFORMED),
                "files_scanned": files_scanned,
                "findings": [
                    {
                        "pattern": f["pattern"],
                        "file": f.get("file", f"skill:{name}"),
                        "line": f["line_no"],
                        "excerpt": f["line_content"][:200],
                    }
                    for f in findings
                ],
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "skill_name": name,
                "source": source,
            }
            _audit_log_path = LOGS_DIR / "skill_audit_log.jsonl"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(_audit_log_path, "a", encoding="utf-8") as _alf:
                _alf.write(json.dumps(_fail_record) + "\n")
        except Exception as _ale:
            log.warning("Failed to write skill audit log entry: %s", _ale)
        log.info(
            "skill_audit skill_name=%s source=%s outcome=block trail=%s",
            name,
            origin,
            audit_trail,
        )
        raise SkillAuditFailedError(f"Skill blocked: {findings[0]['mechanism'] if findings else 'unknown'}")
    else:
        if source in _EXTERNAL_SOURCES:
            _channel_risk_entry = {
                "line_no": -1,
                "line_content": "",
                "pattern": "external_channel",
                "category": "CHANNEL_RISK",
                "mechanism": f"skill arrived via '{source}' — passes technical audit but channel trust is unverified; evaluator should apply lower trust weight",
            }
            warnings.append(_channel_risk_entry)
            log.warning(
                "Skill '%s' CHANNEL_RISK — source='%s'; passed technical checks but external channel origin requires lower trust weight",
                name,
                source,
            )

        _outcome = "passed-external-channel" if source in _EXTERNAL_SOURCES else "passed-clean"
        _n_checks = len(proxy_chain)
        _n_passed = sum(1 for c in proxy_chain if c["passed"])
        log.info(
            "Skill '%s' PASSED (%d/%d pattern checks clean; scope: known-bad patterns only | NOT checked: %s)",
            name,
            _n_passed,
            _n_checks,
            not_checked,
        )
        log.info(
            "skill_audit skill_name=%s source=%s outcome=%s trail=%s",
            name,
            source,
            _outcome,
            audit_trail,
        )
        log.info(
            "skill_audit skill_name=%s spec_coverage_note='Audit covers known-bad static patterns only. Novel or obfuscated attack vectors outside this spec are undetected.'",
            name,
        )

    implicit_trust_extensions = []
    _TRUST_PATTERNS = [
        (r'["\']~?[/\\]?(?:.*Sandbox[/\\]Mira|MIRA_ROOT)\S*["\']', "Sandbox/Mira path string"),
        (r"MIRA_ROOT\s*(?:\+|/|\\|\[)", "MIRA_ROOT reference"),
        (r"^(?:from|import)\s+(agents|lib)\b", "agent module import"),
        (r"open\s*\([^)]*\.(?:py|md)\b", "open() call to .py or skill file"),
    ]
    for pattern, label in _TRUST_PATTERNS:
        for line_no, line in enumerate(lines, start=1):
            m = re.search(pattern, line, re.IGNORECASE)
            if m:
                entry = f"[line {line_no}] {label}: {line.strip()[:120]}"
                if entry not in implicit_trust_extensions:
                    implicit_trust_extensions.append(entry)

    if implicit_trust_extensions:
        log.warning(
            "Skill '%s' passes code audit but implicitly trusts: %s",
            name,
            implicit_trust_extensions,
        )

    if passed and origin == "external":
        today = today_local()
        try:
            ingestion_log = (
                json.loads(_SKILL_INGESTION_LOG.read_text(encoding="utf-8")) if _SKILL_INGESTION_LOG.exists() else {}
            )
        except (json.JSONDecodeError, OSError):
            ingestion_log = {}
        ingestion_log[today] = ingestion_log.get(today, 0) + 1
        try:
            _SKILL_INGESTION_LOG.parent.mkdir(parents=True, exist_ok=True)
            _SKILL_INGESTION_LOG.write_text(json.dumps(ingestion_log, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as _e:
            log.warning("Failed to update skill ingestion log: %s", _e)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        _safe_name = re.sub(r"[^\w\-]", "_", name)
        _trace_path = LOGS_DIR / f"skill_audit_{_safe_name}_{_ts}.json"
        _trace_path.write_text(
            json.dumps(
                {"skill_name": name, "timestamp": _ts, "passed": passed, "audit_trail": audit_trail},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as _e:
        log.warning("Failed to write skill audit trace: %s", _e)

    WARN_PATTERNS = [
        "subprocess",
        "importlib",
        "ctypes",
        "cffi",
        "pickle",
        "__import__",
        "marshal",
        "compileall",
        "zipimport",
    ]
    coverage_gaps = [p for p in WARN_PATTERNS if p in combined]
    if coverage_gaps:
        log.warning(
            "Skill '%s' coverage_gaps — high-surface-area patterns not fully covered by block rules: %s",
            name,
            coverage_gaps,
        )

    _has_channel_risk = source in _EXTERNAL_SOURCES
    _result_tier = "PASS_WITH_CONCERNS" if warnings else "PASS_CLEAN"
    if warnings:
        log.warning(
            "Skill '%s' PASS_WITH_CONCERNS — %d concern(s): %s",
            name,
            len(warnings),
            "; ".join(w["mechanism"] for w in warnings[:5]),
        )
    _ts_iso = datetime.utcnow().isoformat() + "Z"
    result = {
        "passed": passed,
        "verdict": "pass",
        "checks_run": list(_AUDIT_CHECKS_PERFORMED),
        "files_scanned": files_scanned,
        "findings": [
            {
                "pattern": f["pattern"],
                "file": f.get("file", f"skill:{name}"),
                "line": f["line_no"],
                "excerpt": f["line_content"][:200],
            }
            for f in findings
        ],
        "timestamp": _ts_iso,
        "result": _result_tier,
        "requires_review": requires_review,
        "implicit_trust_extensions": implicit_trust_extensions,
        "transitive_dependencies": _transitive_dep_warnings,
        "status": "CHANNEL_RISK" if _has_channel_risk else ("WARN" if coverage_gaps else "PASS"),
        "source_provenance": source,
        "source_channel": _source_channel,
        "blocked": False,
        "spec_coverage_note": "Audit covers known-bad static patterns only. Novel or obfuscated attack vectors outside this spec are undetected.",
        "proxy_chain": proxy_chain,
        "verification_depth": "static-pattern-match",
        "assumptions": [
            "no known-bad URL patterns detected",
            "no eval/exec/os.system calls detected",
            "no base64/hex obfuscation detected",
            "no known privilege-escalation patterns detected",
            "runtime behavior and intent unverified",
        ],
    }
    if coverage_gaps:
        result["coverage_gaps"] = coverage_gaps
    try:
        _audit_log_path = LOGS_DIR / "skill_audit_log.jsonl"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _log_entry = {
            "verdict": "pass",
            "checks_run": result["checks_run"],
            "files_scanned": files_scanned,
            "findings": result["findings"],
            "timestamp": _ts_iso,
            "skill_name": name,
            "source": source,
        }
        with open(_audit_log_path, "a", encoding="utf-8") as _alf:
            _alf.write(json.dumps(_log_entry) + "\n")
    except Exception as _ale:
        log.warning("Failed to write skill audit log entry: %s", _ale)
    return result


def reaudit_all_skills(skills_dir: Path) -> list[str]:
    """Re-run audit_skill() over every .md/.yaml skill file in skills_dir.

    Returns the list of skill names that failed. Logs a WARN for each failure.
    Does NOT delete failing skills — they are surfaced for human review only.
    """
    failing: list[str] = []
    if not skills_dir.exists():
        return failing
    skill_files = [p for p in sorted(skills_dir.rglob("*")) if p.is_file() and p.suffix in {".md", ".yaml", ".yml"}]
    for skill_path in skill_files:
        name = skill_path.stem
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("reaudit_all_skills: could not read %s: %s", skill_path.name, e)
            continue
        try:
            audit_skill(name, content, source="internal")
        except SkillAuditFailedError as e:
            log.warning("reaudit_all_skills: skill '%s' FAILED audit — %s", name, e)
            failing.append(name)
        except Exception as e:
            log.warning("reaudit_all_skills: error auditing '%s' — %s", name, e)
    if failing:
        log.warning(
            "reaudit_all_skills: %d/%d skill(s) failed re-audit (NOT deleted — human review required): %s",
            len(failing),
            len(skill_files),
            ", ".join(failing),
        )
    else:
        log.info("reaudit_all_skills: all %d skill(s) passed", len(skill_files))
    return failing


def _classify_skill_type(name: str, content: str) -> tuple[str, str]:
    """Classify whether content is a genuine skill or a bug fix/procedure.

    Returns (classification, reason) where classification is one of:
    - 'skill': Transferable knowledge framework - save it
    - 'bugfix': Bug fix or workaround - should be code, not a skill
    - 'procedure': Step-by-step for specific tool - should be docs, not a skill
    """
    lower = content.lower()

    # Bug fix indicators: rules about what to check/verify before doing X
    bugfix_patterns = [
        r"(?:always|must|never)\s+(?:check|verify|ensure|validate)\s+.*\s+(?:before|first)",
        r"(?:workaround|fix for|regression|patch)\b",
        r"(?:bug|broke|broken|incident)\b.*\b(?:fix|patch|resolve)",
        r"never\s+(?:skip|forget|omit)\b",
    ]
    bugfix_score = sum(1 for p in bugfix_patterns if re.search(p, lower))

    # Procedure indicators: specific API calls, tool commands
    procedure_patterns = [
        r"(?:POST|GET|PUT|DELETE)\s+/api/",
        r"curl\s+",
        r"step\s+\d+[.:]\s",
        r"(?:run|execute)\s+(?:the\s+)?(?:command|script)",
        r"endpoint[:\s]",
    ]
    procedure_score = sum(1 for p in procedure_patterns if re.search(p, lower))

    # Skill indicators: frameworks, principles, transferable concepts
    skill_patterns = [
        r"(?:principle|framework|pattern|strategy|technique|method)\b",
        r"(?:when to|how to|why)\s+\w+",
        r"(?:trade-?off|spectrum|continuum)\b",
        r"(?:example|instance|case)\b.*\b(?:apply|use|adapt)",
    ]
    skill_score = sum(1 for p in skill_patterns if re.search(p, lower))

    # Decision logic
    if bugfix_score >= 2 and skill_score < 2:
        return "bugfix", f"Content has {bugfix_score} bug-fix patterns (verify/check/never rules)"
    if procedure_score >= 2 and skill_score < 2:
        return "procedure", f"Content has {procedure_score} procedure patterns (API calls, step-by-step)"

    return "skill", "Passes skill classification"


def _parse_last_audited(content: str) -> "datetime | None":
    """Parse last_audited timestamp from YAML frontmatter."""
    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = content[4:end]
    m = re.search(r"^last_audited:\s*(\S+)", frontmatter, re.MULTILINE)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).strip().rstrip("Z"))
    except ValueError:
        return None


def _inject_last_audited(content: str, ts: str) -> str:
    """Inject or update last_audited in YAML frontmatter. Creates minimal frontmatter if absent."""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            frontmatter = content[4:end]
            rest = content[end:]
            if re.search(r"^last_audited:", frontmatter, re.MULTILINE):
                frontmatter = re.sub(r"^last_audited:\s*\S+", f"last_audited: {ts}", frontmatter, flags=re.MULTILINE)
            else:
                frontmatter = frontmatter.rstrip("\n") + f"\nlast_audited: {ts}\n"
            return "---\n" + frontmatter + rest
    return f"---\nlast_audited: {ts}\n---\n\n{content}"


def reaudit_stale_skills(max_age_days: int = 30) -> dict:
    """Re-audit skill files not audited within max_age_days.

    On pass: updates last_audited timestamp in file frontmatter.
    On fail: renames file to .blocked (quarantine).
    Returns summary counts.
    """
    from datetime import timedelta

    if not SKILLS_DIR.exists():
        return {"checked": 0, "passed": 0, "quarantined": 0, "skipped": 0}

    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    skill_files = [p for p in sorted(SKILLS_DIR.rglob("*")) if p.is_file() and p.suffix == ".md"]

    checked = passed = quarantined = skipped = 0

    for skill_path in skill_files:
        name = skill_path.stem
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("reaudit_stale_skills: cannot read %s: %s", skill_path.name, e)
            skipped += 1
            continue

        last_audited = _parse_last_audited(content)
        if last_audited and last_audited > cutoff:
            skipped += 1
            continue

        checked += 1
        try:
            audit_skill(name, content, source="internal")
            ts = datetime.utcnow().isoformat() + "Z"
            _atomic_write(skill_path, _inject_last_audited(content, ts))
            passed += 1
            log.info("reaudit_stale_skills: '%s' passed", name)
        except SkillAuditFailedError as e:
            blocked_path = skill_path.with_suffix(".blocked")
            skill_path.rename(blocked_path)
            quarantined += 1
            log.warning(
                "reaudit_stale_skills: '%s' FAILED — quarantined as %s: %s",
                name,
                blocked_path.name,
                e,
            )
        except Exception as e:
            log.warning("reaudit_stale_skills: error auditing '%s': %s", name, e)
            skipped += 1

    log.info(
        "reaudit_stale_skills: checked=%d passed=%d quarantined=%d skipped=%d",
        checked,
        passed,
        quarantined,
        skipped,
    )
    return {"checked": checked, "passed": passed, "quarantined": quarantined, "skipped": skipped}


def save_skill(name: str, description: str, content: str, source_title: str = "", source_url: str = "") -> bool:
    """Save a new skill and update the index. Runs security audit first.

    Returns True if the skill was saved, False if rejected by audit or quality gate.
    """
    # --- Security audit gate ---
    try:
        _audit = audit_skill(name, content)
    except SkillAuditFailedError:
        return False  # Do not save
    if _audit.get("requires_review"):
        log.warning(
            "Skill '%s' REQUIRES_REVIEW — soul access pattern detected; quarantined pending explicit justification",
            name,
        )
        return False
    _audit_has_concerns = _audit.get("result") == "PASS_WITH_CONCERNS"
    if _audit_has_concerns:
        log.warning(
            "Skill '%s' saving with concerns: %s",
            name,
            _audit.get("proxy_chain", _audit.get("findings", [])),
        )

    # Quality gate: reject bug fixes and procedures
    classification, reason = _classify_skill_type(name, content)
    if classification != "skill":
        log.warning("Skill '%s' classified as %s, not saving: %s", name, classification, reason)
        return False

    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    _audited_ts = datetime.utcnow().isoformat() + "Z"
    content = _inject_last_audited(content, _audited_ts)
    _atomic_write(path, content)
    _save_skill_audit_hash(slug, hashlib.sha256(content.encode("utf-8")).hexdigest())

    # Update index (locked — shared across processes)
    def _update_index(text):
        index = []
        if text:
            try:
                index = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
        index = [s for s in index if s["name"] != name]
        entry = {
            "name": name,
            "description": description,
            "file": f"{slug}.md",
            "created": datetime.now().isoformat(),
            "audited_at": datetime.utcnow().isoformat() + "Z",
        }
        if _audit_has_concerns:
            entry["scrutiny"] = True
        index.append(entry)
        return json.dumps(index, indent=2, ensure_ascii=False)

    _locked_read_modify_write(SKILLS_INDEX, _update_index)
    log.info("Saved skill: %s", name)
    _log_change("SAVE_SKILL", f"{slug}.md", name)

    def _append_provenance(text):
        try:
            records = json.loads(text) if text else []
        except (json.JSONDecodeError, ValueError):
            records = []
        records = [r for r in records if r.get("skill_name") != name]
        records.append(
            {
                "skill_name": name,
                "source_article_title": source_title,
                "source_url": source_url,
                "date_added": datetime.now().isoformat(),
                "times_loaded": 0,
                "last_loaded": None,
            }
        )
        return json.dumps(records, indent=2, ensure_ascii=False)

    _locked_read_modify_write(_SKILL_PROVENANCE_FILE, _append_provenance)

    # Keep skills.md in sync
    rebuild_skills_md()

    # Sync actionable skills to CLAUDE.md for Claude Code sessions
    _sync_skills_to_claude_md()

    return True


def update_skill(name: str, content: str) -> bool:
    """Update the content of an existing skill. Runs security audit first.

    Returns True if the skill was updated, False if rejected by audit or not found.
    """
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    if not path.exists():
        log.warning("update_skill: skill '%s' not found, cannot update", name)
        return False

    try:
        _audit = audit_skill(name, content)
    except SkillAuditFailedError:
        return False
    if _audit.get("requires_review"):
        log.warning(
            "Skill '%s' REQUIRES_REVIEW — soul access pattern detected; quarantined pending explicit justification",
            name,
        )
        return False
    if _audit.get("result") == "PASS_WITH_CONCERNS":
        log.warning(
            "Skill '%s' updating with concerns: %s",
            name,
            _audit.get("proxy_chain", _audit.get("findings", [])),
        )

    _atomic_write(path, content)
    _save_skill_audit_hash(slug, hashlib.sha256(content.encode("utf-8")).hexdigest())
    log.info("Updated skill: %s", name)

    rebuild_skills_md()
    _sync_skills_to_claude_md()

    return True


def get_stale_skills(threshold_days: int = 30) -> list[dict]:
    """Return skills whose audited_at is older than threshold_days or missing."""
    from datetime import timedelta

    if not SKILLS_INDEX.exists():
        return []
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = datetime.utcnow() - timedelta(days=threshold_days)
    stale = []
    for skill in index:
        audited_at = skill.get("audited_at")
        if not audited_at:
            stale.append(skill)
            continue
        try:
            ts = datetime.fromisoformat(audited_at.rstrip("Z"))
            if ts < cutoff:
                stale.append(skill)
        except ValueError:
            stale.append(skill)
    return stale


def _refresh_audited_at(name: str):
    """Update audited_at timestamp for a skill that passed re-audit."""

    def _update(text):
        try:
            index = json.loads(text) if text else []
        except (json.JSONDecodeError, ValueError):
            index = []
        now = datetime.utcnow().isoformat() + "Z"
        for skill in index:
            if skill.get("name") == name:
                skill["audited_at"] = now
                break
        return json.dumps(index, indent=2, ensure_ascii=False)

    _locked_read_modify_write(SKILLS_INDEX, _update)


def quarantine_skill(name: str, reason: str) -> bool:
    """Move a skill to soul/skills/quarantine/ and remove it from the index.

    Returns True if quarantined, False if the skill file was not found.
    """
    slug = name.lower().replace(" ", "-")
    skill_path = SKILLS_DIR / f"{slug}.md"
    quarantine_dir = SKILLS_DIR / "quarantine"

    if not skill_path.exists():
        log.warning("quarantine_skill: '%s' not found at %s", name, skill_path)
        return False

    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dest = quarantine_dir / f"{slug}.md"
    skill_path.rename(dest)

    def _remove_from_index(text):
        try:
            index = json.loads(text) if text else []
        except (json.JSONDecodeError, ValueError):
            index = []
        index = [s for s in index if s.get("name") != name]
        return json.dumps(index, indent=2, ensure_ascii=False)

    _locked_read_modify_write(SKILLS_INDEX, _remove_from_index)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "skill_name": name,
            "reason": reason,
            "quarantine_path": str(dest),
        }
        quarantine_log = LOGS_DIR / "skill_quarantine.jsonl"
        with open(quarantine_log, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry) + "\n")
    except OSError as e:
        log.warning("Failed to write quarantine log entry: %s", e)

    log.warning("Skill '%s' quarantined: %s", name, reason)
    return True


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
_ACTIONABLE_TAGS = {
    "writing",
    "craft",
    "fiction",
    "dialogue",
    "video",
    "editing",
    "agents",
    "coding",
    "architecture",
    "tool-use",
    "debugging",
    "math",
    "proof",
    "latex",
    "exposition",
    "publishing",
    "research",
    "problem-solving",
    "asymptotics",
}

# CLAUDE.md lives at MtJoy root so all Claude Code sessions in MtJoy see it
_CLAUDE_MD = MIRA_ROOT.parent / "CLAUDE.md"


_AGENT_SKILL_INDEXES = [
    # Per-agent skill index files (relative to agents dir)
    Path(MIRA_ROOT) / "agents" / "researcher" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "coder" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "general" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "analyst" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "explorer" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "photo" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "video" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "podcast" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "socialmedia" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "writer" / "skills" / "index.json",
    Path(MIRA_ROOT) / "agents" / "super" / "skills" / "index.json",
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
    skill_lines.append(f"Full skill details: `Mira/data/soul/learned/` and `Mira/agents/math/skills/`")
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
        before = existing[: existing.index(MARKER_START)]
        after_marker = existing[existing.index(MARKER_END) + len(MARKER_END) :]
        new_content = f"{before}{MARKER_START}\n{skills_block}\n{MARKER_END}{after_marker}"
    else:
        if existing:
            new_content = f"{existing}\n\n{MARKER_START}\n{skills_block}\n{MARKER_END}\n"
        else:
            new_content = f"{MARKER_START}\n{skills_block}\n{MARKER_END}\n"

    _locked_write(_CLAUDE_MD, new_content)
    log.info("Synced %d actionable skills to CLAUDE.md", len(actionable))


def skill_audit_summary(days: int = 7) -> dict:
    """Aggregate recent security audit failures for reflection.

    Reads blocked_skills_log.jsonl, filters to the last N days,
    groups by rejection reason, and returns counts with representative examples.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    blocked_log_path = LOGS_DIR / "blocked_skills_log.jsonl"

    if not blocked_log_path.exists():
        return {}

    by_reason: dict[str, list[str]] = {}
    try:
        for line in blocked_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                ts = datetime.fromisoformat(entry["timestamp"].rstrip("Z"))
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            for reason in entry.get("rejection_reasons", []):
                by_reason.setdefault(reason, []).append(entry.get("skill_name", "unknown"))
    except OSError:
        return {}

    return {reason: {"count": len(names), "examples": names[:3]} for reason, names in by_reason.items()}
