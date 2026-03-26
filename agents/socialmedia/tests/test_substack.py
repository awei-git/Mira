"""Substack tests — verify publishing pipeline doesn't truncate or skip."""
from __future__ import annotations
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "socialmedia"))
sys.path.insert(0, str(_AGENTS / "shared"))


def test_md_to_html_no_truncation():
    """_md_to_html must not truncate long articles (was [:8000] bug)."""
    from substack import _md_to_html
    import inspect
    source = inspect.getsource(_md_to_html)
    assert "[:8000]" not in source, "CRITICAL: _md_to_html still has [:8000] truncation!"
    assert "[:5000]" not in source, "_md_to_html has suspicious truncation"


def test_prosemirror_handles_hr():
    """_html_to_prosemirror should handle <hr> tags (section dividers)."""
    from substack import _html_to_prosemirror
    html = "<p>Paragraph one.</p>\n<hr/>\n<p>Paragraph two.</p>"
    doc = _html_to_prosemirror(html)
    types = [n["type"] for n in doc["content"]]
    assert "horizontal_rule" in types, f"Missing horizontal_rule in {types}"
    assert types.count("paragraph") == 2, f"Expected 2 paragraphs, got {types}"


def test_publish_overwrites():
    """_copy_mp3_to_repo should always overwrite, not skip existing."""
    from substack import _md_to_html
    import inspect
    # Check rss.py _copy_mp3_to_repo doesn't have "if not dest.exists()" skip
    rss_path = _AGENTS / "podcast" / "rss.py"
    if rss_path.exists():
        rss_source = rss_path.read_text()
        assert "if not dest.exists()" not in rss_source, \
            "rss.py still skips existing files — should always overwrite"
