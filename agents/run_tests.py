#!/usr/bin/env python3
"""Mira test runner — discovers and runs all test files."""
from __future__ import annotations
import importlib.util
import sys
import traceback
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent

# Ensure paths
sys.path.insert(0, str(AGENTS_DIR / "super"))
sys.path.insert(0, str(AGENTS_DIR / "shared"))
sys.path.insert(0, str(AGENTS_DIR / "writer"))
sys.path.insert(0, str(AGENTS_DIR / "podcast"))
sys.path.insert(0, str(AGENTS_DIR / "socialmedia"))


def discover_tests() -> list[tuple[Path, str]]:
    """Find all test_*.py files and extract test functions."""
    tests = []
    for test_file in sorted(AGENTS_DIR.rglob("tests/test_*.py")):
        module_name = f"test_{test_file.stem}_{id(test_file)}"
        spec = importlib.util.spec_from_file_location(module_name, str(test_file))
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                print(f"  ERROR loading {test_file.relative_to(AGENTS_DIR)}: {e}")
                continue
            for name in sorted(dir(module)):
                if name.startswith("test_") and callable(getattr(module, name)):
                    tests.append((test_file, name, getattr(module, name)))
    return tests


def main():
    print("=" * 60)
    print("Mira Agent Test Suite")
    print("=" * 60)

    tests = discover_tests()
    if not tests:
        print("No tests found!")
        sys.exit(1)

    passed = 0
    failed = 0
    errors = []
    current_file = None

    for test_file, name, func in tests:
        rel = test_file.relative_to(AGENTS_DIR)
        if test_file != current_file:
            current_file = test_file
            print(f"\n{rel}")

        try:
            func()
            print(f"  PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
            failed += 1
            errors.append((f"{rel}::{name}", str(e)))
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            failed += 1
            errors.append((f"{rel}::{name}", traceback.format_exc()))

    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")

    if errors:
        print(f"\nFailures:")
        for loc, msg in errors:
            print(f"  {loc}")
            for line in msg.strip().split("\n"):
                print(f"    {line}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
