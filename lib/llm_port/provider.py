from __future__ import annotations

from typing import Protocol

from .types import LLMMessage, LLMRequest, LLMResponse, ModelClass


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


_ADAPTERS: dict[str, LLMProvider] = {}
_ROUTES: dict[ModelClass, str] = {
    "routine": "anthropic_oauth",
    "premium": "anthropic_oauth",
    "tool": "anthropic_oauth",
    "local": "omlx",
}


def register_adapter(adapter: LLMProvider, *, model_classes: list[ModelClass] | None = None) -> None:
    _ADAPTERS[adapter.name] = adapter
    for model_class in model_classes or []:
        _ROUTES[model_class] = adapter.name


def get_provider(model_class: ModelClass = "routine") -> LLMProvider:
    _ensure_default_adapters()
    name = _ROUTES.get(model_class, "anthropic_oauth")
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise RuntimeError(f"LLMProvider adapter not registered: {name}") from exc


def complete(
    messages: list[LLMMessage],
    *,
    model_class: ModelClass = "routine",
    max_tokens: int | None = None,
    timeout: int | None = None,
    cwd=None,
    agent_id: str | None = None,
) -> LLMResponse:
    request = LLMRequest(
        messages=messages,
        model_class=model_class,
        max_tokens=max_tokens,
        timeout=timeout,
        cwd=cwd,
        agent_id=agent_id,
    )
    provider = get_provider(model_class)
    try:
        return provider.complete(request)
    except Exception as exc:
        if provider.name != "anthropic_oauth":
            raise
        from auth_health import is_auth_or_quota_failure, record_auth_event

        if not is_auth_or_quota_failure(exc):
            raise
        record_auth_event(
            "anthropic_oauth",
            "oauth_auth_failure",
            status="failed",
            detail=str(exc)[:500],
            payload={"model_class": model_class},
        )
        raise


def _ensure_default_adapters() -> None:
    if _ADAPTERS:
        return
    from .adapters.anthropic_oauth_adapter import AnthropicOAuthAdapter
    from .adapters.omlx_adapter import OMLXAdapter

    register_adapter(AnthropicOAuthAdapter(), model_classes=["routine", "premium", "tool"])
    register_adapter(OMLXAdapter(), model_classes=["local"])
