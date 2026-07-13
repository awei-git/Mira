---
activation_trigger: "Apply when aligning video cuts to music beats using librosa beat detection and ffmpeg to create rhythmically polished edits."
---

# Beat-Sync Editing

**Tags:** video, editing, music, rhythm, automation
**Source:** librosa + ffmpeg pipeline

## Core Principle

Detect beat timestamps in the music track, then place video cuts precisely on those beats. The human brain perceives beat-synced cuts as intentional and professional — even mediocre footage looks polished when cut to rhythm.

## Technical Pipeline

### 1. Beat Detection (Python)

```python
import librosa

y, sr = librosa.load("music.wav", sr=22050)
tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
beat_times = librosa.frames_to_time(beat_frames, sr=sr)
```

For more nuanced control:
- `librosa.onset.onset_detect()` — catches percussion hits, not just beats
- `librosa.beat.beat_track(units='time')` — direct timestamps
- Use `aubio` CLI: `aubiotrack music.wav` for quick beat list

### 2. Beat Selection Strategy

Not every beat needs a cut. Strategy:
- **Every beat**: fast energy montage (action, travel highlights)
- **Every 2nd beat**: moderate pacing (exploring, discovery)
- **Every 4th beat (downbeat)**: slow, contemplative sequences
- **Mixed**: vary density to match emotional arc

### 3. FFmpeg Execution

For each beat interval:
```bash
ffmpeg -ss {start} -i clip_pool.mp4 -t {duration} -vf "setpts=PTS-STARTPTS" segment_N.mp4
```

Then concatenate with optional transitions at beat points:
```bash
ffmpeg -i seg1.mp4 -i seg2.mp4 -filter_complex "xfade=transition=fade:duration=0.15:offset={beat_offset}" output.mp4
```

### 4. Speed Adjustment

When clip duration doesn't match beat interval:
- Slightly speed up/slow down: `setpts=(beat_duration/clip_duration)*PTS`
- Or trim to nearest beat and accept the natural endpoint

## When to Apply

- Music-driven montages (travel, highlight reels)
- Title sequences with rhythmic energy
- Action sequences where rhythm matches movement
- NOT for dialogue scenes, interviews, or narrative-driven cuts

## Common Mistakes

- Cutting on every single beat — monotonous, loses impact
- Ignoring visual rhythm — a pan or gesture that ends naturally between beats can feel right even if it's technically off-beat
- Using beat-sync as the only editing logic — combine with narrative structure
