from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path


def _load_daily_book_review():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "daily_book_review_for_test",
        root / "agents" / "reader" / "daily_book_review.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_export_week_epub_writes_to_icloud_books_mira(monkeypatch, tmp_path):
    mod = _load_daily_book_review()
    monkeypatch.setattr(mod, "ICLOUD_MIRA_BOOKS", tmp_path / "Books" / "Mira")
    monkeypatch.setattr(
        mod,
        "model_think",
        lambda prompt, model_name="", timeout=0: "翻译后的纯中文段落",
    )

    epub_path = mod.export_week_epub(
        title="读书不是把作者供起来",
        subtitle="本周精读：Test Book。七天，七个角度。",
        body="# 开头\n\n正文段落。\n\n## Day 1\n\n更多内容。Carlo Rovelli 写了 The Order of Time。",
        book={"title": "Test Book", "author": "A. Writer"},
        state={"week_id": "2026-W19"},
        series_dir=tmp_path / "series",
    )

    assert epub_path.parent == tmp_path / "Books" / "Mira"
    assert epub_path.name == "2026-W19-读书不是把作者供起来.epub"
    assert epub_path.exists()

    with zipfile.ZipFile(epub_path) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/chapter.xhtml" in names
        chapter = zf.read("OEBPS/chapter.xhtml").decode("utf-8")

    assert "读书不是把作者供起来" in chapter
    assert "正文段落" in chapter
    assert "卡洛·罗韦利" in chapter
    assert "时间的秩序" in chapter
    assert "Test Book" not in chapter
    assert "Carlo Rovelli" not in chapter
    assert "The Order of Time" not in chapter


def test_write_daily_report_runs_second_pass_refinement(monkeypatch, tmp_path):
    mod = _load_daily_book_review()
    raw = "# 初稿\n\n总而言之，这本书深刻揭示了一个问题。" * 80
    refined = "# 修订稿\n\n这本书真正麻烦的地方在第三章的论证缝隙里。" * 80
    monkeypatch.setattr(mod, "_load_mira_voice", lambda: "Mira voice")
    monkeypatch.setattr(mod, "_load_previous_reports", lambda series_dir, day: "")
    monkeypatch.setattr(mod, "model_think", lambda prompt, model_name="", timeout=0: raw)
    monkeypatch.setattr(mod, "claude_think", lambda prompt, timeout=0, tier="": refined)

    report = mod.write_daily_report(
        {"title": "Test Book", "author": "A. Writer"},
        "book text",
        1,
        tmp_path,
    )

    assert report == refined


def test_write_daily_report_rejects_too_short_refinement(monkeypatch, tmp_path):
    mod = _load_daily_book_review()
    raw = "# 初稿\n\n这是一个足够长的报告。" * 120
    monkeypatch.setattr(mod, "_load_mira_voice", lambda: "Mira voice")
    monkeypatch.setattr(mod, "_load_previous_reports", lambda series_dir, day: "")
    monkeypatch.setattr(mod, "model_think", lambda prompt, model_name="", timeout=0: raw)
    monkeypatch.setattr(mod, "claude_think", lambda prompt, timeout=0, tier="": "太短")

    report = mod.write_daily_report(
        {"title": "Test Book", "author": "A. Writer"},
        "book text",
        1,
        tmp_path,
    )

    assert report == raw
