#!/usr/bin/env python3
"""Build a release tarball for a deploy project and stage it on S3.

Usage: mkdist.py <project> <tag>
Reads deploy/projects.yaml (next to this script).
Prints JSON: {"project","tag","sha","tarball","sha256","url"}.

Source of files: local git clone if it has the commit (git archive),
otherwise GitHub API (recursive tree + blobs, cached under ~/.cache/mkdist).
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPOS_DIR = os.path.expanduser("~/workspace/repos")
CACHE = os.path.expanduser("~/.cache/mkdist/blobs")
GH_API = os.path.expanduser("~/workspace/skills/github/bin/gh-api")
S3_BUCKET = "mira-content-126517272111-us-east-1"
S3_PREFIX = "deploy"


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {r.stderr[:200]}")
    return r.stdout


def api(path, method="GET", data=None):
    cmd = [GH_API, path]
    if method != "GET":
        cmd += ["--method", method]
    if data is not None:
        cmd += ["--data", json.dumps(data)]
    out = sh(cmd)
    return json.loads(out) if out.strip() else {}


def load_projects():
    import yaml
    with open(os.path.join(HERE, "projects.yaml")) as f:
        return yaml.safe_load(f)["projects"]


def get_blob(repo, sha):
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, sha)
    if os.path.exists(cp):
        return open(cp, "rb").read()
    d = api(f"/repos/{repo}/git/blobs/{sha}")
    raw = base64.b64decode(d["content"])
    open(cp, "wb").write(raw)
    return raw


def build_from_api(repo, sha, subdir):
    tree = api(f"/repos/{repo}/git/trees/{sha}?recursive=1")["tree"]
    prefix = "" if subdir == "." else subdir.rstrip("/") + "/"
    files = {}
    for e in tree:
        if e["type"] != "blob":
            continue
        p = e["path"]
        if not p.startswith(prefix):
            continue
        rel = p[len(prefix):]
        if rel == "":
            continue
        files[rel] = get_blob(repo, e["sha"])
    return files


def build_from_clone(repo, sha, subdir):
    name = repo.split("/")[1]
    clone = os.path.join(REPOS_DIR, name)
    try:
        sh(["git", "-C", clone, "cat-file", "-t", sha])
        commit = sha
    except RuntimeError:
        # release commit was created via API; find a local commit with the same tree
        tree = api(f"/repos/{repo}/git/commits/{sha}")["tree"]["sha"]
        out = sh(["git", "-C", clone, "log", "--all", "--format=%H %T"])
        commit = None
        for line in out.split("\n"):
            if line.endswith(" " + tree):
                commit = line.split()[0]
                break
        if not commit:
            return None
    subdir = "" if subdir == "." else subdir
    out = subprocess.run(
        ["git", "-C", clone, "archive", commit, subdir] if subdir
        else ["git", "-C", clone, "archive", commit],
        capture_output=True)
    if out.returncode != 0:
        return None
    files = {}
    with tarfile.open(fileobj=io.BytesIO(out.stdout)) as tf:
        for m in tf.getmembers():
            if m.isfile():
                p = m.name
                if subdir and p.startswith(subdir + "/"):
                    p = p[len(subdir) + 1:]
                files[p] = tf.extractfile(m).read()
    return files


def main():
    project, tag = sys.argv[1], sys.argv[2]
    projects = load_projects()
    cfg = projects[project]
    repo, subdir = cfg["repo"], cfg.get("subdir", ".")
    ref = api(f"/repos/{repo}/git/ref/tags/{tag}")
    sha = ref["object"]["sha"]
    if ref["object"]["type"] == "tag":
        sha = api(f"/repos/{repo}/git/tags/{sha}")["object"]["sha"]

    files = build_from_clone(repo, sha, subdir)
    src = "clone" if files is not None else "api"
    if files is None:
        files = build_from_api(repo, sha, subdir)
    if not files:
        raise RuntimeError("empty file set")

    tmp = tempfile.mkdtemp()
    tgz = os.path.join(tmp, f"{project}-{tag}.tar.gz")
    with tarfile.open(tgz, "w:gz") as tf:
        for rel, raw in sorted(files.items()):
            ti = tarfile.TarInfo(rel)
            ti.size = len(raw)
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(raw))
    digest = hashlib.sha256(open(tgz, "rb").read()).hexdigest()
    key = f"{S3_PREFIX}/{project}/{tag}.tar.gz"
    sh(["aws", "s3", "cp", tgz, f"s3://{S3_BUCKET}/{key}"],
       env={**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep + os.environ["PATH"]})
    url = sh(["aws", "s3", "presign", f"s3://{S3_BUCKET}/{key}", "--expires-in", "3600"],
             env={**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep + os.environ["PATH"]}).strip()
    print(json.dumps({"project": project, "tag": tag, "sha": sha,
                      "source": src, "files": len(files),
                      "sha256": digest, "url": url}))


if __name__ == "__main__":
    main()
