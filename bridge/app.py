"""Cloud bridge entry point; secrets remain host-local."""

from os import getenv
from pathlib import Path

from api import create_app
from obligations import Ledger

BASE = Path(getenv("MIRA_BRIDGE_ROOT", "/opt/mira-bridge"))
tokens = {"codex": (BASE / ".token").read_text().strip()}
for actor, filename in (("mira-app", ".app-token"), ("mira-aws", ".worker-token")):
    path = BASE / filename
    if path.exists():
        tokens[actor] = path.read_text().strip()

app = create_app(
    Ledger(
        getenv("MIRA_LEDGER_PATH", "/var/lib/mira-obligations/ledger.sqlite3"),
        getenv("MIRA_ARTIFACT_ROOT", "/opt/mira/Mira"),
    ),
    tokens,
)
