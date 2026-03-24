"""Screenplay generator — turn scene analysis into a narrative structure.

Phase 2: scene_log.json → Claude → screenplay.md + edit_plan.json

Enhanced version injects taste profile, beat map, and editing skills
to produce both a human-readable screenplay and a machine-parseable edit plan.
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
    # Use Gemini for speed; fall back to claude_think_fn if needed
    from sub_agent import model_think
    screenplay = model_think(prompt, model_name="gemini", timeout=120)
    if not screenplay and claude_think_fn:
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


# ---------------------------------------------------------------------------
# Enhanced: generate_edit_plan (screenplay + structured JSON)
# ---------------------------------------------------------------------------

_EDIT_PLAN_PROMPT = """You are editing a video. You have scene analysis, music beat data, and the editor's style preferences.
Your job: create a precise edit plan that maps clips to musical phrases.

## Editor's Style
{taste_profile}

## Music Analysis
{beat_summary}

## Scene Analysis
{scene_summary}

## Total footage: {total_duration:.0f}s across {video_count} files
## Target output: match the full song ({song_duration:.0f}s)
## Content mode: {content_mode} (determines pacing and grade style)

## Instructions

1. **Select clips** — prioritize usability_score >= 3, prefer best_segment ranges
2. **Map to phrases** — each clip should align with phrase boundaries from the beat map
   - {content_mode_pacing}
3. **Choose transitions** based on content:
   - Hard cut: same location, matching energy (DEFAULT — 90%+ of cuts)
   - Motion-blur whip: between different locations or chapters
   - Dissolve: for time passing, mood shift, contemplative moments
4. **Assign grade type** per clip based on its lighting_type
5. **Plan narrative arc**:
   - Intro section (low energy phrases): establishing shots, gentle opening
   - Build section: exploration, variety
   - Peak section (high energy phrases): best moments, emotional core
   - Outro section: reflection, gentle close

## CRITICAL DURATION REQUIREMENT

You MUST generate enough clips to fill the ENTIRE song ({song_duration:.0f} seconds).
- Family mode: 3-5s per clip → you need approximately {clip_count_estimate} clips
- The sum of all clip durations MUST equal {song_duration:.0f}s (±5s)
- Do NOT stop after 10-15 clips. You need {clip_count_estimate} clips.
- Vary clip durations: mix 2s, 3s, 4s, and 5s clips for rhythm
- Reuse source files if needed — different segments from the same clip are fine

## Output: JSON only

Return ONLY this JSON (no markdown, no explanation):
{{
  "title": "short evocative title",
  "content_mode": "{content_mode}",
  "clips": [
    {{
      "source_file": "filename.MP4",
      "start_time": "MM:SS",
      "end_time": "MM:SS",
      "duration": 3.5,
      "phrase_idx": 0,
      "beat_time": 0.0,
      "transition_in": "cut|dissolve|whip|fade_from_black",
      "speed": 1.0,
      "grade_type": "golden_hour|night|indoor_warm|etc",
      "description": "brief content description",
      "narrative_role": "intro|build|peak|outro"
    }}
  ],
  "title_card": {{
    "text": "title text",
    "duration": 3.5,
    "position": "start"
  }},
  "credits": {{
    "text": "credits text",
    "duration": 5.0
  }}
}}
"""


def generate_edit_plan(scene_log: dict, beat_map: dict,
                       taste_profile: str, work_dir: Path,
                       content_mode: str = "family",
                       claude_think_fn=None) -> tuple:
    """Generate a structured edit plan with taste + beat awareness.

    Args:
        scene_log: dict from scene_analyzer.analyze_all()
        beat_map: dict from beat_analyzer.analyze_beats()
        taste_profile: full text of editing_taste_profile.md
        work_dir: directory for output
        content_mode: "travel" or "family"
        claude_think_fn: callable for LLM reasoning

    Returns:
        (screenplay_md: str, edit_plan: dict)
    """
    if not claude_think_fn:
        log.error("No LLM function provided")
        return "", {}

    scenes = scene_log.get("scenes", [])
    visual_scenes = [s for s in scenes if s.get("type") != "transcript"]
    if not visual_scenes:
        log.warning("No visual scenes")
        return "", {}

    # Build scene summary with new fields
    lines = []
    for s in visual_scenes:
        q = s.get("quality", 3)
        u = s.get("usability_score", q)
        ts_range = s.get("timestamp_str", "?")
        if s.get("end_timestamp_str"):
            ts_range = f"{ts_range}-{s['end_timestamp_str']}"

        best_seg = s.get("best_segment", {})
        best_str = ""
        if best_seg:
            best_str = f" best:{best_seg.get('start','?')}-{best_seg.get('end','?')}"

        lighting = s.get("lighting_type", "mixed")
        color_temp = s.get("color_temperature_est", "neutral")

        line = (
            f"[{s['file']} {ts_range}] "
            f"quality={q} usability={u} "
            f"lighting={lighting} temp={color_temp} "
            f"| {s.get('description', '')} "
            f"| mood:{s.get('mood', '?')} "
            f"| cam:{s.get('camera_motion', '?')} "
            f"| intensity:{s.get('action_intensity', '?')}"
            f"{best_str}"
        )
        lines.append(line)

    scene_summary = "\n".join(lines)

    # Beat summary
    from beat_analyzer import summarize_beat_map
    beat_summary = summarize_beat_map(beat_map)

    # Pacing instruction based on mode
    if content_mode == "travel":
        pacing = ("Travel mode: cut on individual BEATS (2-3s per clip). "
                  "Fast, driving momentum. Beat-synced cuts.")
    else:
        pacing = ("Family mode: cut on PHRASES (3-5s per clip). "
                  "Unhurried, observational. Phrase-synced cuts, NOT beat-synced.")

    # Trim scene summary if too large (keep under ~4000 chars)
    if len(scene_summary) > 4000:
        # Keep only the most useful lines
        trimmed_lines = scene_summary.split("\n")[:60]
        scene_summary = "\n".join(trimmed_lines) + f"\n... ({len(lines) - 60} more clips)"

    song_dur = beat_map.get("duration", 180)
    avg_clip = 4.0 if content_mode == "family" else 2.5
    clip_count_est = int(song_dur / avg_clip)

    prompt = _EDIT_PLAN_PROMPT.format(
        taste_profile=taste_profile[:2000],
        beat_summary=beat_summary,
        scene_summary=scene_summary,
        clip_count_estimate=clip_count_est,
        total_duration=scene_log.get("total_duration", 0),
        video_count=scene_log.get("video_count", 0),
        song_duration=beat_map.get("duration", 180),
        content_mode=content_mode,
        content_mode_pacing=pacing,
    )

    log.info("Generating edit plan (mode: %s, %d scenes, %d phrases)",
             content_mode, len(visual_scenes), len(beat_map.get("phrases", [])))

    # Use Gemini for edit plan (fast, good at structured JSON)
    from sub_agent import model_think
    result = model_think(prompt, model_name="gemini", timeout=120)

    if not result:
        log.error("Edit plan generation returned empty")
        return "", {}

    # Parse JSON from result
    edit_plan = _parse_edit_plan(result)
    _normalize_clip_keys(edit_plan)

    # Also generate human-readable screenplay using original function
    screenplay = generate_screenplay(
        scene_log, work_dir,
        target_minutes=beat_map.get("duration", 180) / 60,
        claude_think_fn=claude_think_fn,
    )

    # ── Duration validation ──
    if edit_plan.get("clips"):
        clips = edit_plan["clips"]
        total_dur = sum(c.get("duration", 0) for c in clips)
        target_dur = beat_map.get("duration", 180)
        if total_dur < target_dur * 0.8:
            log.warning("Edit plan too short: %.0fs vs %.0fs target. Regenerating with stronger constraint.",
                        total_dur, target_dur)
            # Retry with even stronger prompt
            retry_prompt = (
                f"The previous edit plan only had {len(clips)} clips totaling {total_dur:.0f}s. "
                f"The song is {target_dur:.0f}s. You need ~{clip_count_est} clips. "
                f"Generate a COMPLETE edit plan with enough clips to fill the entire song.\n\n"
                + prompt
            )
            result2 = model_think(retry_prompt, model_name="gemini", timeout=120)
            if result2:
                retry_plan = _parse_edit_plan(result2)
                retry_total = sum(c.get("duration", 0) for c in retry_plan.get("clips", []))
                if retry_total > total_dur:
                    edit_plan = retry_plan
                    log.info("Retry: %d clips, %.0fs (was %d clips, %.0fs)",
                             len(retry_plan.get("clips", [])), retry_total,
                             len(clips), total_dur)

    # ── Self-review pass ──
    if edit_plan.get("clips"):
        log.info("Self-review pass on edit plan (%d clips)", len(edit_plan["clips"]))
        edit_plan = _self_review_plan(
            edit_plan, taste_profile, beat_map, content_mode, claude_think_fn)

    # Save edit plan
    work_dir.mkdir(parents=True, exist_ok=True)
    plan_path = work_dir / "edit_plan.json"
    plan_path.write_text(json.dumps(edit_plan, indent=2, ensure_ascii=False))
    log.info("Edit plan saved: %s (%d clips)", plan_path,
             len(edit_plan.get("clips", [])))

    return screenplay, edit_plan


_SELF_REVIEW_PROMPT = """You are reviewing a video edit plan against the editor's taste profile.

## Editor's Style
{taste_profile}

## Content Mode: {content_mode}

## Music: {tempo:.0f} BPM, {song_duration:.0f}s, {num_phrases} phrases

## Current Edit Plan ({num_clips} clips, {total_dur:.0f}s total)
{plan_summary}

## Review Checklist

Check each item and fix if needed:

1. **Pacing variation** — Are all clips roughly the same duration? BAD. Mix short (2s) and long (5-6s) clips for rhythm.
2. **Narrative arc** — Is there a clear intro → build → peak → outro structure? Check narrative_role distribution.
3. **Mode-appropriate sync** — Family mode should use phrase-sync (3-5s clips). Travel mode should use beat-sync (2-3s clips).
4. **No duplicate sources** — Same source file used too many times? Spread variety.
5. **Transition variety** — 90%+ should be hard cuts. 1-2 whip transitions between location changes. No more.
6. **Emotional peak placement** — Best clips should be in the middle-to-late section, not wasted at the start.
7. **Opening and close** — Does it open with an establishing shot? End with a quiet/reflective moment?
8. **Total duration** — Must match the song duration ({song_duration:.0f}s). If clips total is off by >5s, fix it.

## Output

Return the COMPLETE revised edit plan as JSON (same format as input). If no changes needed, return the original.
Fix any issues you find. Only change what's necessary — don't rewrite clips that are fine.

Return ONLY the JSON."""


def _self_review_plan(edit_plan: dict, taste_profile: str,
                      beat_map: dict, content_mode: str,
                      claude_think_fn) -> dict:
    """Self-review pass: check edit plan against taste profile and fix issues."""
    clips = edit_plan.get("clips", [])
    if not clips:
        return edit_plan

    # Build plan summary
    lines = []
    for i, c in enumerate(clips):
        lines.append(
            f"[{i}] {c.get('source_file', '?')} "
            f"{c.get('start_time', '?')}-{c.get('end_time', '?')} "
            f"({c.get('duration', 0):.1f}s) "
            f"role={c.get('narrative_role', '?')} "
            f"transition={c.get('transition_in', 'cut')}"
        )
    plan_summary = "\n".join(lines)

    total_dur = sum(c.get("duration", 0) for c in clips)
    tempo = beat_map.get("tempo", 120)
    song_dur = beat_map.get("duration", 180)
    num_phrases = len(beat_map.get("phrases", []))

    prompt = _SELF_REVIEW_PROMPT.format(
        taste_profile=taste_profile[:2000],
        content_mode=content_mode,
        tempo=tempo,
        song_duration=song_dur,
        num_phrases=num_phrases,
        num_clips=len(clips),
        total_dur=total_dur,
        plan_summary=plan_summary,
    )

    from sub_agent import model_think
    result = model_think(prompt, model_name="gemini", timeout=120)
    if not result:
        log.warning("Self-review returned empty, keeping original plan")
        return edit_plan

    revised = _parse_edit_plan(result)
    if isinstance(revised, list):
        # Gemini sometimes returns just the clips array
        revised = {"clips": revised}
    _normalize_clip_keys(revised)
    if isinstance(revised, dict) and revised.get("clips"):
        log.info("Self-review: %d clips (was %d)", len(revised["clips"]), len(clips))
        # Preserve metadata from original
        revised.setdefault("title", edit_plan.get("title", ""))
        revised.setdefault("content_mode", edit_plan.get("content_mode", content_mode))
        revised.setdefault("title_card", edit_plan.get("title_card", {}))
        revised.setdefault("credits", edit_plan.get("credits", {}))
        return revised

    log.warning("Self-review parse failed, keeping original plan")
    return edit_plan


def _normalize_clip_keys(plan: dict):
    """Normalize clip field names from various LLM output formats."""
    if not isinstance(plan, dict):
        return
    for c in plan.get("clips", []):
        if "source" in c and "source_file" not in c:
            c["source_file"] = c.pop("source")
        if "clip" in c and "source_file" not in c:
            c["source_file"] = c.pop("clip")
        if "file" in c and "source_file" not in c:
            c["source_file"] = c.pop("file")
        if "filename" in c and "source_file" not in c:
            c["source_file"] = c.pop("filename")
        if "start" in c and "start_time" not in c:
            c["start_time"] = c.pop("start")
        if "end" in c and "end_time" not in c:
            c["end_time"] = c.pop("end")
        if "role" in c and "narrative_role" not in c:
            c["narrative_role"] = c.pop("role")
        if "transition" in c and "transition_in" not in c:
            c["transition_in"] = c.pop("transition")
        # Compute duration if missing
        if "duration" not in c and c.get("start_time") and c.get("end_time"):
            from scene_analyzer import _parse_ts
            st = str(c["start_time"])
            et = str(c["end_time"])
            c["start_time"] = st
            c["end_time"] = et
            c["duration"] = _parse_ts(et) - _parse_ts(st)
        c.setdefault("duration", 4.0)
        c.setdefault("speed", 1.0)
        c.setdefault("grade_type", "mixed")
        c.setdefault("source_file", "")


def _parse_edit_plan(text: str) -> dict:
    """Extract JSON edit plan from LLM response."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        log.error("Failed to parse edit plan JSON")
        return {"clips": [], "title_card": {}, "credits": {}}
