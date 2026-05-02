"""Durable JSON storage for the Substack publisher-operator agent."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from config import SOCIAL_STATE_DIR

from models import ArticleRecord, PublicationStrategy, TopicCandidate, utc_now


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SubstackStore:
    """File-backed store for strategy, topics, articles, and calendars."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or SOCIAL_STATE_DIR / "substack_agent")
        self.strategy_path = self.root / "strategy.json"
        self.topics_path = self.root / "topic_backlog.json"
        self.articles_path = self.root / "articles.json"
        self.calendar_path = self.root / "editorial_calendar.json"

    def load_strategy(self) -> PublicationStrategy:
        if not self.strategy_path.exists():
            strategy = PublicationStrategy()
            self.save_strategy(strategy)
            return strategy
        try:
            return PublicationStrategy.from_dict(json.loads(self.strategy_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            strategy = PublicationStrategy()
            self.save_strategy(strategy)
            return strategy

    def save_strategy(self, strategy: PublicationStrategy) -> None:
        strategy.updated_at = utc_now()
        _atomic_write_json(self.strategy_path, strategy.to_dict())

    def load_topics(self) -> list[TopicCandidate]:
        return [
            TopicCandidate.from_dict(item)
            for item in self._load_list(self.topics_path)
            if isinstance(item, dict) and item.get("id")
        ]

    def save_topics(self, topics: list[TopicCandidate]) -> None:
        topics = sorted(topics, key=lambda item: (-item.priority_score, item.updated_at, item.title))
        _atomic_write_json(self.topics_path, [topic.to_dict() for topic in topics])

    def upsert_topics(self, candidates: list[TopicCandidate]) -> tuple[int, int]:
        existing = {topic.id: topic for topic in self.load_topics()}
        created = 0
        updated = 0
        for candidate in candidates:
            if candidate.id in existing:
                old = existing[candidate.id]
                candidate.created_at = old.created_at
                candidate.status = old.status
                candidate.updated_at = utc_now()
                updated += 1
            else:
                created += 1
            existing[candidate.id] = candidate
        self.save_topics(list(existing.values()))
        return created, updated

    def load_articles(self) -> list[ArticleRecord]:
        return [
            ArticleRecord.from_dict(item)
            for item in self._load_list(self.articles_path)
            if isinstance(item, dict) and item.get("id")
        ]

    def save_articles(self, articles: list[ArticleRecord]) -> None:
        _atomic_write_json(self.articles_path, [article.to_dict() for article in articles])

    def save_calendar(self, calendar: dict[str, Any]) -> None:
        calendar["updated_at"] = utc_now()
        _atomic_write_json(self.calendar_path, calendar)

    def load_calendar(self) -> dict[str, Any]:
        if not self.calendar_path.exists():
            return {"weeks": [], "updated_at": ""}
        try:
            data = json.loads(self.calendar_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"weeks": [], "updated_at": ""}
        except (json.JSONDecodeError, OSError):
            return {"weeks": [], "updated_at": ""}

    def _load_list(self, path: Path) -> list[Any]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []
