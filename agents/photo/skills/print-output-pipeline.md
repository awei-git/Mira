# Print Output Pipeline

**Tags:** photo, editing, print, color-management, ICC, output

## Core Principle
Manage the full color-managed pipeline from calibrated display to final print — without calibration, soft proofing, and output sharpening, print output is guesswork.

## The Pipeline
```
Calibrate display → Set working color space → Get paper ICC profile →
Soft proof → Gamut check → Output sharpening → Test print → Final print
```

## 1. Monitor Calibration
- Use a hardware colorimeter (X-Rite i1Display, Datacolor Spyder) — software-only is insufficient.
- Target: D65 white point, gamma 2.2, luminance 80–120 cd/m².
- Recalibrate every 2–4 weeks. Monitors drift.

## 2. Working Color Space
- **sRGB**: web, social, standard lab prints
- **Adobe RGB (1998)**: wide-gamut inkjet — covers greens and cyans sRGB cannot
- **ProPhoto RGB**: RAW roundtrip and archival only — never deliver to external systems in ProPhoto

## 3. Soft Proofing
- Lightroom: Develop panel → Soft Proofing → select paper's ICC profile → enable "Simulate Paper & Ink"
- Create a Virtual Copy for the proof — adjustments here don't affect the master
- Adjust until the soft proof matches the master's intent

## 4. Gamut Warning
- Enable gamut warning (! icon) to see pixels outside the printer/paper's reproducible gamut
- Common out-of-gamut: saturated blues (sky), greens (foliage), deep reds
- Fix by selectively desaturating flagged areas — not globally

## 5. Output Sharpening
- Apply as the absolute last step, after all tonal and color work
- Lightroom Export: Paper type (Matte or Glossy) → Strength (Standard or High)
- Matte requires heavier sharpening than glossy (ink absorption diffuses micro-detail)

## 6. Test Print Protocol
- Print a test strip of the key tonal range before committing to a full print
- Evaluate under D50 (5000K) illumination — the standard print evaluation light
- Compare to the soft proof, not to the uncalibrated screen

## Source
X-Rite softproofing guide; Bay Photo Lab ICC profiles guide; photoworkout.com "Color Management for Photographers"
