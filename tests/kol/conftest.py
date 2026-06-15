import sys
from pathlib import Path

KOL_DIR = Path(__file__).resolve().parents[2] / "agents" / "kol"
if str(KOL_DIR) not in sys.path:
    sys.path.insert(0, str(KOL_DIR))
