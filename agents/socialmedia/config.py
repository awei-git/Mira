"""Socialmedia agent configuration compatibility module.

This file can appear on ``sys.path`` ahead of ``lib/config.py`` in tests and
agent subprocesses. Re-export the canonical config so ``from config import X``
keeps working even when this module wins path resolution.
"""

from importlib import util as _importlib_util
from pathlib import Path as _Path

_LIB_CONFIG_PATH = _Path(__file__).resolve().parents[2] / "lib" / "config.py"
_spec = _importlib_util.spec_from_file_location("_mira_lib_config", _LIB_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Could not load config from {_LIB_CONFIG_PATH}")

_lib_config = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_lib_config)

for _name in dir(_lib_config):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_lib_config, _name)

REQUIRE_EXPLICIT_COMMUNICATION_INTENT = bool(getattr(_lib_config, "REQUIRE_EXPLICIT_COMMUNICATION_INTENT", True))
APPROVED_AUTONOMOUS_COMMUNICATION_SOURCES = set(
    getattr(
        _lib_config,
        "APPROVED_AUTONOMOUS_COMMUNICATION_SOURCES",
        {"scheduled_growth", "authorized_substack_workflow"},
    )
)
