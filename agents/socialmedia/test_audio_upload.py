#!/usr/bin/env python3
"""Test the audio upload to Substack."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

import logging
logging.basicConfig(level=logging.INFO)

from substack import upload_audio_to_post

mp3_path = Path(__file__).resolve().parent.parent.parent / "artifacts" / "audio" / "i-am-the-bug-i-study.mp3"
POST_ID = 190544583  # "I Am a Function, Not a Variable"

print(f"Uploading {mp3_path.name} ({mp3_path.stat().st_size} bytes) to post {POST_ID}")
result = upload_audio_to_post(mp3_path, POST_ID)
print(f"Result: {result}")
