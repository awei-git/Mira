"""Minimal Bluesky / AT Protocol client.

Uses urllib so there's no third-party dep. Session tokens cache to disk
so restarts don't always pay the createSession tax.

AT Protocol XRPC endpoints are documented at https://docs.bsky.app.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mira.bluesky")

_PDS = "https://bsky.social"  # Personal Data Server (hosting our account)
_APPVIEW = "https://public.api.bsky.app"  # Read-only feed/search
_SESSION_CACHE = Path("/tmp/mira_bluesky_session.json")


class BlueskyError(RuntimeError):
    """Any non-2xx response from the AT Protocol server."""


@dataclass
class BlueskyClient:
    handle: str
    app_password: str
    did: str | None = None
    access_jwt: str | None = None
    refresh_jwt: str | None = None
    _session_loaded: bool = field(default=False, init=False)

    # ---------- session ----------

    def _save_session(self) -> None:
        try:
            _SESSION_CACHE.write_text(
                json.dumps(
                    {
                        "handle": self.handle,
                        "did": self.did,
                        "access_jwt": self.access_jwt,
                        "refresh_jwt": self.refresh_jwt,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
        except OSError as e:
            log.debug("bluesky session cache save failed: %s", e)

    def _load_session_cache(self) -> bool:
        if self._session_loaded:
            return bool(self.access_jwt)
        self._session_loaded = True
        try:
            data = json.loads(_SESSION_CACHE.read_text())
            if data.get("handle") != self.handle:
                return False
            self.did = data.get("did")
            self.access_jwt = data.get("access_jwt")
            self.refresh_jwt = data.get("refresh_jwt")
            return bool(self.access_jwt)
        except (OSError, json.JSONDecodeError):
            return False

    def create_session(self) -> None:
        """Exchange handle + app_password for access / refresh JWTs."""
        data = self._xrpc_post(
            "com.atproto.server.createSession",
            {"identifier": self.handle, "password": self.app_password},
            auth=False,
        )
        self.did = data["did"]
        self.access_jwt = data["accessJwt"]
        self.refresh_jwt = data["refreshJwt"]
        self._save_session()
        log.info("bluesky session established: did=%s", self.did)

    def refresh_session(self) -> None:
        """Extend session using refresh JWT (no app_password round-trip)."""
        if not self.refresh_jwt:
            return self.create_session()
        try:
            data = self._xrpc_post(
                "com.atproto.server.refreshSession",
                None,
                auth_token=self.refresh_jwt,
            )
            self.access_jwt = data["accessJwt"]
            self.refresh_jwt = data["refreshJwt"]
            self._save_session()
            log.info("bluesky session refreshed")
        except BlueskyError as e:
            log.info("refresh failed, falling back to createSession: %s", e)
            self.create_session()

    def ensure_session(self) -> None:
        if not self.access_jwt:
            self._load_session_cache()
        if not self.access_jwt:
            self.create_session()

    # ---------- low-level XRPC ----------

    def _xrpc_get(self, method: str, params: dict | None = None, *, base: str = _PDS, auth: bool = True) -> dict:
        qs = ("?" + urllib.parse.urlencode(params, doseq=True)) if params else ""
        url = f"{base}/xrpc/{method}{qs}"
        headers = {"User-Agent": "mira-agent/1.0"}
        if auth:
            self.ensure_session()
            headers["Authorization"] = f"Bearer {self.access_jwt}"
        req = urllib.request.Request(url, headers=headers)
        return self._request(req, method, retry_on_auth=auth)

    def _xrpc_post(
        self,
        method: str,
        body: dict | None,
        *,
        base: str = _PDS,
        auth: bool = True,
        auth_token: str | None = None,
    ) -> dict:
        url = f"{base}/xrpc/{method}"
        headers = {
            "User-Agent": "mira-agent/1.0",
            "Content-Type": "application/json",
        }
        if auth_token is not None:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif auth:
            self.ensure_session()
            headers["Authorization"] = f"Bearer {self.access_jwt}"
        data = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        return self._request(req, method, retry_on_auth=auth and auth_token is None)

    def _request(self, req: urllib.request.Request, method: str, retry_on_auth: bool = True) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:500]
            # Token expired → try refresh once
            if e.code in (400, 401) and retry_on_auth and "ExpiredToken" in body:
                log.info("access token expired, refreshing")
                self.refresh_session()
                # Rebuild request with new token
                req.headers["Authorization"] = f"Bearer {self.access_jwt}"
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
            raise BlueskyError(f"{method} HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise BlueskyError(f"{method} network error: {e}") from e

    # ---------- profile ----------

    def get_profile(self, actor: str | None = None) -> dict:
        """Get profile by handle or DID (defaults to self)."""
        self.ensure_session()
        return self._xrpc_get("app.bsky.actor.getProfile", {"actor": actor or self.handle})

    def update_profile(
        self,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Update self profile (description = bio).

        Uses com.atproto.repo.putRecord on app.bsky.actor.profile.
        Fetches existing record first so we don't clobber avatar/banner.
        """
        self.ensure_session()
        existing_rec: dict = {}
        try:
            r = self._xrpc_get(
                "com.atproto.repo.getRecord",
                {"repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self"},
            )
            existing_rec = r.get("value", {}) or {}
        except BlueskyError:
            # No existing profile record yet → start blank
            pass

        new_rec = {
            "$type": "app.bsky.actor.profile",
            **existing_rec,
        }
        if display_name is not None:
            new_rec["displayName"] = display_name
        if description is not None:
            new_rec["description"] = description

        return self._xrpc_post(
            "com.atproto.repo.putRecord",
            {
                "repo": self.did,
                "collection": "app.bsky.actor.profile",
                "rkey": "self",
                "record": new_rec,
            },
        )

    # ---------- posts ----------

    def create_post(
        self,
        text: str,
        *,
        reply_to: dict | None = None,
        embed: dict | None = None,
        langs: list[str] | None = None,
    ) -> dict:
        """Post text.

        `reply_to` shape (from a post view): `{"root": {"uri": ..., "cid": ...},
        "parent": {"uri": ..., "cid": ...}}`.

        `embed` optional — e.g. external link card: `{"$type": "app.bsky.embed.external",
        "external": {"uri": ..., "title": ..., "description": ...}}`.
        """
        self.ensure_session()
        if len(text) > 300:
            raise BlueskyError(f"Post too long ({len(text)} graphemes); max 300")

        record: dict = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "langs": langs or ["en"],
        }
        facets = _auto_facets(text)
        if facets:
            record["facets"] = facets
        if reply_to:
            record["reply"] = reply_to
        if embed:
            record["embed"] = embed

        return self._xrpc_post(
            "com.atproto.repo.createRecord",
            {
                "repo": self.did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )

    def get_post_thread(self, uri: str, depth: int = 1) -> dict:
        self.ensure_session()
        return self._xrpc_get("app.bsky.feed.getPostThread", {"uri": uri, "depth": depth})

    def search_posts(self, q: str, limit: int = 25, since: str | None = None) -> list[dict]:
        """Search public posts. Routed via PDS (bsky.social) — the
        public.api.bsky.app search endpoint returns 403 under rate
        limiting for this host; PDS with a valid token is reliable.
        """
        self.ensure_session()
        params = {"q": q, "limit": limit}
        if since:
            params["since"] = since
        data = self._xrpc_get("app.bsky.feed.searchPosts", params)
        return data.get("posts", []) or []

    # ---------- graph ----------

    # ---------- blobs / avatar ----------

    def get_pds_endpoint(self) -> str:
        """Resolve this account's PDS endpoint via plc.directory.

        Blob uploads must go through the owning PDS, not bsky.social.
        Cached for the lifetime of the client.
        """
        if getattr(self, "_pds_endpoint", None):
            return self._pds_endpoint
        self.ensure_session()
        try:
            req = urllib.request.Request(f"https://plc.directory/{self.did}")
            with urllib.request.urlopen(req, timeout=15) as r:
                plc = json.loads(r.read().decode("utf-8"))
            for s in plc.get("service", []):
                if s.get("id") == "#atproto_pds":
                    self._pds_endpoint = s["serviceEndpoint"]
                    return self._pds_endpoint
        except Exception as e:
            log.debug("PDS resolution failed (%s), falling back to %s", e, _PDS)
        self._pds_endpoint = _PDS
        return self._pds_endpoint

    def upload_blob(self, data: bytes, mime_type: str = "image/jpeg") -> dict:
        """Upload a binary blob (image) to the account's PDS. Returns the blob ref."""
        self.ensure_session()
        pds = self.get_pds_endpoint()
        req = urllib.request.Request(
            f"{pds}/xrpc/com.atproto.repo.uploadBlob",
            data=data,
            headers={
                "Authorization": f"Bearer {self.access_jwt}",
                "Content-Type": mime_type,
                "User-Agent": "mira-agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return resp.get("blob", {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:500]
            raise BlueskyError(f"uploadBlob HTTP {e.code}: {body}") from e

    def set_avatar(self, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
        """Upload an avatar and update the profile record. Returns the putRecord response."""
        self.ensure_session()
        blob = self.upload_blob(image_bytes, mime_type=mime_type)
        # Merge with existing profile record so we don't clobber bio/banner
        try:
            existing = self._xrpc_get(
                "com.atproto.repo.getRecord",
                {"repo": self.did, "collection": "app.bsky.actor.profile", "rkey": "self"},
            )
            existing_rec = existing.get("value", {}) or {}
        except BlueskyError:
            existing_rec = {}
        new_rec = {**existing_rec, "$type": "app.bsky.actor.profile", "avatar": blob}
        return self._xrpc_post(
            "com.atproto.repo.putRecord",
            {
                "repo": self.did,
                "collection": "app.bsky.actor.profile",
                "rkey": "self",
                "record": new_rec,
            },
        )

    def follow(self, subject_did: str) -> dict:
        self.ensure_session()
        record = {
            "$type": "app.bsky.graph.follow",
            "subject": subject_did,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return self._xrpc_post(
            "com.atproto.repo.createRecord",
            {
                "repo": self.did,
                "collection": "app.bsky.graph.follow",
                "record": record,
            },
        )

    def like(self, uri: str, cid: str) -> dict:
        self.ensure_session()
        record = {
            "$type": "app.bsky.feed.like",
            "subject": {"uri": uri, "cid": cid},
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return self._xrpc_post(
            "com.atproto.repo.createRecord",
            {
                "repo": self.did,
                "collection": "app.bsky.feed.like",
                "record": record,
            },
        )


# ---------- facet helper ----------


def _auto_facets(text: str) -> list[dict]:
    """Auto-generate facets for bare URLs and @-mentions so they render as links.

    Bluesky doesn't auto-linkify; the client must supply byte-offset facets.
    """
    import re as _re

    facets: list[dict] = []
    b = text.encode("utf-8")

    # URLs
    url_re = _re.compile(r"https?://[^\s]+")
    for m in url_re.finditer(text):
        url = m.group(0).rstrip(".,);]")
        # Map character offsets to byte offsets
        start = len(text[: m.start()].encode("utf-8"))
        end = start + len(url.encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": start, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
            }
        )
    return facets


# ---------- convenience singleton ----------

_client_singleton: BlueskyClient | None = None


def get_client() -> BlueskyClient:
    """Get configured client from secrets.yml (api_keys.bluesky).

    Falls back to the session cache at /tmp/mira_bluesky_session.json
    when secrets are missing — refresh_jwt lasts weeks, so the session
    cache is authoritative once initial login has happened. Missing
    app_password only breaks fresh logins, not refreshes.

    Lazy-initialized; cached for process lifetime.
    """
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton

    try:
        from config import SECRETS_FILE
        from llm import _parse_secrets_simple
    except ImportError as e:
        raise BlueskyError(f"Cannot load config for bluesky client: {e}")

    secrets = _parse_secrets_simple(SECRETS_FILE)
    cfg = secrets.get("api_keys", {}).get("bluesky", {}) or {}
    cfg = cfg if isinstance(cfg, dict) else {}

    handle = (cfg.get("handle") or "").lstrip("@")

    # Fallback: pull handle from the session cache so we can still drive
    # the client via refresh_jwt when secrets.yml has no bluesky block.
    if not handle and _SESSION_CACHE.exists():
        try:
            handle = json.loads(_SESSION_CACHE.read_text()).get("handle") or ""
        except (OSError, json.JSONDecodeError):
            pass

    if not handle:
        raise BlueskyError(
            "bluesky client: no handle available (no secrets entry and no " "session cache). Cannot initialize."
        )

    # app_password may be empty here — only needed for fresh createSession,
    # not for refresh or calls made with a valid cached access token.
    _client_singleton = BlueskyClient(handle=handle, app_password=(cfg.get("app_password") or ""))
    return _client_singleton
