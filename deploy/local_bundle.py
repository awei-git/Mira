"""Build an offline, commit-pinned deployment bundle; never upload or activate it."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

import yaml

from dist_filter import include_file


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def build(repo, project, commit, tag, output):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("full_commit_required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", tag):
        raise ValueError("invalid_release_tag")
    if git(repo, "rev-parse", f"refs/tags/{tag}^{{commit}}").decode().strip() != commit:
        raise ValueError("release_commit_mismatch")
    # Read config and source from the same immutable tree, never the dirty checkout.
    cfg = yaml.safe_load(git(repo, "show", f"{commit}:deploy/projects.yaml"))["projects"][project]
    prefix = "" if cfg.get("subdir", ".") == "." else cfg["subdir"].rstrip("/") + "/"
    files = {}
    with tarfile.open(fileobj=io.BytesIO(git(repo, "archive", "--format=tar", commit))) as archive:
        members = {m.name: m for m in archive if m.isfile()}
        for name, member in members.items():
            if name.startswith(prefix):
                relative = name[len(prefix) :]
                if include_file(relative, cfg):
                    files[relative] = (archive.extractfile(member).read(), member.mode & 0o777)
        for source, destination in (cfg.get("extra_files") or {}).items():
            if not include_file(source, {}) or not include_file(destination, cfg):
                raise ValueError("invalid_extra_distribution_file")
            if source not in members or destination in files:
                raise ValueError("missing_or_colliding_extra_distribution_file")
            member = members[source]
            files[destination] = (archive.extractfile(member).read(), member.mode & 0o777)
    if not files:
        raise ValueError("empty_distribution")
    data = io.BytesIO()
    with gzip.GzipFile(fileobj=data, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, (raw, mode) in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(raw), mode
                archive.addfile(member, io.BytesIO(raw))
    payload = data.getvalue()
    receipt = {
        "schema": 1,
        "project": project,
        "repo": cfg["repo"],
        "commit": commit,
        "tag": tag,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "files": {name: hashlib.sha256(raw).hexdigest() for name, (raw, _) in sorted(files.items())},
        "uploaded": False,
        "deployed": False,
        "skills_approved": False,
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "bundle.tar.gz").write_bytes(payload)
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "project", "commit", "tag", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    receipt = build(**vars(args))
    print(json.dumps({key: value for key, value in receipt.items() if key != "files"}))


if __name__ == "__main__":
    main()
