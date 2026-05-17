"""Codex CLI provider.

This uses the local Codex subscription session instead of an OpenAI API key.
It is intentionally shell-free: prompts are passed as argv and final output is
read from Codex's --output-last-message file.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from config import CODEX_MODEL

log = logging.getLogger("mira")


def _codex_bin() -> str:
    configured = os.environ.get("MIRA_CODEX_BIN", "")
    if configured:
        return configured
    homebrew = Path("/opt/homebrew/bin/codex")
    if homebrew.exists():
        return str(homebrew)
    return "codex"


def _run_codex(
    prompt: str,
    *,
    model_id: str = "",
    cwd: Path | None = None,
    timeout: int = 300,
    sandbox: str = "read-only",
) -> str:
    model = model_id or CODEX_MODEL
    with tempfile.NamedTemporaryFile(prefix="mira-codex-", suffix=".txt", delete=False) as tmp:
        out_path = Path(tmp.name)

    cmd = [
        _codex_bin(),
        "exec",
        "-m",
        model,
        "-s",
        sandbox,
        "--skip-git-repo-check",
        "--output-last-message",
        str(out_path),
    ]
    if cwd:
        cmd.extend(["-C", str(cwd)])
    cmd.append(prompt)

    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE"}}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else "/tmp",
            env=env,
        )
        if result.returncode != 0:
            log.error("Codex CLI failed (exit %d): %s", result.returncode, result.stderr[:500])
            return ""
        try:
            text = out_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if not text:
            stdout = result.stdout.strip()
            failure_markers = (
                "ERROR:",
                "stream error:",
                "unexpected status",
                "thread 'main' panicked",
                "requires a newer version of Codex",
            )
            if any(marker in stdout for marker in failure_markers):
                log.error("Codex CLI produced no final message; stdout looked like an error: %s", stdout[:500])
                return ""
            text = stdout
        log.info("Codex CLI call: %s -> %d chars", model, len(text))
        try:
            from llm import _estimate_tokens, _log_usage

            _log_usage(
                "codex_cli",
                model,
                _estimate_tokens(prompt),
                _estimate_tokens(text),
                estimated=True,
            )
        except Exception as exc:
            log.debug("Codex CLI usage logging failed: %s", exc)
        return text
    except subprocess.TimeoutExpired:
        log.error("Codex CLI timed out (%ds)", timeout)
        return ""
    except FileNotFoundError:
        log.error("Codex CLI not found on PATH")
        return ""
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


def codex_think(prompt: str, model_id: str = "", system: str = "", timeout: int = 300) -> str:
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    return _run_codex(full_prompt, model_id=model_id, timeout=timeout, sandbox="read-only")


def codex_act(prompt: str, cwd: Path | None = None, model_id: str = "", timeout: int = 600) -> str:
    return _run_codex(prompt, model_id=model_id, cwd=cwd, timeout=timeout, sandbox="workspace-write")
