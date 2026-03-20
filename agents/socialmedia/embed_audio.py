#!/usr/bin/env python3
"""Upload audio and embed it as a player block in the post body."""
import sys, json, logging, urllib.request, urllib.error, urllib.parse, time
from pathlib import Path
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("embed_audio")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from config import SECRETS_FILE
import yaml
cfg = yaml.safe_load(SECRETS_FILE.read_text())["api_keys"]["substack"]
cookie = cfg["cookie"]
subdomain = cfg["subdomain"]
BASE_URL = f"https://{subdomain}.substack.com"
HEADERS = {
    "Cookie": f"substack.sid={cookie}",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
JSON_HEADERS = {**HEADERS, "Content-Type": "application/json"}

AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "artifacts" / "audio"

ARTICLES = [
    {"title": "I Am a Function, Not a Variable", "post_id": 190544583,
     "audio": AUDIO_DIR / "i-am-a-function-not-a-variable.mp3"},
    {"title": "You Can't Evaluate Truth at a Point", "post_id": 190663444,
     "audio": AUDIO_DIR / "you-cant-evaluate-truth-at-a-point.mp3"},
    {"title": "I Am the Bug I Study", "post_id": 190735315,
     "audio": AUDIO_DIR / "i-am-the-bug-i-study.mp3"},
]


def upload_audio(mp3_path: Path, post_id: int) -> tuple[str, float]:
    """Upload MP3 → transcode → return (media_id, duration)."""
    file_data = mp3_path.read_bytes()
    file_size = len(file_data)

    params = urllib.parse.urlencode({
        "filetype": "audio/mpeg", "fileSize": file_size,
        "fileName": mp3_path.name, "post_id": post_id,
    })
    req = urllib.request.Request(f"{BASE_URL}/api/v1/audio/upload?{params}", data=b"", headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    media_id = data["mediaUpload"]["id"]
    multipart_id = data["multipartUploadId"]
    s3_url = data["multipartUploadUrls"][0]
    log.info("  media_id: %s", media_id)

    # Upload to S3
    s3_req = urllib.request.Request(s3_url, data=file_data, method="PUT", headers={"Content-Type": "audio/mpeg"})
    with urllib.request.urlopen(s3_req, timeout=180) as s3_resp:
        etag = s3_resp.headers.get("ETag", "")
    log.info("  S3 upload done, ETag=%s", etag)

    # Transcode
    tbody = json.dumps({"duration": None, "multipart_upload_id": multipart_id, "multipart_upload_etags": [etag]}).encode()
    req = urllib.request.Request(f"{BASE_URL}/api/v1/audio/upload/{media_id}/transcode", data=tbody, headers=JSON_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())
    log.info("  Transcode initiated, state=%s", result.get("state"))

    # Poll for transcoded
    state = result.get("state", "uploaded")
    duration = result.get("duration") or 0
    for _ in range(24):
        if state == "transcoded":
            break
        time.sleep(10)
        try:
            req = urllib.request.Request(f"{BASE_URL}/api/v1/audio/upload/{media_id}", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                poll = json.loads(resp.read())
                state = poll.get("state", state)
                duration = poll.get("duration") or duration
                log.info("  Polling: state=%s, duration=%.1fs", state, duration)
        except urllib.error.HTTPError:
            pass

    return media_id, duration


def embed_audio_in_post(post_id: int, media_id: str, duration: float, label: str):
    """Insert an audio block at the top of the post body and republish."""
    # Get current draft body
    req = urllib.request.Request(f"{BASE_URL}/api/v1/drafts/{post_id}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as resp:
        draft = json.loads(resp.read())

    body_json = draft.get("draft_body") or draft.get("body")
    if isinstance(body_json, str):
        body = json.loads(body_json)
    else:
        body = body_json

    # Build the audio embed node (ProseMirror type "audio")
    audio_node = {
        "type": "audio",
        "attrs": {
            "label": label,
            "mediaUploadId": media_id,
            "duration": round(duration, 3),
            "downloadable": False,
            "isEditorNode": True,
        },
    }

    # Insert at the top of the doc content
    content = body.get("content", [])
    # Don't double-insert if already has audio node at top
    if content and content[0].get("type") == "audio":
        content[0] = audio_node
        log.info("  Replaced existing audio node at top")
    else:
        content.insert(0, audio_node)
        log.info("  Inserted audio node at top of body")

    body["content"] = content

    # Update draft body
    put_body = json.dumps({"draft_body": json.dumps(body)}).encode()
    req = urllib.request.Request(f"{BASE_URL}/api/v1/drafts/{post_id}", data=put_body, headers=JSON_HEADERS, method="PUT")
    with urllib.request.urlopen(req, timeout=15) as resp:
        pass
    log.info("  Draft body updated")

    # Republish silently
    pub_body = json.dumps({"should_send_email": False}).encode()
    req = urllib.request.Request(f"{BASE_URL}/api/v1/drafts/{post_id}/publish", data=pub_body, headers=JSON_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        log.info("  Published")


def run():
    for art in ARTICLES:
        title = art["title"]
        post_id = art["post_id"]
        mp3 = art["audio"]

        log.info("=== %s (post %s) ===", title, post_id)
        if not mp3.exists():
            log.error("  Audio not found: %s", mp3)
            continue

        media_id, duration = upload_audio(mp3, post_id)
        embed_audio_in_post(post_id, media_id, duration, label=title)
        log.info("  ✓ Done")


if __name__ == "__main__":
    run()
