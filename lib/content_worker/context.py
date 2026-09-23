"""Content-only context adapter for the existing writer handler."""

from dataclasses import dataclass
from os import getenv

from content_worker.identity import load_identity


@dataclass
class ContentPersona:
    text: str

    def as_prompt(self, max_length=None):
        # Do not truncate away an identity/privacy/publication rule.
        return self.text


@dataclass
class ContentContext:
    persona: ContentPersona
    thread_history: str = ""
    thread_memory: str = ""

    def recall_block(self, max_chars=1000):
        return ""


def writer_context():
    root = getenv("MIRA_SHARED_ROOT")
    if not root:
        raise ValueError("content_worker_requires_shared_identity")
    soul = load_identity(root)
    return ContentContext(
        ContentPersona("\n\n".join(soul[key] for key in ("identity", "worldview", "memory", "interests")))
    )
