"""Music mixer — add background music to the edited video.

Phase 4: rough_cut.mp4 + music file → final.mp4

Includes auto-download of royalty-free music from Incompetech (Kevin MacLeod)
when no music file is provided. CC BY 3.0 license.
"""

import json
import logging
import random
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("video.music_mixer")

MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}

# Incompetech catalog URL — 1400+ royalty-free tracks
_INCOMPETECH_CATALOG = "https://incompetech.com/music/royalty-free/pieces.json"
_INCOMPETECH_DL_BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"

# Mood mapping: video mood → Incompetech "feel" tags
_MOOD_MAP = {
    # Travel / energetic
    "energetic": ["Bright", "Bouncy", "Driving", "Uplifting"],
    "cinematic": ["Epic", "Film Noir", "Intense", "Serious"],
    "adventure": ["Bright", "Uplifting", "Driving", "Bold"],
    # Family / intimate
    "playful": ["Bouncy", "Humorous", "Whimsical", "Bright"],
    "contemplative": ["Calming", "Relaxed", "Pensive", "Sentimental"],
    "warm": ["Relaxed", "Calming", "Sentimental", "Uplifting"],
    "joyful": ["Bright", "Bouncy", "Uplifting", "Happy"],
    # Default
    "default": ["Bright", "Relaxed", "Calming", "Uplifting"],
}


def get_duration(file_path: Path) -> float:
    """Get media duration in seconds."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(file_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip() or 0)
    except Exception:
        return 0


def mix_music(
    video_path: Path,
    music_path: Path,
    output_path: Path,
    music_volume: float = 0.3,
    original_volume: float = 0.15,
    fade_in: float = 2.0,
    fade_out: float = 3.0,
    speech_segments: list[dict] | None = None,
) -> bool:
    """Mix background music with video's original audio, with auto-ducking.

    When speech_segments are provided (from Whisper transcription), the music
    automatically ducks (lowers volume) during dialogue so speech stays clear.

    Args:
        video_path: input video (rough_cut.mp4)
        music_path: background music file
        output_path: final output path
        music_volume: music volume when no speech (0-1, default 0.3)
        original_volume: original audio volume when no speech (0-1, default 0.15)
        fade_in: music fade in duration (seconds)
        fade_out: music fade out duration (seconds)
        speech_segments: list of {start, end, text} from Whisper transcription
    """
    video_dur = get_duration(video_path)
    if video_dur <= 0:
        log.error("Could not get video duration")
        return False

    has_speech = speech_segments and len(speech_segments) > 0

    if has_speech:
        # With speech: use sidechaincompress for automatic ducking
        # Original audio plays at full volume, music ducks when voice is detected
        log.info("Mixing with auto-ducking (%d speech segments)", len(speech_segments))

        # Build volume expression that boosts original audio during speech
        # and keeps it low otherwise
        speech_vol_expr = _build_speech_volume_expr(speech_segments, speech_vol=0.85, silent_vol=original_volume)
        music_duck_expr = _build_speech_volume_expr(speech_segments, speech_vol=0.08, silent_vol=music_volume)

        filter_complex = (
            # Original audio: loud during speech, quiet otherwise
            f"[0:a]volume='{speech_vol_expr}':eval=frame[orig];"
            # Music: duck during speech, normal otherwise
            f"[1:a]volume='{music_duck_expr}':eval=frame,"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={max(0, video_dur - fade_out)}:d={fade_out},"
            f"atrim=0:{video_dur},asetpts=PTS-STARTPTS[music];"
            # Mix together + normalize
            f"[orig][music]amix=inputs=2:duration=first:dropout_transition=2,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )
    else:
        # No speech: simple mix (original stays quiet, music dominates)
        filter_complex = (
            f"[0:a]volume={original_volume}[orig];"
            f"[1:a]volume={music_volume},"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={max(0, video_dur - fade_out)}:d={fade_out},"
            f"atrim=0:{video_dur},asetpts=PTS-STARTPTS[music];"
            f"[orig][music]amix=inputs=2:duration=first:dropout_transition=2,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[aout]"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]

    log.info(
        "Mixing music: %s + %s → %s%s",
        video_path.name,
        music_path.name,
        output_path.name,
        " (with ducking)" if has_speech else "",
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("Music mix failed: %s", result.stderr[-500:])
            # Fallback: try without ducking if the volume expression was too complex
            if has_speech:
                log.info("Retrying without ducking...")
                return mix_music(
                    video_path,
                    music_path,
                    output_path,
                    music_volume,
                    original_volume,
                    fade_in,
                    fade_out,
                    speech_segments=None,
                )
            return False
        log.info("Music mix complete: %s (%.1fs)", output_path.name, video_dur)
        return True
    except subprocess.TimeoutExpired:
        log.error("Music mix timed out")
        return False
    except Exception as e:
        log.error("Music mix error: %s", e)
        return False


def _build_speech_volume_expr(segments: list[dict], speech_vol: float, silent_vol: float, pad: float = 0.3) -> str:
    """Build an ffmpeg volume expression that switches based on speech timing.

    Creates a between(t,start,end) expression chain. Adds padding around each
    speech segment for smooth transitions.

    Args:
        segments: list of {start, end} dicts
        speech_vol: volume during speech
        silent_vol: volume during silence
        pad: seconds of padding around speech segments
    """
    if not segments:
        return str(silent_vol)

    # Merge overlapping/adjacent segments (with padding)
    merged = []
    for seg in sorted(segments, key=lambda s: s.get("start", 0)):
        start = max(0, seg.get("start", 0) - pad)
        end = seg.get("end", 0) + pad
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Limit to 30 segments to avoid overly complex expressions
    if len(merged) > 30:
        # Keep the longest segments
        merged.sort(key=lambda x: x[1] - x[0], reverse=True)
        merged = sorted(merged[:30], key=lambda x: x[0])

    # Build expression: if any between() matches → speech_vol, else silent_vol
    conditions = "+".join(f"between(t,{s:.1f},{e:.1f})" for s, e in merged)
    return f"if({conditions},{speech_vol},{silent_vol})"


def find_music(music_dir: Path) -> list[Path]:
    """Find music files in a directory."""
    if not music_dir.exists():
        return []
    return sorted([f for f in music_dir.iterdir() if f.suffix.lower() in MUSIC_EXTS and not f.name.startswith(".")])


# ---------------------------------------------------------------------------
# Auto music selection from Incompetech (Kevin MacLeod, CC BY 3.0)
# ---------------------------------------------------------------------------

_catalog_cache: list[dict] | None = None


def _fetch_catalog() -> list[dict]:
    """Fetch and cache the Incompetech music catalog."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    log.info("Fetching Incompetech music catalog...")
    try:
        req = urllib.request.Request(_INCOMPETECH_CATALOG)
        with urllib.request.urlopen(req, timeout=15) as resp:
            _catalog_cache = json.loads(resp.read().decode("utf-8"))
            log.info("Catalog loaded: %d tracks", len(_catalog_cache))
            return _catalog_cache
    except Exception as e:
        log.error("Failed to fetch Incompetech catalog: %s", e)
        return []


def search_music(
    mood: str = "default", min_duration: float = 60, max_duration: float = 300, genre: str = ""
) -> list[dict]:
    """Search Incompetech catalog by mood and duration.

    Args:
        mood: one of the keys in _MOOD_MAP, or a direct Incompetech feel tag
        min_duration: minimum track length in seconds
        max_duration: maximum track length in seconds
        genre: optional genre filter (e.g., "Cinematic", "Jazz", "Rock")

    Returns:
        List of matching track dicts sorted by relevance.
    """
    catalog = _fetch_catalog()
    if not catalog:
        return []

    # Get feel tags for the mood
    feel_tags = _MOOD_MAP.get(mood.lower(), [mood])

    matches = []
    for track in catalog:
        # Duration filter (field is in seconds as string or int)
        try:
            dur = float(track.get("duration", 0))
        except (ValueError, TypeError):
            continue
        if dur < min_duration or dur > max_duration:
            continue

        # Genre filter
        if genre and genre.lower() not in track.get("genre", "").lower():
            continue

        # Score by mood match
        track_feels = [f.strip() for f in track.get("feel", "").split(",")]
        score = sum(1 for tag in feel_tags if tag in track_feels)
        if score > 0:
            matches.append((score, track))

    # Sort by score descending, then shuffle within same score for variety
    matches.sort(key=lambda x: -x[0])
    return [t for _, t in matches]


def download_music(track: dict, output_dir: Path) -> Path | None:
    """Download a track from Incompetech.

    Returns the local file path, or None on failure.
    """
    filename = track.get("filename", "")
    if not filename:
        log.error("Track has no filename: %s", track.get("title", "?"))
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    local_path = output_dir / filename

    # Skip if already downloaded
    if local_path.exists() and local_path.stat().st_size > 10000:
        log.info("Already downloaded: %s", filename)
        return local_path

    url = _INCOMPETECH_DL_BASE + urllib.parse.quote(filename)
    log.info("Downloading: %s — %s", track.get("title", "").strip(), url)

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            if len(data) < 10000:
                log.error("Download too small (%d bytes), likely 404", len(data))
                return None
            local_path.write_bytes(data)
            log.info("Downloaded: %s (%.1f MB)", filename, len(data) / (1024 * 1024))
            return local_path
    except Exception as e:
        log.error("Download failed for %s: %s", filename, e)
        return None


def auto_select_music(mood: str, video_duration: float, output_dir: Path) -> Path | None:
    """Automatically find and download a matching track.

    Selects a track slightly longer than the video duration,
    matching the requested mood. Downloads to output_dir.

    Args:
        mood: desired mood (e.g., "energetic", "contemplative", "playful")
        video_duration: target video length in seconds
        output_dir: where to save the downloaded MP3

    Returns:
        Path to the downloaded music file, or None.
    """
    # Want music slightly longer than video (at least 10s buffer for fade)
    min_dur = video_duration * 0.8
    max_dur = video_duration * 2.5  # not too long to avoid awkward looping

    tracks = search_music(mood=mood, min_duration=min_dur, max_duration=max_dur)

    if not tracks:
        # Relax duration constraints
        tracks = search_music(mood=mood, min_duration=30, max_duration=600)

    if not tracks:
        # Fall back to default mood
        tracks = search_music(mood="default", min_duration=min_dur, max_duration=max_dur)

    if not tracks:
        log.warning("No matching music found for mood=%s, duration=%.0fs", mood, video_duration)
        return None

    # Pick randomly from top 5 matches for variety
    pick = random.choice(tracks[: min(5, len(tracks))])
    log.info(
        "Selected: '%s' by Kevin MacLeod (mood: %s, %.0fs)",
        pick.get("title", "").strip(),
        pick.get("feel", ""),
        float(pick.get("duration", 0)),
    )

    return download_music(pick, output_dir)
