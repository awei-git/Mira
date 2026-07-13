#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [ROOT / "agents", ROOT / "web"]
ALLOW = {
    ROOT / "lib" / "llm_port",
    ROOT / "lib" / "llm_providers",
}

BANNED = [
    (re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.genai|minimax)\b", re.M), "direct provider import"),
    (re.compile(r"playwright.*claude\.ai|claude-cli-mod|openclaw", re.I), "unsupported Claude session wrapper"),
    (re.compile(r"https?://(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[0-1])\.)\d+\.\d+(?::\d+)?"), "hardcoded LAN URL"),
]


def _allowed(path: Path) -> bool:
    return any(path == allow or allow in path.parents for allow in ALLOW)


def main() -> int:
    findings: list[str] = []
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".swift", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml"}:
                continue
            if _allowed(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = path.relative_to(ROOT)
            for pattern, label in BANNED:
                if pattern.search(text):
                    findings.append(f"{rel}: {label}")
    if findings:
        print("V2 kernel boundary violations:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("V2 kernel boundaries OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
