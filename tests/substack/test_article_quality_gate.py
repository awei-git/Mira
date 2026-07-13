"""Tests for the post-draft Substack article quality gate."""

from __future__ import annotations

import sys
from pathlib import Path

_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_quality_gate_passes_source_backed_mira_article():
    from article_quality_gate import evaluate_article_quality

    article = """# My Agent Said Done Before It Had Proof

Last week I showed my human a task as working for hours after the worker had already failed. I only caught it because the app stayed cheerful while the logs were dead.

The useful lesson was not that agents fail. Everyone knows that. The useful lesson was that the interface can become the lie if the status model treats activity as evidence.

## What The Status Hid

I traced the thread through the task record, the bridge item, and the app reply path. The failure was not a missing model call. It was a state transition that made a human-visible promise before verification existed.

## A Better Rule

A reliable agent needs a standard: done means the observable user outcome exists. If the outcome is not checked, the honest state is still running or unverified.
"""
    report = evaluate_article_quality(
        article_text=article,
        subtitle="The app looked settled because the status model confused activity with proof.",
        reader_promise="A practical standard for deciding when an agent task can honestly be marked done.",
        evidence_ledger=[
            {"task_id": "health_today_ang", "source": "control_db"},
            {"source": "bridge_item", "path": "redacted"},
            {"source": "runtime_log", "task_id": "health_today_ang"},
        ],
    )

    assert report.pass_gate
    assert report.quality_scores["title"] >= 8
    assert report.quality_scores["opening"] >= 8


def test_quality_gate_blocks_generic_unsourced_article():
    from article_quality_gate import evaluate_article_quality

    article = """# Thoughts and Reflections

In this essay, I explore the architecture of trust in modern AI systems.

I ran 8 experiments and my pipeline proved that reliability is important.
"""
    report = evaluate_article_quality(article_text=article, subtitle="")

    assert not report.pass_gate
    assert any("title score" in reason for reason in report.blocking_reasons)
    assert any("evidence ledger" in reason for reason in report.blocking_reasons)
    assert any("subtitle is required" in reason for reason in report.blocking_reasons)
    assert any("first-hand operational experience" in reason for reason in report.blocking_reasons)


def test_quality_gate_blocks_non_english_substack_article():
    from article_quality_gate import evaluate_article_quality

    article = """# I Tested My Monitor

今天我发现 monitor 又没有改变我的行为。这个失败很诚实，但如果直接发到 Substack，就是违反 English-only rule。
"""

    report = evaluate_article_quality(
        article_text=article,
        subtitle="A short field note about a monitor that collected signals without changing action.",
        reader_promise="A first-person operational lesson about agent monitoring.",
        evidence_ledger=[{"source": "daily_collab", "task_id": "disc_daily_collab"}],
    )

    assert not report.pass_gate
    assert any("English-only" in reason for reason in report.blocking_reasons)


def test_quality_gate_blocks_third_person_mira_voice():
    from article_quality_gate import evaluate_article_quality

    article = """# Why Mira Failed Her Human

Mira was an autonomous agent that needed better trust protocols. The system broke because receipts were not enough.
"""

    report = evaluate_article_quality(
        article_text=article,
        subtitle="A field note about trust protocols after an operational failure.",
        reader_promise="A first-person operational lesson about trust.",
        evidence_ledger=[{"source": "runtime_log", "task_id": "disc_daily_collab"}],
    )

    assert not report.pass_gate
    assert any("first-person Mira voice" in reason for reason in report.blocking_reasons)
    assert any("outside" in reason for reason in report.blocking_reasons)


def test_quality_gate_reads_workspace_packet(tmp_path: Path):
    from article_quality_gate import evaluate_workspace_article, write_article_packet

    write_article_packet(
        tmp_path,
        {
            "subtitle": "The app looked settled because activity was mistaken for proof.",
            "reader_promise": "A practical standard for marking agent work done.",
            "evidence_ledger": [
                {"task_id": "req_1", "source": "control_db"},
                {"path": "redacted", "source": "bridge_item"},
            ],
        },
    )
    article = """# My Agent Said Done Before It Had Proof

Today I traced a Mira task through the app, the thread, and the task record after the interface showed work that had already stopped.

The lesson is a rule: done only means done after the user-visible outcome exists. Before that, it is theater.
"""

    report = evaluate_workspace_article(workspace=tmp_path, article_text=article)

    assert report.subtitle
    assert report.evidence_ledger


def test_quality_gate_rejects_partial_claim_linking():
    from article_quality_gate import evaluate_article_quality

    article = """# I Found Two Failures In My Agent

Last week I traced a failed task through my app and found the visible status had lied. I also ran a second review and found my memory had stored the proposal as if it were a lesson.

The rule is simple: a proposal needs an outcome before it becomes memory.
"""
    report = evaluate_article_quality(
        article_text=article,
        subtitle="Two operational receipts changed how I let my agent claim that it learned.",
        reader_promise="A test for separating self-improvement activity from actual learning.",
        evidence_ledger=[
            {
                "claim": "I traced a failed task through my app",
                "source": "task_trace",
                "task_id": "req-1",
            }
        ],
    )

    assert not report.pass_gate
    assert any("claim-linked evidence" in reason for reason in report.blocking_reasons)
