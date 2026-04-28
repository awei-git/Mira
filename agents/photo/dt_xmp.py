"""darktable XMP sidecar generator.

Generates per-image XMP sidecars with precise module parameters
for darktable-cli processing. Based on darktable 5.4.x C struct formats.

All params are little-endian packed C structs, encoded as hex or gz-base64.
"""

import struct
import zlib
import base64
import xml.etree.ElementTree as ET

# This module *generates* XMP sidecars (Element/SubElement) — no parse of
# untrusted XML, so defusedxml's secure-parse subset isn't applicable.
# All XML produced here comes from data we control.
from pathlib import Path


# ---------------------------------------------------------------------------
# Param encoding
# ---------------------------------------------------------------------------


def encode_params(raw_bytes: bytes) -> str:
    if len(raw_bytes) < 100:
        return raw_bytes.hex()
    compressed = zlib.compress(raw_bytes)
    factor = min(len(raw_bytes) // len(compressed) + 1, 99)
    b64 = base64.b64encode(compressed).decode("ascii")
    return f"gz{factor:02d}{b64}"


# ---------------------------------------------------------------------------
# Module param builders
# ---------------------------------------------------------------------------


def make_exposure(ev: float = 0.0, black: float = 0.0) -> bytes:
    """Exposure module (modversion=7)."""
    return struct.pack(
        "<iffffii",
        0,  # MANUAL mode
        black,
        ev,
        50.0,  # deflicker_pct (unused)
        -4.0,  # deflicker_target (unused)
        0,  # compensate_exposure_bias
        0,  # compensate_hilite_pres
    )


def make_filmic(
    white_ev: float = 4.0,
    black_ev: float = -8.0,
    contrast: float = 1.0,
    grey_point: float = 18.45,
    latitude: float = 0.01,
    saturation: float = 0.0,
) -> bytes:
    """Filmic RGB module (modversion=6)."""
    return struct.pack(
        "<18f11i",
        grey_point,  # grey_point_source
        black_ev,  # black_point_source
        white_ev,  # white_point_source
        0.0,  # reconstruct_threshold
        3.0,  # reconstruct_feather
        100.0,  # reconstruct_bloom_vs_details
        100.0,  # reconstruct_grey_vs_color
        0.0,  # reconstruct_structure_vs_texture
        0.0,  # security_factor
        18.45,  # grey_point_target
        0.01517634,  # black_point_target
        100.0,  # white_point_target
        4.0,  # output_power
        latitude,
        contrast,
        saturation,
        0.0,  # balance
        0.2,  # noise_level
        4,  # preserve_color: EUCLIDEAN
        4,  # version: V5
        1,  # auto_hardness
        0,  # custom_grey
        1,  # high_quality_reconstruction
        1,  # noise_distribution: GAUSSIAN
        1,  # shadows: SOFT
        1,  # highlights: SOFT
        0,  # compensate_icc_black
        2,  # spline_version: V3
        0,  # enable_highlight_reconstruction
    )


def make_colorbalance(
    shadows_Y=0.0,
    shadows_C=0.0,
    shadows_H=0.0,
    midtones_Y=0.0,
    midtones_C=0.0,
    midtones_H=0.0,
    highlights_Y=0.0,
    highlights_C=0.0,
    highlights_H=0.0,
    global_Y=0.0,
    global_C=0.0,
    global_H=0.0,
    shadows_weight=1.0,
    white_fulcrum=0.0,
    highlights_weight=1.0,
    chroma_shadows=0.0,
    chroma_highlights=0.0,
    chroma_global=0.0,
    chroma_midtones=0.0,
    saturation_global=0.0,
    saturation_highlights=0.0,
    saturation_midtones=0.0,
    saturation_shadows=0.0,
    hue_angle=0.0,
    brilliance_global=0.0,
    brilliance_highlights=0.0,
    brilliance_midtones=0.0,
    brilliance_shadows=0.0,
    mask_grey_fulcrum=0.1845,
    vibrance=0.0,
    grey_fulcrum=0.1845,
    contrast=0.0,
    saturation_formula=1,
) -> bytes:
    """Color Balance RGB module (modversion=5)."""
    fields = [
        shadows_Y,
        shadows_C,
        shadows_H,
        midtones_Y,
        midtones_C,
        midtones_H,
        highlights_Y,
        highlights_C,
        highlights_H,
        global_Y,
        global_C,
        global_H,
        shadows_weight,
        white_fulcrum,
        highlights_weight,
        chroma_shadows,
        chroma_highlights,
        chroma_global,
        chroma_midtones,
        saturation_global,
        saturation_highlights,
        saturation_midtones,
        saturation_shadows,
        hue_angle,
        brilliance_global,
        brilliance_highlights,
        brilliance_midtones,
        brilliance_shadows,
        mask_grey_fulcrum,
        vibrance,
        grey_fulcrum,
        contrast,
    ]
    # 32 floats + 1 int32
    return struct.pack("<32fi", *fields, saturation_formula)


def make_tone_equalizer(
    noise=0.0,
    ultra_deep_blacks=0.0,
    deep_blacks=0.0,
    blacks=0.0,
    shadows=0.0,
    midtones=0.0,
    highlights=0.0,
    whites=0.0,
    speculars=0.0,
    blending=5.0,
    smoothing=1.414,
    feathering=1.0,
    quantization=0.0,
    contrast_boost=0.0,
    exposure_boost=0.0,
    details=0,
    method=0,
    iterations=1,
) -> bytes:
    """Tone Equalizer module (modversion=2)."""
    return struct.pack(
        "<15f3i",
        noise,
        ultra_deep_blacks,
        deep_blacks,
        blacks,
        shadows,
        midtones,
        highlights,
        whites,
        speculars,
        blending,
        smoothing,
        feathering,
        quantization,
        contrast_boost,
        exposure_boost,
        details,
        method,
        iterations,
    )


# ---------------------------------------------------------------------------
# XMP builder
# ---------------------------------------------------------------------------

# Default blendop: normal blend, 100% opacity, no masks, version 11
DEFAULT_BLENDOP = "gz12eJxjYGBgkGAAgRNODESDBnsIHll8ANNSGQM="


def build_xmp(history: list[dict], derived_from: str = "image.ARW") -> str:
    """Build a complete darktable XMP sidecar string.

    history: list of dicts with keys:
        operation (str), modversion (int), params (bytes),
        enabled (bool, default True)
    """
    root = ET.Element(
        "x:xmpmeta",
        {
            "xmlns:x": "adobe:ns:meta/",
            "x:xmptk": "XMP Core 4.4.0-Exiv2",
        },
    )
    rdf = ET.SubElement(
        root,
        "rdf:RDF",
        {
            "xmlns:rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        },
    )
    desc = ET.SubElement(
        rdf,
        "rdf:Description",
        {
            "rdf:about": "",
            "xmlns:xmp": "http://ns.adobe.com/xap/1.0/",
            "xmlns:xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:darktable": "http://darktable.sf.net/",
            "xmp:Rating": "0",
            "xmpMM:DerivedFrom": derived_from,
            "darktable:xmp_version": "5",
            "darktable:raw_params": "0",
            "darktable:auto_presets_applied": "1",
        },
    )

    # Empty mask sequences (required)
    for tag in ("mask_id", "mask_type", "mask_name", "mask_version", "mask", "mask_nb", "mask_src"):
        el = ET.SubElement(desc, f"darktable:{tag}")
        ET.SubElement(el, "rdf:Seq")

    # History stack
    hist_el = ET.SubElement(desc, "darktable:history")
    seq = ET.SubElement(hist_el, "rdf:Seq")

    for entry in history:
        li = ET.SubElement(seq, "rdf:li")
        li.set("darktable:operation", entry["operation"])
        li.set("darktable:enabled", "1" if entry.get("enabled", True) else "0")
        li.set("darktable:modversion", str(entry["modversion"]))
        li.set("darktable:params", encode_params(entry["params"]))
        li.set("darktable:multi_name", entry.get("multi_name", ""))
        li.set("darktable:multi_priority", str(entry.get("multi_priority", 0)))
        li.set("darktable:blendop_version", "11")
        li.set("darktable:blendop_params", entry.get("blendop_params", DEFAULT_BLENDOP))

    desc.set("darktable:history_end", str(len(history)))

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_xmp(history: list[dict], output_path: Path, derived_from: str = "image.ARW"):
    """Write XMP sidecar to file."""
    xmp_str = build_xmp(history, derived_from)
    output_path.write_text(xmp_str, encoding="utf-8")
    return output_path
