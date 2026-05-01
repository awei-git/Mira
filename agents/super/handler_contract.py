"""Canonical contract every agent's `handle()` function must satisfy.

The dispatcher (`task_support._invoke_registry_handler`) introspects each
handler's signature and only passes kwargs the handler accepts. That makes
the system *forgiving* but also masks contract drift — a handler that
silently ignores `tier` looks identical to one that respects it. The
fallback path (`_safe_general_fallback`) used to side-step introspection
entirely, which is why a missing `tier` kwarg on `analyst.handle` blew up
the open-market task on 2026-04-29.

This module pins the contract in one place:

* `REQUIRED_POSITIONAL` — the 5 positionals every handler must accept.
* `RUNTIME_KWARGS`     — the optional kwargs the dispatcher may inject.
* `validate_handler()` — inspect a handler and return any contract gaps.

`agent_registry.load_handler()` calls `validate_handler` on every load and
warns on violations so drift is loud, not silent.
"""

from __future__ import annotations

import inspect
import logging
from typing import Callable

log = logging.getLogger("mira.handler_contract")

# Positionals. The dispatcher calls handlers positionally, so the *names*
# below are conventional — what matters is arity. (photo/video name the
# 3rd positional `instruction` instead of `content` and that's fine.)
REQUIRED_POSITIONAL: tuple[str, ...] = (
    "workspace",
    "task_id",
    "content",
    "sender",
    "thread_id",
)

# Optional kwargs the dispatcher injects when the handler accepts them
# (either by name or via **kwargs).
RUNTIME_KWARGS: tuple[str, ...] = (
    "thread_history",
    "thread_memory",
    "tier",
    "agent_id",
    "user_id",
)


def validate_handler(handler_fn: Callable, name: str = "") -> list[str]:
    """Return a list of contract violations for `handler_fn`.

    Empty list = handler conforms. The dispatcher will not refuse to call
    a non-conforming handler (we still want degraded service over a hard
    crash on load), but each violation is logged at WARNING so drift is
    visible.
    """
    problems: list[str] = []
    try:
        sig = inspect.signature(handler_fn)
    except (TypeError, ValueError) as exc:
        return [f"cannot introspect {name or handler_fn!r}: {exc}"]

    params = list(sig.parameters.values())
    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    positionals = [p for p in params if p.kind in positional_kinds]
    accepts_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    if len(positionals) < len(REQUIRED_POSITIONAL):
        problems.append(
            f"expects {len(positionals)} positionals, contract requires "
            f"{len(REQUIRED_POSITIONAL)}: {REQUIRED_POSITIONAL}"
        )

    # If the handler does not accept **kwargs, every RUNTIME_KWARGS name
    # the dispatcher might inject must be an explicit parameter. Missing
    # entries mean the dispatcher will silently skip that runtime context
    # for this agent — which is recoverable but worth flagging.
    if not accepts_var_kwargs:
        explicit = {p.name for p in params}
        missing = [k for k in RUNTIME_KWARGS if k not in explicit]
        if missing:
            problems.append(
                f"no **kwargs and missing optional kwargs {missing} — "
                "handler will silently drop these runtime context fields"
            )

    return problems


def warn_on_violations(handler_fn: Callable, name: str) -> None:
    """Log contract violations for `handler_fn` at WARNING level."""
    for problem in validate_handler(handler_fn, name=name):
        log.warning("handler contract: %s — %s", name, problem)
