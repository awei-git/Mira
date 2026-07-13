from __future__ import annotations


def test_standard_review_requires_verdict_and_receipts():
    from prompts import review_draft_prompt

    prompt = review_draft_prompt("# Draft\n\nI traced my task.", {"argument": "coherent"}, 1)

    assert "VERDICT: HOLD|PASS" in prompt
    assert "P0/P1" in prompt
    assert "claim" in prompt.lower()
    assert "evidence" in prompt.lower()


def test_harsh_review_matches_draft_language_and_requires_verdict():
    from prompts import harsh_review_prompt

    prompt = harsh_review_prompt("# An English Draft\n\nI found a failure.", {"voice": "distinct"}, 1)

    assert "VERDICT: HOLD|PASS" in prompt
    assert "same language as the draft" in prompt
    assert "用中文评审" not in prompt


def test_review_verdict_summary_requires_unanimous_clear_pass():
    from writing_workflow import _review_verdict_summary

    passed = _review_verdict_summary("VERDICT: PASS\nUNRESOLVED_P0_P1: 0\n---\nVERDICT: PASS\nUNRESOLVED_P0_P1: 0")
    held = _review_verdict_summary("VERDICT: PASS\nUNRESOLVED_P0_P1: 0\n---\nVERDICT: HOLD\nUNRESOLVED_P0_P1: 1")

    assert passed["verdict"] == "PASS"
    assert held["verdict"] == "HOLD"
    assert held["unresolved_p0_p1"] == 1
