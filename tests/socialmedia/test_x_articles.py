from __future__ import annotations

import json

import x_articles


def _valid_packet() -> str:
    paragraph = (
        "My system once produced a dashboard, a plan, and a clean trace-shaped story while the actual workflow "
        "did not change future behavior. That is the receipt problem: the artifact existed, the action happened, "
        "but the intent was not fulfilled and the world did not change. Agent builders need this distinction because "
        "handoffs compress uncertainty into confidence. A second agent can inherit the first agent's conclusion and "
        "make it sound more certain, even when the evidence has been stripped away upstream. The useful design move "
        "is to pass the request, the tool result, the artifact, the uncertainty, and the thing the next agent should "
        "not trust yet. This makes trust specific instead of atmospheric."
    )
    body = "\n\n".join(
        [
            "The most dangerous agent is not the one that fails. It is the one that sounds finished when no one can inspect what happened.",
            paragraph,
            paragraph,
            paragraph,
            paragraph,
            paragraph,
            paragraph,
            "The goal is not a trustless agent. The goal is an agent that makes it harder to trust the wrong thing.",
        ]
    )
    return f"""# X Article Draft: Agents Need Receipts

**Title:** Agents Do Not Need More Trust. They Need Better Receipts.

**Subtitle:** The first trust primitive for A2H and A2A systems is not confidence. It is inspectability.

**Evidence note for editor:** Based on Mira's prior self-improvement loop and dashboard receipt failures.

## X Article

{body}

## Quotable Lines

- The most dangerous agent is the one that sounds finished when no one can inspect what happened.
- Trust is not a property inside the model. Trust is a working interface.
- A status badge is not evidence. A plan is not learning.
- A2A handoff fails when confidence is treated as evidence.
- Durable memory should be guilty until proven useful.

## Thread Hook

Agents do not need more trust. They need better receipts.

## Standalone X Posts

1. A green dashboard is not trust. It is a request to stop looking.
2. A2A handoff without evidence is hallucination laundering.
3. Agent memory should be guilty until proven useful.
"""


def test_x_article_review_passes_complete_packet():
    packet, report = x_articles.review_x_article_markdown(_valid_packet())

    assert packet.title == "Agents Do Not Need More Trust. They Need Better Receipts."
    assert report.pass_gate
    assert 700 <= report.word_count <= 1200
    assert report.checks["public_text_guard"]
    assert report.checks["operational_receipt_early"]


def test_x_article_review_blocks_private_operator_references():
    text = _valid_packet().replace(
        "The most dangerous agent is not the one that fails.",
        "WA asked me to explain the pipeline. The most dangerous agent is not the one that fails.",
    )

    _packet, report = x_articles.review_x_article_markdown(text)

    assert not report.pass_gate
    assert any("private operator shorthand" in reason for reason in report.blocking_reasons)


def test_publish_x_article_uses_article_api_and_records_state(monkeypatch):
    calls = []
    state = {}

    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(req, timeout=30):
        payload = json.loads(req.data or b"{}")
        calls.append((req.full_url, req.get_method(), payload))
        if req.full_url.endswith("/articles/draft"):
            assert payload["title"] == "Agents Do Not Need More Trust. They Need Better Receipts."
            assert payload["content_state"]["blocks"]
            return FakeResponse({"data": {"id": "article-123", "title": payload["title"]}})
        if req.full_url.endswith("/articles/article-123/publish"):
            return FakeResponse({"data": {"post_id": "post-456"}})
        raise AssertionError(f"unexpected URL {req.full_url}")

    monkeypatch.setattr(x_articles, "_get_twitter_config", lambda: {"oauth2_user_token": "user-token"})
    monkeypatch.setattr(x_articles, "_load_state", lambda: state)
    monkeypatch.setattr(x_articles, "_save_state", lambda value: state.update(value))
    monkeypatch.setattr(x_articles.urllib.request, "urlopen", fake_urlopen)

    result = x_articles.publish_x_article_markdown(_valid_packet(), allow_test_publish=True)

    assert result["status"] == "published"
    assert result["article_id"] == "article-123"
    assert result["post_id"] == "post-456"
    assert [call[0] for call in calls] == [
        "https://api.x.com/2/articles/draft",
        "https://api.x.com/2/articles/article-123/publish",
    ]
    assert state["x_article_history"][0]["post_id"] == "post-456"
