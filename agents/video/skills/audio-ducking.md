# Audio Ducking and Mixing

**Tags:** video, audio, mixing, music, dialogue
**Source:** Broadcast audio engineering, ffmpeg sidechaincompress

## Core Principle

When dialogue/narration and background music coexist, the music must automatically lower (duck) during speech and return to full volume during silence. Without ducking, either the dialogue is drowned out or the music is inaudibly quiet throughout.

## FFmpeg Implementation

### Automatic sidechaincompress (best approach)
```bash
ffmpeg -i video_with_voice.mp4 -i music.mp3 -filter_complex "
  [0:a]aformat=fltp:44100:stereo[voice];
  [1:a]aformat=fltp:44100:stereo,volume=0.3[music];
  [music][voice]sidechaincompress=threshold=0.015:ratio=6:attack=200:release=1000:level_in=1:level_sc=1[ducked];
  [voice][ducked]amix=inputs=2:duration=first:normalize=0[aout]
" -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k output.mp4
```

Parameters:
- `threshold=0.015`: voice level that triggers ducking (lower = more sensitive)
- `ratio=6`: how much to reduce music (6:1 = aggressive)
- `attack=200`: ms to start ducking (200ms = quick but not jarring)
- `release=1000`: ms to restore volume (1s = smooth return)

### Manual ducking with volume keyframes
When you know speech timestamps:
```bash
# Duck music to 20% volume during speech at 5-15s and 20-30s
ffmpeg -i video.mp4 -i music.mp3 -filter_complex "
  [1:a]volume='if(between(t,5,15)+between(t,20,30),0.1,0.3)':eval=frame[ducked];
  [0:a][ducked]amix=inputs=2:duration=first[aout]
" -map 0:v -map "[aout]" output.mp4
```

### Loudness normalization (always apply last)
```bash
-af "loudnorm=I=-16:TP=-1.5:LRA=11"
```
EBU R128 standard: -16 LUFS for streaming, -24 LUFS for broadcast.

## When to Use

- Any video with both speech and music
- Podcast/interview with background music
- Travel video with narration over ambient music
- NOT needed when music and speech don't overlap

## Common Mistakes

- Setting threshold too low — music ducks on every ambient noise
- Release too short — music "pumps" up and down noticeably
- Not normalizing loudness at the end — platform will auto-normalize and crush dynamics
- Forgetting to match sample rates — causes subtle audio glitches
