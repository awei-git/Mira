"""Exercise immutable-source packaging against real disposable git repositories."""

import importlib.util
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "deploy"))
    spec = importlib.util.spec_from_file_location("local_bundle", ROOT / "deploy/local_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()

    git("init")
    for name, text in {
        "deploy/projects.yaml": "projects:\n  worker:\n    repo: test/repo\n    data_dirs: [data, secrets.yml]\n    package_excludes: [identity, skills]\n  bridge:\n    repo: test/repo\n    subdir: bridge\n    data_dirs: [.token]\n    extra_files:\n      lib/obligations.py: obligations.py\n",
        "data/private.txt": "private fixture",
        "identity/USER.md": "private fixture",
        "skills/unapproved.md": "unapproved fixture",
        "secrets.yml": "private fixture",
        "bridge/.token": "fixture credential",
        "bridge/app.py": "release app",
        "lib/obligations.py": "release ledger",
    }.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (repo / "bridge/link").symlink_to(".token")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD")
    git("tag", "release")
    return module, repo, commit, tmp_path


def test_dirty_worktree_cannot_change_release_and_bundles_repeat(bundle):
    module, repo, commit, tmp = bundle
    (repo / "lib/obligations.py").write_text("uncommitted replacement")
    (repo / "deploy/projects.yaml").write_text("broken config")
    first = module.build(repo, "bridge", commit, "release", tmp / "first")
    second = module.build(repo, "bridge", commit, "release", tmp / "second")
    assert first == second
    assert (tmp / "first/bundle.tar.gz").read_bytes() == (tmp / "second/bundle.tar.gz").read_bytes()
    with tarfile.open(tmp / "first/bundle.tar.gz") as archive:
        assert sorted(archive.getnames()) == ["app.py", "obligations.py"]
        assert archive.extractfile("obligations.py").read() == b"release ledger"
    assert not first["uploaded"] and not first["deployed"] and not first["skills_approved"]


def test_private_runtime_and_identity_excluded_before_archive(bundle):
    module, repo, commit, tmp = bundle
    receipt = module.build(repo, "worker", commit, "release", tmp / "worker")
    assert all(not name.startswith(("data/", "identity/", "skills/", "secrets.yml")) for name in receipt["files"])


def test_wrong_commit_and_existing_output_fail_closed(bundle):
    module, repo, commit, tmp = bundle
    with pytest.raises(ValueError, match="release_commit_mismatch"):
        module.build(repo, "bridge", "a" * 40, "release", tmp / "wrong")
    assert not (tmp / "wrong").exists()
    with pytest.raises(FileExistsError):
        module.build(repo, "bridge", commit, "release", repo)
    assert (repo / "bridge/app.py").read_text() == "release app"


@pytest.mark.parametrize("tag", ["../release", "--help", "release;false"])
def test_invalid_tag_no_output(bundle, tag):
    module, repo, commit, tmp = bundle
    with pytest.raises(ValueError, match="invalid_release_tag"):
        module.build(repo, "bridge", commit, tag, tmp / "wrong")
    assert not (tmp / "wrong").exists()
