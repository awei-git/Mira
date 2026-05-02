"""Compatibility checks for the existing production Substack stack."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


_AGENTS_DIR = Path(__file__).resolve().parent.parent
_SOCIALMEDIA_DIR = _AGENTS_DIR / "socialmedia"


CURRENT_STACK = {
    "publish_article": ("substack", "publish_to_substack"),
    "sync_posts_for_ios": ("substack", "sync_posts_for_ios"),
    "recent_posts": ("substack", "get_recent_posts"),
    "publication_stats": ("substack", "fetch_publication_stats"),
    "subscriber_snapshot": ("substack_stats", "fetch_subscriber_snapshot"),
    "own_comment_replies": ("substack", "check_and_reply_comments"),
    "external_comment": ("substack", "comment_on_post"),
    "outbound_comment_replies": ("substack", "check_outbound_comment_replies"),
    "outbound_note_replies": ("substack", "check_outbound_note_replies"),
    "article_notes_queue": ("notes", "queue_notes_for_article"),
}


def ensure_socialmedia_path() -> None:
    path = str(_SOCIALMEDIA_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def check_current_stack() -> dict:
    """Return a non-invasive capability report for the existing stack."""
    ensure_socialmedia_path()
    capabilities: dict[str, dict] = {}
    ok = True
    for capability, (module_name, attr_name) in CURRENT_STACK.items():
        try:
            module = importlib.import_module(module_name)
            attr = getattr(module, attr_name)
            present = callable(attr)
            reason = "" if present else f"{module_name}.{attr_name} is not callable"
        except Exception as exc:
            present = False
            reason = str(exc)
        capabilities[capability] = {
            "module": module_name,
            "function": attr_name,
            "present": present,
            "reason": reason,
        }
        ok = ok and present
    return {"ok": ok, "capabilities": capabilities}
