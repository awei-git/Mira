import llm
import io
import json
import pytest
import urllib.error
from llm_providers import claude as claude_provider
from llm_providers import codex
from llm_providers import openai_compat


@pytest.fixture(autouse=True)
def _isolated_codex_circuit(monkeypatch, tmp_path):
    monkeypatch.setattr(codex, "_CODEX_CIRCUIT_FILE", tmp_path / "api_provider_circuit.json")


def test_default_route_prefers_codex_subscription_then_fallbacks():
    assert llm._fallback_chain("codex", "Summarize this market note.") == [
        "codex",
        "claude",
        "gpt",
        "omlx",
    ]


def test_daemon_route_keeps_codex_cli_by_default(monkeypatch):
    monkeypatch.setenv("MIRA_BACKGROUND_DAEMON", "1")

    assert llm._fallback_chain("codex", "Summarize this market note.") == [
        "codex",
        "claude",
        "gpt",
        "omlx",
    ]
    assert llm._fallback_chain("deepseek", "写一个短的每日哲思") == [
        "codex",
        "gpt",
        "claude",
        "omlx",
    ]


def test_runtime_policy_can_disable_codex_cli(monkeypatch):
    monkeypatch.setenv("MIRA_DISABLE_CODEX_CLI", "1")

    assert llm._fallback_chain("codex", "Summarize this market note.") == [
        "claude",
        "gpt",
        "omlx",
    ]


def test_runtime_policy_skips_codex_cli_when_circuit_open(monkeypatch):
    monkeypatch.setattr(codex, "codex_circuit_open", lambda: True)

    assert llm._fallback_chain("codex", "Summarize this market note.") == [
        "claude",
        "gpt",
        "omlx",
    ]


def test_chinese_writing_routes_to_gpt_before_claude():
    prompt = "请帮我写一篇中文文章，要求标题有吸引力，摘要自然，段落不要太干。"
    assert llm._fallback_chain("gpt5", prompt)[:3] == ["codex", "gpt", "claude"]


def test_deepseek_route_uses_gpt_instead():
    assert llm._fallback_chain("deepseek", "写一个短的每日哲思") == [
        "codex",
        "gpt",
        "claude",
        "omlx",
    ]


def test_dashboard_model_ids_route_to_runtime_backends():
    assert llm._fallback_chain("claude-sonnet-4-6", "Summarize this.")[:3] == ["claude", "codex", "gpt"]
    assert llm._fallback_chain("deepseek-v4-pro", "Summarize this.")[:2] == ["codex", "gpt"]
    assert llm._fallback_chain("gpt-5.5", "Summarize this.")[:2] == ["codex", "claude"]


def test_local_policy_overrides_cloud_fallback_chain():
    llm.set_model_policy("omlx")
    try:
        assert llm._fallback_chain("codex", "anything") == ["omlx"]
    finally:
        llm.set_model_policy(None)


def test_legacy_local_model_request_uses_codex_subscription_by_default():
    assert llm._fallback_chain("omlx", "Summarize this routine note.") == [
        "codex",
        "claude",
        "gpt",
        "omlx",
    ]


def test_codex_provider_uses_current_exec_flags(monkeypatch, tmp_path):
    calls = []
    inputs = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        inputs.append(kwargs.get("input"))
        out_path = cmd[cmd.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        return Result()

    monkeypatch.setattr(codex.subprocess, "run", fake_run)

    assert codex.codex_think("say ok", model_id="gpt-5.5", timeout=5) == "ok"
    assert "-a" not in calls[0]
    assert "--full-auto" not in calls[0]
    assert "--ephemeral" in calls[0]
    assert calls[0][-1] == "-"
    assert inputs[0] == "say ok"

    assert codex.codex_act("say ok", cwd=tmp_path, model_id="gpt-5.5", timeout=5) == "ok"
    assert "-a" not in calls[1]
    assert "--full-auto" not in calls[1]
    assert "--ephemeral" in calls[1]
    assert calls[1][-1] == "-"
    assert inputs[1] == "say ok"


def test_codex_provider_rejects_cli_error_transcript(monkeypatch):
    class Result:
        returncode = 0
        stderr = ""
        stdout = "ERROR: unexpected status 400 Bad Request"

    monkeypatch.setattr(codex.subprocess, "run", lambda *args, **kwargs: Result())

    assert codex.codex_think("say ok", model_id="gpt-5.5", timeout=5) == ""


def test_codex_provider_circuits_quota_without_logging_prompt(monkeypatch, tmp_path, caplog):
    circuit = tmp_path / "api_provider_circuit.json"
    monkeypatch.setattr(codex, "_CODEX_CIRCUIT_FILE", circuit)

    class Result:
        returncode = 1
        stderr = (
            "OpenAI Codex v0.142.4\n"
            "Reason: You've hit your usage limit. Try again later.\n"
            "--------\n"
            "user\n"
            "private prompt that must not appear"
        )
        stdout = ""

    monkeypatch.setattr(codex.subprocess, "run", lambda *args, **kwargs: Result())

    with caplog.at_level("ERROR", logger="mira"):
        assert codex.codex_think("private prompt that must not appear", model_id="gpt-5.5", timeout=5) == ""

    data = json.loads(circuit.read_text(encoding="utf-8"))
    assert data["codex_cli"]["reason"].startswith("OpenAI Codex")
    assert codex.codex_circuit_open() is True
    assert "private prompt" not in caplog.text


def test_codex_provider_allows_explicit_binary_override(monkeypatch):
    monkeypatch.setenv("MIRA_CODEX_BIN", "/custom/codex")
    assert codex._codex_bin() == "/custom/codex"


def test_claude_fallback_uses_codex_subscription(monkeypatch):
    calls = []

    def fake_codex_think(prompt, model_id="", system="", timeout=300):
        calls.append((prompt, model_id, system, timeout))
        return "ok"

    monkeypatch.setattr(claude_provider, "CLAUDE_FALLBACK_MODEL", "codex")
    monkeypatch.setattr(codex, "codex_think", fake_codex_think)

    assert claude_provider._fallback_think("prompt", timeout=7, tier="light") == "ok"
    assert calls == [("prompt", claude_provider.MODELS["codex"]["model_id"], "", 7)]


def test_claude_fallback_respects_codex_circuit(monkeypatch):
    monkeypatch.setattr(claude_provider, "CLAUDE_FALLBACK_MODEL", "codex")
    monkeypatch.setattr(codex, "codex_circuit_open", lambda: True)
    monkeypatch.setattr(
        codex,
        "codex_think",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex should be skipped")),
    )

    assert claude_provider._fallback_think("prompt", timeout=7, tier="light") == ""


def test_api_provider_circuit_persists_between_processes(monkeypatch, tmp_path):
    circuit = tmp_path / "api_provider_circuit.json"
    monkeypatch.setattr(openai_compat, "_PROVIDER_CIRCUIT_FILE", circuit)

    openai_compat._open_provider_circuit("deepseek", reason="HTTP 402", hours=6)

    assert openai_compat._provider_circuit_open("deepseek") is True


def test_api_provider_circuit_records_readable_http_error_reason():
    body = '{"error":{"message":"Insufficient Balance","type":"unknown_error"}}'

    assert openai_compat._http_error_reason(body, default="HTTP 402") == "Insufficient Balance"


def test_api_provider_circuit_records_openai_quota_from_api_call(monkeypatch, tmp_path):
    circuit = tmp_path / "api_provider_circuit.json"
    monkeypatch.setattr(openai_compat, "_PROVIDER_CIRCUIT_FILE", circuit)
    monkeypatch.setattr(openai_compat, "_probed_providers", {"openai": True})
    monkeypatch.setattr(llm, "_get_api_key", lambda provider: "test-key")
    monkeypatch.setattr(llm, "redact_secrets", lambda text: text)

    body = b'{"error":{"message":"You exceeded your current quota","type":"insufficient_quota"}}'

    def fake_urlopen(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url="https://api.openai.test",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr(openai_compat.urllib.request, "urlopen", fake_urlopen)

    assert openai_compat._api_call("openai", "gpt-5.5", "hello", timeout=1) == ""
    data = openai_compat._load_provider_circuit()
    assert data["openai"]["reason"] == "You exceeded your current quota"
    assert openai_compat._provider_circuit_open("openai") is True
