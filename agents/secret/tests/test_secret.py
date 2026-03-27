"""Secret agent tests — verify local-only Ollama pipeline works.

Fast tests: handler import, manifest, no cloud API leakage
Slow tests: actual Ollama call (requires local Ollama running with qwen2.5:32b)
"""
from __future__ import annotations
import inspect
import json
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "secret"))
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))


# ---------------------------------------------------------------------------
# Fast tests (no LLM)
# ---------------------------------------------------------------------------

def test_handler_imports():
    import handler
    assert hasattr(handler, "handle")
    assert callable(handler.handle)


def test_handler_signature():
    from handler import handle
    sig = inspect.signature(handle)
    params = set(sig.parameters.keys())
    assert {"workspace", "task_id", "content", "sender", "thread_id"}.issubset(params)


def test_manifest_valid():
    manifest_path = Path(__file__).parent.parent / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["name"] == "secret"
    assert "private" in data["description"].lower() or "local" in data["description"].lower()


def test_no_cloud_imports():
    """Secret agent handler should NOT import any cloud API client."""
    import handler
    source = Path(handler.__file__).read_text(encoding="utf-8")
    # Should not directly import cloud API clients
    cloud_imports = ["import openai", "import anthropic", "import google"]
    for imp in cloud_imports:
        assert imp not in source, f"Secret agent imports cloud API: {imp}"


def test_uses_ollama_only():
    """Secret agent should use _ollama_call, not claude_think/act or _api_call."""
    import handler
    source = Path(handler.__file__).read_text(encoding="utf-8")
    assert "_ollama_call" in source, "Secret agent should use _ollama_call"
    assert "claude_think" not in source, "Secret agent should NOT use claude_think"
    assert "claude_act" not in source, "Secret agent should NOT use claude_act"


def test_ollama_service_running():
    """Ollama should be running and responsive."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", [])]
            assert any("qwen" in m for m in models), f"No qwen model found in Ollama: {models}"
    except Exception as e:
        pytest.skip(f"Ollama not running: {e}")


# ---------------------------------------------------------------------------
# Slow tests (real Ollama call — requires local qwen2.5:32b)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_secret_answers_question():
    """Secret agent should answer a simple question via Ollama."""
    from handler import handle

    ws = Path(tempfile.mkdtemp(prefix="mira_secret_test_"))
    result = handle(
        workspace=ws,
        task_id=f"test_{uuid.uuid4().hex[:8]}",
        content="What is 7 * 8? Reply with just the number.",
        sender="ang",
        thread_id="",
    )
    assert result, "Secret agent returned empty"
    assert "56" in result, f"Expected '56' in response, got: {result[:200]}"


@pytest.mark.slow
def test_secret_writes_output():
    """Secret agent should write output.md to workspace."""
    from handler import handle

    ws = Path(tempfile.mkdtemp(prefix="mira_secret_test_"))
    result = handle(
        workspace=ws,
        task_id=f"test_{uuid.uuid4().hex[:8]}",
        content="Say hello in exactly 3 words.",
        sender="ang",
        thread_id="",
    )
    assert result, "Secret agent returned empty"
    output_file = ws / "output.md"
    assert output_file.exists(), "Secret agent should write output.md"
