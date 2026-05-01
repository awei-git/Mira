from __future__ import annotations

import json

from llm_port import LLMMessage, LLMRequest, LLMResponse, complete, register_adapter
from llm_port import provider as provider_module


class FailingOAuth:
    name = "anthropic_oauth"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise RuntimeError("rate limit exceeded")


class FakeAPI:
    name = "anthropic_api"

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="fallback ok", provider=self.name, model="claude-fallback", raw={"ok": True})


def test_anthropic_oauth_auth_failure_falls_back_to_api(monkeypatch, tmp_path):
    import auth_health
    import config

    provider_module._ADAPTERS.clear()
    provider_module._ROUTES["routine"] = "anthropic_oauth"
    register_adapter(FailingOAuth(), model_classes=["routine"])
    register_adapter(FakeAPI())
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    response = complete([LLMMessage(role="user", content="hello")], model_class="routine")

    assert response.provider == "anthropic_api"
    assert response.text == "fallback ok"
    assert response.raw["fallback_from"] == "anthropic_oauth"
    events = (tmp_path / "auth_state" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(events[0])["event"] == "oauth_throttle_fallback"
    assert json.loads(events[0])["payload"]["fallback"] == "anthropic_api"


def test_non_auth_oauth_failure_does_not_fallback(monkeypatch, tmp_path):
    import config

    class BrokenOAuth:
        name = "anthropic_oauth"

        def complete(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("unexpected parser bug")

    provider_module._ADAPTERS.clear()
    provider_module._ROUTES["routine"] = "anthropic_oauth"
    register_adapter(BrokenOAuth(), model_classes=["routine"])
    register_adapter(FakeAPI())
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    try:
        complete([LLMMessage(role="user", content="hello")], model_class="routine")
    except RuntimeError as exc:
        assert "parser bug" in str(exc)
    else:
        raise AssertionError("non-auth failure should not fallback")
