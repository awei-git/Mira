import llm
from llm_providers import codex
from llm_providers import openai_compat


def test_default_route_prefers_codex_subscription_then_fallbacks():
    assert llm._fallback_chain("codex", "Summarize this market note.") == [
        "codex",
        "claude",
        "deepseek",
        "omlx",
    ]


def test_chinese_writing_routes_to_deepseek_before_gpt():
    prompt = "请帮我写一篇中文文章，要求标题有吸引力，摘要自然，段落不要太干。"
    assert llm._fallback_chain("gpt5", prompt)[:2] == ["deepseek", "codex"]


def test_deepseek_first_routes_do_not_die_when_deepseek_is_down():
    assert llm._fallback_chain("deepseek", "写一个短的每日哲思") == [
        "deepseek",
        "codex",
        "claude",
        "omlx",
    ]


def test_local_policy_overrides_cloud_fallback_chain():
    llm.set_model_policy("omlx")
    try:
        assert llm._fallback_chain("codex", "anything") == ["omlx"]
    finally:
        llm.set_model_policy(None)


def test_codex_provider_uses_current_exec_flags(monkeypatch, tmp_path):
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out_path = cmd[cmd.index("--output-last-message") + 1]
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        return Result()

    monkeypatch.setattr(codex.subprocess, "run", fake_run)

    assert codex.codex_think("say ok", model_id="gpt-5.5", timeout=5) == "ok"
    assert "-a" not in calls[0]
    assert "--full-auto" not in calls[0]

    assert codex.codex_act("say ok", cwd=tmp_path, model_id="gpt-5.5", timeout=5) == "ok"
    assert "-a" not in calls[1]
    assert "--full-auto" not in calls[1]


def test_codex_provider_rejects_cli_error_transcript(monkeypatch):
    class Result:
        returncode = 0
        stderr = ""
        stdout = "ERROR: unexpected status 400 Bad Request"

    monkeypatch.setattr(codex.subprocess, "run", lambda *args, **kwargs: Result())

    assert codex.codex_think("say ok", model_id="gpt-5.5", timeout=5) == ""


def test_codex_provider_allows_explicit_binary_override(monkeypatch):
    monkeypatch.setenv("MIRA_CODEX_BIN", "/custom/codex")
    assert codex._codex_bin() == "/custom/codex"


def test_api_provider_circuit_persists_between_processes(monkeypatch, tmp_path):
    circuit = tmp_path / "api_provider_circuit.json"
    monkeypatch.setattr(openai_compat, "_PROVIDER_CIRCUIT_FILE", circuit)

    openai_compat._open_provider_circuit("deepseek", reason="HTTP 402", hours=6)

    assert openai_compat._provider_circuit_open("deepseek") is True


def test_api_provider_circuit_records_readable_http_error_reason():
    body = '{"error":{"message":"Insufficient Balance","type":"unknown_error"}}'

    assert openai_compat._http_error_reason(body, default="HTTP 402") == "Insufficient Balance"
