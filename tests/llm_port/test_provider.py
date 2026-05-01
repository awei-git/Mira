from __future__ import annotations

import json

from llm_port import LLMMessage, LLMRequest, LLMResponse, get_provider, register_adapter
from llm_port import provider as provider_module
from llm_port.adapters.omlx_adapter import OMLXAdapter, _chat_completions_endpoint


class FakeAdapter:
    name = "fake"

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=request.prompt, provider=self.name, model="fake-model")


def test_llm_provider_registers_model_class_route():
    provider_module._ADAPTERS.clear()
    provider_module._ROUTES["routine"] = "anthropic_oauth"
    register_adapter(FakeAdapter(), model_classes=["routine"])

    provider = get_provider("routine")
    response = provider.complete(LLMRequest(messages=[LLMMessage(role="user", content="hello")]))

    assert response.provider == "fake"
    assert "USER:\nhello" in response.text


def test_local_model_class_routes_to_omlx_adapter():
    provider_module._ADAPTERS.clear()
    provider_module._ROUTES["local"] = "omlx"

    assert get_provider("local").name == "omlx"


def test_omlx_adapter_calls_openai_compatible_chat_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "model": "gemma-4-31b-it-4bit",
                    "choices": [{"message": {"content": "OK"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("llm_port.adapters.omlx_adapter.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("llm_port.adapters.omlx_adapter.OMLX_API_ENDPOINT", "http://127.0.0.1:8800")

    response = OMLXAdapter().complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Reply OK")],
            model_class="local",
            max_tokens=4,
            timeout=5,
        )
    )

    assert captured["url"] == "http://127.0.0.1:8800/v1/chat/completions"
    assert captured["body"]["model"] == "gemma-4-31b-it-4bit"
    assert captured["body"]["messages"] == [{"role": "user", "content": "Reply OK"}]
    assert captured["body"]["max_tokens"] == 4
    assert captured["timeout"] == 5
    assert response.text == "OK"
    assert response.provider == "omlx"
    assert response.model == "gemma-4-31b-it-4bit"
    assert response.input_tokens == 3
    assert response.output_tokens == 1


def test_omlx_endpoint_normalization():
    assert _chat_completions_endpoint("http://127.0.0.1:8800") == "http://127.0.0.1:8800/v1/chat/completions"
    assert _chat_completions_endpoint("http://127.0.0.1:8800/v1") == "http://127.0.0.1:8800/v1/chat/completions"
    assert (
        _chat_completions_endpoint("http://127.0.0.1:8800/v1/chat/completions")
        == "http://127.0.0.1:8800/v1/chat/completions"
    )
