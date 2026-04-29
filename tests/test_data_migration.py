"""Tests for data directory migration — verify all paths resolve to data/."""

from __future__ import annotations
import sys
from pathlib import Path

import pytest

_MIRA_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 1. Config path constants all point to data/
# ---------------------------------------------------------------------------


class TestConfigPaths:
    def test_data_dir_exists(self):
        from config import DATA_DIR

        assert DATA_DIR.exists(), f"DATA_DIR missing: {DATA_DIR}"

    def test_soul_dir_under_data(self):
        from config import DATA_DIR, SOUL_DIR

        assert str(SOUL_DIR).startswith(str(DATA_DIR)), f"SOUL_DIR {SOUL_DIR} not under DATA_DIR {DATA_DIR}"

    def test_logs_dir_under_data(self):
        from config import DATA_DIR, LOGS_DIR

        assert str(LOGS_DIR).startswith(str(DATA_DIR))

    def test_feeds_dir_under_data(self):
        from config import DATA_DIR, FEEDS_DIR

        assert str(FEEDS_DIR).startswith(str(DATA_DIR))

    def test_state_file_under_data(self):
        from config import DATA_DIR, STATE_FILE

        assert str(STATE_FILE).startswith(str(DATA_DIR))
        assert STATE_FILE.name == "agent_state.json"

    def test_session_file_under_data(self):
        from config import DATA_DIR, SESSION_FILE

        assert str(SESSION_FILE).startswith(str(DATA_DIR))

    def test_health_file_under_data(self):
        from config import DATA_DIR, HEALTH_FILE

        assert str(HEALTH_FILE).startswith(str(DATA_DIR))

    def test_pending_publish_under_data(self):
        from config import DATA_DIR, PENDING_PUBLISH_FILE

        assert str(PENDING_PUBLISH_FILE).startswith(str(DATA_DIR))

    def test_pids_dir_under_data(self):
        from config import DATA_DIR, PIDS_DIR

        assert str(PIDS_DIR).startswith(str(DATA_DIR))

    def test_social_state_dir_under_data(self):
        from config import DATA_DIR, SOCIAL_STATE_DIR

        assert str(SOCIAL_STATE_DIR).startswith(str(DATA_DIR))

    def test_proposals_dir_under_data(self):
        from config import DATA_DIR, PROPOSALS_DIR

        assert str(PROPOSALS_DIR).startswith(str(DATA_DIR))

    def test_autoresearch_dir_under_data(self):
        from config import DATA_DIR, AUTORESEARCH_DIR

        assert str(AUTORESEARCH_DIR).startswith(str(DATA_DIR))

    def test_tasks_dir_under_data(self):
        from config import DATA_DIR, TASKS_DIR

        assert str(TASKS_DIR).startswith(str(DATA_DIR))

    def test_scheduled_jobs_under_data(self):
        from config import DATA_DIR, SCHEDULED_JOBS_FILE

        assert str(SCHEDULED_JOBS_FILE).startswith(str(DATA_DIR))


# ---------------------------------------------------------------------------
# 2. Data files actually exist at new locations
# ---------------------------------------------------------------------------


class TestDataFilesExist:
    def test_state_file_exists(self):
        from config import STATE_FILE

        assert STATE_FILE.exists(), f"Missing: {STATE_FILE}"

    def test_session_file_exists(self):
        from config import SESSION_FILE

        assert SESSION_FILE.exists(), f"Missing: {SESSION_FILE}"

    def test_health_file_exists(self):
        from config import HEALTH_FILE

        assert HEALTH_FILE.exists(), f"Missing: {HEALTH_FILE}"

    def test_soul_identity_exists(self):
        from config import SOUL_DIR

        assert (SOUL_DIR / "identity.md").exists()

    def test_soul_learned_exists(self):
        from config import SKILLS_DIR

        # data/soul/learned/ is .gitignored — absent on CI runners. Skip
        # rather than fail; the assertion is meaningful only on local
        # checkouts where Mira has actually run and seeded skills.
        if not SKILLS_DIR.exists():
            pytest.skip(f"{SKILLS_DIR} absent (not a local agent runtime)")
        # rglob so quarantined skills (under quarantine/) and .blocked
        # variants both count toward "learned skills present" — empty
        # top-level glob was producing false negatives once the audit
        # workflow moved approved skills around.
        md_count = len(list(SKILLS_DIR.rglob("*.md")))
        blocked_count = len(list(SKILLS_DIR.glob("*.blocked")))
        assert (md_count + blocked_count) > 0, "No learned skills found"

    def test_social_state_files_exist(self):
        from config import SOCIAL_STATE_DIR

        expected = ["twitter_state.json", "growth_state.json", "notes_state.json"]
        for name in expected:
            assert (SOCIAL_STATE_DIR / name).exists(), f"Missing: {SOCIAL_STATE_DIR / name}"


# ---------------------------------------------------------------------------
# 3. No stale references to old paths in source code
# ---------------------------------------------------------------------------


class TestNoStaleReferences:
    """Verify Python source files don't reference old hardcoded paths."""

    @pytest.fixture
    def python_sources(self):
        """All .py files under lib/ and agents/ (excluding tests and __pycache__)."""
        root = _MIRA_ROOT
        files = []
        for d in [root / "lib", root / "agents"]:
            for f in d.rglob("*.py"):
                if "__pycache__" in str(f) or "/tests/" in str(f) or "test_" in f.name:
                    continue
                files.append(f)
        return files

    def test_no_root_agent_state(self, python_sources):
        """No code should reference MIRA_ROOT / '.agent_state.json'."""
        bad = []
        for f in python_sources:
            content = f.read_text(encoding="utf-8", errors="replace")
            if '".agent_state.json"' in content or "'.agent_state.json'" in content:
                bad.append(str(f))
        assert not bad, f"Old .agent_state.json reference in: {bad}"

    def test_no_root_session_context(self, python_sources):
        bad = []
        for f in python_sources:
            content = f.read_text(encoding="utf-8", errors="replace")
            if '".session_context.json"' in content or "'.session_context.json'" in content:
                bad.append(str(f))
        assert not bad, f"Old .session_context.json reference in: {bad}"

    def test_no_root_bg_health(self, python_sources):
        bad = []
        for f in python_sources:
            content = f.read_text(encoding="utf-8", errors="replace")
            if '".bg_health.json"' in content or "'.bg_health.json'" in content:
                bad.append(str(f))
        assert not bad, f"Old .bg_health.json reference in: {bad}"

    def test_no_hardcoded_bg_pids(self, python_sources):
        """No code should build agents/.bg_pids path manually."""
        bad = []
        for f in python_sources:
            content = f.read_text(encoding="utf-8", errors="replace")
            if '".bg_pids"' in content or "'.bg_pids'" in content:
                bad.append(str(f))
        assert not bad, f"Old .bg_pids reference in: {bad}"

    def test_no_hardcoded_socialmedia_state(self, python_sources):
        """No code should use Path(__file__).parent for socialmedia state files."""
        bad = []
        patterns = [
            '"twitter_state.json"',
            '"growth_state.json"',
            '"notes_state.json"',
            '"comment_state.json"',
            '"publication_stats.json"',
            '"reply_tracking.json"',
        ]
        for f in python_sources:
            if "socialmedia" not in str(f) and "super" not in str(f):
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            for pat in patterns:
                if pat in content and "Path(__file__)" in content and "SOCIAL_STATE_DIR" not in content:
                    bad.append(f"{f}: still uses Path(__file__) for {pat}")
        assert not bad, f"Old socialmedia state path: {bad}"


# ---------------------------------------------------------------------------
# 4. Old locations should be empty (no data files left behind)
# ---------------------------------------------------------------------------


class TestOldLocationsClean:
    def test_no_root_dotfiles(self):
        """Root should not have .agent_state.json etc.

        These are .gitignored legacy paths — long-running local checkouts
        may still have them. The CI assertion holds (clean checkout never
        creates them) so the rule is: warn-but-don't-fail on local where a
        legacy file is still hanging around.
        """
        stale = []
        for name in [".agent_state.json", ".session_context.json", ".bg_health.json", ".pending_publish.json"]:
            if (_MIRA_ROOT / name).exists():
                stale.append(name)
        if stale:
            # Allow stale files locally (long-running dev tree); CI sees none.
            if (_MIRA_ROOT / ".git").exists() and (_MIRA_ROOT / "secrets.yml").exists():
                pytest.skip(f"Local checkout has legacy files {stale}; CI is clean")
            assert not stale, f"Stale files in root: {stale}"

    def test_no_socialmedia_state_in_agent_dir(self):
        """agents/socialmedia/ should not have state files.

        Same rationale as test_no_root_dotfiles — local checkouts may have
        legacy files; the CI assertion is what matters.
        """
        sm_dir = _MIRA_ROOT / "agents" / "socialmedia"
        stale = []
        for name in ["twitter_state.json", "growth_state.json", "notes_state.json", "comment_state.json"]:
            if (sm_dir / name).exists():
                stale.append(name)
        if stale:
            if (_MIRA_ROOT / ".git").exists() and (_MIRA_ROOT / "secrets.yml").exists():
                pytest.skip(f"Local checkout has legacy files {stale}; CI is clean")
            assert not stale, f"Stale socialmedia state files: {stale}"


# ---------------------------------------------------------------------------
# 5. Config still loads and modules still import
# ---------------------------------------------------------------------------


class TestImports:
    def test_config_loads(self):
        from config import (
            MIRA_ROOT,
            DATA_DIR,
            SOUL_DIR,
            LOGS_DIR,
            FEEDS_DIR,
            STATE_FILE,
            SESSION_FILE,
            HEALTH_FILE,
            PENDING_PUBLISH_FILE,
            PIDS_DIR,
            SOCIAL_STATE_DIR,
            PROPOSALS_DIR,
            AUTORESEARCH_DIR,
            TASKS_DIR,
            SCHEDULED_JOBS_FILE,
        )

        assert MIRA_ROOT.exists()
        assert DATA_DIR.exists()

    def test_evolution_config_loads(self):
        from config import SOUL_DIR
        from evolution.config import EXPERIENCE_DIR, LESSON_DIR, VARIANT_DIR

        assert str(EXPERIENCE_DIR).startswith(str(SOUL_DIR))
