"""Triage — fast local pre-filter for video clips.

Phase 0 of the v2 video pipeline:
    input_dir → ffmpeg/ffprobe analysis → triage.json

Runs entirely locally (no API calls). Rejects unusable footage
(too short, black frames, extreme blur, lens cap) and scores the rest
for prioritized analysis in Phase 1.
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("video.triage")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".mpg", ".wmv"}

# Rejection thresholds
_MIN_DURATION = 1.5          # seconds
_MIN_BRIGHTNESS = 15         # 0-255, below = lens cap / pure black
_MAX_BRIGHTNESS = 248        # above = pure white / overexposed
_MIN_BLUR_SCORE = 20.0       # laplacian variance, below = extremely blurry


def _ffprobe_info(path: Path) -> dict:
    """Get duration, resolution, fps, codec via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout)
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))

        width, height, fps = 0, 0, 30.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                width = int(s.get("width", 0))
                height = int(s.get("height", 0))
                fr = s.get("r_frame_rate", "30/1")
                parts = fr.split("/")
                if len(parts) == 2 and int(parts[1]) > 0:
                    fps = int(parts[0]) / int(parts[1])
                break

        has_audio = any(s.get("codec_type") == "audio"
                        for s in data.get("streams", []))

        return {
            "duration": duration, "width": width, "height": height,
            "fps": fps, "has_audio": has_audio,
        }
    except Exception as e:
        log.warning("ffprobe failed for %s: %s", path.name, e)
        return {"duration": 0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False}


def _extract_sample_frames(path: Path, work_dir: Path, n: int = 3) -> list[Path]:
    """Extract n sample frames at 10%, 50%, 90% of duration."""
    info = _ffprobe_info(path)
    dur = info["duration"]
    if dur < 0.5:
        return []

    positions = [dur * p for p in [0.1, 0.5, 0.9]]
    frames = []
    for i, ss in enumerate(positions[:n]):
        out = work_dir / f"{path.stem}_sample_{i}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(ss), "-i", str(path),
                 "-frames:v", "1", "-vf", "scale=320:-1", "-q:v", "5", str(out)],
                capture_output=True, timeout=10,
            )
            if out.exists() and out.stat().st_size > 500:
                frames.append(out)
        except Exception:
            pass
    return frames


def _compute_brightness(frame_path: Path) -> float:
    """Compute mean brightness of a frame (0-255)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-f", "lavfi",
             "-i", f"movie={frame_path},signalstats",
             "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
             "-of", "csv=p=0"],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        # Fallback: use ffmpeg to get mean brightness
        try:
            r = subprocess.run(
                ["ffmpeg", "-i", str(frame_path), "-vf",
                 "format=gray,stats_file=-", "-f", "null", "/dev/null"],
                capture_output=True, text=True, timeout=10,
            )
            # Parse mean from stats
            for line in r.stderr.split("\n"):
                if "mean" in line.lower():
                    parts = line.split()
                    for p in parts:
                        try:
                            return float(p)
                        except ValueError:
                            continue
        except Exception:
            pass
    return 128.0  # neutral default


def _compute_blur_score(frame_path: Path) -> float:
    """Compute blur score via laplacian variance. Higher = sharper."""
    try:
        # Use ffmpeg to compute laplacian and get variance
        r = subprocess.run(
            ["ffmpeg", "-i", str(frame_path),
             "-vf", "format=gray,convolution=0 1 0 1 -4 1 0 1 0:0 1 0 1 -4 1 0 1 0:0 1 0 1 -4 1 0 1 0",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=10,
        )
        if r.stdout:
            import array
            pixels = array.array('B', r.stdout)
            if pixels:
                mean = sum(pixels) / len(pixels)
                variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
                return variance
    except Exception:
        pass
    return 100.0  # assume ok if can't compute


def _compute_motion(path: Path, duration: float) -> float:
    """Estimate motion level by comparing frames at 25% and 75%."""
    try:
        t1, t2 = duration * 0.25, duration * 0.75
        # Use ffmpeg to compute PSNR between two frames (lower PSNR = more different = more motion)
        r = subprocess.run(
            ["ffmpeg", "-ss", str(t1), "-i", str(path), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "gray", "-vf", "scale=160:-1", "-"],
            capture_output=True, timeout=10,
        )
        frame1 = r.stdout

        r = subprocess.run(
            ["ffmpeg", "-ss", str(t2), "-i", str(path), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "gray", "-vf", "scale=160:-1", "-"],
            capture_output=True, timeout=10,
        )
        frame2 = r.stdout

        if frame1 and frame2 and len(frame1) == len(frame2):
            import array
            p1 = array.array('B', frame1)
            p2 = array.array('B', frame2)
            diff = sum(abs(a - b) for a, b in zip(p1, p2)) / len(p1)
            return diff  # 0 = static, higher = more motion
    except Exception:
        pass
    return 10.0  # assume moderate motion


def triage_clip(path: Path, work_dir: Path) -> dict:
    """Analyze a single clip and return triage result.

    Returns:
        dict with: file, path, info, brightness, blur_score, motion,
                   quality_score (0-5), reject, reject_reason
    """
    info = _ffprobe_info(path)
    result = {
        "file": path.name,
        "path": str(path),
        "info": info,
        "reject": False,
        "reject_reason": "",
    }

    # Reject: too short
    if info["duration"] < _MIN_DURATION:
        result["reject"] = True
        result["reject_reason"] = f"too_short ({info['duration']:.1f}s)"
        result["quality_score"] = 0
        return result

    # Extract sample frames
    frames = _extract_sample_frames(path, work_dir)
    if not frames:
        result["reject"] = True
        result["reject_reason"] = "no_frames_extracted"
        result["quality_score"] = 0
        return result

    # Compute metrics on middle frame (most representative)
    mid_frame = frames[len(frames) // 2]
    brightness = _compute_brightness(mid_frame)
    blur = _compute_blur_score(mid_frame)
    motion = _compute_motion(path, info["duration"])

    result["brightness"] = round(brightness, 1)
    result["blur_score"] = round(blur, 1)
    result["motion"] = round(motion, 1)
    result["is_slow_mo"] = info["fps"] > 60

    # Reject: black / lens cap
    if brightness < _MIN_BRIGHTNESS:
        result["reject"] = True
        result["reject_reason"] = f"too_dark (brightness={brightness:.0f})"
        result["quality_score"] = 0
        return result

    # Reject: pure white / overexposed
    if brightness > _MAX_BRIGHTNESS:
        result["reject"] = True
        result["reject_reason"] = f"overexposed (brightness={brightness:.0f})"
        result["quality_score"] = 0
        return result

    # Reject: extreme blur
    if blur < _MIN_BLUR_SCORE:
        result["reject"] = True
        result["reject_reason"] = f"extremely_blurry (blur={blur:.0f})"
        result["quality_score"] = 0
        return result

    # Score: composite quality (0-5)
    score = 3.0  # baseline

    # Duration bonus (longer clips = more usable)
    if info["duration"] > 10:
        score += 0.5
    elif info["duration"] < 3:
        score -= 0.5

    # Sharpness bonus
    if blur > 500:
        score += 0.5
    elif blur < 50:
        score -= 0.5

    # Good brightness range (not too dark, not too bright)
    if 60 < brightness < 200:
        score += 0.3
    else:
        score -= 0.3

    # Motion (moderate is ideal for editing)
    if 5 < motion < 30:
        score += 0.2

    result["quality_score"] = round(max(0, min(5, score)), 1)

    # Cleanup sample frames
    for f in frames:
        f.unlink(missing_ok=True)

    return result


def triage_all(input_dir: Path, work_dir: Path) -> dict:
    """Triage all video clips in a directory.

    Returns:
        dict with: clips (list), summary stats, reject count
    """
    videos = sorted([
        f for f in input_dir.iterdir()
        if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith(".")
    ])

    if not videos:
        log.warning("No video files in %s", input_dir)
        return {"clips": [], "total": 0, "rejected": 0}

    log.info("Triaging %d clips in %s", len(videos), input_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    triage_dir = work_dir / "triage_frames"
    triage_dir.mkdir(exist_ok=True)

    clips = []
    rejected = 0

    for i, v in enumerate(videos):
        if i % 20 == 0:
            log.info("Triage progress: %d/%d", i, len(videos))
        result = triage_clip(v, triage_dir)
        clips.append(result)
        if result["reject"]:
            rejected += 1

    # Sort by quality score (descending)
    clips.sort(key=lambda c: c.get("quality_score", 0), reverse=True)

    triage_result = {
        "input_dir": str(input_dir),
        "total": len(videos),
        "rejected": rejected,
        "kept": len(videos) - rejected,
        "clips": clips,
    }

    # Save
    out_path = work_dir / "triage.json"
    out_path.write_text(json.dumps(triage_result, ensure_ascii=False, indent=2))
    log.info("Triage complete: %d/%d kept, %d rejected",
             len(videos) - rejected, len(videos), rejected)

    return triage_result
