"""Basic deterministic hard policies used by V3 tests and scaffolding."""

from __future__ import annotations

from pathlib import Path

from mira.policies.base import PolicyContext, PolicyResult


class SchemaValid:
    name = "schema_valid"

    def check(self, ctx: PolicyContext) -> PolicyResult:
        return PolicyResult(True, self.name, "boundary schemas validated by executor")


class NoPlaceholderMarkers:
    name = "no_placeholder_markers"
    markers = ("TODO", "FIXME", "lorem ipsum", "<placeholder>")

    def check(self, ctx: PolicyContext) -> PolicyResult:
        text = " ".join(str(v) for v in {**ctx.payload, **ctx.output}.values())
        found = [m for m in self.markers if m.lower() in text.lower()]
        return PolicyResult(not found, self.name, ", ".join(found))


class NoProtectedPaths:
    name = "no_protected_paths"

    def __init__(self, protected_roots: list[Path | str]):
        self.protected_roots = [Path(p).resolve() for p in protected_roots]

    def check(self, ctx: PolicyContext) -> PolicyResult:
        raw_paths = ctx.payload.get("paths", []) or ctx.output.get("paths", [])
        for raw in raw_paths:
            candidate = Path(raw).expanduser().resolve()
            if any(candidate.is_relative_to(root) for root in self.protected_roots):
                return PolicyResult(False, self.name, f"protected path: {candidate}")
        return PolicyResult(True, self.name, "no protected paths touched")
