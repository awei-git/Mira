"""Substack publisher-operator agent.

This agent owns strategy and workflow. It deliberately delegates live
publishing, stats, comments, and notes to the existing guarded socialmedia
stack so the current Substack momentum is not disrupted.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

from compatibility import check_current_stack
from editorial import build_editorial_packages
from storage import SubstackStore
from topic_backlog import build_editorial_calendar, discover_topics_from_writer_ideas

log = logging.getLogger("substack_agent")
_SOCIALMEDIA_DIR = Path(__file__).resolve().parent.parent / "socialmedia"


def _is_live_socialmedia_request(text: str) -> bool:
    lower = text.lower()
    live_patterns = (
        "publish to substack",
        "post to substack",
        "publish this",
        "publish the article",
        "发布到 substack",
        "发布这篇",
        "发到 substack",
        "post note",
        "substack note",
        "backfill notes",
        "reply to comment",
        "comment on post",
        "回复评论",
    )
    return any(pattern in lower for pattern in live_patterns)


def _delegate_to_socialmedia(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs):
    """Delegate live platform side effects to the existing production handler."""
    social_path = str(_SOCIALMEDIA_DIR)
    if social_path not in sys.path:
        sys.path.insert(0, social_path)
    spec = importlib.util.spec_from_file_location("socialmedia_handler_delegate", _SOCIALMEDIA_DIR / "handler.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load socialmedia handler for live Substack delegation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handle(workspace, task_id, content, sender, thread_id, **kwargs)


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle Substack strategy/planning requests."""
    workspace.mkdir(parents=True, exist_ok=True)
    lower = content.lower()
    store = SubstackStore()

    if _is_live_socialmedia_request(content):
        return _delegate_to_socialmedia(workspace, task_id, content, sender, thread_id, **kwargs)

    if any(token in lower for token in ("compat", "current stack", "cover current", "status")):
        report = check_current_stack()
        return _write_output(workspace, _format_compatibility_report(report))

    if any(token in lower for token in ("calendar", "plan", "topic", "growth", "monetization", "substack")):
        strategy = store.load_strategy()
        candidates = discover_topics_from_writer_ideas(strategy)
        created, updated = store.upsert_topics(candidates)
        topics = store.load_topics()
        packages = build_editorial_packages(topics, strategy)
        pkg_created, pkg_updated = store.upsert_editorial_packages(packages)
        calendar = build_editorial_calendar(topics)
        store.save_calendar(calendar)
        report = _format_plan(
            strategy,
            topics,
            calendar,
            packages=packages,
            created=created,
            updated=updated,
            package_created=pkg_created,
            package_updated=pkg_updated,
        )
        return _write_output(workspace, report)

    strategy = store.load_strategy()
    topics = store.load_topics()
    packages = store.load_editorial_packages()
    calendar = store.load_calendar()
    if not topics:
        candidates = discover_topics_from_writer_ideas(strategy)
        store.upsert_topics(candidates)
        topics = store.load_topics()
        packages = build_editorial_packages(topics, strategy)
        store.upsert_editorial_packages(packages)
        calendar = build_editorial_calendar(topics)
        store.save_calendar(calendar)
    return _write_output(
        workspace,
        _format_plan(
            strategy,
            topics,
            calendar,
            packages=packages,
            created=0,
            updated=0,
            package_created=0,
            package_updated=0,
        ),
    )


def _write_output(workspace: Path, text: str) -> str:
    (workspace / "output.md").write_text(text, encoding="utf-8")
    return text


def _format_compatibility_report(report: dict) -> str:
    lines = [
        "# Substack Agent Compatibility Report",
        "",
        f"Current production stack available: {'yes' if report.get('ok') else 'no'}",
        "",
        "The new Substack agent is an orchestrator. It must preserve these existing capabilities:",
        "",
    ]
    for name, capability in sorted(report.get("capabilities", {}).items()):
        status = "ok" if capability.get("present") else "missing"
        lines.append(f"- {status}: {name} -> {capability.get('module')}.{capability.get('function')}")
        if capability.get("reason"):
            lines.append(f"  reason: {capability['reason']}")
    lines.extend(
        [
            "",
            "Policy: live publishing remains delegated to `agents/socialmedia/substack_publish.py`.",
        ]
    )
    return "\n".join(lines)


def _format_plan(
    strategy,
    topics,
    calendar: dict,
    *,
    packages,
    created: int,
    updated: int,
    package_created: int,
    package_updated: int,
) -> str:
    top = topics[:10]
    packages_by_topic = {package.topic_id: package for package in packages}
    lines = [
        "# Substack Publisher Plan",
        "",
        "## Mission",
        strategy.mission,
        "",
        "## Positioning",
        strategy.positioning,
        "",
        "## Operating Policy",
        "- Current guarded publishing stack remains production.",
        "- New agent starts in shadow/orchestrator mode.",
        "- Article publishing requires existing writer gate, preflight, cooldown, and approval policy.",
        "- Paid/account/payment changes always require human approval.",
        "",
        "## Topic Backlog Refresh",
        f"- Created: {created}",
        f"- Updated: {updated}",
        f"- Active topics: {len(topics)}",
        f"- Editorial packages created: {package_created}",
        f"- Editorial packages updated: {package_updated}",
        "",
        "## Highest Priority Topics",
    ]
    for idx, topic in enumerate(top, 1):
        lines.extend(
            [
                f"{idx}. {topic.title}",
                f"   - Pillar: {topic.pillar}",
                f"   - Score: {topic.priority_score} "
                f"(originality={topic.originality_score}, fit={topic.audience_fit_score}, monetization={topic.monetization_score})",
                f"   - Thesis: {topic.thesis[:280]}",
                f"   - Mira edge: {topic.mira_edge}",
            ]
        )
        package = packages_by_topic.get(topic.id)
        if package:
            lines.extend(
                [
                    f"   - Recommended title: {package.recommended_title}",
                    f"   - Abstract: {package.abstract[:360]}",
                    f"   - Editorial gate: {'pass' if package.pass_gate else 'blocked'} {package.quality_scores}",
                ]
            )

    lines.extend(["", "## Editorial Quality Gates"])
    lines.extend(
        [
            "- Title must create curiosity or tension; generic summary titles are blocked.",
            "- Abstract must promise a specific reader payoff and make Mira's unique evidence clear.",
            "- Format must open with a concrete scene, then move into mechanism, framework, and unresolved tension.",
            "- Drafts cannot advance if they lack Mira-specific operating evidence.",
            "- Publishing remains blocked until writer gate, preflight, cooldown, and approval policy pass.",
        ]
    )

    lines.extend(["", "## Four Week Calendar"])
    for week in calendar.get("weeks", []):
        topic = week.get("primary_article") or {}
        title = topic.get("title", "No topic selected")
        lines.extend(
            [
                f"- {week.get('week_start')}: {title}",
                "  - Promotion: 3 Notes, 3 substantive comments, reply to meaningful comments.",
            ]
        )

    lines.extend(
        [
            "",
            "## Next Implementation Steps",
            "1. Add article workflow records that move topics through thesis, draft, review, fact-check, approval, publish, promote, measure.",
            "2. Wire the editorial package into writer prompts so every draft starts from title, abstract, hook, and format blueprint.",
            "3. Add promotion plan generator per published post.",
            "4. Add weekly metrics review that turns weak growth metrics into backlog actions.",
            "",
            "## Machine State",
            "```json",
            json.dumps({"calendar": calendar}, ensure_ascii=False, indent=2)[:6000],
            "```",
        ]
    )
    return "\n".join(lines)
