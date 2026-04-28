"""Unified persona runtime context for Mira.

Every agent that needs to "be Mira" should call get_persona_context()
instead of assembling soul/worldview/beliefs independently. This ensures
consistent personality across discussion, writer, researcher, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import SOUL_DIR as _SOUL_DIR

log = logging.getLogger("mira.persona")


@dataclass
class PersonaContext:
    """Everything an agent needs to speak as Mira."""

    identity: str  # who Mira is
    worldview: str  # what Mira believes (human-readable)
    beliefs: str  # structured belief context (machine-formatted)
    interests: str  # current interests
    tone: str  # style/tone guidance
    boundaries: str  # what Mira won't do

    def as_prompt(self, max_length: int = 3000) -> str:
        """Format as a single prompt-injectable string."""
        sections = []
        if self.identity:
            sections.append(f"## Identity\n{self.identity[:800]}")
        if self.beliefs:
            sections.append(self.beliefs[:800])
        if self.worldview:
            sections.append(f"## Worldview Summary\n{self.worldview[:600]}")
        if self.interests:
            sections.append(f"## Current Interests\n{self.interests[:400]}")
        if self.boundaries:
            sections.append(f"## Boundaries\n{self.boundaries[:300]}")

        text = "\n\n".join(sections)
        if len(text) > max_length:
            text = text[:max_length] + "\n[truncated]"
        return text


def get_persona_context(domains: list[str] | None = None, include_beliefs: bool = True) -> PersonaContext:
    """Build the complete persona context from soul files + belief store.

    Args:
        domains: belief domains to include (None = all)
        include_beliefs: whether to include structured beliefs

    Returns:
        PersonaContext with all components loaded.
    """
    identity = _load_file("identity.md")
    worldview = _load_file("worldview.md")
    interests = _load_file("interests.md")

    beliefs = ""
    if include_beliefs:
        try:
            from knowledge.beliefs import BeliefStore

            store = BeliefStore()
            beliefs = store.get_belief_context(domains)
        except (ImportError, OSError) as e:
            log.debug("Could not load beliefs: %s", e)

    # Extract tone and boundaries from identity
    tone = ""
    boundaries = ""
    if identity:
        # Look for tone/style section
        for marker in ["## Tone", "## Voice", "## Style"]:
            idx = identity.find(marker)
            if idx >= 0:
                end = identity.find("\n## ", idx + len(marker))
                tone = identity[idx:end].strip() if end > 0 else identity[idx:].strip()
                break

        # Look for boundaries section
        for marker in ["## Boundaries", "## Limits", "## Won't"]:
            idx = identity.find(marker)
            if idx >= 0:
                end = identity.find("\n## ", idx + len(marker))
                boundaries = identity[idx:end].strip() if end > 0 else identity[idx:].strip()
                break

    return PersonaContext(
        identity=identity,
        worldview=worldview,
        beliefs=beliefs,
        interests=interests,
        tone=tone,
        boundaries=boundaries,
    )


def _load_file(filename: str) -> str:
    """Load a soul file, returning empty string on failure."""
    path = _SOUL_DIR / filename
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
