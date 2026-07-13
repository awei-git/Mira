"""Runtime wiring for Mira V3.

This module is the bridge between the legacy super-agent runtime and the new
memory-first package. It intentionally keeps side effects narrow: load stores,
write experience records, and run the first migrated pipeline.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
from os import getenv
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping
import xml.etree.ElementTree as ET

import httpx

from mira.engine import (
    ApprovalStore,
    EffectLog,
    HttpJsonProviderResolver,
    PipelineExecutor,
    ProviderEffectResolver,
    reconcile_effects_from_provider_state,
)
from mira.engine.checkpoint import CheckpointStore
from mira.baselines import capture_all_baselines
from mira.kernel import ArchivedMemory, ExperienceLedger, MemoryAction, MemoryDelta
from mira.kernel.causal import CausalEvidenceLog
from mira.kernel.commit import MemoryCommitLog, MemoryQuarantineStore, SecurityGateway
from mira.kernel.consolidation import MemoryConsolidator
from mira.kernel.ledger import ExperienceRecord, new_run_id
from mira.kernel.schema import MemoryClass, to_jsonable
from mira.kernel.snapshot import SnapshotBuilder
from mira.kernel.store import JsonKernelStore, KernelStore
from mira.pipelines.operational import build_communication_pipeline
from mira.workflows import RouterContext, WorkflowRouter, compile_workflow_pack

V3_DIRNAME = "v3"

JOB_PIPELINE_MAP: dict[str, str] = {
    "explore": "intelligence_briefing",
    "writing-pipeline": "article_creation",
    "autowrite-check": "article_creation",
    "journal": "daily_journal",
    "research-cycle": "research_deep_dive",
    "reflect": "weekly_reflection",
    "soul-question": "daily_thought_discussion",
    "spark-check": "daily_thought_discussion",
    "idle-think": "daily_thought_discussion",
    "substack-comments": "social_reactive",
    "substack-growth": "weekly_growth_report",
    "substack-notes": "social_proactive",
    "analyst-pre": "market_monitor",
    "analyst-post": "market_monitor",
    "daily-research": "research_deep_dive",
    "podcast": "podcast_production",
    "voiceover": "podcast_production",
    "book-review": "book_reading_notes",
    "comparative-book-project": "book_reading_notes",
    "skill-study": "skill_learning",
    "health-check": "health_wellness",
    "health-weekly": "health_wellness",
    "self-audit": "self_evolution",
    "self-evolve": "self_evolution",
    "backlog-executor": "memory_maintenance",
    "restore-dry-run": "system_health",
    "assessment": "memory_maintenance",
    "daily-report": "daily_journal",
    "zhesi": "daily_thought_discussion",
    "log-cleanup": "memory_maintenance",
}

NOOP_COMPLETION_JOBS: set[str] = {
    "writing-pipeline",
}

TASK_TAG_PIPELINE_MAP: dict[str, str] = {
    "writing": "article_creation",
    "writer": "article_creation",
    "publish": "article_creation",
    "podcast": "podcast_production",
    "research": "research_deep_dive",
    "briefing": "intelligence_briefing",
    "social": "social_reactive",
    "health": "health_wellness",
    "analyst": "market_monitor",
    "market": "market_monitor",
    "code": "self_evolution",
    "coder": "self_evolution",
    "skill": "skill_learning",
    "discussion": "daily_thought_discussion",
    "communication": "communication",
}

PIPELINE_MEMORY_CLASS: dict[str, MemoryClass] = {
    "article_creation": "creative",
    "podcast_production": "creative",
    "book_reading_notes": "creative",
    "social_reactive": "social",
    "social_proactive": "social",
    "weekly_growth_report": "social",
    "intelligence_briefing": "epistemic",
    "research_deep_dive": "epistemic",
    "a2a_trust_experiment": "epistemic",
    "daily_thought_discussion": "epistemic",
    "daily_journal": "epistemic",
    "weekly_reflection": "epistemic",
    "market_monitor": "operational",
    "communication": "operational",
    "system_health": "operational",
    "incident_response": "operational",
    "health_wellness": "bodily",
    "self_evolution": "self_modification",
    "skill_learning": "self_modification",
    "memory_maintenance": "self_modification",
    "deterministic_reference": "operational",
}

PRODUCTION_PROVIDER_RESOLVER_PROFILES: dict[str, dict[str, Any]] = {
    "substack": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_SUBSTACK_RESOLVER_ENDPOINT",
        "bearer_token_env": "MIRA_SUBSTACK_RESOLVER_TOKEN",
        "payload_path": ["data"],
    },
    "rss": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_RSS_RESOLVER_ENDPOINT",
        "bearer_token_env": "MIRA_RSS_RESOLVER_TOKEN",
        "payload_path": ["data"],
    },
    "social": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_SOCIAL_RESOLVER_ENDPOINT",
        "bearer_token_env": "MIRA_SOCIAL_RESOLVER_TOKEN",
        "payload_path": ["data"],
    },
    "market": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_MARKET_RESOLVER_ENDPOINT",
        "bearer_token_env": "MIRA_MARKET_RESOLVER_TOKEN",
        "payload_path": ["data"],
    },
    "health": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_HEALTH_RESOLVER_ENDPOINT",
        "bearer_token_env": "MIRA_HEALTH_RESOLVER_TOKEN",
        "payload_path": ["data"],
    },
}

PRODUCTION_PROVIDER_ADAPTER_PROFILES: dict[str, dict[str, Any]] = {
    "deployment": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_DEPLOYMENT_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_DEPLOYMENT_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "self_evolution_production_promotion_preview.json",
    },
    "deployment_health": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_DEPLOYMENT_HEALTH_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_DEPLOYMENT_HEALTH_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "self_evolution_production_promotion_preview.json",
    },
    "deployment_rollback": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_DEPLOYMENT_ROLLBACK_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_DEPLOYMENT_ROLLBACK_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "self_evolution_production_promotion_preview.json",
    },
    "substack": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_SUBSTACK_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_SUBSTACK_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
    },
    "rss": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_RSS_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_RSS_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "rss_publish_preview.json",
    },
    "tts": {
        "type": "hosted_tts_http",
        "endpoint_template_env": "MIRA_TTS_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_TTS_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
    },
    "social": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_SOCIAL_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_SOCIAL_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "social_publish_preview.json",
    },
    "market": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_MARKET_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_MARKET_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "market_alert_preview.json",
    },
    "health": {
        "type": "http_json",
        "endpoint_template_env": "MIRA_HEALTH_ADAPTER_ENDPOINT",
        "bearer_token_env": "MIRA_HEALTH_ADAPTER_TOKEN",
        "payload_path": ["data"],
        "method": "POST",
        "preview_filename": "health_write_preview.json",
    },
}


@dataclass(frozen=True)
class HttpJsonProviderAdapter:
    endpoint_template: str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None
    artifact_root: Path | str | None = None
    preview_filename: str = ""

    def __call__(self, entry) -> Mapping[str, Any] | None:
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "external_ref": entry.external_ref or "",
        }
        if self.preview_filename and self.artifact_root is not None:
            body["preview"] = _read_provider_preview_artifact(
                Path(self.artifact_root),
                entry,
                filename=self.preview_filename,
            )
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
        except httpx.HTTPError:
            return None
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            return {
                "status": "error",
                "provider_id": entry.external_ref or entry.target,
                "message": f"provider adapter returned HTTP {response.status_code}",
            }
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


@dataclass(frozen=True)
class LocalRssFeedAdapter:
    feed_path: Path | str
    artifact_root: Path | str
    channel_title: str = "Mira Podcast"
    channel_link: str = "https://mira.local/podcast"
    channel_description: str = "Mira generated podcast feed"

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "publish_rss":
            return {"status": "error", "message": f"local RSS adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="rss_publish_preview.json",
        )
        feed_path = Path(self.feed_path).expanduser()
        feed_path.parent.mkdir(parents=True, exist_ok=True)
        tree, channel = _load_or_create_rss_feed(
            feed_path,
            title=self.channel_title,
            link=self.channel_link,
            description=self.channel_description,
        )
        guid = str(preview.get("episode_id") or entry.target)
        item = _rss_item_for_guid(channel, guid)
        if item is None:
            item = ET.SubElement(channel, "item")
        _set_child_text(item, "title", str(preview.get("title") or entry.target))
        _set_child_text(item, "guid", guid)
        _set_child_text(item, "description", str(preview.get("description") or "Published by Mira V3.1"))
        link = str(preview.get("episode_url") or preview.get("audio_url") or "")
        if link:
            _set_child_text(item, "link", link)
        audio_url = str(preview.get("audio_url") or "")
        if audio_url:
            enclosure = item.find("enclosure")
            if enclosure is None:
                enclosure = ET.SubElement(item, "enclosure")
            enclosure.set("url", audio_url)
            enclosure.set("type", str(preview.get("audio_mime_type") or "audio/mpeg"))
        tree.write(feed_path, encoding="utf-8", xml_declaration=True)
        return {
            "status": "published",
            "provider_id": guid,
            "external_ref": f"rss_feed:{feed_path}:{guid}",
            "detail": f"rss feed updated for {guid}",
        }


@dataclass(frozen=True)
class HostedRssHttpAdapter:
    endpoint_template: str
    artifact_root: Path | str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "publish_rss":
            return {"status": "error", "message": f"hosted RSS adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="rss_publish_preview.json",
        )
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "episode_id": str(preview.get("episode_id") or entry.target),
            "title": str(preview.get("title") or entry.target),
            "description": str(preview.get("description") or ""),
            "audio_url": str(preview.get("audio_url") or ""),
            "audio_mime_type": str(preview.get("audio_mime_type") or "audio/mpeg"),
            "episode_url": str(preview.get("episode_url") or ""),
            "preview": preview,
        }
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
        except httpx.HTTPError:
            return {"status": "unknown", "message": "hosted RSS adapter request failed", "provider_id": entry.target}
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            return {
                "status": "error",
                "provider_id": entry.target,
                "message": f"hosted RSS adapter returned HTTP {response.status_code}",
            }
        try:
            response_payload = response.json()
        except ValueError:
            return {
                "status": "unknown",
                "message": "hosted RSS adapter returned no JSON confirmation",
                "provider_id": entry.target,
            }
        if not isinstance(response_payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted RSS adapter returned unsupported payload",
                "provider_id": entry.target,
            }
        payload = _payload_at_path(response_payload, self.payload_path)
        if not isinstance(payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted RSS adapter response payload path did not resolve",
                "provider_id": entry.target,
            }
        status = str(payload.get("status") or "").lower()
        if status not in {"published", "succeeded", "success", "ok"}:
            return {
                "status": status or "unknown",
                "provider_id": str(payload.get("provider_id") or preview.get("episode_id") or entry.target),
                "message": str(
                    payload.get("message")
                    or payload.get("detail")
                    or "hosted RSS adapter returned no publish confirmation"
                ),
            }
        provider_id = str(payload.get("provider_id") or preview.get("episode_id") or entry.target)
        external_ref = str(
            payload.get("external_ref")
            or payload.get("episode_url")
            or payload.get("feed_url")
            or payload.get("url")
            or provider_id
        )
        manifest = {
            "status": "published",
            "provider_id": provider_id,
            "external_ref": external_ref,
            "feed_url": str(payload.get("feed_url") or ""),
            "episode_url": str(payload.get("episode_url") or payload.get("url") or ""),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "rss_publish_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class HostedSocialHttpAdapter:
    endpoint_template: str
    artifact_root: Path | str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "post_social":
            return {"status": "error", "message": f"hosted social adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="social_publish_preview.json",
        )
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "kind": str(preview.get("kind") or ""),
            "platform": str(preview.get("platform") or ""),
            "content": str(preview.get("content") or ""),
            "preview": preview,
        }
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
        except httpx.HTTPError:
            return {"status": "unknown", "message": "hosted social adapter request failed", "provider_id": entry.target}
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            return {
                "status": "error",
                "provider_id": entry.target,
                "message": f"hosted social adapter returned HTTP {response.status_code}",
            }
        try:
            response_payload = response.json()
        except ValueError:
            return {
                "status": "unknown",
                "message": "hosted social adapter returned no JSON confirmation",
                "provider_id": entry.target,
            }
        if not isinstance(response_payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted social adapter returned unsupported payload",
                "provider_id": entry.target,
            }
        payload = _payload_at_path(response_payload, self.payload_path)
        if not isinstance(payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted social adapter response payload path did not resolve",
                "provider_id": entry.target,
            }
        status = str(payload.get("status") or "").lower()
        if status not in {"posted", "published", "succeeded", "success", "ok"}:
            return {
                "status": status or "unknown",
                "provider_id": str(payload.get("provider_id") or entry.target),
                "message": str(
                    payload.get("message")
                    or payload.get("detail")
                    or "hosted social adapter returned no post confirmation"
                ),
            }
        provider_id = str(payload.get("provider_id") or entry.target)
        external_ref = str(payload.get("external_ref") or payload.get("post_url") or payload.get("url") or provider_id)
        manifest = {
            "status": "posted",
            "provider_id": provider_id,
            "external_ref": external_ref,
            "post_url": str(payload.get("post_url") or payload.get("url") or ""),
            "platform": str(preview.get("platform") or ""),
            "kind": str(preview.get("kind") or ""),
            "target": str(preview.get("target") or entry.target),
            "content": str(preview.get("content") or ""),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "social_publish_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class HostedMarketHttpAdapter:
    endpoint_template: str
    artifact_root: Path | str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "send_market_alert":
            return {"status": "error", "message": f"hosted market adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="market_alert_preview.json",
        )
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "kind": str(preview.get("kind") or ""),
            "message": str(preview.get("message") or ""),
            "severity": str(preview.get("severity") or ""),
            "tetra_report_id": str(preview.get("tetra_report_id") or ""),
            "preview": preview,
        }
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
        except httpx.HTTPError:
            return {"status": "unknown", "message": "hosted market adapter request failed", "provider_id": entry.target}
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            return {
                "status": "error",
                "provider_id": entry.target,
                "message": f"hosted market adapter returned HTTP {response.status_code}",
            }
        try:
            response_payload = response.json()
        except ValueError:
            return {
                "status": "unknown",
                "message": "hosted market adapter returned no JSON confirmation",
                "provider_id": entry.target,
            }
        if not isinstance(response_payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted market adapter returned unsupported payload",
                "provider_id": entry.target,
            }
        payload = _payload_at_path(response_payload, self.payload_path)
        if not isinstance(payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted market adapter response payload path did not resolve",
                "provider_id": entry.target,
            }
        status = str(payload.get("status") or "").lower()
        if status not in {"sent", "published", "succeeded", "success", "ok"}:
            return {
                "status": status or "unknown",
                "provider_id": str(payload.get("provider_id") or entry.target),
                "message": str(
                    payload.get("message")
                    or payload.get("detail")
                    or "hosted market adapter returned no send confirmation"
                ),
            }
        provider_id = str(payload.get("provider_id") or entry.target)
        external_ref = str(payload.get("external_ref") or payload.get("alert_url") or payload.get("url") or provider_id)
        manifest = {
            "status": "sent",
            "provider_id": provider_id,
            "external_ref": external_ref,
            "alert_url": str(payload.get("alert_url") or payload.get("url") or ""),
            "target": str(preview.get("target") or entry.target),
            "message": str(preview.get("message") or ""),
            "severity": str(preview.get("severity") or ""),
            "tetra_report_id": str(preview.get("tetra_report_id") or ""),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "market_alert_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class HostedHealthHttpAdapter:
    endpoint_template: str
    artifact_root: Path | str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 10.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "write_health":
            return {"status": "error", "message": f"hosted health adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="health_write_preview.json",
        )
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        record = preview.get("record") if isinstance(preview.get("record"), Mapping) else {}
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "kind": str(preview.get("kind") or ""),
            "operation": str(preview.get("operation") or ""),
            "record": record,
            "preview": preview,
        }
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
        except httpx.HTTPError:
            return {"status": "unknown", "message": "hosted health adapter request failed", "provider_id": entry.target}
        finally:
            if close_client:
                client.close()
        if response.status_code >= 400:
            return {
                "status": "error",
                "provider_id": entry.target,
                "message": f"hosted health adapter returned HTTP {response.status_code}",
            }
        try:
            response_payload = response.json()
        except ValueError:
            return {
                "status": "unknown",
                "message": "hosted health adapter returned no JSON confirmation",
                "provider_id": entry.target,
            }
        if not isinstance(response_payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted health adapter returned unsupported payload",
                "provider_id": entry.target,
            }
        payload = _payload_at_path(response_payload, self.payload_path)
        if not isinstance(payload, Mapping):
            return {
                "status": "unknown",
                "message": "hosted health adapter response payload path did not resolve",
                "provider_id": entry.target,
            }
        status = str(payload.get("status") or "").lower()
        if status not in {"synced", "written", "succeeded", "success", "ok"}:
            return {
                "status": status or "unknown",
                "provider_id": str(payload.get("provider_id") or entry.target),
                "message": str(
                    payload.get("message")
                    or payload.get("detail")
                    or "hosted health adapter returned no sync confirmation"
                ),
            }
        provider_id = str(payload.get("provider_id") or entry.target)
        external_ref = str(
            payload.get("external_ref") or payload.get("record_url") or payload.get("url") or provider_id
        )
        manifest = {
            "status": "synced",
            "provider_id": provider_id,
            "external_ref": external_ref,
            "record_url": str(payload.get("record_url") or payload.get("url") or ""),
            "target": str(preview.get("target") or entry.target),
            "operation": str(preview.get("operation") or ""),
            "record": record,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "health_write_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class LocalProviderStateAdapter:
    provider: str
    manifest_path: Path | str
    artifact_root: Path | str
    preview_filename: str = ""

    def __call__(self, entry) -> Mapping[str, Any]:
        provider = self.provider or (_provider_name_for_live_effect(entry.action) or "provider_state")
        if provider not in {"substack", "social", "market", "health"}:
            return {"status": "error", "message": f"local provider-state adapter cannot handle {provider}"}
        preview = {}
        if self.preview_filename:
            preview = _read_provider_preview_artifact(
                Path(self.artifact_root),
                entry,
                filename=self.preview_filename,
            )
        payload = _local_provider_state_payload(provider, entry, preview)
        manifest_path = Path(self.manifest_path).expanduser()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = _read_json_object(manifest_path)
        collection_name = _local_provider_state_collection(provider)
        collection = manifest.get(collection_name)
        if not isinstance(collection, dict):
            collection = {}
        collection[str(payload["provider_id"])] = payload
        manifest[collection_name] = collection
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {
            "status": payload["status"],
            "provider_id": payload["provider_id"],
            "external_ref": payload["external_ref"],
            "detail": f"local provider-state manifest updated for {entry.target}",
        }


@dataclass(frozen=True)
class LocalTtsCommandAdapter:
    command: tuple[str, ...]
    artifact_root: Path | str

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "synthesize_tts":
            return {"status": "error", "message": f"local TTS command adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="tts_synthesis_preview.json",
        )
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        input_text_path = run_dir / "tts_input.txt"
        output_audio_path = run_dir / str(preview.get("audio_output_name") or "episode-audio.wav")
        input_text_path.write_text(str(preview.get("script_text") or ""), encoding="utf-8")
        command = [
            part.format(
                input_text_path=str(input_text_path),
                output_audio_path=str(output_audio_path),
                route=str(preview.get("tts_route") or ""),
                voice=str(preview.get("voice") or ""),
            )
            for part in self.command
        ]
        if not command:
            return {"status": "error", "message": "local TTS command is empty"}
        try:
            subprocess.run(command, check=True, text=True, capture_output=True, timeout=60)
        except subprocess.CalledProcessError as exc:
            return {
                "status": "error",
                "message": (exc.stderr or exc.stdout or str(exc)).strip(),
                "provider_id": entry.target,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "local TTS command timed out", "provider_id": entry.target}
        if not output_audio_path.exists() or output_audio_path.stat().st_size <= 0:
            return {
                "status": "error",
                "message": "local TTS command produced no audio file",
                "provider_id": entry.target,
            }
        manifest = {
            "status": "synthesized",
            "provider_id": str(preview.get("target") or entry.target),
            "external_ref": str(output_audio_path),
            "input_text_path": str(input_text_path),
            "output_audio_path": str(output_audio_path),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        (run_dir / "tts_synthesis_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class HostedTtsHttpAdapter:
    endpoint_template: str
    artifact_root: Path | str
    headers: Mapping[str, str] | None = None
    bearer_token: str | None = None
    timeout_s: float = 30.0
    payload_path: tuple[str, ...] = ()
    method: str = "POST"
    client: httpx.Client | None = None
    audio_base64_field: str = "audio_base64"
    audio_url_field: str = "audio_url"

    def __call__(self, entry) -> Mapping[str, Any]:
        if entry.action != "synthesize_tts":
            return {"status": "error", "message": f"hosted TTS adapter cannot handle {entry.action}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="tts_synthesis_preview.json",
        )
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        output_audio_path = run_dir / str(preview.get("audio_output_name") or "episode-audio.wav")
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
        headers = dict(self.headers or {})
        if self.bearer_token and "authorization" not in {key.lower() for key in headers}:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "script_text": str(preview.get("script_text") or ""),
            "voice": str(preview.get("voice") or ""),
            "tts_route": str(preview.get("tts_route") or ""),
            "preview": preview,
        }
        client = self.client or httpx.Client(timeout=self.timeout_s)
        close_client = self.client is None
        try:
            response = client.request(self.method, url, headers=headers, json=body)
            if response.status_code >= 400:
                return {
                    "status": "error",
                    "provider_id": entry.target,
                    "message": f"hosted TTS adapter returned HTTP {response.status_code}",
                }
            payload: Mapping[str, Any] = {}
            audio_bytes = b""
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("audio/"):
                audio_bytes = response.content
            else:
                try:
                    response_payload = response.json()
                except ValueError:
                    return {
                        "status": "error",
                        "message": "hosted TTS adapter returned no JSON or audio",
                        "provider_id": entry.target,
                    }
                if not isinstance(response_payload, Mapping):
                    return {
                        "status": "error",
                        "message": "hosted TTS adapter returned unsupported payload",
                        "provider_id": entry.target,
                    }
                selected = _payload_at_path(response_payload, self.payload_path)
                if not isinstance(selected, Mapping):
                    return {
                        "status": "error",
                        "message": "hosted TTS adapter response payload path did not resolve",
                        "provider_id": entry.target,
                    }
                payload = selected
                audio_base64 = str(payload.get(self.audio_base64_field) or "")
                audio_url = str(payload.get(self.audio_url_field) or "")
                if audio_base64:
                    try:
                        audio_bytes = base64.b64decode(audio_base64, validate=True)
                    except ValueError:
                        return {
                            "status": "error",
                            "message": "hosted TTS adapter returned invalid base64 audio",
                            "provider_id": entry.target,
                        }
                elif audio_url:
                    audio_response = client.get(audio_url, headers=headers)
                    if audio_response.status_code >= 400:
                        return {
                            "status": "error",
                            "message": f"hosted TTS audio download returned HTTP {audio_response.status_code}",
                            "provider_id": str(payload.get("provider_id") or entry.target),
                        }
                    audio_bytes = audio_response.content
                else:
                    return {
                        "status": "error",
                        "message": "hosted TTS adapter returned no audio field",
                        "provider_id": entry.target,
                    }
        except httpx.HTTPError:
            return {"status": "unknown", "message": "hosted TTS adapter request failed", "provider_id": entry.target}
        finally:
            if close_client:
                client.close()
        output_audio_path.write_bytes(audio_bytes)
        if output_audio_path.stat().st_size <= 0:
            return {
                "status": "error",
                "message": "hosted TTS adapter produced no audio file",
                "provider_id": entry.target,
            }
        manifest = {
            "status": "synthesized",
            "provider_id": str(payload.get("provider_id") or preview.get("target") or entry.target),
            "external_ref": str(output_audio_path),
            "provider_external_ref": str(
                payload.get("external_ref") or payload.get("url") or payload.get("provider_url") or ""
            ),
            "output_audio_path": str(output_audio_path),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        (run_dir / "tts_synthesis_result.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifest


@dataclass(frozen=True)
class LocalDeploymentCommandAdapter:
    role: str
    command: tuple[str, ...]
    artifact_root: Path | str
    timeout_s: float = 120.0

    def __call__(self, entry) -> Mapping[str, Any]:
        role = self.role or "deployment"
        if role not in {"deployment", "deployment_health", "deployment_rollback"}:
            return {"status": "error", "message": f"unsupported local deployment command role: {role}"}
        preview = _read_provider_preview_artifact(
            Path(self.artifact_root),
            entry,
            filename="self_evolution_production_promotion_preview.json",
        )
        run_dir = Path(self.artifact_root) / entry.pipeline / entry.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        input_json_path = run_dir / f"{role}_command_input.json"
        result_json_path = run_dir / f"{role}_command_result.json"
        command_input = {
            "role": role,
            "effect_id": entry.effect_id,
            "idempotency_key": entry.idempotency_key,
            "run_id": entry.run_id,
            "pipeline": entry.pipeline,
            "action": entry.action,
            "target": entry.target,
            "external_ref": entry.external_ref or "",
            "detail": entry.detail or "",
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
            "preview": preview,
        }
        input_json_path.write_text(json.dumps(command_input, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        command = [
            part.format(
                input_json_path=str(input_json_path),
                result_json_path=str(result_json_path),
                target=entry.target,
                external_ref=entry.external_ref or "",
                run_id=entry.run_id,
                preview_hash=entry.preview_hash,
            )
            for part in self.command
        ]
        if not command:
            return {"status": "error", "message": "local deployment command is empty"}
        try:
            subprocess.run(command, check=True, text=True, capture_output=True, timeout=self.timeout_s)
        except subprocess.CalledProcessError as exc:
            return {
                "status": "error",
                "message": (exc.stderr or exc.stdout or str(exc)).strip(),
                "provider_id": entry.target,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "local deployment command timed out", "provider_id": entry.target}
        payload: Mapping[str, Any] = {}
        if result_json_path.exists():
            payload = _read_json_object(result_json_path)
        defaults = {
            "deployment": ("deployed", f"local_deployment:{entry.target}"),
            "deployment_health": ("healthy", f"local_deployment_health:{entry.target}"),
            "deployment_rollback": ("rolled_back", f"local_deployment_rollback:{entry.target}"),
        }
        default_status, default_external_ref = defaults[role]
        external_ref = str(
            payload.get("external_ref") or payload.get("url") or payload.get("provider_url") or default_external_ref
        )
        manifest = {
            "status": str(payload.get("status") or default_status),
            "provider_id": str(payload.get("provider_id") or entry.target),
            "external_ref": external_ref,
            "detail": str(payload.get("detail") or f"local {role} command completed"),
            "input_json_path": str(input_json_path),
            "result_json_path": str(result_json_path),
            "preview_hash": entry.preview_hash,
            "approval_token_id": entry.approval_token_id,
        }
        result_json_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest


@dataclass(frozen=True)
class V3Paths:
    root: Path
    kernel: Path
    ledger: Path
    commits: Path
    causal_evidence: Path
    effect_log: Path
    eval_history: Path
    approvals: Path
    quarantine: Path
    checkpoints: Path
    snapshots: Path
    artifacts: Path
    workflow_audits: Path
    baselines: Path
    provider_resolvers: Path
    provider_adapters: Path
    provider_state_manifests: Path


def default_v3_paths(root: Path | str | None = None) -> V3Paths:
    if root is None:
        try:
            import config

            root = Path(config.MIRA_ROOT)
        except Exception:
            root = Path.cwd()
    data_dir = Path(root) / "data" / V3_DIRNAME
    return V3Paths(
        root=data_dir,
        kernel=data_dir / "kernel.json",
        ledger=data_dir / "experience_ledger.jsonl",
        commits=data_dir / "memory_commits.jsonl",
        causal_evidence=data_dir / "causal_evidence.jsonl",
        effect_log=data_dir / "effect_log.jsonl",
        eval_history=data_dir / "eval_history.jsonl",
        approvals=data_dir / "approvals.jsonl",
        quarantine=data_dir / "memory_quarantine.jsonl",
        checkpoints=data_dir / "checkpoints",
        snapshots=data_dir / "snapshots",
        artifacts=data_dir / "artifacts",
        workflow_audits=data_dir / "workflow_audits",
        baselines=data_dir / "baselines",
        provider_resolvers=data_dir / "provider_resolvers.json",
        provider_adapters=data_dir / "provider_adapters.json",
        provider_state_manifests=data_dir / "provider_state",
    )


def default_kernel_store(root: Path | str | None = None) -> KernelStore:
    return JsonKernelStore(default_v3_paths(root).kernel)


def default_ledger(root: Path | str | None = None) -> ExperienceLedger:
    return ExperienceLedger(default_v3_paths(root).ledger)


def default_commit_log(root: Path | str | None = None) -> MemoryCommitLog:
    return MemoryCommitLog(default_v3_paths(root).commits)


def default_causal_evidence_log(root: Path | str | None = None) -> CausalEvidenceLog:
    return CausalEvidenceLog(default_v3_paths(root).causal_evidence)


def default_quarantine_store(root: Path | str | None = None) -> MemoryQuarantineStore:
    return MemoryQuarantineStore(default_v3_paths(root).quarantine)


def default_effect_log(root: Path | str | None = None) -> EffectLog:
    return EffectLog(default_v3_paths(root).effect_log)


def default_approval_store(root: Path | str | None = None) -> ApprovalStore:
    return ApprovalStore(default_v3_paths(root).approvals)


def default_checkpoint_store(root: Path | str | None = None) -> CheckpointStore:
    return CheckpointStore(default_v3_paths(root).checkpoints)


@dataclass(frozen=True)
class PublicEvidenceRecordResult:
    record: ExperienceRecord
    evidence_artifact: Path
    preview_hash: str


@dataclass(frozen=True)
class BriefingFeedbackRecordResult:
    record: ExperienceRecord
    evidence_artifact: Path
    eval_ref: str


@dataclass(frozen=True)
class BriefingFeedbackPacket:
    packet_dir: Path
    review_artifact: Path
    metadata_artifact: Path
    checklist_artifact: Path
    record_feedback_command: str
    record_feedback_from_packet_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_dir": str(self.packet_dir),
            "review_artifact": str(self.review_artifact),
            "metadata_artifact": str(self.metadata_artifact),
            "checklist_artifact": str(self.checklist_artifact),
            "record_feedback_command": self.record_feedback_command,
            "record_feedback_from_packet_command": self.record_feedback_from_packet_command,
        }


@dataclass(frozen=True)
class PublicWriteupSafetyReport:
    draft_artifact: str
    preview_hash: str
    passed: bool
    findings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_artifact": self.draft_artifact,
            "preview_hash": self.preview_hash,
            "passed": self.passed,
            "findings": self.findings,
        }


@dataclass(frozen=True)
class PublicWriteupPublicationPacket:
    packet_dir: Path
    submission_artifact: Path
    metadata_artifact: Path
    checklist_artifact: Path
    preview_hash: str
    safety_report: PublicWriteupSafetyReport
    record_evidence_command: str
    record_evidence_from_packet_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_dir": str(self.packet_dir),
            "submission_artifact": str(self.submission_artifact),
            "metadata_artifact": str(self.metadata_artifact),
            "checklist_artifact": str(self.checklist_artifact),
            "preview_hash": self.preview_hash,
            "safety_report": self.safety_report.to_dict(),
            "record_evidence_command": self.record_evidence_command,
            "record_evidence_from_packet_command": self.record_evidence_from_packet_command,
        }


@dataclass(frozen=True)
class PublicFeedbackSolicitationPacket:
    packet_dir: Path
    request_artifact: Path
    metadata_artifact: Path
    checklist_artifact: Path
    record_feedback_command: str
    record_feedback_from_packet_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_dir": str(self.packet_dir),
            "request_artifact": str(self.request_artifact),
            "metadata_artifact": str(self.metadata_artifact),
            "checklist_artifact": str(self.checklist_artifact),
            "record_feedback_command": self.record_feedback_command,
            "record_feedback_from_packet_command": self.record_feedback_from_packet_command,
        }


@dataclass(frozen=True)
class CustomerDiscoveryFeedbackPacket:
    packet_dir: Path
    request_artifact: Path
    metadata_artifact: Path
    checklist_artifact: Path
    record_feedback_command: str
    record_feedback_from_packet_command: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_dir": str(self.packet_dir),
            "request_artifact": str(self.request_artifact),
            "metadata_artifact": str(self.metadata_artifact),
            "checklist_artifact": str(self.checklist_artifact),
            "record_feedback_command": self.record_feedback_command,
            "record_feedback_from_packet_command": self.record_feedback_from_packet_command,
        }


@dataclass(frozen=True)
class NorthStarClosurePacketManifest:
    manifest_artifact: Path
    checklist_artifact: Path
    publication_packets: list[PublicWriteupPublicationPacket]
    public_feedback_packets: list[PublicFeedbackSolicitationPacket]
    customer_discovery_packets: list[CustomerDiscoveryFeedbackPacket]
    briefing_feedback_packets: list[BriefingFeedbackPacket]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_artifact": str(self.manifest_artifact),
            "checklist_artifact": str(self.checklist_artifact),
            "counts": {
                "publication_packets": len(self.publication_packets),
                "public_feedback_packets": len(self.public_feedback_packets),
                "customer_discovery_packets": len(self.customer_discovery_packets),
                "briefing_feedback_packets": len(self.briefing_feedback_packets),
                "warnings": len(self.warnings),
            },
            "publication_packets": [packet.to_dict() for packet in self.publication_packets],
            "public_feedback_packets": [packet.to_dict() for packet in self.public_feedback_packets],
            "customer_discovery_packets": [packet.to_dict() for packet in self.customer_discovery_packets],
            "briefing_feedback_packets": [packet.to_dict() for packet in self.briefing_feedback_packets],
            "warnings": list(self.warnings),
            "status_command": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions",
        }


@dataclass(frozen=True)
class CustomerDiscoveryFeedbackResult:
    record: ExperienceRecord
    evidence_artifact: Path
    eval_ref: str


def prepare_public_writeup_publication_packet(
    *,
    slug: str,
    draft_artifact: Path | str,
    root: Path | str | None = None,
    expected_preview_hash: str | None = None,
) -> PublicWriteupPublicationPacket:
    """Create a local no-network publication packet for operator review."""

    evidence_slug = _validate_public_evidence_slug(slug)
    paths = default_v3_paths(root)
    workspace_root = Path(root) if root is not None else paths.root.parents[1]
    draft_path, preview_hash = _resolve_public_evidence_draft(
        draft_artifact,
        workspace_root=workspace_root,
        expected_preview_hash=expected_preview_hash,
    )
    if draft_path is None:
        raise ValueError("draft_artifact is required")
    safety_report = public_writeup_safety_report(draft_path)
    if not safety_report.passed:
        raise ValueError("draft_artifact safety audit failed: " + "; ".join(safety_report.findings[:3]))
    packet_dir = paths.artifacts / "publication_packets" / evidence_slug / preview_hash[:12]
    packet_dir.mkdir(parents=True, exist_ok=True)
    submission_artifact = packet_dir / "public_writeup_submission.md"
    metadata_artifact = packet_dir / "publication_packet.json"
    checklist_artifact = packet_dir / "publication_checklist.md"
    draft_text = draft_path.read_text(encoding="utf-8")
    submission_artifact.write_text(draft_text, encoding="utf-8")
    record_command = _public_evidence_record_command(evidence_slug, draft_path, preview_hash)
    packet_record_command = _public_evidence_packet_record_command(metadata_artifact)
    feedback_ref = f"external_feedback:{evidence_slug}:source=<source>"
    metadata = {
        "slug": evidence_slug,
        "draft_artifact": str(draft_path),
        "submission_artifact": str(submission_artifact),
        "preview_hash": preview_hash,
        "safety_report": safety_report.to_dict(),
        "publish_ref_template": f"public_writeup:{evidence_slug}:url=<url>",
        "feedback_ref_template": feedback_ref,
        "record_evidence_command_template": record_command,
        "record_evidence_from_packet_command_template": packet_record_command,
    }
    metadata_artifact.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_artifact.write_text(
        "\n".join(
            [
                "# Public Writeup Publication Checklist",
                "",
                f"Slug: {evidence_slug}",
                f"Preview hash: {preview_hash}",
                f"Safety audit: {'passed' if safety_report.passed else 'blocked'}",
                "",
                "1. Review `public_writeup_submission.md` for editorial accuracy.",
                "2. Publish or externally share the submission text.",
                "3. Collect the published URL and at least one concrete feedback source.",
                "4. Record evidence with:",
                "",
                "```bash",
                packet_record_command,
                "```",
                "",
                "Or record with explicit slug/draft fields:",
                "",
                "```bash",
                record_command,
                "```",
                "",
                f"Feedback ref template: `{feedback_ref}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return PublicWriteupPublicationPacket(
        packet_dir=packet_dir,
        submission_artifact=submission_artifact,
        metadata_artifact=metadata_artifact,
        checklist_artifact=checklist_artifact,
        preview_hash=preview_hash,
        safety_report=safety_report,
        record_evidence_command=record_command,
        record_evidence_from_packet_command=packet_record_command,
    )


def prepare_public_writeup_publication_packets(
    *,
    root: Path | str | None = None,
    limit: int = 5,
) -> list[PublicWriteupPublicationPacket]:
    """Create publication packets for queued public-writeup plans not yet shipped."""

    shipped_slugs = {
        slug
        for record in default_ledger(root).list()
        for slug, _url in (_parse_public_writeup_ref(ref) for ref in record.eval_refs)
        if slug
    }
    packets: list[PublicWriteupPublicationPacket] = []
    seen: set[tuple[str, str]] = set()
    for record in reversed(default_ledger(root).list()):
        plan_refs = [ref for ref in record.eval_refs if ref.startswith("public_writeup_plan:")]
        if not plan_refs:
            continue
        draft_artifacts = [
            artifact
            for artifact in record.artifacts
            if "writeup" in Path(artifact).name.lower() and "draft" in Path(artifact).name.lower()
        ]
        for plan_ref in plan_refs:
            slug = plan_ref.removeprefix("public_writeup_plan:").strip()
            if not slug or slug in shipped_slugs:
                continue
            for artifact in draft_artifacts:
                key = (slug, artifact)
                if key in seen:
                    continue
                seen.add(key)
                packets.append(
                    prepare_public_writeup_publication_packet(
                        root=root,
                        slug=slug,
                        draft_artifact=artifact,
                    )
                )
                if len(packets) >= limit:
                    return packets
    return packets


def prepare_north_star_closure_packets(
    *,
    root: Path | str | None = None,
) -> NorthStarClosurePacketManifest:
    """Prepare all local no-network packets needed for current north-star gates.

    This function writes operator packets and a manifest only. It does not record
    feedback, publish content, or call external providers.
    """

    from mira.evals import build_strategic_scorecard

    paths = default_v3_paths(root)
    records = default_ledger(root).list()
    warnings: list[str] = []
    try:
        publication_packets = prepare_public_writeup_publication_packets(root=root)
    except ValueError as exc:
        publication_packets = []
        warnings.append(f"publication_packets_blocked: {exc}")
    public_feedback_packets = prepare_public_feedback_solicitation_packets(root=root)
    strategic = build_strategic_scorecard(records)
    customer_discovery_packets = (
        [prepare_customer_discovery_feedback_packet(root=root)] if strategic.public_feedback_items < 3 else []
    )
    briefing_feedback_packets = prepare_briefing_feedback_packets(root=root)
    packet_dir = paths.artifacts / "north_star_closure_packets" / date.today().isoformat()
    packet_dir.mkdir(parents=True, exist_ok=True)
    manifest_artifact = packet_dir / "closure_manifest.json"
    checklist_artifact = packet_dir / "closure_checklist.md"
    manifest = NorthStarClosurePacketManifest(
        manifest_artifact=manifest_artifact,
        checklist_artifact=checklist_artifact,
        publication_packets=publication_packets,
        public_feedback_packets=public_feedback_packets,
        customer_discovery_packets=customer_discovery_packets,
        briefing_feedback_packets=briefing_feedback_packets,
        warnings=warnings,
    )
    manifest_artifact.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_artifact.write_text(_north_star_closure_checklist(manifest), encoding="utf-8")
    return manifest


def prepare_public_feedback_solicitation_packet(
    *,
    slug: str,
    published_url: str,
    root: Path | str | None = None,
    title: str | None = None,
    stats_artifact: Path | str | None = None,
) -> PublicFeedbackSolicitationPacket:
    """Create a local no-network packet for soliciting external feedback."""

    evidence_slug = _validate_public_evidence_slug(slug)
    public_url = _validate_public_url(published_url, "published_url")
    paths = default_v3_paths(root)
    stats_path = Path(stats_artifact) if stats_artifact else None
    if stats_path and not stats_path.is_absolute():
        workspace_root = Path(root) if root is not None else paths.root.parents[1]
        stats_path = workspace_root / stats_path
    stats_snapshot = _public_feedback_stats_snapshot(public_url, stats_path)
    resolved_title = (title or str(stats_snapshot.get("title") or "") or evidence_slug).strip()
    packet_hash = hashlib.sha256(public_url.encode("utf-8")).hexdigest()[:12]
    packet_dir = paths.artifacts / "public_feedback_packets" / evidence_slug / packet_hash
    packet_dir.mkdir(parents=True, exist_ok=True)
    request_artifact = packet_dir / "feedback_request.md"
    metadata_artifact = packet_dir / "feedback_packet.json"
    checklist_artifact = packet_dir / "feedback_checklist.md"
    record_command = _public_feedback_record_command(evidence_slug, public_url)
    packet_record_command = _public_feedback_packet_record_command(metadata_artifact)
    stats_lines = [
        f"- views: {stats_snapshot.get('views', 0)}",
        f"- likes: {stats_snapshot.get('likes', 0)}",
        f"- comments: {stats_snapshot.get('comments', 0)}",
        f"- restacks: {stats_snapshot.get('restacks', 0)}",
    ]
    request_artifact.write_text(
        "\n".join(
            [
                f"# Feedback Request: {resolved_title}",
                "",
                f"Published URL: {public_url}",
                "",
                "Current publication stats:",
                *stats_lines,
                "",
                "I am looking for concrete external feedback on this V3.1 writeup.",
                "",
                "Useful feedback questions:",
                "1. Which claim is least convincing or under-evidenced?",
                "2. What implementation detail, artifact, or metric would make the argument more credible?",
                "3. If you were building against this idea, what would you try, reject, or change first?",
                "",
                "After receiving a concrete comment, reply, review, or customer-discovery note, record it with:",
                "",
                "```bash",
                record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    metadata = {
        "slug": evidence_slug,
        "title": resolved_title,
        "published_url": public_url,
        "stats_artifact": str(stats_path or ""),
        "stats_snapshot": stats_snapshot,
        "feedback_ref_template": f"external_feedback:{evidence_slug}:source=<source>",
        "record_feedback_command_template": record_command,
        "record_feedback_from_packet_command_template": packet_record_command,
    }
    metadata_artifact.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_artifact.write_text(
        "\n".join(
            [
                "# Public Feedback Checklist",
                "",
                f"Slug: {evidence_slug}",
                f"Published URL: {public_url}",
                "",
                "1. Send or post `feedback_request.md` to a concrete external reviewer or venue.",
                "2. Wait for a concrete response, comment, reply, review, or customer-discovery note.",
                "3. Record only the concrete feedback source, not generic impressions or zero-engagement stats.",
                "4. Record evidence with:",
                "",
                "```bash",
                record_command,
                "```",
                "",
                "Or record from this packet metadata with:",
                "",
                "```bash",
                packet_record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return PublicFeedbackSolicitationPacket(
        packet_dir=packet_dir,
        request_artifact=request_artifact,
        metadata_artifact=metadata_artifact,
        checklist_artifact=checklist_artifact,
        record_feedback_command=record_command,
        record_feedback_from_packet_command=packet_record_command,
    )


def prepare_public_feedback_solicitation_packets(
    *,
    root: Path | str | None = None,
) -> list[PublicFeedbackSolicitationPacket]:
    """Create no-network feedback packets for recorded writeups still missing external feedback."""

    paths = default_v3_paths(root)
    workspace_root = Path(root) if root is not None else paths.root.parents[1]
    stats_artifact = workspace_root / "data" / "social" / "publication_stats.json"
    feedback_slugs = {
        slug
        for record in default_ledger(root).list()
        for slug in (_parse_public_feedback_ref(ref) for ref in record.eval_refs)
        if slug
    }
    packets: list[PublicFeedbackSolicitationPacket] = []
    seen: set[str] = set()
    for record in reversed(default_ledger(root).list()):
        for ref in record.eval_refs:
            slug, published_url = _parse_public_writeup_ref(ref)
            if not slug or slug in feedback_slugs or slug in seen:
                continue
            public_url = published_url or _public_url_from_record_artifacts(getattr(record, "artifacts", []))
            if not public_url:
                continue
            seen.add(slug)
            packets.append(
                prepare_public_feedback_solicitation_packet(
                    root=root,
                    slug=slug,
                    published_url=public_url,
                    stats_artifact=stats_artifact,
                )
            )
            if len(packets) >= 5:
                return packets
    return packets


def prepare_customer_discovery_feedback_packet(
    *,
    root: Path | str | None = None,
    topic: str = "a2a_trust_manifest",
    question: str | None = None,
) -> CustomerDiscoveryFeedbackPacket:
    """Create a local no-network packet for collecting independent customer-discovery feedback."""

    topic_value = _validate_customer_discovery_topic(topic)
    prompt = (
        question or "What would make this A2A trust/evidence workflow useful, credible, or not worth adopting?"
    ).strip()
    if _looks_placeholder(prompt):
        raise ValueError("question must be concrete")
    paths = default_v3_paths(root)
    packet_hash = hashlib.sha256(f"{topic_value}:{prompt}".encode("utf-8")).hexdigest()[:12]
    packet_dir = paths.artifacts / "customer_discovery_packets" / topic_value / packet_hash
    packet_dir.mkdir(parents=True, exist_ok=True)
    request_artifact = packet_dir / "customer_discovery_request.md"
    metadata_artifact = packet_dir / "customer_discovery_packet.json"
    checklist_artifact = packet_dir / "customer_discovery_checklist.md"
    record_command = _customer_discovery_record_command()
    packet_record_command = _customer_discovery_packet_record_command(metadata_artifact)
    request_artifact.write_text(
        "\n".join(
            [
                f"# Customer Discovery Request: {topic_value}",
                "",
                "Use this packet to collect concrete external feedback that can count toward the V3.1 external-feedback gate.",
                "",
                f"Primary question: {prompt}",
                "",
                "Useful follow-ups:",
                "1. Which claim or workflow step feels least credible?",
                "2. What evidence, artifact, API, or integration would make it useful?",
                "3. What would make you reject this approach?",
                "",
                "After receiving a concrete response, record it with:",
                "",
                "```bash",
                record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    metadata = {
        "topic": topic_value,
        "question": prompt,
        "feedback_ref_template": "customer_discovery:<source>",
        "record_feedback_command_template": record_command,
        "record_feedback_from_packet_command_template": packet_record_command,
    }
    metadata_artifact.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_artifact.write_text(
        "\n".join(
            [
                "# Customer Discovery Feedback Checklist",
                "",
                f"Topic: {topic_value}",
                "",
                "1. Send or use `customer_discovery_request.md` with a concrete external reviewer, user, builder, or customer-discovery contact.",
                "2. Wait for a concrete response. Do not count internal notes, generic engagement, or placeholders.",
                "3. Record the source and a short insight summary with:",
                "",
                "```bash",
                record_command,
                "```",
                "",
                "Or record from this packet metadata with:",
                "",
                "```bash",
                packet_record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return CustomerDiscoveryFeedbackPacket(
        packet_dir=packet_dir,
        request_artifact=request_artifact,
        metadata_artifact=metadata_artifact,
        checklist_artifact=checklist_artifact,
        record_feedback_command=record_command,
        record_feedback_from_packet_command=packet_record_command,
    )


def prepare_briefing_feedback_packet(
    *,
    item_id: str,
    root: Path | str | None = None,
) -> BriefingFeedbackPacket:
    """Create a local no-network packet for reviewing one briefing blind-sample item."""

    from mira.evals import BRIEFING_FEEDBACK_BUTTONS, build_weekly_blind_sample

    normalized_item_id = _validate_briefing_item_id(item_id)
    ledger = default_ledger(root)
    blind_sample = build_weekly_blind_sample(ledger.list())
    item_by_id = {item.item_id: item for item in blind_sample}
    if normalized_item_id not in item_by_id:
        raise ValueError("item_id must match an unreviewed item in the current weekly blind sample")
    item = item_by_id[normalized_item_id]
    paths = default_v3_paths(root)
    packet_hash = hashlib.sha256(normalized_item_id.encode("utf-8")).hexdigest()[:12]
    packet_dir = paths.artifacts / "briefing_feedback_packets" / packet_hash
    packet_dir.mkdir(parents=True, exist_ok=True)
    review_artifact = packet_dir / "briefing_feedback_review.md"
    metadata_artifact = packet_dir / "briefing_feedback_packet.json"
    checklist_artifact = packet_dir / "briefing_feedback_checklist.md"
    record_command = _briefing_feedback_record_command(normalized_item_id)
    packet_record_command = _briefing_feedback_packet_record_command(metadata_artifact)
    buttons = list(BRIEFING_FEEDBACK_BUTTONS)
    item_text = (getattr(item, "text", "") or "").strip()
    review_artifact.write_text(
        "\n".join(
            [
                "# Briefing Feedback Review",
                "",
                f"Item id: `{normalized_item_id}`",
                "",
                "Briefing item:",
                "",
                item_text or "(item text unavailable; use the dashboard queue metadata below)",
                "",
                f"- topics: {', '.join(item.topics) or 'none'}",
                f"- matched interests: {', '.join(item.matched_interest_ids) or 'none'}",
                f"- novelty_score: {item.novelty_score:.4f}",
                f"- actionability_score: {item.actionability_score:.4f}",
                "",
                "Available buttons:",
                *[f"- `{button}`" for button in buttons],
                "",
                "Record feedback after choosing the single best button:",
                "",
                "```bash",
                record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    metadata = {
        "item_id": normalized_item_id,
        "item_text": item_text,
        "topics": item.topics,
        "matched_interest_ids": item.matched_interest_ids,
        "novelty_score": item.novelty_score,
        "actionability_score": item.actionability_score,
        "available_buttons": buttons,
        "feedback_ref_template": f"briefing_feedback:item={normalized_item_id}:button=<button>",
        "record_feedback_command_template": record_command,
        "record_feedback_from_packet_command_template": packet_record_command,
    }
    metadata_artifact.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checklist_artifact.write_text(
        "\n".join(
            [
                "# Briefing Feedback Checklist",
                "",
                f"Item id: `{normalized_item_id}`",
                "",
                "1. Read `briefing_feedback_review.md` and choose one button from the listed options.",
                "2. Do not record placeholder feedback or score an item that is no longer in the current blind-sample queue.",
                "3. Record feedback with:",
                "",
                "```bash",
                record_command,
                "```",
                "",
                "Or record from this packet metadata with:",
                "",
                "```bash",
                packet_record_command,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return BriefingFeedbackPacket(
        packet_dir=packet_dir,
        review_artifact=review_artifact,
        metadata_artifact=metadata_artifact,
        checklist_artifact=checklist_artifact,
        record_feedback_command=record_command,
        record_feedback_from_packet_command=packet_record_command,
    )


def prepare_briefing_feedback_packets(
    *,
    root: Path | str | None = None,
) -> list[BriefingFeedbackPacket]:
    """Create local no-network packets for every current briefing blind-sample item."""

    from mira.evals import build_weekly_blind_sample

    ledger = default_ledger(root)
    return [
        prepare_briefing_feedback_packet(root=root, item_id=item.item_id)
        for item in build_weekly_blind_sample(ledger.list())
    ]


def _north_star_closure_checklist(manifest: NorthStarClosurePacketManifest) -> str:
    lines = [
        "# V3.1 North-Star Closure Packets",
        "",
        "This manifest prepares local operator packets only. It does not publish content, contact reviewers, call providers, or record feedback evidence.",
        "",
        f"Manifest JSON: `{manifest.manifest_artifact}`",
        "",
        "## Counts",
        "",
        f"- publication packets: `{len(manifest.publication_packets)}`",
        f"- public feedback packets: `{len(manifest.public_feedback_packets)}`",
        f"- customer discovery packets: `{len(manifest.customer_discovery_packets)}`",
        f"- briefing feedback packets: `{len(manifest.briefing_feedback_packets)}`",
        f"- warnings: `{len(manifest.warnings)}`",
        "",
    ]
    if manifest.warnings:
        lines.extend(["## Warnings", "", *[f"- {warning}" for warning in manifest.warnings], ""])
    lines.extend(_closure_packet_lines("Publication Review Packets", manifest.publication_packets, "metadata_artifact"))
    lines.extend(
        _closure_packet_lines("Public Feedback Packets", manifest.public_feedback_packets, "metadata_artifact")
    )
    lines.extend(
        _closure_packet_lines("Customer Discovery Packets", manifest.customer_discovery_packets, "metadata_artifact")
    )
    lines.extend(
        _closure_packet_lines("Briefing Feedback Packets", manifest.briefing_feedback_packets, "metadata_artifact")
    )
    lines.extend(
        [
            "## After External Or Operator Evidence Exists",
            "",
            "Use the packet metadata commands to record only concrete external feedback, publication evidence, or briefing-button feedback.",
            "",
            "```bash",
            "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _closure_packet_lines(title: str, packets: list[Any], metadata_attr: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not packets:
        return [*lines, "- none", ""]
    for packet in packets:
        lines.append(f"- `{getattr(packet, metadata_attr)}`")
        record_command = (
            getattr(packet, "record_feedback_from_packet_command", "")
            or getattr(packet, "record_evidence_from_packet_command", "")
            or getattr(packet, "record_feedback_command", "")
            or getattr(packet, "record_evidence_command", "")
        )
        if record_command:
            lines.extend(["", "  ```bash", f"  {record_command}", "  ```", ""])
    if lines[-1] != "":
        lines.append("")
    return lines


def record_public_writeup_evidence(
    *,
    slug: str,
    published_url: str,
    root: Path | str | None = None,
    draft_artifact: Path | str | None = None,
    expected_preview_hash: str | None = None,
    feedback_source: str | None = None,
    feedback_url: str | None = None,
    notes: str | None = None,
) -> PublicEvidenceRecordResult:
    """Append validated public-writeup / feedback evidence after external publication.

    This records evidence supplied by an operator. It does not publish content or
    contact external systems.
    """

    evidence_slug = _validate_public_evidence_slug(slug)
    public_url = _validate_public_url(published_url, "published_url")
    feedback_url_value = _validate_public_url(feedback_url, "feedback_url") if feedback_url else ""
    feedback_source_value = _validate_feedback_source(feedback_source or feedback_url_value)
    paths = default_v3_paths(root)
    workspace_root = Path(root) if root is not None else paths.root.parents[1]
    draft_path, preview_hash = _resolve_public_evidence_draft(
        draft_artifact,
        workspace_root=workspace_root,
        expected_preview_hash=expected_preview_hash,
    )
    safety_report = public_writeup_safety_report(draft_path) if draft_path else None
    if safety_report and not safety_report.passed:
        raise ValueError("draft_artifact safety audit failed: " + "; ".join(safety_report.findings[:3]))
    run_id = new_run_id("a2a_public_evidence")
    artifact_dir = paths.artifacts / "a2a_trust_experiment" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    eval_refs = [f"public_writeup:{evidence_slug}:url={public_url}"]
    if feedback_source_value:
        eval_refs.append(f"external_feedback:{evidence_slug}:source={_eval_ref_component(feedback_source_value)}")
    evidence_payload = {
        "record_id": run_id,
        "slug": evidence_slug,
        "published_url": public_url,
        "draft_artifact": str(draft_path) if draft_path else "",
        "preview_hash": preview_hash,
        "safety_passed": safety_report.passed if safety_report else None,
        "safety_findings": safety_report.findings if safety_report else [],
        "feedback_source": feedback_source_value,
        "feedback_url": feedback_url_value,
        "notes": (notes or "").strip(),
        "eval_refs": eval_refs,
    }
    evidence_artifact = artifact_dir / "public_evidence.json"
    evidence_artifact.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta = MemoryDelta.no_kernel_change(
        pipeline="a2a_trust_experiment",
        run_id=run_id,
        memory_class="epistemic",
        what_happened=f"Recorded public writeup evidence for {evidence_slug}.",
        what_mattered="Strategic scorecard can count only externally published and sourced evidence.",
        what_changed="A public writeup ref and optional external feedback ref were appended from operator-supplied evidence.",
        trust_tier="human_confirmed",
    )
    record = ExperienceRecord(
        id=run_id,
        pipeline="a2a_trust_experiment",
        trigger="operator_evidence",
        intent=f"record public evidence for {evidence_slug}",
        outcome="completed",
        delta=delta,
        causal_links=[],
        confidence=0.95,
        memory_class="epistemic",
        artifacts=[str(evidence_artifact), *([str(draft_path)] if draft_path else [])],
        eval_refs=eval_refs,
    )
    default_ledger(root).append(record)
    return PublicEvidenceRecordResult(record=record, evidence_artifact=evidence_artifact, preview_hash=preview_hash)


def record_public_feedback_evidence(
    *,
    slug: str,
    feedback_source: str,
    root: Path | str | None = None,
    published_url: str | None = None,
    feedback_url: str | None = None,
    notes: str | None = None,
) -> PublicEvidenceRecordResult:
    """Append validated external-feedback evidence for an already recorded public writeup."""

    evidence_slug = _validate_public_evidence_slug(slug)
    feedback_url_value = _validate_public_url(feedback_url, "feedback_url") if feedback_url else ""
    feedback_source_value = _validate_feedback_source(feedback_source or feedback_url_value)
    if not feedback_source_value:
        raise ValueError("feedback_source or feedback_url is required")
    supplied_public_url = _validate_public_url(published_url, "published_url") if published_url else ""
    recorded_public_url = _recorded_public_writeup_url(root, evidence_slug)
    if recorded_public_url is None:
        raise ValueError(f"public writeup evidence for slug {evidence_slug!r} must be recorded before feedback")
    if supplied_public_url and recorded_public_url and supplied_public_url != recorded_public_url:
        raise ValueError("published_url does not match the recorded public writeup URL")
    public_url = supplied_public_url or recorded_public_url
    paths = default_v3_paths(root)
    run_id = new_run_id("a2a_public_feedback")
    artifact_dir = paths.artifacts / "a2a_trust_experiment" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    eval_refs = [f"external_feedback:{evidence_slug}:source={_eval_ref_component(feedback_source_value)}"]
    evidence_payload = {
        "record_id": run_id,
        "slug": evidence_slug,
        "published_url": public_url,
        "feedback_source": feedback_source_value,
        "feedback_url": feedback_url_value,
        "notes": (notes or "").strip(),
        "eval_refs": eval_refs,
    }
    evidence_artifact = artifact_dir / "public_feedback.json"
    evidence_artifact.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta = MemoryDelta.no_kernel_change(
        pipeline="a2a_trust_experiment",
        run_id=run_id,
        memory_class="epistemic",
        what_happened=f"Recorded public feedback evidence for {evidence_slug}.",
        what_mattered="Strategic scorecard should count external feedback without duplicating the published writeup.",
        what_changed="An external feedback ref was appended from operator-supplied evidence.",
        trust_tier="human_confirmed",
    )
    record = ExperienceRecord(
        id=run_id,
        pipeline="a2a_trust_experiment",
        trigger="operator_evidence",
        intent=f"record public feedback for {evidence_slug}",
        outcome="completed",
        delta=delta,
        causal_links=[],
        confidence=0.95,
        memory_class="epistemic",
        artifacts=[str(evidence_artifact)],
        eval_refs=eval_refs,
    )
    default_ledger(root).append(record)
    return PublicEvidenceRecordResult(record=record, evidence_artifact=evidence_artifact, preview_hash="")


def record_customer_discovery_feedback(
    *,
    source: str,
    insight: str,
    root: Path | str | None = None,
    feedback_url: str | None = None,
    notes: str | None = None,
    packet_topic: str | None = None,
    packet_question: str | None = None,
) -> CustomerDiscoveryFeedbackResult:
    """Append validated independent customer-discovery feedback evidence."""

    feedback_url_value = _validate_public_url(feedback_url, "feedback_url") if feedback_url else ""
    source_value = _validate_feedback_source(source or feedback_url_value)
    if not source_value:
        raise ValueError("source or feedback_url is required")
    insight_value = _validate_customer_discovery_insight(insight)
    paths = default_v3_paths(root)
    run_id = new_run_id("customer_discovery_feedback")
    artifact_dir = paths.artifacts / "customer_discovery_feedback" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    eval_ref = f"customer_discovery:{_eval_ref_component(source_value)}"
    evidence_payload = {
        "record_id": run_id,
        "source": source_value,
        "feedback_url": feedback_url_value,
        "insight": insight_value,
        "notes": (notes or "").strip(),
        "packet_topic": (packet_topic or "").strip(),
        "packet_question": (packet_question or "").strip(),
        "eval_refs": [eval_ref],
    }
    evidence_artifact = artifact_dir / "customer_discovery_feedback.json"
    evidence_artifact.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta = MemoryDelta.no_kernel_change(
        pipeline="a2a_trust_experiment",
        run_id=run_id,
        memory_class="epistemic",
        what_happened=f"Recorded customer-discovery feedback from {source_value}.",
        what_mattered="Strategic scorecard should count concrete external discovery feedback even when it is not tied to a public writeup.",
        what_changed="A validated customer_discovery ref was appended from operator-supplied evidence.",
        trust_tier="human_confirmed",
    )
    record = ExperienceRecord(
        id=run_id,
        pipeline="a2a_trust_experiment",
        trigger="operator_evidence",
        intent="record customer discovery feedback",
        outcome="completed",
        delta=delta,
        causal_links=[],
        confidence=0.95,
        memory_class="epistemic",
        artifacts=[str(evidence_artifact)],
        eval_refs=[eval_ref],
    )
    default_ledger(root).append(record)
    return CustomerDiscoveryFeedbackResult(record=record, evidence_artifact=evidence_artifact, eval_ref=eval_ref)


def record_briefing_feedback(
    *,
    item_id: str,
    button: str,
    root: Path | str | None = None,
    notes: str | None = None,
) -> BriefingFeedbackRecordResult:
    """Append validated operator feedback for a weekly blind-sample briefing item."""

    from mira.evals import BRIEFING_FEEDBACK_ACTIONS, build_weekly_blind_sample

    normalized_item_id = _validate_briefing_item_id(item_id)
    normalized_button = _normalize_briefing_feedback_button(button)
    action = BRIEFING_FEEDBACK_ACTIONS.get(normalized_button)
    if action is None:
        raise ValueError(f"button must be one of: {', '.join(sorted(BRIEFING_FEEDBACK_ACTIONS))}")
    ledger = default_ledger(root)
    current_records = ledger.list()
    blind_sample = build_weekly_blind_sample(current_records)
    item_by_id = {item.item_id: item for item in blind_sample}
    if normalized_item_id not in item_by_id:
        raise ValueError("item_id must match an unreviewed item in the current weekly blind sample")
    item = item_by_id[normalized_item_id]
    paths = default_v3_paths(root)
    run_id = new_run_id("briefing_feedback")
    artifact_dir = paths.artifacts / "briefing_feedback" / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    eval_ref = f"briefing_feedback:item={normalized_item_id}:button={normalized_button}"
    evidence_payload = {
        "record_id": run_id,
        "item_id": normalized_item_id,
        "button": normalized_button,
        "action": action,
        "topics": item.topics,
        "matched_interest_ids": item.matched_interest_ids,
        "novelty_score": item.novelty_score,
        "actionability_score": item.actionability_score,
        "notes": (notes or "").strip(),
        "eval_refs": [eval_ref],
    }
    evidence_artifact = artifact_dir / "briefing_feedback.json"
    evidence_artifact.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    delta = MemoryDelta.no_kernel_change(
        pipeline="intelligence_briefing",
        run_id=run_id,
        memory_class="epistemic",
        what_happened=f"Recorded briefing feedback for {normalized_item_id}.",
        what_mattered="Eval 4 should reflect operator interest feedback on blind-sample briefing items.",
        what_changed="A validated briefing feedback ref was appended from operator-supplied input.",
        trust_tier="human_confirmed",
    )
    record = ExperienceRecord(
        id=run_id,
        pipeline="intelligence_briefing",
        trigger="operator_feedback",
        intent=f"record briefing feedback for {normalized_item_id}",
        outcome="completed",
        delta=delta,
        causal_links=[],
        confidence=0.95,
        memory_class="epistemic",
        artifacts=[str(evidence_artifact)],
        eval_refs=[eval_ref],
    )
    ledger.append(record)
    return BriefingFeedbackRecordResult(record=record, evidence_artifact=evidence_artifact, eval_ref=eval_ref)


def _public_evidence_record_command(slug: str, draft_path: Path, preview_hash: str) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_public_evidence.py",
            "--slug",
            slug,
            "--published-url",
            "<url>",
            "--draft-artifact",
            str(draft_path),
            "--expected-preview-hash",
            preview_hash,
            "--feedback-source",
            "<source>",
            "--json",
        ]
    )


def _public_evidence_packet_record_command(metadata_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_public_evidence.py",
            "--packet",
            str(metadata_artifact),
            "--published-url",
            "<url>",
            "--feedback-source",
            "<source>",
            "--json",
        ]
    )


def _public_feedback_record_command(slug: str, published_url: str) -> str:
    parts = [
        "PYTHONPATH=lib",
        ".venv/bin/python",
        "agents/super/cli/v3_record_public_feedback.py",
        "--slug",
        slug,
        "--feedback-source",
        "<source>",
    ]
    if published_url:
        parts.extend(["--published-url", published_url])
    parts.append("--json")
    return " ".join(parts)


def _public_feedback_packet_record_command(metadata_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_public_feedback.py",
            "--packet",
            str(metadata_artifact),
            "--feedback-source",
            "<source>",
            "--json",
        ]
    )


def _customer_discovery_record_command() -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--source",
            "<source>",
            "--insight",
            "<insight>",
            "--json",
        ]
    )


def _customer_discovery_packet_record_command(metadata_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_customer_discovery_feedback.py",
            "--packet",
            str(metadata_artifact),
            "--source",
            "<source>",
            "--insight",
            "<insight>",
            "--json",
        ]
    )


def _briefing_feedback_record_command(item_id: str) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_briefing_feedback.py",
            "--item-id",
            item_id,
            "--button",
            "<button>",
            "--json",
        ]
    )


def _briefing_feedback_packet_record_command(metadata_artifact: Path) -> str:
    return " ".join(
        [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_record_briefing_feedback.py",
            "--packet",
            str(metadata_artifact),
            "--button",
            "<button>",
            "--json",
        ]
    )


def _public_feedback_stats_snapshot(public_url: str, stats_path: Path | None) -> dict[str, Any]:
    if stats_path is None:
        return {}
    try:
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    fetched_at = str(payload.get("fetched_at") or "")
    for article in payload.get("articles", []):
        if not isinstance(article, dict):
            continue
        article_id = article.get("id")
        slug = article.get("slug")
        urls = set()
        if article_id:
            urls.add(f"https://uncountablemira.substack.com/p/{article_id}")
        if slug:
            urls.add(f"https://uncountablemira.substack.com/p/{slug}")
        if public_url in urls:
            return {**article, "fetched_at": fetched_at}
    return {}


def public_writeup_safety_report(draft_artifact: Path | str | None) -> PublicWriteupSafetyReport:
    """Run a deterministic no-network publication safety audit for a draft."""

    if not draft_artifact:
        return PublicWriteupSafetyReport(
            draft_artifact="", preview_hash="", passed=False, findings=["draft artifact is missing"]
        )
    draft_path = Path(draft_artifact)
    findings: list[str] = []
    if not draft_path.exists() or not draft_path.is_file():
        return PublicWriteupSafetyReport(
            draft_artifact=str(draft_path),
            preview_hash="",
            passed=False,
            findings=[f"draft artifact does not exist: {draft_path}"],
        )
    content = draft_path.read_text(encoding="utf-8")
    preview_hash = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    if not content.strip():
        findings.append("draft is empty")
    if len(content.encode("utf-8")) > 100_000:
        findings.append("draft is unexpectedly large for public review")
    checks = [
        (re.compile(r"/Users/[^\s)`'\"<>{}]+"), "private local filesystem path"),
        (re.compile(r"\bMIRA_[A-Z0-9_]*(TOKEN|SECRET|KEY)\b"), "credential environment variable"),
        (re.compile(r"\b[a-z]{2}-[A-Za-z0-9_-]{20,}\b"), "secret-looking token"),
        (
            re.compile(r"(?i)\b(api[_ -]?key|access[_ -]?token|bearer[_ -]?token|password|secret)\s*[:=]"),
            "credential assignment",
        ),
        (re.compile(r"(?i)<(url|source|todo|redact|placeholder)>"), "unresolved placeholder"),
        (re.compile(r"(?i)\b(TODO|TBD|REDACT)\b"), "unresolved editorial marker"),
    ]
    for line_number, line in enumerate(content.splitlines(), start=1):
        for pattern, label in checks:
            if pattern.search(line):
                findings.append(f"line {line_number}: {label}")
    return PublicWriteupSafetyReport(
        draft_artifact=str(draft_path),
        preview_hash=preview_hash,
        passed=not findings,
        findings=findings,
    )


def _validate_public_evidence_slug(value: str) -> str:
    slug = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", slug):
        raise ValueError("slug must be 1-81 characters of letters, numbers, '_', '.', or '-'")
    return slug


def _validate_public_url(value: str | None, field_name: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.lower() in {"<url>", "url=<url>", "todo", "tbd"} or any(char in raw for char in "\r\n<>"):
        raise ValueError(f"{field_name} must be a concrete http(s) URL")
    from urllib.parse import urlparse

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a concrete http(s) URL")
    return raw


def _validate_feedback_source(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.lower() in {"<source>", "source=<source>", "todo", "tbd"} or any(char in raw for char in "\r\n<>"):
        raise ValueError("feedback_source must be concrete when supplied")
    if raw.startswith(("http://", "https://")):
        return _validate_public_url(raw, "feedback_source")
    return raw


def _validate_customer_discovery_topic(value: str) -> str:
    topic = (value or "").strip().lower().replace(" ", "_")
    if _looks_placeholder(topic) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,80}", topic):
        raise ValueError("topic must be 1-81 characters of lowercase letters, numbers, '_', '.', or '-'")
    return topic


def _validate_customer_discovery_insight(value: str) -> str:
    insight = (value or "").strip()
    if _looks_placeholder(insight) or len(insight) < 10:
        raise ValueError("insight must be a concrete external-feedback summary")
    return insight


def _looks_placeholder(value: str | None) -> bool:
    raw = (value or "").strip()
    return (
        not raw
        or raw.lower() in {"<source>", "<insight>", "<question>", "<topic>", "todo", "tbd"}
        or any(char in raw for char in "\r\n<>")
    )


def _validate_briefing_item_id(value: str) -> str:
    item_id = (value or "").strip()
    if (
        not item_id
        or item_id.lower() in {"<item_id>", "item_id=<item_id>", "<item>", "todo", "tbd"}
        or any(char in item_id for char in "\r\n<>")
    ):
        raise ValueError("item_id must be a concrete weekly blind-sample item id")
    if not re.fullmatch(r"briefing_item:[A-Za-z0-9_.:-]{1,160}", item_id):
        raise ValueError("item_id must be a concrete briefing_item:* id")
    return item_id


def _normalize_briefing_feedback_button(value: str) -> str:
    button = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if (
        not button
        or button in {"<button>", "button=<button>", "todo", "tbd"}
        or any(char in button for char in "\r\n<>")
    ):
        raise ValueError("button must be concrete")
    return button


def _recorded_public_writeup_url(root: Path | str | None, slug: str) -> str | None:
    for record in reversed(default_ledger(root).list()):
        for ref in record.eval_refs:
            parsed_slug, parsed_url = _parse_public_writeup_ref(ref)
            if parsed_slug == slug:
                return parsed_url
    return None


def _parse_public_writeup_ref(ref: str) -> tuple[str, str] | tuple[None, None]:
    stripped = ref.strip()
    prefixes = ("public_writeup:", "public_note:", "published_writeup:")
    if not stripped.startswith(prefixes):
        return (None, None)
    parts = stripped.split(":", 2)
    if len(parts) < 2:
        return (None, None)
    slug = parts[1].split("=", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", slug):
        return (None, None)
    url_match = re.search(r":url=([^\s]+)$", stripped)
    return (slug, url_match.group(1) if url_match else "")


def _parse_public_feedback_ref(ref: str) -> str:
    stripped = ref.strip()
    if not stripped.startswith(("external_feedback:", "public_feedback:", "reader_feedback:")):
        return ""
    parts = stripped.split(":", 2)
    if len(parts) < 2:
        return ""
    slug = parts[1].split("=", 1)[0].strip()
    return slug if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", slug) else ""


def _public_url_from_record_artifacts(artifacts: list[str]) -> str:
    for artifact in artifacts:
        path = Path(artifact)
        if path.name not in {"public_evidence.json", "public_feedback.json"}:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        published_url = str(payload.get("published_url") or "")
        if published_url:
            return published_url
    return ""


def _resolve_public_evidence_draft(
    draft_artifact: Path | str | None,
    *,
    workspace_root: Path,
    expected_preview_hash: str | None,
) -> tuple[Path | None, str]:
    expected_hash = (expected_preview_hash or "").strip()
    if expected_hash and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValueError("expected_preview_hash must be a 64-character sha256 hex digest")
    if not draft_artifact:
        return None, ""
    draft_path = Path(draft_artifact)
    if not draft_path.is_absolute():
        draft_path = workspace_root / draft_path
    if not draft_path.exists() or not draft_path.is_file():
        raise ValueError(f"draft_artifact does not exist: {draft_path}")
    preview_hash = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    if expected_hash and preview_hash.lower() != expected_hash.lower():
        raise ValueError("draft_artifact preview hash does not match expected_preview_hash")
    return draft_path, preview_hash


def _eval_ref_component(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip())
    return re.sub(r"[^A-Za-z0-9_.:/?#=&%-]", "-", normalized)[:160]


def provider_resolver_config_template(
    providers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    selected = tuple(PRODUCTION_PROVIDER_RESOLVER_PROFILES) if providers is None else tuple(providers)
    unknown = [provider for provider in selected if provider not in PRODUCTION_PROVIDER_RESOLVER_PROFILES]
    if unknown:
        raise KeyError(f"Unknown provider resolver profile(s): {', '.join(unknown)}")
    return {
        "provider_effect_resolvers": {
            provider: dict(PRODUCTION_PROVIDER_RESOLVER_PROFILES[provider]) for provider in selected
        }
    }


def write_provider_resolver_config_template(
    path: Path | str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    overwrite: bool = False,
) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"provider resolver config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(provider_resolver_config_template(providers), indent=2, sort_keys=True), encoding="utf-8"
    )
    return target


def provider_adapter_config_template(
    providers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    selected = tuple(PRODUCTION_PROVIDER_ADAPTER_PROFILES) if providers is None else tuple(providers)
    unknown = [provider for provider in selected if provider not in PRODUCTION_PROVIDER_ADAPTER_PROFILES]
    if unknown:
        raise KeyError(f"Unknown provider adapter profile(s): {', '.join(unknown)}")
    return {
        "provider_effect_adapters": {
            provider: dict(PRODUCTION_PROVIDER_ADAPTER_PROFILES[provider]) for provider in selected
        }
    }


def write_provider_adapter_config_template(
    path: Path | str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    overwrite: bool = False,
) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"provider adapter config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(provider_adapter_config_template(providers), indent=2, sort_keys=True), encoding="utf-8"
    )
    return target


def provider_provisioning_env_template(
    *,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    root: Path | str | None = None,
) -> str:
    env_rows = _provider_provisioning_env_rows(
        resolver_config_path=resolver_config_path,
        adapter_config_path=adapter_config_path,
        required_resolvers=required_resolvers,
        required_adapters=required_adapters,
        root=root,
    )
    lines = [
        "# Mira V3 provider provisioning template",
        "# Fill these values in your shell, launchd plist, or local secret manager.",
        "# Do not commit real token values.",
        "",
    ]
    for row in env_rows:
        lines.append(f"# {row['surface']}.{row['provider']} {row['field']}")
        lines.append(f"{row['env']}={row['placeholder']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def provider_provisioning_runbook(
    *,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    root: Path | str | None = None,
    allow_inline_secrets: bool = False,
) -> str:
    """Render a no-secret operator runbook for provider provisioning."""

    paths = default_v3_paths(root)
    workspace_root = Path(root) if root is not None else paths.root.parents[1]
    resolver_path = Path(resolver_config_path) if resolver_config_path is not None else paths.provider_resolvers
    adapter_path = Path(adapter_config_path) if adapter_config_path is not None else paths.provider_adapters
    report = provider_production_readiness_report(
        root=root,
        resolver_config_path=resolver_path,
        adapter_config_path=adapter_path,
        required_resolvers=required_resolvers,
        required_adapters=required_adapters,
        allow_inline_secrets=allow_inline_secrets,
    )
    env_rows = _provider_provisioning_env_rows(
        resolver_config_path=resolver_path,
        adapter_config_path=adapter_path,
        required_resolvers=required_resolvers,
        required_adapters=required_adapters,
        root=root,
    )
    lines = [
        "# Mira V3 Provider Provisioning Runbook",
        "",
        f"Status: {'ready' if report['ready'] else 'blocked'}",
        f"Resolver config: {resolver_path}",
        f"Adapter config: {adapter_path}",
        "",
        "## Safety Rules",
        "",
        "- Do not commit real token values.",
        "- Store real values in the shell, launchd environment, or local secret manager.",
        "- Rerun readiness before any production canary.",
        "- Keep every canary behind approval, effect logs, and reconciliation.",
        "",
        "## Required Environment",
        "",
        "```dotenv",
    ]
    for row in env_rows:
        lines.append(f"# {row['surface']}.{row['provider']} {row['field']}")
        lines.append(f"{row['env']}={row['placeholder']}")
        lines.append("")
    lines.extend(["```", "", "## Current Findings", ""])
    active_findings = [
        (surface, provider, item)
        for surface, surface_findings in report["findings"].items()
        for provider, items in surface_findings.items()
        for item in items
    ]
    if active_findings:
        for surface, provider, item in active_findings:
            lines.append(f"- {surface}.{provider}: {item}")
    else:
        lines.append("- No readiness findings for the selected scope.")
    lines.extend(["", "## Scoped Canary Commands", ""])
    command_rows = _provider_canary_command_rows(
        workspace_root=workspace_root,
        resolver_config_path=resolver_path,
        adapter_config_path=adapter_path,
        required_resolvers=required_resolvers,
        required_adapters=required_adapters,
    )
    recommended = _recommended_provider_canary_row(command_rows, env_rows)
    if recommended:
        missing_env_vars = recommended.get("missing_env_vars", [])
        lines.extend(
            [
                "## Recommended First Canary",
                "",
                f"- Provider: {recommended['provider']}",
                f"- Missing env vars: {', '.join(missing_env_vars) if missing_env_vars else 'none'}",
                "",
                "```bash",
                recommended["readiness_command"],
                recommended["dry_run_command"],
                recommended["canary_command"],
                "```",
                "",
            ]
        )
    if not command_rows:
        lines.append("- No production canary providers are selected by this scope.")
    for row in command_rows:
        lines.extend(
            [
                f"### {row['provider']}",
                "",
                "```bash",
                row["readiness_command"],
                row["dry_run_command"],
                row["canary_command"],
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_provider_provisioning_runbook(
    path: Path | str,
    *,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    root: Path | str | None = None,
    allow_inline_secrets: bool = False,
    overwrite: bool = False,
) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"provider provisioning runbook already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        provider_provisioning_runbook(
            resolver_config_path=resolver_config_path,
            adapter_config_path=adapter_config_path,
            required_resolvers=required_resolvers,
            required_adapters=required_adapters,
            root=root,
            allow_inline_secrets=allow_inline_secrets,
        ),
        encoding="utf-8",
    )
    return target


def write_provider_provisioning_env_template(
    path: Path | str,
    *,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    root: Path | str | None = None,
    overwrite: bool = False,
) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"provider provisioning env template already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        provider_provisioning_env_template(
            resolver_config_path=resolver_config_path,
            adapter_config_path=adapter_config_path,
            required_resolvers=required_resolvers,
            required_adapters=required_adapters,
            root=root,
        ),
        encoding="utf-8",
    )
    return target


def _provider_provisioning_env_rows(
    *,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    root: Path | str | None = None,
) -> list[dict[str, str]]:
    paths = default_v3_paths(root)
    resolver_path = Path(resolver_config_path) if resolver_config_path is not None else paths.provider_resolvers
    adapter_path = Path(adapter_config_path) if adapter_config_path is not None else paths.provider_adapters
    resolver_providers = _providers_for_env_template(
        resolver_path,
        top_key="provider_effect_resolvers",
        fallback_profiles=PRODUCTION_PROVIDER_RESOLVER_PROFILES,
        required=(
            tuple(PRODUCTION_PROVIDER_RESOLVER_PROFILES) if required_resolvers is None else tuple(required_resolvers)
        ),
    )
    adapter_providers = _providers_for_env_template(
        adapter_path,
        top_key="provider_effect_adapters",
        fallback_profiles=PRODUCTION_PROVIDER_ADAPTER_PROFILES,
        required=(
            tuple(PRODUCTION_PROVIDER_ADAPTER_PROFILES) if required_adapters is None else tuple(required_adapters)
        ),
    )
    return [
        *_provider_env_rows("provider_resolvers", resolver_providers),
        *_provider_env_rows("provider_adapters", adapter_providers),
    ]


def _provider_canary_command_rows(
    *,
    workspace_root: Path,
    resolver_config_path: Path,
    adapter_config_path: Path,
    required_resolvers: list[str] | tuple[str, ...] | None,
    required_adapters: list[str] | tuple[str, ...] | None,
) -> list[dict[str, str]]:
    resolver_required = set(PRODUCTION_PROVIDER_RESOLVER_PROFILES if required_resolvers is None else required_resolvers)
    adapter_required = set(PRODUCTION_PROVIDER_ADAPTER_PROFILES if required_adapters is None else required_adapters)
    rows: list[dict[str, str]] = []
    for provider, surface in sorted(provider_production_canary_surface().items()):
        if provider not in adapter_required:
            continue
        if surface["requires_resolver"] and provider not in resolver_required:
            continue
        readiness_parts = [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_provider_readiness.py",
            "--root",
            str(workspace_root),
            "--resolver-config",
            str(resolver_config_path),
            "--adapter-config",
            str(adapter_config_path),
        ]
        if surface["requires_resolver"]:
            readiness_parts.extend(["--require-resolver", provider])
        else:
            readiness_parts.append("--skip-resolvers")
        readiness_parts.extend(["--require-adapter", provider, "--json"])
        canary_parts = [
            "PYTHONPATH=lib",
            ".venv/bin/python",
            "agents/super/cli/v3_provider_production_canary.py",
            "--root",
            str(workspace_root),
            "--resolver-config",
            str(resolver_config_path),
            "--adapter-config",
            str(adapter_config_path),
            "--provider",
            provider,
            "--json",
        ]
        dry_run_parts = [*canary_parts[:-1], "--dry-run", "--json"]
        rows.append(
            {
                "provider": provider,
                "readiness_command": " ".join(readiness_parts),
                "dry_run_command": " ".join(dry_run_parts),
                "canary_command": " ".join(canary_parts),
            }
        )
    return rows


def _recommended_provider_canary_row(
    command_rows: list[dict[str, str]], env_rows: list[dict[str, str]]
) -> dict[str, Any] | None:
    if not command_rows:
        return None
    surface = provider_production_canary_surface()
    priority = {
        provider: index for index, provider in enumerate(("tts", "social", "substack", "rss", "market", "health"))
    }
    ranked = []
    for row in command_rows:
        provider = str(row["provider"])
        missing_env_vars = _provider_canary_env_vars(env_rows, provider)
        ranked.append(
            {
                **row,
                "missing_env_vars": missing_env_vars,
                "_rank": (
                    len(missing_env_vars),
                    bool(surface.get(provider, {}).get("requires_resolver")),
                    priority.get(provider, len(priority)),
                    provider,
                ),
            }
        )
    best = min(ranked, key=lambda item: item["_rank"])
    return {key: value for key, value in best.items() if key != "_rank"}


def _provider_canary_env_vars(env_rows: list[dict[str, str]], provider: str) -> list[str]:
    seen: set[str] = set()
    env_vars: list[str] = []
    for row in env_rows:
        if row.get("provider") != provider:
            continue
        env_name = str(row.get("env") or "")
        if not env_name or env_name in seen:
            continue
        seen.add(env_name)
        env_vars.append(env_name)
    return env_vars


def reconcile_provider_effects(
    *,
    root: Path | str | None = None,
    publish_manifest_path: Path | str | None = None,
    rss_feed_paths: list[Path | str] | None = None,
    provider_state_manifest_paths: list[Path | str] | None = None,
    provider_resolvers: Mapping[str, ProviderEffectResolver] | None = None,
    provider_config_path: Path | str | None = None,
    provider_http_clients: Mapping[str, Any] | None = None,
):
    paths = default_v3_paths(root)
    config_path = Path(provider_config_path) if provider_config_path is not None else paths.provider_resolvers
    configured = load_provider_resolvers_from_config(config_path, http_clients=provider_http_clients)
    merged_resolvers = {**configured, **dict(provider_resolvers or {})}
    local_provider_state = list(provider_state_manifest_paths or [])
    if paths.provider_state_manifests.exists():
        local_provider_state.extend(sorted(paths.provider_state_manifests.glob("*.json")))
    return reconcile_effects_from_provider_state(
        EffectLog(paths.effect_log),
        publish_manifest_path=publish_manifest_path,
        rss_feed_paths=rss_feed_paths,
        provider_state_manifest_paths=local_provider_state or None,
        provider_resolvers=merged_resolvers or None,
    )


def load_provider_resolvers_from_config(
    path: Path | str,
    *,
    http_clients: Mapping[str, Any] | None = None,
    allow_inline_secrets: bool = False,
) -> dict[str, ProviderEffectResolver]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    providers = config.get("provider_effect_resolvers") or {}
    if not isinstance(providers, dict):
        return {}
    resolvers: dict[str, ProviderEffectResolver] = {}
    for name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        if provider_config.get("type") != "http_json":
            continue
        endpoint_template = _resolved_provider_endpoint_template(provider_config)
        if _provider_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
            continue
        headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
        bearer_token = provider_config.get("bearer_token")
        bearer_token_env = provider_config.get("bearer_token_env")
        if bearer_token_env and not bearer_token:
            bearer_token = getenv(str(bearer_token_env))
        payload_path = provider_config.get("payload_path") or []
        if isinstance(payload_path, str):
            payload_path = [payload_path]
        elif not isinstance(payload_path, (list, tuple)):
            payload_path = []
        resolvers[str(name)] = HttpJsonProviderResolver(
            endpoint_template=endpoint_template,
            headers={str(key): str(value) for key, value in headers.items()},
            bearer_token=str(bearer_token) if bearer_token else None,
            timeout_s=float(provider_config.get("timeout_s", 10.0)),
            payload_path=tuple(str(item) for item in payload_path),
            client=(http_clients or {}).get(str(name)),
        )
    return resolvers


def validate_provider_resolver_config(
    path: Path | str,
    *,
    allow_inline_secrets: bool = False,
) -> dict[str, list[str]]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"_config": [f"invalid_json: {exc}"]}
    providers = config.get("provider_effect_resolvers") or {}
    if not isinstance(providers, dict):
        return {"provider_effect_resolvers": ["must be an object"]}
    findings: dict[str, list[str]] = {}
    for name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            findings[str(name)] = ["provider config must be an object"]
            continue
        provider_findings = _provider_config_findings(
            provider_config,
            allow_inline_secrets=allow_inline_secrets,
        )
        if provider_findings:
            findings[str(name)] = provider_findings
    return findings


def load_provider_adapters_from_config(
    path: Path | str,
    *,
    http_clients: Mapping[str, httpx.Client] | None = None,
    artifact_root: Path | str | None = None,
    allow_inline_secrets: bool = False,
) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    providers = config.get("provider_effect_adapters") or {}
    if not isinstance(providers, dict):
        return {}
    adapters: dict[str, Any] = {}
    for name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        provider_type = provider_config.get("type")
        if provider_type == "hosted_rss_http":
            if _hosted_rss_http_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
                continue
            endpoint_template = _resolved_provider_endpoint_template(provider_config)
            headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
            bearer_token = provider_config.get("bearer_token")
            bearer_token_env = provider_config.get("bearer_token_env")
            if bearer_token_env and not bearer_token:
                bearer_token = getenv(str(bearer_token_env))
            payload_path = provider_config.get("payload_path") or []
            if isinstance(payload_path, str):
                payload_path = [payload_path]
            elif not isinstance(payload_path, (list, tuple)):
                payload_path = []
            adapters[str(name)] = HostedRssHttpAdapter(
                endpoint_template=endpoint_template,
                headers={str(key): str(value) for key, value in headers.items()},
                bearer_token=str(bearer_token) if bearer_token else None,
                timeout_s=float(provider_config.get("timeout_s", 10.0)),
                payload_path=tuple(str(item) for item in payload_path),
                method=str(provider_config.get("method") or "POST").upper(),
                client=(http_clients or {}).get(str(name)),
                artifact_root=artifact_root or default_v3_paths().artifacts,
            )
            continue
        if provider_type == "hosted_social_http":
            if _hosted_social_http_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
                continue
            endpoint_template = _resolved_provider_endpoint_template(provider_config)
            headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
            bearer_token = provider_config.get("bearer_token")
            bearer_token_env = provider_config.get("bearer_token_env")
            if bearer_token_env and not bearer_token:
                bearer_token = getenv(str(bearer_token_env))
            payload_path = provider_config.get("payload_path") or []
            if isinstance(payload_path, str):
                payload_path = [payload_path]
            elif not isinstance(payload_path, (list, tuple)):
                payload_path = []
            adapters[str(name)] = HostedSocialHttpAdapter(
                endpoint_template=endpoint_template,
                headers={str(key): str(value) for key, value in headers.items()},
                bearer_token=str(bearer_token) if bearer_token else None,
                timeout_s=float(provider_config.get("timeout_s", 10.0)),
                payload_path=tuple(str(item) for item in payload_path),
                method=str(provider_config.get("method") or "POST").upper(),
                client=(http_clients or {}).get(str(name)),
                artifact_root=artifact_root or default_v3_paths().artifacts,
            )
            continue
        if provider_type == "hosted_market_http":
            if _hosted_market_http_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
                continue
            endpoint_template = _resolved_provider_endpoint_template(provider_config)
            headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
            bearer_token = provider_config.get("bearer_token")
            bearer_token_env = provider_config.get("bearer_token_env")
            if bearer_token_env and not bearer_token:
                bearer_token = getenv(str(bearer_token_env))
            payload_path = provider_config.get("payload_path") or []
            if isinstance(payload_path, str):
                payload_path = [payload_path]
            elif not isinstance(payload_path, (list, tuple)):
                payload_path = []
            adapters[str(name)] = HostedMarketHttpAdapter(
                endpoint_template=endpoint_template,
                headers={str(key): str(value) for key, value in headers.items()},
                bearer_token=str(bearer_token) if bearer_token else None,
                timeout_s=float(provider_config.get("timeout_s", 10.0)),
                payload_path=tuple(str(item) for item in payload_path),
                method=str(provider_config.get("method") or "POST").upper(),
                client=(http_clients or {}).get(str(name)),
                artifact_root=artifact_root or default_v3_paths().artifacts,
            )
            continue
        if provider_type == "hosted_health_http":
            if _hosted_health_http_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
                continue
            endpoint_template = _resolved_provider_endpoint_template(provider_config)
            headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
            bearer_token = provider_config.get("bearer_token")
            bearer_token_env = provider_config.get("bearer_token_env")
            if bearer_token_env and not bearer_token:
                bearer_token = getenv(str(bearer_token_env))
            payload_path = provider_config.get("payload_path") or []
            if isinstance(payload_path, str):
                payload_path = [payload_path]
            elif not isinstance(payload_path, (list, tuple)):
                payload_path = []
            adapters[str(name)] = HostedHealthHttpAdapter(
                endpoint_template=endpoint_template,
                headers={str(key): str(value) for key, value in headers.items()},
                bearer_token=str(bearer_token) if bearer_token else None,
                timeout_s=float(provider_config.get("timeout_s", 10.0)),
                payload_path=tuple(str(item) for item in payload_path),
                method=str(provider_config.get("method") or "POST").upper(),
                client=(http_clients or {}).get(str(name)),
                artifact_root=artifact_root or default_v3_paths().artifacts,
            )
            continue
        if provider_type == "hosted_tts_http":
            if _hosted_tts_http_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
                continue
            endpoint_template = _resolved_provider_endpoint_template(provider_config)
            headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
            bearer_token = provider_config.get("bearer_token")
            bearer_token_env = provider_config.get("bearer_token_env")
            if bearer_token_env and not bearer_token:
                bearer_token = getenv(str(bearer_token_env))
            payload_path = provider_config.get("payload_path") or []
            if isinstance(payload_path, str):
                payload_path = [payload_path]
            elif not isinstance(payload_path, (list, tuple)):
                payload_path = []
            adapters[str(name)] = HostedTtsHttpAdapter(
                endpoint_template=endpoint_template,
                headers={str(key): str(value) for key, value in headers.items()},
                bearer_token=str(bearer_token) if bearer_token else None,
                timeout_s=float(provider_config.get("timeout_s", 30.0)),
                payload_path=tuple(str(item) for item in payload_path),
                method=str(provider_config.get("method") or "POST").upper(),
                client=(http_clients or {}).get(str(name)),
                artifact_root=artifact_root or default_v3_paths().artifacts,
                audio_base64_field=str(provider_config.get("audio_base64_field") or "audio_base64"),
                audio_url_field=str(provider_config.get("audio_url_field") or "audio_url"),
            )
            continue
        if provider_type == "local_tts_command":
            if _local_tts_command_adapter_config_findings(provider_config):
                continue
            adapters[str(name)] = LocalTtsCommandAdapter(
                command=tuple(str(part) for part in provider_config.get("command", [])),
                artifact_root=artifact_root or default_v3_paths().artifacts,
            )
            continue
        if provider_type == "local_deployment_command":
            if _local_deployment_command_adapter_config_findings(provider_config, provider_name=str(name)):
                continue
            adapters[str(name)] = LocalDeploymentCommandAdapter(
                role=str(provider_config.get("role") or name),
                command=tuple(str(part) for part in provider_config.get("command", [])),
                artifact_root=artifact_root or default_v3_paths().artifacts,
                timeout_s=float(provider_config.get("timeout_s", 120.0)),
            )
            continue
        if provider_type == "local_provider_state":
            if _local_provider_state_adapter_config_findings(provider_config):
                continue
            manifest_path = _resolved_config_path(provider_config, "manifest_path", "manifest_path_env")
            adapters[str(name)] = LocalProviderStateAdapter(
                provider=str(provider_config.get("provider") or name),
                manifest_path=manifest_path,
                artifact_root=artifact_root or default_v3_paths().artifacts,
                preview_filename=str(
                    provider_config.get("preview_filename") or _preview_filename_for_provider(str(name))
                ),
            )
            continue
        if provider_type == "local_rss_feed":
            if _local_rss_adapter_config_findings(provider_config):
                continue
            feed_path = _resolved_config_path(provider_config, "feed_path", "feed_path_env")
            adapters[str(name)] = LocalRssFeedAdapter(
                feed_path=feed_path,
                artifact_root=artifact_root or default_v3_paths().artifacts,
                channel_title=str(provider_config.get("channel_title") or "Mira Podcast"),
                channel_link=str(provider_config.get("channel_link") or "https://mira.local/podcast"),
                channel_description=str(provider_config.get("channel_description") or "Mira generated podcast feed"),
            )
            continue
        if provider_type != "http_json":
            continue
        endpoint_template = _resolved_provider_endpoint_template(provider_config)
        if _provider_adapter_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets):
            continue
        headers = provider_config.get("headers") if isinstance(provider_config.get("headers"), dict) else {}
        bearer_token = provider_config.get("bearer_token")
        bearer_token_env = provider_config.get("bearer_token_env")
        if bearer_token_env and not bearer_token:
            bearer_token = getenv(str(bearer_token_env))
        payload_path = provider_config.get("payload_path") or []
        if isinstance(payload_path, str):
            payload_path = [payload_path]
        elif not isinstance(payload_path, (list, tuple)):
            payload_path = []
        adapters[str(name)] = HttpJsonProviderAdapter(
            endpoint_template=endpoint_template,
            headers={str(key): str(value) for key, value in headers.items()},
            bearer_token=str(bearer_token) if bearer_token else None,
            timeout_s=float(provider_config.get("timeout_s", 10.0)),
            payload_path=tuple(str(item) for item in payload_path),
            method=str(provider_config.get("method") or "POST").upper(),
            client=(http_clients or {}).get(str(name)),
            artifact_root=artifact_root,
            preview_filename=str(provider_config.get("preview_filename") or ""),
        )
    return adapters


def validate_provider_adapter_config(
    path: Path | str,
    *,
    allow_inline_secrets: bool = False,
) -> dict[str, list[str]]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"_config": [f"invalid_json: {exc}"]}
    providers = config.get("provider_effect_adapters") or {}
    if not isinstance(providers, dict):
        return {"provider_effect_adapters": ["must be an object"]}
    findings: dict[str, list[str]] = {}
    for name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            findings[str(name)] = ["provider adapter config must be an object"]
            continue
        if provider_config.get("type") == "local_rss_feed":
            provider_findings = _local_rss_adapter_config_findings(provider_config)
        elif provider_config.get("type") == "hosted_rss_http":
            provider_findings = _hosted_rss_http_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        elif provider_config.get("type") == "hosted_social_http":
            provider_findings = _hosted_social_http_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        elif provider_config.get("type") == "hosted_market_http":
            provider_findings = _hosted_market_http_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        elif provider_config.get("type") == "hosted_health_http":
            provider_findings = _hosted_health_http_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        elif provider_config.get("type") == "local_provider_state":
            provider_findings = _local_provider_state_adapter_config_findings(provider_config)
        elif provider_config.get("type") == "hosted_tts_http":
            provider_findings = _hosted_tts_http_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        elif provider_config.get("type") == "local_tts_command":
            provider_findings = _local_tts_command_adapter_config_findings(provider_config)
        elif provider_config.get("type") == "local_deployment_command":
            provider_findings = _local_deployment_command_adapter_config_findings(
                provider_config,
                provider_name=str(name),
            )
        else:
            provider_findings = _provider_adapter_config_findings(
                provider_config,
                allow_inline_secrets=allow_inline_secrets,
            )
        if provider_findings:
            findings[str(name)] = provider_findings
    return findings


def provider_production_readiness_report(
    *,
    root: Path | str | None = None,
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    required_resolvers: list[str] | tuple[str, ...] | None = None,
    required_adapters: list[str] | tuple[str, ...] | None = None,
    allow_inline_secrets: bool = False,
) -> dict[str, Any]:
    paths = default_v3_paths(root)
    resolver_path = Path(resolver_config_path) if resolver_config_path is not None else paths.provider_resolvers
    adapter_path = Path(adapter_config_path) if adapter_config_path is not None else paths.provider_adapters
    resolver_required = (
        tuple(PRODUCTION_PROVIDER_RESOLVER_PROFILES) if required_resolvers is None else tuple(required_resolvers)
    )
    adapter_required = (
        tuple(PRODUCTION_PROVIDER_ADAPTER_PROFILES) if required_adapters is None else tuple(required_adapters)
    )
    resolver_config_findings = validate_provider_resolver_config(
        resolver_path,
        allow_inline_secrets=allow_inline_secrets,
    )
    adapter_config_findings = validate_provider_adapter_config(
        adapter_path,
        allow_inline_secrets=allow_inline_secrets,
    )
    if required_resolvers is not None:
        resolver_config_findings = _filter_provider_findings(resolver_config_findings, resolver_required)
    if required_adapters is not None:
        adapter_config_findings = _filter_provider_findings(adapter_config_findings, adapter_required)
    resolver_findings, configured_resolvers = _provider_readiness_findings(
        resolver_path,
        surface="provider_resolvers",
        top_key="provider_effect_resolvers",
        required=resolver_required,
        config_findings=resolver_config_findings,
        allow_inline_secrets=allow_inline_secrets,
    )
    adapter_findings, configured_adapters = _provider_readiness_findings(
        adapter_path,
        surface="provider_adapters",
        top_key="provider_effect_adapters",
        required=adapter_required,
        config_findings=adapter_config_findings,
        allow_inline_secrets=allow_inline_secrets,
    )
    findings = {
        "provider_resolvers": resolver_findings,
        "provider_adapters": adapter_findings,
    }
    ready = not any(
        provider_findings for surface_findings in findings.values() for provider_findings in surface_findings.values()
    )
    return {
        "ready": ready,
        "resolver_config": str(resolver_path),
        "adapter_config": str(adapter_path),
        "configured_resolvers": configured_resolvers,
        "configured_adapters": configured_adapters,
        "findings": findings,
    }


def _filter_provider_findings(
    findings: Mapping[str, list[str]], required: list[str] | tuple[str, ...]
) -> dict[str, list[str]]:
    required_set = {str(provider) for provider in required}
    return {
        str(provider): list(items)
        for provider, items in findings.items()
        if str(provider) in required_set or str(provider).startswith("_")
    }


def run_local_provider_dress_rehearsal(
    *,
    root: Path | str | None = None,
    providers: list[str] | tuple[str, ...] | None = None,
    granted_by: str = "v3-provider-dress-rehearsal",
) -> dict[str, Any]:
    selected = tuple(providers or ("social", "market", "health"))
    cases = _local_provider_dress_rehearsal_cases()
    unknown = [provider for provider in selected if provider not in cases]
    if unknown:
        raise KeyError(f"Unknown local provider dress rehearsal provider(s): {', '.join(unknown)}")
    paths = default_v3_paths(root)
    paths.provider_state_manifests.mkdir(parents=True, exist_ok=True)
    approvals = default_approval_store(root)
    effects = default_effect_log(root)
    rehearsals: list[dict[str, Any]] = []
    for provider in selected:
        case = cases[provider]
        target = f"local-{provider}-canary-{new_run_id(provider)}"
        payload = {**case["payload"], "target": target}
        first = run_named_workflow(case["workflow"], payload=payload, root=root)
        if first.record.outcome != "approval_required":
            raise RuntimeError(f"{provider} dress rehearsal did not stop for approval")
        pending = [
            request
            for request in approvals.list_requests(status="pending")
            if request.action == case["approval_action"]
            and request.scope == case["workflow"]
            and request.run_id == first.record.run_id
        ]
        if not pending:
            raise RuntimeError(f"{provider} dress rehearsal has no pending approval request")
        grant = approvals.grant(pending[-1].request_id, granted_by=granted_by)
        second = run_named_workflow(case["workflow"], payload=payload, root=root)
        candidates = [
            effect
            for effect in effects.unresolved()
            if effect.pipeline == case["workflow"]
            and effect.action == case["effect_action"]
            and effect.target == target
        ]
        if not candidates:
            raise RuntimeError(f"{provider} dress rehearsal has no planned provider effect")
        effect = candidates[-1]
        if not effect.approval_token_id or not effect.preview_hash:
            raise RuntimeError(f"{provider} dress rehearsal effect is missing approval metadata")
        manifest_path = paths.provider_state_manifests / f"{provider}_dress_rehearsal.json"
        adapter = LocalProviderStateAdapter(
            provider=provider,
            manifest_path=manifest_path,
            artifact_root=paths.artifacts,
            preview_filename=_preview_filename_for_provider(provider),
        )
        adapter_result = adapter(effect)
        reconciled = reconcile_provider_effects(root=root, provider_state_manifest_paths=[manifest_path])
        latest = effects.get_by_idempotency_key(effect.idempotency_key)
        rehearsals.append(
            {
                "provider": provider,
                "workflow": case["workflow"],
                "target": target,
                "approval_request_id": pending[-1].request_id,
                "approval_token_id": grant.grant_id,
                "planned_effect_id": effect.effect_id,
                "effect_id": latest.effect_id if latest is not None else effect.effect_id,
                "effect_status": latest.status if latest is not None else "",
                "external_ref": latest.external_ref if latest is not None else "",
                "provider_state_manifest": str(manifest_path),
                "adapter_status": str(adapter_result.get("status") or ""),
                "reconciled_effects": [entry.effect_id for entry in reconciled],
                "run_ids": [first.record.run_id, second.record.run_id],
            }
        )
    ready = all(item["effect_status"] == "reconciled_succeeded" for item in rehearsals)
    return {"ready": ready, "providers": list(selected), "rehearsals": rehearsals}


def run_provider_production_canary(
    *,
    root: Path | str | None = None,
    providers: list[str] | tuple[str, ...] | None = None,
    granted_by: str = "v3-provider-production-canary",
    resolver_config_path: Path | str | None = None,
    adapter_config_path: Path | str | None = None,
    provider_http_clients: Mapping[str, httpx.Client] | None = None,
    provider_adapters: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    selected = tuple(providers or ("social", "market", "health"))
    cases = _provider_production_canary_cases()
    unknown = [provider for provider in selected if provider not in cases]
    if unknown:
        raise KeyError(f"Unknown production canary provider(s): {', '.join(unknown)}")
    resolver_required = tuple(provider for provider in selected if provider in PRODUCTION_PROVIDER_RESOLVER_PROFILES)
    readiness = provider_production_readiness_report(
        root=root,
        resolver_config_path=resolver_config_path,
        adapter_config_path=adapter_config_path,
        required_resolvers=resolver_required,
        required_adapters=selected,
    )
    if not readiness["ready"]:
        return {
            "ready": False,
            "providers": list(selected),
            "dry_run": dry_run,
            "canaries": [],
            "readiness": readiness,
        }
    if dry_run:
        return {
            "ready": True,
            "providers": list(selected),
            "dry_run": True,
            "canaries": [
                {
                    "provider": provider,
                    "workflow": str(cases[provider]["workflow"]),
                    "target": f"production-{provider}-canary-<dry-run>",
                    "approval_action": str(cases[provider]["approval_action"]),
                    "effect_action": str(cases[provider]["effect_action"]),
                    "payload_keys": sorted(str(key) for key in cases[provider]["payload"].keys()),
                }
                for provider in selected
            ],
            "readiness": readiness,
        }
    approvals = default_approval_store(root)
    effects = default_effect_log(root)
    canaries: list[dict[str, Any]] = []
    for provider in selected:
        case = cases[provider]
        target = f"production-{provider}-canary-{new_run_id(provider)}"
        payload = {**case["payload"], "target": target}
        first = run_named_workflow(case["workflow"], payload=payload, root=root)
        if first.record.outcome != "approval_required":
            raise RuntimeError(f"{provider} production canary did not stop for approval")
        pending = [
            request
            for request in approvals.list_requests(status="pending")
            if request.action == case["approval_action"]
            and request.scope == case["workflow"]
            and request.run_id == first.record.run_id
        ]
        if not pending:
            raise RuntimeError(f"{provider} production canary has no pending approval request")
        grant = approvals.grant(pending[-1].request_id, granted_by=granted_by)
        second = run_named_workflow(case["workflow"], payload=payload, root=root)
        candidates = [
            effect
            for effect in effects.unresolved()
            if effect.pipeline == case["workflow"]
            and effect.action == case["effect_action"]
            and effect.target == target
        ]
        if not candidates:
            raise RuntimeError(f"{provider} production canary has no planned provider effect")
        planned = candidates[-1]
        executed = run_provider_effect_adapter(
            root=root,
            idempotency_key=planned.idempotency_key,
            provider_config_path=adapter_config_path,
            provider_http_clients=provider_http_clients,
            provider_adapters=provider_adapters,
        )
        reconciled = reconcile_provider_effects(
            root=root,
            provider_config_path=resolver_config_path,
            provider_http_clients=provider_http_clients,
        )
        latest = effects.get_by_idempotency_key(planned.idempotency_key) or executed
        canaries.append(
            {
                "provider": provider,
                "workflow": case["workflow"],
                "target": target,
                "approval_request_id": pending[-1].request_id,
                "approval_token_id": grant.grant_id,
                "planned_effect_id": planned.effect_id,
                "effect_id": latest.effect_id,
                "effect_status": latest.status,
                "external_ref": latest.external_ref,
                "reconciled_effects": [entry.effect_id for entry in reconciled],
                "run_ids": [first.record.run_id, second.record.run_id],
            }
        )
    ready = all(item["effect_status"] in {"succeeded", "reconciled_succeeded"} for item in canaries)
    return {"ready": ready, "providers": list(selected), "dry_run": False, "canaries": canaries, "readiness": readiness}


def provider_production_canary_surface() -> dict[str, dict[str, Any]]:
    """Return the non-mutating production canary coverage surface."""
    return {
        provider: {
            "workflow": str(case["workflow"]),
            "approval_action": str(case["approval_action"]),
            "effect_action": str(case["effect_action"]),
            "requires_resolver": provider in PRODUCTION_PROVIDER_RESOLVER_PROFILES,
            "requires_adapter": provider in PRODUCTION_PROVIDER_ADAPTER_PROFILES,
        }
        for provider, case in _provider_production_canary_cases().items()
    }


def _local_provider_dress_rehearsal_cases() -> dict[str, dict[str, Any]]:
    return {
        "social": {
            "workflow": "social_proactive",
            "approval_action": "post_note_idempotent",
            "effect_action": "post_social",
            "payload": {
                "connectors": {"social": True},
                "platform": "local_provider_state",
                "content": "Mira V3 local provider dress rehearsal social canary",
            },
        },
        "market": {
            "workflow": "market_monitor",
            "approval_action": "send_market_alert_idempotent",
            "effect_action": "send_market_alert",
            "payload": {
                "connectors": {"market_alert": True},
                "message": "Mira V3 local provider dress rehearsal market canary",
                "severity": "review",
                "tetra_report_id": "local-provider-dress-rehearsal",
            },
        },
        "health": {
            "workflow": "health_wellness",
            "approval_action": "write_health_idempotent",
            "effect_action": "write_health",
            "payload": {
                "connectors": {"health_provider": True},
                "operation": "sync_review_record",
                "record": {"source": "local_provider_dress_rehearsal", "status": "canary"},
            },
        },
    }


def _provider_production_canary_cases() -> dict[str, dict[str, Any]]:
    return {
        "substack": {
            "workflow": "article_creation",
            "approval_action": "publish_substack_idempotent",
            "effect_action": "publish_substack",
            "payload": {
                "connectors": {"substack": True, "twitter": False},
                "title": "Mira V3 production Substack canary",
            },
        },
        "rss": {
            "workflow": "podcast_production",
            "approval_action": "publish_rss_idempotent",
            "effect_action": "publish_rss",
            "payload": {
                "connectors": {"tts": False, "rss": True},
                "title": "Mira V3 production RSS canary",
                "episode_id": "mira-v3-production-rss-canary",
                "description": "Mira V3 production RSS provider canary.",
                "audio_url": "https://podcast.example/mira-v3-production-rss-canary.mp3",
                "episode_url": "https://podcast.example/mira-v3-production-rss-canary",
            },
        },
        "tts": {
            "workflow": "podcast_production",
            "approval_action": "synthesize_tts_idempotent",
            "effect_action": "synthesize_tts",
            "payload": {
                "connectors": {"tts": True, "rss": False},
                "title": "Mira V3 production TTS canary",
                "script_text": "Mira V3 production TTS provider canary.",
                "voice": "mira-canary",
                "audio_output_name": "mira-v3-production-tts-canary.wav",
            },
        },
        **_local_provider_dress_rehearsal_cases(),
    }


def _provider_readiness_findings(
    path: Path,
    *,
    surface: str,
    top_key: str,
    required: list[str] | tuple[str, ...],
    config_findings: Mapping[str, list[str]],
    allow_inline_secrets: bool,
) -> tuple[dict[str, list[str]], list[str]]:
    findings: dict[str, list[str]] = {str(key): list(value) for key, value in config_findings.items()}
    if not path.exists():
        findings.setdefault("_config", []).append(f"{surface} config file is missing: {path}")
        return findings, []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        findings.setdefault("_config", []).append(f"invalid_json: {exc}")
        return findings, []
    providers = config.get(top_key) or {}
    if not isinstance(providers, dict):
        findings.setdefault(top_key, []).append("must be an object")
        return findings, []
    configured = sorted(str(name) for name in providers)
    required_set = {str(provider) for provider in required}
    for provider in required:
        if str(provider) not in providers:
            findings.setdefault(str(provider), []).append("required provider is not configured")
    for name, provider_config in providers.items():
        if str(name) not in required_set:
            continue
        if not isinstance(provider_config, dict):
            findings.setdefault(str(name), []).append("provider config must be an object")
            continue
        provider_findings = _provider_provisioning_findings(
            provider_config,
            provider_name=str(name),
            allow_inline_secrets=allow_inline_secrets,
        )
        if provider_findings:
            findings.setdefault(str(name), []).extend(provider_findings)
    findings = {provider: _dedupe_strings(items) for provider, items in findings.items()}
    return findings, configured


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _providers_for_env_template(
    path: Path,
    *,
    top_key: str,
    fallback_profiles: Mapping[str, Mapping[str, Any]],
    required: list[str] | tuple[str, ...],
) -> dict[str, Mapping[str, Any]]:
    providers: dict[str, Mapping[str, Any]] = {}
    required_set = {str(provider) for provider in required}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}
        configured = config.get(top_key) if isinstance(config, dict) else {}
        if isinstance(configured, dict):
            providers.update(
                {
                    str(name): provider_config
                    for name, provider_config in configured.items()
                    if str(name) in required_set and isinstance(provider_config, Mapping)
                }
            )
    for provider in required:
        provider_name = str(provider)
        if provider_name not in providers and provider_name in fallback_profiles:
            providers[provider_name] = fallback_profiles[provider_name]
    return dict(sorted(providers.items()))


def _provider_env_rows(surface: str, providers: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    fields = ("endpoint_template_env", "bearer_token_env", "feed_path_env", "manifest_path_env")
    for provider, provider_config in providers.items():
        for field in fields:
            env_name = str(provider_config.get(field) or "").strip()
            if not env_name or env_name in seen:
                continue
            seen.add(env_name)
            rows.append(
                {
                    "surface": surface,
                    "provider": str(provider),
                    "field": field,
                    "env": env_name,
                    "placeholder": _provider_env_placeholder(env_name, field, str(provider)),
                }
            )
    return rows


def _provider_env_placeholder(env_name: str, field: str, provider: str) -> str:
    if field == "endpoint_template_env":
        return f'"https://provider.example/{provider}/{{target}}"'
    if field == "bearer_token_env":
        return f'"REPLACE_WITH_{env_name}_SECRET"'
    if field == "feed_path_env":
        return f'"{Path.cwd() / "data" / V3_DIRNAME / "provider_state" / (provider + "_feed.xml")}"'
    if field == "manifest_path_env":
        return f'"{Path.cwd() / "data" / V3_DIRNAME / "provider_state" / (provider + ".json")}"'
    return '""'


def _provider_provisioning_findings(
    provider_config: Mapping[str, Any],
    *,
    provider_name: str,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    provider_type = str(provider_config.get("type") or "")
    endpoint_template_env = str(provider_config.get("endpoint_template_env") or "").strip()
    bearer_token_env = str(provider_config.get("bearer_token_env") or "").strip()
    if endpoint_template_env:
        endpoint_template = str(getenv(endpoint_template_env) or "").strip()
        if not endpoint_template:
            findings.append(f"endpoint_template_env {endpoint_template_env} is not set")
        elif _is_placeholder_provider_value(endpoint_template):
            findings.append(f"endpoint_template_env {endpoint_template_env} still contains a placeholder value")
    if bearer_token_env:
        bearer_token = str(getenv(bearer_token_env) or "").strip()
        if not bearer_token:
            findings.append(f"bearer_token_env {bearer_token_env} is not set")
        elif _is_placeholder_provider_value(bearer_token):
            findings.append(f"bearer_token_env {bearer_token_env} still contains a placeholder value")
    if provider_config.get("bearer_token") and not allow_inline_secrets:
        findings.append("inline bearer_token is not allowed for production readiness")
    if provider_type in {"local_tts_command", "local_deployment_command"}:
        command = provider_config.get("command")
        if isinstance(command, list) and command:
            command0 = str(command[0])
            resolved = _resolve_command_executable(command0)
            if resolved is None:
                findings.append(f"local command executable is not available: {command0}")
            elif not resolved.is_file():
                findings.append(f"local command executable is not a file: {resolved}")
            elif not _is_executable_file(resolved):
                findings.append(f"local command executable is not executable: {resolved}")
        role = str(provider_config.get("role") or provider_name)
        if provider_type == "local_deployment_command" and role != provider_name:
            findings.append(f"local deployment command role {role} does not match provider {provider_name}")
    return findings


def _is_placeholder_provider_value(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    return any(
        marker in normalized
        for marker in (
            "provider.example",
            "replace_with",
            "changeme",
            "change_me",
            "placeholder",
            "your_token",
            "your-secret",
        )
    )


def _resolve_command_executable(command0: str) -> Path | None:
    if not command0:
        return None
    if "/" in command0:
        return Path(command0).expanduser()
    resolved = shutil.which(command0)
    return Path(resolved) if resolved else None


def _is_executable_file(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_mode & 0o111 != 0
    except OSError:
        return False


def run_memory_compaction_adapter(
    *,
    root: Path | str | None = None,
    idempotency_key: str | None = None,
    target: str | None = None,
):
    paths = default_v3_paths(root)
    effects = default_effect_log(root)
    effect = _memory_compaction_effect(effects, idempotency_key=idempotency_key, target=target)
    if effect.status in {"succeeded", "reconciled_succeeded"}:
        return effect
    if effect.action != "compact_memory" or effect.pipeline != "memory_maintenance":
        raise ValueError("memory compaction adapter requires a compact_memory memory_maintenance effect")
    if effect.status != "planned":
        raise ValueError(f"memory compaction effect must be planned, got {effect.status}")
    if not effect.approval_token_id or not effect.preview_hash:
        raise ValueError("memory compaction effect requires approval token and preview hash")
    preview = _read_memory_compaction_preview(paths, effect)
    candidates = list(preview.get("candidates") or [])
    if not candidates:
        raise ValueError("memory compaction preview has no candidates")
    effects.mark_executing(effect.idempotency_key, "memory compaction adapter started")
    try:
        kernel = default_kernel_store(root).load()
        archived = _archive_kernel_items(kernel, candidates, run_id=effect.run_id, effect_id=effect.effect_id)
        if not archived:
            raise ValueError("memory compaction candidates did not match active kernel items")
        default_kernel_store(root).save(kernel)
    except Exception:
        effects.mark_unknown(effect.idempotency_key, "memory compaction adapter failed after execution started")
        raise
    return effects.mark_succeeded(
        effect.idempotency_key,
        f"archived {len(archived)} memory item(s)",
        external_ref=f"kernel_archive:{effect.run_id}:{len(archived)}",
    )


def run_self_evolution_production_adapter(
    *,
    root: Path | str | None = None,
    idempotency_key: str | None = None,
    target: str | None = None,
    provider_config_path: Path | str | None = None,
    provider_http_clients: Mapping[str, httpx.Client] | None = None,
    provider_adapters: Mapping[str, Any] | None = None,
):
    paths = default_v3_paths(root)
    effects = default_effect_log(root)
    effect = _self_evolution_production_effect(effects, idempotency_key=idempotency_key, target=target)
    if effect.status in {"succeeded", "reconciled_succeeded"}:
        return effect
    if effect.action != "promote_production" or effect.pipeline != "self_evolution":
        raise ValueError("self-evolution production adapter requires a promote_production self_evolution effect")
    if effect.status != "planned":
        raise ValueError(f"self-evolution production effect must be planned, got {effect.status}")
    if not effect.approval_token_id or not effect.preview_hash:
        raise ValueError("self-evolution production effect requires approval token and preview hash")
    preview = _read_self_evolution_production_preview(paths, effect)
    if preview.get("status") != "staged":
        raise ValueError("self-evolution production preview is not staged")
    repo_path = Path(str(preview.get("repo_path") or ".")).expanduser()
    production_branch = str(preview.get("production_branch") or "")
    canary_branch = str(preview.get("canary_branch") or "")
    remote_promotion_enabled = bool(preview.get("remote_promotion_enabled"))
    remote_name = str(preview.get("remote_name") or "")
    remote_branch = str(preview.get("remote_branch") or production_branch)
    deployment_service_enabled = bool(preview.get("deployment_service_enabled"))
    deployment_health_check_enabled = bool(preview.get("deployment_health_check_enabled"))
    deployment_rollback_enabled = bool(preview.get("deployment_rollback_enabled"))
    _validate_production_promotion_repo(repo_path, production_branch, canary_branch)
    if remote_promotion_enabled:
        _validate_production_promotion_remote(repo_path, remote_name, remote_branch)
    effects.mark_executing(effect.idempotency_key, "self-evolution production promotion adapter started")
    try:
        result = _promote_self_evolution_canary(
            repo_path,
            production_branch=production_branch,
            canary_branch=canary_branch,
            rollback_after_promotion=bool(preview.get("rollback_after_promotion")),
            remote_promotion_enabled=remote_promotion_enabled,
            remote_name=remote_name,
            remote_branch=remote_branch,
        )
        if deployment_service_enabled:
            if result["rollback_executed"]:
                result["deployment"] = {
                    "status": "skipped",
                    "detail": "deployment skipped because promotion was rolled back",
                    "external_ref": "",
                }
            else:
                result["deployment"] = _run_self_evolution_deployment_adapter(
                    effect,
                    result,
                    paths=paths,
                    provider_config_path=provider_config_path,
                    provider_http_clients=provider_http_clients,
                    provider_adapters=provider_adapters,
                )
                if deployment_health_check_enabled and result["deployment"]["status"] == "succeeded":
                    result["deployment_health"] = _run_self_evolution_deployment_health_adapter(
                        effect,
                        result,
                        result["deployment"],
                        paths=paths,
                        provider_config_path=provider_config_path,
                        provider_http_clients=provider_http_clients,
                        provider_adapters=provider_adapters,
                    )
                    if deployment_rollback_enabled and result["deployment_health"]["status"] != "succeeded":
                        result["deployment_rollback"] = _run_self_evolution_deployment_rollback_adapter(
                            effect,
                            result,
                            result["deployment"],
                            result["deployment_health"],
                            paths=paths,
                            provider_config_path=provider_config_path,
                            provider_http_clients=provider_http_clients,
                            provider_adapters=provider_adapters,
                        )
        result_path = (
            paths.artifacts / effect.pipeline / effect.run_id / "self_evolution_production_promotion_result.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        effects.mark_unknown(effect.idempotency_key, "self-evolution production adapter failed after execution started")
        raise
    deployment = result.get("deployment")
    if isinstance(deployment, Mapping) and deployment.get("status") not in {"succeeded", "skipped"}:
        return effects.mark_unknown(
            effect.idempotency_key,
            f"production promotion completed but deployment {deployment.get('status')}: {deployment.get('detail')}",
        )
    deployment_health = result.get("deployment_health")
    if isinstance(deployment_health, Mapping) and deployment_health.get("status") != "succeeded":
        deployment_rollback = result.get("deployment_rollback")
        rollback_detail = ""
        if isinstance(deployment_rollback, Mapping):
            rollback_detail = (
                f"; deployment rollback {deployment_rollback.get('status')}: " f"{deployment_rollback.get('detail')}"
            )
        return effects.mark_unknown(
            effect.idempotency_key,
            (
                "production promotion completed but deployment health "
                f"{deployment_health.get('status')}: {deployment_health.get('detail')}{rollback_detail}"
            ),
        )
    external_ref = (
        f"git_promotion_rolled_back:{production_branch}:{result['rollback_ref']}"
        if result["rollback_executed"]
        else f"git_promotion:{production_branch}:{result['promoted_sha']}:rollback:{result['rollback_ref']}"
    )
    if isinstance(deployment, Mapping) and deployment.get("status") == "succeeded" and deployment.get("external_ref"):
        external_ref = f"{external_ref}:deployment:{deployment['external_ref']}"
    if (
        isinstance(deployment_health, Mapping)
        and deployment_health.get("status") == "succeeded"
        and deployment_health.get("external_ref")
    ):
        external_ref = f"{external_ref}:health:{deployment_health['external_ref']}"
    return effects.mark_succeeded(
        effect.idempotency_key,
        f"production promotion {result['status']}",
        external_ref=external_ref,
    )


def run_provider_effect_adapter(
    *,
    root: Path | str | None = None,
    idempotency_key: str | None = None,
    target: str | None = None,
    action: str | None = None,
    provider_adapters: Mapping[str, Any] | None = None,
    provider_config_path: Path | str | None = None,
    provider_http_clients: Mapping[str, httpx.Client] | None = None,
):
    effects = default_effect_log(root)
    effect = _provider_adapter_effect(effects, idempotency_key=idempotency_key, target=target, action=action)
    if effect.status in {"succeeded", "reconciled_succeeded"}:
        return effect
    if effect.status != "planned":
        raise ValueError(f"provider effect adapter requires a planned effect, got {effect.status}")
    if not effect.approval_token_id or not effect.preview_hash:
        raise ValueError("provider effect adapter requires approval token and preview hash")
    provider = _provider_name_for_live_effect(effect.action)
    if provider is None:
        raise ValueError(f"unsupported provider effect action: {effect.action}")
    paths = default_v3_paths(root)
    config_path = Path(provider_config_path) if provider_config_path is not None else paths.provider_adapters
    configured_adapters = load_provider_adapters_from_config(
        config_path,
        http_clients=provider_http_clients,
        artifact_root=paths.artifacts,
    )
    adapter = {**configured_adapters, **dict(provider_adapters or {})}.get(provider)
    if adapter is None:
        raise ValueError(f"provider adapter is not configured: {provider}")
    effects.mark_executing(effect.idempotency_key, f"{provider} provider adapter started")
    try:
        result = adapter(effect)
    except Exception:
        effects.mark_unknown(effect.idempotency_key, f"{provider} provider adapter failed after execution started")
        raise
    normalized = _normalize_provider_adapter_result(provider, effect.action, result)
    if normalized["status"] == "succeeded":
        return effects.mark_succeeded(
            effect.idempotency_key,
            normalized["detail"],
            external_ref=normalized["external_ref"] or None,
        )
    if normalized["status"] == "failed":
        return effects.mark_failed(effect.idempotency_key, normalized["detail"])
    return effects.mark_unknown(effect.idempotency_key, normalized["detail"])


def _memory_compaction_effect(effects: EffectLog, *, idempotency_key: str | None, target: str | None):
    if idempotency_key:
        effect = effects.get_by_idempotency_key(idempotency_key)
        if effect is None:
            raise KeyError(f"No memory compaction effect: {idempotency_key}")
        return effect
    candidates = [
        effect
        for effect in effects.unresolved()
        if effect.pipeline == "memory_maintenance"
        and effect.action == "compact_memory"
        and (target is None or effect.target == target)
    ]
    if not candidates:
        raise KeyError("No planned memory compaction effect found")
    if len(candidates) > 1:
        raise ValueError("Multiple planned memory compaction effects match; pass idempotency_key")
    return candidates[0]


def _read_memory_compaction_preview(paths: V3Paths, effect) -> dict:
    preview_path = paths.artifacts / effect.pipeline / effect.run_id / "memory_compaction_preview.json"
    if not preview_path.exists():
        raise FileNotFoundError(f"memory compaction preview not found: {preview_path}")
    try:
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid memory compaction preview: {exc}") from exc
    if not isinstance(preview, dict):
        raise ValueError("memory compaction preview must be an object")
    return preview


def _self_evolution_production_effect(effects: EffectLog, *, idempotency_key: str | None, target: str | None):
    if idempotency_key:
        effect = effects.get_by_idempotency_key(idempotency_key)
        if effect is None:
            raise KeyError(f"No self-evolution production effect: {idempotency_key}")
        return effect
    candidates = [
        effect
        for effect in effects.unresolved()
        if effect.pipeline == "self_evolution"
        and effect.action == "promote_production"
        and (target is None or effect.target == target)
    ]
    if not candidates:
        raise KeyError("No planned self-evolution production promotion effect found")
    if len(candidates) > 1:
        raise ValueError("Multiple planned self-evolution production effects match; pass idempotency_key")
    return candidates[0]


def _provider_adapter_effect(
    effects: EffectLog,
    *,
    idempotency_key: str | None,
    target: str | None,
    action: str | None,
):
    if idempotency_key:
        effect = effects.get_by_idempotency_key(idempotency_key)
        if effect is None:
            raise KeyError(f"No provider effect: {idempotency_key}")
        return effect
    candidates = [
        effect
        for effect in effects.unresolved()
        if _provider_name_for_live_effect(effect.action) is not None
        and (target is None or effect.target == target)
        and (action is None or effect.action == action)
    ]
    if not candidates:
        raise KeyError("No planned provider effect found")
    if len(candidates) > 1:
        raise ValueError("Multiple planned provider effects match; pass idempotency_key")
    return candidates[0]


def _read_self_evolution_production_preview(paths: V3Paths, effect) -> dict:
    preview_path = (
        paths.artifacts / effect.pipeline / effect.run_id / "self_evolution_production_promotion_preview.json"
    )
    if not preview_path.exists():
        raise FileNotFoundError(f"self-evolution production preview not found: {preview_path}")
    try:
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid self-evolution production preview: {exc}") from exc
    if not isinstance(preview, dict):
        raise ValueError("self-evolution production preview must be an object")
    return preview


def _validate_production_promotion_repo(repo_path: Path, production_branch: str, canary_branch: str) -> None:
    if not repo_path.exists() or not (repo_path / ".git").exists():
        raise ValueError("production promotion requires a local git repository path")
    for branch_name in (production_branch, canary_branch):
        _validate_safe_git_branch_name(branch_name)
    if _git_output(repo_path, ["status", "--porcelain"]):
        raise ValueError("production promotion requires a clean git worktree")
    _git_output(repo_path, ["rev-parse", "--verify", production_branch])
    _git_output(repo_path, ["rev-parse", "--verify", canary_branch])


def _validate_production_promotion_remote(repo_path: Path, remote_name: str, remote_branch: str) -> None:
    if not remote_name:
        raise ValueError("remote production promotion requires remote_name")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", remote_name) or remote_name.startswith("-"):
        raise ValueError("unsafe remote name")
    _validate_safe_git_branch_name(remote_branch)
    _git_output(repo_path, ["remote", "get-url", remote_name])


def _validate_safe_git_branch_name(branch_name: str) -> None:
    if not branch_name:
        raise ValueError("production promotion requires production_branch and canary_branch")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch_name) or branch_name.startswith(("-", "/")):
        raise ValueError("unsafe branch name")
    if ".." in branch_name or branch_name.endswith(("/", ".")) or "@{" in branch_name:
        raise ValueError("unsafe branch name")


def _promote_self_evolution_canary(
    repo_path: Path,
    *,
    production_branch: str,
    canary_branch: str,
    rollback_after_promotion: bool,
    remote_promotion_enabled: bool = False,
    remote_name: str = "",
    remote_branch: str | None = None,
) -> dict[str, str | bool]:
    original_ref = _git_output(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if original_ref == "HEAD":
        original_ref = _git_output(repo_path, ["rev-parse", "HEAD"])
    rollback_ref = _git_output(repo_path, ["rev-parse", production_branch])
    canary_sha = _git_output(repo_path, ["rev-parse", canary_branch])
    remote_branch = remote_branch or production_branch
    remote_ref_before = ""
    remote_ref_after = ""
    remote_rollback_ref_after = ""
    remote_pushed = False
    remote_rollback_pushed = False
    if remote_promotion_enabled:
        remote_ref_before = _remote_branch_sha(repo_path, remote_name, remote_branch)
    _git_output(repo_path, ["checkout", production_branch])
    _git_output(repo_path, ["merge", "--ff-only", canary_branch])
    promoted_sha = _git_output(repo_path, ["rev-parse", production_branch])
    if remote_promotion_enabled:
        _git_output(repo_path, ["push", remote_name, f"{production_branch}:refs/heads/{remote_branch}"])
        remote_pushed = True
        remote_ref_after = _remote_branch_sha(repo_path, remote_name, remote_branch)
    rollback_executed = False
    status = "promoted"
    if rollback_after_promotion:
        _git_output(repo_path, ["checkout", "--detach", rollback_ref])
        _git_output(repo_path, ["branch", "-f", production_branch, rollback_ref])
        rollback_executed = True
        status = "rolled_back"
        if remote_promotion_enabled:
            _git_output(
                repo_path,
                [
                    "push",
                    f"--force-with-lease=refs/heads/{remote_branch}:{promoted_sha}",
                    remote_name,
                    f"{production_branch}:refs/heads/{remote_branch}",
                ],
            )
            remote_rollback_pushed = True
            remote_rollback_ref_after = _remote_branch_sha(repo_path, remote_name, remote_branch)
    _git_output(repo_path, ["checkout", original_ref])
    return {
        "status": status,
        "repo_path": str(repo_path),
        "production_branch": production_branch,
        "canary_branch": canary_branch,
        "rollback_ref": rollback_ref,
        "canary_sha": canary_sha,
        "promoted_sha": promoted_sha,
        "rollback_executed": rollback_executed,
        "original_ref": original_ref,
        "remote_promotion_enabled": remote_promotion_enabled,
        "remote_name": remote_name,
        "remote_branch": remote_branch,
        "remote_ref_before": remote_ref_before,
        "remote_ref_after": remote_ref_after,
        "remote_rollback_ref_after": remote_rollback_ref_after,
        "remote_pushed": remote_pushed,
        "remote_rollback_pushed": remote_rollback_pushed,
    }


def _remote_branch_sha(repo_path: Path, remote_name: str, remote_branch: str) -> str:
    output = _git_output(repo_path, ["ls-remote", "--heads", remote_name, remote_branch])
    if not output:
        return ""
    return output.split()[0]


def _run_self_evolution_deployment_adapter(
    effect,
    promotion_result: Mapping[str, Any],
    *,
    paths: V3Paths,
    provider_config_path: Path | str | None,
    provider_http_clients: Mapping[str, httpx.Client] | None,
    provider_adapters: Mapping[str, Any] | None,
) -> dict[str, str]:
    config_path = Path(provider_config_path) if provider_config_path is not None else paths.provider_adapters
    configured_adapters = load_provider_adapters_from_config(
        config_path,
        http_clients=provider_http_clients,
        artifact_root=paths.artifacts,
    )
    adapter = {**configured_adapters, **dict(provider_adapters or {})}.get("deployment")
    if adapter is None:
        raise ValueError("deployment adapter is not configured")
    deployment_entry = replace(
        effect,
        target=f"{promotion_result['production_branch']}:{promotion_result['promoted_sha']}",
        external_ref=f"git_promotion:{promotion_result['production_branch']}:{promotion_result['promoted_sha']}",
    )
    return _normalize_provider_adapter_result("deployment", "promote_production", adapter(deployment_entry))


def _run_self_evolution_deployment_health_adapter(
    effect,
    promotion_result: Mapping[str, Any],
    deployment_result: Mapping[str, Any],
    *,
    paths: V3Paths,
    provider_config_path: Path | str | None,
    provider_http_clients: Mapping[str, httpx.Client] | None,
    provider_adapters: Mapping[str, Any] | None,
) -> dict[str, str]:
    config_path = Path(provider_config_path) if provider_config_path is not None else paths.provider_adapters
    configured_adapters = load_provider_adapters_from_config(
        config_path,
        http_clients=provider_http_clients,
        artifact_root=paths.artifacts,
    )
    adapter = {**configured_adapters, **dict(provider_adapters or {})}.get("deployment_health")
    if adapter is None:
        raise ValueError("deployment health adapter is not configured")
    health_entry = replace(
        effect,
        target=str(deployment_result.get("external_ref") or promotion_result["promoted_sha"]),
        external_ref=str(deployment_result.get("external_ref") or ""),
    )
    return _normalize_provider_adapter_result("deployment_health", "verify_deployment_health", adapter(health_entry))


def _run_self_evolution_deployment_rollback_adapter(
    effect,
    promotion_result: Mapping[str, Any],
    deployment_result: Mapping[str, Any],
    health_result: Mapping[str, Any],
    *,
    paths: V3Paths,
    provider_config_path: Path | str | None,
    provider_http_clients: Mapping[str, httpx.Client] | None,
    provider_adapters: Mapping[str, Any] | None,
) -> dict[str, str]:
    config_path = Path(provider_config_path) if provider_config_path is not None else paths.provider_adapters
    configured_adapters = load_provider_adapters_from_config(
        config_path,
        http_clients=provider_http_clients,
        artifact_root=paths.artifacts,
    )
    adapter = {**configured_adapters, **dict(provider_adapters or {})}.get("deployment_rollback")
    if adapter is None:
        raise ValueError("deployment rollback adapter is not configured")
    rollback_entry = replace(
        effect,
        target=str(deployment_result.get("external_ref") or promotion_result["promoted_sha"]),
        external_ref=str(deployment_result.get("external_ref") or ""),
        detail=(
            "rollback_ref="
            f"{promotion_result.get('rollback_ref', '')}; health_status={health_result.get('status', '')}; "
            f"health_detail={health_result.get('detail', '')}"
        ),
    )
    return _normalize_provider_adapter_result("deployment_rollback", "rollback_deployment", adapter(rollback_entry))


def _provider_name_for_live_effect(action: str) -> str | None:
    return {
        "publish_substack": "substack",
        "publish_rss": "rss",
        "synthesize_tts": "tts",
        "post_social": "social",
        "send_market_alert": "market",
        "write_health": "health",
    }.get(action)


def _normalize_provider_adapter_result(provider: str, action: str, result: Any) -> dict[str, str]:
    if result is True:
        return {
            "status": "succeeded",
            "detail": f"{provider} provider adapter completed {action}",
            "external_ref": "",
        }
    if result is False or result is None:
        return {
            "status": "unknown",
            "detail": f"{provider} provider adapter returned no confirmed result for {action}",
            "external_ref": "",
        }
    if hasattr(result, "succeeded"):
        return {
            "status": "succeeded" if bool(result.succeeded) else "failed",
            "detail": str(getattr(result, "detail", "") or f"{provider} provider adapter completed {action}"),
            "external_ref": str(getattr(result, "external_ref", "") or ""),
        }
    if not isinstance(result, Mapping):
        return {
            "status": "unknown",
            "detail": f"{provider} provider adapter returned unsupported result for {action}",
            "external_ref": "",
        }
    status = str(result.get("status") or "").lower()
    external_ref = str(
        result.get("external_ref") or result.get("url") or result.get("provider_url") or result.get("provider_id") or ""
    )
    detail = str(result.get("detail") or result.get("message") or f"{provider} provider adapter completed {action}")
    if status in {
        "published",
        "posted",
        "sent",
        "synced",
        "synthesized",
        "deployed",
        "rolled_back",
        "rollback_succeeded",
        "healthy",
        "succeeded",
        "success",
        "ok",
    }:
        return {"status": "succeeded", "detail": detail, "external_ref": external_ref}
    if status in {"failed", "error", "rejected", "denied", "unhealthy"}:
        return {"status": "failed", "detail": detail, "external_ref": external_ref}
    return {"status": "unknown", "detail": detail, "external_ref": external_ref}


def _archive_kernel_items(kernel, candidates: list[str], *, run_id: str, effect_id: str) -> list[ArchivedMemory]:
    archived: list[ArchivedMemory] = []
    archived_ids = {item.item_id for item in kernel.archived_memories}
    for item_id in candidates:
        item_id = str(item_id)
        if item_id in archived_ids:
            continue
        archived_item = _archive_kernel_item(kernel, item_id, run_id=run_id, effect_id=effect_id)
        if archived_item is None:
            continue
        kernel.archived_memories.append(archived_item)
        archived.append(archived_item)
        archived_ids.add(item_id)
    return archived


def _archive_kernel_item(kernel, item_id: str, *, run_id: str, effect_id: str) -> ArchivedMemory | None:
    if item_id.startswith("scar:"):
        for index, scar in enumerate(kernel.scars):
            if scar.scar_id == item_id:
                kernel.scars.pop(index)
                return ArchivedMemory(
                    item_id=item_id,
                    source="scar",
                    summary=scar.behavioral_change,
                    run_id=run_id,
                    effect_id=effect_id,
                )
    if item_id.startswith("skill:"):
        skill_name = item_id.removeprefix("skill:")
        for index, trace in enumerate(kernel.skill_traces):
            if trace.skill_name == skill_name:
                kernel.skill_traces.pop(index)
                return ArchivedMemory(
                    item_id=item_id,
                    source="skill_trace",
                    summary=f"{trace.skill_name}: {trace.last_outcome}",
                    run_id=run_id,
                    effect_id=effect_id,
                )
    if item_id.startswith("failure:"):
        pattern = item_id.removeprefix("failure:")
        for index, signature in enumerate(kernel.failure_signatures):
            if signature.pattern == pattern:
                kernel.failure_signatures.pop(index)
                return ArchivedMemory(
                    item_id=item_id,
                    source="failure_signature",
                    summary=signature.detection_rule,
                    run_id=run_id,
                    effect_id=effect_id,
                )
    if item_id.startswith("relationship:"):
        try:
            relative_index = int(item_id.removeprefix("relationship:"))
        except ValueError:
            return None
        start = max(len(kernel.relationship_model.notes) - 5, 0)
        absolute_index = start + relative_index
        if 0 <= absolute_index < len(kernel.relationship_model.notes):
            note = kernel.relationship_model.notes.pop(absolute_index)
            return ArchivedMemory(
                item_id=item_id,
                source="relationship_note",
                summary=note,
                run_id=run_id,
                effect_id=effect_id,
            )
    return None


def _provider_config_findings(provider_config: Mapping[str, Any], *, allow_inline_secrets: bool) -> list[str]:
    findings: list[str] = []
    provider_type = provider_config.get("type")
    if provider_type != "http_json":
        findings.append("unsupported provider resolver type")
        return findings
    endpoint_template_env = provider_config.get("endpoint_template_env")
    if endpoint_template_env is not None and not str(endpoint_template_env).strip():
        findings.append("endpoint_template_env must be non-empty")
    endpoint_template = _resolved_provider_endpoint_template(provider_config)
    if not endpoint_template:
        if endpoint_template_env:
            findings.append(f"endpoint_template_env {endpoint_template_env} is not set")
        else:
            findings.append("endpoint_template is required")
    elif not endpoint_template.startswith("https://"):
        findings.append("endpoint_template must use https")
    elif not any(
        field in endpoint_template for field in ("{target}", "{external_ref}", "{idempotency_key}", "{effect_id}")
    ):
        findings.append("endpoint_template must include an effect identity field")
    if provider_config.get("bearer_token") and not allow_inline_secrets:
        findings.append("inline bearer_token is not allowed; use bearer_token_env")
    if provider_config.get("bearer_token_env") is not None and not str(provider_config.get("bearer_token_env")).strip():
        findings.append("bearer_token_env must be non-empty")
    payload_path = provider_config.get("payload_path")
    if payload_path is not None and not isinstance(payload_path, (str, list, tuple)):
        findings.append("payload_path must be a string or list")
    preview_filename = provider_config.get("preview_filename")
    if preview_filename is not None and not str(preview_filename).endswith(".json"):
        findings.append("preview_filename must be a JSON filename")
    return findings


def _provider_adapter_config_findings(provider_config: Mapping[str, Any], *, allow_inline_secrets: bool) -> list[str]:
    findings = _provider_config_findings(provider_config, allow_inline_secrets=allow_inline_secrets)
    method = str(provider_config.get("method") or "POST").upper()
    if method not in {"POST", "PUT", "PATCH"}:
        findings.append("provider adapter method must be POST, PUT, or PATCH")
    return findings


def _local_rss_adapter_config_findings(provider_config: Mapping[str, Any]) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "local_rss_feed":
        findings.append("unsupported provider adapter type")
        return findings
    feed_path = _resolved_config_path(provider_config, "feed_path", "feed_path_env")
    if not feed_path:
        if provider_config.get("feed_path_env"):
            findings.append(f"feed_path_env {provider_config.get('feed_path_env')} is not set")
        else:
            findings.append("feed_path or feed_path_env is required")
    if provider_config.get("feed_path") and str(provider_config.get("feed_path")).strip().startswith(
        ("http://", "https://")
    ):
        findings.append("local RSS feed_path must be a filesystem path")
    return findings


def _hosted_rss_http_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "hosted_rss_http":
        findings.append("unsupported provider adapter type")
        return findings
    http_config = {**dict(provider_config), "type": "http_json"}
    findings.extend(_provider_adapter_config_findings(http_config, allow_inline_secrets=allow_inline_secrets))
    method = str(provider_config.get("method") or "POST").upper()
    if method != "POST":
        findings.append("hosted RSS adapter method must be POST")
    return findings


def _hosted_social_http_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "hosted_social_http":
        findings.append("unsupported provider adapter type")
        return findings
    http_config = {**dict(provider_config), "type": "http_json"}
    findings.extend(_provider_adapter_config_findings(http_config, allow_inline_secrets=allow_inline_secrets))
    method = str(provider_config.get("method") or "POST").upper()
    if method != "POST":
        findings.append("hosted social adapter method must be POST")
    return findings


def _hosted_market_http_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "hosted_market_http":
        findings.append("unsupported provider adapter type")
        return findings
    http_config = {**dict(provider_config), "type": "http_json"}
    findings.extend(_provider_adapter_config_findings(http_config, allow_inline_secrets=allow_inline_secrets))
    method = str(provider_config.get("method") or "POST").upper()
    if method != "POST":
        findings.append("hosted market adapter method must be POST")
    return findings


def _hosted_health_http_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "hosted_health_http":
        findings.append("unsupported provider adapter type")
        return findings
    http_config = {**dict(provider_config), "type": "http_json"}
    findings.extend(_provider_adapter_config_findings(http_config, allow_inline_secrets=allow_inline_secrets))
    method = str(provider_config.get("method") or "POST").upper()
    if method != "POST":
        findings.append("hosted health adapter method must be POST")
    return findings


def _local_provider_state_adapter_config_findings(provider_config: Mapping[str, Any]) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "local_provider_state":
        findings.append("unsupported provider adapter type")
        return findings
    provider = str(provider_config.get("provider") or "").strip()
    if provider and provider not in {"substack", "social", "market", "health"}:
        findings.append("local provider-state provider must be substack, social, market, or health")
    manifest_path = _resolved_config_path(provider_config, "manifest_path", "manifest_path_env")
    if not manifest_path:
        if provider_config.get("manifest_path_env"):
            findings.append(f"manifest_path_env {provider_config.get('manifest_path_env')} is not set")
        else:
            findings.append("manifest_path or manifest_path_env is required")
    if provider_config.get("manifest_path") and str(provider_config.get("manifest_path")).strip().startswith(
        ("http://", "https://")
    ):
        findings.append("local provider-state manifest_path must be a filesystem path")
    preview_filename = provider_config.get("preview_filename")
    if preview_filename is not None and not str(preview_filename).endswith(".json"):
        findings.append("preview_filename must be a JSON filename")
    return findings


def _local_tts_command_adapter_config_findings(provider_config: Mapping[str, Any]) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "local_tts_command":
        findings.append("unsupported provider adapter type")
        return findings
    command = provider_config.get("command")
    if not isinstance(command, list) or not command:
        findings.append("local TTS command must be a non-empty list")
    elif not all(isinstance(part, str) and part.strip() for part in command):
        findings.append("local TTS command entries must be non-empty strings")
    return findings


def _local_deployment_command_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    provider_name: str,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "local_deployment_command":
        findings.append("unsupported provider adapter type")
        return findings
    role = str(provider_config.get("role") or provider_name).strip()
    if role not in {"deployment", "deployment_health", "deployment_rollback"}:
        findings.append("local deployment command role must be deployment, deployment_health, or deployment_rollback")
    command = provider_config.get("command")
    if not isinstance(command, list) or not command:
        findings.append("local deployment command must be a non-empty list")
    elif not all(isinstance(part, str) and part.strip() for part in command):
        findings.append("local deployment command entries must be non-empty strings")
    try:
        timeout_s = float(provider_config.get("timeout_s", 120.0))
    except (TypeError, ValueError):
        findings.append("local deployment command timeout_s must be a positive number")
    else:
        if timeout_s <= 0:
            findings.append("local deployment command timeout_s must be a positive number")
    return findings


def _hosted_tts_http_adapter_config_findings(
    provider_config: Mapping[str, Any],
    *,
    allow_inline_secrets: bool,
) -> list[str]:
    findings: list[str] = []
    if provider_config.get("type") != "hosted_tts_http":
        findings.append("unsupported provider adapter type")
        return findings
    http_config = {**dict(provider_config), "type": "http_json"}
    findings.extend(_provider_adapter_config_findings(http_config, allow_inline_secrets=allow_inline_secrets))
    method = str(provider_config.get("method") or "POST").upper()
    if method != "POST":
        findings.append("hosted TTS adapter method must be POST")
    for field_name in ("audio_base64_field", "audio_url_field"):
        value = provider_config.get(field_name)
        if value is not None and not str(value).strip():
            findings.append(f"{field_name} must be non-empty")
    return findings


def _resolved_provider_endpoint_template(provider_config: Mapping[str, Any]) -> str:
    endpoint_template = str(provider_config.get("endpoint_template") or "").strip()
    if endpoint_template:
        return endpoint_template
    endpoint_template_env = str(provider_config.get("endpoint_template_env") or "").strip()
    if not endpoint_template_env:
        return ""
    return str(getenv(endpoint_template_env) or "").strip()


def _resolved_config_path(config: Mapping[str, Any], key: str, env_key: str) -> str:
    value = str(config.get(key) or "").strip()
    if value:
        return value
    env_name = str(config.get(env_key) or "").strip()
    if not env_name:
        return ""
    return str(getenv(env_name) or "").strip()


def _payload_at_path(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _read_provider_preview_artifact(artifact_root: Path, entry, *, filename: str) -> dict:
    preview_path = artifact_root / entry.pipeline / entry.run_id / filename
    if not preview_path.exists():
        raise FileNotFoundError(f"provider preview not found: {preview_path}")
    try:
        preview = json.loads(preview_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid provider preview: {exc}") from exc
    if not isinstance(preview, dict):
        raise ValueError("provider preview must be an object")
    return preview


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _preview_filename_for_provider(provider: str) -> str:
    return {
        "rss": "rss_publish_preview.json",
        "social": "social_publish_preview.json",
        "market": "market_alert_preview.json",
        "health": "health_write_preview.json",
    }.get(provider, "")


def _local_provider_state_collection(provider: str) -> str:
    return {
        "substack": "effects",
        "social": "social_posts",
        "market": "market_alerts",
        "health": "health_writes",
    }.get(provider, "effects")


def _local_provider_state_payload(provider: str, entry, preview: Mapping[str, Any]) -> dict[str, Any]:
    target = str(preview.get("target") or entry.target)
    provider_id = str(preview.get("provider_id") or target)
    external_ref = str(preview.get("external_ref") or f"local_provider_state:{provider}:{provider_id}")
    statuses = {
        "substack": "published",
        "social": "posted",
        "market": "sent",
        "health": "synced",
    }
    payload = {
        "provider_id": provider_id,
        "effect_id": entry.effect_id,
        "idempotency_key": entry.idempotency_key,
        "target": target,
        "status": statuses.get(provider, "published"),
        "external_ref": external_ref,
        "preview_hash": entry.preview_hash,
        "approval_token_id": entry.approval_token_id,
        "preview": dict(preview),
    }
    if provider == "social":
        payload["url"] = str(preview.get("url") or external_ref)
    return payload


def _load_or_create_rss_feed(
    feed_path: Path,
    *,
    title: str,
    link: str,
    description: str,
) -> tuple[ET.ElementTree, ET.Element]:
    if feed_path.exists():
        try:
            tree = ET.parse(feed_path)
        except ET.ParseError as exc:
            raise ValueError(f"invalid RSS feed: {feed_path}") from exc
        root = tree.getroot()
        channel = root.find("channel")
        if root.tag != "rss" or channel is None:
            raise ValueError(f"RSS feed must have rss/channel root: {feed_path}")
        return tree, channel
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    _set_child_text(channel, "title", title)
    _set_child_text(channel, "link", link)
    _set_child_text(channel, "description", description)
    return ET.ElementTree(root), channel


def _rss_item_for_guid(channel: ET.Element, guid: str) -> ET.Element | None:
    for item in channel.findall("item"):
        if (item.findtext("guid") or "") == guid:
            return item
    return None


def _set_child_text(parent: ET.Element, tag: str, text: str) -> ET.Element:
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
    child.text = text
    return child


def _git_output(repo_path: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        text=True,
        capture_output=True,
        timeout=15,
    )
    return completed.stdout.strip()


def pipeline_for_background_job(bg_name: str) -> str:
    return route_background_job(bg_name).workflow


def pipeline_for_task(tags: list[str] | None) -> str:
    return route_task(tags).workflow


def workflow_router() -> WorkflowRouter:
    return WorkflowRouter(
        task_tags=TASK_TAG_PIPELINE_MAP,
        background_jobs=JOB_PIPELINE_MAP,
    )


def route_background_job(bg_name: str, connectors: dict[str, bool] | None = None):
    return workflow_router().route_background_job(bg_name, RouterContext(connectors or {}))


def route_task(tags: list[str] | None, connectors: dict[str, bool] | None = None):
    return workflow_router().route_task(tags, RouterContext(connectors or {}))


def route_named_workflow(name: str, connectors: dict[str, bool] | None = None):
    return workflow_router().route_named_workflow(name, RouterContext(connectors or {}))


def record_experience(
    *,
    pipeline: str,
    trigger: str,
    intent: str,
    outcome: str,
    what_happened: str,
    what_mattered: str,
    what_changed: str,
    what_failed: str | None,
    actions: list[MemoryAction] | None = None,
    causal_links: list[str] | None = None,
    confidence: float = 0.8,
    root: Path | str | None = None,
) -> ExperienceRecord:
    memory_class = PIPELINE_MEMORY_CLASS.get(pipeline, "operational")
    run_id = new_run_id(pipeline)
    delta = MemoryDelta(
        pipeline=pipeline,
        run_id=run_id,
        memory_class=memory_class,
        what_happened=what_happened,
        what_mattered=what_mattered,
        what_changed=what_changed,
        what_failed=what_failed,
        actions=list(actions or []),
    )
    store = default_kernel_store(root)
    kernel = store.load()
    existing_memory = [*kernel.relationship_model.notes, *(scar.behavioral_change for scar in kernel.scars)]
    commit = SecurityGateway(
        existing_memory=existing_memory,
        quarantine_store=default_quarantine_store(root),
    ).validate(delta)
    MemoryConsolidator().apply_commit(kernel, delta, commit)
    default_commit_log(root).append(commit)
    record = ExperienceRecord(
        id=run_id,
        pipeline=pipeline,
        trigger=trigger,
        intent=intent,
        outcome=outcome,
        delta=delta,
        causal_links=list(causal_links or []),
        confidence=confidence,
        memory_class=memory_class,
        memory_commit_id=commit.commit_id,
    )
    default_ledger(root).append(record)
    store.save(kernel)
    return record


def record_task_completion(
    *,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None,
    root: Path | str | None = None,
) -> ExperienceRecord:
    pipeline = pipeline_for_task(tags)
    failure_mode = _task_failure_mode(status, summary)
    gate_outcome = _task_gate_outcome(status, failure_mode)
    failed = status not in {"done", "verified", "completed", "completed_unverified"} and gate_outcome is None
    actions: list[MemoryAction] = [
        MemoryAction(
            "update_skill_trace",
            f"skill:{pipeline}",
            f"task={task_id} status={status}",
        )
    ]
    if failed:
        actions.append(MemoryAction("create_scar", f"scar:{pipeline}:{task_id}", summary[:500] or status))
        actions.append(
            MemoryAction(
                "update_failure_signature",
                f"failure:{pipeline}:{failure_mode}",
                f"{pipeline} task failures matching mode={failure_mode}",
                metadata={"failure_rate": "1.0"},
            )
        )
    return record_experience(
        pipeline=pipeline,
        trigger="task_result",
        intent=f"complete task {task_id}",
        outcome=gate_outcome or status,
        what_happened=f"Task {task_id} finished with status {status}",
        what_mattered=(summary or "Task completed without a summary")[:1000],
        what_changed=f"Future {pipeline} snapshots include task outcome {task_id}",
        what_failed=summary[:1000] if failed else None,
        actions=actions,
        confidence=0.75 if not failed else 0.45,
        root=root,
    )


def _task_failure_mode(status: str, summary: str) -> str:
    text = f"{status} {summary}".lower()
    patterns = [
        ("approval_prompt", r"approval (required|prompt|request)|confirm .*(publish|post|send|upload)"),
        ("preflight_blocked", r"blocked_preflight|preflight blocked|missing capabilities"),
        ("preflight_failed", r"preflight failed|failed preflight"),
        ("missing_reasoning_field", r"missing required reasoning field|required reasoning"),
        ("no_verifiable_output", r"no verifiable output|verifiable output"),
        ("missing_source_material", r"missing source material|source material missing"),
        ("fallback_failed", r"fallback failed|fallback .* failed"),
        ("handler_load_failed", r"handler load failed|failed to load handler|handler .* import"),
        ("provider_unavailable", r"provider unavailable|503|timeout|timed out|connection refused"),
        ("effect_reconciliation_required", r"effect reconciliation required|unreconciled effect"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, text):
            return label
    tokens = re.findall(r"[a-z0-9]+", text)
    return "_".join(tokens[:4])[:80] if tokens else "unknown_failure"


def _task_gate_outcome(status: str, failure_mode: str) -> str | None:
    if failure_mode == "approval_prompt":
        return "approval_required"
    if failure_mode == "preflight_blocked":
        return "blocked_preflight"
    return None


def record_background_completion(
    bg_name: str,
    *,
    root: Path | str | None = None,
) -> ExperienceRecord | None:
    normalized_bg_name = bg_name.strip()
    if any(normalized_bg_name == name or normalized_bg_name.startswith(name + "-") for name in NOOP_COMPLETION_JOBS):
        return None
    pipeline = pipeline_for_background_job(bg_name)
    return record_experience(
        pipeline=pipeline,
        trigger="background_job",
        intent=f"run scheduled job {bg_name}",
        outcome="completed",
        what_happened=f"Background job {bg_name} completed",
        what_mattered=f"{bg_name} is now part of the V3 experience ledger",
        what_changed=f"Future {pipeline} snapshots include scheduled-job outcome {bg_name}",
        what_failed=None,
        actions=[
            MemoryAction(
                "update_skill_trace",
                f"skill:{pipeline}",
                f"background job completed: {bg_name}",
            )
        ],
        confidence=0.7,
        root=root,
    )


def prepare_background_context(
    bg_name: str,
    *,
    root: Path | str | None = None,
) -> dict[str, str]:
    """Write a memory snapshot for a legacy background job and return env vars."""

    route = route_background_job(bg_name)
    pipeline = route.workflow
    paths = default_v3_paths(root)
    kernel = default_kernel_store(root).load()
    snapshot = SnapshotBuilder(default_ledger(root)).build(
        kernel=kernel,
        pipeline=pipeline,
        memory_class=PIPELINE_MEMORY_CLASS.get(pipeline, "operational"),
        involved_skills=[pipeline],
        intent=f"run scheduled job {bg_name}",
        run_id=f"background:{bg_name}",
    )
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", bg_name)[:120] or "background"
    target = paths.snapshots / f"{safe_name}.json"
    target.write_text(json.dumps(to_jsonable(snapshot), indent=2, sort_keys=True), encoding="utf-8")
    return {
        "MIRA_V3_PIPELINE": pipeline,
        "MIRA_V3_ROUTE_DECISION": json.dumps(to_jsonable(route), sort_keys=True),
        "MIRA_V3_MEMORY_SNAPSHOT": str(target),
        "MIRA_V3_LEDGER": str(paths.ledger),
        "MIRA_V3_KERNEL": str(paths.kernel),
    }


WORKFLOW_PACK_PATHS: dict[str, str] = {
    "system_health": "workflow_packs/operational/commands/system_health.yaml",
    "intelligence_briefing": "workflow_packs/epistemic/commands/intelligence_briefing.yaml",
    "article_creation": "workflow_packs/creative/commands/article_creation.yaml",
    "podcast_production": "workflow_packs/creative/commands/podcast_production.yaml",
    "book_reading_notes": "workflow_packs/creative/commands/book_reading_notes.yaml",
    "social_reactive": "workflow_packs/social/commands/social_reactive.yaml",
    "social_proactive": "workflow_packs/social/commands/social_proactive.yaml",
    "weekly_growth_report": "workflow_packs/social/commands/weekly_growth_report.yaml",
    "a2a_trust_experiment": "workflow_packs/epistemic/commands/a2a_trust_experiment.yaml",
    "research_deep_dive": "workflow_packs/epistemic/commands/research_deep_dive.yaml",
    "daily_thought_discussion": "workflow_packs/epistemic/commands/daily_thought_discussion.yaml",
    "daily_journal": "workflow_packs/epistemic/commands/daily_journal.yaml",
    "weekly_reflection": "workflow_packs/epistemic/commands/weekly_reflection.yaml",
    "market_monitor": "workflow_packs/operational/commands/market_monitor.yaml",
    "incident_response": "workflow_packs/operational/commands/incident_response.yaml",
    "health_wellness": "workflow_packs/bodily/commands/health_wellness.yaml",
    "self_evolution": "workflow_packs/self_modification/commands/self_evolution.yaml",
    "skill_learning": "workflow_packs/self_modification/commands/skill_learning.yaml",
    "memory_maintenance": "workflow_packs/self_modification/commands/memory_maintenance.yaml",
    "deterministic_reference": "workflow_packs/operational/commands/deterministic_reference.yaml",
}


def run_workflow_pack(
    pack_path: Path | str,
    *,
    payload: dict | None = None,
    intent: str = "",
    trigger: str = "manual",
    root: Path | str | None = None,
):
    paths = default_v3_paths(root)
    payload = dict(payload or {})
    payload.setdefault("artifact_dir", str(paths.artifacts))
    executor = PipelineExecutor(
        default_kernel_store(root),
        default_ledger(root),
        commit_log=default_commit_log(root),
        causal_evidence_log=default_causal_evidence_log(root),
        effect_log=default_effect_log(root),
        approval_store=default_approval_store(root),
        checkpoint_store=default_checkpoint_store(root),
        artifact_root=paths.artifacts,
    )
    pipeline = compile_workflow_pack(pack_path, audit_artifact_dir=paths.workflow_audits)
    return executor.run(pipeline, payload, intent=intent or f"run {pack_path}", trigger=trigger)


def run_named_workflow(
    name: str,
    *,
    payload: dict | None = None,
    intent: str = "",
    trigger: str = "manual",
    root: Path | str | None = None,
):
    if name not in WORKFLOW_PACK_PATHS:
        raise KeyError(f"No V3.1 workflow pack registered for {name}")
    base = Path(__file__).resolve().parents[2]
    return run_workflow_pack(
        base / WORKFLOW_PACK_PATHS[name],
        payload=payload,
        intent=intent or f"run {name}",
        trigger=trigger,
        root=root,
    )


def capture_v31_baselines(
    root: Path | str | None = None,
    *,
    capture_date=None,
    window_days: int = 7,
):
    paths = default_v3_paths(root)
    return capture_all_baselines(
        ledger=default_ledger(root),
        commit_log=default_commit_log(root),
        effect_log=default_effect_log(root),
        approval_store=default_approval_store(root),
        output_dir=paths.baselines,
        causal_evidence=default_causal_evidence_log(root).list(),
        capture_date=capture_date,
        window_days=window_days,
    )


def run_communication(message: str, *, root: Path | str | None = None) -> str:
    executor = PipelineExecutor(
        default_kernel_store(root),
        default_ledger(root),
        commit_log=default_commit_log(root),
        causal_evidence_log=default_causal_evidence_log(root),
        effect_log=default_effect_log(root),
        approval_store=default_approval_store(root),
        checkpoint_store=default_checkpoint_store(root),
    )
    result = executor.run(
        build_communication_pipeline(),
        {"message": message},
        intent="answer WA communication request",
        trigger="manual",
    )
    return result.outputs["execute"]["reply"]
