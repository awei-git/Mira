"""growth.py spark generator must read journals from config.JOURNAL_DIR,
not the deprecated `agents/shared/soul/journal` path.

The deprecated path is an empty directory left over from a soul-storage
relocation. The real journal lives at data/soul/journal (resolved via
config.SOUL_DIR / "journal"). Pointing at the deprecated path silently
returns zero spark files and the Twitter spark pipeline produces zero
tweets per day forever, with no log signal that anything is wrong.

This test guards against re-introducing the old hard-coded path.
"""

from __future__ import annotations

from pathlib import Path


def test_growth_spark_uses_config_journal_dir():
    growth_path = Path(__file__).resolve().parents[2] / "agents" / "socialmedia" / "growth.py"
    src = growth_path.read_text(encoding="utf-8")

    # Hard-coded deprecated path must not be used for journal lookup
    assert '"shared" / "soul" / "journal"' not in src, (
        "growth.py uses the deprecated agents/shared/soul/journal path. "
        "Use `from config import JOURNAL_DIR` and reference JOURNAL_DIR instead — "
        "data/soul/journal is the real journal location."
    )

    # Spark generator must reference JOURNAL_DIR
    assert "JOURNAL_DIR" in src, (
        "growth.py spark generator must import and use config.JOURNAL_DIR " "for idle_question file lookup."
    )
