"""Agent Registry — dynamic manifest-based agent discovery and loading.

Replaces hardcoded if/elif routing in task_worker.py with a registry
that scans agent manifest.json files and dynamically imports handlers.

Usage:
    registry = AgentRegistry()
    handler = registry.load_handler("writer")
    handler(workspace, task_id, instruction, sender, thread_id)

    # For LLM planner prompt:
    descriptions = registry.get_agent_descriptions()
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Callable

_SUPER_DIR = Path(__file__).resolve().parent
_SHARED_DIR = _SUPER_DIR.parent.parent / "lib"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from ops.policy import get_capability_policy, resolve_capability_class

log = logging.getLogger("mira.registry")

_AGENTS_DIR = _SUPER_DIR.parent  # agents/


class AgentManifest:
    """Parsed agent manifest."""

    def __init__(self, data: dict, agent_dir: Path):
        self.name: str = data["name"]
        self.description: str = data["description"]
        self.keywords: list[str] = data.get("keywords", [])
        self.handles: list[str] = data.get("handles", [])
        self.tier: str = data.get("tier", "light")
        self.timeout_category: str = data.get("timeout_category", "short")
        self.entry_point: str = data.get("entry_point", "handler.py:handle")
        self.requires_workspace: bool = data.get("requires_workspace", True)
        self.capability_class: str = resolve_capability_class(
            self.name,
            data.get("capability_class"),
        )
        # Tool access control: if absent, defaults to full access (legacy compat)
        self.allowed_tools: list[str] | None = data.get("allowed_tools")
        # Permission scopes: machine-readable resource/API access declarations
        self.permissions: list[str] = data.get("permissions", [])
        self.agent_dir: Path = agent_dir

    def handler_path(self) -> tuple[Path, str]:
        """Return (file_path, function_name) from entry_point string."""
        file_part, func_name = self.entry_point.split(":")
        return self.agent_dir / file_part, func_name


class AgentRegistry:
    """Scans agent directories for manifest.json, provides dynamic loading."""

    def __init__(self, agents_dir: Path | None = None):
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._manifests: dict[str, AgentManifest] = {}
        self._handlers: dict[str, Callable] = {}
        self._modules: dict[str, object] = {}
        self._scan()

    def _scan(self):
        """Scan all agent directories for manifest.json files."""
        for manifest_path in sorted(self._agents_dir.glob("*/manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = AgentManifest(data, manifest_path.parent)
                self._manifests[manifest.name] = manifest
                log.debug("Registered agent: %s (%s)", manifest.name, manifest.agent_dir.name)
            except (json.JSONDecodeError, KeyError) as e:
                log.warning("Invalid manifest %s: %s", manifest_path, e)

    def list_agents(self) -> list[str]:
        """Return sorted list of registered agent names."""
        return sorted(self._manifests.keys())

    def get_manifest(self, name: str) -> AgentManifest | None:
        return self._manifests.get(name)

    def get_timeout_category(self, name: str) -> str:
        """Return timeout category for an agent. Defaults to 'short'."""
        manifest = self._manifests.get(name)
        return manifest.timeout_category if manifest else "short"

    def get_agent_descriptions(self) -> str:
        """Format all agent descriptions for the LLM planner prompt."""
        lines = []
        for name in sorted(self._manifests):
            m = self._manifests[name]
            handles = ", ".join(m.handles) if m.handles else m.description
            lines.append(f"- **{name}** ({m.tier}): {handles}")
        return "\n".join(lines)

    def get_valid_agents(self) -> set[str]:
        """Return set of valid agent names (for plan validation)."""
        return set(self._manifests.keys())

    def _load_module(self, name: str):
        """Dynamically import and cache the agent module."""
        if name in self._modules:
            return self._modules[name]
        manifest = self._manifests.get(name)
        if not manifest:
            raise KeyError(f"Agent '{name}' not in registry. Available: {self.list_agents()}")

        file_path, func_name = manifest.handler_path()
        if not file_path.exists():
            raise ImportError(f"Handler file not found: {file_path}")

        # Add agent directory to sys.path if needed
        agent_dir = str(manifest.agent_dir)
        if agent_dir not in sys.path:
            sys.path.insert(0, agent_dir)

        # Also ensure shared is in path
        shared_dir = str(self._agents_dir.parent / "lib")
        if shared_dir not in sys.path:
            sys.path.insert(0, shared_dir)

        # Dynamic import
        module_name = f"mira_agent_{name}_{file_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {file_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._modules[name] = module
        return module

    def load_handler(self, name: str) -> Callable:
        """Dynamically import and return the handler function for an agent.

        Handlers are cached after first load.
        Raises KeyError if agent not registered, ImportError if handler can't load.
        """
        if name in self._handlers:
            return self._handlers[name]

        manifest = self._manifests.get(name)
        if not manifest:
            raise KeyError(f"Agent '{name}' not in registry. Available: {self.list_agents()}")

        file_path, func_name = manifest.handler_path()
        module = self._load_module(name)
        handler = getattr(module, func_name, None)
        if handler is None:
            raise ImportError(f"Function '{func_name}' not found in {file_path}")

        self._handlers[name] = handler
        log.info("Loaded handler: %s → %s:%s", name, file_path.name, func_name)
        return handler

    def load_preflight(self, name: str) -> Callable | None:
        """Return optional preflight hook for an agent module."""
        module = self._load_module(name)
        preflight = getattr(module, "preflight", None)
        return preflight if callable(preflight) else None

    def requires_preflight(self, name: str) -> bool:
        """Return True when runtime must fail closed if preflight is unavailable."""
        manifest = self._manifests.get(name)
        policy = get_capability_policy(
            name,
            manifest.capability_class if manifest else None,
        )
        return policy.requires_preflight

    def get_allowed_tools(self, name: str) -> list[str] | None:
        """Return the allowed_tools list for an agent, or None if unrestricted."""
        manifest = self._manifests.get(name)
        if not manifest:
            return None
        return manifest.allowed_tools

    def get_capability_class(self, name: str) -> str:
        manifest = self._manifests.get(name)
        return manifest.capability_class if manifest else resolve_capability_class(name)

    def get_capability_policy(self, name: str) -> dict:
        manifest = self._manifests.get(name)
        policy = get_capability_policy(
            name,
            manifest.capability_class if manifest else None,
        )
        return policy.to_dict()


# Singleton — created once at import time
_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Get or create the singleton registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
