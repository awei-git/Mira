#!/usr/bin/env python3
"""Deploy a released tag to EC2 boxes.

Usage: deploy.py <project> <tag> [--stage]
  --stage: extract to /opt/deploy/staging-test/ on the box, touch nothing live.

Pipeline: mkdist.py (tarball -> S3 -> presigned URL)
         -> install box-deploy.sh on each box if missing/stale
         -> SSM: box-deploy.sh with project options from projects.yaml
"""
import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.expanduser("~/workspace/tools")


def load_projects():
    with open(os.path.join(HERE, "projects.yaml")) as f:
        return yaml.safe_load(f)["projects"]


def ssm(instance, cmd):
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "ssm_cmd.py"),
                        instance, "--", cmd],
                       capture_output=True, text=True, timeout=400)
    print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        raise RuntimeError(f"SSM failed on {instance}")


def ensure_worker(instance):
    local = open(os.path.join(HERE, "box-deploy.sh"), "rb").read()
    want = hashlib.md5(local).hexdigest()
    check = ("md5sum /opt/deploy/box-deploy.sh 2>/dev/null | cut -d' ' -f1 || echo missing")
    r = subprocess.run([sys.executable, os.path.join(TOOLS, "ssm_cmd.py"),
                        instance, "--", check],
                       capture_output=True, text=True, timeout=120)
    if r.stdout.strip() == want:
        print(f"[{instance}] box-deploy.sh up to date")
        return
    print(f"[{instance}] installing box-deploy.sh")
    b64 = base64.b64encode(local).decode()
    # chunked to stay under SSM command size limits
    ssm(instance, "mkdir -p /opt/deploy/staging /opt/deploy/backups && echo done")
    for i in range(0, len(b64), 4000):
        part = b64[i:i + 4000]
        op = ">" if i == 0 else ">>"
        ssm(instance, f"echo {part} {op} /tmp/bd.b64 && echo chunk-ok")
    ssm(instance, "base64 -d /tmp/bd.b64 > /opt/deploy/box-deploy.sh && "
                  "chmod +x /opt/deploy/box-deploy.sh && rm /tmp/bd.b64 && "
                  "md5sum /opt/deploy/box-deploy.sh")


def build_cmd(cfg, box, dist, stage):
    c = ["bash", "/opt/deploy/box-deploy.sh",
         "--project", cfg["_name"], "--path", box["path"],
         "--url", dist["url"], "--sha256", dist["sha256"],
         "--tag", dist["tag"]]
    for d in cfg.get("data_dirs") or []:
        c += ["--exclude", d]
    if cfg.get("env_file"):
        c += ["--exclude", cfg["env_file"]]
    if cfg.get("overlay"):
        c += ["--overlay", cfg["overlay"]]
    if cfg.get("service"):
        c += ["--service", cfg["service"]]
    if cfg.get("health_cmd"):
        c += ["--health-cmd", cfg["health_cmd"]]
    dk = cfg.get("docker")
    if dk:
        c += ["--docker-container", dk["container"],
              "--docker-publish", dk["publish"],
              "--docker-volume", dk["volume"]]
        if dk.get("env_file"):
            c += ["--docker-env-file", dk["env_file"]]
    if stage:
        c += ["--stage"]
    return " ".join(shlex.quote(x) for x in c)


def main():
    project, tag = sys.argv[1], sys.argv[2]
    stage = "--stage" in sys.argv
    projects = load_projects()
    cfg = projects[project]
    cfg["_name"] = project

    print(f"== mkdist {project} {tag}")
    out = subprocess.run([sys.executable, os.path.join(HERE, "mkdist.py"),
                          project, tag], capture_output=True, text=True)
    print(out.stdout, end="")
    if out.returncode != 0:
        print(out.stderr[-2000:], file=sys.stderr)
        raise SystemExit(1)
    dist = json.loads(out.stdout)

    for box in cfg["boxes"]:
        inst = box["instance"]
        print(f"== deploy {project} {tag} -> {box['host']} ({inst})"
              + (" [STAGE]" if stage else ""))
        ensure_worker(inst)
        ssm(inst, build_cmd(cfg, box, dist, stage))
    print("ALL DONE")


if __name__ == "__main__":
    main()
