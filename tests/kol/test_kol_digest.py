import json

import handler


def test_registry_contains_expected_kol_count():
    registry = handler.load_registry()

    assert len(registry["kol_list"]) == 53
    assert {kol["category"] for kol in registry["kol_list"]} == {
        "ai_tech",
        "markets",
        "geopolitics",
        "culture",
    }


def test_source_type_inference_prefers_primary_platforms():
    assert handler.infer_source_type("https://example.substack.com/p/test") == "substack"
    assert handler.infer_source_type("https://x.com/simonw/status/1") == "x"
    assert handler.infer_source_type("https://youtube.com/watch?v=1") == "youtube"
    assert handler.infer_source_type("https://example.com", "New podcast with Jim Bianco") == "podcast"


def test_dry_run_does_not_mutate_state(tmp_path, monkeypatch):
    monkeypatch.setattr(handler, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(handler, "ITEMS_PATH", tmp_path / "items.jsonl")
    monkeypatch.setattr(handler, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.setattr(handler, "DAILY_DIR", tmp_path / "daily")
    monkeypatch.setattr(handler, "ARTIFACTS_DIR", tmp_path / "artifacts")

    report_path = handler.run_daily_digest(max_kols=2, dry_run=True)

    assert report_path.exists()
    assert not handler.STATE_PATH.exists()
    assert not handler.ITEMS_PATH.exists()
    assert not handler.HEALTH_PATH.exists()


def test_real_run_records_state_with_dry_items(tmp_path, monkeypatch):
    monkeypatch.setattr(handler, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(handler, "ITEMS_PATH", tmp_path / "items.jsonl")
    monkeypatch.setattr(handler, "HEALTH_PATH", tmp_path / "health.json")
    monkeypatch.setattr(handler, "DAILY_DIR", tmp_path / "daily")
    monkeypatch.setattr(handler, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(handler, "fetch_kol_items", lambda kol, now_iso: handler._dry_run_items([kol], now_iso))
    monkeypatch.setattr(handler, "enrich_top_items", lambda items, limit=8: items)
    monkeypatch.setattr(handler, "build_llm_summary", lambda items: "Synthetic daily synthesis.")
    monkeypatch.setattr(handler, "_create_bridge_feed", lambda report, now, user_id="ang": None)

    report_path = handler.run_daily_digest(max_kols=1, dry_run=False)
    state = json.loads(handler.STATE_PATH.read_text(encoding="utf-8"))

    assert report_path.exists()
    assert state["last_kol_digest_path"] == str(report_path)
    assert handler.HEALTH_PATH.exists()
    assert handler.ITEMS_PATH.exists()
