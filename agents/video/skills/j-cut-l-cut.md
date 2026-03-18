# J-Cut and L-Cut

**Tags:** video, editing, transitions, audio, technique
**Source:** Professional film editing technique, ffmpeg implementation

## Core Principle

In a standard cut, audio and video transition at the same frame. A J-cut or L-cut splits them:

- **J-cut**: Audio from the NEXT scene starts before the video cuts. The viewer hears what's coming, creating anticipation. Named after the J-shape in the timeline (audio extends left).
- **L-cut**: Audio from the CURRENT scene continues after the video has cut to the next. Creates continuity and emotional carry-over. Named after the L-shape (audio extends right).

These are the single biggest quality differentiator between amateur and professional edits. They make transitions feel invisible because they mimic how human perception works — we often hear before we see.

## When to Use

**J-cut:**
- Transitioning to a new location (hear the waves before seeing the beach)
- Building anticipation (hear crowd noise before revealing the concert)
- Dialogue: hear speaker B start while still showing speaker A's reaction

**L-cut:**
- Emotional carry-over (character finishes speaking, cut to scenery while voice lingers)
- Providing reaction time (speaker's words continue over listener's face)
- Smooth exit from a scene (ambient sound fades as new visuals begin)

## FFmpeg Implementation

### J-cut (audio from clip B starts 1s before video cut)
```bash
ffmpeg -i clipA.mp4 -i clipB.mp4 -filter_complex "
  [0:v]trim=0:10,setpts=PTS-STARTPTS[vA];
  [1:v]trim=0:10,setpts=PTS-STARTPTS[vB];
  [0:a]atrim=0:9,asetpts=PTS-STARTPTS[aA];
  [1:a]atrim=0:11,asetpts=PTS-STARTPTS[aB];
  [aA][aB]acrossfade=d=1:c1=tri:c2=tri[amix];
  [vA][vB]concat=n=2:v=1:a=0[vout]
" -map "[vout]" -map "[amix]" output.mp4
```

### L-cut (audio from clip A continues 1s into clip B's video)
Same structure but extend clip A's audio trim and delay clip B's audio.

### Multi-pass approach (more reliable)
1. Extract clip A video + extended audio
2. Extract clip B with offset audio start
3. Use `amix` or `acrossfade` to blend the overlap
4. Concatenate video tracks with hard cut

## Overlap Duration Guidelines

- **0.5s**: subtle, barely noticeable (good for fast pacing)
- **1-2s**: standard, professional feel
- **3-5s**: dramatic, cinematic (use sparingly)
- Match the overlap to the emotional weight of the transition

## Common Mistakes

- Making the audio offset too long — becomes confusing
- Using J/L cuts everywhere — some transitions should be hard cuts for impact
- Forgetting to crossfade the audio overlap — creates jarring volume change
- Using on action cuts — J/L cuts work best for scene or mood transitions
