"""Per-image aesthetic editor.

Analyzes each photo individually via vision model, generates
specific darktable editing parameters, renders, and scores the result.

Pipeline:
1. Vision model analyzes the camera JPG preview
2. Based on style DNA + this specific image, decides editing parameters
3. Generates darktable XMP sidecar with those parameters
4. darktable-cli renders the RAW
5. Scorer evaluates the result
6. If score too low, iterate with adjusted parameters
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from sub_agent import claude_act
from dt_xmp import (
    make_exposure, make_filmic, make_colorbalance,
    make_tone_equalizer, write_xmp,
)

log = logging.getLogger("photo.editor")

DARKTABLE_CLI = "/Applications/darktable.app/Contents/MacOS/darktable-cli"
STYLE_DNA = Path(__file__).parent.parent / "shared/soul/learned/wa-photography-style-dna.md"
OUTPUT_DIR = Path(__file__).parent / "output"


def analyze_photo(image_path: Path) -> dict:
    """Vision model analyzes a photo and prescribes specific editing parameters."""
    style_context = ""
    if STYLE_DNA.exists():
        style_context = STYLE_DNA.read_text()

    prompt = f"""Read this photograph: {image_path}

You are a professional photo editor. Analyze this specific image and prescribe exact editing parameters.

## Style Reference
{style_context}

## Your Task
Look at THIS specific image and decide:
1. What is the mood/atmosphere you want to achieve?
2. What are the specific problems to fix (exposure, white balance, contrast)?
3. What color grading would enhance this scene?

Then output EXACT numeric parameters for darktable modules.

Output as JSON — every value must be a number, not a description:
```json
{{
    "analysis": {{
        "scene_type": "landscape|portrait|street|nature",
        "light_condition": "golden_hour|blue_hour|overcast|harsh_midday|indoor",
        "mood_target": "one sentence describing the target mood",
        "key_issues": ["issue 1", "issue 2"]
    }},
    "exposure": {{
        "ev": 0.0,
        "black": 0.0
    }},
    "filmic": {{
        "white_ev": 4.0,
        "black_ev": -8.0,
        "contrast": 1.0,
        "latitude": 0.01,
        "saturation": 0.0
    }},
    "colorbalance": {{
        "contrast": 0.0,
        "vibrance": 0.0,
        "saturation_global": 0.0,
        "chroma_global": 0.0,
        "chroma_shadows": 0.0,
        "chroma_highlights": 0.0,
        "brilliance_global": 0.0,
        "brilliance_shadows": 0.0,
        "brilliance_highlights": 0.0,
        "shadows_Y": 0.0,
        "shadows_C": 0.0,
        "shadows_H": 0.0,
        "midtones_Y": 0.0,
        "midtones_C": 0.0,
        "midtones_H": 0.0,
        "highlights_Y": 0.0,
        "highlights_C": 0.0,
        "highlights_H": 0.0
    }},
    "tone_eq": {{
        "blacks": 0.0,
        "shadows": 0.0,
        "midtones": 0.0,
        "highlights": 0.0,
        "whites": 0.0,
        "speculars": 0.0
    }}
}}
```

## Parameter Guidelines — CRITICAL: darktable values are EXTREMELY sensitive
- exposure.ev: typically -1.0 to +2.0. Positive brightens.
- filmic.white_ev: 2.0 to 6.0. Lower = more highlight compression.
- filmic.black_ev: -10.0 to -4.0. Higher (less negative) = more shadow lift.
- filmic.contrast: 0.8 to 1.5. >1.0 = more contrast.
- colorbalance.contrast: -0.2 to 0.2. Adds midtone contrast.
- colorbalance.vibrance: -0.2 to 0.2. Boosts weak colors more than strong.
- colorbalance.saturation_global: -0.1 to 0.1.
- colorbalance.chroma_global: -0.05 to 0.05. VERY sensitive.
- colorbalance.chroma_shadows / chroma_highlights: -0.03 to 0.03.
- colorbalance Y/C/H: luminance/chroma/hue shifts per tonal range.
  - H values are in radians (-pi to pi). For warm shadows, H ~ 0.5-1.0. For cool highlights, H ~ -1.5 to -2.0.
  - C values: 0.001 to 0.01 MAX. Even 0.01 is a strong color push. 0.005 is subtle. NEVER exceed 0.02.
  - Y values: -0.1 to 0.1 for luminance shifts.
- tone_eq: -1.0 to +1.0 EV per zone. Negative darkens, positive brightens.
- brilliance_*: -0.1 to 0.1.

IMPORTANT: These are NOT percentage values. They are absolute multipliers in a linear pipeline.
A chroma value of 0.1 will create an extreme, unnatural color cast. Keep C values under 0.01.

Be SPECIFIC to this image. Don't give generic defaults. Look at the actual content."""

    result = claude_act(prompt, cwd=image_path.parent, tier="light")
    if not result:
        return {}

    import re
    parsed = None
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', result, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    if not parsed:
        m = re.search(r'\{.*\}', result, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                pass
    if not parsed:
        return {}
    return _clamp_params(parsed)


def _clamp_params(params: dict) -> dict:
    """Hard-clamp colorbalance values to prevent color catastrophe."""
    cb = params.get("colorbalance", {})
    if cb:
        clamps = {
            "contrast": (-0.3, 0.3),
            "vibrance": (-0.3, 0.3),
            "saturation_global": (-0.15, 0.15),
            "chroma_global": (-0.05, 0.05),
            "chroma_shadows": (-0.03, 0.03),
            "chroma_highlights": (-0.03, 0.03),
            "chroma_midtones": (-0.03, 0.03),
            "brilliance_global": (-0.1, 0.1),
            "brilliance_shadows": (-0.1, 0.1),
            "brilliance_highlights": (-0.1, 0.1),
            "brilliance_midtones": (-0.1, 0.1),
        }
        for key in ("shadows_C", "midtones_C", "highlights_C", "global_C"):
            clamps[key] = (-0.015, 0.015)
        for key in ("shadows_Y", "midtones_Y", "highlights_Y", "global_Y"):
            clamps[key] = (-0.15, 0.15)

        for key, (lo, hi) in clamps.items():
            if key in cb:
                original = cb[key]
                cb[key] = max(lo, min(hi, cb[key]))
                if cb[key] != original:
                    log.info("Clamped colorbalance.%s: %.4f -> %.4f", key, original, cb[key])
        params["colorbalance"] = cb

    te = params.get("tone_eq", {})
    if te:
        for key in te:
            if isinstance(te[key], (int, float)):
                original = te[key]
                te[key] = max(-1.5, min(1.5, te[key]))
                if te[key] != original:
                    log.info("Clamped tone_eq.%s: %.4f -> %.4f", key, original, te[key])
        params["tone_eq"] = te

    return params


def params_to_xmp(params: dict, raw_path: Path) -> Path:
    """Convert analysis params dict to darktable XMP sidecar."""
    exp = params.get("exposure", {})
    film = params.get("filmic", {})
    cb = params.get("colorbalance", {})
    te = params.get("tone_eq", {})

    history = [
        {
            "operation": "exposure",
            "modversion": 7,
            "params": make_exposure(
                ev=exp.get("ev", 0.0),
                black=exp.get("black", 0.0),
            ),
        },
        {
            "operation": "filmicrgb",
            "modversion": 6,
            "params": make_filmic(
                white_ev=film.get("white_ev", 4.0),
                black_ev=film.get("black_ev", -8.0),
                contrast=film.get("contrast", 1.0),
                latitude=film.get("latitude", 0.01),
                saturation=film.get("saturation", 0.0),
            ),
        },
        {
            "operation": "colorbalancergb",
            "modversion": 5,
            "params": make_colorbalance(
                contrast=cb.get("contrast", 0.0),
                vibrance=cb.get("vibrance", 0.0),
                saturation_global=cb.get("saturation_global", 0.0),
                chroma_global=cb.get("chroma_global", 0.0),
                chroma_shadows=cb.get("chroma_shadows", 0.0),
                chroma_highlights=cb.get("chroma_highlights", 0.0),
                brilliance_global=cb.get("brilliance_global", 0.0),
                brilliance_shadows=cb.get("brilliance_shadows", 0.0),
                brilliance_highlights=cb.get("brilliance_highlights", 0.0),
                shadows_Y=cb.get("shadows_Y", 0.0),
                shadows_C=cb.get("shadows_C", 0.0),
                shadows_H=cb.get("shadows_H", 0.0),
                midtones_Y=cb.get("midtones_Y", 0.0),
                midtones_C=cb.get("midtones_C", 0.0),
                midtones_H=cb.get("midtones_H", 0.0),
                highlights_Y=cb.get("highlights_Y", 0.0),
                highlights_C=cb.get("highlights_C", 0.0),
                highlights_H=cb.get("highlights_H", 0.0),
            ),
        },
        {
            "operation": "toneequal",
            "modversion": 2,
            "params": make_tone_equalizer(
                blacks=te.get("blacks", 0.0),
                shadows=te.get("shadows", 0.0),
                midtones=te.get("midtones", 0.0),
                highlights=te.get("highlights", 0.0),
                whites=te.get("whites", 0.0),
                speculars=te.get("speculars", 0.0),
            ),
        },
    ]

    xmp_path = OUTPUT_DIR / f"{raw_path.stem}.xmp"
    write_xmp(history, xmp_path, derived_from=raw_path.name)
    return xmp_path


def render_with_darktable(raw_path: Path, xmp_path: Path, output_path: Path) -> bool:
    """Render RAW using darktable-cli with XMP sidecar."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        DARKTABLE_CLI, str(raw_path), str(xmp_path), str(output_path),
        "--hq", "true", "--apply-custom-presets", "false",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if output_path.exists() and output_path.stat().st_size > 0:
            log.info("Rendered: %s (%.1f MB)", output_path.name,
                     output_path.stat().st_size / 1024 / 1024)
            return True
        log.error("Render failed: %s", result.stderr[-200:] if result.stderr else "no output")
        return False
    except subprocess.TimeoutExpired:
        log.error("Render timed out for %s", raw_path.name)
        return False


def apply_color_match(target_path: Path, reference_path: Path, output_path: Path) -> bool:
    """Apply color matching from reference image to target."""
    try:
        from color_matcher import ColorMatcher
        from color_matcher.io_handler import load_img_file, save_img_file
        from color_matcher.normalizer import Normalizer

        target = load_img_file(str(target_path))
        reference = load_img_file(str(reference_path))

        cm = ColorMatcher()
        matched = cm.transfer(src=target, ref=reference, method="mkl")
        matched = Normalizer(matched).uint8_norm()

        save_img_file(matched, str(output_path))
        log.info("Color matched: %s -> style of %s", target_path.name, reference_path.name)
        return True
    except Exception as e:
        log.error("Color match failed: %s", e)
        return False


def review_edit(original_path: Path, edited_path: Path, params: dict) -> dict:
    """Vision model reviews the rendered edit vs. the original.

    Returns {"approved": bool, "score": 0-10, "critique": str, "suggestions": str}
    """
    prompt = f"""You are a photo editing reviewer. Compare the ORIGINAL and EDITED versions of this photo.

Original: {original_path}
Edited: {edited_path}

The editing parameters applied were:
{json.dumps(params, indent=2, default=str)}

Evaluate the edit critically:
1. Did the color grading improve the photo or make it worse?
2. Are there obvious problems? (color cast, over-saturation, crushed shadows/blown highlights, unnatural tones)
3. Does the edit serve the mood of the scene?
4. Score the edit 0-10 (0=ruined, 5=no improvement, 7=good, 10=perfect)

Output as JSON:
```json
{{
    "approved": true/false,
    "score": 7,
    "critique": "what's wrong or right with this edit",
    "suggestions": "specific parameter changes if not approved, e.g. 'reduce chroma_global from 0.15 to 0.05, shadows_H is too warm'"
}}
```

Be HONEST. If the edit is bad, say so. A color cast across the whole image, sepia/monochrome effect, or loss of natural color is a FAIL."""

    result = claude_act(prompt, cwd=edited_path.parent, tier="light")
    if not result:
        return {"approved": False, "score": 0, "critique": "review failed", "suggestions": ""}

    import re as _re
    m = _re.search(r'```(?:json)?\s*\n(.*?)\n```', result, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _re.search(r'\{.*\}', result, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"approved": False, "score": 0, "critique": result[:200], "suggestions": ""}


def revise_params(params: dict, review: dict) -> dict:
    """Apply reviewer suggestions to adjust parameters for next iteration."""
    prompt = f"""You are a photo editor receiving feedback on your edit.

Your previous parameters:
{json.dumps(params, indent=2, default=str)}

Reviewer feedback:
- Score: {review.get('score', 0)}/10
- Critique: {review.get('critique', '')}
- Suggestions: {review.get('suggestions', '')}

Revise the parameters to address the feedback. Output the COMPLETE revised parameter set as JSON.
Keep the same structure. Only change values that need fixing based on the feedback.

```json
{{
    "analysis": {{ ... }},
    "exposure": {{ ... }},
    "filmic": {{ ... }},
    "colorbalance": {{ ... }},
    "tone_eq": {{ ... }}
}}
```"""

    result = claude_act(prompt, tier="light")
    if not result:
        return params

    import re as _re
    m = _re.search(r'```(?:json)?\s*\n(.*?)\n```', result, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = _re.search(r'\{.*\}', result, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return params


def edit_photo(raw_path: Path, reference_path: Path = None,
               output_dir: Path = None, max_iterations: int = 3) -> dict:
    """Full editing pipeline: analyze → render → review → iterate if needed."""
    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    preview = raw_path.with_suffix(".JPG")
    if not preview.exists():
        preview = raw_path.with_suffix(".jpg")
    if not preview.exists():
        preview = raw_path

    best_score = 0
    best_output = None
    best_params = None
    best_review = None
    params = None

    for iteration in range(max_iterations):
        log.info("=== Iteration %d for %s ===", iteration + 1, raw_path.stem)

        # First iteration: analyze from scratch. Later: revise based on review.
        if iteration == 0:
            params = analyze_photo(preview)
        else:
            params = revise_params(params, last_review)
        if not params:
            log.error("Analysis/revision failed")
            continue

        analysis = params.get("analysis", {})
        log.info("Scene: %s, Light: %s, Mood: %s",
                 analysis.get("scene_type", "?"),
                 analysis.get("light_condition", "?"),
                 analysis.get("mood_target", "?"))

        xmp_path = params_to_xmp(params, raw_path)
        log.info("XMP written: %s", xmp_path.name)

        dt_output = output_dir / f"{raw_path.stem}_v{iteration + 1}_edited.jpg"
        if not render_with_darktable(raw_path, xmp_path, dt_output):
            continue

        # Skip color matching — it causes unnatural color casts
        final_output = dt_output

        # Visual review: vision model compares original vs edited
        last_review = review_edit(preview, final_output, params)
        review_score = last_review.get("score", 0)
        approved = last_review.get("approved", False)
        log.info("Review: score=%s, approved=%s, critique=%s",
                 review_score, approved, last_review.get("critique", "")[:100])

        if review_score > best_score:
            best_score = review_score
            best_output = final_output
            best_params = params
            best_review = last_review

        if approved and review_score >= 6:
            log.info("Review approved (score=%s), accepting", review_score)
            break

        if iteration < max_iterations - 1:
            log.info("Review not approved, iterating with suggestions: %s",
                     last_review.get("suggestions", "")[:100])

    if best_params:
        params_path = output_dir / f"{raw_path.stem}_params.json"
        params_path.write_text(json.dumps(best_params, ensure_ascii=False, indent=2))

    return {
        "raw": str(raw_path),
        "output": str(best_output) if best_output else None,
        "score": best_score,
        "params": best_params,
        "review": best_review,
    }


def pick_and_edit(footage_dir: Path, output_dir: Path = None,
                  reference_dir: Path = None, n_candidates: int = 10) -> dict:
    """Daily pipeline: pick the best RAW candidate and edit it."""
    from scorer import AestheticScorer

    if output_dir is None:
        from config import ARTIFACTS_DIR; output_dir = ARTIFACTS_DIR / "photos"
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_exts = {".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf"}
    pairs = []
    for raw in footage_dir.rglob("*"):
        if raw.suffix.lower() in raw_exts:
            jpg = raw.with_suffix(".JPG")
            if not jpg.exists():
                jpg = raw.with_suffix(".jpg")
            if jpg.exists():
                pairs.append((raw, jpg))

    if not pairs:
        return {"error": "No RAW+JPG pairs found"}

    log.info("Found %d RAW+JPG pairs", len(pairs))

    scorer = AestheticScorer()
    scored = []
    for raw, jpg in pairs:
        try:
            s = scorer.score(jpg)
            scored.append({"raw": raw, "jpg": jpg, "score": s})
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:n_candidates]

    best = top[0]
    log.info("Selected: %s (score: %.2f)", best["raw"].name, best["score"])

    ref_path = None
    if reference_dir and reference_dir.exists():
        ref_images = list(reference_dir.rglob("*.jpg"))[:20]
        if ref_images:
            ref_scored = scorer.score_batch(ref_images)
            if ref_scored:
                ref_path = Path(ref_scored[0]["file"])

    result = edit_photo(best["raw"], reference_path=ref_path, output_dir=output_dir)
    result["candidates"] = [{"file": str(c["raw"]), "score": c["score"]} for c in top[:5]]
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="Per-image aesthetic photo editor")
    parser.add_argument("raw", help="RAW file to edit")
    parser.add_argument("--reference", help="Reference style image")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--iterations", type=int, default=2)
    args = parser.parse_args()

    result = edit_photo(
        Path(args.raw),
        reference_path=Path(args.reference) if args.reference else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        max_iterations=args.iterations,
    )
    print(json.dumps(result, indent=2, default=str))
