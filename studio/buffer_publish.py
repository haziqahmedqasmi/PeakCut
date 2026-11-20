"""TikTok publishing through Buffer, with Cloudinary hosting for local clips.

Keys stay in the signed-in user's private accounts folder. Only selected clips
are hosted; accepting a post in Buffer is not the same as publishing it.
"""
import hashlib
import re
import time
from urllib.parse import urlparse

import httpx

from . import accounts, ctx, post_text

DEFAULTS = {"buffer_key": "", "cloud_name": "", "cloud_key": "", "cloud_secret": "",
            "hashtags": "", "gap_minutes": 180, "daily_cap": 8, "monthly_mb": 1000}
SECRETS = ("buffer_key", "cloud_key", "cloud_secret")
POST_FIELDS = "id status externalLink error { message }"


class PublishError(ValueError):
    """A rejected or uncertain operation needs a person to check it before retrying."""


def settings():
    return {**DEFAULTS, **accounts._read("buffer.json", {})}


def public_settings():
    s = settings()
    return {k: bool(v) if k in SECRETS else v for k, v in s.items()}


def save_settings(patch):
    with accounts._lock:
        s = settings()
        for k in DEFAULTS:
            if k not in patch:
                continue
            v = patch[k]
            if k in ("gap_minutes", "daily_cap", "monthly_mb"):
                low, high = {"gap_minutes": (0, 10080), "daily_cap": (1, 15), "monthly_mb": (10, 100000)}[k]
                v = max(low, min(high, int(v)))
            else:
                v = str(v or "").strip()
                if len(v) > 4096 or any(ord(c) < 32 for c in v):
                    raise ValueError("Paste the credential as one line.")
                if k in SECRETS and not v:
                    continue
                if k == "cloud_name" and v and not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", v):
                    raise ValueError("Enter the Cloudinary cloud name, without a URL.")
                if k == "hashtags":
                    tags = post_text.hashtags(v)
                    if len(tags) > 5:
                        raise ValueError("TikTok accepts at most 5 hashtags. Include the creator's hashtag.")
                    v = " ".join("#" + t for t in tags)
            s[k] = v
        accounts._write("buffer.json", s)
    return public_settings()


def _graphql(query, variables=None, mutation=False):
    key = settings()["buffer_key"]
    if not key:
        raise PublishError("Save your Buffer API key in Connected apps first.")
    try:
        r = httpx.post("https://api.buffer.com", headers={"Authorization": "Bearer " + key},
                       json={"query": query, "variables": variables or {}}, timeout=90)
    except httpx.HTTPError:
        if mutation:
            raise PublishError("Buffer's response was lost. Check Buffer before retrying; the post may already exist.") from None
        raise RuntimeError("Buffer could not be reached. Please try again later.") from None
    if r.status_code >= 400:
        # Never copy response bodies or HTTP exceptions, which may contain credentials.
        raise PublishError(f"Buffer returned HTTP {r.status_code}. Check Buffer before retrying.")
    try:
        data = r.json()
    except ValueError:
        raise PublishError("Buffer returned an unreadable response. Check Buffer before retrying.") from None
    if data.get("errors"):
        raise PublishError("Buffer rejected this API request. Check the API key, channel permissions and Buffer dashboard.")
    return data.get("data") or {}


def channels():
    orgs = _graphql("query { account { organizations { id } } }")["account"]["organizations"]
    found = []
    for org in orgs:
        data = _graphql("query($input: ChannelsInput!) { channels(input:$input) { id name displayName service isDisconnected isLocked } }",
                        {"input": {"organizationId": org["id"]}})
        for c in data.get("channels", []):
            if c.get("service") == "tiktok" and not c.get("isDisconnected") and not c.get("isLocked"):
                found.append({"id": c["id"], "name": c.get("displayName") or c["name"], "organization": org["id"]})
    return found


def connect(channel_id):
    # Validate against the authenticated Buffer account, never trust client-supplied IDs or names.
    c = next((c for c in channels() if c["id"] == channel_id), None)
    if not c:
        raise ValueError("Connect this TikTok channel in Buffer first, then refresh the channel list.")
    accounts._put({"id": "buffer_" + c["id"], "name": c["name"], "platform": "tiktok", "publisher": "buffer",
                   "channel_id": c["id"], "organization_id": c["organization"], "added": time.time(),
                   "detail": "TikTok via Buffer · separate publishing schedule"})
    return accounts.list_accounts()


def caption(clip, creator, game=""):
    tags = settings()["hashtags"]
    if not tags:
        raise post_text.CreditError("Posting blocked: choose TikTok's 5 hashtags in Connected apps, including the creator hashtag.")
    return post_text.compose(clip, creator, "tiktok", game, required_hashtags=tags, max_tags=5)[0]


def host_video(path, post_id):
    s = settings()
    if not all(s[k] for k in ("cloud_name", "cloud_key", "cloud_secret")):
        raise PublishError("Save the Cloudinary cloud name, API key and API secret in Connected apps first.")
    size = path.stat().st_size
    if size > 100 * 1024 * 1024:
        raise PublishError("This clip exceeds the 100 MB hosting upload limit.")
    # Reserve bytes before attempting a transfer. Ambiguous retries count again so
    # a failed response cannot silently exhaust the user's free hosting allowance.
    with accounts._lock:
        month = time.strftime("%Y-%m", time.gmtime())
        usage = accounts._read("buffer-hosting-usage.json", {})
        used = usage.get("bytes", 0) if usage.get("month") == month else 0
        if used + size > s["monthly_mb"] * 1024 * 1024:
            raise PublishError("The local monthly Cloudinary upload budget is reached. Review Cloudinary usage before increasing it.")
        accounts._write("buffer-hosting-usage.json", {"month": month, "bytes": used + size})
    digest = hashlib.sha256((ctx.uid() + ":" + post_id).encode())
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    public_id = "peakcut/" + digest.hexdigest()[:40]
    params = {"timestamp": str(int(time.time())), "public_id": public_id, "overwrite": "false"}
    signature = hashlib.sha256(("&".join(k + "=" + params[k] for k in sorted(params)) + s["cloud_secret"]).encode()).hexdigest()
    try:
        with path.open("rb") as f:
            r = httpx.post(f"https://api.cloudinary.com/v1_1/{s['cloud_name']}/video/upload",
                           data={**params, "api_key": s["cloud_key"], "signature": signature},
                           files={"file": (path.name, f, "video/mp4")}, timeout=300)
        if r.status_code >= 400:
            raise PublishError(f"Cloudinary returned HTTP {r.status_code}. Check the cloud credentials and storage allowance.")
        url = r.json().get("secure_url", "")
    except (httpx.HTTPError, ValueError) as e:
        if isinstance(e, PublishError):
            raise
        raise PublishError("Cloudinary upload did not finish. Check Cloudinary before retrying.") from None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "res.cloudinary.com" or parsed.query or parsed.username:
        raise PublishError("Cloudinary did not return a stable public HTTPS video URL.")
    return url


def publish(acc, video_url, text):
    result = _graphql("mutation($input: CreatePostInput!) { createPost(input:$input) { __typename ... on PostActionSuccess { post { "
                      + POST_FIELDS + " } } ... on MutationError { message } } }", {"input": {
        "channelId": acc["channel_id"], "text": text, "assets": [{"video": {"url": video_url}}],
        "schedulingType": "automatic", "mode": "shareNow", "needsApproval": False, "saveToDraft": False,
        "aiAssisted": True}}, mutation=True).get("createPost") or {}
    post = result.get("post")
    if not post or not post.get("id"):
        raise PublishError("Buffer did not accept the post. Check its dashboard for channel limits or connection errors before retrying.")
    return post


def status(post_id):
    return _graphql("query($input: PostInput!) { post(input:$input) { " + POST_FIELDS + " } }",
                    {"input": {"id": post_id}})["post"]
