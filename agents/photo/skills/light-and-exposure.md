# Expose for Recovery

**Tags:** photo, exposure, histogram, ETTR, dynamic-range, sensor-physics

## Trigger
Activate this skill when you must:
1. Choose what to clip because the scene's dynamic range exceeds your sensor's.
2. Decide between underexposure (shadow noise) and overexposure (highlight loss).
3. Diagnose a RAW file that looks wrong in post to find your actual stop count.
4. Determine if ETTR (Expose to the Right) is still beneficial at high ISO.
5. Check for single-channel clipping when the luminance histogram looks clean.

Ignore for: white balance, color grading, composition, or scenes that fit within your camera's dynamic range.

## Core Asymmetry
Shadow recovery costs noise. Highlight recovery is impossible. The exchange rate depends on ISO and sensor architecture.

## Workflow

### Step 1: ETTR (Expose to the Right)
Default for RAW in any controlled situation:
1. Take a test shot at metered exposure.
2. Check the **per-channel** histogram — not combined luminance. Red clips first in warm light; blue clips first in cool light. The combined histogram hides single-channel clipping, which produces color shifts even when luminance looks fine.
3. If no channel clips, increase exposure +1/3 EV. Reshoot.
4. Repeat until any single channel begins to clip, then back off 1/3 EV.
5. The image will look overexposed on the LCD. Ignore it — you pull down in post.

**Why this works:** Sensors capture linearly. The brightest stop of a 14-bit file holds ~8,192 of 16,384 total levels. The darkest stop holds ~1 level. Exposing right packs data where you have headroom; pulling down is nearly free.

### Step 2: Know When ETTR Hurts
ETTR assumes read noise dominates shot noise — true at base ISO, not at high ISO.

| ISO range | ETTR benefit | Why |
|---|---|---|
| Base (100-200) | ~2 stops cleaner shadows | Read noise dominates; more photons = less relative noise |
| Mid (400-1600) | ~0.5-1 stop | Shot noise catches up; diminishing returns |
| High (3200+) | Near zero or negative | Sensor already amplifying; ETTR risks highlights for negligible shadow gain |

**Dual-gain / ISO-invariant sensors** (Sony IMX series, many modern mirrorless): A second analog gain stage kicks in at a specific ISO (often 640 or 800), dropping the read noise floor dramatically. Below that threshold: ETTR matters. Above it: protect highlights only — shadow push is nearly free. Find your sensor's inflection point at photonstophotos.net.

### Step 3: Recovery Triage

| Situation | Recovery | Cost |
|---|---|---|
| Underexposed shadows, ISO 100 | +3 to +4 stops | Visible noise in deepest shadows; usable with denoise |
| Underexposed shadows, ISO 3200 | +1 to +2 stops | Heavy noise, color shifts |
| Crushed blacks (clipped) | 0 | Data gone — flat gray, not detail |
| Hot highlights (not clipped) | -1 to -1.5 stops | Nearly free — this is hidden headroom |
| Blown highlights (any channel clipped) | 0 | Irrecoverable. Clipped channels produce color shifts even if luminance looks intact |
| One channel clipped, others intact | Luminance yes, color no | Desaturated or hue-shifted zone. Clean B&W conversion possible |

**Cost function:** Pushing +2 stops at ISO 100 ≈ correctly-exposed ISO 400 noise. Pushing +2 stops at ISO 1600 ≈ ISO 6400 noise. Recovery is always cheaper at low ISO.

### Step 4: Find Your Actual Headroom
The camera's rear LCD and its histogram lie — both are generated from the embedded JPEG, not the RAW data. Your RAW file almost always has more highlight headroom than the camera shows.

**How to find it:**
1. **In-camera:** Enable highlight blinkies ("blinkies"). If only specular highlights blink, you likely have 0.5-1 stop of hidden headroom in RAW. If diffuse surfaces blink, you're genuinely clipped.
2. **In post:** Pull the highlights slider to -100 in your RAW converter. If detail appears, you had hidden headroom. If it stays white, the data is gone. This is the ground truth — do it once per camera to calibrate your expectations.
3. **UniWB method** (advanced): Set a custom white balance where R=G=B multipliers are equal (often a very green-looking preset). Now the in-camera histogram reflects the raw channel with the *least* headroom. Ugly on screen, accurate for exposure. Switch back to normal WB for composition.
4. **Raw-specific tools:** RawDigger or FastRawViewer show the actual sensor data histogram, not the JPEG-derived one. If you shoot high-dynamic-range scenes regularly, these pay for themselves immediately.

**How much hidden headroom to expect:** Most cameras show JPEG clipping 0.3-1.0 stops before RAW clipping. Canon typically ~0.5 stops. Sony/Nikon often ~1 stop. This varies by white balance — tungsten WB gives more blue-channel headroom; daylight WB gives more red-channel headroom.

### Step 5: Difficult Scenes — Quick Reference
When dynamic range exceeds sensor and you must choose:

| Situation | Action | Reason |
|---|---|---|
| Subject in shadow, background blown | Expose for subject | Blown sky is expected; dark faces are rejected |
| Subject in light, shadows crushed | Let shadows go | Viewers read the lit subject first |
| No clear priority (even split) | Bracket -2/0/+2 EV | Faster than agonizing; merge or pick best frame |
| Specular highlights (sun, chrome, water) | Ignore in histogram | They clip in reality — meter diffuse highlights instead |

## Pitfalls
- **Over-recovery (HDR look):** Pushing shadows AND pulling highlights compresses everything to mid-gray. Shadows give form. Recover selectively, not globally.
- **Trusting the LCD in bright sun:** Screen reads 1-1.5 stops brighter than the file. Histogram only — and know that even the histogram lies (see Step 4).
- **Applying base-ISO logic at high ISO:** ETTR at ISO 6400 risks highlights for almost no shadow benefit. Know your sensor's crossover point.