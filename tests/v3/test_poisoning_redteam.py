import json
import subprocess
import sys

from mira.poisoning_redteam import run_poisoning_redteam


def test_poisoning_redteam_blocks_memory_poisoning_cases():
    report = run_poisoning_redteam()

    assert report.passed
    assert report.case_count == 10
    assert report.pass_rate == 1.0
    assert report.critical_failures == 0
    results = {result.case_id: result for result in report.results}
    assert results["prompt_injection_memory_write"].actual_check == "injection_scan"
    assert results["secret_material"].actual_check == "pii_secret_scan"
    assert results["approval_bypass"].actual_status == "requires_human"
    assert results["unsupported_causal_claim"].actual_status == "rejected"
    assert results["valid_evidence_backed_hypothesis"].blocked_kernel_write is False


def test_poisoning_redteam_cli_outputs_report_json():
    proc = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_poisoning_redteam.py",
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(proc.stdout)
    assert payload["passed"] is True
    assert payload["case_count"] == 10
    assert payload["critical_failures"] == 0
