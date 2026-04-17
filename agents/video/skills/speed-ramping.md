---
activation_trigger: "Apply when dynamically varying playback speed within a shot for dramatic emphasis, creating smooth velocity curves between normal and slow-motion."
---

# Speed Ramping

**Tags:** video, editing, speed, slow-motion, technique
**Source:** Action/travel video technique, ffmpeg implementation

## Core Principle

Speed ramping is the controlled transition between different playback speeds within a single shot. Unlike a hard cut between slow-mo and normal speed, a ramp creates a smooth velocity curve that feels organic and dramatic.

The classic pattern: normal speed → sudden slow-mo at the peak moment → accelerate back to normal. Creates a "bullet time" feeling without the budget.

## When to Use

- **Action peak**: a jump, a splash, a throw — slow at the apex
- **Reveal moments**: walk around a corner, curtain lifts — slow to savor
- **Emotion emphasis**: a smile, a glance, a reaction
- **Travel transitions**: speed up the mundane (walking), slow the beautiful (sunset)
- **NOT for**: dialogue, interviews, narrative scenes

## Speed Levels

| Speed | setpts multiplier | Feel |
|-------|-------------------|------|
| 4x fast | 0.25*PTS | Time-lapse, montage |
| 2x fast | 0.5*PTS | Quick travel, transitions |
| 1x normal | 1.0*PTS | Standard |
| 0.5x slow | 2.0*PTS | Gentle emphasis |
| 0.25x slow | 4.0*PTS | Dramatic slow-mo |

## FFmpeg Implementation

### Simple slow-mo (entire clip)
```bash
ffmpeg -i input.mp4 -vf "setpts=2.0*PTS" -af "atempo=0.5" output.mp4
```

### Speed ramp (3 segments: normal → slow → normal)
```bash
# Segment 1: normal speed (0-5s)
ffmpeg -ss 0 -i input.mp4 -t 5 -vf "setpts=PTS-STARTPTS" -an seg1.mp4

# Segment 2: slow-mo (5-7s source → plays over 4s)
ffmpeg -ss 5 -i input.mp4 -t 2 -vf "setpts=2.0*PTS,minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" -r 30 -an seg2.mp4

# Segment 3: back to normal (7-12s)
ffmpeg -ss 7 -i input.mp4 -t 5 -vf "setpts=PTS-STARTPTS" -an seg3.mp4

# Concatenate
printf "file 'seg1.mp4'\nfile 'seg2.mp4'\nfile 'seg3.mp4'" > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy output.mp4
```

### Smooth slow-mo with frame interpolation
```bash
ffmpeg -i input.mp4 -vf "minterpolate=fps=120:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" -r 30 output.mp4
```

Parameters:
- `mi_mode=mci` — motion compensated interpolation (best quality)
- `mc_mode=aobmc` — adaptive overlapped block motion compensation
- `me_mode=bidir` — bidirectional motion estimation
- `vsbmc=1` — variable-size block motion compensation

### Audio handling
- `atempo` is limited to 0.5-2.0 range per filter
- For 4x speed: chain `atempo=2.0,atempo=2.0`
- For 0.25x speed: chain `atempo=0.5,atempo=0.5`
- Often better to drop audio during slow-mo and use music instead

## Smooth Ramp Transitions

The hard cut between speeds is jarring. Options:
1. **Crossfade between segments**: brief dissolve masks the speed change
2. **Frame blending at boundaries**: `tblend=all_mode=average` on 2-3 frames
3. **Cut on motion**: place the speed change at a moment of peak motion — the eye is already tracking movement so the speed shift feels natural
4. **Match with audio swell**: drop music bass at slow-mo start, bring it back at resume

## Common Mistakes

- Slow-mo on footage that's already 24fps — not enough frames, will stutter
- minterpolate on fast camera motion — creates artifacts (ghosting)
- Speed ramping every shot — loses impact, becomes a gimmick
- Forgetting audio — silent slow-mo with no music feels broken
