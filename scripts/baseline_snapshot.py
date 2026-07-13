#!/usr/bin/env python3
"""Baseline snapshot for the Hermes integration plan (Phase 0 pre-work).

Non-destructive: reads existing Mira artifacts only, emits a Markdown
report with the numbers the plan uses as its "before" picture.

Usage:
    python scripts/baseline_snapshot.py
    python scripts/baseline_snapshot.py --out /tmp/baseline.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BRIDGE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Bridge"

LOOKBACK_DAYS = 7
ARTICLE_LIMIT = 30


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def recent_date_strs(days: int) -> list[str]:
    today = datetime.now().date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def pct(num: int, denom: int) -> str:
    return f"{(num / denom * 100):.0f}%" if denom else "n/a"


def p90(vals: list[float]) -> float | None:
    if len(vals) < 10:
        return None
    return statistics.quantiles(vals, n=10)[-1]


# --- Sections -----------------------------------------------------------


def section_evolution() -> str:
    d = DATA / "soul" / "experiences"
    if not d.exists():
        return "## Evolution experiences\n\n_No `data/soul/experiences/` directory._\n"
    rows = []
    for date_str in recent_date_strs(LOOKBACK_DAYS):
        rows.extend(iter_jsonl(d / f"{date_str}.jsonl"))
    if not rows:
        return f"## Evolution experiences (past {LOOKBACK_DAYS}d)\n\n_0 records._\n"
    scores = [r.get("score") for r in rows if isinstance(r.get("score"), (int, float))]
    by_agent = Counter(r.get("agent") or "<none>" for r in rows)
    lines = [
        f"## Evolution experiences (past {LOOKBACK_DAYS}d)",
        "",
        f"- total records: **{len(rows)}**",
        f"- by agent: {dict(by_agent.most_common())}",
    ]
    if scores:
        lines.append(
            f"- score min/median/mean/max: "
            f"{min(scores):.2f} / {statistics.median(scores):.2f} / "
            f"{statistics.mean(scores):.2f} / {max(scores):.2f}"
        )
        p = p90(scores)
        if p is not None:
            lines.append(f"- score p90: {p:.2f}")
    return "\n".join(lines) + "\n"


def section_timing() -> str:
    path = DATA / "logs" / "timing.jsonl"
    rows = list(iter_jsonl(path))
    if not rows:
        return "## Orchestration timing\n\n_No `timing.jsonl`._\n"
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    recent = [r for r in rows if (t := parse_ts(r.get("ts"))) and t >= cutoff]
    if not recent:
        return f"## Orchestration timing\n\n_No records in last {LOOKBACK_DAYS}d._\n"
    phase_ms: dict[str, list[float]] = defaultdict(list)
    cycle_ms: list[float] = []
    for r in recent:
        for p, ms in (r.get("phases") or {}).items():
            if isinstance(ms, (int, float)):
                phase_ms[p].append(ms)
        if isinstance(r.get("cycle_ms"), (int, float)):
            cycle_ms.append(r["cycle_ms"])
    lines = [
        f"## Orchestration timing (past {LOOKBACK_DAYS}d, n={len(recent)} cycles)",
        "",
    ]
    if cycle_ms:
        cyc_p90 = p90(cycle_ms)
        p90_str = f" (p90 {cyc_p90:.0f})" if cyc_p90 is not None else ""
        lines.append(f"- cycle_ms median: **{statistics.median(cycle_ms):.0f}**{p90_str}")
    lines += [
        "",
        "| phase | median ms | p90 ms | count |",
        "|---|---|---|---|",
    ]
    for phase, vals in sorted(phase_ms.items(), key=lambda kv: -statistics.median(kv[1])):
        med = statistics.median(vals)
        pp = p90(vals)
        p90_s = f"{pp:.0f}" if pp is not None else "—"
        lines.append(f"| {phase} | {med:.0f} | {p90_s} | {len(vals)} |")
    return "\n".join(lines) + "\n"


def section_substack() -> str:
    path = DATA / "social" / "publication_stats.json"
    if not path.exists():
        return "## Substack\n\n_No `publication_stats.json`._\n"
    data = json.loads(path.read_text())
    articles = data.get("articles", [])[:ARTICLE_LIMIT]
    if not articles:
        return "## Substack\n\n_No articles._\n"
    total_v = sum(a.get("views", 0) for a in articles)
    total_l = sum(a.get("likes", 0) for a in articles)
    total_c = sum(a.get("comments", 0) for a in articles)
    total_r = sum(a.get("restacks", 0) for a in articles)
    zero_eng = sum(1 for a in articles if not (a.get("views", 0) or a.get("likes", 0)))
    lines = [
        f"## Substack (last {len(articles)} articles)",
        "",
        f"- fetched_at: `{data.get('fetched_at', '?')}`",
        f"- totals: views={total_v} likes={total_l} comments={total_c} restacks={total_r}",
        f"- zero-engagement articles: {zero_eng}/{len(articles)} ({pct(zero_eng, len(articles))})",
        f"- mean views/article: {total_v / len(articles):.1f}",
        f"- mean likes/article: {total_l / len(articles):.1f}",
    ]
    return "\n".join(lines) + "\n"


def section_bridge() -> str:
    if not BRIDGE.exists():
        return "## Notes bridge\n\n_Bridge dir not mounted._\n"
    lines = ["## Notes bridge", ""]
    users = BRIDGE / "users"
    if users.exists():
        for udir in sorted(users.iterdir()):
            if not udir.is_dir():
                continue
            items = udir / "items"
            n = len(list(items.iterdir())) if items.exists() else 0
            lines.append(f"- user `{udir.name}`: {n} items in inbox/outbox tree")
    hb = BRIDGE / "heartbeat.json"
    if hb.exists():
        try:
            data = json.loads(hb.read_text())
            lines.append(f"- heartbeat: `{data}`")
        except Exception:
            lines.append("- heartbeat: unreadable")
    return "\n".join(lines) + "\n"


def section_errors() -> str:
    log_dir = DATA / "logs"
    if not log_dir.exists():
        return "## Error logs\n\n_No log dir._\n"
    err_count = 0
    warn_count = 0
    by_logger: Counter[str] = Counter()
    msg_prefixes: Counter[str] = Counter()
    for date_str in recent_date_strs(LOOKBACK_DAYS):
        path = log_dir / f"{date_str}.jsonl"
        for r in iter_jsonl(path):
            level = (r.get("level") or "").upper()
            if level == "ERROR":
                err_count += 1
                by_logger[r.get("logger") or "<?>"] += 1
                msg = (r.get("msg") or "")[:80]
                msg_prefixes[msg] += 1
            elif level == "WARNING":
                warn_count += 1
    lines = [
        f"## Error logs (past {LOOKBACK_DAYS}d)",
        "",
        f"- ERROR rows: **{err_count}**",
        f"- WARNING rows: {warn_count}",
    ]
    if by_logger:
        lines += ["", "Top loggers by ERROR count:"]
        for logger, n in by_logger.most_common(10):
            lines.append(f"- `{logger}`: {n}")
    if msg_prefixes:
        lines += ["", "Top ERROR message prefixes:"]
        for msg, n in msg_prefixes.most_common(10):
            lines.append(f"- ({n}) {msg}")
    # Extra: pipeline_failures.jsonl + security_incidents.jsonl
    for extra in ("pipeline_failures.jsonl", "security_incidents.jsonl", "skill_quarantine.jsonl"):
        p = log_dir / extra
        if p.exists():
            n = sum(1 for _ in iter_jsonl(p))
            lines.append(f"- `{extra}`: {n} total rows")
    return "\n".join(lines) + "\n"


def section_tasks() -> str:
    path = DATA / "tasks" / "history.jsonl"
    rows = list(iter_jsonl(path))
    if not rows:
        return "## Task history\n\n_No `history.jsonl`._\n"
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    recent = [r for r in rows if (t := parse_ts(r.get("started_at"))) and t >= cutoff]
    scope = f"past {LOOKBACK_DAYS}d"
    if not recent:
        recent = rows
        scope = "all time (no rows in lookback)"
    by_status = Counter(r.get("status", "?") for r in recent)
    by_failure = Counter(r.get("failure_class") or "" for r in recent)
    by_failure.pop("", None)
    tag_combos = Counter(",".join(sorted(r.get("tags") or [])) or "<none>" for r in recent)
    lines = [
        f"## Task history ({scope}, n={len(recent)})",
        "",
        f"- status: {dict(by_status)}",
        f"- failure classes: {dict(by_failure) or 'none'}",
        "",
        "Top tag combos:",
    ]
    for combo, n in tag_combos.most_common(10):
        lines.append(f"- `{combo}`: {n}")
    return "\n".join(lines) + "\n"


# --- Entry point --------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser()
    default_out = ROOT / "docs" / "plans" / "hermes-integration" / f"baseline-{datetime.now().date().isoformat()}.md"
    ap.add_argument("--out", type=Path, default=default_out)
    args = ap.parse_args()

    body = [
        "# Baseline snapshot — Hermes integration Phase 0 pre-work",
        "",
        f"- generated: {datetime.now(timezone.utc).isoformat()}",
        f"- source root: `{ROOT}`",
        f"- bridge dir: `{BRIDGE}` (exists: {BRIDGE.exists()})",
        f"- lookback: {LOOKBACK_DAYS} days",
        "",
        "Captured before any Phase 0 code changes. Re-run after each pillar to diff.",
        "",
        "---",
        "",
        section_evolution(),
        section_timing(),
        section_substack(),
        section_bridge(),
        section_errors(),
        section_tasks(),
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(body))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
