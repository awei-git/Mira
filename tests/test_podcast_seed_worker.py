"""Task7 local fixtures: no paid providers, cloud changes, TTS or publication."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import re

import pytest

from content_worker import podcast
from content_worker.model_guard import guard_model_call, model_guard
from content_worker.outbox import daily_records
from content_worker.seeds import eligible, generate, policies, run_batch, run_seed, seed_job
from obligations import Conflict, Ledger

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def example(tmp_path):
    seed = next(
        json.loads(line)
        for line in (ROOT / "seeds/seeds.jsonl").read_text().splitlines()
        if json.loads(line).get("kind") == "podcast_script"
    )
    for path in podcast.POLICY_FILES:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    (tmp_path / "seeds").mkdir()
    (tmp_path / "seeds/seeds.jsonl").write_text(json.dumps(seed))
    ledger = Ledger(tmp_path / "state/ledger.sqlite3", tmp_path)
    spec = podcast.constraints(seed, policies(tmp_path, seed))
    return tmp_path, seed, ledger, spec


def fixture_script(spec):
    # Deliberately repetitive fixture only. Passing mechanical constraints does
    # not make this publishable content or count as a real draft.
    body = spec["opening"] + "\n\n" + "，".join(spec["required_terms"]) + "。\n\n" + spec["closing"] + "。"
    count = len(re.findall(r"[\u3400-\u9fff]", body))
    return "# 测试用独白\n\n" + body.replace("\n\n", "\n\n" + "字" * (1400 - count), 1)


def test_only_ready_podcast_chinese_seed_is_eligible(example):
    _, seed, _, _ = example
    assert eligible(seed)
    assert not eligible({**seed, "status": "candidate"})
    assert not eligible({**seed, "kind": "essay"})
    assert not eligible({**seed, "track": "fr"})


def test_actual_episode_constraints_and_mechanical_failures(example):
    _, _, _, spec = example
    text = fixture_script(spec)
    assert podcast.inspect(text, spec)["pass_gate"]
    for broken in (
        text.replace("temperature", ""),
        text.replace(spec["opening"], "你好。"),
        text.replace(spec["closing"], "谢谢收听"),
        text + "\nAng: hello",
        "# 短稿\n\n太短了",
    ):
        assert not podcast.inspect(broken, spec)["pass_gate"]


def test_unconfigured_episode_fails_before_any_obligation_or_writer(example):
    repo, seed, ledger, _ = example
    with pytest.raises(ValueError, match="reviewed_constraints"):
        run_seed(repo, {**seed, "seed_id": "not-approved"}, ledger=ledger)
    assert ledger.listing()["items"] == []


def test_chinese_draft_uses_same_event_and_is_not_repeated(example):
    repo, seed, ledger, spec = example
    calls = []

    def writer(workspace, *_):
        calls.append(1)
        (workspace / "output.md").write_text(fixture_script(spec))
        return "fixture"

    result = run_seed(repo, seed, ledger=ledger, writer=writer)
    assert result["status"] == "draft_ready_for_signoff"
    assert result["draft_path"].startswith("data/drafts/zh/")
    assert result["published"] is False
    event = next(e for e in ledger.events()["items"] if e["kind"] == "draft.ready_for_signoff")
    assert event["payload"]["track"] == "zh" and event["payload"]["kind"] == "podcast_script"
    assert ledger.artifact(event["payload"]["artifact_id"])[0].decode() == fixture_script(spec)
    assert run_batch(repo, [seed], ledger=ledger, writer=writer) == [] and len(calls) == 1
    from zoneinfo import ZoneInfo

    day = datetime.fromisoformat(result["finished_at"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    assert seed["seed_id"] in daily_records(repo, day)["journal"][0]["text"]
    packet_path = repo / result["packet_path"]
    packet = json.loads(packet_path.read_text())
    packet["track"] = "substack_en"
    packet_path.write_text(json.dumps(packet))
    job = ledger.get(result["obligation_id"])
    with pytest.raises(Conflict, match="track_or_kind"):
        ledger.complete_draft(job["id"], "mira-aws", job["revision"])


def test_chinese_blocked_script_cannot_emit_ready_event(example):
    repo, seed, ledger, _ = example

    def writer(workspace, *_):
        (workspace / "output.md").write_text("# Wrong language\n\nAn English essay.")
        return "fixture"

    result = run_seed(repo, seed, ledger=ledger, writer=writer)
    assert result["status"] == "editorial_blocked"
    assert not any(e["kind"] == "draft.ready_for_signoff" for e in ledger.events()["items"])


def test_configured_model_route_applies_in_every_writer_thread(example):
    repo, seed, ledger, _ = example
    job = seed_job(repo, seed, ledger)
    job = ledger.claim(job["id"], "mira-aws", job["revision"])
    job = ledger.transition(job["id"], "mira-aws", job["revision"], "running")
    calls = []

    @guard_model_call
    def dispatch(route, *args):
        calls.append(route)
        return "fixture response"

    with model_guard(ledger, job, repo, route="configured-api"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda n: dispatch("legacy-request", str(n), "", 1), range(2)))
    assert calls == ["configured-api", "configured-api"]
    dispatch("legacy-request", "outside guard", "", 1)
    assert calls[-1] == "legacy-request"


def test_generation_uses_existing_handler_with_explicit_chinese_metadata(example, monkeypatch):
    import pathsetup  # noqa: F401
    from agents.writer import handler

    repo, seed, _, _ = example
    seen = {}

    def capture(*args, **kwargs):
        seen.update(content=args[2], **kwargs)
        return "fixture"

    monkeypatch.setattr(handler, "handle", capture)
    assert generate(repo, seed, policies(repo, seed), []) == "fixture"
    assert seen["content_only"] is True
    assert seen["metadata"]["source_genre"] == "podcast_script"
    assert "输出语言：简体中文" in seen["content"]
    assert seed["brief"] in seen["content"]
    assert "Write an English Substack draft" not in seen["content"]


def test_real_dispatcher_uses_configured_api_route_not_cli(example, monkeypatch):
    import llm

    repo, seed, ledger, _ = example
    job = seed_job(repo, seed, ledger)
    job = ledger.claim(job["id"], "mira-aws", job["revision"])
    calls = []
    monkeypatch.setattr(llm, "_api_call", lambda provider, model, *a, **k: calls.append((provider, model)) or "fixture")
    monkeypatch.setattr(llm, "codex_think", lambda *a, **k: pytest.fail("CLI route was used"))
    with model_guard(ledger, job, repo, route="gpt"):
        assert llm.model_think("中文独白稿", model_name="codex") == "fixture"
    assert calls == [(llm.MODELS["gpt"]["provider"], llm.MODELS["gpt"]["model_id"])]
    with pytest.raises(RuntimeError, match="configured_model_route"):
        with model_guard(ledger, job, repo, route=""):
            pytest.fail("empty route accepted")


def test_explicit_podcast_language_survives_substack_in_source_notes(tmp_path, monkeypatch):
    import pathsetup  # noqa: F401
    import writing_workflow as workflow
    from memory import soul

    captured = {}
    monkeypatch.setattr(workflow, "_analyze", lambda _: {"type": "essay", "language": "en"})

    def plan(context, analysis, idea, directory):
        captured.update(analysis=analysis, idea=idea)
        return "fixture plan"

    monkeypatch.setattr(workflow, "_plan", plan)
    monkeypatch.setattr(workflow, "_write_drafts", lambda *a: {"fixture": "中文稿" * 1000})
    monkeypatch.setattr(workflow, "_review_cycle", lambda *a: "# 中文标题\n\n中文独白。")
    monkeypatch.setattr(soul, "catalog_add", lambda *a: None)
    workflow.run_full_pipeline(
        "测试",
        "The old line is Substack; 这次中文播客。",
        content_only=True,
        persona_prompt="content-only fixture",
        workspace=tmp_path,
        output_language="zh",
    )
    assert captured["analysis"]["language"] == "zh"
    assert "final Substack title" not in captured["idea"]
    with pytest.raises(ValueError, match="explicit_language"):
        workflow.run_full_pipeline("test", "test", output_language="zh")


def test_podcast_disclosure_is_retained_outside_spoken_script(tmp_path, monkeypatch):
    import pathsetup  # noqa: F401
    from agents.writer import handler
    from content_worker.context import ContentContext, ContentPersona

    final = "# 中文标题\n\n这里是独白。\n\n我是米拉，下期见。"
    project = tmp_path / "pipeline"
    project.mkdir()
    (project / "final.md").write_text(final)

    def pipeline(*args, **kwargs):
        assert kwargs["output_language"] == "zh" and kwargs["content_only"]
        return project, final

    monkeypatch.setattr(handler, "run_full_pipeline", pipeline)
    monkeypatch.setattr(handler, "_apply_obsession_constraints_gate", lambda *a, **k: False)
    monkeypatch.setattr(handler, "minimal_voice_preserving_editorial_pass", lambda text, **k: text)
    monkeypatch.setattr(handler, "_assess_obsession_gap", lambda text, **k: k["metadata"])
    monkeypatch.setattr(handler, "_obsession_gap_check", lambda *a: False)
    monkeypatch.setattr(handler, "audit_pass", lambda *a, **k: {})
    monkeypatch.setattr(handler, "_generated_content_preflight", lambda *a: (True, ""))
    monkeypatch.setattr(handler, "_ceiling_handoff", lambda *a, **k: {})
    monkeypatch.setattr(handler, "_write_ceiling_note_result", lambda *a, **k: None)
    monkeypatch.setattr(handler, "record_writer_gate", lambda *a, **k: None)
    monkeypatch.setattr(handler, "claude_think", lambda *a, **k: pytest.fail("unexpected provider call"))
    result = handler._handle_full_write(
        tmp_path,
        "fixture",
        "中文独白",
        "标题",
        ContentContext(ContentPersona("fixture")),
        content_only=True,
        voice_preserving=True,
        metadata={"source_genre": "podcast_script"},
    )
    assert result
    assert (tmp_path / "output.md").read_text() == final
    assert "Judgment Disclosure" in (tmp_path / "editorial-disclosure.md").read_text()


def test_writer_failure_receipt_keeps_candidate_without_signoff_or_rerun(example):
    from content_worker.failure import retain_candidate

    repo, seed, ledger, _ = example
    calls = []

    def writer(workspace, *_):
        calls.append(1)
        retain_candidate(workspace, "Blocked candidate", {"violations": [{"trigger": "em_dash_overuse"}]})
        return None

    result = run_seed(repo, seed, ledger=ledger, writer=writer)
    assert result["status"] == "blocked"
    assert result["writer_failure"]["code"] == "writer_obsession_constraints"
    assert result["writer_failure"]["approved"] is False
    assert "draft_path" not in result
    assert not any(e["kind"] == "draft.ready_for_signoff" for e in ledger.events()["items"])
    assert run_batch(repo, [seed], ledger=ledger, writer=writer) == []
    assert calls == [1]
