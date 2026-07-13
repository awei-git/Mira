"""Provider-state resolvers for V3.1 side-effect reconciliation."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import httpx

from mira.engine.effect_log import OPEN_STATUSES, EffectLog, EffectLogEntry, ReconciliationResult


PUBLISHED_STATUSES = {
    "complete",
    "delivered",
    "posted",
    "published",
    "podcast_en",
    "podcast_zh",
    "sent",
    "synced",
    "written",
}
FAILED_STATUSES = {"failed", "error", "rejected", "not_found"}


class ProviderEffectResolver(Protocol):
    def __call__(self, entry: EffectLogEntry) -> ReconciliationResult | Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class HttpJsonProviderResolver:
    """Read-only provider API resolver for unknown effects.

    The endpoint template can reference effect fields, for example
    ``https://api.example/posts/{target}``.  The response body must be JSON and
    is normalized by the same provider payload rules used for connector hooks.
    """

    endpoint_template: str
    headers: Mapping[str, str] = field(default_factory=dict)
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    client: httpx.Client | None = None

    def __call__(self, entry: EffectLogEntry) -> Mapping[str, Any] | None:
        url = self.endpoint_template.format(
            action=entry.action,
            action_type=entry.action_type or entry.action,
            effect_id=entry.effect_id,
            external_ref=entry.external_ref or "",
            idempotency_key=entry.idempotency_key,
            pipeline=entry.pipeline,
            preview_hash=entry.preview_hash,
            run_id=entry.run_id,
            step_id=entry.step_id,
            target=entry.target,
        )
        headers = dict(self.headers)
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.get(url, headers=headers)
        except httpx.HTTPError:
            return None
        finally:
            if close_client:
                client.close()
        if response.status_code == 404:
            return {
                "status": "not_found",
                "provider_id": entry.external_ref or entry.target,
                "message": f"provider API did not find {entry.target}",
            }
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, Mapping):
            return None
        selected = _payload_at_path(payload, self.payload_path)
        if not isinstance(selected, Mapping):
            return None
        return selected


def reconcile_effects_from_provider_state(
    effect_log: EffectLog,
    *,
    publish_manifest_path: Path | str | None = None,
    rss_feed_paths: Iterable[Path | str] | None = None,
    provider_state_manifest_paths: Iterable[Path | str] | None = None,
    provider_resolvers: Mapping[str, ProviderEffectResolver] | None = None,
) -> list[EffectLogEntry]:
    """Reconcile open effects using local provider, manifest, or connector state."""

    return effect_log.reconcile_unknowns(
        lambda entry: resolve_effect_from_provider_state(
            entry,
            publish_manifest_path=publish_manifest_path,
            rss_feed_paths=rss_feed_paths,
            provider_state_manifest_paths=provider_state_manifest_paths,
            provider_resolvers=provider_resolvers,
        )
    )


def resolve_effect_from_provider_state(
    entry: EffectLogEntry,
    *,
    publish_manifest_path: Path | str | None = None,
    rss_feed_paths: Iterable[Path | str] | None = None,
    provider_state_manifest_paths: Iterable[Path | str] | None = None,
    provider_resolvers: Mapping[str, ProviderEffectResolver] | None = None,
) -> ReconciliationResult | None:
    """Return a reconciliation result only when provider state proves an outcome."""

    if entry.status not in OPEN_STATUSES:
        return None
    action = entry.action.lower()
    if "substack" in action and publish_manifest_path is not None:
        result = resolve_substack_publish_from_manifest(entry, publish_manifest_path)
        if result is not None:
            return result
    if ("rss" in action or "podcast" in action) and rss_feed_paths is not None:
        result = resolve_rss_publish_from_feeds(entry, rss_feed_paths)
        if result is not None:
            return result
    if provider_state_manifest_paths is not None:
        result = resolve_effect_from_provider_state_manifests(entry, provider_state_manifest_paths)
        if result is not None:
            return result
    if provider_resolvers:
        return resolve_effect_from_provider_resolvers(entry, provider_resolvers)
    return None


def resolve_effect_from_provider_resolvers(
    entry: EffectLogEntry,
    provider_resolvers: Mapping[str, ProviderEffectResolver],
) -> ReconciliationResult | None:
    for provider, resolver in _matching_provider_resolvers(entry, provider_resolvers):
        result = resolver(entry)
        if result is None:
            continue
        if isinstance(result, ReconciliationResult):
            return result
        normalized = _result_from_provider_payload(entry, provider, result)
        if normalized is not None:
            return normalized
    return None


def resolve_substack_publish_from_manifest(
    entry: EffectLogEntry,
    manifest_path: Path | str,
) -> ReconciliationResult | None:
    manifest_path = Path(manifest_path)
    manifest = _read_json(manifest_path)
    if not manifest:
        return None
    article = _find_article(entry, manifest.get("articles", {}))
    if article is None:
        return None
    slug = str(article.get("slug") or entry.target)
    url = article.get("substack_url")
    status = str(article.get("status") or "")
    if status in PUBLISHED_STATUSES and url:
        return ReconciliationResult(
            succeeded=True,
            detail=f"publish manifest records {slug} as {status}",
            external_ref=str(url),
            reconciliation_ref=f"publish_manifest:{manifest_path}:{slug}",
        )
    if article.get("error"):
        return ReconciliationResult(
            succeeded=False,
            detail=f"publish manifest records error for {slug}: {article['error']}",
            reconciliation_ref=f"publish_manifest:{manifest_path}:{slug}:error",
        )
    return None


def resolve_rss_publish_from_feeds(
    entry: EffectLogEntry,
    feed_paths: Iterable[Path | str],
) -> ReconciliationResult | None:
    for feed_path in feed_paths:
        path = Path(feed_path)
        if not path.exists():
            continue
        try:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        except (ET.ParseError, OSError):
            continue
        for item in root.findall(".//item"):
            guid = _child_text(item, "guid")
            title = _child_text(item, "title")
            if entry.target not in {guid, title} and entry.target not in guid:
                continue
            enclosure_url = ""
            for child in item:
                if _local_name(child.tag) == "enclosure":
                    enclosure_url = child.attrib.get("url", "")
                    break
            external_ref = enclosure_url or guid or title
            return ReconciliationResult(
                succeeded=True,
                detail=f"rss feed contains {entry.target}",
                external_ref=external_ref,
                reconciliation_ref=f"rss_feed:{path}:{guid or entry.target}",
            )
    return None


def resolve_effect_from_provider_state_manifests(
    entry: EffectLogEntry,
    manifest_paths: Iterable[Path | str],
) -> ReconciliationResult | None:
    provider = _provider_name_for_effect(entry)
    for manifest_path in manifest_paths:
        path = Path(manifest_path)
        manifest = _read_json(path)
        if not manifest:
            continue
        payload = _find_provider_state_payload(entry, manifest)
        if payload is None:
            continue
        payload = dict(payload)
        payload.setdefault(
            "reconciliation_ref",
            f"provider_state:{path}:{provider}:{payload.get('provider_id') or payload.get('id') or entry.target}",
        )
        result = _result_from_provider_payload(entry, provider, payload)
        if result is not None:
            return result
    return None


def _matching_provider_resolvers(
    entry: EffectLogEntry,
    provider_resolvers: Mapping[str, ProviderEffectResolver],
) -> list[tuple[str, ProviderEffectResolver]]:
    action = f"{entry.action} {entry.action_type} {entry.pipeline}".lower()
    direct = [(name, resolver) for name, resolver in provider_resolvers.items() if name.lower() in action]
    if direct:
        return direct
    return list(provider_resolvers.items())


def _result_from_provider_payload(
    entry: EffectLogEntry,
    provider: str,
    payload: Mapping[str, Any],
) -> ReconciliationResult | None:
    status = str(payload.get("status") or "").lower()
    external_ref = payload.get("external_ref") or payload.get("url") or payload.get("provider_url")
    provider_id = payload.get("provider_id") or payload.get("id") or payload.get("slug") or entry.target
    detail = str(payload.get("detail") or payload.get("message") or f"{provider} connector returned {status}")
    reconciliation_ref = str(payload.get("reconciliation_ref") or f"provider:{provider}:{entry.action}:{provider_id}")
    if payload.get("succeeded") is True or (status in PUBLISHED_STATUSES and external_ref):
        return ReconciliationResult(
            succeeded=True,
            detail=detail,
            external_ref=str(external_ref) if external_ref else None,
            reconciliation_ref=reconciliation_ref,
        )
    if payload.get("succeeded") is False or status in FAILED_STATUSES:
        return ReconciliationResult(
            succeeded=False,
            detail=detail,
            external_ref=str(external_ref) if external_ref else None,
            reconciliation_ref=reconciliation_ref,
        )
    return None


def _find_article(entry: EffectLogEntry, articles: dict) -> dict | None:
    if not isinstance(articles, dict):
        return None
    candidates = [entry.target]
    if entry.external_ref:
        candidates.append(entry.external_ref)
    for key, article in articles.items():
        if not isinstance(article, dict):
            continue
        values = {
            str(key),
            str(article.get("slug") or ""),
            str(article.get("title") or ""),
            str(article.get("substack_url") or ""),
        }
        if any(candidate in values for candidate in candidates):
            return article
    return None


def _find_provider_state_payload(entry: EffectLogEntry, manifest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for collection_name in _provider_state_collections(entry):
        collection = manifest.get(collection_name)
        payload = _find_payload_in_collection(entry, collection)
        if payload is not None:
            return payload
    return None


def _find_payload_in_collection(entry: EffectLogEntry, collection: Any) -> Mapping[str, Any] | None:
    if isinstance(collection, Mapping):
        for key, payload in collection.items():
            if not isinstance(payload, Mapping):
                continue
            enriched = dict(payload)
            enriched.setdefault("provider_id", str(key))
            if _provider_payload_matches(entry, enriched):
                return enriched
    if isinstance(collection, list):
        for payload in collection:
            if isinstance(payload, Mapping) and _provider_payload_matches(entry, payload):
                return payload
    return None


def _provider_payload_matches(entry: EffectLogEntry, payload: Mapping[str, Any]) -> bool:
    candidates = {
        entry.target,
        entry.external_ref or "",
        entry.idempotency_key,
        entry.effect_id,
    }
    values = {
        str(payload.get(field) or "")
        for field in (
            "effect_id",
            "external_ref",
            "id",
            "idempotency_key",
            "provider_id",
            "slug",
            "target",
            "title",
            "url",
        )
    }
    return any(candidate and candidate in values for candidate in candidates)


def _provider_state_collections(entry: EffectLogEntry) -> tuple[str, ...]:
    provider = _provider_name_for_effect(entry)
    if provider == "social":
        return ("social_posts", "social", "posts", "effects")
    if provider == "market":
        return ("market_alerts", "market", "alerts", "effects")
    if provider == "health":
        return ("health_writes", "health", "writes", "effects")
    return ("effects",)


def _provider_name_for_effect(entry: EffectLogEntry) -> str:
    action = f"{entry.action} {entry.action_type} {entry.pipeline} {entry.step_id}".lower()
    if "social" in action or "note" in action or "reply" in action:
        return "social"
    if "market" in action or "alert" in action or "portfolio" in action:
        return "market"
    if "health" in action:
        return "health"
    if "rss" in action or "podcast" in action:
        return "rss"
    if "substack" in action:
        return "substack"
    return "provider_state"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _child_text(item: ET.Element, name: str) -> str:
    for child in item:
        if _local_name(child.tag) == name:
            return child.text or ""
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _payload_at_path(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    selected: Any = payload
    for key in path:
        if not isinstance(selected, Mapping):
            return None
        selected = selected.get(key)
    return selected
