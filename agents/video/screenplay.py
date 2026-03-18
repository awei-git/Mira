"""Screenplay generator — turn scene analysis into a narrative structure.

Phase 2: scene_log.json → Claude → screenplay.md
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("video.screenplay")


SCREENPLAY_PROMPT = """You are editing a video. You have a detailed scene analysis from the footage.
Your job: create a screenplay that tells a compelling {target_minutes}-minute story.

## Scene Analysis
{scene_summary}

## Total footage: {total_duration:.0f}s across {video_count} files
## Target output: {target_minutes} minutes

## Instructions

1. Select the best moments (quality >= 3, interesting highlights)
2. Use the camera motion and action intensity data to plan pacing:
   - Match cuts to natural motion endpoints (pan stops, zoom settles)
   - Alternate high-intensity and low-intensity scenes for rhythm
   - Use tracking/drone shots for longer holds, static shots can be shorter
3. Arrange into a narrative arc:
   - Opening: establish the place, set the mood (15-30s)
   - Build: exploration, discovery, details (60-90s)
   - Heart: the most emotional/beautiful/interesting moments (60-90s)
   - Close: reflection, departure, or a striking final image (15-30s)
4. Plan transitions based on scene relationships:
   - Cut: for energy, matching action, same location
   - Dissolve/crossfade: for time passing, mood shift, location change
   - J-cut/L-cut: let audio from next scene start before the visual cut (or vice versa)
5. Use audio notes to plan sound design:
   - Preserve ambient sound where it adds atmosphere
   - Note where music should swell, quiet down, or drop out entirely
6. Plan speed variations:
   - Mark slow-motion moments (0.5x) for dramatic emphasis
   - Mark speed ramps (normal → slow → normal) for action highlights

## Output format (Markdown)

# [Title — short evocative title]

## Act 1: [name] (0:00 - ~0:30)
- **[filename 00:12-00:28]** description — *editing note*
  - Camera: [pan_left/static/etc] | Speed: [1x/0.5x/ramp]
  - Transition: fade to next
  - Audio: [keep ambient / music only / J-cut from next]

## Act 2: [name] (0:30 - ~2:00)
- **[filename 01:20-01:35]** description — *editing note*
  - Camera: [tracking] | Speed: [1x]
  - Transition: cut
  - Audio: [ambient + music]

[etc.]

## Music Notes
- Opening: [mood, tempo, instrument suggestion]
- Build: [mood, tempo]
- Climax: [mood, tempo]
- Close: [mood, fade out timing]

## Color Notes
- Overall look: [warm/cool/natural/cinematic]
- Per-act adjustments if needed

## Pacing Notes
[Brief notes on rhythm, energy curve, and speed variation strategy]

Be specific about timestamps and filenames. Only use scenes from the log.
"""


def generate_screenplay(scene_log: dict, work_dir: Path,
                        target_minutes: float = 4.0,
                        claude_think_fn=None) -> str:
    """Generate a screenplay from the scene log.

    Args:
        scene_log: dict from scene_analyzer.analyze_all()
        work_dir: directory for output
        target_minutes: target video length
        claude_think_fn: callable for LLM reasoning (claude_think or equivalent)

    Returns:
        screenplay text (markdown)
    """
    if not claude_think_fn:
        log.error("No LLM function provided")
        return ""

    # Build a concise scene summary for the prompt
    scenes = scene_log.get("scenes", [])
    if not scenes:
        log.warning("No scenes in scene log")
        return ""

    # Filter to visual scenes (not transcript-only)
    visual_scenes = [s for s in scenes if s.get("type") != "transcript"]
    transcript_scenes = [s for s in scenes if s.get("type") == "transcript"]

    # Summarize scenes concisely
    lines = []
    for s in visual_scenes:
        q = s.get("quality", 3)
        stars = "*" * q
        # Build timestamp range if end available
        ts_range = s.get("timestamp_str", "?")
        if s.get("end_timestamp_str"):
            ts_range = f"{ts_range}-{s['end_timestamp_str']}"
        # Include motion and intensity if available (from native analysis)
        extras = []
        if s.get("camera_motion"):
            extras.append(f"cam:{s['camera_motion']}")
        if s.get("action_intensity"):
            extras.append(f"intensity:{s['action_intensity']}")
        if s.get("audio_notes"):
            extras.append(f"audio:{s['audio_notes']}")
        extra_str = " | ".join(extras)
        line = (
            f"[{s['file']} {ts_range}] "
            f"({stars}) {s.get('description', 'no description')} "
            f"| mood:{s.get('mood', '?')} "
            f"| {s.get('highlights', '')}"
        )
        if extra_str:
            line += f" | {extra_str}"
        lines.append(line)

    # Add overall analysis notes (from native video mode)
    overall_scenes = [s for s in scenes if s.get("type") == "overall_analysis"]
    if overall_scenes:
        lines.append("\n## AI Analysis Overview:")
        for s in overall_scenes:
            if s.get("description"):
                lines.append(f"- Narrative: {s['description']}")
            if s.get("highlights"):
                lines.append(f"- Best moments: {s['highlights']}")
            if s.get("notes"):
                lines.append(f"- Pacing: {s['notes']}")
            if s.get("mood"):
                lines.append(f"- Dominant mood: {s['mood']}")

    # Add transcript context
    if transcript_scenes:
        lines.append("\n## Audio/Dialogue:")
        for s in transcript_scenes[:20]:  # limit
            lines.append(f"[{s['file']} {s.get('timestamp_str', '?')}] {s.get('description', '')}")

    scene_summary = "\n".join(lines)

    prompt = SCREENPLAY_PROMPT.format(
        scene_summary=scene_summary,
        total_duration=scene_log.get("total_duration", 0),
        video_count=scene_log.get("video_count", 0),
        target_minutes=target_minutes,
    )

    log.info("Generating screenplay (target: %.1f min, %d scenes)",
             target_minutes, len(visual_scenes))
    screenplay = claude_think_fn(prompt, timeout=120)

    if not screenplay:
        log.error("Screenplay generation returned empty")
        return ""

    # Save
    work_dir.mkdir(parents=True, exist_ok=True)
    sp_path = work_dir / "screenplay.md"
    sp_path.write_text(screenplay, encoding="utf-8")
    log.info("Screenplay saved: %s", sp_path)

    return screenplay
