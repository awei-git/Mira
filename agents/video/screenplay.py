"""Screenplay generator — turn scene analysis into a narrative structure.

Phase 2: scene_log.json → Claude → screenplay.md
"""
import json
import logging
from pathlib import Path

log = logging.getLogger("video.screenplay")


SCREENPLAY_PROMPT = """You are editing a travel video. You have a scene log from analyzed footage.
Your job: create a screenplay that tells a compelling 3-5 minute story.

## Scene Log
{scene_summary}

## Total footage: {total_duration:.0f}s across {video_count} files
## Target output: {target_minutes} minutes

## Instructions

1. Select the best moments (quality >= 3, interesting highlights)
2. Arrange them into a narrative arc:
   - Opening: establish the place, set the mood (15-30s)
   - Build: exploration, discovery, details (60-90s)
   - Heart: the most emotional/beautiful/interesting moments (60-90s)
   - Close: reflection, departure, or a striking final image (15-30s)
3. Vary the rhythm — mix long contemplative shots with quick cuts
4. Note transition types between clips (cut, fade, dissolve)
5. Mark where music should swell or quiet down

## Output format (Markdown)

# [Title — suggest a short evocative title]

## Act 1: [name] (0:00 - ~0:30)
- **[filename 00:12-00:28]** description — *editing note*
  - Transition: fade to next

## Act 2: [name] (0:30 - ~2:00)
- **[filename 01:20-01:35]** description — *editing note*
  - Transition: cut

[etc.]

## Music Notes
- Opening: [mood suggestion]
- Build: [mood suggestion]
- Climax: [mood suggestion]

## Pacing Notes
[Brief notes on rhythm and feel]

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
        line = (
            f"[{s['file']} {s.get('timestamp_str', '?')}] "
            f"({stars}) {s.get('description', 'no description')} "
            f"| mood:{s.get('mood', '?')} "
            f"| {s.get('highlights', '')}"
        )
        lines.append(line)

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
