from __future__ import annotations

import pytest

from public_text_guard import PublicTextLeakError, redact_public_text, validate_public_text


@pytest.mark.parametrize(
    "text",
    [
        "WA asked me to explain the pipeline.",
        "My human asked me to explain the pipeline.",
        "I checked file:///Users/example/Sandbox/Mira/docs/v3.1-architecture.html.",
        "The screenshot came from /private/var/folders/tmp/capture.png.",
        "Open localhost:8384 to see the dashboard.",
        "Email operator@example.com for the artifact.",
    ],
)
def test_validate_public_text_blocks_private_details(text):
    with pytest.raises(PublicTextLeakError):
        validate_public_text(text, surface="test")


def test_redact_public_text_removes_private_details_from_drafts():
    raw = (
        "The operator asked me to inspect file:///Users/example/Sandbox/Mira/docs/v3.1-architecture.html "
        "on localhost:8384 and email operator@example.com."
    )

    redacted = redact_public_text(raw)

    assert "someone" in redacted
    assert "my human" not in redacted
    assert "[private path]" in redacted
    assert "[private endpoint]" in redacted
    assert "[private email]" in redacted
    assert "operator" not in redacted.lower()
    assert validate_public_text(redacted, surface="test") == redacted


def test_validate_public_text_allows_public_lab_evidence():
    text = (
        "I found a dashboard saying success when the podcast artifact did not exist. "
        "I changed the rule: every green row needs evidence a reader can inspect."
    )

    assert validate_public_text(text, surface="test") == text
