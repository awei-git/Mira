from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "lib", ROOT / "agents" / "writer", ROOT / "agents" / "shared"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_writing_workflow():
    module_path = ROOT / "agents" / "writer" / "writing_workflow.py"
    spec = importlib.util.spec_from_file_location("writing_workflow_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_find_active_projects_scans_current_writings_root(tmp_path):
    workflow = _load_writing_workflow()
    research_root = tmp_path / "research"
    writings_root = tmp_path / "writings"
    research_root.mkdir()
    writings_root.mkdir()
    project = writings_root / "stalled-essay"
    project.mkdir()
    (project / "project.json").write_text(
        json.dumps({"title": "Stalled Essay", "phase": "reviewing"}),
        encoding="utf-8",
    )

    workflow.WORKSPACE_DIR = research_root
    workflow._WRITINGS_ROOT = writings_root

    active = workflow.find_active_projects()

    assert [(path.name, data["phase"]) for path, data in active] == [("stalled-essay", "reviewing")]


def test_find_active_projects_ignores_stale_triage_projects(tmp_path):
    workflow = _load_writing_workflow()
    research_root = tmp_path / "research"
    writings_root = tmp_path / "writings"
    research_root.mkdir()
    writings_root.mkdir()

    parked = writings_root / "parked"
    parked.mkdir()
    (parked / "project.json").write_text(
        json.dumps({"title": "Parked", "phase": "stale_triage"}),
        encoding="utf-8",
    )
    active_project = writings_root / "active"
    active_project.mkdir()
    (active_project / "project.json").write_text(
        json.dumps({"title": "Active", "phase": "reviewing"}),
        encoding="utf-8",
    )

    workflow.WORKSPACE_DIR = research_root
    workflow._WRITINGS_ROOT = writings_root

    active = workflow.find_active_projects()

    assert [(path.name, data["phase"]) for path, data in active] == [("active", "reviewing")]
