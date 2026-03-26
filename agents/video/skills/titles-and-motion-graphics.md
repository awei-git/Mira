# Titles and Motion Graphics

## Title Card Types
Every card serves exactly one function. If it's doing two, split it.

- **Identification** — name, location, date. Example: `SARAH CHEN / Lead Engineer, Waymo`
- **Orientation** — context the audience needs before the next segment makes sense. Example: `DETROIT, MI — March 2024` before a factory tour.
- **Emphasis** — one statistic or phrase pulled out for impact. Example: `3.2 million units recalled` over B-roll.
- **Transition** — signals a section break or time jump. Example: `TWO WEEKS LATER` or a chapter title card.

## When to Use Each Type
- **Use Identification** when a new speaker appears on screen for the first time
- **Use Orientation** when location/time context is missing from preceding shots
- **Use Emphasis** when a key statistic or quote appears in voiceover/narration
- **Use Transition** when topic changes or time passes between scenes

## Typography Specs
- Typeface: medium-to-bold weight; must be legible at 50% scale on a phone screen
- Case: sentence case or ALL-CAPS for ≤5 words; never mixed-case for longer strings
- Color: white text + semi-transparent black drop shadow (30-50% opacity) or backing bar (60-80% opacity)
- Positioning: all text inside title-safe zone (10% inset from every edge)

## Technical Notes
- **Motion Blur:** Apply a 180° shutter angle (or 50% motion blur) to animated text for natural-looking movement.
- **Mobile Readability:** For text that must be legible on mobile, ensure the primary font size is at least 1/20th of the screen's vertical height (e.g., 90px on a 1920x1080 frame).

## Lower Third Format
- **Line 1:** Name — bold, larger (120-140% of line 2)
- **Line 2:** Title/role — regular weight, smaller, optionally different color (subtle accent)
- **Trigger:** Within 2–3 seconds of the subject starting to speak
- **Hold duration:** 4–6 seconds minimum for two lines
- **Remove trigger:** When subject stops speaking for 3+ seconds or camera cuts away

## Motion Checklist
1. **Easing on everything.** Linear motion looks cheap. Use ease-in-out as default (After Effects: Easy Ease; Premiere: 50% curve).
2. **Animate in:** 10–15 frames (0.3–0.5s at 24fps). Start animation on first spoken word.
3. **Hold for read time:** word count × 0.3 seconds, minimum. A 10-word lower third holds at least 3 seconds.
4. **Animate out faster than in.** If in = 12 frames, out = 8 frames. Trigger out animation when subject's sentence ends.
5. **Hierarchy through timing:** primary info appears first; secondary info follows 5–10 frames later (0.2–0.4s delay).
6. **No unmotivated motion.** If an element has no reason to move, it shouldn't. Motion directs attention; decorative animation competes with footage.

## Common Mistakes & Fixes
- **Mistake:** Text is hard to read over busy backgrounds.
  **Fix:** Increase backing bar opacity to 80% or use a stronger drop shadow (50% opacity, 8-10px spread).
- **Mistake:** Lower third appears/disappears too abruptly.
  **Fix:** Add a 4-frame (0.17s) opacity fade to the beginning and end of all position animations.
- **Mistake:** Emphasis card distracts from, rather than highlights, the B-roll.
  **Fix:** Shorten hold time to word count × 0.2 seconds. Animate out 2 seconds after the VO mentions the stat.
- **Mistake:** Transition cards feel disconnected from the edit.
  **Fix:** Time the card's animation peak (e.g., scale at 100%) to the exact frame of the cut or audio beat.

## Implementation Examples
**After Effects:**
- Build templates with Essential Graphics properties for text
- Use transform keyframes with Easy Ease (F9)
- Set hold keyframes for duration calculation

**Premiere Pro:**
- Use Essential Graphics templates
- Apply "Ease In/Out" to position/opacity keyframes
- Use markers to time animations to dialogue

**DaVinci Resolve:**
- Create Fusion templates with text+ nodes
- Use spline editor for smooth easing curves
- Time animations to audio waveform peaks