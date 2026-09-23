"""Audit and stage shared content assets independently of live code deployment.

Raw app identity is never a cloud input. An app-approved identity/cloud projection
is required. Skills use the existing backend security audit before any asset save.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from content_worker.files import active_snapshot, audit, checksum, safe_file, write_json, atomic_write
from content_worker.identity import IDENTITY_FILES


def backend_audit(name, content):
    from memory.soul_skills import audit_skill

    return audit_skill(
        name,
        content,
        origin="external",
        source="community_import",
        caller_agent="shared_asset_sync",
        invocation_source="github_registry",
    )


def gather(source, audit_skill=backend_audit):
    projection = safe_file(source, "identity/cloud")
    if not projection.is_dir():
        raise ValueError("content_only_identity_projection_required")
    policy = json.loads(safe_file(projection, "manifest.json").read_text())
    if policy.get("scope") != "content_only" or policy.get("approved_by") != "mira-app":
        raise ValueError("app_reviewed_cloud_projection_required")
    if set(policy.get("sha256", {})) != set(IDENTITY_FILES):
        raise ValueError("cloud_projection_file_set_mismatch")
    files = {}
    for name in IDENTITY_FILES:
        body = safe_file(projection, name).read_bytes()
        if not body or len(body) > 200_000 or checksum(body) != policy["sha256"][name]:
            raise ValueError("cloud_projection_hash_or_size_mismatch")
        files["identity/" + name] = body
    registry = safe_file(source, "skills")
    index, hashes, audit_receipts = [], {}, {}
    for folder in sorted(registry.iterdir()):
        if folder.name == "README.md":
            continue
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", folder.name):
            raise ValueError("invalid_registry_skill")
        package = {}
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise ValueError("skill_symlink_rejected")
            if path.is_file():
                relative = str(path.relative_to(folder))
                body = safe_file(folder, relative).read_bytes()
                if len(body) > 200_000:
                    raise ValueError("skill_file_too_large")
                package[relative] = body.decode("utf-8")
        if "SKILL.md" not in package:
            raise ValueError("skill_instructions_missing")
        # Include scripts, not merely the reassuring description, in the audit.
        combined = "\n\n".join("# " + name + "\n" + text for name, text in package.items())
        verdict = audit_skill(folder.name, combined)
        if verdict.get("passed") is not True or verdict.get("requires_review") or verdict.get("findings"):
            raise ValueError("registry_skill_audit_blocked:" + folder.name)
        audited_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        body = package["SKILL.md"].encode()
        # Flat entry is the format memory.soul_skills.load_skill actually reads.
        files["skills/" + folder.name + ".md"] = body
        hashes[folder.name] = checksum(body)
        index.append(
            dict(
                name=folder.name,
                file=folder.name + ".md",
                description=folder.name + " shared registry skill",
                tags=[folder.name],
                audited_at=audited_at,
                source="github_registry",
            )
        )
        audit_receipts[folder.name] = {"passed": True, "files": {k: checksum(v.encode()) for k, v in package.items()}}
        for name, text in package.items():
            files["skills/" + folder.name + "/" + name] = text.encode()
    files["skills/index.json"] = json.dumps(index, ensure_ascii=False).encode()
    files["audit_hashes.json"] = json.dumps(hashes).encode()
    files["skill_audit_receipts.json"] = json.dumps(audit_receipts).encode()
    return files


def sync(source, destination, *, activate=False, legacy_soul=None, audit_skill=backend_audit):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if activate and legacy_soul is None:
        raise ValueError("legacy_soul_path_required_for_activation")
    # All reads and security audits precede any destination write.
    files = gather(source, audit_skill)
    return stage_files(files, source, destination, activate=activate, legacy_soul=legacy_soul)


def install_snapshot(
    snapshot_path, destination, *, audit_root, activate=False, legacy_soul=None, audit_skill=backend_audit
):
    """Install an already sanitized transport bundle; raw app files never transit AWS."""
    source = Path(snapshot_path).resolve()
    manifest = json.loads(safe_file(source, "manifest.json").read_text())
    digest = checksum(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    if source.name != digest or manifest.get("scope") != "content_only" or manifest.get("format") != 1:
        raise ValueError("transport_manifest_mismatch")
    files = {name: safe_file(source, name).read_bytes() for name in manifest["sha256"]}
    for name, body in files.items():
        if checksum(body) != manifest["sha256"][name]:
            raise ValueError("transport_file_mismatch")
    allowed = {"identity/" + name for name in IDENTITY_FILES}
    allowed.update({"skills/index.json", "audit_hashes.json", "skill_audit_receipts.json"})
    for entry in json.loads(files["skills/index.json"]):
        name = entry["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", name):
            raise ValueError("invalid_registry_skill")
        prefix = "skills/" + name + "/"
        package = {key: body for key, body in files.items() if key.startswith(prefix)}
        if files["skills/" + name + ".md"] != package[prefix + "SKILL.md"]:
            raise ValueError("transport_skill_mapping_mismatch")
        verdict = audit_skill(
            name, "\n\n".join("# " + key + "\n" + body.decode("utf-8") for key, body in sorted(package.items()))
        )
        if verdict.get("passed") is not True or verdict.get("requires_review") or verdict.get("findings"):
            raise ValueError("registry_skill_audit_blocked:" + name)
        allowed.add("skills/" + name + ".md")
        allowed.update(package)
    if set(files) != allowed:
        raise ValueError("transport_file_set_mismatch")
    return stage_files(
        files, Path(audit_root).resolve(), Path(destination).resolve(), activate=activate, legacy_soul=legacy_soul
    )


def stage_files(files, source, destination, *, activate=False, legacy_soul=None):
    if activate and legacy_soul is None:
        raise ValueError("legacy_soul_path_required_for_activation")
    manifest = {"format": 1, "scope": "content_only", "sha256": {k: checksum(v) for k, v in files.items()}}
    snapshot = checksum(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    destination.mkdir(parents=True, exist_ok=True)
    with safe_file(destination, "sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        generation = safe_file(destination, "releases/" + snapshot)
        audit(source, "Stage audited shared assets; no live code changes", str(generation))
        for name, body in files.items():
            atomic_write(safe_file(generation, name), body, mode=0o644)
        write_json(safe_file(generation, "manifest.json"), manifest, mode=0o644)
        if activate:
            if legacy_soul is None:
                raise ValueError("legacy_soul_path_required_for_activation")
            # Keep original bytes beside the old files, never copy them into GitHub.
            backups = []
            legacy = Path(legacy_soul).resolve()
            for name in ("identity.md", "worldview.md", "memory.md", "interests.md"):
                old = safe_file(legacy, name)
                if old.exists():
                    backup = old.with_name(name + ".legacy-" + checksum(old.read_bytes())[:16])
                    if backup.exists():
                        raise ValueError("legacy_backup_already_exists")
                    backups.append((old, backup))
            audit(source, "Activate shared identity; preserve original soul files by rename", str(legacy))
            renamed = []
            pointer = safe_file(destination, "current.json")
            previous = pointer.read_bytes() if pointer.exists() else None
            try:
                for old, backup in backups:
                    old.rename(backup)
                    renamed.append((old, backup))
                write_json(safe_file(destination, "current.json"), {"snapshot": snapshot}, mode=0o644)
                active_snapshot(destination)
            except BaseException:
                if previous is None:
                    pointer.unlink(missing_ok=True)
                else:
                    atomic_write(pointer, previous, mode=0o644)
                for old, backup in reversed(renamed):
                    backup.rename(old)
                raise
        return {
            "snapshot": snapshot,
            "path": str(generation),
            "activated": activate,
            "skills": len(json.loads(files["skills/index.json"])),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source", type=Path)
    inputs.add_argument("--snapshot", type=Path)
    parser.add_argument("--audit-root", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--legacy-soul", type=Path)
    args = parser.parse_args()
    if args.snapshot:
        if args.audit_root is None:
            parser.error("--snapshot requires --audit-root")
        result = install_snapshot(
            args.snapshot,
            args.destination,
            audit_root=args.audit_root,
            activate=args.activate,
            legacy_soul=args.legacy_soul,
        )
    else:
        result = sync(args.source, args.destination, activate=args.activate, legacy_soul=args.legacy_soul)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
