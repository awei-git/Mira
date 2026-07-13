from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))


def test_sycophancy_assessment_accepts_set_tokens(monkeypatch):
    import soul_manager

    monkeypatch.setattr(
        soul_manager,
        "_sycophancy_tokens",
        lambda _text: {"alpha", "beta", "gamma", "delta"},
    )

    result = soul_manager.assess_sycophancy_resistance(
        [{"framing": "alpha beta gamma delta", "output": "alpha beta gamma delta"}]
    )

    assert result["sample_count"] == 1
