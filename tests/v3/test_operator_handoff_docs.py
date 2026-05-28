import shlex
import subprocess
import sys
from pathlib import Path


def _handoff_commands() -> list[list[str]]:
    doc_path = Path(__file__).resolve().parents[2] / "docs" / "v31-north-star-remaining-gates-2026-05-21.md"
    commands: list[list[str]] = []
    for line in doc_path.read_text(encoding="utf-8").splitlines():
        if "agents/super/cli/" not in line:
            continue
        commands.append(shlex.split(line))
    return commands


def test_remaining_gates_handoff_cli_commands_are_supported():
    repo_root = Path(__file__).resolve().parents[2]

    for command in _handoff_commands():
        script = next(token for token in command if token.startswith("agents/super/cli/"))
        script_path = repo_root / script
        flags = sorted({token for token in command if token.startswith("--")})

        assert script_path.exists(), script
        help_result = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=True,
        )
        help_text = help_result.stdout
        for flag in flags:
            assert flag in help_text, f"{script} help is missing documented flag {flag}"
