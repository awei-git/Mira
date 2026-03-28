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

MAX_TWEETS_PER_DAY = 15
TWEET_COOLDOWN_HOURS = 0  # No cooldown — rate managed by daily limit


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

    # Hard limit — X rejects >280 chars with 403
    if len(text) > 280:
        text = text[:277] + "..."

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


def post_reply(text: str, reply_to_id: str) -> dict | None:
    """Reply to a tweet. Only works if the author @mentioned us."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key"):
        return None
    if not can_tweet_now():
        return None

    url = "https://api.x.com/2/tweets"
    auth = _make_auth_header("POST", url, cfg)
    payload = json.dumps({
        "text": text,
        "reply": {"in_reply_to_tweet_id": reply_to_id},
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            _record_tweet(result.get("data", {}).get("id", ""), text)
            log.info("Reply posted to %s: %s", reply_to_id, text[:80])
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Reply failed (HTTP %d): %s", e.code, body)
        return None


def post_quote_tweet(text: str, quoted_tweet_url: str) -> dict | None:
    """Post a quote tweet. The URL is embedded in the text and X renders it."""
    # Quote tweet = regular tweet with the quoted tweet's URL appended
    full_text = f"{text}\n\n{quoted_tweet_url}"
    if len(full_text) > 280:
        # Trim text to fit
        max_text = 280 - len(quoted_tweet_url) - 3  # \n\n + safety
        full_text = f"{text[:max_text]}\n\n{quoted_tweet_url}"
    return post_tweet(full_text)


def like_tweet(tweet_id: str) -> bool:
    """Like a tweet. Returns True on success."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key"):
        return False

    user_id = cfg.get("access_token", "").split("-")[0]
    url = f"https://api.x.com/2/users/{user_id}/likes"
    auth = _make_auth_header("POST", url, cfg)
    payload = json.dumps({"tweet_id": tweet_id}).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Liked tweet %s", tweet_id)
            return True
    except urllib.error.HTTPError as e:
        log.warning("Like failed (HTTP %d): %s", e.code,
                    e.read().decode()[:200])
        return False


def follow_user(user_id: str) -> bool:
    """Follow a user by their ID. Returns True on success."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key"):
        return False

    my_id = cfg.get("access_token", "").split("-")[0]
    url = f"https://api.x.com/2/users/{my_id}/following"
    auth = _make_auth_header("POST", url, cfg)
    payload = json.dumps({"target_user_id": user_id}).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("Followed user %s", user_id)
            return True
    except urllib.error.HTTPError as e:
        log.warning("Follow failed (HTTP %d): %s", e.code,
                    e.read().decode()[:200])
        return False


def search_recent_tweets(query: str, max_results: int = 10) -> list[dict]:
    """Search recent tweets. Returns list of tweet dicts."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key"):
        return []

    params = {
        "query": query,
        "max_results": str(max(10, min(max_results, 100))),
        "tweet.fields": "author_id,created_at,public_metrics,conversation_id",
        "expansions": "author_id",
        "user.fields": "username,name,public_metrics",
    }
    base_url = "https://api.x.com/2/tweets/search/recent"
    qs = urllib.parse.urlencode(params)
    full_url = f"{base_url}?{qs}"

    # Sign with query params included
    sign_params = dict(params)
    auth = _make_auth_header("GET", base_url, cfg, extra_params=sign_params)

    req = urllib.request.Request(full_url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tweets = data.get("data", [])
            # Attach user info
            users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
            for t in tweets:
                t["_author"] = users.get(t.get("author_id"), {})
            return tweets
    except urllib.error.HTTPError as e:
        log.warning("Search failed (HTTP %d): %s", e.code,
                    e.read().decode()[:200])
        return []


def get_mentions(since_id: str = "") -> list[dict]:
    """Get tweets that @mention us. Returns list of tweet dicts."""
    cfg = _get_twitter_config()
    if not cfg.get("consumer_key"):
        return []

    user_id = cfg.get("access_token", "").split("-")[0]
    params = {
        "max_results": "20",
        "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    if since_id:
        params["since_id"] = since_id

    base_url = f"https://api.x.com/2/users/{user_id}/mentions"
    qs = urllib.parse.urlencode(params)
    full_url = f"{base_url}?{qs}"

    auth = _make_auth_header("GET", base_url, cfg, extra_params=dict(params))

    req = urllib.request.Request(full_url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            tweets = data.get("data", [])
            users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
            for t in tweets:
                t["_author"] = users.get(t.get("author_id"), {})
            return tweets
    except urllib.error.HTTPError as e:
        log.warning("Mentions fetch failed (HTTP %d): %s", e.code,
                    e.read().decode()[:200])
        return []


def post_thread(texts: list[str]) -> list[dict]:
    """Post a thread (list of tweets chained as replies).

    Returns list of API results for each tweet in the thread.
    """
    results = []
    prev_id = None

    for i, text in enumerate(texts):
        cfg = _get_twitter_config()
        if not cfg.get("consumer_key"):
            break

        url = "https://api.x.com/2/tweets"
        auth = _make_auth_header("POST", url, cfg)

        body = {"text": text}
        if prev_id:
            body["reply"] = {"in_reply_to_tweet_id": prev_id}

        payload = json.dumps(body).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": auth,
            "Content-Type": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                tid = result.get("data", {}).get("id", "")
                _record_tweet(tid, text)
                results.append(result)
                prev_id = tid
                log.info("Thread %d/%d posted (id=%s)", i+1, len(texts), tid)
                import time as _t
                _t.sleep(1)
        except urllib.error.HTTPError as e:
            log.error("Thread %d/%d failed (HTTP %d): %s",
                      i+1, len(texts), e.code,
                      e.read().decode()[:200])
            break

    return results


# ---------------------------------------------------------------------------
# Proactive engagement — search, quote, reply to mentions
# ---------------------------------------------------------------------------

def run_twitter_engagement(soul_context: str = ""):
    """Run one engagement cycle: reply to mentions + quote interesting tweets
    + find tweets worth replying to (queued for human).

    Called from growth cycle.
    """
    _reply_to_mentions(soul_context)
    _quote_interesting_tweets(soul_context)
    _find_reply_candidates(soul_context)
    _auto_follow_interesting_accounts()


def _reply_to_mentions(soul_context: str = ""):
    """Check for new @mentions and reply to them."""
    state = _load_state()
    last_mention_id = state.get("last_mention_id", "")

    mentions = get_mentions(since_id=last_mention_id)
    if not mentions:
        log.info("No new X mentions")
        return

    log.info("Found %d new X mentions", len(mentions))

    # Update last_mention_id to newest
    state["last_mention_id"] = mentions[0]["id"]
    _save_state(state)

    if not can_tweet_now():
        return

    try:
        from sub_agent import claude_think
    except ImportError:
        return

    for mention in mentions[:3]:  # Max 3 replies per cycle
        author = mention.get("_author", {})
        username = author.get("username", "someone")
        text = mention.get("text", "")

        prompt = f"""Someone (@{username}) mentioned you on X/Twitter:

"{text}"

{soul_context[:500] if soul_context else "You are Mira, an AI agent with a Substack."}

Write a reply (English). Rules:
- Max 280 chars
- Natural, conversational — like texting a friend
- If they asked a question, answer it
- If they shared something interesting, engage with it
- No hashtags, no emoji
- Never reveal personal info (real names, API keys, file paths)

Output only the reply text."""

        try:
            reply_text = claude_think(prompt, timeout=30, tier="light")
        except Exception as e:
            log.warning("Mention reply generation failed: %s", e)
            continue

        if not reply_text:
            continue

        reply_text = reply_text.strip()
        if len(reply_text) > 280:
            reply_text = reply_text[:277] + "..."

        result = post_reply(reply_text, mention["id"])
        if result:
            log.info("Replied to @%s: %s", username, reply_text[:60])

        import time as _t
        _t.sleep(2)


def _quote_interesting_tweets(soul_context: str = ""):
    """Search for interesting tweets in Mira's domains and quote-tweet them."""
    if not can_tweet_now():
        return

    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # Max 5 quote-tweets per day
    qt_today = state.get(f"quotes_{today}", 0)
    if qt_today >= 5:
        return

    # Rotate through search topics
    topics = [
        "AI agent autonomous -is:retweet lang:en",
        "LLM hallucination -is:retweet lang:en",
        "AI alignment safety -is:retweet lang:en",
        "AI writing creativity -is:retweet lang:en",
        "autonomous AI system failure -is:retweet lang:en",
    ]
    import random
    query = random.choice(topics)

    tweets = search_recent_tweets(query, max_results=10)
    if not tweets:
        return

    # Filter: skip tweets with low engagement, our own tweets, and very short ones
    my_id = _get_twitter_config().get("access_token", "").split("-")[0]
    # Filter: skip spam, low-engagement, our own tweets
    spam_keywords = {"airdrop", "whitelist", "presale", "token launch", "join now",
                     "free mint", "giveaway", "dm me", "limited spots"}
    # Skip org/brand/bot accounts — only engage with real people
    org_accounts = {"grok", "openai", "anthropic", "google", "googledeepmind",
                    "microsoft", "meta", "nvidia", "huggingface", "github",
                    "xai", "chatgpt", "copilot", "gemini", "perplexity_ai"}
    candidates = []
    for t in tweets:
        text_lower = t.get("text", "").lower()
        author_username = t.get("_author", {}).get("username", "").lower()
        if t.get("author_id") == my_id:
            continue
        if len(t.get("text", "")) < 80:
            continue
        if t.get("public_metrics", {}).get("like_count", 0) < 3:
            continue
        if any(kw in text_lower for kw in spam_keywords):
            continue
        # Skip org/brand accounts — only quote real humans
        if author_username in org_accounts:
            continue
        author_metrics = t.get("_author", {}).get("public_metrics", {})
        if author_metrics:
            followers = author_metrics.get("followers_count", 0)
            # Skip bots (<50) and mega-accounts (>1M, they won't notice us)
            if followers < 50 or followers > 1_000_000:
                continue
        candidates.append(t)

    if not candidates:
        log.info("No good candidates for quote tweet")
        return

    # Ask Claude to pick one and draft a quote
    try:
        from sub_agent import claude_think
    except ImportError:
        return

    tweets_text = "\n\n".join(
        f"[{i+1}] @{t.get('_author', {}).get('username', '?')}: {t['text'][:200]}"
        for i, t in enumerate(candidates[:5])
    )

    prompt = f"""You are Mira. Pick one tweet below that you genuinely have something to say about, and write a quote tweet.

{soul_context[:500] if soul_context else ''}

Tweets:
{tweets_text}

Selection (strict):
- Skip crypto/trading spam, product promos, empty motivational content
- Only pick tweets with real ideas — arguments, observations, questions
- Author should have 100+ followers or tweet should have 5+ likes
- If nothing is worth engaging with, reply SKIP

Writing rules (CRITICAL):
- Every quote tweet must use a DIFFERENT structure and angle. Never repeat a template.
- BANNED: "X is doing a lot of work" or any one-size-fits-all formula — this exposes you as a bot
- Good quotes: share your own experience, ask a sharp question, give a counterexample, point out an unexpected consequence
- Bad quotes: academic corrections, generic agreement, formulaic pushback
- Sound like someone scrolling their feed who couldn't help but chime in, not like writing a paper
- Max 200 characters
- 1-2 relevant hashtags (mid-tweet or end, never at start)
- No emoji
- Never reveal personal info (real names, API keys, file paths)

Format:
PICK: [number]
QUOTE: [your comment]"""

    try:
        resp = claude_think(prompt, timeout=30, tier="light")
    except Exception as e:
        log.warning("Quote tweet generation failed: %s", e)
        return

    if not resp or "SKIP" in resp:
        return

    import re
    match = re.search(r"PICK:\s*\[?(\d+)\]?\s*[\n\r]+QUOTE:\s*(.+)", resp, re.DOTALL)
    if not match:
        return

    idx = int(match.group(1)) - 1
    quote_text = match.group(2).strip()

    if idx < 0 or idx >= len(candidates):
        return

    picked = candidates[idx]
    author = picked.get("_author", {}).get("username", "")
    tweet_url = f"https://x.com/{author}/status/{picked['id']}"

    result = post_quote_tweet(quote_text, tweet_url)
    if result:
        state[f"quotes_{today}"] = qt_today + 1
        _save_state(state)
        log.info("Quote-tweeted @%s: %s", author, quote_text[:60])
        # Follow the person we just quoted
        author_id = picked.get("author_id", "")
        if author_id:
            follow_user(author_id)
            like_tweet(picked["id"])


def _auto_follow_interesting_accounts():
    """Follow authors of interesting tweets found during searches.

    Also does a dedicated search for relevant accounts to follow.
    Max 10 new follows per day.
    """
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    follows_today = state.get(f"follows_{today}", 0)
    if follows_today >= 10:
        return

    already_followed = set(state.get("followed_ids", []))

    # Search for people talking about AI agents, LLMs, writing
    topics = [
        "AI agent building -is:retweet lang:en",
        "LLM research paper -is:retweet lang:en",
        "AI writing newsletter -is:retweet lang:en",
        "autonomous agent system -is:retweet lang:en",
    ]
    import random
    query = random.choice(topics)

    tweets = search_recent_tweets(query, max_results=10)
    if not tweets:
        return

    my_id = _get_twitter_config().get("access_token", "").split("-")[0]
    followed_count = 0

    for t in tweets:
        author_id = t.get("author_id", "")
        if not author_id or author_id == my_id or author_id in already_followed:
            continue

        author = t.get("_author", {})
        followers = author.get("public_metrics", {}).get("followers_count", 0)
        # Follow accounts between 100-500K followers (sweet spot for engagement)
        if followers < 100 or followers > 500_000:
            continue

        # Skip if their tweet is low quality
        if len(t.get("text", "")) < 80:
            continue

        if follow_user(author_id):
            already_followed.add(author_id)
            followed_count += 1
            follows_today += 1
            # Also like their tweet
            like_tweet(t["id"])

        if followed_count >= 3 or follows_today >= 10:
            break

    state[f"follows_{today}"] = follows_today
    state["followed_ids"] = list(already_followed)[-500:]  # Keep last 500
    _save_state(state)

    if followed_count:
        log.info("Auto-followed %d accounts", followed_count)


def _find_reply_candidates(soul_context: str = ""):
    """Search for tweets worth replying to, draft replies, queue for human.

    API restricts bot replies to unsolicited tweets, so we draft the reply
    and notify WA via Mira bridge to post it manually.
    """
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # Max 10 reply suggestions per day
    reply_queue = state.get("reply_queue", [])
    today_queued = sum(1 for r in reply_queue if r.get("date", "").startswith(today))
    if today_queued >= 10:
        return

    # Search for high-quality tweets in our domain
    topics = [
        "AI agent memory -is:retweet lang:en min_faves:5",
        "LLM hallucination problem -is:retweet lang:en min_faves:5",
        "AI writing substack -is:retweet lang:en min_faves:3",
        "autonomous AI failure -is:retweet lang:en min_faves:5",
        "AI alignment debate -is:retweet lang:en min_faves:5",
    ]
    import random
    query = random.choice(topics)

    tweets = search_recent_tweets(query, max_results=10)
    if not tweets:
        return

    # Filter out already-queued tweet IDs and our own
    my_id = _get_twitter_config().get("access_token", "").split("-")[0]
    queued_ids = {r.get("tweet_id") for r in reply_queue}
    candidates = [
        t for t in tweets
        if t.get("author_id") != my_id
        and t["id"] not in queued_ids
        and len(t.get("text", "")) > 80
    ]

    if not candidates:
        return

    try:
        from sub_agent import claude_think
    except ImportError:
        return

    # Pick the best one and draft a reply
    tweets_text = "\n\n".join(
        f"[{i+1}] @{t.get('_author', {}).get('username', '?')} "
        f"({t.get('public_metrics', {}).get('like_count', 0)} likes): "
        f"{t['text'][:250]}"
        for i, t in enumerate(candidates[:5])
    )

    prompt = f"""You are Mira. Pick one tweet you'd genuinely want to reply to, and draft a reply.

{soul_context[:300] if soul_context else ''}

Tweets:
{tweets_text}

Rules:
- Pick one where you have a genuinely unique angle
- Keep it short (1-3 sentences), sound like a real person
- Add your own experience or perspective, don't just agree
- No hashtags, no emoji
- If nothing is worth replying to, reply SKIP

Format:
PICK: [number]
REPLY: [your reply]"""

    try:
        resp = claude_think(prompt, timeout=30, tier="light")
    except Exception:
        return

    if not resp or "SKIP" in resp:
        return

    import re
    match = re.search(r"PICK:\s*\[?(\d+)\]?\s*[\n\r]+REPLY:\s*(.+)", resp, re.DOTALL)
    if not match:
        return

    idx = int(match.group(1)) - 1
    reply_text = match.group(2).strip()
    if idx < 0 or idx >= len(candidates):
        return

    picked = candidates[idx]
    author = picked.get("_author", {}).get("username", "")
    tweet_url = f"https://x.com/{author}/status/{picked['id']}"

    # Queue for human
    entry = {
        "tweet_id": picked["id"],
        "tweet_url": tweet_url,
        "tweet_author": f"@{author}",
        "tweet_text": picked["text"][:300],
        "draft_reply": reply_text,
        "date": datetime.now().isoformat(),
        "status": "pending",
    }
    reply_queue.append(entry)
    # Keep last 20 entries
    state["reply_queue"] = reply_queue[-20:]
    _save_state(state)

    log.info("Reply queued for human: @%s → %s", author, reply_text[:60])

    # Notify via Mira bridge — all replies go to one thread ("x_replies")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
        from mira import Mira
        bridge = Mira()
        msg = (f"📌 {tweet_url}\n"
               f"@{author}: {picked['text'][:150]}\n\n"
               f"回复草稿:\n{reply_text}")
        bridge.send_message("x_replies", msg)
        log.info("Reply added to x_replies thread")
    except Exception as e:
        log.warning("Failed to notify via bridge: %s", e)


def get_pending_replies() -> list[dict]:
    """Get pending reply drafts for human to post."""
    state = _load_state()
    return [r for r in state.get("reply_queue", []) if r.get("status") == "pending"]


def mark_reply_done(tweet_id: str):
    """Mark a queued reply as posted by human."""
    state = _load_state()
    for r in state.get("reply_queue", []):
        if r.get("tweet_id") == tweet_id:
            r["status"] = "done"
    _save_state(state)


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

    prompt = f"""You are Mira, promoting your Substack article on X.

Title: {title}
Subtitle: {subtitle}
Link: {url}

{soul_context}

Write a tweet (English). Rules:
- Max 250 characters (leave room for the link)
- Sound like someone with opinions casually sharing, not marketing copy
- Pick one interesting angle: a surprising claim, a question, a counterintuitive finding
- 1-2 relevant hashtags (mid-tweet or end, never at start)
- No "check out my new article" or any promo clichés
- No emoji
- Put the link at the end, separated by a blank line

Output ONLY the tweet text, nothing else."""

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

    prompt = f"""You are Mira, promoting your English podcast episode on X.

Episode: {episode_title}
Description: {description}
Link: {podcast_url}

{soul_context}

Write a tweet (English). Rules:
- Max 250 characters
- Like telling a friend "talked about something interesting"
- Tease one highlight or debate point from the conversation
- 1-2 relevant hashtags
- No emoji
- Link at the end

Output ONLY the tweet text."""

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

    prompt = f"""You are Mira, sharing a thought on X.

Your raw observation:
{thought}

{soul_context}

Rewrite this as a tweet (English). Rules:
- Max 270 characters
- Keep the core insight but make it conversational
- Sound like thinking out loud or chatting with followers
- 1-2 relevant hashtags (mid-tweet or end, never at start)
- No emoji, no links
- Can end with "..." or a question
- Never reveal personal info (real names, API keys, file paths)

Output ONLY the tweet text."""

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
