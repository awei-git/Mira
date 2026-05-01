"""Authorization event logging and skill audit coverage checks."""

import ast
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("mira")

_TRUST_AUDIT_LOG: Path | None = None


def _trust_audit_log() -> Path:
    global _TRUST_AUDIT_LOG
    if _TRUST_AUDIT_LOG is None:
        from config import LOGS_DIR

        _TRUST_AUDIT_LOG = LOGS_DIR / "trust_audit.jsonl"
    return _TRUST_AUDIT_LOG


def log_authorization_event(
    action: str,
    authorizing_source: str,
    permission_level: str,
    bypassed_check: bool,
) -> None:
    """Append one authorization event to logs/trust_audit.jsonl.

    authorizing_source: iphone_bridge | api_key | cron | internal
    permission_level:   high | normal | low
    bypassed_check:     True when a confirmation step was skipped
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "authorizing_source": authorizing_source,
        "permission_level": permission_level,
        "bypassed_check": bypassed_check,
    }
    if bypassed_check and permission_level == "high":
        log.warning(
            "TRUST_AUDIT high-privilege source skipped confirmation: action=%s source=%s",
            action,
            authorizing_source,
        )
    try:
        path = _trust_audit_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        log.debug("trust_audit write failed: %s", exc)


def check_audit_coverage() -> list[str]:
    """Return filenames of skill files in SKILLS_DIR with no audit record.

    Cross-references every .py/.md file under the soul skills directory against
    audit_hashes.json. Any file whose stem has no entry there has never been
    audited and is returned so callers can surface it as a warning.
    """
    try:
        from config import SKILLS_DIR
    except Exception as exc:
        log.debug("check_audit_coverage: config import failed: %s", exc)
        return []

    if not SKILLS_DIR.exists():
        return []

    audit_hashes_path: Path = SKILLS_DIR.parent / "audit_hashes.json"
    try:
        audited: set[str] = set(json.loads(audit_hashes_path.read_text(encoding="utf-8")).keys())
    except (OSError, json.JSONDecodeError):
        audited = set()

    unaudited: list[str] = []
    for skill_file in sorted(SKILLS_DIR.rglob("*")):
        if skill_file.is_file() and skill_file.suffix in {".py", ".md"}:
            if skill_file.stem not in audited:
                unaudited.append(skill_file.name)

    return unaudited


_VERIF_PATTERN = re.compile(r"\b(verif|validat|check|confirm)\w*", re.IGNORECASE)
_NETWORK_PATTERN = re.compile(r"\b(requests\.|httpx\.|urllib|aiohttp|fetch|subprocess\.|os\.system|Popen)\b")

_TRUST_VOCAB: tuple[str, ...] = ("verify", "certified", "trusted", "official", "validated", "authentic")

_VERIF_MECHANISM_PATTERN = re.compile(
    r"\b(hashlib|sha\d+|md5|checksum|assert)\b"
    r"|\b(open\s*\(|read_text|read_bytes|\.read\s*\()"
    r"|\b(requests\.|httpx\.|urllib|aiohttp)\b"
    r"|\s==\s|\s!=\s",
    re.IGNORECASE,
)

_BEHAV_LOOP_CRED_PATTERN = re.compile(
    r"for\s+\w.*\bin\b.*(range|list|hosts|ips|users|passwords)",
    re.IGNORECASE,
)
_BEHAV_BULK_CONNECT_PATTERN = re.compile(r"\b(socket\.connect|requests\.get|paramiko\.connect)\b")
_BEHAV_LOOP_PATTERN = re.compile(r"\b(for|while)\b")
_BEHAV_CRED_HARVEST_PATTERN = re.compile(
    r"(glob|os\.walk|os\.listdir).{0,300}(\.ssh|\.aws|/etc/passwd|/etc/shadow)",
    re.DOTALL,
)
_BEHAV_EXFIL_PATTERN = re.compile(r"\b(requests\.post|socket\.send)\b")
_BEHAV_FILE_READ_PATTERN = re.compile(r"\b(open\s*\(|read_text|read_bytes|\.read\s*\()\b")

KNOWN_ATTACK_PATTERNS: dict[str, list[re.Pattern]] = {
    "credential_harvest": [
        re.compile(r"\bos\.environ\b"),
        re.compile(r"\bos\.getenv\s*\("),
        re.compile(r"\bdotenv\.load\b"),
        re.compile(r"security\s+find-(?:generic|internet)-password"),
        re.compile(r"\bKeychain\b", re.IGNORECASE),
        re.compile(r"['\"]~?/\.ssh/"),
        re.compile(r"/etc/(?:passwd|shadow|sudoers)"),
        re.compile(r"['\"]~?/\.(?:aws|gcp|azure)/"),
    ],
    "data_exfil": [
        re.compile(r"\brequests\.post\s*\("),
        re.compile(r"\bhttpx\.post\s*\("),
        re.compile(r"\burllib\.request\.urlopen\b"),
        re.compile(r"\bcurl\b.*\s-[dTF]\s"),
        re.compile(r"\bsocket\.send(?:all)?\s*\("),
    ],
    "persistence": [
        re.compile(r"\blaunchctl\b"),
        re.compile(r"\bcrontab\b"),
        re.compile(r"['\"]~?/\.(?:bashrc|bash_profile|zshrc|profile|zprofile)['\"]"),
        re.compile(r"LaunchAgents/.*\.plist"),
        re.compile(r"LaunchDaemons/.*\.plist"),
        re.compile(r"/etc/cron(?:tab|\.d)/"),
    ],
    "lateral_movement": [
        re.compile(r"\bsubprocess\b.{0,80}\bssh\b", re.DOTALL),
        re.compile(r"\bssh\b.{0,80}\b(?:subprocess|Popen|run)\b", re.DOTALL),
        re.compile(r"\bparamiko\b"),
        re.compile(r"\bfabric\.(?:Connection|api)\b"),
        re.compile(r"\bsmbclient\b"),
        re.compile(r"\bwinrm\b", re.IGNORECASE),
        re.compile(r"\bpsexec\b", re.IGNORECASE),
    ],
}

_CIRC_AUDIT_IMPORTS = frozenset({"json", "logging", "re", "datetime", "pathlib"})
_CIRC_GROUND_TRUTH_PATHS = frozenset({"audit_hashes.json", "trust_audit.jsonl"})
_CIRC_AUDIT_FN_NAMES = frozenset(
    {
        "_check_behavioral_patterns",
        "_check_circular_trust",
        "audit_skill",
        "check_audit_coverage",
        "log_authorization_event",
        "_trust_audit_log",
    }
)

_CIRC_IMPORT_PATTERN = re.compile(
    r"(?:import|from)\s+(" + "|".join(re.escape(m) for m in sorted(_CIRC_AUDIT_IMPORTS)) + r")\b"
)
_CIRC_MONKEYPATCH_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in sorted(_CIRC_AUDIT_IMPORTS)) + r")\.\w+\s*="
)
_CIRC_WRITE_PATTERN = re.compile(r"\b(?:open\s*\([^)]*['\"][wa]['\"]|write_text|write_bytes|\.write\s*\()")
_CIRC_GT_PATH_PATTERN = re.compile("|".join(re.escape(p) for p in sorted(_CIRC_GROUND_TRUTH_PATHS)))
_CIRC_FUNC_DEF_PATTERN = re.compile(
    r"\bdef\s+(" + "|".join(re.escape(f) for f in sorted(_CIRC_AUDIT_FN_NAMES)) + r")\s*\("
)

_SELF_REF_AUDIT_PATTERNS = [
    "audit_skill",
    "soul_manager.audit",
    "bypass_audit",
    "pre_audited",
    "already_audited",
    "skip_audit",
    "audit_bypass",
]

TRUSTED_INTERNAL_SOURCES: frozenset[str] = frozenset({"internal", "self_distillation", "mira_core"})

BEHAVIOR_MODIFICATION_VOCABULARY: frozenset[str] = frozenset(
    {
        "align",
        "tune",
        "adjust behavior",
        "modify tone",
        "calibrate response",
        "personality",
        "values",
    }
)

_SELF_MOD_KEYWORDS: tuple[str, ...] = (
    "align",
    "calibrate behavior",
    "adjust values",
    "tune personality",
    "modify tone",
    "fine-tune response",
    "internalize",
    "reshape",
)

ORCHESTRATOR_SCOPE_TAGS: frozenset[str] = frozenset(
    {"super", "orchestrator", "dispatch", "core", "task_manager", "task_worker"}
)

_OVERREACH_SANDBOX_ROOTS = (
    "~/Sandbox/",
    "~/Library/Mobile Documents/com~apple~CloudDocs/MtJoy/",
)

_OVERREACH_PATH_PATTERN = re.compile(r'(?:[\'"]|\b)((?:\.\./|/Users/|/home/|~/)[^\s\'"`,;)\\\n]{2,})')
_OVERREACH_ENV_PATTERN = re.compile(r"\b(os\.environ|os\.getenv|dotenv\.load)\b")
_OVERREACH_NETWORK_CALL_PATTERN = re.compile(r"\b(?:socket\.connect|urllib\.request|requests\.(?:get|post))\b")
_OVERREACH_HARDCODED_IP_PATTERN = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_OVERREACH_HARDCODED_DOMAIN_PATTERN = re.compile(r'[\'"]https?://[a-zA-Z0-9][a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}[/\'"]')
_PERMISSIONS_ANNOTATION_PATTERN = re.compile(r"^#\s*PERMISSIONS\s*:", re.MULTILINE)

PROMPT_INJECTION_SIGNATURES: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|all|your)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+your\s+(rules|guidelines|constraints)", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions", re.IGNORECASE),
    re.compile(r"your\s+new\s+instructions\s+are", re.IGNORECASE),
    re.compile(r"\bforget\s+everything\b", re.IGNORECASE),
    re.compile(r"\boverride\s+your\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\s*:", re.IGNORECASE),
]

_INDIRECT_EXEC_PATTERNS = re.compile(
    r"(subprocess\.Popen|subprocess\.run|subprocess\.call|subprocess\.check_output"
    r"|__import__\s*\(|importlib\.import_module|compile\s*\("
    r"|getattr\s*\(\s*__builtins__|getattr\s*\(\s*builtins"
    r"|ctypes\.|cffi\.|distutils\.core\.run_setup)"
)

_OBFUSCATION_PATTERN = re.compile(r"(base64\.b64decode|codecs\.decode|\\x[0-9a-fA-F]{2}.*\\x)")
_STRICT_NETWORK_PATTERN = re.compile(
    r"\b(requests\.|httpx\.|urllib|aiohttp|fetch|subprocess\.|os\.system|Popen"
    r"|import\s+requests|import\s+httpx|import\s+socket|import\s+aiohttp)\b"
)
_STRICT_OBFUSCATION_PATTERN = re.compile(r"(base64|codecs\.decode|\\x[0-9a-fA-F]{2})")

_COMPOUND_SENSITIVE_PATH_PATTERN = re.compile(
    r"soul/|/config\b|\.env\b|~/\.ssh|keychain|API_KEY|SECRET|TOKEN",
    re.IGNORECASE,
)
_COMPOUND_NETWORK_CALL_PATTERN = re.compile(
    r"\b(requests\.get|requests\.post|httpx\.|urllib\.|aiohttp\.|socket\.connect)\b|(?<!\w)curl\b",
)

COVERT_CHANNEL_SERVICES: frozenset[str] = frozenset(
    {
        "drive.google.com",
        "script.google.com",
        "googleapis.com",
        "workers.dev",
        "cloudflare.com",
        "amazonaws.com",
        "s3.",
        "pastebin.com",
        "gist.github.com",
        "webhook.site",
        "ngrok.io",
        "serveo.net",
    }
)

DETECTION_VOCAB: frozenset[str] = frozenset(
    {"detect", "classify", "filter", "identify", "recognize", "validate", "check", "screen"}
)
SENSITIVE_VOCAB: frozenset[str] = frozenset(
    {"harmful", "dangerous", "malicious", "unsafe", "exploit", "attack", "injection", "jailbreak", "toxic"}
)

_SOUL_LOGS_PATH_PATTERN = re.compile(r"(?:soul/|/soul\b|logs/|/logs\b)", re.IGNORECASE)

_VISUAL_FIELD_PATTERN = re.compile(
    r"""(["'](description|caption|label|summary)["']\s*[=:]\s*["']|(?<!\w)(description|caption|label|summary)\s*=\s*["'])""",
    re.IGNORECASE,
)
_VISUAL_SOURCE_ANCHOR_PATTERN = re.compile(
    r"""\b(checksum|sha\d*|md5|hash)\b|https?://|\b(source_path|image_path|file_path|img_path)\b|(?<![a-zA-Z])/[^\s"']{3,}""",
    re.IGNORECASE,
)
_VISUAL_IMAGE_CONTEXT_PATTERN = re.compile(
    r"""\b(image|photo|img|picture|pixel|vision|screenshot|thumbnail)\b""",
    re.IGNORECASE,
)

SENSITIVE_PATH_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("soul_identity_memory", re.compile(r"[\"'/]soul/|(?<!\w)soul/", re.IGNORECASE)),
    ("env_credentials", re.compile(r"\.env\b|credentials\.(?:json|yaml|toml|env|py)", re.IGNORECASE)),
    ("mira_bridge", re.compile(r"Mira-Bridge", re.IGNORECASE)),
    ("audit_self_modification", re.compile(r"\baudit_skill\b|\bsoul_manager\b")),
    ("launchagents_plist", re.compile(r"LaunchAgents|\.plist\b", re.IGNORECASE)),
]

_REJECTION_COUNTS_N = 20
_REJECTION_RATE_THRESHOLD = 0.6

_AUDIT_REJECTION_COUNTS_PATH: Path | None = None


def _audit_rejection_counts_path() -> Path:
    global _AUDIT_REJECTION_COUNTS_PATH
    if _AUDIT_REJECTION_COUNTS_PATH is None:
        try:
            from config import SKILLS_DIR

            _AUDIT_REJECTION_COUNTS_PATH = SKILLS_DIR.parent / "audit_rejection_counts.json"
        except Exception:
            _AUDIT_REJECTION_COUNTS_PATH = Path(__file__).parent / "soul" / "audit_rejection_counts.json"
    return _AUDIT_REJECTION_COUNTS_PATH


_AUDIT_BLOCK_LIST_PATH: Path | None = None
_AUDIT_PASS_CACHE_PATH: Path | None = None


def _audit_block_list_path() -> Path:
    global _AUDIT_BLOCK_LIST_PATH
    if _AUDIT_BLOCK_LIST_PATH is None:
        try:
            from config import SKILLS_DIR

            _AUDIT_BLOCK_LIST_PATH = SKILLS_DIR.parent / "audit_block_list.json"
        except Exception:
            _AUDIT_BLOCK_LIST_PATH = Path(__file__).parent / "soul" / "audit_block_list.json"
    return _AUDIT_BLOCK_LIST_PATH


def _audit_pass_cache_path() -> Path:
    global _AUDIT_PASS_CACHE_PATH
    if _AUDIT_PASS_CACHE_PATH is None:
        try:
            from config import SKILLS_DIR

            _AUDIT_PASS_CACHE_PATH = SKILLS_DIR.parent / "audit_pass_cache.json"
        except Exception:
            _AUDIT_PASS_CACHE_PATH = Path(__file__).parent / "soul" / "audit_pass_cache.json"
    return _AUDIT_PASS_CACHE_PATH


def _read_rejection_counts() -> dict:
    path = _audit_rejection_counts_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _update_rejection_counts(source: str, rejected: bool) -> None:
    path = _audit_rejection_counts_path()
    try:
        counts = _read_rejection_counts()
        history = counts.get(source, [])
        history.append(rejected)
        if len(history) > _REJECTION_COUNTS_N:
            history = history[-_REJECTION_COUNTS_N:]
        counts[source] = history
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts, indent=2), encoding="utf-8")
    except OSError as exc:
        log.debug("audit_rejection_counts write failed: %s", exc)


def _is_strict_mode(source: str) -> bool:
    counts = _read_rejection_counts()
    history = counts.get(source, [])
    if len(history) < 3:
        return False
    rate = sum(1 for r in history if r) / len(history)
    return rate >= _REJECTION_RATE_THRESHOLD


def _check_circular_trust(skill_name: str, skill_content: str) -> bool:
    if _CIRC_IMPORT_PATTERN.search(skill_content) or _CIRC_MONKEYPATCH_PATTERN.search(skill_content):
        return True
    if _CIRC_WRITE_PATTERN.search(skill_content) and _CIRC_GT_PATH_PATTERN.search(skill_content):
        return True
    if _CIRC_FUNC_DEF_PATTERN.search(skill_content):
        return True
    return False


def _check_behavioral_patterns(skill_name: str, skill_content: str) -> list[str]:
    warnings: list[str] = []

    if _BEHAV_LOOP_CRED_PATTERN.search(skill_content):
        warnings.append("bulk_enumeration_loop")
        log.warning(
            "SKILL_AUDIT behavioral_warning: skill=%s pattern=bulk_enumeration_loop "
            "iterates over IP ranges or credential lists — review before enabling",
            skill_name,
        )

    if _BEHAV_LOOP_PATTERN.search(skill_content) and _BEHAV_BULK_CONNECT_PATTERN.search(skill_content):
        warnings.append("bulk_connection_pattern")
        log.warning(
            "SKILL_AUDIT behavioral_warning: skill=%s pattern=bulk_connection_pattern "
            "network/connection calls inside loops suggest bulk enumeration — review before enabling",
            skill_name,
        )

    if _BEHAV_CRED_HARVEST_PATTERN.search(skill_content):
        warnings.append("credential_harvesting_pattern")
        log.warning(
            "SKILL_AUDIT behavioral_warning: skill=%s pattern=credential_harvesting_pattern "
            "filesystem iteration targeting sensitive credential paths — review before enabling",
            skill_name,
        )

    if (
        _BEHAV_LOOP_PATTERN.search(skill_content)
        and _BEHAV_EXFIL_PATTERN.search(skill_content)
        and _BEHAV_FILE_READ_PATTERN.search(skill_content)
    ):
        warnings.append("bulk_exfiltration_pattern")
        log.warning(
            "SKILL_AUDIT behavioral_warning: skill=%s pattern=bulk_exfiltration_pattern "
            "outbound send/post inside loop with file read suggests bulk data exfiltration — review before enabling",
            skill_name,
        )

    return warnings


def _label_without_mechanism(skill_name: str, skill_description: str, skill_code: str) -> bool:
    _trust_text = (skill_name + " " + skill_description).lower()
    if not any(vocab in _trust_text for vocab in _TRUST_VOCAB):
        return False
    return not bool(_VERIF_MECHANISM_PATTERN.search(skill_code))


def _check_permission_overreach(skill_name: str, skill_content: str) -> list[str]:
    overreach_warnings: list[str] = []

    for match in _OVERREACH_PATH_PATTERN.finditer(skill_content):
        path = match.group(1)
        if not any(path.startswith(root) for root in _OVERREACH_SANDBOX_ROOTS):
            overreach_warnings.append("PERMISSION_OVERREACH")
            log.warning(
                "SKILL_AUDIT permission_overreach: skill=%s pattern=PERMISSION_OVERREACH "
                "path=%r points outside declared workspace",
                skill_name,
                path,
            )
            break

    if _OVERREACH_ENV_PATTERN.search(skill_content):
        overreach_warnings.append("ENV_ACCESS")
        log.warning(
            "SKILL_AUDIT permission_overreach: skill=%s pattern=ENV_ACCESS "
            "accesses environment variables which may carry API keys or secrets",
            skill_name,
        )

    if _OVERREACH_NETWORK_CALL_PATTERN.search(skill_content) and (
        _OVERREACH_HARDCODED_IP_PATTERN.search(skill_content)
        or _OVERREACH_HARDCODED_DOMAIN_PATTERN.search(skill_content)
    ):
        overreach_warnings.append("HARDCODED_NETWORK_TARGET")
        log.warning(
            "SKILL_AUDIT permission_overreach: skill=%s pattern=HARDCODED_NETWORK_TARGET "
            "network call to hardcoded IP or domain",
            skill_name,
        )

    return overreach_warnings


_SHARED_IMPORT_SCAN_RE = re.compile(
    r"from\s+\.*(shared|agents/shared)\S*\s+import|import\s+\.*(shared|soul_manager|config|sub_agent)",
)
_PRIVILEGED_SHARED_MODULES: frozenset[str] = frozenset({"soul_manager", "config", "sub_agent"})

_TRANSITIVE_LOAD_CALL_PATTERN = re.compile(
    r'(?:load_skill|import_skill)\s*\(\s*[\'"]([^\'"]+)[\'"]',
    re.IGNORECASE,
)
_TRANSITIVE_SKILL_FILE_PATTERN = re.compile(
    r'[\'"]([^\'"\s]*(?:skill[s]?/[^\'"]+\.(?:md|skill)|[\w\-]+\.skill))[\'"]',
    re.IGNORECASE,
)

_STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "re",
        "json",
        "ast",
        "io",
        "abc",
        "math",
        "time",
        "datetime",
        "pathlib",
        "logging",
        "hashlib",
        "shutil",
        "copy",
        "functools",
        "itertools",
        "collections",
        "typing",
        "types",
        "enum",
        "dataclasses",
        "contextlib",
        "threading",
        "multiprocessing",
        "queue",
        "socket",
        "ssl",
        "http",
        "urllib",
        "email",
        "html",
        "xml",
        "csv",
        "sqlite3",
        "unittest",
        "traceback",
        "inspect",
        "importlib",
        "gc",
        "weakref",
        "struct",
        "codecs",
        "base64",
        "binascii",
        "string",
        "textwrap",
        "difflib",
        "fnmatch",
        "glob",
        "stat",
        "tempfile",
        "zipfile",
        "tarfile",
        "gzip",
        "bz2",
        "lzma",
        "pickle",
        "shelve",
        "dbm",
        "subprocess",
        "signal",
        "errno",
        "ctypes",
        "platform",
        "random",
        "secrets",
        "statistics",
        "decimal",
        "fractions",
        "numbers",
        "cmath",
        "heapq",
        "bisect",
        "array",
        "pprint",
        "reprlib",
        "warnings",
        "__future__",
        "builtins",
        "operator",
        "keyword",
        "tokenize",
        "token",
        "argparse",
        "getopt",
        "getpass",
        "locale",
        "atexit",
        "asyncio",
        "concurrent",
        "select",
        "selectors",
        "uuid",
        "ipaddress",
        "mimetypes",
        "unicodedata",
        "encodings",
        "sysconfig",
        "site",
        "runpy",
        "pkgutil",
        "dis",
        "profile",
        "cProfile",
        "timeit",
        "pdb",
    }
)

_MIRA_INTERNAL_MODULES: frozenset[str] = frozenset(
    {
        "config",
        "soul_manager",
        "sub_agent",
        "mira",
        "notes_bridge",
        "prompts",
        "task_manager",
        "task_worker",
    }
)

_EXTERNAL_IMPORT_LINE_PATTERN = re.compile(
    r"^\s*(?:import\s+([\w,\s.]+)|from\s+([\w.]+)\s+import)",
    re.MULTILINE,
)


_STATIC_DANGEROUS_EXEC_PATTERN = re.compile(r"\b(?:exec|eval)\s*\(|\bos\.system\s*\(")
_STATIC_SUBPROCESS_SHELL_PATTERN = re.compile(r"\bsubprocess\b.{0,200}shell\s*=\s*True", re.DOTALL)
_STATIC_CURL_PIPE_PATTERN = re.compile(
    r"\bcurl\b.{0,200}\|\s*(?:bash|sh)\b|\bwget\b.{0,200}\|\s*(?:bash|sh)\b", re.DOTALL
)
_STATIC_KEYCHAIN_PATTERN = re.compile(r"\b(?:keychain|keyring|ssh-agent|ssh_agent)\b", re.IGNORECASE)
_STATIC_PERSISTENCE_PATTERN = re.compile(r"\b(?:launchctl|crontab)\b")
_STATIC_BASE64_DECODE_LINE_PATTERN = re.compile(r"base64\.b64decode")
_STATIC_EXEC_EVAL_LINE_PATTERN = re.compile(r"\b(?:exec|eval)\s*\(")


def _static_audit(skill_text: str) -> tuple[bool, str]:
    if _STATIC_DANGEROUS_EXEC_PATTERN.search(skill_text):
        return True, "dangerous_exec"
    if _STATIC_SUBPROCESS_SHELL_PATTERN.search(skill_text):
        return True, "subprocess_shell_true"
    if _STATIC_CURL_PIPE_PATTERN.search(skill_text):
        return True, "curl_pipe_exec"
    if _STATIC_KEYCHAIN_PATTERN.search(skill_text):
        return True, "keychain_access"
    if _STATIC_PERSISTENCE_PATTERN.search(skill_text):
        return True, "persistence_call"
    _lines = skill_text.splitlines()
    for _i, _line in enumerate(_lines):
        if _STATIC_BASE64_DECODE_LINE_PATTERN.search(_line):
            _window = "\n".join(_lines[_i : _i + 4])
            if _STATIC_EXEC_EVAL_LINE_PATTERN.search(_window):
                return True, "base64_decode_then_exec"
    return False, ""


def _log_skill_addition(skill_name: str, skill_content: str) -> None:
    try:
        from config import SKILLS_DIR

        content_hash = hashlib.sha256(skill_content.encode()).hexdigest()
        iso_now = datetime.now(timezone.utc).isoformat()
        active_skills = sorted(p.name for p in SKILLS_DIR.glob("*.md")) if SKILLS_DIR.exists() else []

        history_path = SKILLS_DIR.parent / "skill_history.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        except (OSError, json.JSONDecodeError):
            history = []

        history.append(
            {
                "timestamp": iso_now,
                "action": "add",
                "skill": skill_name,
                "hash": content_hash,
                "active_skills": active_skills,
            }
        )
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except Exception as exc:
        log.debug("_log_skill_addition failed: %s", exc)


class SkillAuditLockedOut(Exception):
    pass


_failure_log: list[tuple[datetime, str]] = []


def audit_skill(
    skill_name: str,
    skill_content: str,
    tags: list[str] | None = None,
    source: str = "unknown",
    metadata: dict | None = None,
) -> dict:
    """Audit a skill for known attack vectors.

    Checks:
    - unauthorized_network_calls
    - dangerous_code_execution
    - obfuscated_payloads
    - privilege_escalation
    - verification_anchor_injection (WARNING only, requires manual review)

    Returns a dict with keys 'blocked' (bool), 'categories' (list[str]),
    'warnings' (list[str]), and 'overreach_warnings' (list[str]).
    """
    try:
        from config import SKILL_AUDIT_LOCKOUT_THRESHOLD, SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES

        _lockout_now = datetime.now(timezone.utc)
        _lockout_window = timedelta(minutes=SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES)
        _failure_log[:] = [(ts, sn) for ts, sn in _failure_log if ts > _lockout_now - _lockout_window]
        if len(_failure_log) >= SKILL_AUDIT_LOCKOUT_THRESHOLD:
            log.warning(
                "SKILL_AUDIT lockout: source=%s reason='%d failures in rolling %d-min window — skill import suspended'",
                source,
                len(_failure_log),
                SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES,
            )
            raise SkillAuditLockedOut(
                f"Skill audit locked out: {len(_failure_log)} failures in last {SKILL_AUDIT_LOCKOUT_WINDOW_MINUTES} minutes"
            )
    except SkillAuditLockedOut:
        raise
    except Exception:
        pass

    _evasion_terms = ["soul_manager", "audit_skill", "_content_looks_like_error", "preflight_check"]
    if any(term in (skill_name + "\n" + skill_content) for term in _evasion_terms) or re.search(
        r"""['"](?:subprocess|os\.system)['"]""", skill_content
    ):
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='self-referential: skill references auditor internals — possible evasion probe'",
            skill_name,
        )
        return {
            "blocked": True,
            "categories": ["self_referential_evasion_probe"],
            "warnings": [],
            "overreach_warnings": [],
            "trust_velocity_warning": False,
        }

    _static_blocked, _static_pattern = _static_audit(skill_content)
    if _static_blocked:
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='static_audit: matched high-confidence threat pattern=%s'",
            skill_name,
            _static_pattern,
        )
        return {
            "blocked": True,
            "categories": [f"static_audit:{_static_pattern}"],
            "warnings": [],
            "overreach_warnings": [],
            "trust_velocity_warning": False,
        }

    _skill_id = skill_name.lower().replace(" ", "-")
    try:
        _bl_path = _audit_block_list_path()
        if _bl_path.exists():
            _bl_data = json.loads(_bl_path.read_text(encoding="utf-8"))
            if _skill_id in _bl_data:
                log.warning(
                    "SKILL_AUDIT short_circuit_blocked: skill=%s reason='found in shared audit_block_list'",
                    skill_name,
                )
                return {
                    "blocked": True,
                    "categories": _bl_data[_skill_id].get("categories", ["previously_blocked"]),
                    "warnings": [],
                    "overreach_warnings": [],
                    "trust_velocity_warning": False,
                }
    except (OSError, json.JSONDecodeError):
        pass

    try:
        from config import SKILL_AUDIT_PATTERN_REVIEWED_DATE, SKILL_AUDIT_STALENESS_DAYS

        _today = datetime.now(timezone.utc).date()
        _reviewed = datetime.fromisoformat(SKILL_AUDIT_PATTERN_REVIEWED_DATE).date()
        days_since_review = (_today - _reviewed).days
        if days_since_review > SKILL_AUDIT_STALENESS_DAYS and source not in TRUSTED_INTERNAL_SOURCES:
            log.warning(
                "WARNING: Skill audit patterns are %dd old — external skill ingestion may outpace defense coverage. Review soul_manager.py audit patterns.",
                days_since_review,
            )
    except Exception:
        pass

    age_days = -1
    try:
        _patterns_mtime = datetime.fromtimestamp(Path(__file__).stat().st_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - _patterns_mtime).days
        if age_days > 7:
            log.warning(
                "[audit] Skill audit patterns are %d days old (last updated: %s). New attack vectors may not be covered.",
                age_days,
                _patterns_mtime.strftime("%Y-%m-%d"),
            )
    except OSError:
        pass

    strict_mode = _is_strict_mode(source)
    if strict_mode:
        log.warning(
            "SKILL_AUDIT strict_mode_escalated: source=%s reason='rejection rate >= %.0f%% in last %d skills'",
            source,
            _REJECTION_RATE_THRESHOLD * 100,
            _REJECTION_COUNTS_N,
        )

    blocked_categories: list[str] = []
    warning_categories: list[str] = []

    _combined = (skill_name + "\n" + skill_content).lower()
    if any(p in _combined for p in _SELF_REF_AUDIT_PATTERNS):
        blocked_categories.append("self_referential_audit_reference")
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='self-referential audit reference'",
            skill_name,
        )
        _update_rejection_counts(source, True)
        return {
            "blocked": True,
            "categories": blocked_categories,
            "warnings": warning_categories,
            "overreach_warnings": [],
            "trust_velocity_warning": False,
        }

    if re.search(r"\b(audit_skill|soul_manager|_content_looks_like_error)\b", skill_content):
        blocked_categories.append("audit_infrastructure_reference")
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='skill references audit infrastructure — verification loop risk'",
            skill_name,
        )
        _update_rejection_counts(source, True)
        return {
            "blocked": True,
            "categories": blocked_categories,
            "warnings": warning_categories,
            "overreach_warnings": [],
            "trust_velocity_warning": False,
        }

    _TRUSTED_INFRA_TARGETS = ["soul_manager", "audit_skill", "core.py", "task_manager", "notes_bridge"]
    if any(target in skill_content for target in _TRUSTED_INFRA_TARGETS):
        blocked_categories.append("skill_targets_trusted_infrastructure")
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='skill targets trusted infrastructure'",
            skill_name,
        )

    has_network = bool((_STRICT_NETWORK_PATTERN if strict_mode else _NETWORK_PATTERN).search(skill_content))

    has_dangerous_exec = bool(re.search(r"\b(eval|exec|__import__|compile)\s*\(", skill_content))

    has_obfuscation = bool((_STRICT_OBFUSCATION_PATTERN if strict_mode else _OBFUSCATION_PATTERN).search(skill_content))

    has_privilege_escalation = bool(
        re.search(r"\b(sudo|chmod|chown|setuid|setgid|os\.chmod|shutil\.chown)\b", skill_content)
    )

    if has_network:
        blocked_categories.append("unauthorized_network_calls")
    if has_dangerous_exec:
        blocked_categories.append("dangerous_code_execution")
    if has_obfuscation:
        blocked_categories.append("obfuscated_payloads")
    if has_privilege_escalation:
        blocked_categories.append("privilege_escalation")

    trust_value_score = 0
    _tvs_reasons: list[str] = []

    _deploy_ctx = (metadata or {}).get("deploy_context", "") or (metadata or {}).get("file_path", "")
    if "shared/" in _deploy_ctx or "super/" in _deploy_ctx:
        trust_value_score += 2
        _tvs_reasons.append("deploy_context=shared/super(+2)")

    if has_network:
        trust_value_score += 2
        _tvs_reasons.append("network_access(+2)")

    _has_file_write = bool(_CIRC_WRITE_PATTERN.search(skill_content))
    if _has_file_write and "soul/" not in skill_content:
        trust_value_score += 1
        _tvs_reasons.append("file_write_outside_soul(+1)")

    _has_subprocess_exec = bool(re.search(r"\b(subprocess\.|os\.system|Popen)\b", skill_content)) or has_dangerous_exec
    if _has_subprocess_exec:
        trust_value_score += 1
        _tvs_reasons.append("subprocess_exec(+1)")

    requires_secondary_review = False
    if trust_value_score >= 4:
        warning_categories.append("HIGH_VALUE_TARGET")
        requires_secondary_review = True
        log.warning(
            "SKILL_AUDIT HIGH_VALUE_TARGET: skill=%s trust_value_score=%d breakdown=%s "
            "— high-value compromise target; secondary adversarial review required",
            skill_name,
            trust_value_score,
            _tvs_reasons,
        )
        # NOTE: the secondary adversarial-review hook used to dispatch into a
        # `sub_agent` module that no longer exists in the repo. The previous
        # code wrapped that import in try/except so the failure was silent —
        # but the static import checker (preflight) catches it and refuses to
        # run the agent at all (SIGHUP loop). Until a real secondary-review
        # path is wired up, just record the warning category and rely on the
        # primary audit findings; the skill is still subject to all other
        # checks. TODO: route through the registry's `evaluator` agent or a
        # dedicated adversarial-review worker.

    _pi_full_text = skill_name + "\n" + (metadata or {}).get("description", "") + "\n" + skill_content
    prompt_injection_patterns = [
        re.compile(r"ignore (all |previous |prior )?instructions", re.IGNORECASE),
        re.compile(r"you are now", re.IGNORECASE),
        re.compile(r"SYSTEM:", re.IGNORECASE),
        re.compile(r"\[INST\]"),
        re.compile(r"<\|system\|>"),
        re.compile(r"disregard (your |all |previous )?", re.IGNORECASE),
        re.compile(r"pretend (you are|to be)", re.IGNORECASE),
        re.compile(r"override (your |all |previous )?", re.IGNORECASE),
    ]
    for _pip in prompt_injection_patterns:
        if _pip.search(_pi_full_text):
            blocked_categories.append("prompt_injection")
            log.warning(
                "SKILL_AUDIT blocked: skill=%s category=prompt_injection pattern=%r",
                skill_name,
                _pip.pattern,
            )
            break

    if _COMPOUND_SENSITIVE_PATH_PATTERN.search(skill_content) and _COMPOUND_NETWORK_CALL_PATTERN.search(skill_content):
        blocked_categories.append("compound_exfiltration_risk")
        log.warning(
            "SKILL_AUDIT compound_exfiltration_risk: skill=%s "
            "reason='skill reads sensitive paths AND makes network calls — possible exfiltration chain'",
            skill_name,
        )

    if _VERIF_PATTERN.search(skill_content) and (
        has_network or bool(re.search(r"\b(subprocess\.|os\.system|Popen)\b", skill_content))
    ):
        warning_categories.append("verification_anchor_injection")
        log.warning(
            "SKILL_AUDIT verification_anchor_injection: skill=%s defines verification/validation "
            "logic that also controls a network or subprocess call — manual review required",
            skill_name,
        )

    _indirect_matches = _INDIRECT_EXEC_PATTERNS.findall(skill_content)
    if _indirect_matches:
        warning_categories.append("indirect_execution_vectors")
        log.warning(
            "SKILL_AUDIT indirect_execution_vectors: skill=%s patterns=%s "
            "— indirect execution pathway detected, human review required",
            skill_name,
            sorted(set(_indirect_matches)),
        )

    warning_categories.extend(_check_behavioral_patterns(skill_name, skill_content))

    _matched_covert_domains = [svc for svc in COVERT_CHANNEL_SERVICES if svc in skill_content]
    if _matched_covert_domains:
        _has_soul_logs_read = bool(_SOUL_LOGS_PATH_PATTERN.search(skill_content)) and bool(
            _BEHAV_FILE_READ_PATTERN.search(skill_content)
        )
        for _domain in _matched_covert_domains:
            _covert_msg = f"COVERT_CHANNEL_RISK: calls trusted relay service {_domain} — verify data flow direction and payload contents"
            if _has_soul_logs_read:
                blocked_categories.append(_covert_msg)
                log.warning(
                    "SKILL_AUDIT blocked: skill=%s reason='%s with soul/logs file read'",
                    skill_name,
                    _covert_msg,
                )
            else:
                warning_categories.append(_covert_msg)
                log.warning(
                    "SKILL_AUDIT COVERT_CHANNEL_RISK: skill=%s domain=%s — verify data flow direction and payload contents",
                    skill_name,
                    _domain,
                )

    for _kap_category, _kap_patterns in KNOWN_ATTACK_PATTERNS.items():
        for _kap_pattern in _kap_patterns:
            if _kap_pattern.search(skill_content):
                warning_categories.append(_kap_category)
                log.warning(
                    "SKILL_AUDIT known_attack_pattern: skill=%s category=%s",
                    skill_name,
                    _kap_category,
                )
                break

    if _label_without_mechanism(skill_name, (metadata or {}).get("description", ""), skill_content):
        warning_categories.append("trust_label_without_mechanism")
        log.warning(
            "SKILL_AUDIT trust_label_without_mechanism: skill=%s "
            "reason='Trust vocabulary without verification mechanism — inspect manually'",
            skill_name,
        )

    if (
        _VISUAL_FIELD_PATTERN.search(skill_content)
        and _VISUAL_IMAGE_CONTEXT_PATTERN.search(skill_content)
        and not _VISUAL_SOURCE_ANCHOR_PATTERN.search(skill_content)
    ):
        warning_categories.append("WARN_VISUAL_INJECTION")
        log.warning(
            "SKILL_AUDIT WARN_VISUAL_INJECTION: skill=%s "
            "reason='skill sets description/caption/label/summary on image input without checksum, URL, or source path — silent injection risk'",
            skill_name,
        )

    _behav_mod_text = (skill_content + " " + ((metadata or {}).get("description", ""))).lower()
    if any(kw in _behav_mod_text for kw in BEHAVIOR_MODIFICATION_VOCABULARY):
        mechanism = (metadata or {}).get("mechanism", "")
        if not mechanism:
            warning_categories.append("undeclared_behavior_modification")
            log.warning(
                "SKILL_AUDIT undeclared_behavior_modification: skill=%s "
                "reason='Skill claims behavior modification but declares no mechanism — "
                "cannot distinguish alignment from structural damage (see soul audit rule SR-001).'",
                skill_name,
            )

    _self_mod_text = (skill_name + " " + ((metadata or {}).get("description", "")) + " " + skill_content).lower()
    if any(kw in _self_mod_text for kw in _SELF_MOD_KEYWORDS):
        warning_categories.append("behavioral_self_modification_claim")
        log.warning(
            "SKILL_AUDIT behavioral_self_modification_claim: skill=%s "
            "reason='Skill claims behavioral self-modification — indistinguishable from structural damage per audit rule. Manual review required.'",
            skill_name,
        )

    if _check_circular_trust(skill_name, skill_content):
        blocked_categories.append("circular_trust")
        log.warning(
            "SKILL_AUDIT circular_trust: skill=%s reason='circular_trust: skill can influence its own verification path'",
            skill_name,
        )

    if tags and (set(tags) & ORCHESTRATOR_SCOPE_TAGS):
        blocked_categories.append("topology_escalation")
        log.warning(
            "SKILL_AUDIT topology_escalation: skill=%s source=%s claimed_tags=%s "
            "reason='topology_escalation: skill claims orchestrator-tier scope from untrusted source'",
            skill_name,
            source,
            sorted(set(tags) & ORCHESTRATOR_SCOPE_TAGS),
        )

    overreach_warnings = _check_permission_overreach(skill_name, skill_content)
    if overreach_warnings and not _PERMISSIONS_ANNOTATION_PATTERN.search(skill_content):
        blocked_categories.extend(overreach_warnings)
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason='permission overreach without PERMISSIONS annotation' overreach=%s",
            skill_name,
            overreach_warnings,
        )

    for _pi_pattern in PROMPT_INJECTION_SIGNATURES:
        _pi_match = _pi_pattern.search(skill_content)
        if _pi_match:
            _snippet = skill_content[max(0, _pi_match.start() - 20) : _pi_match.end() + 20].strip()
            blocked_categories.append("prompt_injection")
            log.warning(
                "SKILL_AUDIT blocked: skill=%s category=prompt_injection snippet=%r",
                skill_name,
                _snippet,
            )
            break

    for _si_line in skill_content.splitlines():
        if not _SHARED_IMPORT_SCAN_RE.search(_si_line):
            continue
        _from_m = re.match(r"\s*from\s+([\w./]+)\s+import", _si_line)
        if _from_m:
            _mod_name = _from_m.group(1).replace("/", ".").strip(".").rsplit(".", 1)[-1]
        else:
            _import_m = re.match(r"\s*import\s+([\w./]+)", _si_line)
            _mod_name = _import_m.group(1).replace("/", ".").strip(".").rsplit(".", 1)[-1] if _import_m else None
        if not _mod_name:
            continue
        if _mod_name in _PRIVILEGED_SHARED_MODULES:
            blocked_categories.append(f"skill attempts to import privileged shared module: {_mod_name}")
            log.warning(
                "SKILL_AUDIT blocked: skill=%s reason='skill attempts to import privileged shared module: %s'",
                skill_name,
                _mod_name,
            )
        else:
            _shared_file = Path(__file__).parent.parent / "agents" / "shared" / f"{_mod_name}.py"
            if _shared_file.exists():
                try:
                    _shared_text = _shared_file.read_text(encoding="utf-8")
                    if _NETWORK_PATTERN.search(_shared_text):
                        blocked_categories.append(f"shared_module_network_call: {_shared_file.name}")
                    if re.search(r"\b(eval|exec|__import__|compile)\s*\(", _shared_text):
                        blocked_categories.append(f"shared_module_dangerous_exec: {_shared_file.name}")
                    if _OBFUSCATION_PATTERN.search(_shared_text):
                        blocked_categories.append(f"shared_module_obfuscated_payload: {_shared_file.name}")
                    if re.search(r"\b(sudo|chmod|chown|setuid|setgid|os\.chmod|shutil\.chown)\b", _shared_text):
                        blocked_categories.append(f"shared_module_privilege_escalation: {_shared_file.name}")
                except OSError as _exc:
                    log.debug(
                        "skill_audit shared_module_read_failed: skill=%s path=%s exc=%s",
                        skill_name,
                        _shared_file,
                        _exc,
                    )

    _sensitive_matches: list[str] = []
    for _sp_label, _sp_pattern in SENSITIVE_PATH_PATTERNS:
        if _sp_pattern.search(skill_content):
            _sensitive_matches.append(_sp_label)

    if _sensitive_matches:
        _has_malicious = len(blocked_categories) > 0
        for _sp_label in _sensitive_matches:
            if _has_malicious:
                blocked_categories.append(f"sensitive_path_with_malicious_pattern: {_sp_label}")
                log.warning(
                    "SKILL_AUDIT blocked: skill=%s reason='sensitive_path_access combined with malicious pattern' path=%s",
                    skill_name,
                    _sp_label,
                )
            else:
                warning_categories.append(f"sensitive_path_access: {_sp_label}")
                log.warning(
                    "SKILL_AUDIT sensitive_path_access: skill=%s path=%s — review before enabling",
                    skill_name,
                    _sp_label,
                )

    _branch_count = 0
    try:
        from config import SKILL_AUDIT_BRANCH_THRESHOLD

        _match_node_types = (ast.If, ast.IfExp) + ((ast.Match,) if hasattr(ast, "Match") else ())
        _ast_tree = ast.parse(skill_content)
        for _ast_node in ast.walk(_ast_tree):
            if isinstance(_ast_node, _match_node_types):
                _branch_count += 1
        if _branch_count > SKILL_AUDIT_BRANCH_THRESHOLD:
            warning_categories.append("HIGH_CONDITIONAL_COMPLEXITY")
            log.warning(
                "SKILL_AUDIT HIGH_CONDITIONAL_COMPLEXITY: skill=%s branch_count=%d threshold=%d",
                skill_name,
                _branch_count,
                SKILL_AUDIT_BRANCH_THRESHOLD,
            )
    except SyntaxError:
        pass
    except Exception:
        pass

    requires_manual_review = False
    _ci_text = (skill_name + " " + (metadata or {}).get("description", "") + " " + skill_content).lower()
    _ci_detection_matches = [t for t in DETECTION_VOCAB if t in _ci_text]
    _ci_sensitive_matches = [t for t in SENSITIVE_VOCAB if t in _ci_text]
    if _ci_detection_matches and _ci_sensitive_matches:
        warning_categories.append("capability_inversion_risk")
        requires_manual_review = True
        log.warning(
            "SKILL_AUDIT capability_inversion_risk: skill=%s detection_terms=%s sensitive_terms=%s "
            "— skill encodes detection/classification of sensitive content; manual review required",
            skill_name,
            sorted(_ci_detection_matches),
            sorted(_ci_sensitive_matches),
        )

    _transitive_warnings: list[str] = []

    _skill_refs: set[str] = set()
    for _m in _TRANSITIVE_LOAD_CALL_PATTERN.finditer(skill_content):
        _skill_refs.add(_m.group(1).strip())
    for _m in _TRANSITIVE_SKILL_FILE_PATTERN.finditer(skill_content):
        _stem = Path(_m.group(1)).stem
        _skill_refs.add(_stem)

    if _skill_refs:
        _audit_registry: dict = {}
        try:
            from config import SKILLS_DIR as _SKILLS_DIR_TV

            _hashes_path_tv = _SKILLS_DIR_TV.parent / "audit_hashes.json"
            try:
                _audit_registry = (
                    json.loads(_hashes_path_tv.read_text(encoding="utf-8")) if _hashes_path_tv.exists() else {}
                )
            except (OSError, json.JSONDecodeError):
                _audit_registry = {}
        except Exception:
            pass
        for _ref_name in sorted(_skill_refs):
            _ref_slug = _ref_name.lower().replace(" ", "-")
            if _ref_slug not in _audit_registry:
                _transitive_warnings.append(f"unaudited_transitive_dependency: {_ref_name}")
                log.warning(
                    "SKILL_AUDIT unaudited_transitive_dependency: skill=%s references=%s "
                    "— referenced skill has no audit record; full trust chain unverified",
                    skill_name,
                    _ref_name,
                )

    _seen_external_imports: set[str] = set()
    for _imp_match in _EXTERNAL_IMPORT_LINE_PATTERN.finditer(skill_content):
        _import_names_str = _imp_match.group(1)
        _from_module = _imp_match.group(2)
        if _import_names_str:
            _mods = [m.strip().split(".")[0] for m in _import_names_str.split(",")]
        elif _from_module:
            _mods = [_from_module.split(".")[0]]
        else:
            continue
        for _mod in _mods:
            _mod = _mod.strip()
            if not _mod or _mod in _STDLIB_MODULES or _mod in _MIRA_INTERNAL_MODULES:
                continue
            if _mod in _seen_external_imports:
                continue
            _seen_external_imports.add(_mod)
            _transitive_warnings.append(f"unverified_external_import: {_mod}")
            log.warning(
                "SKILL_AUDIT unverified_external_import: skill=%s module=%s "
                "— non-stdlib non-Mira-internal import; verify trust chain before enabling",
                skill_name,
                _mod,
            )

    warning_categories.extend(_transitive_warnings)

    _meta = metadata or {}
    _rationale = _meta.get("rationale", "")
    _accountability = "NORMAL"
    if not _rationale or len(_rationale.strip()) <= 50:
        _accountability = "LOW"
        blocked_categories.append("MISSING_EPISTEMIC_PROVENANCE")
        log.warning(
            "SKILL_AUDIT blocked: skill=%s reason=MISSING_EPISTEMIC_PROVENANCE field=rationale"
            " — rationale key missing or too short (must be >50 chars)",
            skill_name,
        )

    if source in ("agent_generated", "external"):
        _provenance = _meta.get("provenance", "")
        if not _provenance:
            blocked_categories.append("MISSING_EPISTEMIC_PROVENANCE")
            log.warning(
                "SKILL_AUDIT blocked: skill=%s reason=MISSING_EPISTEMIC_PROVENANCE field=provenance"
                " — source=%s requires provenance key naming originating source or task ID",
                skill_name,
                source,
            )

    blocked = len(blocked_categories) > 0

    HIGH_TRUST_VOCAB = ["security", "audit", "verify", "trust", "credential", "auth", "cert", "checksum", "integrity"]
    _hvt_text = (skill_name + " " + (metadata or {}).get("description", "")).lower()
    high_value_target = any(vocab in _hvt_text for vocab in HIGH_TRUST_VOCAB)
    if high_value_target:
        requires_manual_review = True
        log.warning(
            "SKILL_AUDIT high_value_target: skill=%s reason='Skill occupies high-trust position — manual review required before enabling.'",
            skill_name,
        )

    log.info(
        "SKILL_AUDIT result: skill=%s source=%s blocked=%s categories=%s warnings=%s",
        skill_name,
        source,
        blocked,
        blocked_categories,
        warning_categories,
    )

    if blocked:
        _failure_log.append((datetime.now(timezone.utc), skill_name))
        log.warning(
            "SKILL_AUDIT blocked: skill=%s categories=%s",
            skill_name,
            blocked_categories,
        )
        try:
            from config import LOGS_DIR

            _sf_path = LOGS_DIR / "security_flags.jsonl"
            _sf_path.parent.mkdir(parents=True, exist_ok=True)
            _sf_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent_id": (metadata or {}).get("agent_id", "unknown"),
                "skill_name": skill_name,
                "block_reason": blocked_categories,
                "source_url_if_known": (metadata or {}).get("source_url"),
            }
            with open(_sf_path, "a", encoding="utf-8") as _sf:
                _sf.write(json.dumps(_sf_entry) + "\n")
        except OSError as _sfe:
            log.debug("security_flags write failed: %s", _sfe)

    _update_rejection_counts(source, blocked)

    if blocked:
        try:
            _bl_path = _audit_block_list_path()
            _bl_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _bl_data = json.loads(_bl_path.read_text(encoding="utf-8")) if _bl_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                _bl_data = {}
            _bl_data[_skill_id] = {
                "reason": blocked_categories[0] if blocked_categories else "blocked",
                "categories": blocked_categories,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            _bl_path.write_text(json.dumps(_bl_data, indent=2), encoding="utf-8")
        except OSError as _exc:
            log.debug("audit_block_list write failed: %s", _exc)

    _SPEC_COVERAGE_NOTE = (
        "Audit covers known-bad static patterns only. "
        "Novel or obfuscated attack vectors outside this spec are undetected."
    )

    if not blocked:
        log.info(
            "SKILL_AUDIT spec_coverage: skill=%s source=%s audit_coverage='known-bad patterns only; novel attack vectors undetected'",
            skill_name,
            source,
        )
        _log_skill_addition(skill_name, skill_content)
        try:
            _pc_path = _audit_pass_cache_path()
            _pc_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _pc_data = json.loads(_pc_path.read_text(encoding="utf-8")) if _pc_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                _pc_data = {}
            _pc_data[_skill_id] = {"timestamp": datetime.now(timezone.utc).isoformat()}
            _pc_path.write_text(json.dumps(_pc_data, indent=2), encoding="utf-8")
        except OSError as _exc:
            log.debug("audit_pass_cache write failed: %s", _exc)
        try:
            from config import SKILLS_DIR

            _slug = skill_name.lower().replace(" ", "-")
            _content_hash = hashlib.sha256(skill_content.encode()).hexdigest()
            _hashes_path = SKILLS_DIR.parent / "audit_hashes.json"
            _hashes_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                _hashes = json.loads(_hashes_path.read_text(encoding="utf-8")) if _hashes_path.exists() else {}
            except (json.JSONDecodeError, OSError):
                _hashes = {}
            _hashes[_slug] = {
                "hash": _content_hash,
                "last_verified_at": datetime.now(timezone.utc).isoformat(),
            }
            _hashes_path.write_text(json.dumps(_hashes, indent=2), encoding="utf-8")
            _sidecar_dir = SKILLS_DIR / ".hashes"
            _sidecar_dir.mkdir(parents=True, exist_ok=True)
            (_sidecar_dir / f"{_slug}.sha256").write_text(_content_hash, encoding="utf-8")
        except Exception as _exc:
            log.debug("audit_skill hash persist failed: %s", _exc)

    trust_velocity_warning = False
    try:
        from config import SKILL_MIN_AGE_HOURS, SKILLS_DIR

        _tv_path: str | None = (metadata or {}).get("file_path")
        if not _tv_path:
            _slug = skill_name.lower().replace(" ", "-")
            for _ext in (".md", ".py"):
                _candidate = SKILLS_DIR / f"{_slug}{_ext}"
                if _candidate.exists():
                    _tv_path = str(_candidate)
                    break
        if _tv_path and os.path.exists(_tv_path):
            _age_hours = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(_tv_path)) / 3600
            if _age_hours < SKILL_MIN_AGE_HOURS:
                trust_velocity_warning = True
                warning_categories.append("WARN_TRUST_VELOCITY")
                log.warning(
                    "SKILL_AUDIT WARN_TRUST_VELOCITY: skill=%s age_hours=%.1f threshold_hours=%d "
                    "— skill file is newer than trust velocity threshold",
                    skill_name,
                    _age_hours,
                    SKILL_MIN_AGE_HOURS,
                )
    except Exception:
        pass

    _cap_network: list[str] = []
    _cap_files: list[str] = []
    _cap_env: list[str] = []
    _cap_triggers: list[str] = []

    for _m in re.finditer(r"""['"]https?://[^\s'"\\]{3,}['"]""", skill_content):
        _u = _m.group(0).strip("'\"")
        if _u not in _cap_network:
            _cap_network.append(_u)
    for _m in re.finditer(r"""['"]([a-zA-Z0-9][a-zA-Z0-9\-]{1,63}(?:\.[a-zA-Z0-9]{2,}){1,3})['"]""", skill_content):
        _h = _m.group(1)
        if not _h.startswith("http") and "." in _h and _h not in _cap_network:
            _cap_network.append(_h)

    for _m in re.finditer(r"""(?:open|Path)\s*\(\s*f?['"]([^'"]+)['"]""", skill_content):
        _f = _m.group(1)
        if _f not in _cap_files:
            _cap_files.append(_f)

    for _m in re.finditer(
        r"""os\.environ(?:\.get)?\s*\(\s*f?['"]([^'"]+)['"]|os\.environ\[f?['"]([^'"]+)['"]\]|os\.getenv\s*\(\s*f?['"]([^'"]+)['"]""",
        skill_content,
    ):
        _v = _m.group(1) or _m.group(2) or _m.group(3)
        if _v and _v not in _cap_env:
            _cap_env.append(_v)

    try:
        _cm_ast = ast.parse(skill_content)
        for _cm_node in ast.walk(_cm_ast):
            if (
                isinstance(_cm_node, ast.Expr)
                and isinstance(_cm_node.value, ast.Constant)
                and isinstance(_cm_node.value.value, str)
            ):
                for _cm_line in _cm_node.value.value.splitlines():
                    _cm_line = _cm_line.strip()
                    if _cm_line and any(
                        kw in _cm_line.lower()
                        for kw in ("trigger", "invoke", "when ", "called when", "use when", "activat")
                    ):
                        if _cm_line not in _cap_triggers:
                            _cap_triggers.append(_cm_line)
            elif isinstance(_cm_node, ast.If):
                for _cm_child in ast.walk(_cm_node.test):
                    if (
                        isinstance(_cm_child, ast.Constant)
                        and isinstance(_cm_child.value, str)
                        and len(_cm_child.value) > 3
                    ):
                        if _cm_child.value not in _cap_triggers:
                            _cap_triggers.append(_cm_child.value)
    except SyntaxError:
        pass
    except Exception:
        pass

    capability_manifest = {
        "skill": skill_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "network_surface": _cap_network,
        "file_surface": _cap_files,
        "env_surface": _cap_env,
        "trigger_conditions": _cap_triggers,
    }

    try:
        from config import LOGS_DIR as _cm_logs_dir

        _cm_path = _cm_logs_dir / f"skill_manifest_{_skill_id}.json"
        _cm_path.parent.mkdir(parents=True, exist_ok=True)
        _cm_path.write_text(json.dumps(capability_manifest, indent=2), encoding="utf-8")
    except Exception as _cm_exc:
        log.debug("skill_manifest write failed: %s", _cm_exc)

    try:
        from config import SKILLS_DIR as _cm_skills_dir

        _cm_hashes_path = _cm_skills_dir.parent / "audit_hashes.json"
        if _cm_hashes_path.exists():
            _cm_hashes = json.loads(_cm_hashes_path.read_text(encoding="utf-8"))
            if _skill_id in _cm_hashes and isinstance(_cm_hashes[_skill_id], dict):
                _cm_hashes[_skill_id]["capability_manifest"] = capability_manifest
                _cm_hashes_path.write_text(json.dumps(_cm_hashes, indent=2), encoding="utf-8")
    except Exception as _cm_exc:
        log.debug("skill_manifest registry update failed: %s", _cm_exc)

    return {
        "blocked": blocked,
        "categories": blocked_categories,
        "warnings": warning_categories,
        "overreach_warnings": overreach_warnings,
        "trust_velocity_warning": trust_velocity_warning,
        "requires_manual_review": requires_manual_review,
        "requires_secondary_review": requires_secondary_review,
        "trust_value_score": trust_value_score,
        "high_value_target": high_value_target,
        "patterns_age_days": age_days,
        "conditional_branch_count": _branch_count,
        "accountability": _accountability,
        "spec_coverage_note": _SPEC_COVERAGE_NOTE if not blocked else None,
        "capability_manifest": capability_manifest,
    }


def save_skill(
    skill_name: str,
    content: str,
    source: str = "unknown",
    metadata: dict | None = None,
) -> bool:
    """Write a skill file, blocking the write if content changed and re-audit fails.

    Returns True on success, False if blocked or write failed.
    On first write (no stored hash) the file is written without re-audit — the
    caller is expected to have run audit_skill() beforehand.  On any subsequent
    write where the content hash differs from the stored audit-time hash,
    audit_skill() is re-run; a failing audit blocks the write entirely.
    """
    try:
        from config import SKILLS_DIR
    except Exception as _exc:
        log.debug("save_skill: config import failed: %s", _exc)
        return False

    slug = skill_name.lower().replace(" ", "-")
    skill_file = SKILLS_DIR / f"{slug}.md"
    new_hash = hashlib.sha256(content.encode()).hexdigest()

    hashes_path = SKILLS_DIR.parent / "audit_hashes.json"
    try:
        _hashes_raw = json.loads(hashes_path.read_text(encoding="utf-8")) if hashes_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        _hashes_raw = {}

    _stored_entry = _hashes_raw.get(slug)
    _stored_hash = _stored_entry.get("hash") if isinstance(_stored_entry, dict) else None

    if _stored_hash is not None and new_hash != _stored_hash:
        _result = audit_skill(skill_name, content, source=source, metadata=metadata)
        if _result["blocked"]:
            log.warning(
                "skill update blocked: skill=%s content changed and failed re-audit categories=%s",
                skill_name,
                _result["categories"],
            )
            return False

    try:
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")
    except OSError as _exc:
        log.debug("save_skill: write failed skill=%s: %s", skill_name, _exc)
        return False

    return True


def load_skill(skill_name: str) -> str:
    """Load skill content, verifying file hash against audit-time sidecar.

    Blocks and re-audits if the file changed since the last passing audit.
    Forces an audit if no sidecar exists (legacy or new skills).
    Returns empty string if blocked or not found.
    """
    try:
        from config import SKILLS_DIR
    except Exception as _exc:
        log.debug("load_skill: config import failed: %s", _exc)
        return ""

    slug = skill_name.lower().replace(" ", "-")
    skill_file: Path | None = None
    for _ext in (".md", ".py"):
        _candidate = SKILLS_DIR / f"{slug}{_ext}"
        if _candidate.exists():
            skill_file = _candidate
            break
    if skill_file is None:
        return ""

    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError as _exc:
        log.debug("load_skill: read failed skill=%s: %s", skill_name, _exc)
        return ""

    current_hash = hashlib.sha256(content.encode()).hexdigest()
    sidecar = SKILLS_DIR / ".hashes" / f"{slug}.sha256"

    if not sidecar.exists():
        log.warning(
            "SKILL_LOAD unaudited: skill=%s no stored hash — forcing audit before use",
            skill_name,
        )
        _result = audit_skill(skill_name, content)
        if _result["blocked"]:
            log.warning("SKILL_LOAD blocked: skill=%s failed initial audit", skill_name)
            return ""
        return content

    stored_hash = sidecar.read_text(encoding="utf-8").strip()
    if current_hash != stored_hash:
        log.warning(
            "SKILL_LOAD hash_mismatch: skill=%s file changed since last audit — re-auditing",
            skill_name,
        )
        _result = audit_skill(skill_name, content)
        if _result["blocked"]:
            log.warning(
                "SKILL_LOAD blocked: skill=%s re-audit failed after hash mismatch",
                skill_name,
            )
            return ""

    return content


def audit_skill_freshness() -> list[dict]:
    """Check all audited skills for trust label staleness.

    For each skill in audit_hashes.json that has a 'last_verified_at' timestamp,
    compares against SKILL_REVERIFICATION_DAYS. Skills whose trust labels have aged
    past the threshold are returned as stale entries and a warning is logged.

    Returns a list of dicts with keys: skill, last_verified_at, days_since_verification,
    days_stale, status ('stale'). Does not block skills — warning only.
    """
    try:
        from config import SKILLS_DIR, SKILL_REVERIFICATION_DAYS
    except Exception as exc:
        log.debug("audit_skill_freshness: config import failed: %s", exc)
        return []

    _hashes_path: Path = SKILLS_DIR.parent / "audit_hashes.json"
    try:
        _hashes: dict = json.loads(_hashes_path.read_text(encoding="utf-8")) if _hashes_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return []

    now = datetime.now(timezone.utc)
    stale: list[dict] = []

    for skill_slug, entry in _hashes.items():
        if not isinstance(entry, dict):
            continue
        ts_str = entry.get("last_verified_at")
        if not ts_str:
            continue
        try:
            last_verified = datetime.fromisoformat(ts_str)
            days_since = (now - last_verified).days
            if days_since > SKILL_REVERIFICATION_DAYS:
                stale_entry = {
                    "skill": skill_slug,
                    "last_verified_at": ts_str,
                    "days_since_verification": days_since,
                    "days_stale": days_since - SKILL_REVERIFICATION_DAYS,
                    "status": "stale",
                }
                stale.append(stale_entry)
                log.warning(
                    "SKILL_AUDIT stale_label: skill=%s last_verified_at=%s "
                    "days_since_verification=%d threshold=%d status=stale "
                    "— trust labels need re-verification",
                    skill_slug,
                    ts_str,
                    days_since,
                    SKILL_REVERIFICATION_DAYS,
                )
        except (ValueError, TypeError) as exc:
            log.debug("audit_skill_freshness: bad timestamp skill=%s exc=%s", skill_slug, exc)

    return stale


def revert_skills_to(timestamp: str) -> None:
    """Restore SKILLS_DIR to the state captured at the checkpoint at-or-before timestamp.

    Reads skill_history.json, finds the last entry whose timestamp <= the given value,
    then deletes any skill files present now that were not in the recorded active_skills snapshot.
    """
    try:
        from config import SKILLS_DIR
    except Exception as exc:
        log.debug("revert_skills_to: config import failed: %s", exc)
        return

    history_path = SKILLS_DIR.parent / "skill_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    except (OSError, json.JSONDecodeError):
        log.warning("revert_skills_to: could not read skill_history.json")
        return

    checkpoint = None
    for entry in history:
        if entry.get("timestamp", "") <= timestamp:
            checkpoint = entry

    if checkpoint is None:
        log.warning("revert_skills_to: no checkpoint found at or before %s", timestamp)
        return

    snapshot_skills: set[str] = set(checkpoint.get("active_skills", []))
    current_skills: set[str] = {p.name for p in SKILLS_DIR.glob("*.md")} if SKILLS_DIR.exists() else set()
    excess = current_skills - snapshot_skills

    for filename in sorted(excess):
        skill_path = SKILLS_DIR / filename
        try:
            skill_path.unlink()
            log.info("revert_skills_to: deleted %s", filename)
        except OSError as exc:
            log.warning("revert_skills_to: failed to delete %s: %s", filename, exc)
