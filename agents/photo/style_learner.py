from __future__ import annotations

"""Style learner — extract editing style from reference photos.

Analyzes a collection of edited photos to build a style profile:
- Color temperature tendency (warm/cool/neutral)
- Contrast and tone curve preferences
- Saturation/vibrance levels
- Shadow and highlight treatment
- Common color palette
- Mood and subject patterns

The style profile is saved as JSON and used by the photo editor
to match the user's aesthetic in future edits.
"""
import json
import logging
import sys
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from sub_agent import claude_act, claude_think

log = logging.getLogger("photo.style_learner")


def learn_style(images: list[Path], workspace: Path,
                max_samples: int = 12) -> dict | None:
    """Analyze reference images and extract a style profile.

    Args:
        images: List of edited/finished photo paths
        workspace: Working directory for intermediate files
        max_samples: Max images to analyze (vision API costs)

    Returns:
        Style profile dict, or None on failure
    """
    workspace.mkdir(parents=True, exist_ok=True)

    # Select representative sample (spread across the collection)
    sample = _select_sample(images, max_samples)
    log.info("Analyzing %d/%d reference images", len(sample), len(images))

    # Phase 1: Analyze each image individually
    individual_analyses = []
    for i, img in enumerate(sample):
        log.info("Analyzing reference [%d/%d]: %s", i + 1, len(sample), img.name)
        analysis = _analyze_single_reference(img)
        if analysis:
            individual_analyses.append({
                "file": img.name,
                "analysis": analysis,
            })

    if not individual_analyses:
        log.error("No reference images could be analyzed")
        return None

    # Save individual analyses
    analyses_path = workspace / "reference_analyses.json"
    analyses_path.write_text(
        json.dumps(individual_analyses, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Phase 2: Synthesize into a unified style profile
    log.info("Synthesizing style profile from %d analyses", len(individual_analyses))
    profile = _synthesize_style(individual_analyses)

    if profile:
        profile_path = workspace / "style_profile.json"
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Style profile saved: %s", profile_path)

    return profile


def _select_sample(images: list[Path], max_n: int) -> list[Path]:
    """Select a representative sample spread across the collection."""
    if len(images) <= max_n:
        return images
    # Evenly spaced selection
    step = len(images) / max_n
    return [images[int(i * step)] for i in range(max_n)]


def _analyze_single_reference(image_path: Path) -> str | None:
    """Analyze a single reference image for style characteristics."""
    prompt = f"""Read this photograph and analyze its EDITING STYLE (not just content): {image_path}

Focus on the post-processing choices the photographer made:

1. **Color Temperature**: Warm, cool, or neutral? How far from daylight?
2. **Contrast**: High contrast (crushed blacks, blown highlights) or low contrast (lifted shadows, soft)? Matte/film look or punchy?
3. **Saturation**: Oversaturated, muted/desaturated, or natural? Selective saturation on specific colors?
4. **Color Grading**: Any split toning? Shadow color cast? Highlight color cast? Dominant color harmony (complementary, analogous, mono)?
5. **Tone Curve**: S-curve (contrast), lifted blacks (film look), crushed highlights? Where are the blacks — true black or lifted?
6. **Highlights/Shadows**: Recovered highlights or let them clip? Opened shadows or kept them dark?
7. **Sharpness/Clarity**: Over-sharpened, soft/dreamy, or balanced? Heavy clarity/texture or smooth?
8. **Vignette**: Dark edges? Light? None?
9. **Noise/Grain**: Clean, grainy, or film-grain added?
10. **Overall Mood**: One-word mood description + why the edits create that mood

Output your analysis as structured JSON:
```json
{{
    "temperature": "warm|cool|neutral",
    "temperature_shift": -100 to 100,
    "contrast_level": "high|medium|low",
    "contrast_value": -100 to 100,
    "saturation_level": "high|medium|low|muted",
    "vibrance_value": -100 to 100,
    "saturation_value": -100 to 100,
    "shadow_treatment": "lifted|deep|crushed",
    "shadow_color": "blue|teal|purple|brown|neutral",
    "highlight_treatment": "recovered|natural|clipped",
    "highlight_color": "warm|cool|neutral",
    "black_point": "true_black|lifted|crushed",
    "clarity": -100 to 100,
    "dehaze": -100 to 100,
    "vignette": "none|light|heavy",
    "grain": "none|subtle|heavy",
    "sharpness": "soft|balanced|sharp|over",
    "mood": "one word",
    "color_palette": ["color1", "color2", "color3"],
    "subject_type": "portrait|street|landscape|urban|abstract|other",
    "editing_notes": "brief description of the overall editing approach"
}}
```"""

    result = claude_act(prompt, cwd=image_path.parent, tier="light")
    return result if result else None


def _synthesize_style(analyses: list[dict]) -> dict | None:
    """Synthesize individual analyses into a unified style profile."""
    analyses_text = json.dumps(analyses, ensure_ascii=False, indent=2)

    prompt = f"""You are a professional photo editor analyzing a photographer's consistent editing style across multiple images.

Here are individual analyses of {len(analyses)} reference photos:

{analyses_text}

Synthesize these into a SINGLE style profile that captures the photographer's consistent preferences.
Look for PATTERNS — what appears in most images is the style; what appears in one is circumstantial.

Output a JSON style profile:
```json
{{
    "overall_mood": "2-3 word description of the dominant mood",
    "color_tendency": "description of color approach (e.g., 'warm with muted tones, lifted shadows')",
    "tone_curve": "description of tonal approach (e.g., 'medium contrast with lifted blacks, film-like')",

    "common_adjustments": {{
        "exposure_bias": 0.0,
        "contrast": 0,
        "highlights": 0,
        "shadows": 0,
        "whites": 0,
        "blacks": 0,
        "clarity": 0,
        "dehaze": 0,
        "texture": 0,
        "vibrance": 0,
        "saturation": 0,
        "temperature_shift": 0,
        "tint_shift": 0,
        "sharpness": 0,
        "noise_reduction": 0,
        "vignette": 0
    }},

    "shadow_color_cast": "color name or neutral",
    "highlight_color_cast": "color name or neutral",
    "black_point_style": "true_black|lifted|crushed",
    "grain_preference": "none|subtle|heavy",

    "color_palette": ["dominant color 1", "dominant color 2", "dominant color 3"],
    "subjects": ["most common subject types"],
    "signature_traits": ["3-5 distinctive traits that make this style recognizable"],

    "lightroom_preset_base": {{
        "Exposure2012": 0.00,
        "Contrast2012": 0,
        "Highlights2012": 0,
        "Shadows2012": 0,
        "Whites2012": 0,
        "Blacks2012": 0,
        "Clarity2012": 0,
        "Dehaze": 0,
        "Vibrance": 0,
        "Saturation": 0,
        "Sharpness": 0,
        "SharpenRadius": 1.0,
        "SharpenDetail": 25,
        "LuminanceSmoothing": 0,
        "ColorNoiseReduction": 25,
        "PostCropVignetteAmount": 0,
        "GrainAmount": 0
    }},

    "editing_philosophy": "1-2 sentences describing the overall approach"
}}
```

Use the Lightroom parameter ranges:
- Exposure: -5.0 to 5.0
- Most others: -100 to 100
- Sharpness: 0 to 150
- SharpenRadius: 0.5 to 3.0

Be specific with numbers — don't use 0 for everything. If the style consistently leans warm, put a positive temperature shift. If shadows are always lifted, put a positive Blacks2012 value. The numbers should be a STARTING POINT that gets the user 70-80% to their look."""

    result = claude_think(prompt, timeout=180, tier="heavy")
    if not result:
        return None

    # Extract JSON from response
    return _extract_json(result)


def _extract_json(text: str) -> dict | None:
    """Extract JSON from text that may contain markdown code blocks."""
    import re

    # Try code block first
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return None
