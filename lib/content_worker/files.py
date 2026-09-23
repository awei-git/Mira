"""Small filesystem contracts shared by the batch worker and asset synchronizer."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def safe_file(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("invalid_relative_artifact_path")
    candidate = root / relative
    if any(p.is_symlink() for p in (candidate, *candidate.parents) if p != root.parent):
        raise ValueError("artifact_symlink_rejected")
    if not candidate.is_relative_to(root):
        raise ValueError("artifact_outside_root")
    return candidate


def atomic_write(path, data, *, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("output_symlink_rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, data, *, mode=0o600):
    atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(), mode=mode)


def audit(repo, intention, changed):
    path = safe_file(repo, "logs/permacomputing_audit.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n- {datetime.now(timezone.utc).isoformat()} | content worker | "
            f"Intention: {intention}; changed: {changed}; "
            "self-check: explicit inputs, retained receipts, no publication implied; "
            "scope_adherence: issue19 content-only batch workflow.\n"
        )


def active_snapshot(root):
    root = Path(root).resolve()
    pointer = json.loads(safe_file(root, "current.json").read_text())
    digest = pointer.get("snapshot")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid_snapshot_pointer")
    directory = safe_file(root, "releases/" + digest)
    manifest = json.loads(safe_file(directory, "manifest.json").read_text())
    if (
        manifest.get("format") != 1
        or manifest.get("scope") != "content_only"
        or not isinstance(manifest.get("sha256"), dict)
    ):
        raise ValueError("invalid_snapshot_manifest")
    if checksum(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()) != digest:
        raise ValueError("snapshot_manifest_mismatch")
    for name, expected in manifest["sha256"].items():
        if checksum(safe_file(directory, name).read_bytes()) != expected:
            raise ValueError("snapshot_file_mismatch")
    return directory
