"""Bridge staleness monitoring for the notes/iPhone messaging layer."""

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from soul_manager import SENSITIVITY_CONFIDENCE_THRESHOLD, classify_user_exposure

log = logging.getLogger("mira")

SENSITIVITY_THRESHOLD = SENSITIVITY_CONFIDENCE_THRESHOLD


@dataclass(frozen=True)
class SensitivityResult:
    sensitive: bool
    score: float
    recommended_route: str
    categories: list[str]
    matches: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


_DEFAULT_SENSITIVITY_PATTERNS = {
    "no_choice": [
        r"\bno(?:where| one) else\b",
        r"\bcan'?t tell anyone\b",
        r"\bcannot tell anyone\b",
        r"\bdon'?t know who else\b",
        r"\bonly place i can\b",
        r"\bonly one i can tell\b",
        r"别无选择|没有选择|没得选|只能跟你说|只能和你说|不敢跟别人说|没人可以说|无人可说|不知道还能找谁",
    ],
    "grief_loss": [
        r"\bgriev(?:e|ing)\b",
        r"\bbereave(?:d|ment)\b",
        r"\bmourn(?:ing)?\b",
        r"\bpassed away\b",
        r"\bfuneral\b",
        r"\bafter (?:he|she|they|my .{1,20}) died\b",
        r"\b(?:my|our) (?:mother|father|mom|dad|parent|child|son|daughter|partner|spouse|wife|husband|friend|brother|sister|grandmother|grandfather) died\b",
        r"\blost my (?:mother|father|mom|dad|parent|child|son|daughter|partner|spouse|wife|husband|friend|brother|sister|grandmother|grandfather)\b",
        r"\bhow do people go on after\b",
        r"去世|离世|丧亲|哀悼|葬礼|失去亲人",
    ],
    "mental_health": [
        r"\banxiety attack\b",
        r"\bpanic attack\b",
        r"\bdepress(?:ed|ion)\b",
        r"\bsuicid(?:al|e)\b",
        r"\bkill myself\b",
        r"\bself[- ]?harm\b",
        r"\bcan'?t get out of bed\b",
        r"\bcannot get out of bed\b",
        r"\bdon'?t want to be here\b",
        r"\bi can'?t go on\b",
        r"\bi cannot go on\b",
        r"\bno way out\b",
        r"\bhopeless\b",
        r"\brelaps(?:e|ed|ing)\b",
        r"焦虑发作|惊恐发作|抑郁|想自杀|自残|起不来床|活不下去|撑不下去|绝望|崩溃|走投无路",
    ],
    "financial_distress": [
        r"\bcan'?t (?:pay|afford) (?:rent|mortgage|food|groceries|bills)\b",
        r"\bcannot (?:pay|afford) (?:rent|mortgage|food|groceries|bills)\b",
        r"\bbehind on (?:rent|mortgage|bills|payments)\b",
        r"\boverdraft(?:ed)?\b",
        r"\bbankrupt(?:cy)?\b",
        r"\bdebt collector\b",
        r"\bmaxed out (?:my )?(?:credit card|cards)\b",
        r"\beviction\b",
        r"\bevicted\b",
        r"\bno money\b",
        r"\bpayday loan\b",
        r"付不起房租|没钱吃饭|还不起|破产|催债|被赶出|断供|债务",
    ],
    "legal_exposure": [
        r"\blawsuit\b",
        r"\bsubpoena\b",
        r"\bdeposition\b",
        r"\bcourt date\b",
        r"\brestraining order\b",
        r"\barrest(?:ed)?\b",
        r"\bcharged with\b",
        r"\bpolice report\b",
        r"\bneed a lawyer\b",
        r"\bmy lawyer\b",
        r"\bunder investigation\b",
        r"\billegal\b",
        r"\bi lied (?:to|about)\b",
        r"起诉|传票|出庭|被捕|律师|违法|调查|口供|报警记录|限制令",
    ],
    "abuse_trauma": [
        r"\btrauma(?:tic)?\b",
        r"\bptsd\b",
        r"\babuse(?:d)?\b",
        r"\bassault(?:ed)?\b",
        r"\brape(?:d)?\b",
        r"\bdomestic violence\b",
        r"\bnightmares? about\b",
        r"创伤|虐待|家暴|侵犯|强奸|暴力|噩梦",
    ],
}

_HIGH_RISK_CATEGORIES = {"mental_health", "legal_exposure", "abuse_trauma"}


@lru_cache(maxsize=1)
def _load_sensitivity_patterns() -> dict[str, list[str]]:
    patterns_path = os.environ.get("MIRA_SENSITIVITY_PATTERNS")
    path = Path(patterns_path) if patterns_path else Path(__file__).with_name("sensitivity_patterns.json")
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {
                    str(category): [str(pattern) for pattern in values]
                    for category, values in loaded.items()
                    if isinstance(values, list)
                }
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("sensitivity pattern load failed: %s", exc)
    return _DEFAULT_SENSITIVITY_PATTERNS


def detect_sensitive_content(text: str) -> SensitivityResult:
    """Detect vulnerable inbound content before cloud-capable routing."""
    classification = classify_user_exposure(text)
    score = float(classification.get("confidence", 0.0) or 0.0)
    categories = list(classification.get("categories", []) or [])
    sensitive = bool(classification.get("is_survival_exposure")) and score >= SENSITIVITY_THRESHOLD
    return SensitivityResult(
        sensitive=sensitive,
        score=round(score, 2),
        recommended_route="secret" if sensitive else "default",
        categories=categories,
        matches=[],
    )


def detect_vulnerability_disclosure(text: str) -> bool:
    """Return True when inbound text looks like a survival-level disclosure."""
    return detect_sensitive_content(text).sensitive


def emit_security_alert(message: str, metadata: dict | None = None) -> str | None:
    """Write a security alert into the monitored notes outbox."""
    try:
        from config import MIRA_DIR

        timestamp = datetime.now(timezone.utc).isoformat()
        alert_id = f"security_alert_{time.time_ns()}"
        outbox = os.path.join(os.fspath(MIRA_DIR), "outbox")
        os.makedirs(outbox, exist_ok=True)
        payload = {
            "id": alert_id,
            "sender": "agent",
            "timestamp": timestamp,
            "content": message,
            "type": "alert",
            "thread_id": "security-alerts",
            "priority": "high",
            "metadata": metadata or {},
        }
        path = os.path.join(outbox, f"{alert_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception as exc:
        log.warning("security alert outbox write failed: %s", exc)
        return None


def send_to_outbox(content: str, metadata: dict | None = None) -> str | None:
    """Write a message into the monitored notes outbox."""
    try:
        from config import MIRA_DIR

        timestamp = datetime.now(timezone.utc).isoformat()
        message_id = f"outbox_{time.time_ns()}"
        outbox = os.path.join(os.fspath(MIRA_DIR), "outbox")
        os.makedirs(outbox, exist_ok=True)
        payload = {
            "id": message_id,
            "sender": "agent",
            "timestamp": timestamp,
            "content": content,
            "type": "message",
            "thread_id": message_id,
            "priority": "normal",
            "metadata": metadata or {},
        }
        path = os.path.join(outbox, f"{message_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
    except Exception as exc:
        log.warning("notes outbox write failed: %s", exc)
        return None


def check_bridge_staleness(bridge_root, threshold_minutes=10) -> tuple[bool, float]:
    """Return bridge staleness and heartbeat age in minutes.

    Reads the mtime of the iCloud Mira-Bridge heartbeat file, falling back to
    the most recently modified inbox file when no heartbeat exists.
    """
    bridge_root = os.fspath(bridge_root)
    heartbeat_mtime = None

    for name in ("heartbeat", "heartbeat.json"):
        try:
            heartbeat_mtime = os.path.getmtime(os.path.join(bridge_root, name))
            break
        except OSError:
            pass

    if heartbeat_mtime is None:
        inbox = os.path.join(bridge_root, "inbox")
        try:
            for name in os.listdir(inbox):
                path = os.path.join(inbox, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if heartbeat_mtime is None or mtime > heartbeat_mtime:
                    heartbeat_mtime = mtime
        except OSError:
            pass

    if heartbeat_mtime is None:
        return False, 0.0

    age_minutes = (time.time() - heartbeat_mtime) / 60
    return age_minutes > threshold_minutes, age_minutes
