"""Clip grader — deterministic rules engine for per-clip adaptive color grading.

Maps clip analysis metadata (lighting_type, color_temperature, mood) to
ffmpeg filter chains. No LLM calls — pure lookup + interpolation.

The key insight: Phase 1 vision analysis already captures the visual character
of each clip. This module converts that analysis into precise ffmpeg parameters,
giving each clip an appropriate grade while maintaining visual consistency
across clips with similar lighting conditions.

Log footage handling: detects S-Log3 (Sony), D-Log M (DJI), and flat/D-Cinelike
profiles, and prepends the appropriate 3D LUT for linearization before grading.
"""

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("video.clip_grader")

# LUT directory
_LUT_DIR = Path(__file__).parent / "luts"

# LUT file paths
_SLOG3_LUT = _LUT_DIR / "SLog3_SGamut3Cine_to_Rec709.cube"
_DLOGM_LUT = _LUT_DIR / "DLogM_to_Rec709.cube"

# Normalize: convert 10-bit to 8-bit and auto-stretch histogram
# This fixes the "grey fog" from 10-bit XAVC footage
_NORMALIZE = "format=yuv420p,normalize=blackpt=black:whitept=white:smoothing=50"

# Base filter: always applied (scale + pad to 1080p)
_BASE = "scale=1920:1080:force_original_aspect_ratio=decrease," "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"

# High FPS handling (slow-mo source clips)
_FPS_NORMALIZE = "fps=30"

# Grade presets indexed by (lighting_type, content_mode)
# content_mode: "travel" (Mode 1) or "family" (Mode 2)
# Each value is the eq/colorbalance/vignette portion of the filter chain
GRADE_PRESETS = {
    # --- Golden hour ---
    ("golden_hour", "travel"): (
        "eq=brightness=0.02:contrast=1.18:saturation=1.15,"
        "colorbalance=rs=0.08:gs=0.02:bs=-0.06:rh=0.03:gh=0.01:bh=-0.04,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("golden_hour", "family"): (
        "eq=saturation=1.10,"
        "colorbalance=rs=0.05:gs=0.02:bs=-0.03:rh=0.02:gh=0.01:bh=-0.02,"
        "unsharp=3:3:0.2,"
        "vignette=PI/6"
    ),
    # --- Blue hour ---
    ("blue_hour", "travel"): (
        "eq=brightness=-0.03:contrast=1.15:saturation=1.05,"
        "colorbalance=rs=-0.03:gs=0.0:bs=0.06:rh=-0.04:gh=0.0:bh=0.05,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("blue_hour", "family"): (
        "eq=brightness=-0.02:contrast=1.08:saturation=0.95,"
        "colorbalance=rs=-0.02:gs=0.0:bs=0.04:rh=-0.02:gh=0.0:bh=0.03,"
        "unsharp=3:3:0.2,"
        "vignette=PI/6"
    ),
    # --- Overcast / diffused ---
    ("overcast", "travel"): (
        "eq=brightness=0.03:contrast=1.15:saturation=0.95,"
        "colorbalance=rs=0.03:gs=0.01:bs=-0.02:rh=0.0:gh=0.0:bh=0.0,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("overcast", "family"): ("eq=saturation=1.05," "colorbalance=rs=0.03:gs=0.01:bs=-0.01," "unsharp=3:3:0.2"),
    # --- Harsh midday ---
    ("harsh_midday", "travel"): (
        "eq=brightness=-0.04:contrast=1.12:saturation=1.10,"
        "colorbalance=rs=0.04:gs=0.01:bs=-0.04:rh=-0.02:gh=0.0:bh=0.02,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("harsh_midday", "family"): (
        "eq=brightness=-0.03:contrast=1.08:saturation=0.95,"
        "colorbalance=rs=0.03:gs=0.01:bs=-0.02,"
        "unsharp=3:3:0.2,"
        "vignette=PI/6"
    ),
    # --- Indoor warm ---
    ("indoor_warm", "travel"): (
        "eq=brightness=0.02:contrast=1.10:saturation=1.00," "colorbalance=rs=0.04:gs=0.01:bs=-0.03," "unsharp=3:3:0.2"
    ),
    ("indoor_warm", "family"): ("eq=saturation=1.10," "colorbalance=rs=0.04:gs=0.01:bs=-0.02," "unsharp=3:3:0.2"),
    # --- Indoor cool (fluorescent, etc.) ---
    ("indoor_cool", "travel"): (
        "eq=brightness=0.02:contrast=1.10:saturation=0.95,"
        "colorbalance=rs=0.02:gs=-0.01:bs=0.0:rh=0.02:gh=0.01:bh=-0.01,"
        "unsharp=3:3:0.2"
    ),
    ("indoor_cool", "family"): ("eq=saturation=1.05," "colorbalance=rs=0.04:gs=0.01:bs=-0.01," "unsharp=3:3:0.2"),
    # --- Night ---
    ("night", "travel"): (
        "eq=brightness=-0.05:contrast=1.22:saturation=1.10,"
        "colorbalance=rs=-0.02:gs=0.0:bs=0.05:rh=0.02:gh=0.01:bh=-0.02,"
        "unsharp=3:3:0.3,"
        "vignette=PI/4.5"
    ),
    ("night", "family"): (
        "eq=brightness=-0.03:contrast=1.15:saturation=1.00,"
        "colorbalance=rs=0.02:gs=0.01:bs=-0.01,"
        "unsharp=3:3:0.2,"
        "vignette=PI/5"
    ),
    # --- Mixed / unknown (safe default) ---
    ("mixed", "travel"): (
        "eq=brightness=0.0:contrast=1.12:saturation=1.05,"
        "colorbalance=rs=0.04:gs=0.01:bs=-0.03,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("mixed", "family"): ("eq=saturation=1.12," "colorbalance=rs=0.04:gs=0.01:bs=-0.03," "unsharp=3:3:0.2"),
    # --- Underwater / aquarium ---
    ("underwater", "travel"): (
        "eq=brightness=-0.02:contrast=1.15:saturation=1.10,"
        "colorbalance=rs=-0.04:gs=0.0:bs=0.06:rh=-0.03:gh=0.0:bh=0.04,"
        "unsharp=3:3:0.3,"
        "vignette=PI/5"
    ),
    ("underwater", "family"): (
        "eq=brightness=-0.02:contrast=1.10:saturation=1.05,"
        "colorbalance=rs=-0.03:gs=0.0:bs=0.04,"
        "unsharp=3:3:0.2,"
        "vignette=PI/6"
    ),
}

# Default fallback
_DEFAULT_GRADE = "eq=saturation=1.10," "colorbalance=rs=0.04:gs=0.01:bs=-0.03," "unsharp=3:3:0.2"


def detect_log_profile(source_path: Path) -> str:
    """Detect if a video file was shot in a log/flat gamma profile.

    Returns:
        "slog3" — Sony S-Log3 (XAVC, 10-bit, Sony camera)
        "dlogm" — DJI D-Log M (10-bit, DJI camera)
        "flat"  — Flat/D-Cinelike profile (DJI Osmo Pocket 3 default 10-bit)
        "rec709" — Standard Rec.709 (no LUT needed)
    """
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_entries",
                "stream=pix_fmt,color_transfer,color_primaries,profile",
                "-show_entries",
                "format_tags=major_brand,compatible_brands",
                "-select_streams",
                "v:0",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        data = json.loads(r.stdout)
        stream = data.get("streams", [{}])[0]
        tags = data.get("format", {}).get("tags", {})

        pix_fmt = stream.get("pix_fmt", "")
        color_transfer = stream.get("color_transfer", "")
        major_brand = tags.get("major_brand", "")
        filename = source_path.name.upper()

        is_10bit = "10" in pix_fmt  # yuv420p10le, yuv422p10le
        is_xavc = "XAVC" in major_brand

        # Sony XAVC with 10-bit and unspecified transfer — could be S-Log3 or standard
        # S-Log3 footage has very low contrast: middle grey at ~40% IRE (brightness ~100)
        # Standard Rec.709 has middle grey at ~46% IRE (brightness ~118)
        # Check actual pixel brightness to distinguish
        if is_xavc and is_10bit and color_transfer in ("unknown", "unspecified", ""):
            avg_bright = _probe_brightness(source_path)
            if avg_bright < 70:
                # Very flat/low brightness = S-Log3
                return "slog3"
            # Otherwise standard XAVC — no LUT needed
            return "rec709"

        # DJI files (filename starts with DJI_)
        if filename.startswith("DJI_"):
            if is_10bit and color_transfer in ("unknown", "unspecified", ""):
                # Only apply D-Log M LUT if transfer is truly unspecified
                # DJI tagged bt709 = normal color, don't touch
                return "dlogm"
            return "rec709"

        # Standard 8-bit with bt709 = no LUT needed
        if not is_10bit or color_transfer == "bt709":
            return "rec709"

    except Exception as e:
        log.warning("Log profile detection failed for %s: %s", source_path.name, e)

    return "rec709"


def _probe_brightness(source_path: Path) -> float:
    """Quick brightness check — extract one frame at 30% and measure mean."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(source_path)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        dur = float(r.stdout.strip())
        ss = dur * 0.3

        r = subprocess.run(
            [
                "ffmpeg",
                "-ss",
                str(ss),
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-vf",
                "scale=80:-1",
                "-",
            ],
            capture_output=True,
            timeout=10,
        )
        if r.stdout:
            import array

            pixels = array.array("B", r.stdout)
            if pixels:
                return sum(pixels) / len(pixels)
    except Exception:
        pass
    return 100.0  # assume normal


def _get_lut_filter(log_profile: str) -> str:
    """Return the ffmpeg LUT filter string for a given log profile."""
    if log_profile == "slog3" and _SLOG3_LUT.exists():
        return f"lut3d='{_SLOG3_LUT}'"
    elif log_profile == "dlogm" and _DLOGM_LUT.exists():
        return f"lut3d='{_DLOGM_LUT}'"
    elif log_profile == "flat":
        # For flat/D-Cinelike: boost contrast and saturation (no LUT, just stronger grade)
        return "eq=contrast=1.25:saturation=1.15:brightness=0.03"
    return ""


def grade_clip(
    clip_analysis: dict, content_mode: str = "family", high_fps: bool = False, source_path: Path = None
) -> str:
    """Build ffmpeg video filter chain for a clip based on its analysis.

    Args:
        clip_analysis: dict with at least 'lighting_type' key.
            Optional: 'color_temperature_est', 'mood'
        content_mode: "travel" or "family"
        high_fps: True if source is >60fps (slow-mo), adds fps normalization
        source_path: Path to source file (for log profile detection)

    Returns:
        Complete ffmpeg -vf filter string (LUT + scale + grade)
    """
    lighting = clip_analysis.get("lighting_type", "mixed")
    if lighting not in {k[0] for k in GRADE_PRESETS}:
        lighting = "mixed"

    mode = content_mode if content_mode in ("travel", "family") else "family"

    # Look up grade
    grade = GRADE_PRESETS.get((lighting, mode))
    if grade is None:
        grade = GRADE_PRESETS.get((lighting, "family"), _DEFAULT_GRADE)

    # Apply color temperature adjustment if specified
    color_temp = clip_analysis.get("color_temperature_est", "neutral")
    grade = _adjust_for_color_temp(grade, color_temp, mode)

    # Build full filter chain
    parts = []

    # Detect and apply log LUT FIRST (before any other grading)
    is_10bit = False
    if source_path:
        log_profile = detect_log_profile(source_path)
        lut_filter = _get_lut_filter(log_profile)
        if lut_filter:
            parts.append(lut_filter)
            log.debug("Applying %s LUT for %s", log_profile, source_path.name)

        # Check if 10-bit source (needs normalize to avoid grey fog)
        try:
            r = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=pix_fmt",
                    "-of",
                    "csv=p=0",
                    str(source_path),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_10bit = "10" in r.stdout.strip()
        except Exception:
            pass

    if is_10bit:
        parts.append(_NORMALIZE)

    parts.append(_BASE)
    if high_fps:
        parts.append(_FPS_NORMALIZE)
    parts.append(grade)

    return ",".join(parts)


def _adjust_for_color_temp(grade: str, color_temp: str, mode: str) -> str:
    """Fine-tune grade based on color temperature estimate.

    If the source is already warm and we're adding warm grading,
    pull back slightly to avoid over-warming. Vice versa for cool.
    """
    if color_temp == "warm" and "rs=0.08" in grade:
        # Source already warm, reduce red shift
        grade = grade.replace("rs=0.08", "rs=0.05")
    elif color_temp == "cool" and mode == "family":
        # Cool source in family mode — add a touch more warmth
        grade = grade.replace("rs=0.03", "rs=0.05")

    return grade


def detect_content_mode(clip_analyses: list[dict]) -> str:
    """Auto-detect content mode from clip analyses.

    Heuristic: if most clips are outdoor/scenic/aerial → travel
    If most clips have subjects (people, kids) → family
    """
    if not clip_analyses:
        return "family"

    travel_signals = 0
    family_signals = 0

    for clip in clip_analyses:
        subjects = clip.get("subjects", [])
        location = clip.get("location_type", "")
        mood = clip.get("mood", "")

        # Family signals
        if any(s in str(subjects).lower() for s in ["child", "kid", "baby", "family", "people"]):
            family_signals += 1
        if "intimate" in mood or "gentle" in mood or "tender" in mood:
            family_signals += 1

        # Travel signals
        if location in ("outdoor", "scenic", "landmark", "nature"):
            travel_signals += 1
        if "epic" in mood or "adventure" in mood or "energetic" in mood:
            travel_signals += 1

    return "travel" if travel_signals > family_signals * 1.5 else "family"
