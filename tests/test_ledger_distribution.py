"""Exercise the real archive builder with local fixtures; no GitHub/AWS writes."""

import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def distribution(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "deploy"))
    spec = importlib.util.spec_from_file_location("mkdist_under_test", ROOT / "deploy/mkdist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.load_projects()["bridge"]
    monkeypatch.setattr(module, "load_projects", lambda: {"bridge": config})
    monkeypatch.setattr(sys, "argv", ["mkdist.py", "bridge", "test-release"])
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda: str(tmp_path))
    files = {name: (ROOT / "bridge" / name).read_bytes() for name in ("app.py", "api.py")}
    monkeypatch.setattr(module, "build_from_clone", lambda *args: files.copy())
    commands = []
    monkeypatch.setattr(module, "sh", lambda command, **kwargs: commands.append(command) or "fixture-url")
    return module, config, commands, tmp_path


def test_bridge_archive_contains_canonical_ledger_from_exact_release(distribution, monkeypatch, capsys):
    module, _, commands, tmp = distribution
    sha = "a" * 40
    requests = []

    def api(path):
        requests.append(path)
        if "/git/ref/" in path:
            return {"object": {"type": "commit", "sha": sha}}
        assert path == f"/repos/awei-git/Mira/contents/lib/obligations.py?ref={sha}"
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode((ROOT / "lib/obligations.py").read_bytes()).decode(),
        }

    monkeypatch.setattr(module, "api", api)
    module.main()
    result = json.loads(capsys.readouterr().out)
    assert result["sha"] == sha and result["files"] == 3
    assert len(requests) == 2 and len(commands) == 2
    with tarfile.open(tmp / "bridge-test-release.tar.gz") as archive:
        assert sorted(archive.getnames()) == ["api.py", "app.py", "obligations.py"]
        assert archive.extractfile("obligations.py").read() == (ROOT / "lib/obligations.py").read_bytes()
        standalone = tmp / "standalone"
        standalone.mkdir()
        for member in archive.getmembers():
            (standalone / member.name).write_bytes(archive.extractfile(member).read())
    # Fresh interpreter, outside the repo: the packaged API resolves its own
    # ledger without accidentally using lib/bridge.py or the source checkout.
    check = subprocess.run(
        [sys.executable, "-c", "from api import create_app; from obligations import Ledger; print(Ledger.__module__)"],
        cwd=standalone,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr
    assert check.stdout.strip() == "obligations"


@pytest.mark.parametrize("destination", ["../escape.py", "/absolute.py", ".app-token"])
def test_invalid_or_preserved_extra_path_never_uploads(distribution, monkeypatch, destination):
    module, config, commands, _ = distribution
    config["extra_files"] = {"lib/obligations.py": destination}
    monkeypatch.setattr(module, "api", lambda path: {"object": {"type": "commit", "sha": "a" * 40}})
    with pytest.raises(ValueError):
        module.main()
    assert not commands
