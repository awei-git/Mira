"""TTS provider comparison test.

Generates the same text with Gemini, 火山引擎(豆包), CosyVoice(通义), and ChatTTS,
saves output to comparison/ directory for listening.

Usage:
    python tts_comparison.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
shared = str(here.parent.parent / "lib")
if shared not in sys.path:
    sys.path.insert(0, shared)

OUTPUT_DIR = here / "comparison"
OUTPUT_DIR.mkdir(exist_ok=True)

# Test text — a typical podcast opening with natural Chinese
TEST_HOST = (
    "哎，Mira，我跟你说个事儿。我前阵子在网上看到一个帖子，"
    "一个程序员说，他跟同事合作了十几年，关系特别好。"
    "结果去年公司引入了AI编程工具之后，他们三个人吵了一架。"
    "不是那种小拌嘴啊，是真的那种，互相看着对方觉得，你到底是谁。"
)
TEST_MIRA = (
    "嗯，我知道你说的那种感觉。不是因为AI抢了谁的活儿，"
    "而是突然之间，大家发现自己对同一件事的理解完全不一样。"
    "之前从来没意识到。其实吧，这个场景我在文章里写过一个特别像的。"
)


def test_gemini():
    """Test Gemini TTS (current provider)."""
    print("\n=== Gemini TTS ===")
    try:
        from handler import _call_gemini_tts_text, _get_gemini_key
        from handler import VOICE_HOST_GEMINI, VOICE_MIRA_ZH_GEMINI

        key = _get_gemini_key()
        if not key:
            print("  No Gemini key found")
            return

        for label, text, voice in [
            ("host", TEST_HOST, VOICE_HOST_GEMINI),
            ("mira", TEST_MIRA, VOICE_MIRA_ZH_GEMINI),
        ]:
            print(f"  Generating {label}...")
            pcm = _call_gemini_tts_text(text, voice, "zh", key)
            if pcm:
                # Convert PCM to MP3
                import subprocess

                out = OUTPUT_DIR / f"gemini_{label}.mp3"
                proc = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "s16le",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        "-i",
                        "pipe:0",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "2",
                        str(out),
                    ],
                    input=pcm,
                    capture_output=True,
                )
                print(f"  Saved: {out} ({out.stat().st_size // 1024} KB)")
            else:
                print(f"  Failed for {label}")
            time.sleep(2)
    except Exception as e:
        print(f"  Error: {e}")


def test_volcengine():
    """Test 火山引擎 (豆包) TTS."""
    print("\n=== 火山引擎 (豆包) TTS ===")
    print("  Requires: volcengine account + appid + access token")
    print("  Sign up: https://www.volcengine.com/product/tts")

    # Check if credentials exist
    from config import load_secrets

    secrets = load_secrets()
    volc_config = secrets.get("volcengine", {})
    appid = volc_config.get("appid", "")
    token = volc_config.get("token", "")

    if not appid or not token:
        print("  No volcengine credentials in secrets.yml. Add:")
        print("    volcengine:")
        print('      appid: "your-app-id"')
        print('      token: "your-access-token"')
        print("  Skipping.")
        return

    import json
    import urllib.request

    for label, text in [("host", TEST_HOST), ("mira", TEST_MIRA)]:
        voice_type = "BV701_streaming" if label == "host" else "BV700_streaming"
        print(f"  Generating {label} (voice: {voice_type})...")
        try:
            payload = json.dumps(
                {
                    "appid": appid,
                    "text": text,
                    "format": "mp3",
                    "voice_type": voice_type,
                    "sample_rate": 24000,
                    "speed": 1.0,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                "https://openspeech.bytedance.com/api/v1/tts",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                    "Resource-Id": "volc.megatts.voiceclone",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                audio = resp.read()
            out = OUTPUT_DIR / f"volcengine_{label}.mp3"
            out.write_bytes(audio)
            print(f"  Saved: {out} ({out.stat().st_size // 1024} KB)")
        except Exception as e:
            print(f"  Error: {e}")
        time.sleep(1)


def test_cosyvoice():
    """Test CosyVoice (通义/阿里云百炼) TTS."""
    print("\n=== CosyVoice (通义) TTS ===")

    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not dashscope_key:
        # Try secrets.yml
        try:
            from config import load_secrets

            secrets = load_secrets()
            dashscope_key = secrets.get("dashscope", {}).get("api_key", "")
        except Exception:
            pass

    if not dashscope_key:
        print("  No DASHSCOPE_API_KEY. Add to secrets.yml:")
        print("    dashscope:")
        print('      api_key: "your-key"')
        print("  Get one at: https://dashscope.console.aliyun.com/")
        print("  Skipping.")
        return

    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = dashscope_key

        for label, text, voice in [
            ("host", TEST_HOST, "longanyang"),
            ("mira", TEST_MIRA, "longxiaochun_v2"),
        ]:
            print(f"  Generating {label} (voice: {voice})...")
            synthesizer = SpeechSynthesizer(model="cosyvoice-v3-flash", voice=voice)
            audio = synthesizer.call(text)
            if audio:
                out = OUTPUT_DIR / f"cosyvoice_{label}.mp3"
                out.write_bytes(audio)
                print(f"  Saved: {out} ({out.stat().st_size // 1024} KB)")
            else:
                print(f"  Failed for {label}")
            time.sleep(1)
    except ImportError:
        print("  pip install dashscope first")
    except Exception as e:
        print(f"  Error: {e}")


def test_chattts():
    """Test ChatTTS (local, open source)."""
    print("\n=== ChatTTS (local) ===")
    try:
        import ChatTTS
        import soundfile
        import numpy as np

        chat = ChatTTS.Chat()
        chat.load()

        for label, text in [("host", TEST_HOST), ("mira", TEST_MIRA)]:
            print(f"  Generating {label}...")
            # Use different random seeds for different "voices"
            seed = 42 if label == "host" else 137
            params = ChatTTS.Chat.InferCodeParams(spk_emb=chat.sample_random_speaker(seed))
            wavs = chat.infer([text], params_infer_code=params, use_decoder=True)
            if wavs and len(wavs[0]) > 0:
                out = OUTPUT_DIR / f"chattts_{label}.wav"
                soundfile.write(str(out), wavs[0][0], 24000)
                print(f"  Saved: {out} ({out.stat().st_size // 1024} KB)")
            else:
                print(f"  Failed for {label}")
    except ImportError:
        print("  ChatTTS not installed. Run:")
        print("    pip install chattts soundfile")
        print("  Skipping.")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    print("TTS Comparison Test")
    print(f"Output: {OUTPUT_DIR}")
    print(f"\nTest text (HOST): {TEST_HOST[:50]}...")
    print(f"Test text (MIRA): {TEST_MIRA[:50]}...")

    test_gemini()
    test_cosyvoice()
    test_volcengine()
    test_chattts()

    print("\n=== Done ===")
    print(f"Listen to files in: {OUTPUT_DIR}")
