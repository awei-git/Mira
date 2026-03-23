# Video Agent v2 Architecture Plan

## Problem Statement

Current pipeline uploads each clip individually to Gemini for analysis.
- 88 clips × 4 min/clip = **6 hours** just for Phase 1 (SEE)
- Cost: ~$44 for 88 Gemini Pro native video calls
- No cross-clip context (each clip analyzed in isolation)
- No pre-filtering (wastes API on blurry/unusable footage)

Benchmark: Human edit of KensicoDam scored **8.9/10**. Goal: reach 8.0+.

## Core Architectural Change: Supercut Analysis

Instead of uploading 88 individual clips, **concat all clips into one supercut** and analyze in a single Gemini call.

```
88 clips (34 min raw) → ffmpeg concat (720p proxy) → 1 Gemini Pro call → full scene log
```

- Cost: 1 call, ~600k tokens ≈ **$0.75** (vs $44)
- Time: ~10 min total (vs 6 hours)
- Bonus: Gemini sees ALL footage in context, can make cross-clip judgments

## Pipeline: 6 Phases

### Phase 0: TRIAGE (local, no API)

Fast local pre-filter using ffmpeg only. No API calls.

```python
def triage(input_dir) -> triage.json:
    for clip in input_dir:
        # 1. Basic stats (duration, resolution, fps)
        # 2. Extract 3 frames (10%, 50%, 90%)
        # 3. Compute: blur score (laplacian variance),
        #             brightness (mean pixel),
        #             motion (frame diff between samples)
        # 4. Auto-reject: <1s, pure black, laplacian < threshold, lens cap
        # 5. Detect: high fps (slow-mo source), audio presence

    # Output: ranked list with local quality scores
    # Reject ~20-30% of clips (unusable)
```

Time: ~2 min for 88 clips (pure ffmpeg, no uploads).

### Phase 1: SEE (single supercut upload)

```python
def see(triage, input_dir) -> scene_log.json:
    # 1. Keep only clips that passed triage
    good_clips = [c for c in triage if c.score >= threshold]

    # 2. Build 720p proxy supercut with burned-in clip markers
    #    - Scale to 720p (small enough for upload)
    #    - Burn clip filename + index as overlay text
    #    - This lets Gemini reference specific clips by name
    proxy = build_proxy_supercut(good_clips, scale=720)

    # 3. Single Gemini Pro native video call
    #    - Analyzes entire footage in context
    #    - Returns per-scene breakdown with clip references
    #    - Gets: content, mood, quality, camera motion, subjects,
    #            lighting, audio notes, best segments
    scene_log = gemini_analyze_supercut(proxy)

    # 4. Content mode detection (travel vs family)
    content_mode = detect_mode(scene_log)
```

Time: ~8 min (1 upload + 1 analysis).
Key: Gemini sees the FULL footage, can judge relative quality and suggest narrative.

### Phase 2: THINK (multi-pass planning)

```python
def think(scene_log, beat_map, taste_profile) -> edit_plan.json:
    # Pass 1: Narrative structure
    #   Claude picks overall arc, chapter divisions, emotional peaks
    #   Input: scene_log + taste_profile + content_mode
    narrative = claude_think(narrative_prompt)

    # Pass 2: Clip-to-beat mapping
    #   Claude maps specific clips to phrase/beat positions
    #   Input: narrative + beat_map + scene_log
    #   Constraint: total duration = song duration
    edit_plan = claude_think(mapping_prompt)

    # Pass 3: Self-review
    #   Claude checks its own plan against taste profile
    #   "Would WA approve this cut? What would he change?"
    revised_plan = claude_think(self_review_prompt)
```

Important: The self-review pass catches issues like:
- Monotonous pacing (all clips same duration)
- Missing narrative arc (no build/climax)
- Wrong mode application (beat-sync for family content)

### Phase 3: DO (adaptive rendering)

```python
def do(edit_plan, source_dir) -> clips/:
    for clip in edit_plan.clips:
        # 1. Per-clip color grade based on scene analysis
        grade = compute_grade(clip.lighting_type, clip.color_temp,
                              content_mode, user_preference)

        # 2. Handle slow-mo sources (fps > 60)
        if source_fps > 60:
            apply fps conversion (minterpolate for smooth, fps filter for normal)

        # 3. Render with ffmpeg
        render_clip(clip, grade)

        # 4. Verify: check output is not black/corrupt
        verify_rendered_clip(output)  # check brightness, duration

    # 5. Cross-clip color check
    #    Compare average color of adjacent clips
    #    Flag any with deltaE > threshold for re-grade
    check_color_continuity(clips)
```

### Phase 4: MIX (audio engineering)

```python
def mix(rough_cut, music, edit_plan) -> final.mp4:
    # 1. Assemble rough cut (concat)
    # 2. Music mix (85% music, 12% ambient)
    #    - Use normalize=0 in amix (prevent volume halving bug)
    # 3. Speech ducking (if transcript available)
    #    - Detect speech segments → lower music 6dB during speech
    # 4. Fade in/out on music (2s fade in, 3s fade out)
```

### Phase 5: REVIEW (iterative)

```python
def review(final, edit_plan, taste_profile) -> review.json:
    # 1. Upload final video to Gemini Pro
    # 2. Score on 8 dimensions (pacing, color, narrative, sync, etc.)
    # 3. If overall < 7.5:
    #    - Extract fix proposals (re-grade, replace, re-time, remove)
    #    - Apply fixes (only re-render affected clips)
    #    - Re-assemble, re-mix
    #    - Re-review (max 2 iterations)
```

## Benchmark Protocol

To validate improvements, run both pipelines on KensicoDam footage:

```
Source: 88 clips, 34 min (满溪/2023-08/20230819满溪大坝游乐场)
Human edit: 20230819-ManxiAtKensicoDamPlayground.mp4 (3:16, scored 8.9/10)
Music: Curtis Cole - Give It a Go (2:46)

1. Run v2 pipeline → AI_edit.mp4
2. Score AI_edit with same Gemini reviewer
3. Compare dimension-by-dimension with human edit
4. Identify top 3 gaps → targeted improvements
5. Re-run, iterate until gap < 1.0 on all dimensions
```

## File Changes

| File | Change |
|------|--------|
| `triage.py` | **NEW** — local quality pre-filter |
| `scene_analyzer.py` | Add `build_proxy_supercut()` + `analyze_supercut()` |
| `screenplay.py` | Add self-review pass in `generate_edit_plan()` |
| `editor.py` | Add `verify_rendered_clip()` + `check_color_continuity()` |
| `handler.py` | Wire up new triage phase, supercut path |
| `video_reviewer.py` | No changes needed |

## Key Metrics

| Metric | Current | Target |
|--------|---------|--------|
| SEE phase time (88 clips) | ~6 hours | ~10 min |
| SEE phase cost | ~$44 | ~$0.75 |
| Benchmark score | untested | 8.0+ (vs 8.9 human) |
| End-to-end time | ~7 hours | ~25 min |
