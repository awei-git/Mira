"""Scene analyzer — extract frames from video, analyze with vision API.

Phase 1 of the video editing pipeline:
  video files → ffmpeg frame extraction → Gemini vision → scene_log.json
"""
import base64
import json
import logging
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("video.scene_analyzer")

# Video extensions to scan
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".mpg", ".wmv"}


def _get_video_info(video_path: Path) -> dict:
    """Get video duration, resolution, fps via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))

        # Find video stream
        width, height, fps = 0, 0, 30.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
                # Parse fps from r_frame_rate "30/1" or "30000/1001"
                r = s.get("r_frame_rate", "30/1")
                parts = r.split("/")
                if len(parts) == 2 and int(parts[1]) > 0:
                    fps = int(parts[0]) / int(parts[1])
                break

        return {"duration": duration, "width": width, "height": height, "fps": fps}
    except Exception as e:
        log.warning("ffprobe failed for %s: %s", video_path.name, e)
        return {"duration": 0, "width": 0, "height": 0, "fps": 30.0}


def extract_frames(video_path: Path, output_dir: Path,
                   interval: float = 2.0, scene_threshold: float = 0.3,
                   max_frames: int = 50) -> list[dict]:
    """Extract key frames from a video using ffmpeg.

    Uses both fixed-interval sampling and scene change detection.
    Returns list of {path, timestamp, type} for each extracted frame.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem

    info = _get_video_info(video_path)
    duration = info["duration"]
    if duration <= 0:
        log.warning("Could not determine duration for %s", video_path.name)
        return []

    # Strategy: for short clips (<30s), every 2s; for longer, adapt interval
    if duration > 120:
        interval = max(interval, duration / max_frames)

    frames = []

    # 1. Fixed-interval extraction (reliable baseline)
    interval_dir = output_dir / f"{stem}_interval"
    interval_dir.mkdir(exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"fps=1/{interval},scale=1280:-1",
        "-q:v", "3",  # JPEG quality
        str(interval_dir / f"{stem}_%04d.jpg"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("Frame extraction failed for %s: %s", video_path.name, e)
        return []

    # Collect extracted frames with timestamps
    for f in sorted(interval_dir.glob("*.jpg")):
        idx = int(f.stem.split("_")[-1]) - 1  # 1-indexed from ffmpeg
        ts = idx * interval
        frames.append({
            "path": str(f),
            "timestamp": ts,
            "timestamp_str": _format_ts(ts),
            "type": "interval",
            "source_file": video_path.name,
        })

    # 2. Scene change detection (catch transitions)
    scene_dir = output_dir / f"{stem}_scene"
    scene_dir.mkdir(exist_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"select='gt(scene,{scene_threshold})',scale=1280:-1",
        "-vsync", "vfr",
        "-q:v", "3",
        str(scene_dir / f"{stem}_scene_%04d.jpg"),
    ]
    try:
        # Also get timestamps via showinfo
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("Scene detection failed for %s: %s", video_path.name, e)

    for f in sorted(scene_dir.glob("*.jpg")):
        # Scene frames don't have reliable timestamps from filename,
        # but they supplement the interval frames
        frames.append({
            "path": str(f),
            "timestamp": -1,  # unknown
            "timestamp_str": "scene_change",
            "type": "scene_change",
            "source_file": video_path.name,
        })

    # Limit total frames
    if len(frames) > max_frames:
        # Keep first, last, and evenly sample the rest
        step = len(frames) / max_frames
        selected = [frames[int(i * step)] for i in range(max_frames)]
        frames = selected

    log.info("Extracted %d frames from %s (%.1fs)", len(frames), video_path.name, duration)
    return frames


def analyze_frames_gemini(frames: list[dict], api_key: str,
                          batch_size: int = 8) -> list[dict]:
    """Analyze frames using Gemini Vision API.

    Sends frames in batches for efficiency. Returns enriched frame dicts
    with description, mood, quality score, etc.
    """
    if not api_key:
        log.error("No Gemini API key")
        return frames

    results = []
    total_input_tokens = 0
    total_output_tokens = 0
    batches = [frames[i:i + batch_size] for i in range(0, len(frames), batch_size)]

    for batch_idx, batch in enumerate(batches):
        log.info("Analyzing frame batch %d/%d (%d frames)",
                 batch_idx + 1, len(batches), len(batch))

        # Build multimodal content
        parts = [{
            "text": (
                "You are analyzing frames from a travel video for editing. "
                "For each frame, provide a JSON array with one object per frame:\n"
                "[\n"
                '  {"frame_idx": 0, "description": "brief scene description", '
                '"location_type": "beach/city/mountain/indoor/etc", '
                '"subjects": "people/landscape/food/etc", '
                '"mood": "peaceful/exciting/contemplative/joyful/etc", '
                '"quality": 4, '  # 1-5
                '"highlights": "what makes this frame interesting or worth keeping", '
                '"notes": "any editing notes - camera motion, lighting, etc"}\n'
                "]\n"
                "Quality 1=unusable (blurry/dark), 3=ok, 5=stunning.\n"
                "Be concise. Return ONLY the JSON array."
            )
        }]

        for i, frame in enumerate(batch):
            frame_path = Path(frame["path"])
            if not frame_path.exists():
                continue
            try:
                img_data = frame_path.read_bytes()
                b64 = base64.b64encode(img_data).decode("utf-8")
                parts.append({
                    "text": f"\n--- Frame {i} [{frame.get('timestamp_str', '?')}] from {frame.get('source_file', '?')} ---"
                })
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": b64,
                    }
                })
            except Exception as e:
                log.warning("Could not read frame %s: %s", frame_path, e)

        # Call Gemini
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": 4096,
                "temperature": 0.3,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]

                # Track token usage
                usage = data.get("usageMetadata", {})
                batch_in = usage.get("promptTokenCount", 0)
                batch_out = usage.get("candidatesTokenCount", 0)
                total_input_tokens += batch_in
                total_output_tokens += batch_out
                log.info("Batch %d tokens: %d in / %d out",
                         batch_idx + 1, batch_in, batch_out)

                # Parse JSON from response
                import re
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    analyses = json.loads(match.group())
                    for a in analyses:
                        idx = a.get("frame_idx", 0)
                        if idx < len(batch):
                            batch[idx].update({
                                "description": a.get("description", ""),
                                "location_type": a.get("location_type", ""),
                                "subjects": a.get("subjects", ""),
                                "mood": a.get("mood", ""),
                                "quality": a.get("quality", 3),
                                "highlights": a.get("highlights", ""),
                                "notes": a.get("notes", ""),
                            })
                else:
                    log.warning("Could not parse frame analysis JSON")

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")[:300]
            log.error("Gemini vision HTTP %d: %s", e.code, error_body)
        except Exception as e:
            log.error("Gemini vision failed: %s", e)

        results.extend(batch)

    log.info("Gemini Vision total tokens: %d input / %d output (%.4f USD est.)",
             total_input_tokens, total_output_tokens,
             total_input_tokens * 0.15 / 1_000_000 + total_output_tokens * 0.6 / 1_000_000)
    return results


def transcribe_audio_openai(video_path: Path, api_key: str) -> list[dict]:
    """Extract and transcribe audio using OpenAI Whisper API.

    Returns list of {start, end, text} segments.
    """
    if not api_key:
        log.warning("No OpenAI API key, skipping transcription")
        return []

    # Extract audio to temp file
    audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "libmp3lame", "-q:a", "4",
        "-ac", "1",  # mono
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as e:
        log.warning("Audio extraction failed: %s", e)
        return []

    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        log.info("No audio or too short in %s", video_path.name)
        audio_path.unlink(missing_ok=True)
        return []

    # Check file size — Whisper API limit is 25MB
    file_size = audio_path.stat().st_size
    if file_size > 25 * 1024 * 1024:
        log.warning("Audio file too large (%dMB), skipping transcription",
                     file_size // (1024 * 1024))
        audio_path.unlink(missing_ok=True)
        return []

    # Call Whisper API
    import mimetypes
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"

    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="model"\r\n\r\n'
    body += b"whisper-1\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="response_format"\r\n\r\n'
    body += b"verbose_json\r\n"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'.encode()
    body += b"Content-Type: audio/mpeg\r\n\r\n"
    body += audio_path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )

    segments = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for seg in data.get("segments", []):
                segments.append({
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                    "text": seg.get("text", "").strip(),
                })
            log.info("Transcribed %d segments from %s", len(segments), video_path.name)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Whisper API HTTP %d: %s", e.code, error_body)
    except Exception as e:
        log.error("Whisper API failed: %s", e)

    # Cleanup
    audio_path.unlink(missing_ok=True)
    return segments


def analyze_video(video_path: Path, work_dir: Path,
                  gemini_key: str, openai_key: str = "",
                  transcribe: bool = False) -> dict:
    """Full analysis pipeline for a single video file.

    Args:
        transcribe: if True, run Whisper transcription (costs ~$0.006/min).
                    Off by default — enable when user wants to preserve dialogue.

    Returns a dict with video info, frame analyses, and transcription.
    """
    info = _get_video_info(video_path)
    log.info("Analyzing %s (%.1fs, %dx%d)",
             video_path.name, info["duration"], info["width"], info["height"])

    # Extract and analyze frames
    frames = extract_frames(video_path, work_dir / "frames")
    if frames:
        frames = analyze_frames_gemini(frames, gemini_key)

    # Transcribe audio (only when explicitly requested)
    transcript = []
    if transcribe and openai_key:
        transcript = transcribe_audio_openai(video_path, openai_key)
    elif transcribe and not openai_key:
        log.warning("Transcription requested but no OpenAI API key")

    return {
        "file": video_path.name,
        "path": str(video_path),
        "info": info,
        "frames": frames,
        "transcript": transcript,
    }


def analyze_all(input_dir: Path, work_dir: Path,
                gemini_key: str, openai_key: str = "",
                transcribe: bool = False) -> dict:
    """Analyze all video files in a directory.

    Args:
        transcribe: if True, run Whisper on each video. Off by default.

    Returns a scene_log dict ready to write as JSON.
    """
    videos = sorted([
        f for f in input_dir.iterdir()
        if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith(".")
    ])

    if not videos:
        log.warning("No video files found in %s", input_dir)
        return {"videos": [], "scenes": []}

    log.info("Found %d video files in %s", len(videos), input_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    all_videos = []
    all_scenes = []

    for video_path in videos:
        result = analyze_video(video_path, work_dir / video_path.stem,
                               gemini_key, openai_key, transcribe=transcribe)
        all_videos.append({
            "file": result["file"],
            "path": result["path"],
            "duration": result["info"]["duration"],
            "resolution": f"{result['info']['width']}x{result['info']['height']}",
        })

        # Build scene entries from frame analysis
        for frame in result.get("frames", []):
            if frame.get("quality", 0) >= 2:  # skip unusable frames
                scene = {
                    "file": result["file"],
                    "timestamp": frame.get("timestamp", 0),
                    "timestamp_str": frame.get("timestamp_str", ""),
                    "description": frame.get("description", ""),
                    "location_type": frame.get("location_type", ""),
                    "subjects": frame.get("subjects", ""),
                    "mood": frame.get("mood", ""),
                    "quality": frame.get("quality", 3),
                    "highlights": frame.get("highlights", ""),
                    "notes": frame.get("notes", ""),
                    "key_frame": frame.get("path", ""),
                }
                all_scenes.append(scene)

        # Add transcript as metadata
        if result.get("transcript"):
            for seg in result["transcript"]:
                all_scenes.append({
                    "file": result["file"],
                    "timestamp": seg["start"],
                    "timestamp_str": _format_ts(seg["start"]),
                    "description": f"[AUDIO] {seg['text']}",
                    "type": "transcript",
                    "quality": 3,
                })

    # Sort all scenes by file then timestamp
    all_scenes.sort(key=lambda s: (s.get("file", ""), s.get("timestamp", 0)))

    scene_log = {
        "input_dir": str(input_dir),
        "video_count": len(videos),
        "total_duration": sum(v["duration"] for v in all_videos),
        "videos": all_videos,
        "scenes": all_scenes,
    }

    # Save to work_dir
    log_path = work_dir / "scene_log.json"
    log_path.write_text(json.dumps(scene_log, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    log.info("Scene log saved: %s (%d scenes)", log_path, len(all_scenes))

    return scene_log


def _format_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"
