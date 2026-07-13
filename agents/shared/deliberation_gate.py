"""Human-auditable deliberation gate for high-impact actions."""

from datetime import datetime, timezone
from pathlib import Path
import re


def deliberate(action_type: str, context: dict) -> str:
    timestamp = datetime.now(timezone.utc)
    try:
        from config import MIRA_ROOT
    except Exception:
        MIRA_ROOT = Path(__file__).resolve().parents[2]

    log_dir = Path(MIRA_ROOT) / "logs" / "deliberation"
    log_dir.mkdir(parents=True, exist_ok=True)

    safe_action = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(action_type or "action")).strip("-") or "action"
    log_path = log_dir / f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{safe_action}.md"

    agent_name = str(context.get("agent_name") or "unknown").strip() or "unknown"
    proposed_change = str(context.get("proposed_change") or "").strip()
    justification = str(context.get("justification") or "").strip()
    alternatives_considered = context.get("alternatives_considered")
    if isinstance(alternatives_considered, (list, tuple)):
        alternatives = "\n".join(f"- {str(item).strip()}" for item in alternatives_considered if str(item).strip())
    else:
        alternatives = str(alternatives_considered or "").strip()
    reversible = bool(context.get("reversible", False))

    log_path.write_text(
        "\n".join(
            [
                "# Deliberation Log",
                "",
                f"timestamp: {timestamp.isoformat()}",
                f"agent_name: {agent_name}",
                f"action_type: {str(action_type or '').strip()}",
                "",
                "proposed_change:",
                proposed_change,
                "",
                "justification:",
                justification,
                "",
                "alternatives_considered:",
                alternatives,
                "",
                f"reversible: {str(reversible).lower()}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return str(log_path)
