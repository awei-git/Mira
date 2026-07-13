import pytest


def test_scope_guard_maps_writing_alias_to_writer(monkeypatch):
    import sub_agent

    monkeypatch.setattr(
        sub_agent,
        "_configured_agent_action_scope",
        lambda: {"writer": ["file_write"]},
    )

    assert sub_agent.enforce_scope("file_write", "writing") is True


def test_scope_guard_maps_dotted_agent_to_configured_root(monkeypatch):
    import sub_agent

    monkeypatch.setattr(
        sub_agent,
        "_configured_agent_action_scope",
        lambda: {"socialmedia": ["file_write", "network_call"]},
    )

    assert sub_agent.enforce_scope("file_write", "socialmedia.notes") is True


def test_scope_guard_still_blocks_unknown_agent(monkeypatch):
    import sub_agent

    monkeypatch.setattr(
        sub_agent,
        "_configured_agent_action_scope",
        lambda: {"writer": ["file_write"]},
    )

    with pytest.raises(sub_agent.ScopeEscalationError):
        sub_agent.enforce_scope("file_write", "unknown_agent")
