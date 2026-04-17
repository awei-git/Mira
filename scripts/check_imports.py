#!/usr/bin/env python3
"""Static import hygiene check (Phase 0 柱子 0.5).

Walks agents/ and lib/ for .py files, extracts all `import X` and
`from X import Y` statements via AST, and verifies each module
resolves via importlib.util.find_spec() *after* pathsetup.py
configures sys.path.

Exits non-zero on any unresolved internal import. Third-party /
stdlib imports are skipped (anything not rooted in MIRA_ROOT).

Usage:
    python3 scripts/check_imports.py
    python3 scripts/check_imports.py --format json
    python3 scripts/check_imports.py --files agents/super/core.py lib/memory/store.py
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

MIRA_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = MIRA_ROOT / "lib"

# Ensure pathsetup runs so we see the real runtime sys.path.
sys.path.insert(0, str(LIB_DIR))
import pathsetup  # noqa: F401,E402


SCAN_ROOTS = [MIRA_ROOT / "agents", MIRA_ROOT / "lib"]
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", "tests"}


def extract_imports(path: Path) -> list[tuple[str, int]]:
    """Return [(module_name, lineno), ...] for every import statement."""
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import — skip (handled by the package context).
                continue
            if node.module:
                out.append((node.module.split(".")[0], node.lineno))
    return out


def is_internal(module_name: str) -> bool:
    """True if module_name resolves to a file inside MIRA_ROOT."""
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        return False
    try:
        Path(spec.origin).resolve().relative_to(MIRA_ROOT)
        return True
    except ValueError:
        return False


def resolves(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def walk_targets(roots: list[Path], filter_files: list[Path] | None) -> list[Path]:
    if filter_files:
        return [f.resolve() for f in filter_files if f.suffix == ".py"]
    out = []
    for root in roots:
        for p in root.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            out.append(p)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument(
        "--files",
        nargs="*",
        type=Path,
        help="Limit scan to these files (pre-commit uses this).",
    )
    args = ap.parse_args()

    files = walk_targets(SCAN_ROOTS, args.files)

    # Build the universe of internal module stems (one pass) so we can
    # distinguish internal-broken from "third-party that's fine".
    internal_stems: set[str] = set()
    for root in SCAN_ROOTS:
        for child in root.iterdir():
            if child.is_dir() and child.name not in EXCLUDE_DIRS:
                internal_stems.add(child.name)
                for sub in child.iterdir():
                    if sub.is_dir() and (sub / "__init__.py").exists():
                        internal_stems.add(sub.name)
            elif child.suffix == ".py":
                internal_stems.add(child.stem)

    broken: dict[str, list[dict]] = {}
    third_party_unresolved: dict[str, list[str]] = {}

    for f in files:
        imports = extract_imports(f)
        file_broken = []
        for mod, lineno in imports:
            if resolves(mod):
                continue
            # Unresolved: decide if internal (hard fail) or third-party (warn).
            if mod in internal_stems or _looks_internal(mod):
                file_broken.append({"module": mod, "line": lineno})
            else:
                third_party_unresolved.setdefault(mod, []).append(f"{f.relative_to(MIRA_ROOT)}:{lineno}")
        if file_broken:
            broken[str(f.relative_to(MIRA_ROOT))] = file_broken

    if args.format == "json":
        print(
            json.dumps(
                {"broken": broken, "third_party_unresolved": third_party_unresolved},
                indent=2,
            )
        )
    else:
        if not broken:
            print(f"OK — {len(files)} files scanned, 0 broken internal imports.")
        else:
            print(f"BROKEN — {sum(len(v) for v in broken.values())} internal imports:")
            for f, items in sorted(broken.items()):
                for item in items:
                    print(f"  {f}:{item['line']}  module `{item['module']}`")
        if third_party_unresolved:
            print(
                f"\n(info) {len(third_party_unresolved)} unresolved third-party/stdlib names"
                f" — likely missing deps, not our bug:"
            )
            for mod, locs in sorted(third_party_unresolved.items()):
                print(f"  {mod}  ({len(locs)} refs, first: {locs[0]})")

    return 1 if broken else 0


def _looks_internal(mod: str) -> bool:
    """Heuristic: names that look like Mira-internal packages."""
    mira_ish = {
        "persona",
        "sub_agent",
        "soul_manager",
        "soul",
        "config",
        "mira",
        "pathsetup",
        "health_monitor",
        "bridge",
        "notes_bridge",
        "llm_providers",
        "memory_store",
        "memory",
        "evolution",
        "substack",
        "podcast",
        "prompts",
        "state",
        "handlers_legacy",
        "task_manager",
        "task_worker",
        "agent_registry",
    }
    return mod in mira_ish


if __name__ == "__main__":
    sys.exit(main())
