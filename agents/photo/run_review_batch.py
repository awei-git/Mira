"""Run a batch of photo reviews. Usage: python run_review_batch.py <batch_file> <output_file>"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.WARNING)

from reviewer import review_photo

batch_file = sys.argv[1]
output_file = sys.argv[2]

sample = json.loads(Path(batch_file).read_text())
print(f"Batch: {len(sample)} photos → {output_file}", flush=True)

results = []
for i, img_path in enumerate(sample):
    img = Path(img_path)
    if not img.exists():
        continue

    folder = img.parent.name
    print(f"[{i+1}/{len(sample)}] {folder}/{img.name}...", end=" ", flush=True)

    try:
        r = review_photo(img, "auto")
        overall = r.get("overall", 0)
        cat = r.get("category", "?")
        print(f"{cat} {overall}/10", flush=True)
        r["folder"] = folder
        results.append(r)
    except Exception as e:
        print(f"ERR: {e}", flush=True)

    if (i + 1) % 20 == 0:
        Path(output_file).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"  (saved {len(results)} results)", flush=True)

Path(output_file).write_text(json.dumps(results, ensure_ascii=False, indent=2))
print(f"\nDone: {len(results)} reviews → {output_file}", flush=True)
