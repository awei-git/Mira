"""Daily photo edit pipeline.

Picks the best unprocessed RAW from NAS footage, edits it,
saves to artifacts, and notifies via Mira bridge.

Run by Mira super agent or cron.
"""

import json
import logging
import random
import sys
from datetime import datetime
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

log = logging.getLogger("photo.daily")

from config import NAS_PHOTO_DIR as _NAS_PHOTO_DIR
NAS_PHOTO_DIR = Path(_NAS_PHOTO_DIR)
from config import ARTIFACTS_DIR; ARTIFACTS_DIR = ARTIFACTS_DIR / "photos"
HISTORY_FILE = Path(__file__).parent / "output/daily_history.json"
REFERENCE_DIR = Path.home() / "Sandbox/assets/LRed"


def load_history() -> set:
    """Load set of already-processed RAW file stems."""
    if HISTORY_FILE.exists():
        data = json.loads(HISTORY_FILE.read_text())
        return set(data.get("processed", []))
    return set()


def save_history(processed: set):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps({"processed": sorted(processed)}, indent=2))


def find_candidates(n: int = 20) -> list[Path]:
    """Find unprocessed RAW files with JPG previews, scored by aesthetic model."""
    from scorer import AestheticScorer

    if not NAS_PHOTO_DIR.exists():
        log.error("NAS not mounted at %s", NAS_PHOTO_DIR)
        return []

    processed = load_history()
    raw_exts = {".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf"}

    # Collect all RAW+JPG pairs not yet processed
    pairs = []
    for shoot_dir in NAS_PHOTO_DIR.iterdir():
        if not shoot_dir.is_dir():
            continue
        for raw in shoot_dir.iterdir():
            if raw.suffix.lower() not in raw_exts:
                continue
            if raw.stem in processed:
                continue
            jpg = raw.with_suffix(".JPG")
            if not jpg.exists():
                jpg = raw.with_suffix(".jpg")
            if jpg.exists():
                pairs.append((raw, jpg))

    if not pairs:
        log.info("No unprocessed RAW+JPG pairs found")
        return []

    log.info("Found %d unprocessed RAW+JPG pairs", len(pairs))

    # Sample to avoid scoring thousands
    if len(pairs) > 100:
        pairs = random.sample(pairs, 100)

    # Score JPGs
    scorer = AestheticScorer()
    scored = []
    for raw, jpg in pairs:
        try:
            s = scorer.score(jpg)
            scored.append({"raw": raw, "jpg": jpg, "score": s})
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return [c["raw"] for c in scored[:n]]


def find_best_reference(scene_type: str = "landscape") -> Path | None:
    """Find the best reference image from LRed collection."""
    if not REFERENCE_DIR.exists():
        return None

    # Use high-scoring folders as reference source
    best_folders = ["202410", "20201123", "20201226", "OntheRoad", "InTheCity"]
    for folder in best_folders:
        folder_path = REFERENCE_DIR / folder
        if folder_path.exists():
            jpgs = list(folder_path.glob("*.jpg")) + list(folder_path.glob("*.JPG"))
            if jpgs:
                return jpgs[0]
    return None


def run_daily_edit() -> dict:
    """Main daily pipeline entry point."""
    log.info("=== Daily Photo Edit Pipeline ===")
    log.info("Date: %s", datetime.now().strftime("%Y-%m-%d %H:%M"))

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    # Find candidates
    candidates = find_candidates(n=5)
    if not candidates:
        return {"status": "no_candidates", "message": "No unprocessed RAW files found"}

    # Pick the top one
    selected = candidates[0]
    log.info("Selected: %s", selected)

    # Find reference
    ref = find_best_reference()

    # Edit
    from aesthetic_editor import edit_photo
    result = edit_photo(selected, reference_path=ref, output_dir=ARTIFACTS_DIR)

    # Update history
    processed = load_history()
    processed.add(selected.stem)
    save_history(processed)

    # Notify via Mira bridge
    if result.get("output"):
        notify_result(result)

    result["date"] = datetime.now().isoformat()
    result["status"] = "completed"

    # Save daily result
    daily_log = Path(__file__).parent / "output/daily_log.json"
    logs = []
    if daily_log.exists():
        try:
            logs = json.loads(daily_log.read_text())
        except Exception:
            pass
    logs.append({k: str(v) if isinstance(v, Path) else v for k, v in result.items()})
    daily_log.write_text(json.dumps(logs[-30:], indent=2, default=str))

    return result


def notify_result(result: dict):
    """Write notification to Mira bridge for iOS app."""
    try:
        bridge_dir = ARTIFACTS_DIR / "photos"
        bridge_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d")
        raw_name = Path(result["raw"]).stem

        note = {
            "type": "daily_photo_edit",
            "date": date_str,
            "raw": result["raw"],
            "output": result["output"],
            "score": result.get("score", 0),
            "analysis": result.get("params", {}).get("analysis", {}),
        }
        note_path = bridge_dir / f"{date_str}_{raw_name}.json"
        note_path.write_text(json.dumps(note, indent=2, default=str))
        log.info("Notification written to bridge: %s", note_path.name)
    except Exception as e:
        log.warning("Failed to write bridge notification: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    result = run_daily_edit()
    print(json.dumps(result, indent=2, default=str))
