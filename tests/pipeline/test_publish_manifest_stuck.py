import json
from datetime import datetime, timedelta, timezone


def test_get_stuck_articles_ignores_terminal_and_blocked_statuses(monkeypatch, tmp_path):
    monkeypatch.setenv("MIRA_PUBLISH_MANIFEST_PATH", str(tmp_path / "manifest.json"))

    from publish.manifest import get_stuck_articles, load_manifest

    old = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "articles": {
            status: {
                "slug": status,
                "status": status,
                "timestamps": {status: old},
            }
            for status in (
                "complete",
                "skip",
                "skipped",
                "deleted",
                "approval_required",
                "published",
                "podcast_en",
                "blocked_language",
                "blocked_writer_gate",
                "blocked_security_claim",
                "blocked_publish_error",
                "blocked_manual_review",
                "parked_legacy_blocked",
            )
        }
    }
    manifest["articles"]["approved_waiting"] = {
        "slug": "approved_waiting",
        "status": "approved",
        "timestamps": {"approved": old},
    }
    manifest["articles"]["approved_with_error"] = {
        "slug": "approved_with_error",
        "status": "approved",
        "error": "manual review required",
        "timestamps": {"approved": old},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert load_manifest()["articles"]
    stuck = get_stuck_articles(timeout_minutes=120)

    assert [entry["slug"] for entry in stuck] == ["approved_waiting"]
