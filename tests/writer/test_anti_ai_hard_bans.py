from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "lib", ROOT / "agents" / "writer", ROOT / "agents" / "shared"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _flagged_texts(text: str) -> list[str]:
    from handler import scan_anti_ai_patterns

    report = scan_anti_ai_patterns(text)
    return [span["text"] for span in report["flagged_spans"]]


def test_scan_blocks_bushi_and_zheshi_everywhere():
    flagged = _flagged_texts("这是不是问题。这里还有一句：这是一个判断。")

    assert "不是" in flagged
    assert "这是" in flagged


def test_scan_blocks_any_em_dash():
    from handler import scan_anti_ai_patterns

    text = "这个句子用了长破折号 —— 所以必须被拦。"
    strict_flagged = [span["text"] for span in scan_anti_ai_patterns(text)["flagged_spans"]]
    relaxed_flagged = [span["text"] for span in scan_anti_ai_patterns(text, anti_ai_mode="relaxed")["flagged_spans"]]

    assert any("—" in item for item in strict_flagged)
    assert any("—" in item for item in relaxed_flagged)


def test_scan_blocks_lazy_emotional_and_pause_phrases():
    flagged = _flagged_texts("最先打动我的地方让我不舒服。他想了很久，又反复读了一遍。")

    assert "打动" in flagged
    assert "不舒服" in flagged
    assert "反复读" in flagged
    assert any("想了很久" in item for item in flagged)
    assert any("最先打动我" in item for item in flagged)


def test_scan_blocks_generic_tai_intensifier():
    flagged = _flagged_texts("这个判断太顺手了。")

    assert any("太顺手了" in item for item in flagged)
