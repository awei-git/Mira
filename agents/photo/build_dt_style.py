"""Build a darktable XMP sidecar with WA's style applied.

Strategy: Start from darktable's default pipeline XMP, then inject
modified module params for exposure, sigmoid, sharpen, and vignette.

For the complex color grading (colorbalancergb), we generate the params
by encoding the struct directly from WA's style profile values.
"""
from __future__ import annotations

import struct
import re
from pathlib import Path


def make_exposure_params(ev: float = 0.0) -> str:
    """Build exposure module params (modversion 7)."""
    return struct.pack('<fffff ii',
        0.0,                # compensate_exposure_bias
        -0.000244140625,    # black
        ev,                 # exposure (EV)
        50.0,               # deflicker_percentile
        -4.0,               # deflicker_target_level
        1,                  # mode (1=manual)
        1,                  # compensate_flag
    ).hex()


def make_sharpen_params(radius: float = 0.8, amount: float = 0.7,
                        threshold: float = 0.5) -> str:
    """Build sharpen module params (modversion 1)."""
    return struct.pack('<fff', radius, amount, threshold).hex()


def make_vignette_params(strength: float = -0.3) -> str:
    """Build vignette module params."""
    return struct.pack('<6f 2f i i f i',
        50.0, 50.0, strength, 50.0, 50.0, 0.0,
        0.0, 0.0,
        1, 0, 1.0, 0,
    ).hex()


# Default blendop params (passthrough, no blending)
BLEND_DEFAULT = "gz11eJxjYIAACQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dcF/IADRAGpyHQU="
BLEND_SCENE = "gz08eJxjYGBgYAFiCQYYOOHEgAZY0QWAgBGLGANDgz0Ej1Q+dlAx68oBEMbFxwX+AwGIBgCbGCeh"


def build_wa_style_xmp(base_xmp_path: Path, output_path: Path,
                       ev: float = 0.25) -> bool:
    """Take a darktable default XMP and inject WA style adjustments.

    Args:
        base_xmp_path: Path to a darktable-generated default XMP
        output_path: Where to write the styled XMP
        ev: Exposure compensation in EV
    """
    xmp = base_xmp_path.read_text(encoding="utf-8")

    # 1. Replace exposure params
    exp_params = make_exposure_params(ev)
    xmp = _replace_module_params(xmp, "exposure", exp_params)

    # 2. Add sharpen module (if not present, append to history)
    sharp_params = make_sharpen_params(0.8, 0.7, 0.5)
    if 'darktable:operation="sharpen"' not in xmp:
        xmp = _append_module(xmp, "sharpen", 2, sharp_params, enabled=1)
    else:
        xmp = _replace_module_params(xmp, "sharpen", sharp_params)

    # 3. Add vignette
    vig_params = make_vignette_params(-0.25)
    if 'darktable:operation="vignette"' not in xmp:
        xmp = _append_module(xmp, "vignette", 4, vig_params, enabled=1)
    else:
        xmp = _replace_module_params(xmp, "vignette", vig_params)

    # 4. Update history_end count
    # Count total history entries
    count = xmp.count('darktable:operation=')
    xmp = re.sub(r'darktable:history_end="\d+"',
                 f'darktable:history_end="{count}"', xmp)

    output_path.write_text(xmp, encoding="utf-8")
    return True


def _replace_module_params(xmp: str, operation: str, new_params: str) -> str:
    """Replace params for a specific module in the XMP."""
    pattern = (
        rf'(darktable:operation="{operation}".*?'
        rf'darktable:params=")([^"]*?)(")'
    )
    return re.sub(pattern, rf'\g<1>{new_params}\3', xmp, flags=re.DOTALL)


def _append_module(xmp: str, operation: str, modversion: int,
                   params: str, enabled: int = 1) -> str:
    """Append a new module to the history stack."""
    # Find the last </rdf:li> in history and insert before </rdf:Seq>
    # Count existing entries to get the num
    nums = re.findall(r'darktable:num="(\d+)"', xmp)
    next_num = max(int(n) for n in nums) + 1 if nums else 0

    new_entry = f"""
     <rdf:li
      darktable:num="{next_num}"
      darktable:operation="{operation}"
      darktable:enabled="{enabled}"
      darktable:modversion="{modversion}"
      darktable:params="{params}"
      darktable:multi_name=""
      darktable:multi_name_hand_edited="0"
      darktable:multi_priority="0"
      darktable:blendop_version="14"
      darktable:blendop_params="{BLEND_DEFAULT}"/>"""

    # Insert before </rdf:Seq> in the history section
    xmp = xmp.replace(
        '    </rdf:Seq>\n   </darktable:history>',
        f'{new_entry}\n    </rdf:Seq>\n   </darktable:history>'
    )
    return xmp


if __name__ == "__main__":
    base = Path("/tmp/_DSC9620_test.ARW.xmp")
    out = Path("/tmp/_DSC9620_wa_style.ARW.xmp")

    if not base.exists():
        print(f"Base XMP not found: {base}")
        exit(1)

    build_wa_style_xmp(base, out, ev=0.35)
    print(f"Built styled XMP: {out}")
    print(f"Size: {out.stat().st_size} bytes")

    # Verify
    content = out.read_text()
    for mod in ["exposure", "sharpen", "vignette"]:
        if f'operation="{mod}"' in content:
            print(f"  ✓ {mod} module present")
        else:
            print(f"  ✗ {mod} module MISSING")
