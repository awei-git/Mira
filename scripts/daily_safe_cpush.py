#!/usr/bin/env python3
import datetime as dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


SANDBOX_ROOT = Path("/Users/angwei/Sandbox")
APPS_ROOT = SANDBOX_ROOT / "Apps"
LOG_DIR = Path("/Users/angwei/Library/Logs/daily-safe-cpush")
DRY_RUN = "--dry-run" in sys.argv

PRIMARY_REPOS = [
    SANDBOX_ROOT / "Mira",
    SANDBOX_ROOT / "Tetra",
    SANDBOX_ROOT / "MasterMinds",
    SANDBOX_ROOT / "MiraBridge",
    SANDBOX_ROOT / "MiraApp",
    SANDBOX_ROOT / "CodexAdd",
    SANDBOX_ROOT / "CodexCC",
    SANDBOX_ROOT / "Volive",
    APPS_ROOT / "AlephZero",
]

PRIVATE_KEY_FILE_NAMES = {"id_" + "rsa", "id_" + "ed25519"}
SENSITIVE_EXTENSIONS = {
    ".pem",
    ".p12",
    ".key",
    ".mobileprovision",
    ".sqlite",
    ".db",
    ".dump",
    ".sql",
    ".epub",
    ".xlsx",
    ".docx",
    ".pptx",
    ".pages",
    ".numbers",
    ".keynote",
    ".pdf",
}
SENSITIVE_NAME_TOKENS = [
    "credential",
    "token",
    "pass" + "word",
    "passwd",
    "api_key",
    "api-key",
    "secret_key",
    "secret-key",
    "private_key",
    "private-key",
]
SENSITIVE_DIR_NAMES = {"secret", "secrets", "credentials", "tokens", "private"}

SECRET_CONTENT_RULES = [
    ("private_key_block", r"BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY"),
    ("openai_key", r"\b" + "s" + "k-" + r"(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("github_token", r"\b(?:gh" + "p_" + r"[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ("aws_access_key_id", r"\b" + "AK" + "IA" + r"[0-9A-Z]{16}\b"),
    ("slack_token", r"\bxo" + r"x[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("stripe_live_key", r"\b" + "s" + "k_live_" + r"[A-Za-z0-9]{10,}\b"),
    ("bearer_token", r"\bBearer\s+[A-Za-z0-9._-]{20,}\b"),
    ("connection_string", r"\b(?:postgres|postgresql|mysql|mongodb|redis)://[^\s'\"<>]+"),
    (
        "assigned_secret",
        r"\b(?:API[_-]?KEY|SECRET[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASS"
        + "WORD|PASSWD)"
        + r"\b\s*[:=]\s*['\"][^'\"]{8,}",
    ),
]

CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})", re.M)


def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def git(repo, *args):
    return run(["git", *args], cwd=repo)


def now():
    return dt.datetime.now().astimezone()


def discover_repos():
    ordered = []
    seen = set()

    def add(path):
        resolved = path.resolve()
        if (resolved / ".git").is_dir() and resolved not in seen:
            ordered.append(resolved)
            seen.add(resolved)

    for repo in PRIMARY_REPOS:
        add(repo)

    for root in (SANDBOX_ROOT, APPS_ROOT):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or child.name in {"node_modules", "__pycache__", ".cache"}:
                continue
            add(child)

    return ordered


def status_paths(status_lines):
    paths = []
    for line in status_lines:
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip())
    return paths


def operation_in_progress(repo):
    git_dir_proc = git(repo, "rev-parse", "--git-dir")
    if git_dir_proc.returncode != 0:
        return ["gitdir"]

    git_dir = Path(git_dir_proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir

    markers = {
        "merge": git_dir / "MERGE_HEAD",
        "rebase": git_dir / "rebase-apply",
        "rebase-merge": git_dir / "rebase-merge",
        "cherry-pick": git_dir / "CHERRY_PICK_HEAD",
        "revert": git_dir / "REVERT_HEAD",
    }
    return [name for name, path in markers.items() if path.exists()]


def unmerged(status_lines):
    return any(line[:2] in {"UU", "AA", "DD", "AU", "UA", "DU", "UD"} for line in status_lines)


def sensitive_path(path):
    normalized = path.replace(os.sep, "/")
    parts = normalized.split("/")
    basename = parts[-1].lower()
    stem = Path(basename).stem
    suffix = Path(basename).suffix.lower()

    if basename == "." + "env" or basename.startswith("." + "env" + "."):
        return "env_file"
    if basename == "." + "ds_store":
        return "local_desktop_artifact"
    if stem in PRIVATE_KEY_FILE_NAMES:
        return "ssh_private_key"
    if suffix in SENSITIVE_EXTENSIONS:
        return "sensitive_extension"
    if any(token in normalized.lower() for token in SENSITIVE_NAME_TOKENS):
        return "sensitive_name"

    for index, part in enumerate(parts[:-1]):
        if part.lower() not in SENSITIVE_DIR_NAMES:
            continue
        if index == 1 and parts[0] == "agents" and part.lower() == "secret":
            continue
        return "sensitive_directory"

    return None


def run_external_secret_scanners(repo):
    hits = []

    if shutil.which("gitleaks"):
        proc = run(
            ["gitleaks", "detect", "--source", str(repo), "--redact", "--no-banner", "--exit-code", "1"], cwd=repo
        )
        if proc.returncode not in {0, 1}:
            hits.append(("gitleaks_error", "scanner failed"))
        elif proc.returncode == 1:
            hits.append(("gitleaks", "redacted finding"))

    if shutil.which("trufflehog"):
        proc = run(["trufflehog", "filesystem", str(repo), "--no-update", "--json"], cwd=repo)
        if proc.returncode not in {0, 1, 183}:
            hits.append(("trufflehog_error", "scanner failed"))
        elif proc.stdout.strip():
            hits.append(("trufflehog", "redacted finding"))

    return hits


def changed_text(repo, paths):
    chunks = []
    for args in (("diff", "--", *paths), ("diff", "--cached", "--", *paths)):
        proc = git(repo, *args)
        if proc.returncode == 0 and proc.stdout:
            chunks.append(proc.stdout)

    for rel_path in paths:
        full_path = repo / rel_path
        if not full_path.is_file() or full_path.stat().st_size > 2_000_000:
            continue
        try:
            chunks.append(full_path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue

    return "\n".join(chunks)


def fallback_secret_hits(repo, paths):
    text = changed_text(repo, paths)
    hits = []
    for rule, pattern in SECRET_CONTENT_RULES:
        flags = re.I if rule in {"connection_string", "assigned_secret"} else 0
        if re.search(pattern, text, flags):
            hits.append((rule, "candidate changes"))
    return hits


def commit_message(repo):
    proc = git(repo, "diff", "--cached", "--stat")
    if proc.returncode != 0:
        return "chore: sync local changes"
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return "chore: sync local changes"
    summary = lines[-1]
    return f"chore: sync local changes ({summary})"


def process_repo(repo):
    branch_proc = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else "UNKNOWN"

    status_proc = git(repo, "status", "--porcelain=v1", "-b")
    if status_proc.returncode != 0:
        return f"{repo}: FAILED (git status error) ({branch})"

    status_lines = [line for line in status_proc.stdout.splitlines()[1:] if line.strip()]
    if not status_lines:
        return f"{repo}: NO CHANGES ({branch})"

    in_progress = operation_in_progress(repo)
    if in_progress:
        return f"{repo}: SKIP (in-progress {'/'.join(in_progress)}; manual attention) ({branch})"

    if unmerged(status_lines):
        return f"{repo}: SKIP (unmerged paths/conflicts; manual attention) ({branch})"

    paths = status_paths(status_lines)
    for path in paths:
        rule = sensitive_path(path)
        if rule:
            return f"{repo}: SKIP (sensitive/ambiguous path: {path}; rule={rule}) ({branch})"

    diff_proc = git(repo, "diff")
    cached_proc = git(repo, "diff", "--cached")
    if CONFLICT_MARKER.search(f"{diff_proc.stdout}\n{cached_proc.stdout}"):
        return f"{repo}: SKIP (conflict markers in diff) ({branch})"

    scanner_hits = run_external_secret_scanners(repo)
    if scanner_hits:
        rule, location = scanner_hits[0]
        return f"{repo}: SKIP (secret-scan hit: {rule} in {location}) ({branch})"

    fallback_hits = fallback_secret_hits(repo, paths)
    if fallback_hits:
        rule, location = fallback_hits[0]
        return f"{repo}: SKIP (secret-scan hit: {rule} in {location}) ({branch})"

    if DRY_RUN:
        return f"{repo}: DRY-RUN OK (would stage/commit/push) ({branch})"

    add_proc = git(repo, "add", "-A")
    if add_proc.returncode != 0:
        return f"{repo}: FAILED (git add failed) ({branch})"

    staged_proc = git(repo, "diff", "--cached", "--name-only")
    staged_paths = [line.strip() for line in staged_proc.stdout.splitlines() if line.strip()]
    for path in staged_paths:
        rule = sensitive_path(path)
        if rule:
            git(repo, "restore", "--staged", ".")
            return f"{repo}: SKIP (sensitive/ambiguous after staging: {path}; rule={rule}) ({branch})"

    commit_proc = git(repo, "commit", "-m", commit_message(repo))
    if commit_proc.returncode != 0:
        if "nothing to commit" in commit_proc.stdout.lower():
            return f"{repo}: NO CHANGES ({branch})"
        return f"{repo}: FAILED (git commit failed) ({branch})"

    hash_proc = git(repo, "rev-parse", "--short", "HEAD")
    commit_hash = hash_proc.stdout.strip() if hash_proc.returncode == 0 else "UNKNOWN"

    push_proc = git(repo, "push")
    if push_proc.returncode != 0:
        return f"{repo}: FAILED (push failed after {commit_hash}) ({branch})"

    return f"{repo}: COMMITTED+PUSHED {commit_hash} ({branch})"


def main():
    started = now()
    results = []
    for repo in discover_repos():
        try:
            results.append(process_repo(repo))
        except Exception as exc:
            results.append(f"{repo}: FAILED ({type(exc).__name__})")

    report = "\n".join(
        [
            f"daily-safe-cpush run: {started.isoformat()}",
            *results,
            "",
        ]
    )
    if not DRY_RUN:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / "last-report.log").write_text(report, encoding="utf-8")
        with (LOG_DIR / "history.log").open("a", encoding="utf-8") as handle:
            handle.write(report)
    sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
