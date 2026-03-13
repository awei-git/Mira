# Loudness and Mastering

**Tags:** podcast, audio, mastering, loudness, delivery-standards

## Core Principle
Deliver audio at platform-standard loudness levels (-16 LUFS stereo / -19 LUFS mono) so the podcast sounds consistent with professional shows and doesn't get turned down or distorted by platform normalization.

## 2026 Platform Standards
| Platform | Target LUFS | True Peak |
|----------|------------|-----------|
| Spotify | -14 LUFS | -1 dBTP |
| Apple Podcasts | -16 LUFS | -1 dBTP |
| YouTube | -14 LUFS | -1 dBTP |
| General podcast standard | -16 LUFS stereo / -19 LUFS mono | -1 dBTP |

When targeting multiple platforms: master to -16 LUFS. Platforms normalize louder content down; they don't boost quiet content up.

## The Mastering Chain (in order)
1. **Noise reduction** — remove consistent background noise (HVAC hum, mic hiss) using a noise profile. Do this first, before compression.
2. **EQ** — high-pass filter at 80–100Hz to remove rumble; gentle presence boost at 3–5kHz for speech clarity; low cut at 120Hz if recording on a condenser with proximity effect.
3. **De-esser** — tame harsh sibilance (S and SH sounds) at 5–10kHz. Use only as needed — over-de-essing lispy effect.
4. **Compression** — gentle compression (ratio 2:1 to 3:1, attack 5–10ms, release 100–200ms) to even out volume variation. Preserve dynamics; don't crush them.
5. **Limiting** — final true peak limiter set to -1 dBTP ceiling. This is the safety net, not the loudness target.
6. **Loudness normalization** — use an integrated loudness meter (LUFS) to measure and adjust to target.

## Measuring LUFS
- Use an integrated LUFS meter (not peak metering) — integrated LUFS measures the average loudness across the whole file.
- Measure before applying the final limiter; adjust gain to hit target; limit to prevent peaks.
- Tools: iZotope RX, Adobe Audition, Auphonic (automatic), DaVinci Resolve Fairlight.

## The Critical Check
After mastering, play 30 seconds back-to-back with a professionally produced podcast in the same genre. If yours is noticeably quieter, warmer, or more muffled — fix it before publishing.

## Source
Sone.app "Podcast Loudness Standards 2026"; Resound.fm "Audio Normalization: A Podcaster's Guide"; iZotope podcast production guides
