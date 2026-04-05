"""Tests for unified persona context."""
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SHARED))
sys.path.insert(0, str(_SHARED / "persona"))


def _soul_files_exist():
    """Check if real soul files are present (not in CI)."""
    return (_SHARED / "soul" / "identity.md").exists()


def test_load_persona_context():
    from persona.persona_context import get_persona_context
    ctx = get_persona_context()
    # PersonaContext should always return, even with empty files
    assert ctx is not None
    if _soul_files_exist():
        assert len(ctx.identity) > 0, "identity.md should be non-empty"


def test_beliefs_included():
    if not _soul_files_exist():
        return  # skip in CI
    from persona.persona_context import get_persona_context
    ctx = get_persona_context(include_beliefs=True)
    assert len(ctx.beliefs) > 0, "beliefs should be loaded"


def test_as_prompt():
    from persona.persona_context import get_persona_context, PersonaContext
    # Test with synthetic data (CI-safe)
    ctx = PersonaContext(
        identity="I am Mira",
        worldview="The world is complex",
        beliefs="- Compression favors consistency",
        interests="AI, math",
        tone="curious",
        boundaries="No medical advice",
    )
    prompt = ctx.as_prompt(max_length=5000)
    assert len(prompt) > 50
    assert "Identity" in prompt
    assert "Mira" in prompt


def test_domain_filter():
    if not _soul_files_exist():
        return  # skip in CI
    from persona.persona_context import get_persona_context
    ctx = get_persona_context(domains=["security"])
    if ctx.beliefs:
        assert "security" in ctx.beliefs.lower() or "attack" in ctx.beliefs.lower()
