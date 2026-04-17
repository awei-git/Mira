"""Scene analyzer — analyze video with Gemini native video understanding.

Phase 1 of the video editing pipeline:
  video files → Gemini File API upload → native video analysis → scene_log.json

Uses Gemini's native video input for motion-aware understanding (camera movement,
actions, pacing, transitions) instead of static frame analysis. Falls back to
frame-based analysis for files exceeding the File API size limit.
"""

import base64
import json
import logging
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

from config import (
    VIDEO_FILE_MAX_BYTES,
    VIDEO_FILE_UPLOAD_TIMEOUT,
    VIDEO_FILE_POLL_INTERVAL,
    VIDEO_FILE_POLL_TIMEOUT,
    GEMINI_VIDEO_MODEL,
    GEMINI_FRAME_MODEL,
    GEMINI_SCENE_ANALYZER_MAX_TOKENS,
    GEMINI_FRAME_ANALYZER_MAX_TOKENS,
    GEMINI_SCENE_TEMPERATURE,
    VIDEO_MAX_FRAMES,
)

log = logging.getLogger("video.scene_analyzer")

# Video extensions to scan
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".mpg", ".wmv"}

# Gemini File API limits (from config)
_FILE_API_MAX_BYTES = VIDEO_FILE_MAX_BYTES
_FILE_API_UPLOAD_TIMEOUT = VIDEO_FILE_UPLOAD_TIMEOUT
_FILE_API_POLL_INTERVAL = VIDEO_FILE_POLL_INTERVAL
_FILE_API_POLL_TIMEOUT = VIDEO_FILE_POLL_TIMEOUT

# MIME type mapping
_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".m4v": "video/x-m4v",
    ".mts": "video/mp2t",
    ".mpg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
}


# ---------------------------------------------------------------------------
# Video info (unchanged)
# ---------------------------------------------------------------------------


def _get_video_info(video_path: Path) -> dict:
    """Get video duration, resolution, fps via ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))

        width, height, fps = 0, 0, 30.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
                r = s.get("r_frame_rate", "30/1")
                parts = r.split("/")
                if len(parts) == 2 and int(parts[1]) > 0:
                    fps = int(parts[0]) / int(parts[1])
                break

        return {"duration": duration, "width": width, "height": height, "fps": fps}
    except Exception as e:
        log.warning("ffprobe failed for %s: %s", video_path.name, e)
        return {"duration": 0, "width": 0, "height": 0, "fps": 30.0}


# ---------------------------------------------------------------------------
# Gemini File API — upload and manage video files
# ---------------------------------------------------------------------------


def _upload_to_file_api(video_path: Path, api_key: str) -> str | None:
    """Upload a video file to Gemini File API. Returns the file URI or None."""
    file_size = video_path.stat().st_size
    mime_type = _MIME_TYPES.get(video_path.suffix.lower(), "video/mp4")

    log.info("Uploading %s (%.1f MB) to Gemini File API...", video_path.name, file_size / (1024 * 1024))

    # Step 1: Initiate resumable upload
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files" f"?key={api_key}"
    metadata = json.dumps({"file": {"display_name": video_path.stem}}).encode("utf-8")

    init_req = urllib.request.Request(init_url, data=metadata, method="POST")
    init_req.add_header("X-Goog-Upload-Protocol", "resumable")
    init_req.add_header("X-Goog-Upload-Command", "start")
    init_req.add_header("X-Goog-Upload-Header-Content-Length", str(file_size))
    init_req.add_header("X-Goog-Upload-Header-Content-Type", mime_type)
    init_req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(init_req, timeout=30) as resp:
            # Header name may be mixed case depending on server
            upload_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("x-goog-upload-url")
            if not upload_url:
                log.error("No upload URL in response headers: %s", dict(resp.headers))
                return None
    except Exception as e:
        log.error("File API init failed: %s", e)
        return None

    # Step 2: Upload file content (POST to the resumable URL)
    file_data = video_path.read_bytes()
    upload_req = urllib.request.Request(upload_url, data=file_data, method="POST")
    upload_req.add_header("Content-Length", str(file_size))
    upload_req.add_header("X-Goog-Upload-Offset", "0")
    upload_req.add_header("X-Goog-Upload-Command", "upload, finalize")

    try:
        with urllib.request.urlopen(upload_req, timeout=_FILE_API_UPLOAD_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            file_info = result.get("file", {})
            file_uri = file_info.get("uri", "")
            file_name = file_info.get("name", "")
            state = file_info.get("state", "")

            log.info("Upload complete: %s (state: %s)", file_name, state)
    except Exception as e:
        log.error("File upload failed: %s", e)
        return None

    # Step 3: Wait for processing if needed
    if state == "PROCESSING":
        file_uri = _wait_for_processing(file_name, api_key)

    if not file_uri:
        log.error("No file URI after upload")
        return None

    return file_uri


def _wait_for_processing(file_name: str, api_key: str) -> str | None:
    """Poll until file state is ACTIVE."""
    log.info("Waiting for video processing...")
    start = time.time()

    while time.time() - start < _FILE_API_POLL_TIMEOUT:
        time.sleep(_FILE_API_POLL_INTERVAL)

        url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}" f"?key={api_key}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                state = data.get("state", "")
                if state == "ACTIVE":
                    log.info("Video processing complete: %s", file_name)
                    return data.get("uri", "")
                elif state == "FAILED":
                    log.error("Video processing failed: %s", data.get("error", {}))
                    return None
                log.debug("Still processing... (%ds)", int(time.time() - start))
        except Exception as e:
            log.warning("Poll error: %s", e)

    log.error("Video processing timed out after %ds", _FILE_API_POLL_TIMEOUT)
    return None


def _delete_file(file_name: str, api_key: str):
    """Clean up uploaded file from Gemini storage."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}" f"?key={api_key}"
    try:
        req = urllib.request.Request(url, method="DELETE")
        urllib.request.urlopen(req, timeout=10)
        log.debug("Deleted remote file: %s", file_name)
    except Exception:
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Native video analysis (new — Gemini Pro with video input)
# ---------------------------------------------------------------------------

_VIDEO_ANALYSIS_PROMPT = """You are an expert video editor analyzing raw footage for editing.

Analyze this video comprehensively. For each distinct scene or shot, provide:

Return a JSON object with this structure:
{
  "scenes": [
    {
      "start_time": "MM:SS",
      "end_time": "MM:SS",
      "description": "what's happening — people, actions, setting",
      "location_type": "beach/city/mountain/indoor/outdoor/etc",
      "subjects": "people/landscape/food/architecture/etc",
      "mood": "peaceful/exciting/contemplative/joyful/dramatic/etc",
      "quality": 4,
      "camera_motion": "static/pan_left/pan_right/tilt_up/tilt_down/zoom_in/zoom_out/tracking/handheld/drone",
      "highlights": "what makes this shot interesting or worth keeping",
      "editing_notes": "suggested use — opening, b-roll, climax, transition point, etc",
      "action_intensity": "low/medium/high",
      "audio_notes": "ambient sound, speech, music, silence, wind, etc",
      "lighting_type": "golden_hour/blue_hour/overcast/harsh_midday/indoor_warm/indoor_cool/night/underwater/mixed",
      "color_temperature_est": "warm/neutral/cool",
      "dominant_colors": ["top color 1", "top color 2", "top color 3"],
      "usability_score": 4,
      "best_segment": {"start": "MM:SS", "end": "MM:SS"}
    }
  ],
  "overall": {
    "dominant_mood": "the overall feel",
    "best_moments": ["MM:SS - brief description", ...],
    "suggested_narrative": "brief 1-2 sentence narrative suggestion",
    "pacing_notes": "natural rhythm of the footage — where it's fast, where it slows"
  }
}

Quality: 1=unusable (blurry/dark/shaky), 3=ok, 5=stunning.
usability_score: 1=unusable, 2=poor (shaky/out-of-focus), 3=ok, 4=good (sharp, stable, well-composed), 5=excellent.
best_segment: the single best continuous segment within this shot for editing. If the whole shot is good, use the full start/end times.
lighting_type: classify the dominant lighting condition.
Be precise with timestamps. Identify EVERY distinct shot/scene change.
Return ONLY the JSON, no other text."""


def analyze_video_native(video_path: Path, api_key: str) -> list[dict]:
    """Analyze a video using Gemini's native video understanding.

    Uploads the video file, runs analysis with temporal and motion awareness,
    returns a list of scene dicts compatible with the existing pipeline.
    """
    file_size = video_path.stat().st_size
    if file_size > _FILE_API_MAX_BYTES:
        log.warning(
            "%s too large (%.1f GB) for File API, falling back to frames", video_path.name, file_size / (1024**3)
        )
        return []  # caller will fall back to frame-based

    # Upload
    file_uri = _upload_to_file_api(video_path, api_key)
    if not file_uri:
        log.warning("Upload failed for %s, falling back to frames", video_path.name)
        return []

    # Analyze with Gemini Pro
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_VIDEO_MODEL}:generateContent?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "file_data": {
                            "file_uri": file_uri,
                            "mime_type": _MIME_TYPES.get(video_path.suffix.lower(), "video/mp4"),
                        }
                    },
                    {"text": _VIDEO_ANALYSIS_PROMPT},
                ],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": GEMINI_SCENE_ANALYZER_MAX_TOKENS,
            "temperature": GEMINI_SCENE_TEMPERATURE,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    scenes = []
    try:
        # Longer timeout — video analysis can take a while
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))

            # Track tokens (~295 tokens/sec for video+audio)
            usage = data.get("usageMetadata", {})
            in_tokens = usage.get("promptTokenCount", 0)
            out_tokens = usage.get("candidatesTokenCount", 0)
            # Gemini 2.5 Pro pricing: $1.25/M input, $10/M output
            cost = in_tokens * 1.25 / 1_000_000 + out_tokens * 10.0 / 1_000_000
            log.info("Native video analysis: %d in / %d out tokens ($%.4f)", in_tokens, out_tokens, cost)

            text = data["candidates"][0]["content"]["parts"][0]["text"]

            # Parse JSON
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                analysis = json.loads(match.group())
                for s in analysis.get("scenes", []):
                    scenes.append(
                        {
                            "file": video_path.name,
                            "timestamp": _parse_ts(s.get("start_time", "0:00")),
                            "end_timestamp": _parse_ts(s.get("end_time", "0:00")),
                            "timestamp_str": s.get("start_time", ""),
                            "end_timestamp_str": s.get("end_time", ""),
                            "description": s.get("description", ""),
                            "location_type": s.get("location_type", ""),
                            "subjects": s.get("subjects", ""),
                            "mood": s.get("mood", ""),
                            "quality": s.get("quality", 3),
                            "camera_motion": s.get("camera_motion", ""),
                            "highlights": s.get("highlights", ""),
                            "notes": s.get("editing_notes", ""),
                            "action_intensity": s.get("action_intensity", ""),
                            "audio_notes": s.get("audio_notes", ""),
                            "type": "native_video",
                        }
                    )

                # Store overall analysis
                overall = analysis.get("overall", {})
                if overall:
                    scenes.append(
                        {
                            "file": video_path.name,
                            "timestamp": -1,
                            "type": "overall_analysis",
                            "description": overall.get("suggested_narrative", ""),
                            "highlights": ", ".join(overall.get("best_moments", [])),
                            "notes": overall.get("pacing_notes", ""),
                            "mood": overall.get("dominant_mood", ""),
                            "quality": 0,  # marker, not a real scene
                        }
                    )

                log.info(
                    "Native analysis: %d scenes from %s",
                    len([s for s in scenes if s["type"] == "native_video"]),
                    video_path.name,
                )
            else:
                log.warning("Could not parse native video analysis JSON")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Gemini native video HTTP %d: %s", e.code, error_body)
    except Exception as e:
        log.error("Gemini native video analysis failed: %s", e)

    # Cleanup remote file (best-effort)
    # file_uri format: https://generativelanguage.googleapis.com/v1beta/files/abc-123
    # _delete_file expects "files/abc-123"
    try:
        parts = file_uri.rstrip("/").split("/")
        # Find "files" in the path and take from there
        if "files" in parts:
            idx = parts.index("files")
            file_name = "/".join(parts[idx:])
            _delete_file(file_name, api_key)
    except Exception:
        pass

    return scenes


# ---------------------------------------------------------------------------
# Frame extraction (kept for thumbnails + fallback)
# ---------------------------------------------------------------------------


def extract_frames(
    video_path: Path,
    output_dir: Path,
    interval: float = 2.0,
    scene_threshold: float = 0.3,
    max_frames: int = VIDEO_MAX_FRAMES,
) -> list[dict]:
    """Extract key frames from a video using ffmpeg.

    Used for: (1) thumbnails in scene_log, (2) fallback when native video fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem

    info = _get_video_info(video_path)
    duration = info["duration"]
    if duration <= 0:
        log.warning("Could not determine duration for %s", video_path.name)
        return []

    if duration > 120:
        interval = max(interval, duration / max_frames)

    frames = []

    # Fixed-interval extraction
    interval_dir = output_dir / f"{stem}_interval"
    interval_dir.mkdir(exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval},scale=1280:-1",
        "-q:v",
        "3",
        str(interval_dir / f"{stem}_%04d.jpg"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("Frame extraction failed for %s: %s", video_path.name, e)
        return []

    for f in sorted(interval_dir.glob("*.jpg")):
        idx = int(f.stem.split("_")[-1]) - 1
        ts = idx * interval
        frames.append(
            {
                "path": str(f),
                "timestamp": ts,
                "timestamp_str": _format_ts(ts),
                "type": "interval",
                "source_file": video_path.name,
            }
        )

    # Scene change detection
    scene_dir = output_dir / f"{stem}_scene"
    scene_dir.mkdir(exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"select='gt(scene,{scene_threshold})',scale=1280:-1",
        "-vsync",
        "vfr",
        "-q:v",
        "3",
        str(scene_dir / f"{stem}_scene_%04d.jpg"),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, Exception) as e:
        log.warning("Scene detection failed for %s: %s", video_path.name, e)

    for f in sorted(scene_dir.glob("*.jpg")):
        frames.append(
            {
                "path": str(f),
                "timestamp": -1,
                "timestamp_str": "scene_change",
                "type": "scene_change",
                "source_file": video_path.name,
            }
        )

    if len(frames) > max_frames:
        step = len(frames) / max_frames
        selected = [frames[int(i * step)] for i in range(max_frames)]
        frames = selected

    log.info("Extracted %d frames from %s (%.1fs)", len(frames), video_path.name, duration)
    return frames


# ---------------------------------------------------------------------------
# Frame-based analysis (fallback for large files)
# ---------------------------------------------------------------------------


def analyze_frames_gemini(frames: list[dict], api_key: str, batch_size: int = 8) -> list[dict]:
    """Analyze frames using Gemini Vision API (fallback mode).

    Sends frames in batches. Returns enriched frame dicts.
    """
    if not api_key:
        log.error("No Gemini API key")
        return frames

    results = []
    total_input_tokens = 0
    total_output_tokens = 0
    batches = [frames[i : i + batch_size] for i in range(0, len(frames), batch_size)]

    for batch_idx, batch in enumerate(batches):
        log.info("Analyzing frame batch %d/%d (%d frames) [fallback mode]", batch_idx + 1, len(batches), len(batch))

        parts = [
            {
                "text": (
                    "You are analyzing frames from a video for editing. "
                    "For each frame, provide a JSON array with one object per frame:\n"
                    "[\n"
                    '  {"frame_idx": 0, "description": "brief scene description", '
                    '"location_type": "beach/city/mountain/indoor/etc", '
                    '"subjects": "people/landscape/food/etc", '
                    '"mood": "peaceful/exciting/contemplative/joyful/etc", '
                    '"quality": 4, '
                    '"highlights": "what makes this frame interesting or worth keeping", '
                    '"notes": "any editing notes - camera motion, lighting, etc"}\n'
                    "]\n"
                    "Quality 1=unusable (blurry/dark), 3=ok, 5=stunning.\n"
                    "Be concise. Return ONLY the JSON array."
                )
            }
        ]

        for i, frame in enumerate(batch):
            frame_path = Path(frame["path"])
            if not frame_path.exists():
                continue
            try:
                img_data = frame_path.read_bytes()
                b64 = base64.b64encode(img_data).decode("utf-8")
                parts.append(
                    {
                        "text": f"\n--- Frame {i} [{frame.get('timestamp_str', '?')}] from {frame.get('source_file', '?')} ---"
                    }
                )
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64,
                        }
                    }
                )
            except Exception as e:
                log.warning("Could not read frame %s: %s", frame_path, e)

        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_FRAME_MODEL}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": GEMINI_FRAME_ANALYZER_MAX_TOKENS,
                "temperature": GEMINI_SCENE_TEMPERATURE,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["candidates"][0]["content"]["parts"][0]["text"]

                usage = data.get("usageMetadata", {})
                batch_in = usage.get("promptTokenCount", 0)
                batch_out = usage.get("candidatesTokenCount", 0)
                total_input_tokens += batch_in
                total_output_tokens += batch_out
                log.info("Batch %d tokens: %d in / %d out", batch_idx + 1, batch_in, batch_out)

                match = re.search(r"\[.*\]", text, re.DOTALL)
                if match:
                    analyses = json.loads(match.group())
                    for a in analyses:
                        idx = a.get("frame_idx", 0)
                        if idx < len(batch):
                            batch[idx].update(
                                {
                                    "description": a.get("description", ""),
                                    "location_type": a.get("location_type", ""),
                                    "subjects": a.get("subjects", ""),
                                    "mood": a.get("mood", ""),
                                    "quality": a.get("quality", 3),
                                    "highlights": a.get("highlights", ""),
                                    "notes": a.get("notes", ""),
                                }
                            )
                else:
                    log.warning("Could not parse frame analysis JSON")

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")[:300]
            log.error("Gemini vision HTTP %d: %s", e.code, error_body)
        except Exception as e:
            log.error("Gemini vision failed: %s", e)

        results.extend(batch)

    log.info(
        "Frame analysis total tokens: %d input / %d output (%.4f USD est.)",
        total_input_tokens,
        total_output_tokens,
        total_input_tokens * 0.15 / 1_000_000 + total_output_tokens * 0.6 / 1_000_000,
    )
    return results


# ---------------------------------------------------------------------------
# Audio transcription (unchanged)
# ---------------------------------------------------------------------------


def transcribe_audio_openai(video_path: Path, api_key: str) -> list[dict]:
    """Extract and transcribe audio using OpenAI Whisper API."""
    if not api_key:
        log.warning("No OpenAI API key, skipping transcription")
        return []

    audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "4",
        "-ac",
        "1",
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

    file_size = audio_path.stat().st_size
    if file_size > 25 * 1024 * 1024:
        log.warning("Audio file too large (%dMB), skipping transcription", file_size // (1024 * 1024))
        audio_path.unlink(missing_ok=True)
        return []

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
                segments.append(
                    {
                        "start": seg.get("start", 0),
                        "end": seg.get("end", 0),
                        "text": seg.get("text", "").strip(),
                    }
                )
            log.info("Transcribed %d segments from %s", len(segments), video_path.name)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Whisper API HTTP %d: %s", e.code, error_body)
    except Exception as e:
        log.error("Whisper API failed: %s", e)

    audio_path.unlink(missing_ok=True)
    return segments


# ---------------------------------------------------------------------------
# Main analysis pipeline (upgraded)
# ---------------------------------------------------------------------------


def analyze_video(
    video_path: Path,
    work_dir: Path,
    gemini_key: str,
    openai_key: str = "",
    transcribe: bool = False,
    force_frames: bool = False,
) -> dict:
    """Full analysis pipeline for a single video file.

    Tries native video analysis first (Gemini Pro + File API).
    Falls back to frame-based analysis (Gemini Flash) if:
      - File too large for File API (>2GB)
      - Upload or analysis fails
      - force_frames=True

    Args:
        transcribe: if True, run Whisper transcription.
        force_frames: skip native video, use frame-based analysis.
    """
    info = _get_video_info(video_path)
    log.info("Analyzing %s (%.1fs, %dx%d)", video_path.name, info["duration"], info["width"], info["height"])

    scenes = []
    analysis_mode = "none"

    # Try native video analysis first
    if not force_frames and gemini_key:
        scenes = analyze_video_native(video_path, gemini_key)
        if scenes:
            analysis_mode = "native"
            log.info("Using native video analysis for %s", video_path.name)

    # Fallback to frame-based
    if not scenes:
        log.info("Using frame-based analysis for %s", video_path.name)
        analysis_mode = "frames"
        frames = extract_frames(video_path, work_dir / "frames")
        if frames:
            frames = analyze_frames_gemini(frames, gemini_key)
            # Convert frames to scene format for consistency
            for f in frames:
                scenes.append(
                    {
                        "file": video_path.name,
                        "timestamp": f.get("timestamp", 0),
                        "timestamp_str": f.get("timestamp_str", ""),
                        "description": f.get("description", ""),
                        "location_type": f.get("location_type", ""),
                        "subjects": f.get("subjects", ""),
                        "mood": f.get("mood", ""),
                        "quality": f.get("quality", 3),
                        "highlights": f.get("highlights", ""),
                        "notes": f.get("notes", ""),
                        "key_frame": f.get("path", ""),
                        "type": "frame",
                    }
                )

    # Always extract a few key frames for thumbnails (even in native mode)
    if analysis_mode == "native":
        thumb_frames = extract_frames(
            video_path, work_dir / "frames", interval=max(info["duration"] / 10, 5), max_frames=10
        )
        # Match thumbnails to native scenes by nearest timestamp
        for scene in scenes:
            if scene.get("type") != "native_video":
                continue
            ts = scene.get("timestamp", 0)
            best = min(thumb_frames, key=lambda f: abs(f["timestamp"] - ts), default=None)
            if best:
                scene["key_frame"] = best.get("path", "")

    # Transcribe audio
    transcript = []
    if transcribe and openai_key:
        transcript = transcribe_audio_openai(video_path, openai_key)
    elif transcribe and not openai_key:
        log.warning("Transcription requested but no OpenAI API key")

    return {
        "file": video_path.name,
        "path": str(video_path),
        "info": info,
        "scenes": scenes,
        "transcript": transcript,
        "analysis_mode": analysis_mode,
    }


def analyze_all(
    input_dir: Path,
    work_dir: Path,
    gemini_key: str,
    openai_key: str = "",
    transcribe: bool = False,
    force_frames: bool = False,
) -> dict:
    """Analyze all video files in a directory.

    Returns a scene_log dict ready to write as JSON.
    """
    videos = sorted([f for f in input_dir.iterdir() if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith(".")])

    if not videos:
        log.warning("No video files found in %s", input_dir)
        return {"videos": [], "scenes": []}

    log.info("Found %d video files in %s", len(videos), input_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    all_videos = []
    all_scenes = []

    for video_path in videos:
        result = analyze_video(
            video_path,
            work_dir / video_path.stem,
            gemini_key,
            openai_key,
            transcribe=transcribe,
            force_frames=force_frames,
        )
        all_videos.append(
            {
                "file": result["file"],
                "path": result["path"],
                "duration": result["info"]["duration"],
                "resolution": f"{result['info']['width']}x{result['info']['height']}",
                "analysis_mode": result.get("analysis_mode", "unknown"),
            }
        )

        # Add scenes (already in unified format from analyze_video)
        for scene in result.get("scenes", []):
            if scene.get("quality", 0) >= 2 or scene.get("type") == "overall_analysis":
                all_scenes.append(scene)

        # Add transcript
        if result.get("transcript"):
            for seg in result["transcript"]:
                all_scenes.append(
                    {
                        "file": result["file"],
                        "timestamp": seg["start"],
                        "timestamp_str": _format_ts(seg["start"]),
                        "description": f"[AUDIO] {seg['text']}",
                        "type": "transcript",
                        "quality": 3,
                    }
                )

    # Sort by file then timestamp
    all_scenes.sort(key=lambda s: (s.get("file", ""), s.get("timestamp", 0)))

    scene_log = {
        "input_dir": str(input_dir),
        "video_count": len(videos),
        "total_duration": sum(v["duration"] for v in all_videos),
        "videos": all_videos,
        "scenes": all_scenes,
    }

    # Save
    log_path = work_dir / "scene_log.json"
    log_path.write_text(json.dumps(scene_log, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Scene log saved: %s (%d scenes)", log_path, len(all_scenes))

    return scene_log


# ---------------------------------------------------------------------------
# V2: Supercut analysis (single Gemini call for all footage)
# ---------------------------------------------------------------------------


def build_proxy_supercut(clips: list[dict], work_dir: Path, scale: int = 720) -> Path | None:
    """Build a 720p proxy supercut from triaged clips with burned-in labels.

    Each clip gets its filename + index overlaid so Gemini can reference them.
    Only includes clips that passed triage (not rejected).

    Args:
        clips: list of triage clip dicts (must have 'path' and 'file' keys)
        work_dir: output directory
        scale: target height (720 for analysis, saves upload time)

    Returns:
        Path to proxy supercut, or None on failure.
    """
    kept = [c for c in clips if not c.get("reject", False)]
    if not kept:
        log.warning("No clips to build supercut from")
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    proxy_dir = work_dir / "proxy_clips"
    proxy_dir.mkdir(exist_ok=True)

    # Render each clip as a short proxy with label overlay
    proxy_paths = []
    clip_index = []  # track offset for timestamp mapping

    for i, clip in enumerate(kept):
        src = Path(clip["path"])
        if not src.exists():
            continue

        out = proxy_dir / f"proxy_{i:04d}.mp4"
        label = f"{clip['file']} [{i}]"
        # Escape special chars for ffmpeg drawtext
        label_safe = label.replace("'", "\\'").replace(":", "\\:")

        dur = clip.get("info", {}).get("duration", 30)
        # Cap each clip at 30s for the proxy (enough for Gemini to analyze)
        max_dur = min(dur, 30)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-t",
            str(max_dur),
            "-vf",
            (
                f"scale=-2:{scale},"
                f"drawtext=text='{label_safe}'"
                f":fontcolor=white:fontsize=24:x=10:y=10"
                f":borderw=2:bordercolor=black"
                f":font=Helvetica"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ac",
            "1",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]

        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
            if out.exists() and out.stat().st_size > 1000:
                proxy_paths.append(out)
                clip_index.append(
                    {
                        "idx": i,
                        "file": clip["file"],
                        "proxy_clip": out.name,
                        "original_duration": dur,
                        "proxy_duration": max_dur,
                    }
                )
        except Exception as e:
            log.warning("Proxy render failed for %s: %s", clip["file"], e)

    if not proxy_paths:
        log.error("No proxy clips rendered")
        return None

    # Concat all proxies
    concat_path = work_dir / "proxy_concat.txt"
    with open(concat_path, "w") as f:
        for p in proxy_paths:
            f.write(f"file '{p}'\n")

    supercut_path = work_dir / "proxy_supercut.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        str(supercut_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=300)
    except Exception as e:
        log.error("Supercut concat failed: %s", e)
        return None

    if not supercut_path.exists():
        return None

    # Save clip index for timestamp mapping
    index_path = work_dir / "proxy_clip_index.json"
    # Compute running offsets
    offset = 0.0
    for entry in clip_index:
        entry["supercut_offset"] = round(offset, 2)
        offset += entry["proxy_duration"]

    index_path.write_text(json.dumps(clip_index, indent=2, ensure_ascii=False))

    size_mb = supercut_path.stat().st_size / (1024 * 1024)
    log.info("Proxy supercut: %s (%.0f MB, %d clips, %.0fs)", supercut_path, size_mb, len(proxy_paths), offset)

    return supercut_path


_SUPERCUT_ANALYSIS_PROMPT = """You are an expert video editor analyzing raw footage for editing.

This is a supercut of multiple video clips concatenated together. Each clip has its filename and index burned in at the top-left corner (e.g. "DJI_20250819_082128_0060_D.MP4 [5]").

Analyze ALL clips in this supercut. For each distinct clip (identified by the burned-in label), provide:

Return a JSON object:
{
  "clips": [
    {
      "clip_idx": 0,
      "source_file": "exact filename from label",
      "description": "what's happening — people, actions, setting",
      "location_type": "beach/park/indoor/street/restaurant/playground/etc",
      "subjects": "people/child/family/landscape/food/etc",
      "mood": "peaceful/exciting/contemplative/joyful/dramatic/playful/etc",
      "quality": 4,
      "usability_score": 4,
      "camera_motion": "static/pan_left/pan_right/tilt_up/tilt_down/tracking/handheld",
      "action_intensity": "low/medium/high",
      "lighting_type": "golden_hour/overcast/indoor_warm/indoor_cool/night/harsh_midday",
      "color_temperature_est": "warm/neutral/cool",
      "highlights": "what makes this clip interesting or worth keeping",
      "editing_notes": "suggested use — opening, b-roll, climax, emotional moment, etc",
      "audio_notes": "speech, ambient, music, wind, silence, child laughing, etc",
      "best_segment": {"start_pct": 0, "end_pct": 100}
    }
  ],
  "overall": {
    "dominant_mood": "the overall feel of this footage collection",
    "content_type": "family/travel/event/nature/urban/etc",
    "suggested_narrative": "how these clips could be assembled into a story (2-3 sentences)",
    "best_clips": [0, 5, 12],
    "groupings": [
      {"name": "group name", "clip_indices": [0, 1, 2], "reason": "why they belong together"}
    ]
  }
}

Quality: 1=unusable, 2=poor, 3=ok, 4=good, 5=excellent.
usability_score: 1=unusable (shaky/OOF), 2=poor, 3=ok, 4=good (sharp, stable), 5=excellent.
best_segment: percentage range of the clip that's best for editing (0-100).

Be thorough — analyze EVERY clip you can see. Return ONLY the JSON."""


def analyze_supercut(supercut_path: Path, clip_index: list[dict], api_key: str, work_dir: Path) -> dict:
    """Analyze a proxy supercut with a single Gemini Pro call.

    Args:
        supercut_path: path to the proxy supercut video
        clip_index: list of clip metadata (from build_proxy_supercut)
        api_key: Gemini API key
        work_dir: where to save scene_log.json

    Returns:
        scene_log dict compatible with existing pipeline.
    """
    if not supercut_path.exists():
        log.error("Supercut not found: %s", supercut_path)
        return {"videos": [], "scenes": []}

    # Upload supercut
    file_uri = _upload_to_file_api(supercut_path, api_key)
    if not file_uri:
        log.error("Supercut upload failed")
        return {"videos": [], "scenes": []}

    # Single analysis call
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_VIDEO_MODEL}:generateContent?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "file_data": {
                            "file_uri": file_uri,
                            "mime_type": "video/mp4",
                        }
                    },
                    {"text": _SUPERCUT_ANALYSIS_PROMPT},
                ],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 16384,
            "temperature": 0.2,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    analysis = {}
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))

            usage = data.get("usageMetadata", {})
            in_tok = usage.get("promptTokenCount", 0)
            out_tok = usage.get("candidatesTokenCount", 0)
            cost = in_tok * 1.25 / 1_000_000 + out_tok * 10.0 / 1_000_000
            log.info("Supercut analysis: %d in / %d out tokens ($%.4f)", in_tok, out_tok, cost)

            candidates = data.get("candidates", [])
            if not candidates:
                log.error("Gemini returned no candidates (possible safety block or empty response)")
            else:
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    log.error("Gemini returned empty parts (output may have been truncated)")
                else:
                    text = parts[0].get("text", "")
                    if text:
                        analysis = _parse_json_robust(text)
                        if not analysis:
                            log.error("Could not parse supercut analysis JSON")
                    else:
                        log.error("Gemini returned empty text")

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Gemini supercut HTTP %d: %s", e.code, error_body)
    except Exception as e:
        log.error("Gemini supercut analysis failed: %s", e)

    # Cleanup remote file
    try:
        parts = file_uri.rstrip("/").split("/")
        if "files" in parts:
            idx = parts.index("files")
            file_name = "/".join(parts[idx:])
            _delete_file(file_name, api_key)
    except Exception:
        pass

    # Convert analysis to scene_log format
    return _supercut_to_scene_log(analysis, clip_index, work_dir)


def _supercut_to_scene_log(analysis: dict, clip_index: list[dict], work_dir: Path) -> dict:
    """Convert supercut analysis JSON to standard scene_log format."""
    all_videos = []
    all_scenes = []

    # Build lookup from clip_index
    idx_lookup = {entry["idx"]: entry for entry in clip_index}

    for clip_data in analysis.get("clips", []):
        cidx = clip_data.get("clip_idx", -1)
        src_file = clip_data.get("source_file", "")

        # Find matching clip_index entry
        index_entry = idx_lookup.get(cidx, {})
        if not index_entry and src_file:
            # Try matching by filename
            for entry in clip_index:
                if entry["file"] == src_file:
                    index_entry = entry
                    break

        original_dur = index_entry.get("original_duration", 30)

        # Convert best_segment percentages to timestamps
        best_seg = clip_data.get("best_segment", {})
        start_pct = best_seg.get("start_pct", 0) / 100
        end_pct = best_seg.get("end_pct", 100) / 100
        best_start = original_dur * start_pct
        best_end = original_dur * end_pct

        scene = {
            "file": src_file or index_entry.get("file", f"clip_{cidx}"),
            "clip_idx": cidx,
            "timestamp": best_start,
            "end_timestamp": best_end,
            "timestamp_str": _format_ts(best_start),
            "end_timestamp_str": _format_ts(best_end),
            "description": clip_data.get("description", ""),
            "location_type": clip_data.get("location_type", ""),
            "subjects": clip_data.get("subjects", ""),
            "mood": clip_data.get("mood", ""),
            "quality": clip_data.get("quality", 3),
            "usability_score": clip_data.get("usability_score", 3),
            "camera_motion": clip_data.get("camera_motion", ""),
            "action_intensity": clip_data.get("action_intensity", ""),
            "lighting_type": clip_data.get("lighting_type", "mixed"),
            "color_temperature_est": clip_data.get("color_temperature_est", "neutral"),
            "highlights": clip_data.get("highlights", ""),
            "notes": clip_data.get("editing_notes", ""),
            "audio_notes": clip_data.get("audio_notes", ""),
            "best_segment": {
                "start": _format_ts(best_start),
                "end": _format_ts(best_end),
            },
            "type": "supercut_native",
        }
        all_scenes.append(scene)

        all_videos.append(
            {
                "file": scene["file"],
                "path": index_entry.get("path", ""),
                "duration": original_dur,
                "analysis_mode": "supercut",
            }
        )

    # Add overall analysis
    overall = analysis.get("overall", {})
    if overall:
        all_scenes.append(
            {
                "file": "_overall",
                "timestamp": -1,
                "type": "overall_analysis",
                "description": overall.get("suggested_narrative", ""),
                "highlights": str(overall.get("best_clips", [])),
                "notes": str(overall.get("groupings", [])),
                "mood": overall.get("dominant_mood", ""),
                "quality": 0,
            }
        )

    # Sort by clip_idx
    all_scenes.sort(key=lambda s: (s.get("clip_idx", 999), s.get("timestamp", 0)))

    scene_log = {
        "input_dir": "",
        "video_count": len(all_videos),
        "total_duration": sum(v.get("duration", 0) for v in all_videos),
        "analysis_mode": "supercut",
        "videos": all_videos,
        "scenes": all_scenes,
    }

    # Save
    log_path = work_dir / "scene_log.json"
    log_path.write_text(json.dumps(scene_log, ensure_ascii=False, indent=2))
    log.info("Scene log (supercut): %d clips analyzed", len(all_scenes) - 1)

    return scene_log


def analyze_all_v2(triage_result: dict, work_dir: Path, gemini_key: str) -> dict:
    """V2 entry point: build supercut from triaged clips, analyze in batched calls.

    Splits clips into batches of ~20 to keep each supercut under 5 minutes,
    preventing Gemini from choking on very long videos.

    Args:
        triage_result: dict from triage.triage_all()
        work_dir: working directory
        gemini_key: Gemini API key

    Returns:
        scene_log dict compatible with existing pipeline.
    """
    kept_clips = [c for c in triage_result.get("clips", []) if not c.get("reject", False)]

    if not kept_clips:
        log.error("No clips passed triage")
        return {"videos": [], "scenes": []}

    log.info("=== Phase 1 (v2): Supercut analysis of %d clips ===", len(kept_clips))

    # Check for cached scene_log
    scene_log_path = work_dir / "scene_log.json"
    if scene_log_path.exists():
        log.info("Using cached scene_log")
        return json.loads(scene_log_path.read_text())

    # Split into batches of ~20 clips (each batch ~5 min of proxy)
    _BATCH_SIZE = 20
    batches = [kept_clips[i : i + _BATCH_SIZE] for i in range(0, len(kept_clips), _BATCH_SIZE)]

    all_videos = []
    all_scenes = []

    for batch_idx, batch in enumerate(batches):
        log.info("Supercut batch %d/%d (%d clips)", batch_idx + 1, len(batches), len(batch))

        batch_dir = work_dir / f"batch_{batch_idx}"
        batch_dir.mkdir(exist_ok=True)

        # Build proxy supercut for this batch
        supercut_path = batch_dir / "proxy_supercut.mp4"
        clip_index_path = batch_dir / "proxy_clip_index.json"

        if supercut_path.exists() and clip_index_path.exists():
            log.info("Using cached batch %d proxy", batch_idx)
            clip_index = json.loads(clip_index_path.read_text())
        else:
            supercut_path = build_proxy_supercut(batch, batch_dir)
            if not supercut_path:
                log.warning("Batch %d supercut build failed, skipping", batch_idx)
                continue
            clip_index = json.loads(clip_index_path.read_text())

        # Analyze this batch
        batch_log = analyze_supercut(supercut_path, clip_index, gemini_key, batch_dir)

        # Merge into combined result
        for v in batch_log.get("videos", []):
            all_videos.append(v)
        for s in batch_log.get("scenes", []):
            all_scenes.append(s)

    # Sort scenes by clip_idx
    all_scenes.sort(key=lambda s: (s.get("clip_idx", 999), s.get("timestamp", 0)))

    scene_log = {
        "input_dir": triage_result.get("input_dir", ""),
        "video_count": len(all_videos),
        "total_duration": sum(v.get("duration", 0) for v in all_videos),
        "analysis_mode": "supercut_batched",
        "videos": all_videos,
        "scenes": all_scenes,
    }

    # Save combined scene_log
    scene_log_path.write_text(json.dumps(scene_log, ensure_ascii=False, indent=2))
    log.info(
        "Scene log (batched supercut): %d clips analyzed in %d batches",
        len([s for s in all_scenes if s.get("type") != "overall_analysis"]),
        len(batches),
    )

    return scene_log


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json_robust(text: str) -> dict:
    """Parse JSON from LLM output, handling common formatting issues."""
    # Strip markdown fences
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        json_str = text[start:end]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Common fixes: trailing commas, unescaped newlines in strings
        # Remove trailing commas before ] or }
        import re as _re

        fixed = _re.sub(r",\s*([}\]])", r"\1", json_str)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Try line-by-line repair: skip lines that cause parse errors
        # by finding the problematic line and removing it
        lines = fixed.split("\n")
        for attempt in range(5):
            try:
                return json.loads("\n".join(lines))
            except json.JSONDecodeError as e:
                if hasattr(e, "lineno") and e.lineno <= len(lines):
                    bad_line = e.lineno - 1
                    log.warning("JSON repair: removing line %d: %s", bad_line, lines[bad_line][:80])
                    lines.pop(bad_line)
                else:
                    break

        log.error("JSON parse failed after repair attempts")
    return {}


def _format_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    if seconds < 0:
        return "?"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _parse_ts(ts_str: str) -> float:
    """Parse MM:SS or HH:MM:SS to seconds."""
    try:
        parts = ts_str.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return 0
