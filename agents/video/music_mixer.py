"""Music mixer — add background music to the edited video.

Phase 4: rough_cut.mp4 + music file → final.mp4
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("video.music_mixer")

MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}


def get_duration(file_path: Path) -> float:
    """Get media duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format=duration", "-of", "csv=p=0", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0


def mix_music(video_path: Path, music_path: Path, output_path: Path,
              music_volume: float = 0.3,
              original_volume: float = 0.15,
              fade_in: float = 2.0,
              fade_out: float = 3.0) -> bool:
    """Mix background music with video's original audio.

    Args:
        video_path: input video (rough_cut.mp4)
        music_path: background music file
        output_path: final output path
        music_volume: music volume (0-1, default 0.3)
        original_volume: original audio volume (0-1, default 0.15)
        fade_in: music fade in duration (seconds)
        fade_out: music fade out duration (seconds)
    """
    video_dur = get_duration(video_path)
    if video_dur <= 0:
        log.error("Could not get video duration")
        return False

    # Build filter: mix original audio (quiet) with music (louder),
    # fade music in/out, normalize loudness
    filter_complex = (
        # Original audio: lower volume
        f"[0:a]volume={original_volume}[orig];"
        # Music: adjust volume, fade in/out, trim to video length
        f"[1:a]volume={music_volume},"
        f"afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={video_dur - fade_out}:d={fade_out},"
        f"atrim=0:{video_dur},asetpts=PTS-STARTPTS[music];"
        # Mix together
        f"[orig][music]amix=inputs=2:duration=first:dropout_transition=2,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",  # don't re-encode video
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]

    log.info("Mixing music: %s + %s → %s", video_path.name, music_path.name, output_path.name)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("Music mix failed: %s", result.stderr[-300:])
            return False
        log.info("Music mix complete: %s (%.1fs)", output_path.name, video_dur)
        return True
    except subprocess.TimeoutExpired:
        log.error("Music mix timed out")
        return False
    except Exception as e:
        log.error("Music mix error: %s", e)
        return False


def find_music(music_dir: Path) -> list[Path]:
    """Find music files in a directory."""
    if not music_dir.exists():
        return []
    return sorted([
        f for f in music_dir.iterdir()
        if f.suffix.lower() in MUSIC_EXTS and not f.name.startswith(".")
    ])
