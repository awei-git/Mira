---
activation_trigger: "Apply when applying consistent 3D LUT color grading across video clips using ffmpeg lut3d filter for a specific mood or look."
---

# LUT Color Grading Pipeline

**Tags:** video, color-grading, LUT, ffmpeg, post-production
**Source:** Professional color grading workflow, ffmpeg lut3d filter

## Core Principle

A 3D LUT (Look-Up Table) is a mathematical mapping that transforms input colors to output colors. Instead of manually adjusting curves, temperature, and saturation for every clip, apply a single LUT file for consistent, professional-grade color across the entire video.

LUTs separate the creative decision (what look do we want?) from the technical execution (which parameters achieve it?). An LLM can choose the right LUT by name/mood without needing to understand color science.

## LUT Types

| Type | Format | Use |
|------|--------|-----|
| .cube | Text-based 3D LUT | Industry standard, ffmpeg native |
| .3dl | Numeric 3D LUT | Legacy, Lustre/Resolve |
| Hald CLUT | PNG image | Editable in any image editor |

## FFmpeg Implementation

### Apply a .cube LUT
```bash
ffmpeg -i input.mp4 -vf "lut3d=cinematic_warm.cube:interp=tetrahedral" -c:a copy output.mp4
```

### Recommended processing order
```bash
ffmpeg -i input.mp4 -vf "
  eq=brightness=0.03:contrast=1.05,
  lut3d=grade.cube:interp=tetrahedral,
  unsharp=5:5:0.3
" output.mp4
```

Order: exposure correction → LUT → sharpening. Never sharpen before grading.

### Hald CLUT workflow (create custom looks)
```bash
# Generate neutral identity CLUT
ffmpeg -f lavfi -i haldclutsrc=8 -frames 1 neutral_clut.png

# Edit neutral_clut.png in any image editor (apply curves, color shifts, etc.)
# Then apply to video:
ffmpeg -i input.mp4 -i custom_clut.png -filter_complex "haldclut" output.mp4
```

### Built-in ffmpeg color presets (no LUT needed)
```bash
# Warm cinematic
colortemperature=6500,eq=contrast=1.1:brightness=0.02:saturation=1.15

# Cool moody
colortemperature=4500,eq=saturation=0.9:contrast=1.05

# Vintage film
curves=vintage,eq=saturation=0.8:contrast=1.05

# High contrast B&W
hue=s=0,eq=contrast=1.4:brightness=-0.05

# Teal and orange (blockbuster look)
colorbalance=rs=0.1:gs=-0.05:bs=-0.1:rh=-0.1:gh=0.05:bh=0.15
```

## LUT Selection by Mood

For the screenplay prompt, map mood → LUT:

| Mood | Look | LUT/Filter |
|------|------|-----------|
| Warm, nostalgic | Golden hour warmth | colortemperature=6500 + saturation boost |
| Cool, melancholic | Blue shadows | colortemperature=4500 + contrast lift |
| Dramatic | High contrast, desaturated | eq=contrast=1.3:saturation=0.7 |
| Natural/documentary | Minimal grading | eq=brightness=0.02 only |
| Vintage | Faded, lifted blacks | curves=vintage + vignette |
| Night/moody | Dark with color pops | eq=brightness=-0.05:contrast=1.2:saturation=1.1 |

## Per-Clip vs Global Grading

- **Global**: Apply one LUT to the whole video for consistency
- **Per-act**: Different LUT per narrative section (warm opening → cool middle → warm close)
- **Per-clip**: Only when mixing drastically different lighting conditions

## Common Mistakes

- Applying a LUT to already-graded footage — LUTs expect neutral/log input
- Over-grading — subtle is almost always better
- Not matching across clips — the viewer notices color jumps between cuts
- Using a LUT to fix bad exposure — fix exposure FIRST, then grade
