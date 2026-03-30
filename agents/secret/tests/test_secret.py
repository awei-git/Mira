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


def _load_secret_handler():
    """Import secret handler explicitly to avoid sys.path collisions."""
    import importlib.util
    handler_path = _AGENTS / "secret" / "handler.py"
    spec = importlib.util.spec_from_file_location("secret_handler", handler_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fast tests (no LLM)
# ---------------------------------------------------------------------------

def test_handler_imports():
    handler = _load_secret_handler()
    assert hasattr(handler, "handle")
    assert callable(handler.handle)


def test_handler_signature():
    handler = _load_secret_handler()
    sig = inspect.signature(handler.handle)
    params = set(sig.parameters.keys())
    assert {"workspace", "task_id", "content", "sender", "thread_id"}.issubset(params)


def test_manifest_valid():
    manifest_path = Path(__file__).parent.parent / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["name"] == "secret"
    assert "private" in data["description"].lower() or "local" in data["description"].lower()


def test_no_cloud_imports():
    """Secret agent handler should NOT import any cloud API client."""
    handler = _load_secret_handler()
    source = Path(handler.__file__).read_text(encoding="utf-8")
    cloud_imports = ["import openai", "import anthropic", "import google"]
    for imp in cloud_imports:
        assert imp not in source, f"Secret agent imports cloud API: {imp}"


def test_uses_ollama_only():
    """Secret agent should use _ollama_call, not claude_think/act or _api_call."""
    handler = _load_secret_handler()
    source = Path(handler.__file__).read_text(encoding="utf-8")
    assert "_ollama_call" in source, "Secret agent should use _ollama_call"
    assert "claude_think" not in source, "Secret agent should NOT use claude_think"
    assert "claude_act" not in source, "Secret agent should NOT use claude_act"


def test_privacy_routing_keywords():
    """Privacy keywords should trigger local routing."""
    sys.path.insert(0, str(_AGENTS / "super"))
    from task_worker import _is_private_task

    # Should route to secret
    assert _is_private_task("帮我算一下税"), "税务 should be private"
    assert _is_private_task("my password is broken"), "password should be private"
    assert _is_private_task("I need to discuss a family matter"), "family matter should be private"
    assert _is_private_task("请帮我处理隐私信息"), "隐私 should be private"
    assert _is_private_task("what's my salary breakdown"), "salary should be private"
    assert _is_private_task("help me with my medical records"), "medical should be private"

    # Should NOT route to secret (normal tasks)
    assert not _is_private_task("写一篇关于AI的文章"), "normal writing should not be private"
    assert not _is_private_task("what is 2+2"), "math should not be private"
    assert not _is_private_task("搜索最新的机器学习论文"), "research should not be private"


def test_handler_no_disk_persistence():
    """Secret handler should NOT write output.md."""
    source = Path(_AGENTS / "secret" / "handler.py").read_text(encoding="utf-8")
    assert "output.md" not in source or "Do NOT write" in source, \
        "Secret handler should not persist output to disk"


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
