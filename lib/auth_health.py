from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


AUTH_FAILURE_MARKERS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "401",
    "403",
    "auth",
    "oauth",
    "expired",
    "unauthorized",
    "forbidden",
)


@dataclass(frozen=True)
class AuthHealthResult:
    provider: str
    status: str
    severity: str
    detail: str = ""
    expires_at: str | None = None
    days_remaining: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "severity": self.severity,
            "detail": self.detail,
            "expires_at": self.expires_at,
            "days_remaining": self.days_remaining,
            "checked_at": _utc_iso(),
        }


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _auth_state_dir() -> Path:
    from config import DATA_DIR

    path = DATA_DIR / "auth_state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_auth_or_quota_failure(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


def record_auth_event(provider: str, event: str, *, status: str = "warning", detail: str = "", payload=None) -> None:
    state_dir = _auth_state_dir()
    payload = payload or {}
    record = {
        "provider": provider,
        "event": event,
        "status": status,
        "detail": detail,
        "payload": payload,
        "ts": _utc_iso(),
    }
    with (state_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _write_provider_state(provider, status=status, detail=detail, event=event)
    _surface_auth_alert(provider, status=status, detail=detail, event=event)


def _write_provider_state(provider: str, *, status: str, detail: str, event: str = "health_check", extra=None) -> None:
    state_dir = _auth_state_dir()
    record = {
        "provider": provider,
        "status": status,
        "event": event,
        "detail": detail,
        "updated_at": _utc_iso(),
        **(extra or {}),
    }
    path = state_dir / f"{provider}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _alert_status(status: str, severity: str | None = None) -> str:
    normalized_status = status.lower()
    normalized_severity = (severity or "").lower()
    if normalized_status == "ok" or normalized_severity == "info":
        return "done"
    return "needs-input"


def _auth_alert_item(
    *,
    provider: str,
    status: str,
    detail: str,
    event: str,
    severity: str | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _utc_iso()
    item_id = f"auth_alert_{provider}"
    item_status = _alert_status(status, severity)
    title_status = "resolved" if item_status == "done" else status.replace("_", " ")
    content = f"{provider}: {status}"
    if detail:
        content += f"\n\n{detail}"
    if event and event != "health_check":
        content += f"\n\nEvent: {event}"
    item = dict(existing or {})
    item.update(
        {
            "id": item_id,
            "type": "alert",
            "title": f"Auth alert: {provider} {title_status}",
            "status": item_status,
            "tags": ["auth_alert", provider, status],
            "origin": "system",
            "pinned": item_status == "needs-input",
            "quick": False,
            "parent_id": None,
            "updated_at": now,
            "error": None if item_status == "done" else detail,
            "result_path": None,
            "provider": provider,
            "auth_status": status,
            "severity": severity or ("info" if item_status == "done" else "warning"),
            "event": event,
        }
    )
    item.setdefault("created_at", now)
    item["messages"] = [
        {
            "id": f"auth_{provider}",
            "sender": "mira",
            "content": content,
            "timestamp": now,
            "kind": "text",
        }
    ]
    return item


def _rebuild_user_manifest(user_dir: Path) -> None:
    entries: list[dict[str, Any]] = []
    items_dir = user_dir / "items"
    if items_dir.exists():
        for path in sorted(items_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append(
                {
                    "id": item.get("id", path.stem),
                    "title": item.get("title", ""),
                    "type": item.get("type", "item"),
                    "status": item.get("status", ""),
                    "updated_at": item.get("updated_at", ""),
                }
            )
    entries.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    _write_json_atomic(user_dir / "manifest.json", {"updated_at": _utc_iso(), "items": entries})


def _surface_auth_alert(
    provider: str,
    *,
    status: str,
    detail: str = "",
    event: str = "health_check",
    severity: str | None = None,
) -> None:
    try:
        from config import MIRA_DIR, get_known_user_ids
    except Exception:
        return

    user_ids = get_known_user_ids()
    if not user_ids:
        return
    for user_id in user_ids:
        user_dir = MIRA_DIR / "users" / user_id
        items_dir = user_dir / "items"
        item_path = items_dir / f"auth_alert_{provider}.json"
        existing = None
        if item_path.exists():
            try:
                existing = json.loads(item_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
        if _alert_status(status, severity) == "done" and existing is None:
            continue
        item = _auth_alert_item(
            provider=provider,
            status=status,
            detail=detail,
            event=event,
            severity=severity,
            existing=existing,
        )
        try:
            _write_json_atomic(item_path, item)
            _rebuild_user_manifest(user_dir)
        except OSError:
            continue


def check_anthropic_oauth() -> AuthHealthResult:
    from config import CLAUDE_BIN

    path = Path(CLAUDE_BIN)
    if not path.exists():
        return AuthHealthResult("anthropic_oauth", "expired", "critical", f"Claude CLI missing: {path}")
    return AuthHealthResult("anthropic_oauth", "ok", "info", f"Claude CLI present: {path}")


def check_anthropic_api() -> AuthHealthResult:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return AuthHealthResult("anthropic_api", "ok", "info", "ANTHROPIC_API_KEY configured")
    return AuthHealthResult("anthropic_api", "missing", "warning", "ANTHROPIC_API_KEY is not configured")


def check_bridge_tls_cert(cert_path: Path | None = None) -> AuthHealthResult:
    from config import WEBGUI_TLS_CERT_FILE

    path = Path(cert_path or WEBGUI_TLS_CERT_FILE)
    if not path.exists():
        return AuthHealthResult("bridge_tls_cert", "missing", "warning", f"TLS cert missing: {path}")
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))
        not_after = decoded.get("notAfter", "")
        expires = parsedate_to_datetime(not_after)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        days = int((expires - datetime.now(timezone.utc)).total_seconds() // 86400)
    except Exception as exc:
        return AuthHealthResult("bridge_tls_cert", "invalid", "critical", f"TLS cert unreadable: {exc}")

    if days < 7:
        return AuthHealthResult(
            "bridge_tls_cert", "expiring", "critical", "TLS cert expires in <7 days", expires.isoformat(), days
        )
    if days < 30:
        return AuthHealthResult(
            "bridge_tls_cert", "expiring", "warning", "TLS cert expires in <30 days", expires.isoformat(), days
        )
    return AuthHealthResult("bridge_tls_cert", "ok", "info", "TLS cert expiry ok", expires.isoformat(), days)


def run_auth_health_checks() -> list[AuthHealthResult]:
    results = [check_anthropic_oauth(), check_anthropic_api(), check_bridge_tls_cert()]
    for result in results:
        status = "ok" if result.status == "ok" else result.status
        _write_provider_state(
            result.provider,
            status=status,
            detail=result.detail,
            event="health_check",
            extra={
                "severity": result.severity,
                "expires_at": result.expires_at,
                "days_remaining": result.days_remaining,
            },
        )
        _surface_auth_alert(
            result.provider, status=status, detail=result.detail, event="health_check", severity=result.severity
        )
    return results
