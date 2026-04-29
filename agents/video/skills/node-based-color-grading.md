---
activation_trigger: "Apply when color grading video in DaVinci Resolve using a structured node tree for isolated, non-destructive, and reversible corrections."
---

# Node-Based Color Grading

**Tags:** video, color-grading, davinci-resolve, cinematography, post-production

## Core Principle
Work in a node-based pipeline (DaVinci Resolve) where each correction is isolated to a specific node — enabling non-destructive, reversible, and precisely targeted grading rather than stacked layer effects.

## Node Architecture
Structure every grade with a consistent node tree:
1. **Input node** — Technical corrections only: white balance fix, exposure normalization, log-to-linear conversion if shooting in LOG/RAW
2. **Primary grade node** — Lift/gamma/gain (or curves) to set the overall tonal balance
3. **Creative LUT node** — Apply a look LUT at reduced opacity (30–70%) as a creative starting point
4. **Secondary correction nodes** — Isolated adjustments using qualifiers and power windows: sky, skin tones, shadows
5. **Output node** — Final output transform (color space conversion for delivery: Rec.709, P3, etc.)

## Primary Grade: Lift/Gamma/Gain
- **Lift** (blacks/shadows): set the darkest areas of the image. Avoid crushing to pure black unless intentional.
- **Gamma** (midtones): overall exposure feel. The most perceptually impactful adjustment.
- **Gain** (highlights): control the brightest areas without affecting shadows.
- Use the parade scope to verify: RGB channels should generally track together unless a creative color shift is intended.

## Secondary Grading with Qualifiers
- **HSL qualifier**: isolate a specific color range (e.g., skin tones, sky blue) for targeted adjustment
- **Power windows (masks)**: geometric isolation — darken edges with a radial vignette, brighten a specific subject
- **Tracking**: track a power window to a moving subject so the correction follows across the cut

## Scopes: The Ground Truth
Never trust the monitor alone. Read scopes before every major decision:
- **Waveform**: exposure and tonal distribution across the frame
- **Parade (RGB)**: color balance — equal channels = neutral; one channel high = color cast
- **Vectorscope**: saturation and hue. Skin tones should always plot on the "skin tone line" (roughly 10:30 on the vectorscope clock)

## Source
DaVinci Resolve official documentation; Lowepost "Professional Color Grading Techniques"; Artlist.io "How to Make Cinematic Color Grading"
