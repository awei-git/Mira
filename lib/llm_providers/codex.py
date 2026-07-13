"""Codex CLI provider.

This uses the local Codex subscription session instead of an OpenAI API key.
It is intentionally shell-free: prompts are passed as argv and final output is
read from Codex's --output-last-message file.
"""

from __future__ import annotations

import logging
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import CODEX_MODEL, STATE_DIR

log = logging.getLogger("mira")

_CODEX_CIRCUIT_FILE = STATE_DIR / "api_provider_circuit.json"
_CODEX_PROVIDER_KEY = "codex_cli"


def _load_provider_circuit() -> dict:
    try:
        return json.loads(_CODEX_CIRCUIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_provider_circuit(data: dict) -> None:
    try:
        _CODEX_CIRCUIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CODEX_CIRCUIT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_CODEX_CIRCUIT_FILE)
    except OSError as exc:
        log.debug("Codex CLI circuit save failed: %s", exc)


def codex_circuit_open() -> bool:
    entry = _load_provider_circuit().get(_CODEX_PROVIDER_KEY, {})
    until = str(entry.get("disabled_until") or "")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < until_dt.astimezone(timezone.utc)


def _open_codex_circuit(reason: str, *, hours: int = 2) -> None:
    data = _load_provider_circuit()
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    data[_CODEX_PROVIDER_KEY] = {
        "reason": reason[:300],
        "disabled_until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_provider_circuit(data)


def _safe_error_excerpt(stderr: str) -> str:
    """Return a Codex failure excerpt without echoing the user prompt."""
    text = str(stderr or "")
    for marker in ("\nuser\n", "\n--------\nuser", "\nUSER\n"):
        if marker in text:
            text = text.split(marker, 1)[0]
            break
    return text[:500]


def _codex_circuit_reason(stderr: str) -> str:
    safe = _safe_error_excerpt(stderr)
    lower = safe.lower()
    if any(signal in lower for signal in ("usage limit", "quota", "too many requests", "rate limit")):
        return safe.strip() or "Codex CLI quota/rate limit"
    return ""


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
        "--ephemeral",
        "--output-last-message",
        str(out_path),
    ]
    if cwd:
        cmd.extend(["-C", str(cwd)])
    cmd.append("-")

    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE"}}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            input=prompt,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else "/tmp",
            env=env,
        )
        if result.returncode != 0:
            reason = _codex_circuit_reason(result.stderr)
            if reason:
                _open_codex_circuit(reason)
            log.error("Codex CLI failed (exit %d): %s", result.returncode, _safe_error_excerpt(result.stderr))
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
