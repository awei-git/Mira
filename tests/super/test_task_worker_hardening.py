from __future__ import annotations


def test_load_matching_progress_ignores_other_task(tmp_path):
    import task_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "progress.md").write_text(
        "# Progress — req_old\n\n## Status: blocked\n",
        encoding="utf-8",
    )

    assert task_worker._load_matching_progress(workspace, "req_new") == ""


def test_load_matching_progress_accepts_current_task(tmp_path):
    import task_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    progress = "# Progress — req_current\n\n## Status: running\n"
    (workspace / "progress.md").write_text(progress, encoding="utf-8")

    assert task_worker._load_matching_progress(workspace, "req_current") == progress


def test_emit_status_replaces_existing_trailing_status_cards(monkeypatch, tmp_path):
    import json

    import task_worker

    item_path = tmp_path / "task_1.json"
    item_path.write_text(
        json.dumps(
            {
                "id": "task_1",
                "messages": [
                    {"id": "u1", "sender": "ang", "kind": "text", "content": "go"},
                    {"id": "s1", "sender": "agent", "kind": "status_card", "content": "{}"},
                    {"id": "s2", "sender": "agent", "kind": "status_card", "content": "{}"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(task_worker, "_item_file", lambda task_id: item_path)

    task_worker._emit_status("task_1", "Step 1/2: checking. Elapsed 1m; timeout guard in 14m.", "hourglass")

    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert [m["kind"] for m in item["messages"]] == ["text", "status_card"]
    card = json.loads(item["messages"][-1]["content"])
    assert card["text"].startswith("Step 1/2")


def test_heartbeat_activity_reports_current_step(tmp_path):
    import json

    import task_worker

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "step_states.json").write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "step_index": 0,
                        "status": "running",
                        "execution_agent": "general",
                        "input_summary": "查找 Tetra synthesis 代码并诊断价格偏差",
                    },
                    {"step_index": 1, "status": "pending", "declared_agent": "coder"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    heartbeat = task_worker._Heartbeat("task_1", workspace=workspace)
    snapshot = heartbeat._activity_snapshot(300)

    assert snapshot["current_step"] == 1
    assert snapshot["total_steps"] == 2
    assert snapshot["current_agent"] == "general"
    assert "Elapsed 5m" in snapshot["status_text"]


def test_daily_zhesi_feed_reply_uses_conversation_fast_path():
    import task_worker

    task = {
        "id": "feed_zhesi_20260505",
        "type": "feed",
        "title": "每日哲思 05/05",
        "tags": ["zhesi"],
    }

    assert task_worker._looks_like_conversation_feed("feed_zhesi_20260505", task)


def test_market_feed_reply_uses_market_fast_path():
    import task_worker

    task = {
        "id": "feed_market_20260505_pre",
        "type": "feed",
        "title": "开市前市场分析 2026-05-05",
        "tags": ["market", "analyst", "pre-market"],
    }

    assert task_worker._looks_like_market_thread("feed_market_20260505_pre", task)


def test_current_message_overrides_later_agent_thought():
    import task_worker

    task = {
        "messages": [
            {"sender": "agent", "content": "今天只聊游牧和农耕。"},
            {"sender": "ang", "content": "这个应该是conversation的形式"},
            {"sender": "agent", "content": "另一个自发想法"},
        ]
    }

    updated = task_worker._task_with_current_message(task, "这个应该是conversation的形式", "ang")

    assert updated["current_message"]["content"] == "这个应该是conversation的形式"
    assert updated["messages"][-1]["content"] == "另一个自发想法"
