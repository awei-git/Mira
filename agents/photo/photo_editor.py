from __future__ import annotations

"""Photo editor — apply edits to images using Pillow + ImageMagick.

Also generates Lightroom XMP presets and .cube LUT files from style profiles.

Architecture:
    - Pillow: exposure, contrast, saturation, vibrance, sharpness, vignette, crop
    - ImageMagick (via subprocess): advanced color grading, tone curves, HSL
    - XMP export: for import into Lightroom Classic/CC
    - .cube LUT: for DaVinci Resolve, Premiere, FCPX
"""
import json
import logging
import math
import re
import subprocess
from pathlib import Path

log = logging.getLogger("photo.editor")


# ---------------------------------------------------------------------------
# Edit parameter extraction from analysis text
# ---------------------------------------------------------------------------


def extract_edit_params(analysis_text: str) -> dict | None:
    """Extract edit parameters JSON from analysis text."""
    if not analysis_text:
        return None
    # Try JSON code block
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", analysis_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON with edit-like keys
    m = re.search(r'\{[^{}]*"(?:exposure|contrast|highlights)"[^{}]*\}', analysis_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Apply edits via Pillow
# ---------------------------------------------------------------------------


def apply_edits(input_path: Path, output_path: Path, params: dict) -> bool:
    """Apply edit parameters to an image.

    Uses Pillow for basic adjustments, falls back to ImageMagick for advanced ops.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageDraw
    except ImportError:
        log.error("Pillow not installed — cannot apply edits")
        return False

    try:
        img = Image.open(input_path)
        # Convert to RGB if needed (handles RGBA, CMYK, etc.)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # --- Exposure ---
        exposure = params.get("exposure", 0)
        if exposure != 0:
            factor = 2.0**exposure  # photographic stops
            img = ImageEnhance.Brightness(img).enhance(factor)

        # --- Contrast ---
        contrast = params.get("contrast", 0)
        if contrast != 0:
            # Map -100..100 to 0.5..1.5 (Pillow factor)
            factor = 1.0 + (contrast / 100.0) * 0.5
            img = ImageEnhance.Contrast(img).enhance(factor)

        # --- Saturation ---
        saturation = params.get("saturation", 0)
        vibrance = params.get("vibrance", 0)
        sat_total = saturation + vibrance * 0.5  # vibrance is gentler
        if sat_total != 0:
            factor = 1.0 + (sat_total / 100.0) * 0.5
            img = ImageEnhance.Color(img).enhance(factor)

        # --- Highlights / Shadows (approximate with curves) ---
        highlights = params.get("highlights", 0)
        shadows = params.get("shadows", 0)
        if highlights != 0 or shadows != 0:
            img = _apply_highlight_shadow(img, highlights, shadows)

        # --- Clarity (local contrast via unsharp mask) ---
        clarity = params.get("clarity", 0)
        if clarity > 0:
            # Large-radius unsharp mask = clarity
            radius = 20
            amount = int(clarity * 1.5)
            img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=amount, threshold=3))
        elif clarity < 0:
            # Negative clarity = slight blur
            radius = abs(clarity) / 50.0
            img = img.filter(ImageFilter.GaussianBlur(radius=radius))

        # --- Sharpness ---
        sharpness = params.get("sharpness", 0)
        if sharpness > 0:
            radius = 1.0
            amount = int(sharpness)
            img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=amount, threshold=2))

        # --- Noise reduction (slight blur for high ISO) ---
        noise_reduction = params.get("noise_reduction", 0)
        if noise_reduction > 20:
            radius = noise_reduction / 100.0 * 1.5
            img = img.filter(ImageFilter.GaussianBlur(radius=radius))

        # --- Temperature shift ---
        temperature = params.get("temperature", 0)
        if temperature != 0:
            img = _apply_temperature(img, temperature)

        # --- Tint shift ---
        tint = params.get("tint", 0)
        if tint != 0:
            img = _apply_tint(img, tint)

        # --- Dehaze (increase contrast + saturation in shadows) ---
        dehaze = params.get("dehaze", 0)
        if dehaze > 0:
            factor = 1.0 + dehaze / 200.0
            img = ImageEnhance.Contrast(img).enhance(factor)
            img = ImageEnhance.Color(img).enhance(1.0 + dehaze / 400.0)

        # --- Vignette ---
        vignette = params.get("vignette", 0)
        if vignette != 0:
            img = _apply_vignette(img, vignette)

        # --- Crop ---
        crop = params.get("crop_suggestion")
        if crop and isinstance(crop, dict):
            left = crop.get("left", 0)
            top = crop.get("top", 0)
            right = crop.get("right", img.width)
            bottom = crop.get("bottom", img.height)
            img = img.crop((left, top, right, bottom))

        # Save
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "JPEG", quality=95)
        log.info("Saved edited photo: %s", output_path)
        return True

    except Exception as e:
        log.error("Failed to apply edits to %s: %s", input_path, e)
        return False


# ---------------------------------------------------------------------------
# Image adjustment helpers
# ---------------------------------------------------------------------------


def _apply_highlight_shadow(img, highlights: int, shadows: int):
    """Approximate highlight/shadow recovery using pixel-level manipulation."""
    from PIL import Image
    import numpy as np

    arr = np.array(img, dtype=np.float32)

    # Luminance (rough)
    lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]

    if highlights != 0:
        # Affect bright pixels (lum > 180)
        mask = np.clip((lum - 128) / 127, 0, 1)
        adjustment = highlights / 100.0 * -40  # negative highlights = darken brights
        arr += mask[:, :, np.newaxis] * adjustment

    if shadows != 0:
        # Affect dark pixels (lum < 80)
        mask = np.clip((128 - lum) / 128, 0, 1)
        adjustment = shadows / 100.0 * 40  # positive shadows = brighten darks
        arr += mask[:, :, np.newaxis] * adjustment

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _apply_temperature(img, temp: int):
    """Shift color temperature. Positive = warm (orange), negative = cool (blue)."""
    from PIL import Image
    import numpy as np

    arr = np.array(img, dtype=np.float32)
    shift = temp / 100.0 * 30  # max ±30 per channel

    arr[:, :, 0] += shift  # Red: warm up
    arr[:, :, 2] -= shift  # Blue: cool down

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _apply_tint(img, tint: int):
    """Shift tint. Positive = magenta, negative = green."""
    from PIL import Image
    import numpy as np

    arr = np.array(img, dtype=np.float32)
    shift = tint / 100.0 * 20

    arr[:, :, 1] -= shift  # Green channel

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _apply_vignette(img, amount: int):
    """Apply radial vignette. Negative = darken edges, positive = lighten."""
    from PIL import Image, ImageDraw
    import numpy as np

    w, h = img.size
    arr = np.array(img, dtype=np.float32)

    # Create radial gradient
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    # Normalized distance from center (0 at center, 1 at corners)
    dist = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) / np.sqrt(cx**2 + cy**2)

    # Vignette strength: amount/100 maps to max darkening factor
    strength = abs(amount) / 100.0 * 0.6
    if amount < 0:
        # Darken edges
        factor = 1.0 - strength * dist**2
    else:
        # Lighten edges (less common)
        factor = 1.0 + strength * dist**2

    arr *= factor[:, :, np.newaxis]
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ---------------------------------------------------------------------------
# Lightroom XMP Preset Generation
# ---------------------------------------------------------------------------


def generate_xmp_preset(style_profile: dict, output_path: Path) -> bool:
    """Generate a Lightroom XMP preset from a style profile.

    Supports full parameter set: basic adjustments, HSL, split toning,
    parametric tone curve, sharpening, noise reduction.
    """
    base = style_profile.get("lightroom_preset_base", style_profile.get("common_adjustments", {}))
    if not base:
        log.error("No adjustment data in style profile")
        return False

    def _g(key, fallback_key="", default=0):
        """Get param from base, with fallback key and default."""
        v = base.get(key, base.get(fallback_key, default) if fallback_key else default)
        return v

    mood = style_profile.get("overall_mood", "Mira Style")

    # Build all XMP attributes
    attrs = []
    a = attrs.append

    # Basic
    a(f'crs:WhiteBalance="{_g("WhiteBalance", default="As Shot")}"')
    a(f'crs:Temperature="{_g("Temperature", default=5500)}"')
    a(f'crs:Tint="{_g("Tint", "tint_shift", 0):+d}"')
    a(f'crs:Exposure2012="{_g("Exposure2012", "exposure_bias", 0):.2f}"')
    a(f'crs:Contrast2012="{_g("Contrast2012", "contrast", 0):+d}"')
    a(f'crs:Highlights2012="{_g("Highlights2012", "highlights", 0)}"')
    a(f'crs:Shadows2012="{_g("Shadows2012", "shadows", 0):+d}"')
    a(f'crs:Whites2012="{_g("Whites2012", "whites", 0):+d}"')
    a(f'crs:Blacks2012="{_g("Blacks2012", "blacks", 0)}"')
    a(f'crs:Texture="{_g("Texture", default=0)}"')
    a(f'crs:Clarity2012="{_g("Clarity2012", "clarity", 0):+d}"')
    a(f'crs:Dehaze="{_g("Dehaze", "dehaze", 0):+d}"')
    a(f'crs:Vibrance="{_g("Vibrance", "vibrance", 0):+d}"')
    a(f'crs:Saturation="{_g("Saturation", "saturation", 0):+d}"')

    # Parametric Tone Curve
    a(f'crs:ParametricShadows="{_g("ParametricShadows", default=0)}"')
    a(f'crs:ParametricDarks="{_g("ParametricDarks", default=0)}"')
    a(f'crs:ParametricLights="{_g("ParametricLights", default=0)}"')
    a(f'crs:ParametricHighlights="{_g("ParametricHighlights", default=0)}"')
    a('crs:ParametricShadowSplit="25"')
    a('crs:ParametricMidtoneSplit="50"')
    a('crs:ParametricHighlightSplit="75"')

    # Sharpening
    a(f'crs:Sharpness="{_g("Sharpness", "sharpness", 40)}"')
    a(f'crs:SharpenRadius="{_g("SharpenRadius", default=1.0):.1f}"')
    a(f'crs:SharpenDetail="{_g("SharpenDetail", default=25)}"')
    a(f'crs:SharpenEdgeMasking="{_g("SharpenEdgeMasking", default=0)}"')

    # Noise Reduction
    a(f'crs:LuminanceSmoothing="{_g("LuminanceSmoothing", "noise_reduction", 0)}"')
    a(f'crs:ColorNoiseReduction="{_g("ColorNoiseReduction", default=25)}"')
    a('crs:ColorNoiseReductionDetail="50"')
    a('crs:ColorNoiseReductionSmoothness="50"')

    # HSL Hue
    for color in ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]:
        a(f'crs:HueAdjustment{color}="{_g(f"HueAdjustment{color}", default=0)}"')

    # HSL Saturation
    for color in ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]:
        a(f'crs:SaturationAdjustment{color}="{_g(f"SaturationAdjustment{color}", default=0)}"')

    # HSL Luminance
    for color in ["Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"]:
        a(f'crs:LuminanceAdjustment{color}="{_g(f"LuminanceAdjustment{color}", default=0)}"')

    # Split Toning
    a(f'crs:SplitToningShadowHue="{_g("SplitToningShadowHue", default=0)}"')
    a(f'crs:SplitToningShadowSaturation="{_g("SplitToningShadowSaturation", default=0)}"')
    a(f'crs:SplitToningHighlightHue="{_g("SplitToningHighlightHue", default=0)}"')
    a(f'crs:SplitToningHighlightSaturation="{_g("SplitToningHighlightSaturation", default=0)}"')
    a(f'crs:SplitToningBalance="{_g("SplitToningBalance", default=0)}"')

    # Effects
    a(f'crs:PostCropVignetteAmount="{_g("PostCropVignetteAmount", "vignette", 0)}"')
    a(f'crs:GrainAmount="{_g("GrainAmount", default=0)}"')

    # Misc
    a('crs:AutoLateralCA="1"')
    a('crs:ConvertToGrayscale="False"')
    a('crs:ToneCurveName2012="Linear"')
    a('crs:CameraProfile="Adobe Standard"')
    a('crs:HasSettings="True"')

    attrs_str = "\n    ".join(attrs)

    xmp = f"""<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Mira Photo Agent">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    crs:PresetType="Normal"
    crs:Cluster=""
    crs:UUID=""
    crs:SupportsAmount="False"
    crs:SupportsColor="True"
    crs:SupportsMonochrome="True"
    crs:SupportsHighDynamicRange="True"
    crs:SupportsNormalDynamicRange="True"
    crs:SupportsSceneReferred="True"
    crs:SupportsOutputReferred="True"
    crs:CameraModelRestriction=""
    crs:Copyright="Generated by Mira Photo Agent"
    crs:ContactInfo=""
    crs:Version="15.0"
    crs:ProcessVersion="11.0"
    {attrs_str}>
   <crs:Name>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">Mira - {mood}</rdf:li>
    </rdf:Alt>
   </crs:Name>
   <crs:Group>
    <rdf:Alt>
     <rdf:li xml:lang="x-default">Mira Styles</rdf:li>
    </rdf:Alt>
   </crs:Group>
   <crs:ToneCurvePV2012>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012>
   <crs:ToneCurvePV2012Red>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012Red>
   <crs:ToneCurvePV2012Green>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012Green>
   <crs:ToneCurvePV2012Blue>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012Blue>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xmp, encoding="utf-8")
        log.info("XMP preset saved: %s", output_path)
        return True
    except Exception as e:
        log.error("Failed to save XMP preset: %s", e)
        return False


# ---------------------------------------------------------------------------
# .cube LUT Generation
# ---------------------------------------------------------------------------


def generate_cube_lut(style_profile: dict, output_path: Path, size: int = 33) -> bool:
    """Generate a .cube 3D LUT from style profile.

    Creates a color transformation LUT that approximates the style profile's
    color grading (temperature, tint, saturation, contrast adjustments).
    """
    base = style_profile.get("lightroom_preset_base", style_profile.get("common_adjustments", {}))
    if not base:
        log.error("No adjustment data for LUT generation")
        return False

    # Extract parameters
    temp_shift = base.get("temperature_shift", 0) / 100.0 * 0.1  # subtle
    contrast = base.get("Contrast2012", base.get("contrast", 0)) / 100.0 * 0.3
    saturation = base.get("Saturation", base.get("saturation", 0)) / 100.0 * 0.3
    vibrance = base.get("Vibrance", base.get("vibrance", 0)) / 100.0 * 0.2

    # Shadow/highlight color cast
    shadow_color = style_profile.get("shadow_color_cast", "neutral")
    highlight_color = style_profile.get("highlight_color_cast", "neutral")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(f"# Generated by Mira Photo Agent\n")
            f.write(f"# Style: {style_profile.get('overall_mood', 'default')}\n")
            f.write(f'TITLE "Mira Style"\n')
            f.write(f"LUT_3D_SIZE {size}\n\n")

            for b_i in range(size):
                for g_i in range(size):
                    for r_i in range(size):
                        r = r_i / (size - 1)
                        g = g_i / (size - 1)
                        b = b_i / (size - 1)

                        # Apply temperature shift
                        r += temp_shift
                        b -= temp_shift

                        # Apply contrast (S-curve approximation)
                        if contrast != 0:
                            r = _s_curve(r, contrast)
                            g = _s_curve(g, contrast)
                            b = _s_curve(b, contrast)

                        # Apply saturation
                        if saturation != 0 or vibrance != 0:
                            lum = 0.299 * r + 0.587 * g + 0.114 * b
                            # Vibrance: boost low-sat more
                            current_sat = max(abs(r - lum), abs(g - lum), abs(b - lum))
                            vib_factor = vibrance * (1.0 - current_sat)
                            total_sat = 1.0 + saturation + vib_factor
                            r = lum + (r - lum) * total_sat
                            g = lum + (g - lum) * total_sat
                            b = lum + (b - lum) * total_sat

                        # Apply shadow/highlight color cast
                        lum = 0.299 * r + 0.587 * g + 0.114 * b
                        if shadow_color != "neutral" and lum < 0.3:
                            weight = (0.3 - lum) / 0.3 * 0.05
                            sr, sg, sb = _color_to_rgb(shadow_color)
                            r += weight * (sr - 0.5)
                            g += weight * (sg - 0.5)
                            b += weight * (sb - 0.5)

                        if highlight_color != "neutral" and lum > 0.7:
                            weight = (lum - 0.7) / 0.3 * 0.05
                            hr, hg, hb = _color_to_rgb(highlight_color)
                            r += weight * (hr - 0.5)
                            g += weight * (hg - 0.5)
                            b += weight * (hb - 0.5)

                        # Clamp
                        r = max(0.0, min(1.0, r))
                        g = max(0.0, min(1.0, g))
                        b = max(0.0, min(1.0, b))

                        f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")

        log.info("LUT saved: %s", output_path)
        return True

    except Exception as e:
        log.error("Failed to generate LUT: %s", e)
        return False


def _s_curve(x: float, amount: float) -> float:
    """Apply an S-curve contrast adjustment."""
    # Attempt sigmoid-based S-curve
    midpoint = 0.5
    steepness = 1.0 + abs(amount) * 5
    if amount > 0:
        return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))
    else:
        # Inverse — flatten
        linear_weight = abs(amount)
        return x * (1 - linear_weight) + midpoint * linear_weight


def _color_to_rgb(color_name: str) -> tuple[float, float, float]:
    """Convert a color name to approximate RGB values (0-1 range)."""
    colors = {
        "teal": (0.0, 0.5, 0.5),
        "blue": (0.2, 0.3, 0.7),
        "purple": (0.4, 0.2, 0.6),
        "brown": (0.5, 0.35, 0.2),
        "warm": (0.6, 0.45, 0.3),
        "cool": (0.3, 0.4, 0.6),
        "orange": (0.7, 0.45, 0.2),
        "green": (0.3, 0.5, 0.3),
        "gold": (0.6, 0.5, 0.2),
        "magenta": (0.6, 0.2, 0.5),
    }
    return colors.get(color_name.lower(), (0.5, 0.5, 0.5))
