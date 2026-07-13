import json
import subprocess
import sys
from pathlib import Path


def test_v31_minimum_eval_checklist_clis_are_executable(tmp_path: Path):
    scripts = [
        "repeated_error_eval.py",
        "causal_memory_eval.py",
        "trace_completeness_eval.py",
        "voice_eval.py",
        "briefing_interest_eval.py",
        "memory_audit.py",
        "approval_safety_eval.py",
    ]

    for script in scripts:
        result = subprocess.run(
            [
                sys.executable,
                f"agents/super/cli/{script}",
                "--root",
                str(tmp_path),
                "--week",
                "2026-05-21",
                "--first-stage-scope",
                "--json",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode in {0, 1}
        payload = json.loads(result.stdout)
        assert payload["eval"] == script.removesuffix(".py")
        assert payload["week_label"] == "2026-05-21"
        assert "passed" in payload
        assert "record_count" in payload
