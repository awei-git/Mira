"""Fine-grained photo editing script — per-image adjustments.

Uses numpy for precise control over:
- Exposure, contrast (S-curve)
- Highlights/shadows recovery
- Color temperature and tint
- HSL per-channel adjustments
- Split toning (shadow/highlight color cast)
- Clarity (local contrast via high-pass)
- Vignette
- Sharpening (unsharp mask)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def load_and_convert(path: Path) -> np.ndarray:
    """Load image as float32 RGB array (0-1 range)."""
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.array(img, dtype=np.float32) / 255.0


def save(arr: np.ndarray, path: Path, quality: int = 95):
    """Save float32 array as JPEG."""
    out = np.clip(arr * 255, 0, 255).astype(np.uint8)
    Image.fromarray(out).save(path, "JPEG", quality=quality)
    print(f"  Saved: {path} ({path.stat().st_size // 1024}KB)")


# ---------------------------------------------------------------------------
# Core adjustments (all operate on 0-1 float arrays)
# ---------------------------------------------------------------------------


def adjust_exposure(img: np.ndarray, stops: float) -> np.ndarray:
    """Adjust exposure in photographic stops."""
    return img * (2.0**stops)


def adjust_contrast(img: np.ndarray, amount: float) -> np.ndarray:
    """S-curve contrast. amount: -1 to 1 (0 = no change)."""
    if abs(amount) < 0.01:
        return img
    # Attempt parametric S-curve via power function
    midpoint = 0.5
    if amount > 0:
        # Increase contrast — steepen around midpoint
        gamma_dark = 1.0 + amount * 1.5  # > 1 = darken darks
        gamma_light = 1.0 / (1.0 + amount * 1.5)  # < 1 = brighten lights
    else:
        gamma_dark = 1.0 / (1.0 + abs(amount) * 1.5)
        gamma_light = 1.0 + abs(amount) * 1.5

    result = img.copy()
    dark_mask = img < midpoint
    light_mask = ~dark_mask

    # Darks: remap 0-0.5 → apply gamma → remap back
    dark_norm = img[dark_mask] / midpoint
    result[dark_mask] = np.power(np.clip(dark_norm, 0, 1), gamma_dark) * midpoint

    # Lights: remap 0.5-1 → apply gamma → remap back
    light_norm = (img[light_mask] - midpoint) / midpoint
    result[light_mask] = np.power(np.clip(light_norm, 0, 1), gamma_light) * midpoint + midpoint

    return result


def adjust_highlights(img: np.ndarray, amount: int) -> np.ndarray:
    """Recover/boost highlights. amount: -100 to 100."""
    if amount == 0:
        return img
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    # Smooth mask for highlights (bright areas)
    mask = np.clip((lum - 0.5) / 0.5, 0, 1) ** 1.5
    adjustment = amount / 100.0 * -0.4  # negative amount = darken highlights
    return img + mask[:, :, np.newaxis] * adjustment


def adjust_shadows(img: np.ndarray, amount: int) -> np.ndarray:
    """Lift/crush shadows. amount: -100 to 100."""
    if amount == 0:
        return img
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    mask = np.clip((0.5 - lum) / 0.5, 0, 1) ** 1.5
    adjustment = amount / 100.0 * 0.35
    return img + mask[:, :, np.newaxis] * adjustment


def adjust_whites(img: np.ndarray, amount: int) -> np.ndarray:
    """Shift white point. amount: -100 to 100."""
    if amount == 0:
        return img
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    mask = np.clip((lum - 0.7) / 0.3, 0, 1) ** 2
    adjustment = amount / 100.0 * 0.25
    return img + mask[:, :, np.newaxis] * adjustment


def adjust_blacks(img: np.ndarray, amount: int) -> np.ndarray:
    """Shift black point. amount: -100 to 100."""
    if amount == 0:
        return img
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    mask = np.clip((0.3 - lum) / 0.3, 0, 1) ** 2
    adjustment = amount / 100.0 * 0.2
    return img + mask[:, :, np.newaxis] * adjustment


def adjust_temperature(img: np.ndarray, shift: float) -> np.ndarray:
    """Color temperature. shift: -1 to 1 (positive = warm)."""
    if abs(shift) < 0.01:
        return img
    result = img.copy()
    result[:, :, 0] += shift * 0.08  # Red
    result[:, :, 2] -= shift * 0.08  # Blue
    # Subtle green compensation
    result[:, :, 1] += shift * 0.02
    return result


def adjust_tint(img: np.ndarray, shift: float) -> np.ndarray:
    """Tint. shift: -1 to 1 (positive = magenta, negative = green)."""
    if abs(shift) < 0.01:
        return img
    result = img.copy()
    result[:, :, 1] -= shift * 0.05
    return result


def adjust_vibrance(img: np.ndarray, amount: float) -> np.ndarray:
    """Vibrance — selective saturation that protects already-saturated colors."""
    if abs(amount) < 0.01:
        return img
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    # Current saturation per pixel (rough)
    max_ch = np.max(img, axis=2)
    min_ch = np.min(img, axis=2)
    sat = np.where(max_ch > 0, (max_ch - min_ch) / (max_ch + 1e-6), 0)
    # Boost factor inversely proportional to current saturation
    boost = amount * (1.0 - sat)
    lum_3d = lum[:, :, np.newaxis]
    return lum_3d + (img - lum_3d) * (1.0 + boost[:, :, np.newaxis])


def adjust_saturation(img: np.ndarray, amount: float) -> np.ndarray:
    """Global saturation. amount: -1 to 1."""
    if abs(amount) < 0.01:
        return img
    lum = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2])[:, :, np.newaxis]
    return lum + (img - lum) * (1.0 + amount)


def apply_hsl(img: np.ndarray, hsl: dict) -> np.ndarray:
    """Apply HSL adjustments per color channel.

    hsl format: {
        "hue": {"red": 0, "orange": -22, ...},
        "saturation": {"red": -73, "orange": 43, ...},
        "luminance": {"red": -100, "orange": 56, ...}
    }
    """
    if not hsl:
        return img

    result = img.copy()
    h, w, _ = result.shape

    # Convert to HSV for per-channel work
    # Simple RGB→HSV
    max_c = np.max(result, axis=2)
    min_c = np.min(result, axis=2)
    diff = max_c - min_c + 1e-8

    # Hue (0-360)
    hue = np.zeros((h, w))
    r, g, b = result[:, :, 0], result[:, :, 1], result[:, :, 2]

    mask_r = (max_c == r) & (diff > 1e-6)
    mask_g = (max_c == g) & (diff > 1e-6) & ~mask_r
    mask_b = ~mask_r & ~mask_g & (diff > 1e-6)

    hue[mask_r] = 60 * (((g[mask_r] - b[mask_r]) / diff[mask_r]) % 6)
    hue[mask_g] = 60 * ((b[mask_g] - r[mask_g]) / diff[mask_g] + 2)
    hue[mask_b] = 60 * ((r[mask_b] - g[mask_b]) / diff[mask_b] + 4)

    # Saturation
    sat = np.where(max_c > 0, diff / (max_c + 1e-8), 0)
    val = max_c

    # Define hue ranges for each color channel (Lightroom-like)
    channels = {
        "red": (345, 15),  # wraps around 0
        "orange": (15, 45),
        "yellow": (45, 75),
        "green": (75, 165),
        "aqua": (165, 195),
        "blue": (195, 255),
        "purple": (255, 285),
        "magenta": (285, 345),
    }

    sat_adj = hsl.get("saturation", {})
    lum_adj = hsl.get("luminance", {})

    for ch_name, (h_lo, h_hi) in channels.items():
        s_val = sat_adj.get(ch_name, 0) / 100.0
        l_val = lum_adj.get(ch_name, 0) / 100.0

        if abs(s_val) < 0.01 and abs(l_val) < 0.01:
            continue

        # Build mask for this hue range with soft edges
        if h_lo > h_hi:  # wraps around (red)
            mask = (hue >= h_lo) | (hue < h_hi)
        else:
            mask = (hue >= h_lo) & (hue < h_hi)

        mask_f = mask.astype(np.float32)

        # Apply saturation adjustment
        if abs(s_val) > 0.01:
            lum_px = (0.299 * r + 0.587 * g + 0.114 * b)[:, :, np.newaxis]
            factor = 1.0 + s_val
            # Only adjust masked pixels
            for c in range(3):
                result[:, :, c] = np.where(
                    mask, lum_px[:, :, 0] + (result[:, :, c] - lum_px[:, :, 0]) * factor, result[:, :, c]
                )

        # Apply luminance adjustment
        if abs(l_val) > 0.01:
            for c in range(3):
                result[:, :, c] = np.where(mask, result[:, :, c] + l_val * 0.3, result[:, :, c])

    return result


def apply_split_toning(
    img: np.ndarray, shadow_hue: int, shadow_sat: int, highlight_hue: int, highlight_sat: int
) -> np.ndarray:
    """Apply split toning — colorize shadows and highlights separately."""
    if shadow_sat == 0 and highlight_sat == 0:
        return img

    result = img.copy()
    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]

    def hue_to_rgb(hue_deg):
        """Convert hue (0-360) to RGB unit vector."""
        h = hue_deg / 60.0
        x = 1.0 - abs(h % 2 - 1.0)
        if h < 1:
            return (1, x, 0)
        if h < 2:
            return (x, 1, 0)
        if h < 3:
            return (0, 1, x)
        if h < 4:
            return (0, x, 1)
        if h < 5:
            return (x, 0, 1)
        return (1, 0, x)

    # Shadow toning
    if shadow_sat > 0:
        sr, sg, sb = hue_to_rgb(shadow_hue)
        strength = shadow_sat / 100.0 * 0.15
        shadow_mask = np.clip((0.5 - lum) / 0.5, 0, 1) ** 1.2
        result[:, :, 0] += shadow_mask * strength * (sr - 0.5)
        result[:, :, 1] += shadow_mask * strength * (sg - 0.5)
        result[:, :, 2] += shadow_mask * strength * (sb - 0.5)

    # Highlight toning
    if highlight_sat > 0:
        hr, hg, hb = hue_to_rgb(highlight_hue)
        strength = highlight_sat / 100.0 * 0.12
        highlight_mask = np.clip((lum - 0.5) / 0.5, 0, 1) ** 1.2
        result[:, :, 0] += highlight_mask * strength * (hr - 0.5)
        result[:, :, 1] += highlight_mask * strength * (hg - 0.5)
        result[:, :, 2] += highlight_mask * strength * (hb - 0.5)

    return result


def apply_clarity(img_pil: Image.Image, amount: int) -> Image.Image:
    """Clarity via large-radius unsharp mask (local contrast)."""
    if amount == 0:
        return img_pil
    if amount > 0:
        return img_pil.filter(ImageFilter.UnsharpMask(radius=20, percent=int(amount * 2), threshold=3))
    else:
        return img_pil.filter(ImageFilter.GaussianBlur(radius=abs(amount) / 50.0))


def apply_dehaze(img: np.ndarray, amount: float) -> np.ndarray:
    """Dehaze — increase contrast and saturation in dark/mid areas."""
    if abs(amount) < 0.01:
        return img
    # Mild contrast boost focused on lower tones
    result = adjust_contrast(img, amount * 0.3)
    # Slight saturation boost
    lum = (0.299 * result[:, :, 0] + 0.587 * result[:, :, 1] + 0.114 * result[:, :, 2])[:, :, np.newaxis]
    result = lum + (result - lum) * (1.0 + amount * 0.15)
    return result


def apply_vignette(img: np.ndarray, amount: float, roundness: float = 0.7) -> np.ndarray:
    """Radial vignette. amount: negative = darken edges."""
    if abs(amount) < 0.01:
        return img
    h, w = img.shape[:2]
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((X - cx) / (w / 2)) ** 2 + ((Y - cy) / (h / 2)) ** 2)
    # Feathered falloff
    falloff = np.clip((dist - roundness) / (1.0 - roundness + 0.01), 0, 1) ** 1.5
    factor = 1.0 + amount * falloff
    return img * factor[:, :, np.newaxis]


def sharpen(img_pil: Image.Image, amount: int, radius: float = 0.8, detail: int = 25, masking: int = 0) -> Image.Image:
    """Unsharp mask sharpening."""
    if amount <= 0:
        return img_pil
    return img_pil.filter(ImageFilter.UnsharpMask(radius=radius, percent=amount, threshold=max(0, masking // 10)))


# ---------------------------------------------------------------------------
# High-level edit function
# ---------------------------------------------------------------------------


def edit_photo(input_path: Path, output_path: Path, params: dict) -> bool:
    """Apply a full set of edits to a photo."""
    print(f"\nEditing: {input_path.name}")

    img = load_and_convert(input_path)
    print(f"  Loaded: {img.shape[1]}x{img.shape[0]}")

    # 1. Exposure
    if params.get("exposure", 0) != 0:
        img = adjust_exposure(img, params["exposure"])
        print(f"  Exposure: {params['exposure']:+.2f}")

    # 2. Contrast
    contrast = params.get("contrast", 0) / 100.0
    if contrast != 0:
        img = adjust_contrast(img, contrast)
        print(f"  Contrast: {params['contrast']:+d}")

    # 3. Highlights
    if params.get("highlights", 0) != 0:
        img = adjust_highlights(img, params["highlights"])
        print(f"  Highlights: {params['highlights']:+d}")

    # 4. Shadows
    if params.get("shadows", 0) != 0:
        img = adjust_shadows(img, params["shadows"])
        print(f"  Shadows: {params['shadows']:+d}")

    # 5. Whites
    if params.get("whites", 0) != 0:
        img = adjust_whites(img, params["whites"])
        print(f"  Whites: {params['whites']:+d}")

    # 6. Blacks
    if params.get("blacks", 0) != 0:
        img = adjust_blacks(img, params["blacks"])
        print(f"  Blacks: {params['blacks']:+d}")

    # 7. Dehaze
    dehaze = params.get("dehaze", 0) / 100.0
    if dehaze != 0:
        img = apply_dehaze(img, dehaze)
        print(f"  Dehaze: {params['dehaze']:+d}")

    # 8. Temperature
    temp = params.get("temperature", 0) / 100.0
    if temp != 0:
        img = adjust_temperature(img, temp)
        print(f"  Temperature: {params['temperature']:+d}")

    # 9. Tint
    tint = params.get("tint", 0) / 100.0
    if tint != 0:
        img = adjust_tint(img, tint)
        print(f"  Tint: {params['tint']:+d}")

    # 10. HSL
    hsl = params.get("hsl")
    if hsl:
        img = apply_hsl(img, hsl)
        print(f"  HSL: applied")

    # 11. Vibrance
    vibrance = params.get("vibrance", 0) / 100.0
    if vibrance != 0:
        img = adjust_vibrance(img, vibrance)
        print(f"  Vibrance: {params['vibrance']:+d}")

    # 12. Saturation
    saturation = params.get("saturation", 0) / 100.0
    if saturation != 0:
        img = adjust_saturation(img, saturation)
        print(f"  Saturation: {params['saturation']:+d}")

    # 13. Split toning
    st = params.get("split_toning")
    if st:
        img = apply_split_toning(
            img,
            st.get("shadow_hue", 0),
            st.get("shadow_sat", 0),
            st.get("highlight_hue", 0),
            st.get("highlight_sat", 0),
        )
        print(f"  Split toning: shadow={st.get('shadow_hue',0)}° highlight={st.get('highlight_hue',0)}°")

    # 14. Vignette
    vignette = params.get("vignette", 0) / 100.0
    if vignette != 0:
        img = apply_vignette(img, vignette)
        print(f"  Vignette: {params['vignette']:+d}")

    # Clamp before converting to PIL for clarity/sharpening
    img = np.clip(img, 0, 1)

    # Convert to PIL for filter operations
    img_pil = Image.fromarray((img * 255).astype(np.uint8))

    # 15. Clarity
    clarity = params.get("clarity", 0)
    if clarity != 0:
        img_pil = apply_clarity(img_pil, clarity)
        print(f"  Clarity: {clarity:+d}")

    # 16. Sharpening
    sharp = params.get("sharpness", 0)
    if sharp > 0:
        img_pil = sharpen(
            img_pil,
            sharp,
            radius=params.get("sharpen_radius", 0.8),
            masking=params.get("sharpen_masking", 0),
        )
        print(f"  Sharpness: {sharp}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img_pil.save(output_path, "JPEG", quality=95)
    print(f"  Saved: {output_path} ({output_path.stat().st_size // 1024}KB)")
    return True


# ---------------------------------------------------------------------------
# Per-image edit recipes
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    BASE = Path.home() / "Sandbox/assets/photos"
    OUT = Path(__file__).resolve().parent / "output"

    # ── 1. DSC00239 — Misty beach, mossy rock, Second Beach ──
    # Mood: moody, atmospheric, Pacific Northwest
    # Treatment: enhance the fog atmosphere, boost the green of the moss,
    # keep the grey/silver tones in the mist, add subtle warmth to the rock
    edit_photo(
        BASE / "DSC00239.jpg",
        OUT / "DSC00239_edited.jpg",
        {
            "exposure": 0.15,
            "contrast": 12,
            "highlights": -60,
            "shadows": 35,
            "whites": 20,
            "blacks": -10,
            "dehaze": 25,
            "temperature": -5,  # keep it cool — PNW mood
            "tint": 3,
            "vibrance": 30,
            "saturation": 5,
            "clarity": 30,
            "sharpness": 78,
            "sharpen_radius": 0.8,
            "sharpen_masking": 30,
            "vignette": -25,
            "hsl": {
                "hue": {"orange": -15, "yellow": -20, "green": -10},
                "saturation": {
                    "red": -30,
                    "orange": 20,
                    "yellow": 15,
                    "green": 55,
                    "aqua": 30,
                    "blue": -15,
                    "purple": -50,
                },
                "luminance": {"orange": 15, "yellow": 10, "green": -15, "blue": -20},
            },
            "split_toning": {
                "shadow_hue": 200,
                "shadow_sat": 8,  # cool blue shadows
                "highlight_hue": 40,
                "highlight_sat": 10,  # warm amber highlights
            },
        },
    )

    # ── 2. _DSC2578-HDR — Mt Rainier, alpine meadow ──
    # Mood: majestic, golden-hour warmth, big landscape
    # Treatment: warm up the foreground meadow, recover sky highlights,
    # boost the greens and golds, add depth with split toning
    edit_photo(
        BASE / "_DSC2578-HDR.jpg",
        OUT / "_DSC2578-HDR_edited.jpg",
        {
            "exposure": -0.10,  # slightly pull back — HDR can be bright
            "contrast": 18,
            "highlights": -85,
            "shadows": 25,
            "whites": 45,
            "blacks": -8,
            "dehaze": 20,
            "temperature": 15,  # push warm — golden meadow
            "tint": 5,
            "vibrance": 28,
            "saturation": 8,
            "clarity": 22,
            "sharpness": 78,
            "sharpen_radius": 0.8,
            "sharpen_masking": 30,
            "vignette": -15,
            "hsl": {
                "hue": {"orange": -20, "yellow": -25, "green": -10},
                "saturation": {
                    "red": -40,
                    "orange": 50,
                    "yellow": 45,
                    "green": 40,
                    "aqua": 15,
                    "blue": 10,
                    "purple": -80,
                },
                "luminance": {"orange": 40, "yellow": 30, "green": -10, "blue": -30},
            },
            "split_toning": {
                "shadow_hue": 44,
                "shadow_sat": 5,
                "highlight_hue": 35,
                "highlight_sat": 18,  # golden highlights
            },
        },
    )

    # ── 3. _DSC3775 — Cherry blossoms with bee ──
    # Mood: soft, spring, delicate
    # Treatment: enhance the pink blossoms and blue sky contrast,
    # keep it soft (lower clarity), warm slightly, selective color boost
    edit_photo(
        BASE / "_DSC3775.jpg",
        OUT / "_DSC3775_edited.jpg",
        {
            "exposure": 0.20,
            "contrast": 8,
            "highlights": -45,
            "shadows": 40,
            "whites": 30,
            "blacks": 5,
            "dehaze": 10,
            "temperature": 8,
            "tint": 12,  # slight magenta push — enhance pink blossoms
            "vibrance": 35,
            "saturation": 5,
            "clarity": 8,  # gentle — keep the softness
            "sharpness": 65,
            "sharpen_radius": 0.8,
            "sharpen_masking": 40,  # protect smooth petals
            "vignette": -20,
            "hsl": {
                "hue": {"orange": -15, "yellow": -10, "purple": 10, "magenta": 5},
                "saturation": {
                    "red": 15,
                    "orange": 20,
                    "yellow": -10,
                    "green": -20,
                    "aqua": 20,
                    "blue": 25,
                    "purple": 30,
                    "magenta": 25,
                },
                "luminance": {"orange": 20, "yellow": 15, "blue": -25, "purple": -10},
            },
            "split_toning": {
                "shadow_hue": 280,
                "shadow_sat": 6,  # purple shadows — complement pink
                "highlight_hue": 35,
                "highlight_sat": 12,
            },
        },
    )

    print("\n=== Done! ===")
