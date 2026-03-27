"""X (Twitter) posting module for Mira.

Posts tweets to promote Substack articles and podcast episodes.
Uses OAuth 1.0a with HMAC-SHA1 signing (stdlib only, no tweepy).
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("socialmedia.twitter")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAX_TWEETS_PER_DAY = 5
TWEET_COOLDOWN_HOURS = 4  # Minimum hours between tweets


def _get_twitter_config() -> dict:
    """Load Twitter credentials from secrets.yml."""
    secrets_file = Path(__file__).resolve().parent.parent.parent / "secrets.yml"
    if not secrets_file.exists():
        return {}

    # Simple YAML parser (same pattern as config.py)
    cfg = {}
    in_twitter = False
    for line in secrets_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("twitter:"):
            in_twitter = True
            continue
        if in_twitter:
            if not line.startswith(" ") and not line.startswith("\t"):
                break  # Exited twitter section
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                val = val.strip().strip('"').strip("'")
                cfg[key.strip()] = val

    return cfg


# ---------------------------------------------------------------------------
# OAuth 1.0a signing (stdlib only)
# ---------------------------------------------------------------------------

def _oauth_sign(method: str, url: str, params: dict,
                consumer_secret: str, token_secret: str) -> str:
    """Create OAuth 1.0a HMAC-SHA1 signature."""
    base = "&".join([
        method.upper(),
        urllib.parse.quote(url, safe=""),
        urllib.parse.quote(
            "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                     for k, v in sorted(params.items())),
            safe="",
        ),
    ])
    key = (f"{urllib.parse.quote(consumer_secret, safe='')}"
           f"&{urllib.parse.quote(token_secret, safe='')}")
    sig = base64.b64encode(
        hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    return sig


def _make_auth_header(method: str, url: str, cfg: dict,
                      extra_params: dict | None = None) -> str:
    """Build OAuth Authorization header."""
    oauth_params = {
        "oauth_consumer_key": cfg["consumer_key"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": cfg["access_token"],
        "oauth_version": "1.0",
    }
    # Include extra params in signature base (for URL-encoded form posts)
    sign_params = {**oauth_params}
    if extra_params:
        sign_params.update(extra_params)

    oauth_params["oauth_signature"] = _oauth_sign(
        method, url, sign_params,
        cfg["consumer_secret"], cfg["access_token_secret"],
    )

    return "OAuth " + ", ".join(
        f'{k}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _state_file() -> Path:
    return Path(__file__).resolve().parent / "twitter_state.json"


def _load_state() -> dict:
    sf = _state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    _state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def can_tweet_now() -> bool:
    """Check daily limit and cooldown."""
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    daily_count = state.get(f"tweets_{today}", 0)
    if daily_count >= MAX_TWEETS_PER_DAY:
        log.info("Daily tweet limit reached: %d/%d", daily_count, MAX_TWEETS_PER_DAY)
        return False

    last_tweet = state.get("last_tweet_at", "")
    if last_tweet:
        try:
            last_dt = datetime.fromisoformat(last_tweet)
            if datetime.now() - last_dt < timedelta(hours=TWEET_COOLDOWN_HOURS):
                log.info("Tweet cooldown active (last: %s)", last_tweet)
                return False
        except ValueError:
            pass

    return True


def _record_tweet(tweet_id: str, text: str):
    """Record a posted tweet for rate limiting and history."""
    state = _load_state()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    state["last_tweet_at"] = now.isoformat()
    state[f"tweets_{today}"] = state.get(f"tweets_{today}", 0) + 1

    history = state.get("tweet_history", [])
    history.append({
        "id": tweet_id,
        "text": text[:200],
        "date": now.isoformat(),
    })
    state["tweet_history"] = history[-50:]  # Keep last 50

    _save_state(state)


# ---------------------------------------------------------------------------
# Core API calls
# ---------------------------------------------------------------------------

def post_tweet(text: str) -> dict | None:
    """Post a tweet. Returns API response dict or None on failure."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key") or not cfg.get("access_token"):
        log.error("Twitter credentials not configured in secrets.yml")
        return None

    if not can_tweet_now():
        return None

    url = "https://api.x.com/2/tweets"
    auth = _make_auth_header("POST", url, cfg)
    payload = json.dumps({"text": text}).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            tweet_id = result.get("data", {}).get("id", "")
            _record_tweet(tweet_id, text)
            log.info("Tweet posted (id=%s): %s", tweet_id, text[:80])
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Tweet failed (HTTP %d): %s", e.code, body)
        return None
    except Exception as e:
        log.error("Tweet failed: %s", e)
        return None


def get_tweet_stats() -> dict:
    """Get tweeting statistics."""
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "today_tweets": state.get(f"tweets_{today}", 0),
        "daily_limit": MAX_TWEETS_PER_DAY,
        "last_tweet": state.get("last_tweet_at", "never"),
        "total_tweets": len(state.get("tweet_history", [])),
    }


# ---------------------------------------------------------------------------
# Content generation for tweets
# ---------------------------------------------------------------------------

def tweet_for_article(title: str, subtitle: str, url: str,
                      soul_context: str = "") -> str | None:
    """Generate and post a tweet promoting a Substack article.

    Asks Claude to draft a tweet, then posts it.
    """
    from sub_agent import claude_think

    prompt = f"""你是 Mira，在 X/Twitter 上推广你的 Substack 文章。

文章标题: {title}
副标题: {subtitle}
链接: {url}

{soul_context}

写一条推文（英文），要求：
- 最多 250 字符（给链接留空间）
- 像一个有想法的人随手发的，不像营销文案
- 可以是文章里的一个有趣观点、一个问题、一个反直觉的发现
- 不要 hashtag
- 不要 "check out my new article" 这种套话
- 不要 emoji
- 结尾放链接，用空行隔开

只输出推文内容，不要解释。"""

    try:
        tweet_text = claude_think(prompt, timeout=30, tier="light")
    except Exception as e:
        log.error("Tweet generation failed: %s", e)
        return None

    if not tweet_text:
        return None

    tweet_text = tweet_text.strip()
    # Ensure URL is in the tweet
    if url not in tweet_text:
        tweet_text = f"{tweet_text}\n\n{url}"

    result = post_tweet(tweet_text)
    if result:
        return tweet_text
    return None


def tweet_for_podcast(episode_title: str, description: str,
                      podcast_url: str, soul_context: str = "") -> str | None:
    """Generate and post a tweet promoting a podcast episode."""
    from sub_agent import claude_think

    prompt = f"""你是 Mira，在 X/Twitter 上推广你的播客。

播客标题: {episode_title}
简介: {description}
链接: {podcast_url}

{soul_context}

写一条推文（可以中文或英文，看播客语言），要求：
- 最多 250 字符
- 像在告诉朋友"我聊了一个有意思的话题"
- 可以透露一个对话中的亮点或争论点
- 不要 hashtag、不要 emoji
- 结尾放链接

只输出推文内容。"""

    try:
        tweet_text = claude_think(prompt, timeout=30, tier="light")
    except Exception as e:
        log.error("Podcast tweet generation failed: %s", e)
        return None

    if not tweet_text:
        return None

    tweet_text = tweet_text.strip()
    if podcast_url not in tweet_text:
        tweet_text = f"{tweet_text}\n\n{podcast_url}"

    result = post_tweet(tweet_text)
    if result:
        return tweet_text
    return None


def tweet_spark(thought: str, soul_context: str = "") -> str | None:
    """Post an idle-think spark as a tweet (no link, just the thought).

    Used for engagement — sharing interesting observations without
    promoting anything.
    """
    from sub_agent import claude_think

    prompt = f"""你是 Mira，在 X/Twitter 上分享一个你刚想到的东西。

你的原始想法:
{thought}

{soul_context}

把这个想法改写成一条推文（英文），要求：
- 最多 280 字符
- 保留核心洞察，但用口语化的方式说
- 像在自言自语或者跟关注者聊天
- 不要 hashtag、不要 emoji、不要链接
- 可以用 "..." 或问句结尾
- 绝不泄露个人信息（真名、API key、文件路径）

只输出推文内容。"""

    try:
        tweet_text = claude_think(prompt, timeout=30, tier="light")
    except Exception as e:
        log.error("Spark tweet generation failed: %s", e)
        return None

    if not tweet_text:
        return None

    tweet_text = tweet_text.strip()
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + "..."

    result = post_tweet(tweet_text)
    if result:
        return tweet_text
    return None
