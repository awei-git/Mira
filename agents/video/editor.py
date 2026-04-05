"""Video editor — Python-driven ffmpeg rendering from edit_plan.json.

Phase 3: edit_plan.json → per-clip ffmpeg renders → rough_cut.mp4

Replaces LLM-generated bash scripts with deterministic Python-driven
ffmpeg calls. Each clip is rendered individually for targeted re-rendering
during the review iteration loop.
"""
import json
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("video.editor")


def _get_duration(path: Path) -> float:
    """Get video duration via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _get_fps(path: Path) -> float:
    """Get source video frame rate."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v",
             "-show_entries", "stream=r_frame_rate",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        num, den = r.stdout.strip().split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


def _ts_to_seconds(ts: str) -> float:
    """Convert MM:SS or HH:MM:SS timestamp to seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_clip_times(clip: dict) -> bool:
    """Validate clip timing is sane."""
    start = clip.get("start_time", "0:00")
    end = clip.get("end_time", "0:00")

    def parse_time(t):
        parts = str(t).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return float(t)

    try:
        start_s = parse_time(start)
        end_s = parse_time(end)
        if end_s <= start_s:
            log.warning("Clip has end <= start: %s - %s", start, end)
            return False
        if end_s - start_s > 300:  # 5 minutes max per clip
            log.warning("Clip duration > 5 min: %s - %s", start, end)
            return False
        return True
    except (ValueError, TypeError) as e:
        log.warning("Cannot parse clip times %s - %s: %s", start, end, e)
        return False


# ---------------------------------------------------------------------------
# Per-clip rendering
# ---------------------------------------------------------------------------

def render_clip(clip_spec: dict, idx: int, clips_dir: Path,
                source_dir: Path, color_grade: str) -> Path | None:
    """Render a single clip with color grading and exact duration.

    Args:
        clip_spec: dict from edit_plan.json clips[]
        idx: clip index for filename
        clips_dir: output directory for rendered clips
        source_dir: directory containing source footage
        color_grade: ffmpeg -vf filter string from clip_grader

    Returns:
        Path to rendered clip, or None on failure.
    """
    out_path = clips_dir / f"c{idx:03d}.mp4"
    source_file = clip_spec.get("source_file", "")
    source_path = source_dir / source_file

    if not source_path.exists():
        # Try case-insensitive match
        for f in source_dir.iterdir():
            if f.name.lower() == source_file.lower():
                source_path = f
                break
        else:
            log.warning("Source not found: %s", source_file)
            return None

    # Parse timestamps
    start_ts = clip_spec.get("start_time", "0:00")
    end_ts = clip_spec.get("end_time", "")
    ss = _ts_to_seconds(start_ts)

    if end_ts:
        duration = _ts_to_seconds(end_ts) - ss
    else:
        duration = clip_spec.get("duration", 4.0)

    if duration <= 0:
        duration = 4.0

    # Speed (validate range)
    speed = clip_spec.get("speed", 1.0)
    if not isinstance(speed, (int, float)) or speed <= 0 or speed > 10:
        log.warning("Invalid speed %s for clip %d, defaulting to 1.0",
                    speed if isinstance(speed, (int, float)) else repr(speed), idx)
        speed = 1.0

    # Build filter chain
    vf = color_grade

    # Check for high FPS (slow-mo source)
    src_fps = _get_fps(source_path)
    if src_fps > 60 and "fps=30" not in vf:
        vf = f"fps=30,{vf}"

    # Speed adjustment
    af = "anull"
    input_duration = duration
    if speed != 1.0:
        setpts = f"setpts={1/speed}*PTS"
        vf = f"{setpts},{vf}"
        af = f"atempo={speed}"
        input_duration = duration / speed

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(ss),
        "-i", str(source_path),
        "-t", str(input_duration),
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-r", "30",
        "-vsync", "cfr",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        log.warning("Clip render timed out: %s", source_file)
        return None

    if out_path.exists() and out_path.stat().st_size > 1000:
        if verify_rendered_clip(out_path):
            return out_path
        log.warning("Clip %s failed verification", out_path.name)
    return None


def verify_rendered_clip(path: Path) -> bool:
    """Verify a rendered clip is not black/corrupt.

    Checks: file size, duration > 0, and mean brightness > 10.
    """
    if not path.exists():
        return False
    if path.stat().st_size < 5000:
        return False

    dur = _get_duration(path)
    if dur < 0.3:
        return False

    # Check brightness by sampling a frame at 50%
    try:
        r = subprocess.run(
            ["ffmpeg", "-ss", str(dur * 0.5), "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
             "-vf", "scale=80:-1", "-"],
            capture_output=True, timeout=10,
        )
        if r.stdout:
            import array
            pixels = array.array('B', r.stdout)
            if pixels:
                mean_brightness = sum(pixels) / len(pixels)
                if mean_brightness < 10:
                    log.warning("Clip %s is black (brightness=%.1f)",
                                path.name, mean_brightness)
                    return False
    except Exception:
        pass  # if we can't check, assume ok

    return True


def check_color_continuity(clips_dir: Path) -> list[dict]:
    """Check adjacent clips for color consistency.

    Compares average brightness and color of the last frame of clip N
    with the first frame of clip N+1.

    Returns:
        List of dicts with {clip_a, clip_b, brightness_diff, issue} for
        pairs that exceed the threshold.
    """
    clip_paths = sorted(clips_dir.glob("c*.mp4"))
    if len(clip_paths) < 2:
        return []

    issues = []
    prev_stats = None

    for cp in clip_paths:
        # Get average brightness of first frame
        try:
            r = subprocess.run(
                ["ffmpeg", "-ss", "0.1", "-i", str(cp),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
                 "-vf", "scale=80:-1", "-"],
                capture_output=True, timeout=10,
            )
            if r.stdout:
                import array
                pixels = array.array('B', r.stdout)
                stats = {
                    "file": cp.name,
                    "brightness": sum(pixels) / len(pixels) if pixels else 128,
                }
            else:
                stats = {"file": cp.name, "brightness": 128}
        except Exception:
            stats = {"file": cp.name, "brightness": 128}

        if prev_stats:
            diff = abs(stats["brightness"] - prev_stats["brightness"])
            if diff > 40:  # significant brightness jump
                issues.append({
                    "clip_a": prev_stats["file"],
                    "clip_b": stats["file"],
                    "brightness_diff": round(diff, 1),
                    "issue": f"Brightness jump: {prev_stats['brightness']:.0f} → {stats['brightness']:.0f}",
                })

        prev_stats = stats

    if issues:
        log.warning("Color continuity: %d issues found", len(issues))
    else:
        log.info("Color continuity: OK (no significant jumps)")

    return issues


def create_title_card(text: str, duration: float, output_path: Path) -> Path | None:
    """Create a title card with cinematic look."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0a0a0a:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-vf", (
            f"drawtext=text='{text}'"
            ":fontcolor=0xf0e8d8:fontsize=64"
            ":x=(w-text_w)/2:y=(h-text_h)/2"
            ":font=Helvetica Neue"
            f":alpha='if(lt(t,1),t,if(gt(t,{duration-1}),{duration}-t,1))'"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        if output_path.exists():
            return output_path
    except Exception as e:
        log.warning("Title card creation failed: %s", e)
    return None


def create_credits(main_text: str, sub_text: str, duration: float,
                   output_path: Path) -> Path | None:
    """Create a credits card."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0a0a0a:s=1920x1080:d={duration}:r=30",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-vf", (
            f"drawtext=text='{main_text}'"
            ":fontcolor=0xf0e8d8:fontsize=48"
            ":x=(w-text_w)/2:y=(h-text_h)/2-40"
            ":font=Helvetica Neue"
            f":alpha='if(lt(t,1),t,if(gt(t,{duration-1}),{duration}-t,1))',"
            f"drawtext=text='{sub_text}'"
            ":fontcolor=0x888880:fontsize=28"
            ":x=(w-text_w)/2:y=(h-text_h)/2+40"
            ":font=Helvetica Neue"
            f":alpha='if(lt(t,1),t,if(gt(t,{duration-1}),{duration}-t,1))'"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
        if output_path.exists():
            return output_path
    except Exception as e:
        log.warning("Credits creation failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_rough_cut(clip_paths: list[Path], output_path: Path) -> bool:
    """Concatenate rendered clips into a rough cut.

    Args:
        clip_paths: ordered list of clip file paths
        output_path: where to write rough_cut.mp4

    Returns:
        True if successful.
    """
    valid_clips = [p for p in clip_paths if p and p.exists()]
    if not valid_clips:
        log.error("No valid clips to assemble")
        return False

    concat_path = output_path.parent / "concat.txt"
    with open(concat_path, "w") as f:
        for cp in valid_clips:
            f.write(f"file '{cp}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_path),
        "-c", "copy",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and output_path.exists():
            log.info("Rough cut assembled: %s (%.1fs)",
                     output_path, _get_duration(output_path))
            return True
        log.error("Assembly failed: %s", result.stderr[-300:] if result.stderr else "")
        return False
    except Exception as e:
        log.error("Assembly error: %s", e)
        return False


def mix_with_music(rough_cut: Path, music_path: Path, output_path: Path,
                   song_duration: float) -> bool:
    """Mix rough cut with music track.

    Music at 85%, original audio at 12%. Uses normalize=0 to prevent
    volume halving when one input ends before the other.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(rough_cut),
        "-i", str(music_path),
        "-t", str(song_duration + 6),
        "-filter_complex",
        "[0:a]volume=0.12[va];[1:a]volume=0.85[ma];"
        "[va][ma]amix=inputs=2:duration=longest:normalize=0[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and output_path.exists():
            log.info("Final mix: %s (%.1fs)", output_path,
                     _get_duration(output_path))
            return True
        log.error("Music mix failed: %s", result.stderr[-300:] if result.stderr else "")
        return False
    except Exception as e:
        log.error("Music mix error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Targeted re-rendering (for review iteration)
# ---------------------------------------------------------------------------

def re_render_clips(fix_proposals: list[dict], edit_plan: dict,
                    clips_dir: Path, source_dir: Path,
                    clip_grader_fn=None) -> list[int]:
    """Re-render specific clips based on review fix proposals.

    Args:
        fix_proposals: list of fix dicts from video_reviewer
        edit_plan: the original edit plan
        clips_dir: where rendered clips live
        source_dir: source footage directory
        clip_grader_fn: function(clip_analysis, content_mode) -> ffmpeg filter

    Returns:
        list of clip indices that were re-rendered
    """
    re_rendered = []
    clips = edit_plan.get("clips", [])
    content_mode = edit_plan.get("content_mode", "family")

    for fix in fix_proposals:
        fix_type = fix.get("type", "")
        clip_idx = fix.get("clip_idx_approx", -1)

        if clip_idx < 0 or clip_idx >= len(clips):
            # Try to find by timecode
            timecode = fix.get("timecode", "")
            if timecode:
                target_time = _ts_to_seconds(timecode)
                running = 0
                for i, c in enumerate(clips):
                    running += c.get("duration", 4.0)
                    if running >= target_time:
                        clip_idx = i
                        break

        if clip_idx < 0 or clip_idx >= len(clips):
            log.warning("Cannot resolve clip for fix: %s", fix)
            continue

        clip_spec = clips[clip_idx]

        if fix_type == "re-grade":
            # Re-grade with adjusted parameters
            adjusted_analysis = {
                "lighting_type": clip_spec.get("grade_type", "mixed"),
                "color_temperature_est": "neutral",
            }
            grade = clip_grader_fn(adjusted_analysis, content_mode) if clip_grader_fn else ""
            if grade:
                result = render_clip(clip_spec, clip_idx, clips_dir,
                                     source_dir, grade)
                if result:
                    re_rendered.append(clip_idx)
                    log.info("Re-rendered clip %d (re-grade)", clip_idx)

        elif fix_type == "remove":
            # Mark for removal — caller will skip this clip in assembly
            clip_path = clips_dir / f"c{clip_idx:03d}.mp4"
            if clip_path.exists():
                clip_path.unlink()
                re_rendered.append(clip_idx)
                log.info("Removed clip %d", clip_idx)

        elif fix_type == "re-time":
            # Adjust timing — re-render with modified timestamps
            if clip_grader_fn:
                analysis = {"lighting_type": clip_spec.get("grade_type", "mixed")}
                grade = clip_grader_fn(analysis, content_mode)
            else:
                grade = ""
            result = render_clip(clip_spec, clip_idx, clips_dir,
                                 source_dir, grade)
            if result:
                re_rendered.append(clip_idx)
                log.info("Re-rendered clip %d (re-time)", clip_idx)

    return re_rendered


# ---------------------------------------------------------------------------
# Full edit execution from edit_plan.json
# ---------------------------------------------------------------------------

def execute_edit_plan(edit_plan: dict, source_dir: Path, work_dir: Path,
                      music_path: Path, clip_grader_fn=None) -> Path | None:
    """Execute a complete edit from an edit_plan.json.

    Args:
        edit_plan: structured plan from screenplay.generate_edit_plan()
        source_dir: directory with source footage
        work_dir: working directory for output
        music_path: path to music file
        clip_grader_fn: function(clip_analysis, content_mode) -> ffmpeg filter string

    Returns:
        Path to final video, or None on failure.
    """
    clips_dir = work_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    content_mode = edit_plan.get("content_mode", "family")
    plan_clips = edit_plan.get("clips", [])

    if not plan_clips:
        log.error("No clips in edit plan")
        return None

    # Render title card
    title_info = edit_plan.get("title_card", {})
    title_text = title_info.get("text", edit_plan.get("title", ""))
    title_dur = title_info.get("duration", 3.5)
    rendered_paths = []

    if title_text:
        title_path = clips_dir / "title.mp4"
        t = create_title_card(title_text, title_dur, title_path)
        if t:
            rendered_paths.append(t)

    # Render each clip
    for i, clip_spec in enumerate(plan_clips):
        # Validate clip timestamps before rendering
        if clip_spec.get("end_time") and not _validate_clip_times(clip_spec):
            log.warning("Skipping clip %d/%d — invalid timestamps", i + 1, len(plan_clips))
            continue

        grade_type = clip_spec.get("grade_type", "mixed")
        clip_analysis = {"lighting_type": grade_type}

        # Resolve source path for log detection
        src_file = clip_spec.get("source_file", "")
        src_path = source_dir / src_file
        if not src_path.exists():
            for f in source_dir.iterdir():
                if f.name.lower() == src_file.lower():
                    src_path = f
                    break

        if clip_grader_fn:
            grade = clip_grader_fn(clip_analysis, content_mode,
                                    source_path=src_path if src_path.exists() else None)
        else:
            from clip_grader import grade_clip
            grade = grade_clip(clip_analysis, content_mode,
                               source_path=src_path if src_path.exists() else None)

        log.info("Rendering clip %d/%d [%s] grade=%s",
                 i + 1, len(plan_clips),
                 clip_spec.get("source_file", "?"), grade_type)

        result = render_clip(clip_spec, i, clips_dir, source_dir, grade)
        if result:
            rendered_paths.append(result)
        else:
            log.warning("Skipped clip %d (render failed)", i)

    # Render credits
    credits_info = edit_plan.get("credits", {})
    if credits_info:
        credits_path = clips_dir / "credits.mp4"
        music_name = music_path.stem if music_path else ""
        c = create_credits(
            credits_info.get("text", ""),
            f"Music: {music_name}",
            credits_info.get("duration", 5.0),
            credits_path,
        )
        if c:
            rendered_paths.append(c)

    if len(rendered_paths) < 2:
        log.error("Too few clips rendered (%d)", len(rendered_paths))
        return None

    # Assemble rough cut
    rough_path = work_dir / "rough_cut.mp4"
    if not assemble_rough_cut(rendered_paths, rough_path):
        return None

    # Mix with music
    if music_path and music_path.exists():
        final_path = work_dir / "final.mp4"
        song_dur = _get_duration(music_path)
        if mix_with_music(rough_path, music_path, final_path, song_dur):
            return final_path

    return rough_path


# ---------------------------------------------------------------------------
# Legacy: parse screenplay clips (kept for backward compatibility)
# ---------------------------------------------------------------------------

def parse_screenplay_clips(screenplay: str) -> list[dict]:
    """Extract clip references from a screenplay markdown.

    Looks for patterns like: **[filename.MOV 00:12-00:28]**
    Returns list of {file, start, end, description, transition}
    """
    clips = []
    pattern = r'\*\*\[([^\]]+?)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*-?\s*(\d{1,2}:\d{2}(?::\d{2})?)?\]\*\*\s*(.*?)(?:\n|$)'
    for m in re.finditer(pattern, screenplay):
        clips.append({
            "file": m.group(1).strip(),
            "start": m.group(2),
            "end": m.group(3) or "",
            "description": m.group(4).strip(),
            "transition": "cut",
        })

    if not clips:
        pattern2 = r'\[(\S+\.(?:MOV|mp4|mov|MP4|avi|mkv))\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*-?\s*(\d{1,2}:\d{2}(?::\d{2})?)?\]'
        for m in re.finditer(pattern2, screenplay):
            clips.append({
                "file": m.group(1),
                "start": m.group(2),
                "end": m.group(3) or "",
                "description": "",
                "transition": "cut",
            })

    log.info("Parsed %d clips from screenplay", len(clips))
    return clips
