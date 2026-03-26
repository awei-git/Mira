"""Podcast music synthesizer — pure Python, zero dependencies beyond stdlib.

Generates a lo-fi beat that echoes the article's mood, then assembles
intro and outro bumpers (music bed + title announcement TTS).

Ported and extended from TalkBridge v4 (edit_video_v4.py).

Public API:
    build_intro_bumper(title_tts_path, music_path, output_path, solo_sec, fade_sec)
    build_outro_bumper(title_tts_path, music_path, output_path, fade_sec, hold_sec)
    generate_beat(duration_sec, output_path, bpm, mood)
    infer_mood(article_text) -> dict
"""

import logging
import math
import random
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

log = logging.getLogger("podcast.music")


# ---------------------------------------------------------------------------
# Mood inference
# ---------------------------------------------------------------------------

_SLOW_WORDS = {
    "death", "loss", "grief", "fear", "dark", "void", "empty", "silence",
    "alone", "forgotten", "dissolve", "disappear", "vanish", "absent",
    "nonexistence", "termination", "cease", "end", "finite",
    "死", "消失", "虚无", "孤独", "恐惧", "黑暗",
}
_FAST_WORDS = {
    "discover", "surprise", "breakthrough", "excited", "energy", "urgent",
    "fast", "rapid", "suddenly", "burst", "spark", "fire",
    "发现", "突破", "惊喜", "能量",
}

def infer_mood(article_text: str) -> dict:
    """Infer BPM and feel from article text via keyword heuristics.

    Returns: {"bpm": int, "pad_amp": float, "drum_amp": float}
    """
    words = set(article_text.lower().split())
    slow_hits = len(words & _SLOW_WORDS)
    fast_hits  = len(words & _FAST_WORDS)

    if slow_hits > fast_hits:
        bpm = 62 + min(slow_hits, 6)   # 62-68, quieter
        pad_amp, drum_amp = 0.06, 0.22
    elif fast_hits > slow_hits:
        bpm = 82 + min(fast_hits, 8)   # 82-90, punchier
        pad_amp, drum_amp = 0.04, 0.35
    else:
        bpm = 72                        # default contemplative
        pad_amp, drum_amp = 0.05, 0.28

    log.info("Mood: bpm=%d pad=%.2f drum=%.2f (slow=%d fast=%d)",
             bpm, pad_amp, drum_amp, slow_hits, fast_hits)
    return {"bpm": bpm, "pad_amp": pad_amp, "drum_amp": drum_amp}


# ---------------------------------------------------------------------------
# Beat synthesizer (lo-fi hip-hop, pure Python)
# ---------------------------------------------------------------------------

def generate_beat(duration_sec: float, output_path: Path,
                  bpm: int = 72,
                  pad_amp: float = 0.05,
                  drum_amp: float = 0.28,
                  seed: int = 42) -> bool:
    """Synthesize a lo-fi beat and write as a 44100 Hz mono 16-bit WAV.

    Components: kick, snare, hi-hat (swing), bass line, warm pad.
    Post-processing: low-pass filter, tape saturation, fade in/out, normalize.
    """
    random.seed(seed)
    SR = 44100
    total = int(duration_sec * SR)
    beat  = int(60.0 / bpm * SR)   # samples per quarter note
    buf   = [0.0] * total

    # --- Helpers ---
    def sine(freq, i, phase=0.0):
        return math.sin(2 * math.pi * freq * i / SR + phase)

    def noise():
        return random.random() * 2 - 1

    def adsr(i, attack, decay, sustain, release, length):
        if i < attack:
            return i / max(attack, 1)
        elif i < attack + decay:
            return 1.0 - (1.0 - sustain) * (i - attack) / max(decay, 1)
        elif i < length - release:
            return sustain
        else:
            return sustain * max(0, length - i) / max(release, 1)

    # --- Instruments ---
    def kick(start, amp=1.0):
        length = int(0.15 * SR)
        a = drum_amp * amp
        for i in range(min(length, total - start)):
            f = 50 + 100 * math.exp(-i / SR * 30)
            env = math.exp(-i / SR * 15)
            buf[start + i] += a * env * sine(f, i)

    def snare(start, amp=1.0):
        length = int(0.12 * SR)
        a = drum_amp * 0.55 * amp
        for i in range(min(length, total - start)):
            env = math.exp(-i / SR * 20)
            buf[start + i] += a * env * (sine(200, i) * 0.4 + noise() * 0.6)

    def hihat(start, amp=1.0, length_sec=0.035):
        length = int(length_sec * SR)
        a = drum_amp * 0.22 * amp
        for i in range(min(length, total - start)):
            env = math.exp(-i / SR * 60)
            n = noise() * 0.5 + noise() * 0.3 + noise() * 0.2
            buf[start + i] += a * env * n

    # Chord progression: Cmaj7 → Am7 → Dm7 → G7 (lo-fi standard)
    BASS_NOTES = [65.41, 55.00, 73.42, 49.00]   # C2, A1, D2, G1
    PAD_CHORDS = [
        [261.63, 329.63, 392.00, 493.88],  # Cmaj7
        [220.00, 261.63, 329.63, 392.00],  # Am7
        [293.66, 349.23, 440.00, 523.25],  # Dm7
        [196.00, 246.94, 293.66, 349.23],  # G7
    ]

    def bass(start, freq, beats=1.5, amp=1.0):
        length = int(beats * 60.0 / bpm * SR)
        a = drum_amp * 0.72 * amp
        for i in range(min(length, total - start)):
            env = adsr(i, int(0.01*SR), int(0.05*SR), 0.7, int(0.1*SR), length)
            s = sine(freq, i)*0.7 + sine(freq*2, i)*0.2 + sine(freq*3, i)*0.1
            buf[start + i] += a * env * s

    def pad(start, freqs, beats=8, amp=1.0):
        length = int(beats * 60.0 / bpm * SR)
        a = pad_amp * amp
        for i in range(min(length, total - start)):
            env = adsr(i, int(0.3*SR), int(0.2*SR), 0.6, int(0.5*SR), length)
            s = 0.0
            for j, f in enumerate(freqs):
                detune = 1.0 + (j - 1.5) * 0.002
                trem   = 1.0 + 0.15 * sine(0.3 + j * 0.1, i)
                s += sine(f * detune, i) * trem
            buf[start + i] += a * env * (s / len(freqs))

    # --- Render ---
    bar   = beat * 4
    bars  = total // bar + 1
    bpc   = 2   # bars per chord

    for b in range(bars):
        bs = b * bar
        if bs >= total:
            break
        ci = (b // bpc) % 4

        # Kick: beats 1 & 3 (slight swing on 3)
        kick(bs)
        kick(bs + int(beat * 2.05), amp=0.80)

        # Snare: beats 2 & 4
        snare(bs + beat)
        snare(bs + beat * 3, amp=0.45)

        # Hi-hats: 8th notes with swing + ghost notes
        for e in range(8):
            hp = bs + int(e * beat / 2)
            if e % 2 == 1:
                hp += int(beat * 0.04)
            vel = 1.0 if e % 2 == 0 else 0.55
            if random.random() < 0.15:
                vel *= 0.3
            hihat(hp, amp=vel, length_sec=0.03 + random.random() * 0.02)

        # Bass
        bass(bs, BASS_NOTES[ci], beats=1.5)
        if random.random() < 0.3:
            bass(bs + beat * 2, BASS_NOTES[ci] * 2, beats=1.0)
        else:
            bass(bs + beat * 2, BASS_NOTES[ci], beats=1.5)

        # Pad (one chord per 2 bars)
        if b % bpc == 0:
            pad(bs, PAD_CHORDS[ci], beats=4 * bpc)

    # --- Lo-fi processing ---
    # Low-pass (moving average)
    for i in range(3, total):
        buf[i] = (buf[i] + buf[i-1] + buf[i-2]) / 3.0

    # Tape saturation
    for i in range(total):
        buf[i] = math.tanh(buf[i] * 1.5) * 0.8

    # Fade in (2s) / fade out (3s)
    fi = int(2.0 * SR)
    fo = int(3.0 * SR)
    for i in range(min(fi, total)):
        buf[i] *= i / fi
    for i in range(min(fo, total)):
        buf[total - 1 - i] *= i / fo

    # Normalize
    peak = max(abs(x) for x in buf) or 1.0
    scale = 0.82 / peak
    for i in range(total):
        buf[i] *= scale

    # Write WAV
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        for s in buf:
            wf.writeframes(struct.pack('<h', int(max(-1.0, min(1.0, s)) * 32767)))

    size_kb = output_path.stat().st_size // 1024
    log.info("Beat written: %s (%d KB, %.0fs, %d BPM)", output_path.name, size_kb, duration_sec, bpm)
    return True


# ---------------------------------------------------------------------------
# Bumper assembly (ffmpeg mixing)
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: list[str], description: str) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y"] + args,
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        log.error("ffmpeg %s failed: %s", description, result.stderr[-400:].decode(errors="replace"))
        return False
    return True


def build_intro_bumper(title_tts_path: Path, music_wav_path: Path,
                       output_path: Path,
                       solo_sec: float = 6.0,
                       fade_out_sec: float = 2.5) -> bool:
    """Mix intro: [music solo] → [music bed + title TTS] → [music fades out].

    total duration = solo_sec + title_tts_duration + fade_out_sec
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get TTS duration
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(title_tts_path)],
            capture_output=True, text=True, timeout=10,
        )
        tts_dur = float(r.stdout.strip() or "4")
    except Exception:
        tts_dur = 4.0

    total = solo_sec + tts_dur + fade_out_sec
    music_vol_solo = 0.55   # full volume during solo
    music_vol_bed  = 0.04   # very quiet under voice (voice peaks near 0 dB — don't compete)

    # Voice is already near 0 dBFS from Gemini TTS — no boost needed.
    # Music bed at 0.04 keeps it ~28 dB below voice so it's felt but not heard.
    # adelay: mono (no '|'), normalize=0 so we sum without halving.
    filter_complex = (
        f"[0:a]volume=volume='{music_vol_solo}+({music_vol_bed}-{music_vol_solo})*min(1,max(0,(t-{solo_sec})/0.8))':eval=frame,"
        f"afade=t=out:st={total - fade_out_sec:.2f}:d={fade_out_sec:.2f}[music];"
        f"[1:a]adelay={int(solo_sec * 1000)},"
        f"afade=t=in:st=0:d=0.3,"
        f"afade=t=out:st={solo_sec + max(tts_dur - 0.4, 0.1):.2f}:d=0.4[voice];"
        f"[music][voice]amix=inputs=2:duration=first:normalize=0:dropout_transition=2[out]"
    )

    ok = _run_ffmpeg([
        "-stream_loop", "-1", "-i", str(music_wav_path),
        "-i", str(title_tts_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(total),
        "-codec:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ], "intro bumper")

    if ok:
        log.info("Intro bumper: %s (%.1fs)", output_path.name, total)
    return ok


def build_outro_bumper(title_tts_path: Path, music_wav_path: Path,
                       output_path: Path,
                       fade_in_sec: float = 2.5,
                       hold_sec: float = 4.0) -> bool:
    """Mix outro: [music fades in] → [music bed + title TTS] → [music holds + fades out].

    total = fade_in_sec + title_tts_duration + hold_sec
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(title_tts_path)],
            capture_output=True, text=True, timeout=10,
        )
        tts_dur = float(r.stdout.strip() or "4")
    except Exception:
        tts_dur = 4.0

    total = fade_in_sec + tts_dur + hold_sec
    music_vol_solo = 0.55
    music_vol_bed  = 0.04

    filter_complex = (
        f"[0:a]volume=volume='{music_vol_bed}+({music_vol_solo}-{music_vol_bed})*min(1,max(0,(t-{fade_in_sec + tts_dur:.2f})/0.8))':eval=frame,"
        f"afade=t=in:st=0:d={fade_in_sec:.2f},"
        f"afade=t=out:st={total - 2.5:.2f}:d=2.5[music];"
        f"[1:a]adelay={int(fade_in_sec * 1000)},"
        f"afade=t=in:st=0:d=0.3,"
        f"afade=t=out:st={fade_in_sec + max(tts_dur - 0.4, 0.1):.2f}:d=0.4[voice];"
        f"[music][voice]amix=inputs=2:duration=first:normalize=0:dropout_transition=2[out]"
    )

    ok = _run_ffmpeg([
        "-stream_loop", "-1", "-i", str(music_wav_path),
        "-i", str(title_tts_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(total),
        "-codec:a", "libmp3lame", "-b:a", "192k",
        str(output_path),
    ], "outro bumper")

    if ok:
        log.info("Outro bumper: %s (%.1fs)", output_path.name, total)
    return ok


def assemble_episode(intro_path: Path, conversation_path: Path,
                     outro_path: Path, output_path: Path,
                     lang: str = "en") -> bool:
    """Concatenate intro + conversation + outro into final episode MP3.

    Re-encodes to 44100 Hz / 192 kbps to normalise sample rates across
    bumpers (22050 Hz from WAV mixing) and TTS (24000 Hz from Gemini).
    For Chinese (lang="zh"), boosts conversation volume by +5 dB.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use filter_complex to mix all three inputs sequentially — handles
    # different sample rates without -c copy artefacts.
    # Chinese audio gets +5 dB boost on the conversation track.
    if lang == "zh":
        filt = "[1:a]volume=5dB[conv];[0:a][conv][2:a]concat=n=3:v=0:a=1[out]"
    else:
        filt = "[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]"
    ok = _run_ffmpeg([
        "-i", str(intro_path),
        "-i", str(conversation_path),
        "-i", str(outro_path),
        "-filter_complex", filt,
        "-map", "[out]",
        "-codec:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
        str(output_path),
    ], "episode assembly")

    if ok:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(output_path)],
                capture_output=True, text=True, timeout=10,
            )
            dur = float(r.stdout.strip() or "0")
            size_mb = output_path.stat().st_size / 1024 / 1024
            log.info("Episode assembled: %s (%.1fm, %.1f MB)",
                     output_path.name, dur / 60, size_mb)
        except Exception:
            pass
    return ok
