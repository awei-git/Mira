"""Tests for preflight check and artifact verification."""

import sys
import tempfile
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent


def test_publish_preflight_pass():
    from publish.preflight import preflight_check

    result = preflight_check(
        "publish",
        {
            "instruction": "Publish article",
            "title": "Test Article",
            "content": "A" * 300,
            "platform": "substack",
        },
    )
    assert result.passed
    assert result.action_type == "publish"
    assert len(result.blocking_reasons) == 0


def test_publish_preflight_no_title():
    from publish.preflight import preflight_check

    result = preflight_check(
        "publish",
        {
            "instruction": "Publish",
            "title": "",
            "content": "A" * 300,
        },
    )
    assert not result.passed
    assert any("title" in r for r in result.blocking_reasons)


def test_publish_preflight_short_content():
    from publish.preflight import preflight_check

    result = preflight_check(
        "publish",
        {
            "instruction": "Publish",
            "title": "Test",
            "content": "Too short",
        },
    )
    assert not result.passed
    assert any("short" in r for r in result.blocking_reasons)


def test_file_write_preflight_protected():
    from publish.preflight import preflight_check

    result = preflight_check(
        "file_write",
        {
            "instruction": "Write file",
            "path": "/tmp/CLAUDE.md",
            "content": "test",
        },
    )
    assert not result.passed
    assert any("protected" in r for r in result.blocking_reasons)


def test_file_write_preflight_pass():
    from publish.preflight import preflight_check

    result = preflight_check(
        "file_write",
        {
            "instruction": "Write file",
            "path": "/tmp/test_output.txt",
            "content": "hello world",
        },
    )
    assert result.passed


def test_delete_preflight_not_recoverable():
    from publish.preflight import preflight_check

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"data")
        path = f.name
    result = preflight_check(
        "delete",
        {
            "path": path,
            "recoverable": False,
        },
    )
    assert not result.passed
    assert any("recoverable" in r for r in result.blocking_reasons)
    Path(path).unlink(missing_ok=True)


def test_verify_file_exists():
    from publish.preflight import verify_artifact

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("Hello world content here")
        path = f.name
    result = verify_artifact("file", path, {"min_size": 5})
    assert result.verified
    Path(path).unlink(missing_ok=True)


def test_verify_file_missing():
    from publish.preflight import verify_artifact

    result = verify_artifact("file", "/tmp/nonexistent_file_xyz.txt")
    assert not result.verified
    assert any("not exist" in r for r in result.reasons)


def test_verify_file_contains():
    from publish.preflight import verify_artifact

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("The quick brown fox")
        path = f.name
    result = verify_artifact("file", path, {"contains": "brown fox"})
    assert result.verified

    result2 = verify_artifact("file", path, {"contains": "lazy dog"})
    assert not result2.verified
    Path(path).unlink(missing_ok=True)


def test_verify_publish_uses_runtime_config_path(tmp_path, monkeypatch):
    import config
    from publish.preflight import verify_artifact

    pubdir = tmp_path / "_published"
    pubdir.mkdir(parents=True)
    (pubdir / "essay-slug.md").write_text("x" * 300, encoding="utf-8")
    monkeypatch.setattr(config, "WRITINGS_OUTPUT_DIR", tmp_path)

    result = verify_artifact("publish", "essay-slug", {"min_length": 200})
    assert result.verified
