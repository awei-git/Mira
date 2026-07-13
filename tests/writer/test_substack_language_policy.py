from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "lib", ROOT / "agents" / "writer", ROOT / "agents" / "super"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_substack_idea_metadata_forces_english():
    from writing_workflow import _force_substack_english_idea

    idea = """# 叙事即接口

- **type**: essay
- **language**: 中文
- **platform**: Substack

## Notes

中文思考只能作为素材。
"""

    normalized = _force_substack_english_idea(idea)

    assert "- **language**: en" in normalized
    assert "final Substack title, subtitle, section headers, and body must be English" in normalized


def test_substack_analysis_forces_english_language():
    from writing_workflow import _force_substack_english_analysis

    analysis = {"type": "essay", "language": "zh"}
    idea = "- **platform**: Substack\n- **language**: 中文"

    assert _force_substack_english_analysis(analysis, idea)["language"] == "en"


def test_autowrite_language_guard_blocks_cjk_substack_output():
    from workflows.writing import _is_substack_language_violation

    idea = "- **platform**: Substack\n- **language**: en"
    article = "# 叙事即接口\n\nThis body is mostly English, but the title is not."

    assert _is_substack_language_violation(idea, article)


def test_autowrite_uses_english_h1_as_publish_title():
    from workflows.writing import _extract_markdown_h1

    article = "# Narrative Is the Interface\n\nThe body starts here."

    assert _extract_markdown_h1(article) == "Narrative Is the Interface"
