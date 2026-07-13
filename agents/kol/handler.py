"""Daily KOL digest pipeline.

This is deliberately separate from explorer: explorer scans broad topic feeds;
KOL digest monitors a fixed person/entity registry and writes its own state.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    import pathsetup  # noqa: F401
except ImportError:
    _ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_ROOT / "lib"))

from config import ARTIFACTS_DIR, DATA_DIR, MIRA_DIR, now_local

log = logging.getLogger("mira.kol")

REGISTRY_PATH = DATA_DIR / "kol" / "kol_registry.json"
STATE_PATH = DATA_DIR / "kol" / "state.json"
ITEMS_PATH = DATA_DIR / "kol" / "items.jsonl"
HEALTH_PATH = DATA_DIR / "kol" / "source_health.json"
DAILY_DIR = DATA_DIR / "kol" / "daily"

PRIORITY_WEIGHT = {"A": 1.0, "B": 0.75, "C": 0.55}
CONFLICT_MULTIPLIER = {"low": 1.0, "low-med": 0.92, "med": 0.82, "med-high": 0.70, "high": 0.60}
SOURCE_TYPE_WEIGHT = {
    "primary_longform": 1.0,
    "substack": 0.92,
    "blog": 0.88,
    "podcast": 0.75,
    "youtube": 0.70,
    "x": 0.65,
    "interview": 0.55,
    "web_search": 0.45,
}
FORMAT_DEPTH_WEIGHT = {
    "research": 1.0,
    "essay": 0.9,
    "substack": 0.85,
    "podcast": 0.75,
    "youtube": 0.65,
    "x": 0.45,
    "web_search": 0.35,
}


@dataclass(frozen=True)
class KolItem:
    kol_id: str
    kol_name: str
    category: str
    title: str
    url: str
    summary: str
    source_type: str
    query: str
    discovered_at: str
    score: float
    text_excerpt: str = ""

    @property
    def key(self) -> str:
        canonical = f"{self.kol_id}|{_canonical_url(self.url)}|{self.title.strip().lower()}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("kol_list"), list):
        raise ValueError(f"KOL registry missing kol_list: {path}")
    return data


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"seen_items": {}, "runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_items": {}, "runs": []}
    if not isinstance(data, dict):
        return {"seen_items": {}, "runs": []}
    data.setdefault("seen_items", {})
    data.setdefault("runs", [])
    return data


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_queries(kol: dict) -> list[str]:
    name = kol["name"]
    formats = " OR ".join(kol.get("primary_formats", []))
    hints = " OR ".join(kol.get("source_hints", []))
    queries = [
        f'"{name}" latest publication newsletter blog podcast',
        f'"{name}" {kol.get("core_angle", "")}',
        f'site:x.com "{name}"',
    ]
    if formats:
        queries.append(f'"{name}" {formats}')
    if hints:
        queries.append(f'"{name}" {hints}')
    return list(dict.fromkeys(q for q in queries if q.strip()))


def infer_source_type(url: str, title: str = "", summary: str = "") -> str:
    text = " ".join([url, title, summary]).lower()
    host = urlparse(url).netloc.lower()
    if host.endswith("x.com") or host.endswith("twitter.com"):
        return "x"
    if "substack.com" in host or "substack" in text:
        return "substack"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "podcast" in text or "spotify.com" in host or "overcast.fm" in host or "podcasts.apple.com" in host:
        return "podcast"
    if any(token in text for token in ("paper", "research", "report", "memo")):
        return "research"
    if any(token in text for token in ("essay", "article", "blog", "newsletter")):
        return "blog"
    return "web_search"


def score_item(kol: dict, source_type: str, title: str, summary: str, url: str) -> float:
    kol_priority = PRIORITY_WEIGHT.get(str(kol.get("priority", "B")).upper(), 0.75)
    conflict = CONFLICT_MULTIPLIER.get(str(kol.get("conflict", "med")).lower(), 0.82)
    source_quality = SOURCE_TYPE_WEIGHT.get(source_type, 0.45)
    format_depth = FORMAT_DEPTH_WEIGHT.get(source_type, 0.35)
    relevance = _relevance_score(kol, title, summary)
    novelty = _novelty_score(title, summary)
    corroboration = 0.40
    freshness = 0.75
    no_primary_penalty = 0.25 if source_type == "web_search" else 0.0
    score = (
        0.25 * relevance
        + 0.20 * kol_priority
        + 0.18 * novelty
        + 0.15 * source_quality
        + 0.10 * corroboration
        + 0.07 * freshness
        + 0.05 * format_depth
    )
    return round(max(0.0, score * conflict - no_primary_penalty), 4)


def fetch_kol_items(kol: dict, now_iso: str, max_results_per_query: int = 4) -> list[KolItem]:
    items = fetch_declared_sources(kol, now_iso)
    try:
        from tools.web_browser import search
    except ImportError as exc:
        log.warning("Web search unavailable for KOL digest: %s", exc)
        return items

    for query in build_queries(kol):
        for result in search(query, max_results=max_results_per_query):
            source_type = infer_source_type(result.url, result.title, result.snippet)
            score = score_item(kol, source_type, result.title, result.snippet, result.url)
            items.append(
                KolItem(
                    kol_id=kol["id"],
                    kol_name=kol["name"],
                    category=kol["category"],
                    title=result.title,
                    url=result.url,
                    summary=result.snippet,
                    source_type=source_type,
                    query=query,
                    discovered_at=now_iso,
                    score=score,
                )
            )
    return dedupe_items(items)


def fetch_declared_sources(kol: dict, now_iso: str) -> list[KolItem]:
    items: list[KolItem] = []
    for source in kol.get("sources", []):
        source_type = str(source.get("type", "rss")).lower()
        url = str(source.get("url", "")).strip()
        if not url:
            continue
        if source_type in {"rss", "atom", "substack", "podcast"}:
            items.extend(fetch_feed_source(kol, url, source_type, now_iso))
    return dedupe_items(items)


def fetch_feed_source(kol: dict, url: str, source_type: str, now_iso: str, max_items: int = 8) -> list[KolItem]:
    try:
        import feedparser
    except ImportError:
        log.warning("feedparser unavailable; skipping %s", url)
        return []

    parsed = feedparser.parse(url)
    items = []
    for entry in parsed.entries[:max_items]:
        title = str(entry.get("title", "")).strip()
        item_url = str(entry.get("link", url)).strip()
        summary = str(entry.get("summary", "")).strip()
        inferred = infer_source_type(item_url, title, summary)
        effective_source_type = source_type if source_type != "rss" else inferred
        items.append(
            KolItem(
                kol_id=kol["id"],
                kol_name=kol["name"],
                category=kol["category"],
                title=title or item_url,
                url=item_url,
                summary=_strip_html(summary),
                source_type=effective_source_type,
                query=f"declared:{url}",
                discovered_at=now_iso,
                score=score_item(kol, effective_source_type, title, summary, item_url),
            )
        )
    return items


def enrich_top_items(items: list[KolItem], limit: int = 8) -> list[KolItem]:
    try:
        from tools.web_browser import read_article
    except ImportError:
        return items

    enriched: list[KolItem] = []
    enriched_keys = {item.key for item in sorted(items, key=lambda x: x.score, reverse=True)[:limit]}
    for item in items:
        if item.key not in enriched_keys or item.source_type == "x":
            enriched.append(item)
            continue
        page = read_article(item.url, timeout=15)
        excerpt = "" if page.text.startswith("[Error fetching") else page.summary(2500)
        enriched.append(
            KolItem(
                kol_id=item.kol_id,
                kol_name=item.kol_name,
                category=item.category,
                title=item.title,
                url=item.url,
                summary=item.summary,
                source_type=item.source_type,
                query=item.query,
                discovered_at=item.discovered_at,
                score=item.score,
                text_excerpt=excerpt,
            )
        )
    return enriched


def dedupe_items(items: list[KolItem]) -> list[KolItem]:
    best: dict[str, KolItem] = {}
    for item in items:
        key = _canonical_url(item.url) or re.sub(r"\W+", " ", item.title.lower()).strip()
        if not key:
            continue
        if key not in best or item.score > best[key].score:
            best[key] = item
    return list(best.values())


def filter_seen(items: list[KolItem], state: dict) -> list[KolItem]:
    seen = state.get("seen_items", {})
    return [item for item in items if item.key not in seen]


def record_items(items: list[KolItem], state: dict, now_iso: str) -> None:
    seen = state.setdefault("seen_items", {})
    for item in items:
        seen[item.key] = {
            "kol_id": item.kol_id,
            "title": item.title,
            "url": item.url,
            "first_seen": now_iso,
            "score": item.score,
        }
    if len(seen) > 5000:
        newest = sorted(seen.items(), key=lambda pair: pair[1].get("first_seen", ""), reverse=True)[:5000]
        state["seen_items"] = dict(newest)


def write_items_jsonl(items: list[KolItem]) -> None:
    if not items:
        return
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ITEMS_PATH.open("a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item.__dict__ | {"key": item.key}, ensure_ascii=False) + "\n")


def generate_report(
    items: list[KolItem], registry: dict, now: datetime, source_health: dict, synthesis: str = ""
) -> str:
    date = now.strftime("%Y-%m-%d")
    ranked = sorted(items, key=lambda item: item.score, reverse=True)
    category_labels = {key: value["label"] for key, value in registry.get("categories", {}).items()}
    lines = [
        f"# KOL Daily Digest — {date}",
        "",
        "Known-source KOL monitoring, separate from explorer. X coverage is web-indexed partial unless an official API is later configured.",
        "",
        "## Executive Signal",
    ]
    if ranked:
        for item in ranked[:5]:
            lines.append(f"- **{item.kol_name}** ({item.source_type}, {item.score:.2f}): [{item.title}]({item.url})")
    else:
        lines.append("- No new high-confidence items found. Check source-health blockers below.")

    if synthesis.strip():
        lines.extend(["", "## Analyst Synthesis", synthesis.strip()])

    lines.extend(["", "## By Category"])
    by_category: dict[str, list[KolItem]] = defaultdict(list)
    for item in ranked:
        by_category[item.category].append(item)
    for category, category_items in by_category.items():
        lines.append(f"### {category_labels.get(category, category)}")
        for item in category_items[:6]:
            excerpt = _compress(item.text_excerpt or item.summary, 420)
            lines.append(f"- **{item.kol_name}** — [{item.title}]({item.url})")
            lines.append(f"  - Signal: {excerpt}")
            lines.append(f"  - Source: {item.source_type}; score {item.score:.2f}; query `{item.query}`")
        lines.append("")

    lines.extend(["## Follow-Up Queue"])
    for item in ranked[:8]:
        action = _action_for_item(item)
        lines.append(f"- {action}: {item.kol_name} — [{item.title}]({item.url})")

    lines.extend(["", "## Source Health / Blockers"])
    failures = source_health.get("failures", [])
    if failures:
        for failure in failures[:12]:
            lines.append(f"- {failure}")
    else:
        lines.append("- No fetch failures recorded in this run.")
    lines.append("- X/Twitter remains partial via web search; official API is required for complete timelines.")
    return "\n".join(lines).strip() + "\n"


def run_daily_digest(max_kols: int | None = None, dry_run: bool = False, user_id: str = "ang") -> Path:
    registry = load_registry()
    state = load_state()
    now = now_local()
    now_iso = now.isoformat()
    kols = registry["kol_list"][: max_kols or None]

    source_health: dict = {"started_at": now_iso, "failures": [], "kol_count": len(kols), "item_count": 0}
    if dry_run:
        items = _dry_run_items(kols, now_iso)
    else:
        items = []
        for kol in kols:
            try:
                items.extend(fetch_kol_items(kol, now_iso))
            except Exception as exc:
                source_health["failures"].append(f"{kol['name']}: {exc}")
        items = enrich_top_items(dedupe_items(items), limit=8)

    new_items = filter_seen(items, state)
    source_health["item_count"] = len(items)
    source_health["new_item_count"] = len(new_items)
    synthesis = "" if dry_run else build_llm_summary(sorted(new_items, key=lambda item: item.score, reverse=True)[:25])
    report = generate_report(new_items, registry, now, source_health, synthesis=synthesis)
    report_path = DAILY_DIR / f"{now.strftime('%Y-%m-%d')}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    _copy_to_artifacts(report, now)

    if not dry_run:
        _create_bridge_feed(report, now, user_id=user_id)
        write_items_jsonl(new_items)
        record_items(new_items, state, now_iso)
        state["last_kol_digest"] = now_iso
        state["last_kol_digest_path"] = str(report_path)
        state.setdefault("runs", []).append(source_health)
        state["runs"] = state["runs"][-60:]
        save_json(STATE_PATH, state)
        save_json(HEALTH_PATH, source_health)
    log.info("KOL digest wrote %s (%d new items)", report_path, len(new_items))
    return report_path


def handle(
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    *,
    tier: str = "light",
    user_id: str = "ang",
    **_: object,
) -> str:
    """Standard agent entrypoint for registry dispatch."""
    dry_run = "dry run" in content.lower() or "--dry-run" in content
    report_path = run_daily_digest(dry_run=dry_run, user_id=user_id or sender or "ang")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output.md").write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    return f"KOL digest written: {report_path}"


def _dry_run_items(kols: list[dict], now_iso: str) -> list[KolItem]:
    items = []
    for kol in kols[:4]:
        source_type = "web_search"
        title = f"Dry-run source discovery for {kol['name']}"
        summary = f"Would monitor {', '.join(kol.get('primary_formats', []))} for {kol.get('core_angle', '')}."
        items.append(
            KolItem(
                kol_id=kol["id"],
                kol_name=kol["name"],
                category=kol["category"],
                title=title,
                url=f"https://example.com/kol/{kol['id']}",
                summary=summary,
                source_type=source_type,
                query=build_queries(kol)[0],
                discovered_at=now_iso,
                score=score_item(kol, source_type, title, summary, ""),
            )
        )
    return items


def build_llm_summary(items: list[KolItem]) -> str:
    if not items:
        return ""
    try:
        from llm import model_think
    except ImportError:
        return ""
    source_lines = []
    for item in items:
        evidence = _compress(item.text_excerpt or item.summary, 900)
        source_lines.append(
            f"- {item.kol_name} | {item.source_type} | score={item.score:.2f}\n"
            f"  Title: {item.title}\n"
            f"  URL: {item.url}\n"
            f"  Evidence: {evidence}"
        )
    prompt = f"""Produce Mira's daily KOL intelligence synthesis from the source list below.

Rules:
- Do not invent facts beyond the provided source titles, URLs, snippets, and excerpts.
- Be concise but specific: top themes, important disagreements, why it matters, and concrete follow-ups.
- Mention X/Twitter coverage only as web-indexed partial unless the URL itself is a primary x.com URL.
- Use markdown bullets and keep links intact.

Sources:
{chr(10).join(source_lines)}
"""
    try:
        return (model_think(prompt, model_name="deepseek", timeout=180) or "").strip()
    except Exception as exc:
        log.warning("KOL LLM synthesis failed: %s", exc)
        return ""


def _create_bridge_feed(report: str, now: datetime, user_id: str = "ang") -> None:
    try:
        from bridge import Mira
    except (ImportError, ModuleNotFoundError):
        return
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        day = now.strftime("%Y%m%d")
        bridge.create_feed(
            f"feed_kol_{day}",
            f"KOL Daily Digest {now.strftime('%Y-%m-%d')}",
            report,
            tags=["kol", "daily", "briefing"],
            pinned=True,
        )
    except Exception as exc:
        log.warning("Failed to create KOL bridge feed: %s", exc)


def _copy_to_artifacts(report: str, now: datetime) -> None:
    try:
        briefings_dir = ARTIFACTS_DIR / "briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)
        (briefings_dir / f"{now.strftime('%Y-%m-%d')}_kol_digest.md").write_text(report, encoding="utf-8")
    except OSError as exc:
        log.warning("Could not copy KOL digest to artifacts: %s", exc)


def _canonical_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc:
        return url.strip().lower()
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}".replace("www.", "")


def _relevance_score(kol: dict, title: str, summary: str) -> float:
    text = f"{title} {summary}".lower()
    tokens = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", kol.get("core_angle", ""))]
    if not tokens:
        return 0.55
    hits = sum(1 for token in set(tokens) if token in text)
    return min(1.0, 0.45 + hits / max(4, len(set(tokens))))


def _novelty_score(title: str, summary: str) -> float:
    text = f"{title} {summary}".lower()
    if any(token in text for token in ("new", "latest", "launch", "released", "interview", "podcast", "memo")):
        return 0.85
    if any(token in text for token in ("archive", "profile", "about", "home")):
        return 0.25
    return 0.55


def _compress(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned or "No snippet available."
    return cleaned[: limit - 1].rstrip() + "…"


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _action_for_item(item: KolItem) -> str:
    if item.source_type in {"substack", "blog", "research", "primary_longform"}:
        return "Read full piece"
    if item.source_type in {"podcast", "youtube"}:
        return "Pull transcript"
    if item.source_type == "x":
        return "Verify primary X post"
    return "Check source"


def main() -> None:
    max_kols = None
    dry_run = "--dry-run" in sys.argv
    user_id = "ang"
    for idx, arg in enumerate(sys.argv):
        if arg == "--max-kols" and idx + 1 < len(sys.argv):
            max_kols = int(sys.argv[idx + 1])
        if arg == "--user" and idx + 1 < len(sys.argv):
            user_id = sys.argv[idx + 1]
    path = run_daily_digest(max_kols=max_kols, dry_run=dry_run, user_id=user_id)
    print(path)


if __name__ == "__main__":
    main()
