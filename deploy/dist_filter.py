"""Exclude private/runtime state before archive upload, not just at rsync time."""

from pathlib import PurePosixPath


def include_file(relative, cfg):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("invalid_distribution_path")
    excluded = list(cfg.get("data_dirs") or []) + list(cfg.get("package_excludes") or [])
    if cfg.get("env_file"):
        excluded.append(cfg["env_file"])
    for name in excluded:
        if relative == name or relative.startswith(name.rstrip("/") + "/"):
            return False
    return True
