#!/usr/bin/env python3
"""Cut a GitHub Release for a deploy project.

Usage: release.py <project> <tag> [commit-sha] [notes]
  commit defaults to the local clone's HEAD for the project's repo.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from mkdist import api, sh  # noqa: E402

import re


def get_cfg(project):
    import yaml
    with open(os.path.join(HERE, "projects.yaml")) as f:
        return yaml.safe_load(f)["projects"][project]


def main():
    project, tag = sys.argv[1], sys.argv[2]
    commit = sys.argv[3] if len(sys.argv) > 3 else None
    notes = sys.argv[4] if len(sys.argv) > 4 else f"Deploy release {tag} for {project}."
    cfg = get_cfg(project)
    repo = cfg["repo"]
    if not commit:
        name = repo.split("/")[1]
        clone = os.path.expanduser(f"~/workspace/repos/{name}")
        commit = sh(["git", "-C", clone, "rev-parse", "HEAD"]).strip()
    # create tag ref (fail if exists)
    try:
        api(f"/repos/{repo}/git/refs", "POST",
            {"ref": f"refs/tags/{tag}", "sha": commit})
    except RuntimeError as e:
        if "Reference already exists" not in str(e):
            raise
        print(f"tag {tag} already exists, reusing")
    rel = api(f"/repos/{repo}/releases", "POST",
              {"tag_name": tag, "name": f"{project} {tag}",
               "body": notes, "prerelease": False})
    print(json.dumps({"project": project, "tag": tag, "sha": commit,
                      "release_id": rel["id"], "url": rel["html_url"]}, indent=1))


if __name__ == "__main__":
    main()
