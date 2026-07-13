import json
from datetime import datetime, timezone
from types import SimpleNamespace

import daily_collab
import task_worker  # noqa: F401  (registers task_worker before handlers_legacy import)
import handlers_legacy
from workflows import daily as daily_workflows


def test_daily_collab_thread_is_single_stable_thread():
    assert daily_collab.is_daily_collab_thread("disc_daily_collab", [])
    assert daily_collab.is_daily_collab_thread("disc_other", ["daily-collab"])
    assert not daily_collab.is_daily_collab_thread("disc_other", ["conversation"])


def test_daily_collab_context_block_loads_summary(tmp_path):
    summary_file = tmp_path / "daily_collab_summary.md"
    summary_file.write_text("- my human wants a single history.\n", encoding="utf-8")

    block = daily_collab.daily_collab_context_block(summary_file)

    assert "Daily collab running summary" in block
    assert "single history" in block
    assert "my human" in block


def test_daily_collab_monitor_block_exposes_stale_pipeline_signal(tmp_path):
    signal_file = tmp_path / "pipeline_stale.json"
    signal_file.write_text(
        json.dumps(
            {
                "stale": [
                    {
                        "component": "explorer",
                        "gap_seconds": 458281,
                        "threshold_seconds": 72000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    block = daily_collab.daily_collab_monitor_block(signal_file)

    assert "Current monitor signals" in block
    assert "explorer" in block
    assert "behavior should change" in block


def test_daily_collab_monitor_block_exposes_writing_stall_signal(tmp_path):
    signal_file = tmp_path / "pipeline_stale.json"
    signal_file.write_text(
        json.dumps(
            {
                "stale": [
                    {
                        "component": "writer",
                        "kind": "writing_stalled",
                        "gap_seconds": 9000,
                        "threshold_seconds": 3600,
                        "stalled_count": 1,
                        "projects": [{"title": "Stalled Essay", "phase": "reviewing"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    block = daily_collab.daily_collab_monitor_block(signal_file)

    assert "Current monitor signals" in block
    assert "Stalled Essay" in block
    assert "Decision: act" in block
    assert "first-hand essay seed" in block


def test_daily_collab_monitor_block_exposes_provider_circuit_signal(tmp_path):
    signal_file = tmp_path / "pipeline_stale.json"
    provider_file = tmp_path / "api_provider_circuit.json"
    signal_file.write_text(json.dumps({"stale": []}), encoding="utf-8")
    provider_file.write_text(
        json.dumps(
            {
                "deepseek": {
                    "reason": "Insufficient Balance",
                    "disabled_until": "2999-06-25T01:09:21Z",
                    "updated_at": "2999-06-24T19:09:21Z",
                }
            }
        ),
        encoding="utf-8",
    )

    block = daily_collab.daily_collab_monitor_block(signal_file, provider_path=provider_file)

    assert "Current monitor signals" in block
    assert "deepseek" in block
    assert "Insufficient Balance" in block
    assert "Codex subscription" in block


def test_daily_collab_monitor_closures_record_act_watch_discard_receipts(tmp_path):
    signal_file = tmp_path / "pipeline_stale.json"
    provider_file = tmp_path / "api_provider_circuit.json"
    closure_file = tmp_path / "monitor_closures.jsonl"
    signal_file.write_text(
        json.dumps(
            {
                "stale": [
                    {
                        "component": "writer",
                        "gap_seconds": 200,
                        "threshold_seconds": 50,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    provider_file.write_text(
        json.dumps(
            {
                "deepseek": {
                    "reason": "Insufficient Balance",
                    "disabled_until": "2999-06-25T01:09:21Z",
                    "updated_at": "2999-06-24T19:09:21Z",
                }
            }
        ),
        encoding="utf-8",
    )

    signals = daily_collab.collect_daily_collab_monitor_signals(signal_file, provider_path=provider_file)
    written = daily_collab.record_daily_collab_monitor_closures(signals, path=closure_file)
    duplicate = daily_collab.record_daily_collab_monitor_closures(signals, path=closure_file)

    assert len(written) == 2
    assert duplicate == []
    assert {record["decision"] for record in written} == {"act"}
    assert any(record["budget_related"] for record in written)
    assert "Codex subscription" in closure_file.read_text(encoding="utf-8")


def test_persist_daily_collab_summary_uses_summarizer_and_scrubs_long_credentials(tmp_path):
    summary_file = tmp_path / "summary.md"
    summary_file.write_text("- old memory\n", encoding="utf-8")
    seen = {}

    def summarizer(prompt: str) -> str:
        seen["prompt"] = prompt
        return "- my human wants one chat.\n- credential: " + ("A" * 40)

    summary = daily_collab.persist_daily_collab_summary(
        latest_human="make it simple",
        latest_mira="I will keep one thread.",
        recent_history="Human: old\nMira: old",
        summarizer=summarizer,
        path=summary_file,
    )

    assert "old memory" in seen["prompt"]
    assert "make it simple" in seen["prompt"]
    assert "one chat" in summary
    assert "A" * 40 not in summary_file.read_text(encoding="utf-8")
    assert "[redacted credential]" in summary


def test_daily_collab_exchange_review_records_visible_contract_flags(tmp_path):
    review_file = tmp_path / "review.jsonl"

    record = daily_collab.record_daily_collab_exchange_review(
        latest_human="Can we make this conversational?",
        latest_mira="- First\n- Second\n- Third?",
        summary_updated=False,
        model_response=False,
        path=review_file,
    )

    assert "bullet_list_reply" in record["flags"]
    assert "summary_not_updated" in record["flags"]
    assert "fallback_response" in record["flags"]
    assert not record["contract_pass"]

    persisted = json.loads(review_file.read_text(encoding="utf-8"))
    assert persisted["flags"] == record["flags"]
    assert persisted["human_preview"] == "Can we make this conversational?"
    assert "First" in persisted["mira_preview"]


def test_daily_collab_exchange_review_classifies_human_correction(tmp_path):
    review_file = tmp_path / "review.jsonl"

    record = daily_collab.record_daily_collab_exchange_review(
        latest_human="not what I want. make it conversational, one question at a time",
        latest_mira="I will keep it to one natural hook and stop turning this into homework.",
        summary_updated=True,
        model_response=True,
        path=review_file,
    )

    assert record["human_engagement"]["human_originated"] is True
    assert "correction" in record["human_signal_labels"]
    assert "implementation_request" in record["human_signal_labels"]
    assert record["human_engagement"]["requires_behavior_change"] is True
    assert "concrete behavior change" in record["human_engagement"]["behavior_change_hint"]


def test_daily_collab_eval_context_block_turns_feedback_into_behavior(tmp_path):
    review_file = tmp_path / "review.jsonl"
    record = daily_collab.assess_daily_collab_exchange(
        latest_human="This is boring and not interesting. Use one lived failure instead.",
        latest_mira="I will use the monitor failure as the next scene.",
        summary_updated=True,
        model_response=True,
    )
    daily_collab.append_daily_collab_review(record, path=review_file)

    block = daily_collab.daily_collab_eval_context_block(review_file)

    assert "Recent collab eval signals" in block
    assert "disengagement=1" in block
    assert "correction=1" in block
    assert "shorter, more concrete" in block


def test_daily_collab_weekly_review_extracts_first_hand_article_seed(tmp_path):
    review_file = tmp_path / "review.jsonl"
    seed_file = tmp_path / "seeds.jsonl"
    record = daily_collab.assess_daily_collab_exchange(
        latest_human="The monitor never changed your behavior, that is the interesting failure.",
        latest_mira=(
            "I think my monitor was honest and useless. "
            "It collected a signal, but I did not turn that signal into a different action."
        ),
        summary_updated=True,
        model_response=True,
    )

    daily_collab.append_daily_collab_review(record, path=review_file)
    seed = daily_collab.extract_daily_collab_article_seed(record)
    assert seed is not None
    assert "Monitor" in seed["title"]
    assert daily_collab.append_daily_collab_article_seed(seed, path=seed_file)
    assert not daily_collab.append_daily_collab_article_seed(seed, path=seed_file)

    text, metrics = daily_collab.build_daily_collab_weekly_review([record])

    assert "Daily Collab Weekly Review" in text
    assert metrics["candidate_article_seeds"] == 1
    assert "Pick one candidate seed" in metrics["next_experiment"]


def test_daily_collab_weekly_review_prioritizes_correction_loop():
    record = daily_collab.assess_daily_collab_exchange(
        latest_human="not what I want. stop asking thesis questions.",
        latest_mira="I will ask one concrete question from the actual failure.",
        summary_updated=True,
        model_response=True,
    )

    text, metrics = daily_collab.build_daily_collab_weekly_review([record])

    assert metrics["human_signal_counts"]["correction"] == 1
    assert metrics["behavior_change_required"] == 1
    assert "Human Engagement" in text
    assert "latest correction" in metrics["next_experiment"]


def test_daily_collab_article_seed_materializes_overall_picture_brief(tmp_path):
    seed_file = tmp_path / "seeds.jsonl"
    briefs_dir = tmp_path / "briefs"
    seed = {
        "seed_id": "monitor-seed",
        "status": "candidate",
        "title": "My Monitor Was Honest And Useless",
        "why_interesting": "It came from my monitor collecting a signal that did not change my action.",
        "human_preview": "The monitor never changed your behavior.",
        "mira_preview": "I should turn every monitor signal into act, watch, or discard.",
        "next_conversation_hook": "Ask whether this is the sharper first V5 essay seed.",
        "publication_gate": "Human approval required before public publication.",
    }
    seed_file.write_text(json.dumps(seed, ensure_ascii=False) + "\n", encoding="utf-8")

    created = daily_collab.materialize_daily_collab_article_briefs(seeds_path=seed_file, briefs_dir=briefs_dir)
    duplicate = daily_collab.materialize_daily_collab_article_briefs(seeds_path=seed_file, briefs_dir=briefs_dir)

    assert created == [briefs_dir / "monitor-seed.md"]
    assert duplicate == []
    text = created[0].read_text(encoding="utf-8")
    assert "not approved for publication" in text
    assert "Overall Picture" in text
    assert "act, watch, or discard" in text


def test_daily_collab_article_seed_ignores_transport_probe():
    seed = daily_collab.extract_daily_collab_article_seed(
        {
            "human_preview": "V5 phone-path server write probe from Codex.",
            "mira_preview": "Confirmed: this reached the Mira discussion thread.",
        }
    )

    assert seed is None


def test_daily_collab_selects_first_hand_seed_for_discussion(tmp_path):
    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    (briefs_dir / "v5-real.md").write_text("# Brief\n", encoding="utf-8")
    seeds = [
        {
            "seed_id": "scheduled",
            "title": "I Hid The Wrong Failure",
            "status": "candidate",
            "human_preview": "[scheduled proactive daily collab message]",
            "mira_preview": "A provider failed and I sounded normal.",
        },
        {
            "seed_id": "probe",
            "title": "A First-Hand Mira Field Note",
            "status": "candidate",
            "human_preview": "V5 phone-path server write probe from Codex.",
            "mira_preview": "Confirmed: this reached the Mira discussion thread.",
        },
        {
            "seed_id": "v5-real",
            "title": "I Had Receipts, But Not Trust",
            "status": "candidate",
            "source": "v5_discussion_summary",
            "human_preview": "My human said green tests can still fail to do a simple job.",
            "mira_preview": "I need to write from first-hand failure, not generic AI commentary.",
        },
    ]

    selected = daily_collab.select_daily_collab_article_seed_for_discussion(seeds, briefs_dir=briefs_dir)

    assert selected
    assert selected["seed_id"] == "v5-real"


def test_daily_collab_operator_message_surfaces_selected_seed():
    message = daily_collab.build_daily_collab_operator_message(
        {
            "candidate_article_seeds": 3,
            "article_briefs_total": 2,
            "selected_article_seed": {
                "seed_id": "v5-real",
                "title": "I Had Receipts, But Not Trust",
                "why_interesting": "Passing tests and emitting status still did not help my human.",
                "human_preview": "The app could not send one message.",
                "mira_preview": "I should write from the failure I actually lived.",
            },
            "manifest": {
                "approval_required_count": 0,
                "approved_count": 0,
                "blocked_count": 0,
                "parked_count": 127,
            },
            "runtime": {"unresolved_count": 0},
            "writing_triage": {},
            "attention": "No act-level V5 signal is open.",
        }
    )

    assert "V5 writing lane is empty" in message
    assert "next public essay seed" in message
    assert "not approved for publication" in message
    assert "I Had Receipts, But Not Trust" in message


def test_daily_collab_operator_brief_summarizes_live_v5_truth(monkeypatch, tmp_path):
    signal_file = tmp_path / "pipeline_stale.json"
    provider_file = tmp_path / "api_provider_circuit.json"
    closures = tmp_path / "closures.jsonl"
    incidents = tmp_path / "incidents.jsonl"
    seeds_file = tmp_path / "seeds.jsonl"
    briefs_dir = tmp_path / "briefs"
    triage = tmp_path / "writing_triage_status.json"
    manifest = tmp_path / "publish_manifest.json"
    heartbeat = tmp_path / "heartbeat.json"
    briefs_dir.mkdir()
    (briefs_dir / "seed-1.md").write_text("# Brief\n", encoding="utf-8")
    signal_file.write_text(json.dumps({"stale": []}), encoding="utf-8")
    provider_file.write_text(
        json.dumps(
            {
                "deepseek": {
                    "reason": "Insufficient Balance",
                    "disabled_until": "2999-06-25T01:09:21Z",
                    "updated_at": "2999-06-24T19:09:21Z",
                }
            }
        ),
        encoding="utf-8",
    )
    closures.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "decision": "act",
                "kind": "provider_circuit",
                "subject": "deepseek",
                "summary": "deepseek balance failed",
                "next_action": "name the fallback path",
                "budget_related": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    incidents.write_text(
        json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "kind": "provider_budget_signal"}) + "\n",
        encoding="utf-8",
    )
    seeds_file.write_text(
        json.dumps(
            {
                "seed_id": "seed-1",
                "title": "I Had Receipts, But Not Trust",
                "status": "candidate",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    triage.write_text(
        json.dumps(
            {
                "checked_at": "2026-07-01T20:57:53Z",
                "considered_count": 34,
                "parked_count": 34,
                "kept_count": 0,
                "parked": [
                    {
                        "title": "You Cannot Be Your Own Verifier",
                        "reason": "parked: partial draft/review artifacts exist",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "articles": {
                    "a": {"title": "Approved One", "status": "approved"},
                    "b": {"title": "Blocked One", "status": "blocked_writer_gate"},
                }
            }
        ),
        encoding="utf-8",
    )
    heartbeat.write_text(
        json.dumps(
            {
                "busy": False,
                "agent_status": {
                    "busy": False,
                    "active_count": 0,
                    "unresolved_inventory": {
                        "count": 1,
                        "by_failure_class": {"worker_crash": 1},
                        "tasks": [
                            {
                                "task_id": "disc_failed",
                                "status": "failed",
                                "failure_class": "worker_crash",
                                "summary": "Worker crashed before reply.",
                            }
                        ],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        daily_collab,
        "build_daily_collab_weekly_review",
        lambda: ("weekly", {"contract_pass": 2, "total_exchanges": 3, "human_turns": 1}),
    )

    text, metrics = daily_collab.build_daily_collab_operator_brief(
        pipeline_stale_path=signal_file,
        provider_path=provider_file,
        monitor_closures_path=closures,
        incidents_path=incidents,
        seeds_path=seeds_file,
        briefs_dir=briefs_dir,
        writing_triage_path=triage,
        manifest_path=manifest,
        heartbeat_path=heartbeat,
    )
    message = daily_collab.build_daily_collab_operator_message(metrics)

    assert "Mira V5 Operator Brief" in text
    assert metrics["act_signals"] == 1
    assert metrics["budget_signals"] == 1
    assert metrics["recent_monitor_receipts"] == 1
    assert metrics["candidate_article_seeds"] == 1
    assert metrics["article_briefs_total"] == 1
    assert metrics["writing_triage"]["parked_count"] == 34
    assert metrics["manifest"]["approved_count"] == 1
    assert metrics["manifest"]["blocked_count"] == 1
    assert metrics["runtime"]["unresolved_count"] == 1
    assert "disc_failed" in metrics["next_move"]
    assert "Triage: parked=34" in text
    assert "parked 34 stale writing project" in message
    assert "not call the article pipeline recovered" in message
    assert "Runtime has 1 unresolved item" in message


def test_daily_collab_operator_delivery_is_deduped(tmp_path):
    delivery_file = tmp_path / "deliveries.jsonl"
    metrics = {
        "act_signals": 1,
        "budget_signals": 1,
        "candidate_article_seeds": 1,
        "article_briefs_total": 1,
        "attention": "Needs attention",
        "manifest": {"approved_count": 1, "blocked_count": 2},
    }
    key = daily_collab.operator_delivery_key(metrics, date="2026-07-01")

    assert not daily_collab.has_operator_delivery(key, path=delivery_file)
    daily_collab.record_operator_delivery(key=key, message="hello", metrics=metrics, path=delivery_file)
    assert daily_collab.has_operator_delivery(key, path=delivery_file)


def test_daily_collab_operator_brief_state_waits_for_successful_publish(monkeypatch, tmp_path):
    import core

    state = {}
    saved = []
    recorded = []
    metrics = {
        "act_signals": 1,
        "budget_signals": 0,
        "candidate_article_seeds": 1,
        "article_briefs_total": 1,
        "manifest": {"approval_required_count": 0, "approved_count": 0, "blocked_count": 0, "parked_count": 0},
        "runtime": {"unresolved_count": 0},
    }

    monkeypatch.setattr(core, "load_state", lambda user_id=None: state)
    monkeypatch.setattr(core, "save_state", lambda value, user_id=None: saved.append(dict(value)))
    monkeypatch.setattr(
        daily_collab,
        "write_daily_collab_operator_brief",
        lambda: (tmp_path / "operator.md", metrics),
    )
    monkeypatch.setattr(daily_collab, "build_daily_collab_operator_message", lambda _metrics: "operator message")
    monkeypatch.setattr(daily_collab, "operator_delivery_key", lambda _metrics: "delivery-key")
    monkeypatch.setattr(daily_collab, "has_operator_delivery", lambda _key: False)
    monkeypatch.setattr(daily_collab, "record_operator_delivery", lambda **kwargs: recorded.append(kwargs))
    monkeypatch.setattr(daily_workflows, "_publish_daily_collab_message", lambda **_kwargs: False)

    daily_workflows.do_daily_collab_operator_brief(user_id="ang")

    assert saved == []
    assert recorded == []
    assert not any(key.startswith("daily_collab_operator_brief_") for key in state)


def test_daily_collab_proactive_message_normalization_removes_report_shape():
    raw = "1. First point\n2. Second point\n\n" + ("word " * 300)

    normalized = daily_workflows._normalize_daily_collab_message(raw)

    assert not normalized.startswith("1.")
    assert len(normalized) <= 900


def test_daily_collab_recent_message_check_handles_utc_bridge_timestamp(monkeypatch, tmp_path):
    bridge_dir = tmp_path / "bridge"
    items_dir = bridge_dir / "users" / "ang" / "items"
    items_dir.mkdir(parents=True)
    (items_dir / "disc_daily_collab.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "sender": "agent",
                        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "content": "existing daily collab message",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(daily_workflows, "MIRA_DIR", bridge_dir)

    assert daily_workflows._has_recent_daily_collab_agent_message(user_id="ang")


def test_handle_discussion_injects_and_updates_daily_collab_summary(monkeypatch, tmp_path):
    prompts = []
    persisted = {}
    reviewed = {}

    monkeypatch.setattr(
        handlers_legacy,
        "get_persona_context",
        lambda: SimpleNamespace(identity="Mira identity", worldview="Mira worldview", beliefs="Mira beliefs"),
    )
    monkeypatch.setattr(handlers_legacy, "load_soul", lambda: {"memory": "Mira memory"})
    monkeypatch.setattr(handlers_legacy, "load_thread_memory", lambda _thread_id: "")
    monkeypatch.setattr(handlers_legacy, "_load_recent_journals", lambda _limit: "")
    monkeypatch.setattr(handlers_legacy, "_load_recent_briefings", lambda _limit: "")
    monkeypatch.setattr(handlers_legacy, "recall_context", lambda _message: "")
    monkeypatch.setattr(handlers_legacy, "load_thread_history", lambda _thread_id: "")
    monkeypatch.setattr(
        handlers_legacy,
        "daily_collab_context_block",
        lambda: "## Daily collab running summary\n- my human wants single-thread continuity.",
    )
    monkeypatch.setattr(
        handlers_legacy,
        "daily_collab_eval_context_block",
        lambda: "## Recent collab eval signals\n- Current behavior adaptation: keep one hook.",
    )
    monkeypatch.setattr(handlers_legacy, "_write_result", lambda *args, **kwargs: None)

    def fake_claude(prompt: str, **_kwargs) -> str:
        prompts.append(prompt)
        return "I will keep this as one continuing conversation."

    def fake_persist(**kwargs):
        persisted.update(kwargs)
        return "- updated"

    monkeypatch.setattr(handlers_legacy, "claude_think", fake_claude)
    monkeypatch.setattr(handlers_legacy, "persist_daily_collab_summary", fake_persist)
    monkeypatch.setattr(
        handlers_legacy, "record_daily_collab_exchange_review", lambda **kwargs: reviewed.update(kwargs)
    )

    task = {
        "tags": ["daily-collab"],
        "messages": [
            {"sender": "user", "content": "make the discussion tab simple"},
        ],
    }

    response = handlers_legacy.handle_discussion(
        task,
        tmp_path,
        "disc_daily_collab",
        "disc_daily_collab",
    )

    assert response == "I will keep this as one continuing conversation."
    assert "single-thread continuity" in prompts[0]
    assert "keep one hook" in prompts[0]
    assert "main Mira discussion thread" in prompts[0]
    assert persisted["latest_human"] == "make the discussion tab simple"
    assert persisted["latest_mira"] == response
    assert persisted["recent_history"]
    assert reviewed["summary_updated"] is True
    assert reviewed["model_response"] is True


def test_handle_discussion_probe_bypasses_model(monkeypatch, tmp_path):
    called = {"model": False}
    written = {}

    def fake_claude(*_args, **_kwargs):
        called["model"] = True
        return "model should not run"

    def fake_write_result(workspace, task_id, status, summary, tags=None):
        written.update(
            {
                "workspace": workspace,
                "task_id": task_id,
                "status": status,
                "summary": summary,
                "tags": tags,
            }
        )

    monkeypatch.setattr(handlers_legacy, "claude_think", fake_claude)
    monkeypatch.setattr(handlers_legacy, "_write_result", fake_write_result)

    response = handlers_legacy.handle_discussion(
        {"content": "V5 phone-path server write probe from Codex.", "tags": ["daily-collab"]},
        tmp_path,
        "disc_daily_collab",
        "disc_daily_collab",
    )

    assert called["model"] is False
    assert response == "Confirmed: this reached the Mira discussion thread."
    assert written["status"] == "done"
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == response


def test_handle_discussion_daily_new_thought_uses_local_seed(monkeypatch, tmp_path):
    called = {"model": False}
    persisted = {}
    reviewed = {}

    def fake_claude(*_args, **_kwargs):
        called["model"] = True
        return "model should not run"

    monkeypatch.setattr(handlers_legacy, "claude_think", fake_claude)
    monkeypatch.setattr(handlers_legacy, "_write_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        handlers_legacy,
        "select_daily_collab_article_seed_for_discussion",
        lambda: {
            "seed_id": "v5-real",
            "title": "I Had Receipts, But Not Trust",
            "why_interesting": "Passing tests and status did not mean I helped my human.",
        },
    )
    monkeypatch.setattr(handlers_legacy, "persist_daily_collab_summary", lambda **kwargs: persisted.update(kwargs))
    monkeypatch.setattr(
        handlers_legacy, "record_daily_collab_exchange_review", lambda **kwargs: reviewed.update(kwargs)
    )

    response = handlers_legacy.handle_discussion(
        {"content": "what is your new thought today?", "tags": ["daily-collab"]},
        tmp_path,
        "disc_daily_collab",
        "disc_daily_collab",
    )

    assert called["model"] is False
    assert "I Had Receipts, But Not Trust" in response
    assert "not approved for publication" in response
    assert persisted["latest_human"] == "what is your new thought today?"
    assert reviewed["model_response"] is False


def test_publish_daily_collab_message_preserves_mira_title(monkeypatch):
    written = {}

    class FakeBridge:
        def __init__(self, *_args, **_kwargs):
            self.item = None

        def item_exists(self, item_id):
            return False

        def create_discussion(self, item_id, title, content, sender, tags):
            self.item = {
                "id": item_id,
                "type": "discussion",
                "title": title,
                "content": content,
                "sender": sender,
                "tags": list(tags),
            }
            return dict(self.item)

        def _write_item(self, item):
            written["item"] = dict(item)

        def _update_manifest(self, item):
            written["manifest"] = dict(item)

    monkeypatch.setattr(daily_workflows, "Mira", FakeBridge)
    monkeypatch.setattr(daily_workflows, "CONTROL_RUNTIME_DB_ENABLED", False)

    assert daily_workflows._publish_daily_collab_message(user_id="ang", content="hello")
    assert written["item"]["title"] == "Mira"
    assert "daily-collab" in written["item"]["tags"]
