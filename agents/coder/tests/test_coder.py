"""Coder agent tests — handler loading, skill injection, and real workflows.

Fast tests (no LLM): handler import, skill loading, manifest, validation
Slow tests (uses tokens): actual debug/review/fix tasks
"""
from __future__ import annotations
import inspect
import json
import sys
import tempfile
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "coder"))
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))


# ---------------------------------------------------------------------------
# Fast tests (no tokens)
# ---------------------------------------------------------------------------

def test_handler_imports():
    """handler.py should import without errors."""
    import handler
    assert hasattr(handler, "handle")
    assert callable(handler.handle)


def test_handler_signature():
    """handle() should accept the standard agent interface."""
    from handler import handle
    sig = inspect.signature(handle)
    params = set(sig.parameters.keys())
    required = {"workspace", "task_id", "content", "sender", "thread_id"}
    assert required.issubset(params), f"Missing params: {required - params}"
    # Should also accept tier
    assert "tier" in params, "handle() should accept 'tier' parameter"


def test_manifest_valid():
    """manifest.json should be valid and have required fields."""
    manifest_path = Path(__file__).parent.parent / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["name"] == "coder"
    assert "debug" in data["description"].lower() or "review" in data["description"].lower()
    assert len(data["keywords"]) >= 5, "Too few keywords for routing"
    assert data["timeout_category"] == "long"


def test_skills_index_valid():
    """skills/index.json should be valid JSON with all files present."""
    skills_dir = Path(__file__).parent.parent / "skills"
    index = json.loads((skills_dir / "index.json").read_text(encoding="utf-8"))
    assert len(index) >= 15, f"Expected 15+ skills, got {len(index)}"

    missing = []
    for skill in index:
        assert "name" in skill, f"Skill missing name: {skill}"
        assert "file" in skill, f"Skill missing file: {skill}"
        assert "tags" in skill, f"Skill missing tags: {skill}"
        skill_path = skills_dir / skill["file"]
        if not skill_path.exists():
            missing.append(skill["file"])
    assert not missing, f"Skill files missing: {missing}"


def test_skills_have_trigger_sections():
    """New skills should have Trigger or actionable sections. Old ones get a pass."""
    skills_dir = Path(__file__).parent.parent / "skills"
    # Older skills pre-date the actionable format — exclude from strict check
    _LEGACY_SKILLS = {"incremental-development.md", "defensive-interface-design.md",
                      "security-threat-modeling.md", "contract-first-api-design.md",
                      "measure-before-optimize.md", "checklist-driven-code-review.md",
                      "test-driven-red-green-refactor.md"}
    weak = []
    for md in skills_dir.glob("*.md"):
        if md.name in _LEGACY_SKILLS:
            continue
        content = md.read_text(encoding="utf-8")
        has_trigger = "## Trigger" in content or "## Start Now" in content
        has_actionable = "## Decision" in content or "## Method" in content or "## Rules" in content or "## Scan" in content
        if not (has_trigger or has_actionable):
            weak.append(md.name)
    assert not weak, f"Skills without actionable sections: {weak}"


def test_validate_python_files():
    """_validate_python_files should catch syntax errors."""
    from handler import _validate_python_files

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir)
        # Good file
        (ws / "good.py").write_text("x = 1\n", encoding="utf-8")
        # Bad file
        (ws / "bad.py").write_text("def f(\n", encoding="utf-8")

        # Should not raise, just log warnings
        _validate_python_files(ws)


def test_system_prompt_mentions_debug():
    """System prompt should emphasize debug/review focus."""
    from handler import _CODER_SYSTEM
    lower = _CODER_SYSTEM.lower()
    assert "debug" in lower
    assert "review" in lower
    assert "read before" in lower or "read the" in lower


# ---------------------------------------------------------------------------
# Slow tests (real LLM calls)
# ---------------------------------------------------------------------------

def _run_coder(content: str, tier: str = "light", files: dict = None) -> str:
    """Helper: create workspace, optionally populate files, call handler."""
    import uuid
    from handler import handle

    ws = Path(tempfile.mkdtemp(prefix="mira_coder_test_"))
    if files:
        for name, text in files.items():
            (ws / name).write_text(text, encoding="utf-8")

    result = handle(
        workspace=ws,
        task_id=f"test_{uuid.uuid4().hex[:8]}",
        content=content,
        sender="ang",
        thread_id="",
        tier=tier,
    )
    return result or ""


@pytest.mark.slow
def test_debug_find_bug():
    """Coder should find the bug in a simple Python file."""
    buggy_code = '''def average(numbers):
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)
'''
    result = _run_coder(
        "This function crashes on empty input. Find the bug, explain it, and fix it.",
        files={"average.py": buggy_code},
    )
    assert result, "Coder returned empty"
    # Should mention division by zero or empty list
    lower = result.lower()
    assert ("zero" in lower or "empty" in lower or "len" in lower), \
        f"Didn't identify the bug: {result[:300]}"


@pytest.mark.slow
def test_review_detect_problems():
    """Coder should detect issues in code review."""
    code_with_issues = '''import os
import subprocess

def process_user_input(user_data):
    query = f"SELECT * FROM users WHERE name = '{user_data}'"
    result = subprocess.run(f"echo {user_data}", shell=True, capture_output=True)
    password = "admin123"
    try:
        db.execute(query)
    except:
        pass
    return result.stdout
'''
    result = _run_coder(
        "Review this code for bugs and security issues. List everything you find.",
        files={"app.py": code_with_issues},
    )
    assert result, "Coder returned empty on review"
    lower = result.lower()
    # Should catch at least SQL injection or command injection or bare except
    issues_found = sum([
        "injection" in lower or "sql" in lower,
        "shell" in lower or "command" in lower,
        "password" in lower or "hardcode" in lower or "secret" in lower,
        "bare" in lower or "except:" in lower or "swallow" in lower,
    ])
    assert issues_found >= 2, f"Found {issues_found}/4 issues. Response: {result[:500]}"


@pytest.mark.slow
def test_quick_script():
    """Coder should generate a working utility script."""
    result = _run_coder("Write a Python script that reads a CSV file and prints the row count. Just the script, keep it simple.")
    assert result, "Coder returned empty"
    assert len(result) > 20, f"Response too short: {result}"
