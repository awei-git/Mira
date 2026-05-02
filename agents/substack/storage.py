"""Durable JSON storage for the Substack publisher-operator agent."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from config import SOCIAL_STATE_DIR

from models import ArticleRecord, EditorialPackage, PilotReview, PublicationStrategy, TopicCandidate, utc_now


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
        self.editorial_packages_path = self.root / "editorial_packages.json"
        self.calendar_path = self.root / "editorial_calendar.json"
        self.pilot_reviews_path = self.root / "pilot_reviews.json"

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

    def upsert_articles(self, articles: list[ArticleRecord]) -> tuple[int, int]:
        existing = {article.id: article for article in self.load_articles()}
        created = 0
        updated = 0
        for article in articles:
            if article.id in existing:
                old = existing[article.id]
                article.created_at = old.created_at
                if old.state != "idea":
                    article.state = old.state
                article.updated_at = utc_now()
                updated += 1
            else:
                created += 1
            existing[article.id] = article
        self.save_articles(list(existing.values()))
        return created, updated

    def load_editorial_packages(self) -> list[EditorialPackage]:
        return [
            EditorialPackage.from_dict(item)
            for item in self._load_list(self.editorial_packages_path)
            if isinstance(item, dict) and item.get("topic_id")
        ]

    def save_editorial_packages(self, packages: list[EditorialPackage]) -> None:
        _atomic_write_json(self.editorial_packages_path, [package.to_dict() for package in packages])

    def upsert_editorial_packages(self, packages: list[EditorialPackage]) -> tuple[int, int]:
        existing = {package.topic_id: package for package in self.load_editorial_packages()}
        created = 0
        updated = 0
        for package in packages:
            if package.topic_id in existing:
                package.created_at = existing[package.topic_id].created_at
                package.updated_at = utc_now()
                updated += 1
            else:
                created += 1
            existing[package.topic_id] = package
        self.save_editorial_packages(list(existing.values()))
        return created, updated

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

    def load_pilot_reviews(self) -> list[PilotReview]:
        return [
            PilotReview.from_dict(item)
            for item in self._load_list(self.pilot_reviews_path)
            if isinstance(item, dict) and item.get("id")
        ]

    def save_pilot_reviews(self, reviews: list[PilotReview]) -> None:
        reviews = sorted(reviews, key=lambda item: item.period_end, reverse=True)
        _atomic_write_json(self.pilot_reviews_path, [review.to_dict() for review in reviews])

    def upsert_pilot_review(self, review: PilotReview) -> tuple[int, int]:
        existing = {item.id: item for item in self.load_pilot_reviews()}
        created = 0
        updated = 0
        if review.id in existing:
            review.created_at = existing[review.id].created_at
            review.updated_at = utc_now()
            updated = 1
        else:
            created = 1
        existing[review.id] = review
        self.save_pilot_reviews(list(existing.values()))
        return created, updated

    def _load_list(self, path: Path) -> list[Any]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []
