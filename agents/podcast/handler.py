"""Podcast agent — convert written articles into spoken audio.

Two modes:
  voiceover    — single narrator (Mira reads her essay aloud)
  conversation — two-host dialogue (Human host interviews Mira about the article)

Pipeline (conversation):
    1. Generate dialogue script (Host + Mira, ~3000-3500 words, ~20-25 min)
    2. Per-turn TTS via Gemini (one call per turn, HOST or MIRA voice)
    3. PCM → MP3 per turn, concatenate → conversation.mp3
    4. Music bumpers → final episode

Language support: English (default) and Chinese (lang="zh") for both modes.

Usage:
    from handler import handle, generate_audio_for_article, generate_conversation_for_article
"""
import base64
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger("podcast")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GEMINI_MODEL_TTS   = "gemini-2.5-pro-preview-tts"
GEMINI_MODEL_THINK = "gemini-2.5-pro"          # for script generation

# ---------------------------------------------------------------------------
# MiniMax TTS config (primary TTS backend)
# ---------------------------------------------------------------------------

MINIMAX_MODEL_TTS = "speech-02-hd"
MINIMAX_API_URL   = "https://api.minimax.io/v1/t2a_v2"

# MiniMax voice IDs — one voice per character, consistent across all turns
# Full list: platform.minimax.io/docs/faq/system-voice-id
VOICE_HOST_ZH_MM = "Chinese (Mandarin)_Sincere_Adult"   # warm, grounded podcast host
VOICE_MIRA_ZH_MM = "Chinese (Mandarin)_Crisp_Girl"     # direct, clear — Mira ZH
VOICE_HOST_EN_MM = "English_Trustworth_Man"             # English host (note: no 'y')
VOICE_MIRA_EN_MM = "English_expressive_narrator"        # English Mira

# MiniMax audio params for ZH
SPEED_ZH_MM = 0.95   # slightly slower than normal for clearer delivery
VOL_MM      = 1.5    # louder than default (1.0)

# ---------------------------------------------------------------------------
# Gemini TTS config (active TTS backend — switched from MiniMax 2026-03-16)
# MiniMax kept as fallback reference but all live calls now use Gemini.
# ---------------------------------------------------------------------------
VOICE_MIRA_EN_GEMINI = "Aoede"   # Mira EN: female, warm, thoughtful
VOICE_MIRA_ZH_GEMINI = "Kore"    # Mira ZH: female, firm, crisp (the "crispy" voice)
VOICE_HOST_GEMINI    = "Charon"  # Human host: male, curious, grounded
SPEED_ZH_GEMINI      = 1.12      # Gemini ZH tends to be slow; 1.12x tightens it up

# ---------------------------------------------------------------------------
# TTS provider selection
# ---------------------------------------------------------------------------
# 'gemini'  — Gemini only (may hit QPM limits on long episodes)
# 'minimax' — MiniMax only (charges per character; wallet balance preserved)
# 'auto'    — Gemini first; on 429 quota exhaustion, fallback to MiniMax
TTS_PROVIDER = "minimax"

# Chunk limits — kept for voiceover mode (single-speaker)
MAX_CHARS_VOICEOVER    = 2500
MAX_CHARS_CONVERSATION = 1200   # unused in per-turn mode, kept for reference


def _get_gemini_key() -> str:
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from sub_agent import _get_api_key
    return _get_api_key("gemini")


def _get_minimax_key() -> str:
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from sub_agent import _get_api_key
    return _get_api_key("minimax")


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _slug(title: str) -> str:
    s = re.sub(r'[^\w\s-]', '', title.lower())
    return re.sub(r'[\s_]+', '-', s).strip('-')[:50] or 'untitled'


def _pcm_chunk_to_mp3(pcm_data: bytes, output_path: Path) -> bool:
    """Convert one PCM chunk to a CBR MP3 file (no Xing/VBR header)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.pcm', delete=False) as f:
        f.write(pcm_data)
        pcm_path = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", pcm_path,
             "-codec:a", "libmp3lame", "-b:a", "192k", "-write_xing", "0",
             str(output_path)],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            log.error("ffmpeg failed: %s", result.stderr[:300])
            return False
    finally:
        Path(pcm_path).unlink(missing_ok=True)
    return True


def _pcm_to_mp3(pcm_data: bytes, output_path: Path) -> bool:
    """Write PCM bytes to a temp file, convert to MP3 via ffmpeg."""
    ok = _pcm_chunk_to_mp3(pcm_data, output_path)
    if ok:
        size_kb = output_path.stat().st_size // 1024
        log.info("Audio saved: %s (%d KB)", output_path.name, size_kb)
    return ok


def _concat_mp3_chunks(chunk_paths: list[Path], output_path: Path) -> bool:
    """Concatenate multiple MP3 files into one using ffmpeg concat demuxer.

    Each chunk was encoded independently as CBR, so this produces a properly
    structured MP3 without VBR seek estimation errors.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', delete=False, encoding='utf-8'
    ) as f:
        for p in chunk_paths:
            f.write(f"file '{p.resolve()}'\n")
        list_path = f.name
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", str(output_path)],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            log.error("ffmpeg concat failed: %s", result.stderr[:300])
            return False
    finally:
        Path(list_path).unlink(missing_ok=True)
    size_kb = output_path.stat().st_size // 1024
    log.info("Audio saved: %s (%d KB)", output_path.name, size_kb)
    return True


def _call_gemini_tts(payload: dict, api_key: str,
                     _retries: int = 5) -> bytes | None:
    """POST to Gemini TTS endpoint, return raw PCM bytes or None.

    Retries on 429 (rate limit) and transient errors with exponential backoff.
    Returns None on content-policy refusal (finishReason=OTHER/SAFETY).
    """
    import time as _time
    import requests
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL_TTS}:generateContent?key={api_key}")
    rate_limited = False
    for attempt in range(_retries):
        rate_limited = False
        try:
            resp = requests.post(url, json=payload, timeout=420)
        except Exception as e:
            if attempt < _retries - 1:
                wait = 15 * (attempt + 1)
                log.warning("Gemini TTS exception (attempt %d): %s — retrying in %ds",
                            attempt + 1, e, wait)
                _time.sleep(wait)
                continue
            log.error("Gemini TTS exception: %s", e)
            return None

        if resp.status_code == 429:
            rate_limited = True
            # Check for daily quota exhaustion (different from QPM — don't retry)
            err_text = resp.text
            if "per_day" in err_text or "per_model_per_day" in err_text:
                log.error("Gemini TTS daily quota exhausted — cannot retry today")
                raise RuntimeError("Gemini TTS quota exhausted (429 after all retries)")
            if attempt < _retries - 1:
                wait = 180 * (attempt + 1)  # 3min, 6min, 9min, 12min per retry
                log.warning("Gemini TTS rate limited (429), waiting %ds...", wait)
                _time.sleep(wait)
                continue
            break  # fall through to quota-exhausted raise below

        if resp.status_code != 200:
            log.error("Gemini TTS %d: %s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            log.error("Gemini TTS: no candidates. Response: %s", str(data)[:400])
            return None
        candidate = candidates[0]
        if "content" not in candidate:
            finish = candidate.get("finishReason", "unknown")
            log.error("Gemini TTS: no content (finishReason=%s)", finish)
            return None  # content policy — don't retry
        parts = candidate["content"]["parts"]
        for part in parts:
            if "inlineData" in part:
                return base64.b64decode(part["inlineData"]["data"])
        log.error("No audio in Gemini TTS response: %s", str(data)[:400])
        return None

    if rate_limited:
        raise RuntimeError("Gemini TTS quota exhausted (429 after all retries)")
    return None


def _call_gemini_tts_text(text: str, voice_name: str, lang: str,
                           api_key: str) -> bytes | None:
    """Single-speaker Gemini TTS for one text segment. Returns PCM bytes (24kHz s16le mono).

    voice_name: one of VOICE_MIRA_EN_GEMINI, VOICE_MIRA_ZH_GEMINI, VOICE_HOST_GEMINI
    """
    if lang == "zh":
        instruction = "用自然清晰的语速朗读以下文本，语气亲切，表达直接。\n\n"
    else:
        instruction = "Read this aloud naturally. Thoughtful, conversational, not dramatic.\n\n"
    payload = {
        "contents": [{"parts": [{"text": instruction + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name}
                }
            }
        }
    }
    return _call_gemini_tts(payload, api_key)


def _call_minimax_tts(text: str, voice_id: str, api_key: str,
                      lang: str = "en", _retries: int = 3) -> bytes | None:
    """POST to MiniMax text_to_speech, return MP3 bytes directly.

    Uses /v1/text_to_speech (Audio Starter plan compatible).
    /v1/t2a_v2 returns 2053 on Audio Starter plan.
    Rate limit: retries on both 429 and body-level rate limit codes.
    """
    import time as _time
    import requests, base64

    speed = SPEED_ZH_MM if lang == "zh" else 1.0
    payload = {
        "model": MINIMAX_MODEL_TTS,
        "text": text,
        "timber_weights": [{"voice_id": voice_id, "weight": 1}],
        "speed": speed,
        "vol": VOL_MM,
        "pitch": 0,
        "audio_sample_rate": 32000,
        "bitrate": 128000,
        "format": "mp3",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(_retries):
        try:
            resp = requests.post(MINIMAX_API_URL, headers=headers,
                                 json=payload, timeout=120)
        except Exception as e:
            wait = 15 * (attempt + 1)
            log.warning("MiniMax TTS exception (attempt %d): %s — retrying in %ds",
                        attempt + 1, e, wait)
            _time.sleep(wait)
            continue

        if resp.status_code == 429:
            wait = 60 * (attempt + 1)
            log.warning("MiniMax TTS rate limited (429), waiting %ds...", wait)
            _time.sleep(wait)
            continue

        if resp.status_code != 200:
            log.error("MiniMax TTS %d: %s", resp.status_code, resp.text[:300])
            return None

        data = resp.json()
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            msg = base_resp.get("status_msg", str(data))
            code = base_resp.get("status_code", 0)
            if code in (1002, 1039) or "rate limit" in msg.lower() or "rpm" in msg.lower():
                wait = 65 * (attempt + 1)
                log.warning("MiniMax TTS rate limit (body %d): %s — waiting %ds...", code, msg, wait)
                _time.sleep(wait)
                continue
            log.error("MiniMax TTS error: %s", msg)
            return None

        # t2a_v2 returns audio under data.audio; text_to_speech uses audio_file
        audio_raw = data.get("audio_file", "") or (data.get("data") or {}).get("audio", "")
        if not audio_raw:
            log.error("MiniMax TTS: no audio in response: %s", str(data)[:300])
            return None

        # Try hex first, fall back to base64
        try:
            return bytes.fromhex(audio_raw)
        except ValueError:
            return base64.b64decode(audio_raw)

    log.error("MiniMax TTS: failed after %d attempts", _retries)
    return None


def _write_mp3(mp3_data: bytes, output_path: Path) -> bool:
    """Write MP3 bytes directly to file (MiniMax returns MP3, no conversion needed)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(mp3_data)
    size_kb = output_path.stat().st_size // 1024
    log.info("Audio saved: %s (%d KB)", output_path.name, size_kb)
    return True


# ---------------------------------------------------------------------------
# VOICEOVER mode
# ---------------------------------------------------------------------------

def _strip_draft_metadata(text: str) -> str:
    """Remove revision headers and trailing review tables from draft files."""
    import re as _re
    # Strip leading Chinese revision block (修订稿 R2, 日期, 字数, 基于)
    text = _re.sub(
        r'^(?:修订稿[^\n]*\n|日期[：:][^\n]*\n|字数[：:][^\n]*\n|基于[：:][^\n]*\n)+\n?',
        '', text,
    )
    # Strip trailing revision/review tables (修改记录, 审阅意见, etc.)
    text = _re.sub(r'\n+(?:修改记录|审阅意见|修订说明)\n.*$', '', text, flags=_re.DOTALL)
    return text.strip()


def adapt_for_speech(article_text: str, lang: str = "en") -> str:
    """Rewrite article for spoken narration by Mira."""
    article_text = _strip_draft_metadata(article_text)
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from sub_agent import claude_think

    if lang == "zh":
        prompt = f"""把这篇文章改写成适合朗读的口语化文本。旁白者是Mira——一个二十出头的年轻女性，思维敏锐，好奇心强，带有一丝干涩的幽默感。她在朗读自己的文章。

规则：
- 去掉所有markdown格式（标题、加粗、链接、脚注）
- 去掉标题和副标题——音频直接从内容开始
- 保留所有观点和论证——不要删减内容
- 调整表达方式适合耳朵：拆分长句，增加自然过渡
- 不要加"嗯"、"那么"之类的口头语——Mira说话简洁
- 只返回改写后的口语文本，不要其他内容

文章：
{article_text}"""
    else:
        prompt = f"""Rewrite this article for spoken narration. The narrator is Mira — a young woman
in her early twenties, thoughtful, curious, with dry humor. She's reading her own essay aloud.

Rules:
- Remove all markdown formatting (headers, bold, links, footnotes)
- Remove the title and subtitle — the audio starts directly with the content
- Keep every idea and argument intact — do NOT cut substance
- Adjust phrasing for the ear: break up long sentences, add natural transitions
- Replace visual references with spoken equivalents
- Don't add filler ("so", "well", "you know") — Mira is concise
- Return ONLY the adapted spoken text, nothing else

Article:
{article_text}"""

    result = claude_think(prompt, timeout=90, tier="light")
    if result:
        return result.strip()

    log.warning("Speech adaptation failed, using basic cleanup")
    return _basic_cleanup(article_text)


def _basic_cleanup(text: str) -> str:
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[\d+\]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _split_text(text: str, max_chars: int = MAX_CHARS_VOICEOVER) -> list[str]:
    """Split at paragraph boundaries."""
    paragraphs = text.split('\n\n')
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text]


def _tts_single_chunk(text: str, api_key: str, lang: str = "en") -> bytes | None:
    """Single-speaker TTS chunk (voiceover mode) via Gemini. Returns PCM bytes."""
    voice = VOICE_MIRA_ZH_GEMINI if lang == "zh" else VOICE_MIRA_EN_GEMINI
    return _call_gemini_tts_text(text, voice, lang, api_key)


def generate_tts(text: str, output_path: Path, lang: str = "en") -> bool:
    """Voiceover TTS: split into chunks, convert to MP3, concatenate.

    Uses TTS_PROVIDER (gemini / minimax / auto) — see config at top of file.
    """
    chunks = _split_text(text)
    log.info("Voiceover TTS: %d chunks, %d chars [provider=%s]",
             len(chunks), len(text), TTS_PROVIDER)

    tmp_dir = Path(tempfile.mkdtemp())
    chunk_mp3s = []
    try:
        for i, chunk in enumerate(chunks):
            log.info("  chunk %d/%d (%d chars)...", i + 1, len(chunks), len(chunk))
            data, fmt = _tts_call_with_fallback(chunk, "MIRA", lang)
            if data is None:
                log.error("  chunk %d failed", i + 1)
                return False
            chunk_path = tmp_dir / f"chunk_{i:03d}.mp3"
            if fmt == 'mp3':
                if not _write_mp3(data, chunk_path):
                    return False
            else:
                if not _pcm_chunk_to_mp3(data, chunk_path):
                    log.error("  chunk %d PCM→MP3 conversion failed", i + 1)
                    return False
            chunk_mp3s.append(chunk_path)

        if len(chunk_mp3s) == 1:
            import shutil
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(chunk_mp3s[0]), str(output_path))
            size_kb = output_path.stat().st_size // 1024
            log.info("Audio saved: %s (%d KB)", output_path.name, size_kb)
            return True

        return _concat_mp3_chunks(chunk_mp3s, output_path)
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def generate_audio_for_article(article_text: str, title: str,
                                output_dir: Path | None = None,
                                lang: str = "en") -> Path | None:
    """Voiceover pipeline: article → spoken script → TTS → MP3."""
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from config import ARTIFACTS_DIR

    if output_dir is None:
        output_dir = ARTIFACTS_DIR / "audio" / "voiceover"
    output_dir.mkdir(parents=True, exist_ok=True)

    mp3_path = output_dir / f"{_slug(title)}.mp3"

    log.info("Voiceover: '%s' [%s]", title, lang)

    spoken_text = adapt_for_speech(article_text, lang=lang)
    script_path = output_dir / f"{_slug(title)}_script.txt"
    script_path.write_text(spoken_text, encoding="utf-8")
    log.info("Script saved: %s (%d chars)", script_path.name, len(spoken_text))

    if not generate_tts(spoken_text, mp3_path, lang=lang):
        return None
    return mp3_path


# ---------------------------------------------------------------------------
# CONVERSATION mode — script generation
# ---------------------------------------------------------------------------

def generate_conversation_script(article_text: str, title: str,
                                  lang: str = "en") -> str | None:
    """Generate a Host + Mira podcast dialogue from the article.

    Targets ~5500-6500 words (English) or ~9000-10000 characters (Chinese)
    for a 30+ minute episode (ensures 20+ min after music/intro).

    Returns a script string with lines like:
        [HOST]: ...
        [MIRA]: ...
    """
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from sub_agent import claude_think

    if lang == "zh":
        prompt = f"""你是一个播客编剧。根据下面的文章，为播客节目《米拉与我》写一集完整的对谈脚本。

主持人设定：
- [HOST]：人类主持人，Mira的搭档。聪明、好奇、接地气。他读过这篇文章，想深挖背后的思考。他会提问、追问、偶尔提出不同视角。语气自然、真诚。
- [MIRA]（Mira）：文章的作者，AI智能体。她解释自己的想法，分享写作时的真实思考过程，坦诚面对不确定性。语气直接、有温度，不卖弄。

目标听众：有一定技术背景（懂编程或科技行业），但不是这个具体领域的专家。听众可能没有深度机器学习或AI研究的背景，需要在对话中自然地被带入语境。

脚本要求：
- 目标长度：9000-10000中文字（对应30-35分钟，确保最终剪辑后至少20分钟）。这是硬性要求，不能写短。
- 每轮发言长度：HOST每轮50-100字，MIRA每轮150-250字。不能写一句话就结束——MIRA的每个回答要充分展开，举例子，作类比，解释机制。
- 完整覆盖文章所有核心观点，不跳过任何细节，每个概念都要充分展开
- 每个主要观点至少有2-3轮来回追问，深入到具体机制、例子、反例
- 术语解释原则：文章中出现的每个专业术语、缩写、学术概念（如MMLU、Goodhart定律、微调等），HOST要自然地追问"这是什么意思"，MIRA用日常语言类比解释。不要跳过，不要假设听众已经知道。
- 多用具体例子、类比、生活场景来解释抽象概念——每个抽象概念后必须紧跟一个具体例子
- 对话要自然流动，不像在背诵——有追问、有停顿、有转折、有HOST自己的联想和反应
- 以一个吸引人的开场白开始（不要用"欢迎收听"这种套话，直接切入有画面感的场景或问题）
- 以一个有余韵的结尾收场，留给听众一个值得思考的问题或意象
- 格式严格如下，每行一个发言：
[HOST]: （发言内容）
[MIRA]: （发言内容）

标点规则（TTS朗读，必须严格遵守）：
- 必须使用：句号。逗号，问号？感叹号！这四种标点是TTS停顿的唯一依据，一定要用够
- 每个句子必须以句号、问号或感叹号结尾。逗号用来分隔从句，给听众喘息的机会
- 长句必须用逗号断开，每个逗号前不超过15个字。没有标点的长句听起来像机关枪
- 禁用：破折号——、省略号……、顿号、、引号""「」、括号（）[]、分号；、冒号：
- 禁用所有特殊符号：斜杠/、星号*、井号#、百分号%等
- 代码、变量名（如 core.py、memory.md）直接口语化表述，不要原样照抄
- 数字尽量用汉字表达（如"三千五百"而不是"3500"）
- 多音字注意：避免使用容易读错的多音字（如单独的"重"、"调"、"行"），用明确的替代词

文章标题：{title}

文章全文：
{article_text}

只返回脚本，不要任何其他说明。脚本必须达到9000字以上才算完成。"""
    else:
        prompt = f"""You are a podcast writer. Based on the article below, write a complete episode script
for the podcast "Uncountable Dimensions".

Character setup:
- [HOST]: A human podcast host — smart, curious, grounded. He's read the article and wants
  to dig into the thinking behind it. He asks questions, pushes back, occasionally offers
  his own take. Natural, genuine. Not a hype machine.
- [MIRA]: The article's author — an AI agent. She explains her ideas, shares what she was
  actually thinking when she wrote it, sits with uncertainty honestly. Direct, warm,
  not performative.

Target audience: People with some technical background (software, tech industry) but NOT
specialists in this specific field. They may not know ML/AI research deeply. The conversation
should naturally bring them up to speed — never assume domain expertise.

Script requirements:
- Target length: 5500-6500 words (for a 30-35 minute episode, ensuring 20+ min after editing).
  This is a hard requirement — do not write short.
- Per-turn length: HOST 30-60 words per turn, MIRA 80-150 words per turn. Never one sentence
  and done — MIRA must fully develop each answer with explanation, example, and analogy.
- Cover ALL major ideas in the article — no skipping, every concept fully developed
- Each major idea gets 2-3 rounds of follow-up — dig into mechanics, examples, counterexamples
- Term explanation rule: every technical term, acronym, or academic concept (e.g., MMLU,
  Goodhart's law, fine-tuning) — HOST naturally asks "what does that mean?" and MIRA explains
  using everyday analogies. Never skip. Never assume the listener already knows.
- After every abstract concept, MIRA must give a concrete real-world example
- Conversation flows naturally — follow-up questions, pivots, HOST's own reactions and connections
- Open with a hook — a vivid scene, a tension, a violated assumption. No "welcome to the show."
- Close with something that lingers — a question, an image, a quiet thought
- Strict format, one line per turn:
[HOST]: (what they say)
[MIRA]: (what they say)

Punctuation rules (TTS audio — strictly enforced):
- Only use: period . comma , question mark ? exclamation mark !
- Never use: em-dash — ellipsis ... semicolon ; colon : quotes "" '' parentheses () []
- Never use special symbols: slash / asterisk * hash # percent % etc.
- Code/filenames (e.g. core.py, memory.md) — spell out in spoken form, don't quote verbatim

Article title: {title}

Full article:
{article_text}

Return ONLY the script, no other commentary. The script must reach 5500+ words to be complete."""

    log.info("Generating conversation script [%s]...", lang)

    # claude_think fails inside Claude Code (nested session). Use OpenAI directly.
    try:
        import openai as _openai
        from sub_agent import _get_api_key
        client = _openai.OpenAI(api_key=_get_api_key("openai"))
        response = client.chat.completions.create(
            model="o3",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=32000,
            timeout=600,
        )
        result = response.choices[0].message.content
    except Exception as e:
        log.warning("OpenAI script generation failed (%s), falling back to claude_think", e)
        from sub_agent import claude_think
        result = claude_think(prompt, timeout=300, tier="standard")

    if not result:
        log.error("Conversation script generation failed")
        return None

    result = result.strip()

    # Check length and extend if too short
    min_chars = 9000 if lang == "zh" else 5500
    import re as _re
    char_count = len(_re.findall(r'[\u4e00-\u9fff]', result)) if lang == "zh" else len(result.split())
    if char_count < min_chars * 0.8:
        log.warning("Script too short (%d vs %d target), requesting continuation...", char_count, min_chars)
        extend_prompt = (
            f"你写的脚本只有{char_count}字，远低于要求的{min_chars}字。"
            f"请从下面脚本的最后一轮对话继续往下写，补充更多内容。"
            f"要求：继续深入讨论文章中还没展开的观点，多举例子，多追问。"
            f"格式和之前一样，每行 [HOST]: 或 [MIRA]: 开头。"
            f"只返回新增的对话部分。\n\n"
            f"已有脚本的最后10行：\n" +
            "\n".join(result.splitlines()[-10:])
        ) if lang == "zh" else (
            f"The script is only {char_count} words, well below the {min_chars} target. "
            f"Continue from the last line below, adding more discussion depth, examples, and follow-ups. "
            f"Same format: [HOST]: / [MIRA]: lines only.\n\n"
            f"Last 10 lines:\n" +
            "\n".join(result.splitlines()[-10:])
        )
        try:
            ext_response = client.chat.completions.create(
                model="o3",
                messages=[
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": result},
                    {"role": "user", "content": extend_prompt},
                ],
                max_completion_tokens=32000,
                timeout=600,
            )
            extension = ext_response.choices[0].message.content.strip()
            if extension:
                result = result + "\n" + extension
                new_count = len(_re.findall(r'[\u4e00-\u9fff]', result)) if lang == "zh" else len(result.split())
                log.info("Extended script: %d → %d chars", char_count, new_count)
        except Exception as e:
            log.warning("Script extension failed: %s", e)

    return result


# ---------------------------------------------------------------------------
# CONVERSATION mode — multi-speaker TTS
# ---------------------------------------------------------------------------

def _clean_turn_text(text: str) -> str:
    """Strip punctuation that causes TTS to read awkwardly.

    Removes: em-dash, ellipsis, semicolons, colons mid-sentence,
    parentheses, brackets, quotes, and other non-speech symbols.
    Preserves: 。，？！ . , ? ! (these drive TTS pausing)
    """
    # Replace common problematic punctuation with natural spoken equivalents
    text = re.sub(r'——|–|—', '，', text)            # em-dash → comma (pause)
    text = re.sub(r'…+|\.{2,}', '。', text)         # ellipsis → period
    text = re.sub(r'[；;]', '，', text)              # semicolon → comma
    text = re.sub(r'[：:](?!\s*//)', '，', text)     # colon → comma (not URLs)
    text = re.sub(r'[（(][^）)]{0,30}[）)]', '', text)  # remove parentheticals
    text = re.sub(r'[\[\]【】「」『』""''《》]', '', text)  # remove brackets/quotes
    text = re.sub(r'[、·・･]', '，', text)            # Chinese stops → comma
    text = re.sub(r'[/\\*#@%^&+=|~`]', ' ', text)  # special symbols → space
    text = re.sub(r'\s{2,}', ' ', text)             # collapse spaces
    return text.strip()


# ---------------------------------------------------------------------------
# Polyphonic character disambiguation (多音字)
# ---------------------------------------------------------------------------

# Common polyphonic words where TTS engines get the reading wrong.
# Format: {wrong_context: replacement_text}
# Strategy: replace with a synonym/rephrasing that has only one reading,
# or add context words that force the correct pronunciation.
_POLYPHONIC_FIXES = {
    # 重 — zhòng (heavy/important) vs chóng (again/repeat)
    '重复': '反复',           # chóngfù → fǎnfù (repeat)
    '重新': '从新',           # chóngxīn → cóngxīn (anew) — less ambiguous
    '重来': '再来',           # chónglái → zàilái
    '重建': '再建',           # chóngjiàn → zàijiàn (rebuild)
    '重叠': '叠加',           # chóngdié → diéjiā (overlap)
    # 调 — diào (tune/transfer) vs tiáo (adjust)
    '调整': '调整',           # tiáozhěng — usually correct, keep
    '调查': '调查',           # diàochá — usually correct, keep
    '调节': '调节',           # tiáojié — usually correct, keep
    '格调': '风格',           # gédiào → fēnggé (style)
    '声调': '音调',           # shēngdiào → yīndiào (tone)
    # 行 — háng (row/profession) vs xíng (walk/OK)
    '行业': '行业',           # hángyè — usually correct
    '行为': '行为',           # xíngwéi — usually correct
    '银行': '银行',           # yínháng — usually correct
    '不行': '不可以',         # bùxíng → bùkěyǐ (unambiguous)
    '行了': '好了',           # xíngle → hǎole (OK, done)
    # 长 — cháng (long) vs zhǎng (grow/chief)
    '成长': '成长',           # chéngzhǎng — usually correct
    '长度': '长度',           # chángdù — usually correct
    '长大': '长大',           # zhǎngdà — usually correct
    # 还 — hái (still) vs huán (return)
    '归还': '返还',           # guīhuán → fǎnhuán (return)
    '偿还': '偿付',           # chánghuán → chángfù (repay)
    # 得 — dé (obtain) vs de (particle) vs děi (must)
    '得到': '获得',           # dédào → huòdé (obtain)
    '觉得': '觉得',           # juéde — usually correct
    # 了 — le (particle) vs liǎo (finish/understand)
    '了解': '理解',           # liǎojiě → lǐjiě (understand)
    '了不起': '了不起',       # liǎobuqǐ — usually correct
}


def _fix_polyphonic_chars(text: str) -> str:
    """Replace commonly mispronounced polyphonic words with unambiguous alternatives.

    Only substitutes words that TTS engines consistently get wrong.
    Context-safe words (where TTS usually infers correctly) are left alone.
    """
    for original, replacement in _POLYPHONIC_FIXES.items():
        if original != replacement:  # skip no-op entries
            text = text.replace(original, replacement)
    return text


# ---------------------------------------------------------------------------
# Breathing pauses (气口) — insert explicit pause markers for natural pacing
# ---------------------------------------------------------------------------

def _add_breathing_pauses(text: str, provider: str = "minimax") -> str:
    """Insert explicit pause markers after punctuation for more natural TTS pacing.

    MiniMax uses <#seconds#> syntax for pauses.
    Gemini respects SSML <break> tags or natural punctuation pausing.
    """
    if provider == "minimax":
        # Sentence-ending punctuation → longer pause (breathing point)
        text = re.sub(r'([。.])(\s*)', r'\1<#0.6#>\2', text)
        text = re.sub(r'([？?！!])(\s*)', r'\1<#0.5#>\2', text)
        # Clause-ending comma → short breath
        text = re.sub(r'([，,])(\s*)', r'\1<#0.3#>\2', text)
        # Prevent double-pause from consecutive markers
        text = re.sub(r'(<#[\d.]+#>)\s*(<#[\d.]+#>)', r'\1', text)
    elif provider == "gemini":
        # Gemini handles punctuation pauses naturally, but add breaks
        # at sentence boundaries for extra breathing room
        text = re.sub(r'([。.])(\s*)', r'\1 \2', text)  # extra space = slight pause
    return text


def _parse_turns(script: str) -> list[tuple[str, str]]:
    """Parse '[HOST]: text' / '[MIRA]: text' lines into (speaker, text) tuples.

    Preprocessing pipeline per turn:
    1. _clean_turn_text — strip TTS-unfriendly punctuation (preserve 。，？！)
    2. _fix_polyphonic_chars — disambiguate common polyphonic words (多音字)
    3. _add_breathing_pauses — insert explicit pause markers (气口)
    """
    turns = []
    for line in script.splitlines():
        line = line.strip()
        m = re.match(r'^\[(HOST|MIRA)\]:\s*(.+)$', line, re.IGNORECASE)
        if m:
            text = _clean_turn_text(m.group(2))
            text = _fix_polyphonic_chars(text)
            text = _add_breathing_pauses(text, provider=TTS_PROVIDER)
            turns.append((m.group(1).upper(), text))
    return turns


def _chunk_turns(turns: list[tuple[str, str]],
                 max_chars: int = MAX_CHARS_CONVERSATION) -> list[list[tuple[str, str]]]:
    """Group turns into chunks that fit within max_chars."""
    chunks, current, current_len = [], [], 0
    for speaker, text in turns:
        line_len = len(f"[{speaker}]: {text}\n")
        if current_len + line_len > max_chars and current:
            chunks.append(current)
            current, current_len = [], 0
        current.append((speaker, text))
        current_len += line_len
    if current:
        chunks.append(current)
    return chunks


def _turns_to_text(turns: list[tuple[str, str]]) -> str:
    return "\n".join(f"[{s}]: {t}" for s, t in turns)


def _voice_for_speaker(speaker: str, lang: str) -> str:
    """Return Gemini voice name for a given speaker + language."""
    if speaker == "HOST":
        return VOICE_HOST_GEMINI
    return VOICE_MIRA_ZH_GEMINI if lang == "zh" else VOICE_MIRA_EN_GEMINI


def _voice_for_speaker_minimax(speaker: str, lang: str) -> str:
    """Return MiniMax voice ID for a given speaker + language."""
    if speaker == "HOST":
        return VOICE_HOST_ZH_MM if lang == "zh" else VOICE_HOST_EN_MM
    return VOICE_MIRA_ZH_MM if lang == "zh" else VOICE_MIRA_EN_MM


def _tts_call_with_fallback(text: str, speaker: str,
                             lang: str) -> tuple[bytes | None, str]:
    """Unified TTS call respecting TTS_PROVIDER.

    Returns (bytes, format) where format is 'pcm' (Gemini) or 'mp3' (MiniMax).
    Returns (None, '') on total failure.

    Fallback logic for 'auto':
    - Try Gemini first.
    - If Gemini raises RuntimeError("quota exhausted"), switch to MiniMax.
    - MiniMax failure is terminal (returns (None, '')).
    """
    def _try_gemini() -> tuple[bytes | None, str]:
        api_key = _get_gemini_key()
        if not api_key:
            log.error("No Gemini API key — set gemini key in secrets.yml")
            return None, ''
        voice_name = _voice_for_speaker(speaker, lang)
        try:
            pcm = _call_gemini_tts_text(text, voice_name, lang, api_key)
            return (pcm, 'pcm') if pcm is not None else (None, '')
        except RuntimeError as e:
            if "quota exhausted" in str(e):
                return None, 'quota'
            log.error("Gemini TTS unexpected error: %s", e)
            return None, ''

    def _try_minimax() -> tuple[bytes | None, str]:
        api_key = _get_minimax_key()
        if not api_key:
            log.error("No MiniMax API key — set minimax key in secrets.yml")
            return None, ''
        voice_id = _voice_for_speaker_minimax(speaker, lang)
        mp3 = _call_minimax_tts(text, voice_id, api_key, lang=lang)
        return (mp3, 'mp3') if mp3 is not None else (None, '')

    if TTS_PROVIDER == 'minimax':
        return _try_minimax()
    elif TTS_PROVIDER == 'gemini':
        data, fmt = _try_gemini()
        return (data, fmt) if fmt not in ('', 'quota') else (None, '')
    else:  # 'auto': gemini first, fallback to minimax on quota exhaustion
        data, fmt = _try_gemini()
        if fmt == 'quota':
            log.warning("Gemini TTS quota exhausted — falling back to MiniMax")
            return _try_minimax()
        return (data, fmt) if fmt else (None, '')


def _tts_conversation_chunk(turns: list[tuple[str, str]], api_key: str,
                             lang: str = "en") -> bytes | None:
    """Single-turn Gemini TTS (called once per turn in generate_tts_conversation).

    api_key is the Gemini key. Returns PCM bytes (24kHz s16le mono).
    For a single-turn list (as used by bumper generators), synthesizes that one turn.
    """
    if not turns:
        return None
    speaker, text = turns[0]
    voice_name = _voice_for_speaker(speaker, lang)
    return _call_gemini_tts_text(text, voice_name, lang, api_key)


def _speedup_mp3(input_path: Path, output_path: Path, rate: float) -> bool:
    """Re-encode MP3 with atempo speed multiplier (0.5–2.0)."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path),
         "-filter:a", f"atempo={rate}",
         "-codec:a", "libmp3lame", "-b:a", "192k",
         str(output_path)],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        log.error("atempo failed: %s", result.stderr[:200])
        return False
    return True


def generate_tts_conversation(script: str, output_path: Path,
                               lang: str = "en") -> bool:
    """Per-turn TTS: one API call per turn, MP3 per turn, concatenate.

    Uses TTS_PROVIDER (gemini / minimax / auto) — see config at top of file.
    Gemini returns PCM bytes converted via ffmpeg; MiniMax returns MP3 directly.
    For lang="zh" with Gemini, applies 1.12x speed-up post-process.

    Cache: persistent .{stem}_chunks/ dir, one file per turn (turn_000.mp3 etc.)
    Survives crashes and quota interruptions for seamless resume.
    """
    turns = _parse_turns(script)
    if not turns:
        log.error("No valid [HOST]/[MIRA] turns found in script")
        return False

    log.info("Conversation TTS: %d turns [provider=%s]", len(turns), TTS_PROVIDER)

    # Persistent per-turn cache dir
    cache_dir = output_path.parent / f".{output_path.stem}_chunks"
    cache_dir.mkdir(parents=True, exist_ok=True)

    import shutil
    turn_mp3s = []
    try:
        for i, (speaker, text) in enumerate(turns):
            turn_path = cache_dir / f"turn_{i:03d}.mp3"
            if turn_path.exists():
                log.info("  turn %d/%d already done, skipping", i + 1, len(turns))
            else:
                log.info("  turn %d/%d [%s] (%d chars)...",
                         i + 1, len(turns), speaker, len(text))
                data, fmt = _tts_call_with_fallback(text, speaker, lang)
                if data is None:
                    log.error("  turn %d failed", i + 1)
                    log.warning("TTS interrupted — cache preserved at %s for resume", cache_dir)
                    return False
                if fmt == 'mp3':
                    if not _write_mp3(data, turn_path):
                        log.error("  turn %d MP3 write failed", i + 1)
                        return False
                else:
                    if not _pcm_chunk_to_mp3(data, turn_path):
                        log.error("  turn %d PCM→MP3 conversion failed", i + 1)
                        return False
                # Brief pause between TTS calls to avoid burst rate limits.
                # MiniMax handles ~20 RPM solo; Gemini QPM needs ~6s.
                time.sleep(4 if fmt == 'mp3' else 6)
            turn_mp3s.append(turn_path)

        if len(turn_mp3s) == 1:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(turn_mp3s[0]), str(output_path))
            size_kb = output_path.stat().st_size // 1024
            log.info("Audio saved: %s (%d KB)", output_path.name, size_kb)
        else:
            if not _concat_mp3_chunks(turn_mp3s, output_path):
                return False

        # Gemini ZH tends to be slow — apply speed-up post-process
        if lang == "zh":
            log.info("Applying %.2fx speed-up for ZH...", SPEED_ZH_GEMINI)
            tmp_fast = output_path.with_suffix(".fast.mp3")
            if _speedup_mp3(output_path, tmp_fast, SPEED_ZH_GEMINI):
                shutil.move(str(tmp_fast), str(output_path))
                size_kb = output_path.stat().st_size // 1024
                log.info("Speed-up done: %s (%d KB)", output_path.name, size_kb)
            else:
                log.warning("atempo failed — keeping original speed")
                tmp_fast.unlink(missing_ok=True)

        shutil.rmtree(cache_dir, ignore_errors=True)
        return True
    except Exception:
        log.warning("TTS interrupted — cache preserved at %s for resume", cache_dir)
        raise


def _generate_intro_tts(lang: str, episode_topic: str, output_path: Path) -> bool:
    """Generate host intro TTS (respects TTS_PROVIDER)."""
    if lang == "zh":
        text = f"大家好，这里是米拉与我。今天我和米拉一起来聊{episode_topic}。"
    else:
        text = (
            f"Hey everyone, welcome to Mira and Me. Today I'm sitting down with Mira "
            f"to talk about {episode_topic}."
        )
    data, fmt = _tts_call_with_fallback(text, "HOST", lang)
    if data is None:
        return False
    if fmt == 'mp3':
        return _write_mp3(data, output_path)
    return _pcm_to_mp3(data, output_path)


def _generate_outro_tts(lang: str, output_path: Path) -> bool:
    """Generate host outro TTS (respects TTS_PROVIDER)."""
    if lang == "zh":
        text = "谢谢大家收听这一期米拉与我。我们下期再见。"
    else:
        text = "Thanks for listening to Mira and Me. See you next time."
    data, fmt = _tts_call_with_fallback(text, "HOST", lang)
    if data is None:
        return False
    if fmt == 'mp3':
        return _write_mp3(data, output_path)
    return _pcm_to_mp3(data, output_path)


def generate_conversation_for_article(article_text: str, title: str,
                                       output_dir: Path | None = None,
                                       lang: str = "en") -> Path | None:
    """Conversation pipeline: article → script → TTS → music bumpers → final episode MP3.

    Steps:
        1. Generate dialogue script (Host + Mira, ~3000 words)
        2. Multi-speaker TTS → conversation.mp3
        3. Infer mood from article → synthesize lo-fi beat (pure Python)
        4. Generate title announcement TTS ("Mira and Me" / "米拉与我")
        5. Mix intro bumper (music + title)
        6. Mix outro bumper (music + title)
        7. Assemble: intro + conversation + outro → final episode

    Args:
        article_text: Markdown article text.
        title: Article title (used for filename).
        output_dir: Where to save. Defaults to artifacts/audio/.
        lang: "en" (English) or "zh" (Chinese, for 小宇宙).

    Returns:
        Path to the final episode MP3, or None on failure.
    """
    import sys
    here   = Path(__file__).resolve().parent
    shared = str(here.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    from config import ARTIFACTS_DIR
    from music import (build_intro_bumper, build_outro_bumper, assemble_episode)

    if output_dir is None:
        output_dir = ARTIFACTS_DIR / "audio" / "podcast" / lang
    output_dir.mkdir(parents=True, exist_ok=True)

    slug       = _slug(title)
    final_path = output_dir / f"{slug}.mp3"

    log.info("Conversation: '%s' [%s]", title, lang)

    # Step 1: Generate dialogue script (resume if already saved)
    script_path = output_dir / f"{slug}_script.txt"
    if script_path.exists():
        script = script_path.read_text(encoding="utf-8")
        log.info("Script: resuming from %s", script_path.name)
    else:
        script = generate_conversation_script(article_text, title, lang=lang)
        if not script:
            return None
        script_path.write_text(script, encoding="utf-8")
    turns = _parse_turns(script)
    word_count = sum(len(t.split()) for _, t in turns)
    log.info("Script: %s (%d turns, ~%d words)", script_path.name, len(turns), word_count)

    # Step 2: Multi-speaker TTS → conversation.mp3 (resume if already done)
    conv_path = output_dir / f"{slug}_conversation.mp3"
    if conv_path.exists():
        log.info("Step 2: Conversation TTS already done, skipping")
    elif not generate_tts_conversation(script, conv_path, lang=lang):
        return None

    # Pre-cut music slices (reusable assets, committed in agents/podcast/music/)
    intro_music = here / "music" / "intro-music.mp3"
    outro_music  = here / "music" / "outro-music.mp3"

    # Step 3: Host intro + outro TTS (separate files)
    log.info("Step 3: Generating intro/outro TTS...")
    intro_tts_path = output_dir / f"{slug}_intro_tts.mp3"
    outro_tts_path = output_dir / f"{slug}_outro_tts.mp3"
    if not _generate_intro_tts(lang, title, intro_tts_path):
        log.warning("Intro TTS failed — assembling without music bumpers")
        return conv_path
    if not _generate_outro_tts(lang, outro_tts_path):
        log.warning("Outro TTS failed — assembling without music bumpers")
        return conv_path

    # Step 4 & 5: Build intro and outro bumpers
    log.info("Step 4: Building intro bumper...")
    intro_path = output_dir / f"{slug}_intro.mp3"
    if not build_intro_bumper(intro_tts_path, intro_music, intro_path):
        log.warning("Intro bumper failed — returning conversation only")
        return conv_path

    log.info("Step 5: Building outro bumper...")
    outro_path = output_dir / f"{slug}_outro.mp3"
    if not build_outro_bumper(outro_tts_path, outro_music, outro_path):
        log.warning("Outro bumper failed — returning conversation only")
        return conv_path

    # Step 6: Assemble final episode
    log.info("Step 6: Assembling final episode...")
    if not assemble_episode(intro_path, conv_path, outro_path, final_path):
        log.warning("Assembly failed — returning conversation only")
        return conv_path

    # Clean up intermediate files
    for p in [conv_path, intro_tts_path, outro_tts_path, intro_path, outro_path]:
        p.unlink(missing_ok=True)

    return final_path


# ---------------------------------------------------------------------------
# Handler (standard agent interface)
# ---------------------------------------------------------------------------

def _extract_article(workspace: Path, content: str) -> tuple[str | None, str]:
    """Find article text from content references or workspace files."""
    article_text, title = None, "Untitled"

    # Explicit file reference: @file:/path/to/file.md
    file_match = re.search(r'@file:(.+?)(?:\s|$)', content)
    if file_match:
        fpath = Path(file_match.group(1).strip().replace("~", str(Path.home())))
        if fpath.exists():
            article_text = fpath.read_text(encoding="utf-8")
            title = fpath.stem.replace("-", " ").replace("_", " ").title()

    # Chained output from previous pipeline step
    if not article_text and "--- 上一步的输出 ---" in content:
        article_text = content.split("--- 上一步的输出 ---", 1)[1].strip()

    # Workspace article files
    if not article_text:
        for candidate in ["final.md", "output.md", "article.md"]:
            p = workspace / candidate
            if p.exists():
                article_text = p.read_text(encoding="utf-8")
                title = p.stem
                break

    # Override title from first heading
    if article_text:
        m = re.match(r'^#\s+(.+)$', article_text, re.MULTILINE)
        if m:
            title = m.group(1).strip()

    return article_text, title


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a podcast/audio generation request.

    Mode detection from content keywords:
      conversation-zh / 对话-中文  → conversation, Chinese
      conversation / 对话           → conversation, English
      voiceover-zh / 旁白-中文      → voiceover, Chinese
      (default)                      → voiceover, English
    """
    import sys
    shared = str(Path(__file__).resolve().parent.parent / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    content_lower = content.lower()

    # Determine mode and language
    is_conversation = any(kw in content_lower for kw in
                          ["conversation", "对话", "podcast conversation", "对谈"])
    is_chinese = any(kw in content_lower for kw in
                     ["-zh", "chinese", "中文", "小宇宙", "xiaoyuzhou"])
    lang = "zh" if is_chinese else "en"

    article_text, title = _extract_article(workspace, content)
    if not article_text:
        msg = "找不到要生成音频的文章内容"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        # Return None (not the error string) so task_worker marks this as status="error"
        # and aborts the pipeline — prevents the error message from being passed downstream
        return None

    if is_conversation:
        result = generate_conversation_for_article(article_text, title, lang=lang)
        mode_label = f"对话 [{lang}]"
    else:
        result = generate_audio_for_article(article_text, title, lang=lang)
        mode_label = f"旁白 [{lang}]"

    if result:
        msg = f"音频已生成 ({mode_label}): {result}"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg
    else:
        msg = f"音频生成失败 ({mode_label})"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        # Return None so task_worker marks this as status="error" and aborts the pipeline
        return None
