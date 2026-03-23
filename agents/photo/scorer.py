"""Fast aesthetic scorer — WA's personal aesthetic model.

CLIP ViT-L/14 embeddings → trained MLP regression head.
Trained on 1161 photos scored by vision-model reviewer, calibrated to WA's taste.

Usage:
    from scorer import AestheticScorer
    scorer = AestheticScorer()
    score = scorer.score("path/to/photo.jpg")          # single
    scores = scorer.score_batch(["a.jpg", "b.jpg"])     # batch
    ranked = scorer.rank_folder("/path/to/folder")      # rank all images
"""

import json
import logging
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

log = logging.getLogger("photo.scorer")

MODEL_DIR = Path(__file__).parent / "output"
MODEL_PATH = MODEL_DIR / "aesthetic_model_v2.pth"
META_PATH = MODEL_DIR / "aesthetic_model_v2_meta.json"


class AestheticHead(nn.Module):
    def __init__(self, in_dim=768, hidden=256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(hidden, 64), nn.ReLU(), nn.Dropout(0.2),
            nn.Linear(64, 1),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


class AestheticScorer:
    def __init__(self):
        self._clip_model = None
        self._preprocess = None
        self._head = None
        self._meta = None

    def _load(self):
        if self._head is not None:
            return

        import open_clip

        log.info("Loading CLIP ViT-L/14 + aesthetic head...")
        self._clip_model, _, self._preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14", pretrained="datacomp_xl_s13b_b90k"
        )
        self._clip_model.eval()

        self._head = AestheticHead()
        self._head.load_state_dict(torch.load(MODEL_PATH, map_location="cpu", weights_only=True))
        self._head.eval()

        self._meta = json.loads(META_PATH.read_text())
        log.info(
            "Loaded. CV Spearman=%.3f, trained on %d samples",
            self._meta["cv_spearman"],
            self._meta["samples"],
        )

    def _embed(self, image_path: Path) -> torch.Tensor:
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        tensor = self._preprocess(img).unsqueeze(0)
        with torch.no_grad():
            feat = self._clip_model.encode_image(tensor)
            feat = feat / feat.norm(dim=-1, keepdim=True)
        return feat

    def score(self, image_path: str | Path) -> float:
        """Score a single image. Returns float 0-10."""
        self._load()
        image_path = Path(image_path)
        feat = self._embed(image_path)
        with torch.no_grad():
            pred = self._head(feat).item()
        return round(pred, 2)

    def score_batch(self, image_paths: list[str | Path]) -> list[dict]:
        """Score multiple images. Returns list of {file, score}."""
        self._load()
        results = []
        for p in image_paths:
            p = Path(p)
            if not p.exists():
                continue
            try:
                s = self.score(p)
                results.append({"file": str(p), "score": s})
            except Exception as e:
                log.warning("Failed to score %s: %s", p.name, e)
        return sorted(results, key=lambda r: r["score"], reverse=True)

    def rank_folder(self, folder: str | Path, exts=None) -> list[dict]:
        """Score and rank all images in a folder."""
        if exts is None:
            exts = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".webp"}
        folder = Path(folder)
        images = [f for f in folder.rglob("*") if f.suffix.lower() in exts]
        log.info("Scoring %d images in %s", len(images), folder)
        return self.score_batch(images)

    def pick_best(self, folder: str | Path, n: int = 5, min_score: float = 6.5) -> list[dict]:
        """Pick the top N images from a folder above min_score."""
        ranked = self.rank_folder(folder)
        return [r for r in ranked if r["score"] >= min_score][:n]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="Fast aesthetic scorer")
    parser.add_argument("paths", nargs="+", help="Image files or folders to score")
    parser.add_argument("--top", type=int, default=0, help="Show only top N")
    args = parser.parse_args()

    scorer = AestheticScorer()
    all_results = []

    for p in args.paths:
        p = Path(p)
        if p.is_dir():
            all_results.extend(scorer.rank_folder(p))
        elif p.is_file():
            s = scorer.score(p)
            all_results.append({"file": str(p), "score": s})
            print(f"  {s:.2f}  {p.name}")

    if len(all_results) > 1:
        all_results.sort(key=lambda r: r["score"], reverse=True)
        show = all_results[: args.top] if args.top else all_results
        print(f"\n{'='*50}")
        for r in show:
            print(f"  {r['score']:.2f}  {Path(r['file']).name}")
        print(f"\nTotal: {len(all_results)} images, avg: {np.mean([r['score'] for r in all_results]):.2f}")
