#!/usr/bin/env python3
"""Generate TTS audio and upload to Substack for existing articles."""
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("gen_upload")

# Add paths
agents_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(agents_dir.parent / "lib"))
sys.path.insert(0, str(agents_dir / "socialmedia"))
sys.path.insert(0, str(agents_dir / "podcast"))

artifacts_dir = agents_dir.parent / "artifacts"
audio_dir = artifacts_dir / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {
        "title": "You Can't Evaluate Truth at a Point",
        "post_id": 190663444,
        "draft": artifacts_dir / "writings/you-cant-evaluate-truth-at-a-point/drafts/draft_r2.md",
        "audio_slug": "you-cant-evaluate-truth-at-a-point",
    },
    {
        "title": "I Am the Bug I Study",
        "post_id": 190735315,
        "draft": artifacts_dir / "writings/i-am-the-bug-i-study/drafts/final.md",
        "audio_slug": "i-am-the-bug-i-study",
    },
]

from handler import generate_audio_for_article
from substack import upload_audio_to_post


def run():
    for art in ARTICLES:
        title = art["title"]
        post_id = art["post_id"]
        draft_path = art["draft"]
        audio_path = audio_dir / f"{art['audio_slug']}.mp3"

        log.info("=== %s (post %s) ===", title, post_id)

        # Generate audio if not already done
        if audio_path.exists():
            log.info("Audio already exists: %s", audio_path)
        else:
            if not draft_path.exists():
                log.warning("Draft not found: %s — skipping", draft_path)
                continue
            article_text = draft_path.read_text(encoding="utf-8")
            log.info("Generating audio from %d chars...", len(article_text))
            result = generate_audio_for_article(article_text, title)
            if not result:
                log.error("Audio generation failed for %s", title)
                continue
            # Move to our target path if different
            result_path = Path(result)
            if result_path != audio_path:
                import shutil

                shutil.copy2(result_path, audio_path)
                log.info("Copied to %s", audio_path)
            else:
                log.info("Audio saved to %s", audio_path)

        # Upload to Substack
        log.info("Uploading to post %s...", post_id)
        success = upload_audio_to_post(audio_path, post_id)
        if success:
            log.info("✓ Audio uploaded for '%s'", title)
        else:
            log.error("✗ Upload failed for '%s'", title)


if __name__ == "__main__":
    run()
