"""Beat analyzer — extract musical structure from audio for beat-synced editing.

Phase 0 of the enhanced video pipeline:
  music file → librosa analysis → beat_map.json

Outputs beat timestamps, phrase boundaries, tempo, and energy curve
for use in screenplay generation and clip-to-beat alignment.
"""

import json
import subprocess
from pathlib import Path


def _ensure_librosa():
    """Lazy import librosa (heavy dependency)."""
    try:
        import librosa
        import numpy as np

        return librosa, np
    except ImportError:
        subprocess.run(
            ["pip3", "install", "--break-system-packages", "librosa", "soundfile"],
            capture_output=True,
            timeout=120,
        )
        import librosa
        import numpy as np

        return librosa, np


def analyze_beats(music_path: Path, work_dir: Path) -> dict:
    """Analyze music file for beats, phrases, tempo, and energy.

    Returns dict with:
        tempo: float (BPM)
        duration: float (seconds)
        beats: list[float] (beat timestamps)
        phrases: list[float] (every 4 beats)
        energy_curve: list[dict] (time + energy at phrase intervals)
        sections: list[dict] (detected energy sections: intro/build/peak/outro)
    """
    librosa, np = _ensure_librosa()

    y, sr = librosa.load(str(music_path), sr=22050)
    duration = librosa.get_duration(y=y, sr=sr)

    # Beat detection
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo_val = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    beats = [round(float(t), 3) for t in beat_times]
    phrases = [round(float(t), 3) for t in beat_times[::4]]

    # Energy curve (RMS at phrase intervals)
    rms = librosa.feature.rms(y=y)[0]
    rms_times = librosa.frames_to_time(range(len(rms)), sr=sr)
    rms_max = float(rms.max()) if rms.max() > 0 else 1.0

    energy_curve = []
    for pt in phrases:
        idx = int(np.argmin(np.abs(rms_times - pt)))
        # Average energy in a window around this phrase
        window = rms[max(0, idx - 5) : idx + 5]
        avg_energy = float(window.mean()) / rms_max if len(window) > 0 else 0
        energy_curve.append({"time": pt, "energy": round(avg_energy, 3)})

    # Detect sections based on energy (simple threshold-based)
    sections = _detect_sections(energy_curve, duration)

    result = {
        "tempo": round(tempo_val, 1),
        "duration": round(float(duration), 1),
        "beats": beats,
        "phrases": phrases,
        "energy_curve": energy_curve,
        "sections": sections,
    }

    # Save to work_dir
    out_path = work_dir / "beat_map.json"
    out_path.write_text(json.dumps(result, indent=2))

    return result


def _detect_sections(energy_curve: list[dict], duration: float) -> list[dict]:
    """Detect intro/build/peak/outro sections from energy curve."""
    if not energy_curve:
        return [{"start": 0, "end": duration, "type": "full", "energy": 0.5}]

    energies = [e["energy"] for e in energy_curve]
    times = [e["time"] for e in energy_curve]
    n = len(energies)

    if n < 4:
        return [{"start": 0, "end": duration, "type": "full", "energy": round(sum(energies) / n, 3)}]

    # Split into roughly 4 sections
    quarter = n // 4
    sections = []

    # Intro: first quarter
    intro_energy = sum(energies[:quarter]) / quarter
    sections.append(
        {
            "start": round(times[0], 2),
            "end": round(times[quarter], 2),
            "type": "intro",
            "energy": round(intro_energy, 3),
        }
    )

    # Build: second quarter
    build_energy = sum(energies[quarter : 2 * quarter]) / quarter
    sections.append(
        {
            "start": round(times[quarter], 2),
            "end": round(times[2 * quarter], 2),
            "type": "build",
            "energy": round(build_energy, 3),
        }
    )

    # Peak: third quarter (or wherever energy is highest)
    peak_energy = sum(energies[2 * quarter : 3 * quarter]) / quarter
    sections.append(
        {
            "start": round(times[2 * quarter], 2),
            "end": round(times[3 * quarter], 2),
            "type": "peak",
            "energy": round(peak_energy, 3),
        }
    )

    # Outro: last quarter
    outro_start = 3 * quarter
    outro_energy = sum(energies[outro_start:]) / (n - outro_start)
    sections.append(
        {
            "start": round(times[outro_start], 2),
            "end": round(duration, 2),
            "type": "outro",
            "energy": round(outro_energy, 3),
        }
    )

    return sections


def summarize_beat_map(beat_map: dict) -> str:
    """Create a concise text summary for injection into LLM prompts."""
    sections_desc = ", ".join(
        f"{s['type']}({s['start']:.0f}-{s['end']:.0f}s, energy={s['energy']:.2f})" for s in beat_map.get("sections", [])
    )
    return (
        f"Music: {beat_map['tempo']:.0f} BPM, {beat_map['duration']:.0f}s total. "
        f"{len(beat_map['phrases'])} phrases (every 4 beats). "
        f"Sections: {sections_desc}. "
        f"Beat interval: ~{60/beat_map['tempo']:.2f}s. "
        f"Phrase interval: ~{4*60/beat_map['tempo']:.2f}s."
    )
