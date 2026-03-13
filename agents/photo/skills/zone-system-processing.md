# Zone System Processing

**Tags:** photo, editing, tonal-range, exposure, landscape, black-and-white

## Core Principle
Apply Ansel Adams' Zone System as a post-processing discipline — map tonal zones intentionally before touching a single slider.

## The Zone Scale
```
Zone 0   — Pure black. No detail.
Zone II  — Darkest detail. Just visible texture.
Zone V   — Middle gray. 18% reflectance. Metering anchor.
Zone VII — Bright highlight with full texture.
Zone IX  — Near-clipped. Barely held detail.
Zone X   — Pure white. No detail. Blown highlights.
```

## Zone Mapping Before Editing
Before opening any sliders:
1. Study the histogram — divide into shadow (left third), midtones (middle), highlights (right third).
2. Identify where key subjects are, and where you *want* them to fall in the output.
3. Articulate explicitly: "Subject's face → Zone VI. Sky → Zone VII. Foreground shadow → Zone III."
4. Only then open sliders — each adjustment serves a declared zone assignment.

## ETTR Logic in Post
- Pull highlights before lifting shadows — digital sensors protect highlights less than shadows.
- Check no zone you care about is clipped before adjusting anything else.

## Luminosity Masking for Zone-Specific Control
- Create masks targeting specific ranges: shadows (0–III), midtones (IV–VI), highlights (VII–X).
- Apply adjustments through these masks to affect only the intended zone.
- In Lightroom: Masking panel Luminance Range selector. In Photoshop: Apply Image for channel-based masks.

## B&W Conversion as Zone Assignment
- The HSL/Grayscale sliders reassign each color to a zone.
- Adams' approach: foliage (green) → Zone IV–V; sky (blue) → Zone III–IV; skin (orange/red) → Zone VI–VII.
- The conversion is not automatic — it is deliberate zone assignment for every dominant color.

## Print Zone Check
- Before finalizing: confirm darkest shadow with desired detail shows detail (not Zone 0); confirm brightest highlight with desired texture shows texture (not Zone X).
- Soft proof with paper ICC profile — printable zone range is often II–VIII, not 0–X. Adjust accordingly.

## Source
Ansel Adams "The Negative" and "The Print"; ProEdu Zone System guide; Fstoppers "Zone System in the Digital World"
