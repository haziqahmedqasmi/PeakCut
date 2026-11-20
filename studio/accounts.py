"""Each user's connected social accounts and personal settings (alerts, posting pause).

Stored under users/<user>/accounts. People connect YouTube, Instagram and TikTok with one click: they sign in on the
platform's own page and come back. For that, whoever runs the server registers one app with each platform once
(its id and secret), in .env or in the dashboard (Settings, Connected apps, Set up). Those belong to the server,
never to users."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import config, ctx

_lock = threading.RLock()
_states: dict = {}   # one-time values for the connect redirects: state -> (user id, when, platform)

SECRET_KEYS = ("discord_webhook", "telegram_token")
SETTING_DEFAULTS = {"discord_webhook": "", "telegram_token": "", "telegram_chat": "", "paused": False,
                    "digest_hour": 8, "last_digest": "", "tour_done": False}

YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
META_GRAPH = "https://graph.facebook.com/v21.0"
META_AUTH = "https://www.facebook.com/v21.0/dialog/oauth"
LEGACY_IG = "https://graph.instagram.com"
IG_UPLOAD = "https://rupload.facebook.com/ig-api-upload/v21.0"
IG_SCOPES = "instagram_basic,instagram_content_publish,instagram_manage_insights"
# The older "Instagram Login" sign-in (its own Instagram app ID and secret). It stays available next to Meta's
# Unified Login, so an app that already uses it keeps working.
IG_LOGIN_SCOPES = "instagram_business_basic,instagram_business_content_publish"
TT = "https://open.tiktokapis.com/v2"
TT_SCOPES = "user.info.basic,video.publish"
# "instagram_login" is only a second way to connect: the accounts it adds are ordinary "instagram" accounts.
PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "instagram_login": "Instagram (Instagram Login)",
                  "tiktok": "TikTok"}


def _dir() -> Path:
    d = config.user_dir(ctx.uid()) / "accounts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(name, default):
    f = _dir() / name
    if not f.exists():
        return default
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return default


def _write(name, data):
    with _lock:
        d = _dir()
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        try:
            tmp.chmod(0o600)                 # these hold posting tokens: readable by the app only
        except OSError:
            pass
        tmp.replace(d / name)
        if name == "settings.json":
            _settings_mem[ctx.uid()] = {**SETTING_DEFAULTS, **data}
    ctx.bump()


# ------------------------------------------------------------------ the server's apps with each platform
# name -> (what the platform calls the id, what it calls the secret, .env names for both)
APPS = {"google": ("Client ID", "Client secret", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        "instagram": ("Meta App ID", "Meta App Secret", "PEAKCUT_IG_APP_ID", "PEAKCUT_IG_APP_SECRET"),
        "instagram_login": ("Instagram app ID", "Instagram app secret", "PEAKCUT_IG_LOGIN_APP_ID", "PEAKCUT_IG_LOGIN_APP_SECRET"),
        "tiktok": ("Client key", "Client secret", "PEAKCUT_TIKTOK_CLIENT_KEY", "PEAKCUT_TIKTOK_CLIENT_SECRET")}
APP_FOR = {"youtube": "google", "instagram": "instagram", "instagram_login": "instagram_login", "tiktok": "tiktok"}
_apps_seen = {"at": None, "data": {}}


def _apps_file() -> Path:
    return config.DATA_DIR / "apps.json"


def _apps_saved() -> dict:
    f = _apps_file()
    try:
        at = f.stat().st_mtime_ns
    except OSError:
        at = 0
    if _apps_seen["at"] != at:
        try:
            data = json.loads(f.read_text(encoding="utf-8")) if at else {}
        except (OSError, ValueError):
            data = {}
        _apps_seen.update(at=at, data=data if isinstance(data, dict) else {})
    return _apps_seen["data"]


def app_keys(name: str) -> tuple:
    """(id, secret) of the server's app with this platform: what the dashboard saved, or else .env."""
    saved = _apps_saved().get(name) or {}
    if saved.get("id") and saved.get("secret"):
        return saved["id"], saved["secret"]
    if name == "google":
        return config.google_client()
    if name == "instagram":
        cid = (config.env("PEAKCUT_IG_APP_ID") or config.env("PEAKCUT_META_APP_ID") or "").strip()
        secret = (config.env("PEAKCUT_IG_APP_SECRET") or config.env("PEAKCUT_META_APP_SECRET") or "").strip()
        if cid and secret:
            return cid, secret
    _l1, _l2, id_env, secret_env = APPS[name]
    return (config.env(id_env) or "").strip(), (config.env(secret_env) or "").strip()


def app_config_id(name: str = "instagram") -> str:
    """Optional Meta Login configuration ID for Unified Login / Facebook Login for Business."""
    saved = _apps_saved().get(name) or {}
    if saved.get("config_id"):
        return str(saved["config_id"]).strip()
    return (config.env("PEAKCUT_IG_CONFIG_ID") or config.env("PEAKCUT_META_CONFIG_ID") or "").strip()


def can_connect(platform: str) -> bool:
    return all(app_keys(APP_FOR[platform]))


def redirect_for(platform: str) -> str:
    # both Instagram sign-ins come back to the same address, so one address is registered with Meta for either
    platform = "instagram" if platform == "instagram_login" else platform
    return f"{config.PUBLIC_URL}/oauth/{platform}/callback"


def apps_public() -> dict:
    """For the setup panel: whether each app is set, never its secret."""
    out = {}
    for platform, name in APP_FOR.items():
        cid, secret = app_keys(name)
        saved = _apps_saved().get(name) or {}
        entry = {"ready": bool(cid and secret), "id_label": APPS[name][0], "secret_label": APPS[name][1],
                 "id_hint": cid[:6] + "…" if len(cid) > 10 else cid, "redirect": redirect_for(platform),
                 "https": config.PUBLIC_URL.startswith("https://"),
                 "from_dashboard": bool(saved.get("id"))}
        if platform == "instagram":
            cfg = app_config_id("instagram")
            entry["config_id_label"] = "Login configuration ID"
            entry["config_id_hint"] = cfg[:6] + "…" if len(cfg) > 10 else cfg
            entry["config_id"] = cfg
        out[platform] = entry
    return out


def save_app(platform: str, cid: str, secret: str, config_id: str = "") -> dict:
    """Save the server's app for a platform, from the dashboard. An empty secret keeps the saved one."""
    if platform not in APP_FOR:
        raise ValueError("Unknown platform.")
    name = APP_FOR[platform]
    cid, secret = str(cid or "").strip()[:200], str(secret or "").strip()[:300]
    config_id = str(config_id or "").strip()[:100] if platform == "instagram" else ""   # only Unified Login has one
    old = _apps_saved().get(name) or {}
    if not cid and not secret:
        raise ValueError(f"Paste the {APPS[name][0]} and {APPS[name][1]}.")
    cid = cid or old.get("id", "")                # only a new secret typed: keep the saved id
    if not re.fullmatch(r"[\w.\-]{4,200}", cid):
        raise ValueError(f"Paste the {APPS[name][0]} exactly as the platform shows it.")
    secret = secret or (old.get("secret") if old.get("id") == cid else "")
    if not secret:
        raise ValueError(f"Paste the {APPS[name][1]} too.")
    if config_id and not re.fullmatch(r"[\w.\-]{4,100}", config_id):
        raise ValueError("Paste the Login configuration ID exactly as Meta shows it.")
    if not config_id and old.get("id") == cid:
        config_id = old.get("config_id", "")
    with _lock:
        data = dict(_apps_saved())
        item = {"id": cid, "secret": secret}
        if config_id:
            item["config_id"] = config_id
        data[name] = item
        tmp = _apps_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(_apps_file())
        _apps_seen["at"] = None
    return apps_public()


def _new_state(platform: str) -> str:
    state = secrets.token_urlsafe(16)
    for k in [k for k, v in list(_states.items()) if time.time() - v[1] > 900]:
        _states.pop(k, None)
    _states[state] = (ctx.uid(), time.time(), platform)
    return state


def _take_state(state: str, platform):
    """Use up a one-time sign-in value. `platform` is one name, or several that may finish at this address.
    Returns the name the sign-in was really started for."""
    allowed = (platform,) if isinstance(platform, str) else tuple(platform)
    got = _states.pop(state or "", None)
    if not got or got[0] != ctx.uid() or got[2] not in allowed or time.time() - got[1] > 900:
        raise ValueError("This sign-in has expired. Press Connect again.")
    return got[2]


def connect_url(platform: str) -> str:
    """Where the Connect button sends the browser: the platform's own sign-in page."""
    if platform not in APP_FOR:
        raise ValueError("Unknown platform.")
    if not can_connect(platform):
        raise ValueError(f"Connecting {PLATFORM_NAMES[platform]} isn't set up on this server yet.")
    cid, _ = app_keys(APP_FOR[platform])
    state, redirect = _new_state(platform), redirect_for(platform)
    if platform == "youtube":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": YT_SCOPES,
            "access_type": "offline", "prompt": "consent", "state": state})
    if platform == "instagram":
        cfg = app_config_id("instagram")
        p = {"client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": state}
        if cfg:
            p["config_id"] = cfg
        else:
            p["scope"] = IG_SCOPES
        return f"{META_AUTH}?" + urlencode(p)
    if platform == "instagram_login":
        return "https://www.instagram.com/oauth/authorize?" + urlencode({
            "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": IG_LOGIN_SCOPES, "state": state})
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key": cid, "redirect_uri": redirect, "response_type": "code", "scope": TT_SCOPES, "state": state})


def connect_finish(platform: str, code: str, state: str) -> dict:
    # Unified Login and Instagram Login share one return address: the saved state says which one was started
    platform = _take_state(state, ("instagram", "instagram_login") if platform == "instagram" else platform)
    code = (code or "").split("#")[0]           # Instagram adds "#_" to the code
    if not code:
        raise ValueError("The platform did not send a sign-in code. Try again.")
    return {"youtube": _youtube_finish, "instagram": _instagram_finish, "instagram_login": _instagram_login_finish,
            "tiktok": _tiktok_finish}[platform](code)


# ------------------------------------------------------------------ links the platforms can fetch a clip from
# Instagram takes a video by address, not as an upload. Each address works for one clip, for one hour, and is
# signed, so it cannot be guessed or changed to reach anything else.
def _link_key() -> bytes:
    f = config.DATA_DIR / "link.key"
    if not f.exists():
        with _lock:
            if not f.exists():
                f.write_bytes(secrets.token_bytes(32))
                try:
                    f.chmod(0o600)
                except OSError:
                    pass
    return f.read_bytes()


def clip_link(job_id: str, file_name: str, seconds: int = 3600) -> str:
    body = json.dumps([ctx.uid(), job_id, file_name, int(time.time() + seconds)], separators=(",", ":")).encode()
    b = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(hmac.new(_link_key(), b.encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
    return f"{config.PUBLIC_URL}/pub/{b}.{sig}.mp4"


def read_clip_link(token: str):
    """(user id, job id, file name) for a valid, unexpired link, else None."""
    try:
        b, sig = token.split(".")
        want = base64.urlsafe_b64encode(hmac.new(_link_key(), b.encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
        if not hmac.compare_digest(sig, want):
            return None
        uid, job_id, name, until = json.loads(base64.urlsafe_b64decode(b + "=" * (-len(b) % 4)))
        if time.time() > until or not re.fullmatch(r"[\w\-]+\.mp4", name):
            return None
        return uid, job_id, name
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ settings
_settings_mem: dict = {}


def settings() -> dict:
    u = ctx.uid()
    if u not in _settings_mem:
        _settings_mem[u] = {**SETTING_DEFAULTS, **_read("settings.json", {})}
    return dict(_settings_mem[u])


def save_settings(patch: dict) -> dict:
    with _lock:
        s = settings()
        for k, v in patch.items():
            if k not in SETTING_DEFAULTS:
                continue
            if k in ("paused", "tour_done"):
                v = bool(v)
            elif k == "digest_hour":
                v = max(0, min(23, int(v)))
            else:
                v = str(v).strip()[:300]
                if k == "discord_webhook" and v and not v.startswith("https://discord.com/api/webhooks/"):
                    raise ValueError("That doesn't look like a Discord webhook link.")
                if k in SECRET_KEYS and v == "":
                    continue  # blank means "keep what is saved"
            s[k] = v
        _write("settings.json", s)
        return s


def public_settings() -> dict:
    s = settings()
    return {k: (bool(v) if k in SECRET_KEYS else v) for k, v in s.items()}


# ------------------------------------------------------------------ accounts
def _all() -> list:
    return [a for a in _read("accounts.json", []) if a.get("platform") in PLATFORM_NAMES]


def get(aid):
    return next((a for a in _all() if a["id"] == aid), None)


def list_accounts() -> list:
    return [{"id": a["id"], "platform": a["platform"], "label": PLATFORM_NAMES[a["platform"]], "name": a["name"],
             "added": a["added"], "detail": a.get("detail", "")} for a in _all()]


def _put(acc):
    with _lock:
        rest = [a for a in _all() if a["id"] != acc["id"]]
        _write("accounts.json", rest + [acc])


def remove(aid):
    with _lock:
        _write("accounts.json", [a for a in _all() if a["id"] != aid])


def _check(r, what):
    if r.status_code >= 400:
        msg = ""
        try:
            d = r.json()
            if isinstance(d, dict):
                err = d.get("error")
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("error_user_msg") or ""
                elif isinstance(err, str):
                    msg = err
                elif "error_message" in d:
                    msg = d["error_message"]
        except Exception:
            pass
        detail = f": {msg}" if msg else f": {r.text[:300]}"
        raise RuntimeError(f"{what} failed ({r.status_code}){detail}")
    return r


# ------------------------------------------------------------------ YouTube
def youtube_ready() -> bool:
    return can_connect("youtube")


def _youtube_finish(code: str) -> dict:
    cid, secret = app_keys("google")
    tok = _check(httpx.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_for("youtube"), "grant_type": "authorization_code"}, timeout=30), "Google sign-in").json()
    if "refresh_token" not in tok:
        raise ValueError("Google did not return a long-lived sign-in. Remove PeakCut's access in your Google account and connect again.")
    ch = _check(httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30), "Reading the channel").json()
    if not ch.get("items"):
        raise ValueError("That Google account has no YouTube channel. Create one on YouTube, then connect again.")
    item = ch["items"][0]
    acc = {"id": f"yt_{item['id']}", "platform": "youtube", "name": item["snippet"]["title"], "added": time.time(),
           "refresh_token": tok["refresh_token"], "access_token": tok["access_token"],
           "expires": time.time() + tok.get("expires_in", 3600) - 60}
    _put(acc)
    return acc


def _yt_token(acc) -> str:
    if acc.get("expires", 0) > time.time():
        return acc["access_token"]
    cid, secret = app_keys("google")
    tok = _check(httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": secret,
        "refresh_token": acc["refresh_token"], "grant_type": "refresh_token"}, timeout=30),
        "Refreshing the YouTube sign-in").json()
    acc.update(access_token=tok["access_token"], expires=time.time() + tok.get("expires_in", 3600) - 60)
    _put(acc)
    return acc["access_token"]


def youtube_upload(acc, path: Path, title: str, description: str, tags: list, privacy: str):
    token = _yt_token(acc)
    meta = {"snippet": {"title": re.sub(r"[<>]", "", title)[:100] or "Clip", "description": re.sub(r"[<>]", "", description)[:4900],
                        "tags": tags[:15], "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    size = path.stat().st_size
    start = _check(httpx.post("https://www.googleapis.com/upload/youtube/v3/videos",
                              params={"uploadType": "resumable", "part": "snippet,status"}, json=meta,
                              headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                                       "X-Upload-Content-Length": str(size)}, timeout=60), "Starting the YouTube upload")
    with path.open("rb") as f:
        done = _check(httpx.put(start.headers["Location"], content=f.read(),
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"}, timeout=900),
                      "Uploading to YouTube").json()
    return done["id"], f"https://www.youtube.com/shorts/{done['id']}"


def youtube_views(acc, ids: list) -> dict:
    out = {}
    for i in range(0, len(ids), 50):
        r = _check(httpx.get("https://www.googleapis.com/youtube/v3/videos",
                             params={"part": "statistics", "id": ",".join(ids[i:i + 50])},
                             headers={"Authorization": f"Bearer {_yt_token(acc)}"}, timeout=30), "Reading YouTube views").json()
        for it in r.get("items", []):
            out[it["id"]] = int(it["statistics"].get("viewCount", 0))
    return out


# ------------------------------------------------------------------ Instagram (Meta Unified Login / Professional accounts)
def _instagram_finish(code: str) -> dict:
    cid, secret = app_keys("instagram")
    redirect = redirect_for("instagram")
    tok_res = _check(httpx.get(f"{META_GRAPH}/oauth/access_token", params={
        "client_id": cid, "client_secret": secret, "redirect_uri": redirect, "code": code}, timeout=30),
        "Meta sign-in").json()
    token = tok_res.get("access_token")
    if not token:
        raise ValueError("Meta did not return an access token.")

    token_type = "SUAT"
    expires_at = 0
    if "expires_in" in tok_res:
        expires_at = int(time.time() + int(tok_res["expires_in"]) - 120)

    target_ids = []
    # Inspect token with debug_token to identify token type (SUAT vs USER), scopes, and expiration
    try:
        dbg = httpx.get(f"{META_GRAPH}/debug_token", params={
            "input_token": token, "access_token": f"{cid}|{secret}"}, timeout=15).json().get("data", {})
        if dbg:
            if dbg.get("type"):
                token_type = dbg["type"]
            if "expires_at" in dbg:
                expires_at = int(dbg["expires_at"])
            for g in dbg.get("granular_scopes", []):
                if g.get("scope") in ("instagram_content_publish", "instagram_basic"):
                    for tid in g.get("target_ids", []):
                        if tid and str(tid) not in target_ids:
                            target_ids.append(str(tid))
            # If standard short-lived user token (< 1 day), exchange for long-lived 60-day token
            if token_type == "USER" and 0 < expires_at < time.time() + 86400:
                try:
                    ex = httpx.get(f"{META_GRAPH}/oauth/access_token", params={
                        "grant_type": "fb_exchange_token", "client_id": cid, "client_secret": secret,
                        "fb_exchange_token": token}, timeout=30).json()
                    if "access_token" in ex:
                        token = ex["access_token"]
                        if "expires_in" in ex:
                            expires_at = int(time.time() + int(ex["expires_in"]) - 120)
                except Exception:
                    pass
    except Exception:
        pass

    return _instagram_save(token, token_type=token_type, expires=expires_at, target_ids=target_ids)


def _instagram_save(token: str, token_type: str = "SUAT", expires: int = 0, target_ids: list = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    ig_id = ""
    username = ""
    account_detail = "Professional account"
    api_host = META_GRAPH

    # 1. Inspect granular asset target IDs discovered via debug_token
    if target_ids:
        for tid in target_ids:
            try:
                r = httpx.get(f"{META_GRAPH}/{tid}", params={"fields": "id,username,name"}, headers=headers, timeout=30)
                if r.status_code == 200:
                    d = r.json()
                    if d.get("id"):
                        ig_id = str(d["id"])
                        username = d.get("username", "")
                        account_detail = "Professional account (Unified Login)"
                        break
            except Exception:
                pass

    # 2. Query assigned Instagram accounts (System User / Business Portfolio assets)
    if not ig_id:
        try:
            r = httpx.get(f"{META_GRAPH}/me/assigned_instagram_accounts",
                          params={"fields": "id,username,name"}, headers=headers, timeout=30)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    item = data[0]
                    ig_id = str(item.get("id", ""))
                    username = item.get("username", "")
                    account_detail = "Professional account (Unified Login)"
        except Exception:
            pass

    # 3. Fallback: Facebook Pages linked to Instagram accounts
    if not ig_id:
        try:
            r = httpx.get(f"{META_GRAPH}/me/accounts",
                          params={"fields": "id,name,instagram_business_account{id,username,name}"},
                          headers=headers, timeout=30)
            if r.status_code == 200:
                for page in r.json().get("data", []):
                    iba = page.get("instagram_business_account")
                    if not iba and page.get("id"):
                        try:
                            pr = httpx.get(f"{META_GRAPH}/{page['id']}",
                                           params={"fields": "instagram_business_account{id,username,name}"},
                                           headers=headers, timeout=15)
                            if pr.status_code == 200:
                                iba = pr.json().get("instagram_business_account")
                        except Exception:
                            pass
                    if iba and iba.get("id"):
                        ig_id = str(iba["id"])
                        username = iba.get("username", "")
                        account_detail = f"Page: {page.get('name', 'Facebook Page')}"
                        break
        except Exception:
            pass

    # 4. Fallback: direct me endpoint on Meta Graph
    if not ig_id:
        try:
            r = httpx.get(f"{META_GRAPH}/me", params={"fields": "id,username,account_type"},
                          headers=headers, timeout=30)
            if r.status_code == 200:
                d = r.json()
                if d.get("id"):
                    ig_id = str(d["id"])
                    username = d.get("username", "")
                    account_detail = str(d.get("account_type", "Professional account"))
        except Exception:
            pass

    # 5. Fallback: legacy graph.instagram.com/me
    if not ig_id:
        try:
            r = httpx.get(f"{LEGACY_IG}/me", params={"fields": "user_id,username,account_type"},
                          headers=headers, timeout=30)
            if r.status_code == 200:
                d = r.json()
                if d.get("user_id"):
                    ig_id = str(d["user_id"])
                    username = d.get("username", "")
                    account_detail = str(d.get("account_type", "Professional account"))
                    api_host = LEGACY_IG
        except Exception:
            pass

    if not ig_id:
        raise ValueError("No Instagram Professional account was found. In the Meta login dialog, make sure you selected your Instagram Business or Creator account and granted access.")

    # If username wasn't returned in collection, look up the node
    if not username:
        try:
            info = httpx.get(f"{api_host}/{ig_id}", params={"fields": "id,username,name"},
                             headers=headers, timeout=30).json()
            username = info.get("username", ig_id)
        except Exception:
            username = ig_id

    display_name = "@" + username if username else f"Instagram ({ig_id})"
    acc = {
        "id": f"ig_{ig_id}",
        "platform": "instagram",
        "name": display_name,
        "added": time.time(),
        "user_id": str(ig_id),
        "token": token,
        "token_time": time.time(),
        "token_type": token_type,
        "expires": expires,
        "api_host": api_host,
        "detail": account_detail,
    }
    _put(acc)
    return acc


def instagram_add(token: str) -> dict:
    """An account added with a token pasted from the Meta app dashboard or Business Suite."""
    return _instagram_save(token.strip())


# ------------------------------------------------------------------ Instagram (the older Instagram Login)
def _instagram_login_finish(code: str) -> dict:
    cid, secret = app_keys("instagram_login")
    short = _check(httpx.post("https://api.instagram.com/oauth/access_token", data={
        "client_id": cid, "client_secret": secret, "grant_type": "authorization_code",
        "redirect_uri": redirect_for("instagram_login"), "code": code}, timeout=30), "Instagram sign-in").json()
    if isinstance(short.get("data"), list) and short["data"]:
        short = short["data"][0]
    if "instagram_business_content_publish" not in str(short.get("permissions", "instagram_business_content_publish")):
        raise ValueError("PeakCut needs permission to publish. Connect again and leave every box ticked.")
    long = _check(httpx.get(f"{LEGACY_IG}/access_token", params={"grant_type": "ig_exchange_token", "client_secret": secret,
                                                                 "access_token": short["access_token"]}, timeout=30),
                  "Keeping the Instagram sign-in").json()
    return _instagram_login_save(long["access_token"])


def _instagram_login_save(token: str) -> dict:
    me = _check(httpx.get(f"{LEGACY_IG}/me", params={"fields": "user_id,username,account_type"},
                          headers={"Authorization": f"Bearer {token}"}, timeout=30), "Reading the Instagram account").json()
    kind = str(me.get("account_type") or "")
    acc = {"id": f"ig_{me['user_id']}", "platform": "instagram", "name": "@" + me["username"], "added": time.time(),
           "user_id": str(me["user_id"]), "token": token, "token_time": time.time(), "api_host": LEGACY_IG,
           "detail": {"BUSINESS": "Business account", "MEDIA_CREATOR": "Creator account"}.get(kind.upper(), "")}
    _put(acc)
    return acc


def _ig_host(acc) -> str:
    """Which Instagram API an account talks to. Accounts connected before Unified Login existed carry no mark at
    all and used Instagram's own API, so an unmarked account stays on it."""
    return acc.get("api_host") or (LEGACY_IG if "token_type" not in acc else META_GRAPH)


def _ig_token(acc) -> str:
    # Instagram Login tokens (graph.instagram.com) last 60 days and are renewed weekly
    if _ig_host(acc) == LEGACY_IG:
        if time.time() - acc.get("token_time", 0) > 7 * 86400:  # tokens last 60 days; refresh weekly
            try:
                r = _check(httpx.get(f"{LEGACY_IG}/refresh_access_token", params={"grant_type": "ig_refresh_token"},
                                     headers={"Authorization": f"Bearer {acc['token']}"}, timeout=30), "Refreshing").json()
                acc.update(token=r["access_token"], token_time=time.time())
                _put(acc)
            except Exception:  # noqa: BLE001
                pass
        return acc["token"]

    # Meta Graph / Unified Login:
    # SUATs or permanent tokens have expires == 0
    exp = acc.get("expires", 0)
    if exp and exp > 0 and time.time() > exp:
        raise RuntimeError("Instagram sign-in has expired. Connect again in Settings → Connected apps.")
    return acc["token"]


def instagram_upload(acc, path: Path, caption: str, job_id: str = ""):
    """A Reel. On a public site Instagram fetches the clip from a one-hour signed link; on a computer it cannot
    reach, the file is sent instead (only possible for some app types)."""
    token = _ig_token(acc)
    auth = {"Authorization": f"Bearer {token}"}
    host = _ig_host(acc)
    uid = acc["user_id"]
    if config.PUBLIC_URL.startswith("https://") and job_id:
        box = _check(httpx.post(f"{host}/{uid}/media", data={"media_type": "REELS", "video_url": clip_link(job_id, path.name),
                                                           "caption": caption[:2200], "share_to_feed": "true"},
                                headers=auth, timeout=60), "Starting the Instagram post").json()
    else:
        box = _check(httpx.post(f"{host}/{uid}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                           "caption": caption[:2200], "share_to_feed": "true"},
                                headers=auth, timeout=60), "Starting the Instagram upload").json()
        with path.open("rb") as f:
            _check(httpx.post(f"{IG_UPLOAD}/{box['id']}", content=f.read(),
                              headers={"Authorization": f"OAuth {token}", "offset": "0",
                                       "file_size": str(path.stat().st_size)}, timeout=900),
                   "Uploading to Instagram (posting from a computer needs a public https address)")
    for _ in range(90):
        st = _check(httpx.get(f"{host}/{box['id']}", params={"fields": "status_code,status"}, headers=auth, timeout=30),
                    "Checking the Instagram upload").json()
        if st.get("status_code") in ("FINISHED", "PUBLISHED"):
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram rejected the video: {st.get('status', st['status_code'])}")
        time.sleep(6)
    else:
        raise RuntimeError("Instagram took too long to process the video.")
    pub = _check(httpx.post(f"{host}/{uid}/media_publish", data={"creation_id": box["id"]}, headers=auth, timeout=60),
                 "Publishing on Instagram").json()
    link = ""
    try:
        link = httpx.get(f"{host}/{pub['id']}", params={"fields": "permalink"}, headers=auth, timeout=30).json().get("permalink", "")
    except Exception:  # noqa: BLE001
        pass
    return pub["id"], link


def _extract_metric_value(item: dict):
    if not isinstance(item, dict):
        return None
    if "values" in item and item["values"]:
        v = item["values"][0]
        if isinstance(v, dict) and "value" in v:
            return v["value"]
    if "total_value" in item and isinstance(item["total_value"], dict) and "value" in item["total_value"]:
        return item["total_value"]["value"]
    return None


def instagram_views(acc, media_id: str):
    host = _ig_host(acc)
    token = _ig_token(acc)
    headers = {"Authorization": f"Bearer {token}"}
    for metric in ("views", "plays"):
        try:
            r = httpx.get(f"{host}/{media_id}/insights", params={"metric": metric}, headers=headers, timeout=30)
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    val = _extract_metric_value(data[0])
                    if val is not None:
                        return int(val)
        except Exception:  # noqa: BLE001
            pass
    return None


# ------------------------------------------------------------------ TikTok (Login Kit + Content Posting API)
def _tt(r, what):
    """TikTok answers with an "error" object even on success; anything but "ok" is a failure."""
    try:
        data = r.json()
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    if r.status_code >= 400 or (err and err.get("code") not in (None, "ok")) or (isinstance(data.get("error"), str)):
        code = err.get("code") or data.get("error") or r.status_code
        msg = err.get("message") or data.get("error_description") or r.text[:200]
        e = RuntimeError(f"{what} failed ({code}): {msg}")
        e.code = code
        raise e
    return data


def _tt_tokens(form: dict) -> dict:
    cid, secret = app_keys("tiktok")
    return _tt(httpx.post(f"{TT}/oauth/token/", data={"client_key": cid, "client_secret": secret, **form},
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30), "TikTok sign-in")


def _tiktok_finish(code: str) -> dict:
    tok = _tt_tokens({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_for("tiktok")})
    if "video.publish" not in str(tok.get("scope", "")):
        raise ValueError("PeakCut needs permission to post videos. Connect again and allow posting.")
    acc = {"id": f"tt_{tok['open_id']}", "platform": "tiktok", "name": "TikTok account", "added": time.time(),
           "open_id": tok["open_id"], "access_token": tok["access_token"], "refresh_token": tok["refresh_token"],
           "expires": time.time() + int(tok.get("expires_in", 7200)) - 120}
    try:
        info = _tiktok_creator(acc)
        acc["name"] = "@" + info["creator_username"] if info.get("creator_username") else info.get("creator_nickname") or acc["name"]
    except Exception:  # noqa: BLE001 - the name is only for showing
        pass
    _put(acc)
    return acc


def _tt_token(acc) -> str:
    if acc.get("expires", 0) > time.time():
        return acc["access_token"]
    tok = _tt_tokens({"grant_type": "refresh_token", "refresh_token": acc["refresh_token"]})
    # every refresh hands out a new refresh token and retires the old one: keep the new one straight away
    acc.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", acc["refresh_token"]),
               expires=time.time() + int(tok.get("expires_in", 7200)) - 120)
    _put(acc)
    return acc["access_token"]


def _tt_post(acc, path_, body, what):
    return _tt(httpx.post(f"{TT}{path_}", json=body, timeout=60,
                          headers={"Authorization": f"Bearer {_tt_token(acc)}", "Content-Type": "application/json; charset=UTF-8"}),
               what).get("data") or {}


def _tiktok_creator(acc) -> dict:
    return _tt_post(acc, "/post/publish/creator_info/query/", {}, "Reading the TikTok account")


# PeakCut's visibility setting -> what TikTok calls it, best first
TT_PRIVACY = {"public": ["PUBLIC_TO_EVERYONE", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS"],
              "unlisted": ["MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"], "private": ["SELF_ONLY"]}


def _tt_chunks(size: int) -> tuple:
    """(chunk size, how many). Up to 64 MB goes in one piece; bigger files in 10 MB pieces, the last one taking the rest."""
    if size <= 64_000_000:
        return size, 1
    return 10_000_000, size // 10_000_000


def tiktok_upload(acc, path: Path, caption: str, privacy: str = "public"):
    info = _tiktok_creator(acc)
    options = info.get("privacy_level_options") or ["SELF_ONLY"]
    level = next((p for p in TT_PRIVACY.get(privacy, ["SELF_ONLY"]) if p in options), "SELF_ONLY" if "SELF_ONLY" in options else options[0])
    size = path.stat().st_size
    chunk, count = _tt_chunks(size)

    def init(lv):
        return _tt_post(acc, "/post/publish/video/init/", {
            "post_info": {"title": caption[:2200], "privacy_level": lv, "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False, "video_cover_timestamp_ms": 1000},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}},
            "Starting the TikTok post")
    note = ""
    try:
        box = init(level)
    except RuntimeError as e:
        # A TikTok app that has not passed TikTok's review can only post privately: post privately, and say so.
        if "unaudited" not in str(getattr(e, "code", "")) or level == "SELF_ONLY" or "SELF_ONLY" not in options:
            raise
        level, note = "SELF_ONLY", "Posted as private: this server's TikTok app hasn't passed TikTok's review yet."
        box = init(level)
    with path.open("rb") as f:
        for n in range(count):
            first = n * chunk
            last = size - 1 if n == count - 1 else first + chunk - 1
            part = f.read(last - first + 1)
            r = httpx.put(box["upload_url"], content=part, timeout=900, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(part)), "Content-Range": f"bytes {first}-{last}/{size}"})
            if r.status_code not in (200, 201, 206):
                raise RuntimeError(f"Uploading to TikTok failed ({r.status_code}): {r.text[:200]}")
    for _ in range(120):
        st = _tt_post(acc, "/post/publish/status/fetch/", {"publish_id": box["publish_id"]}, "Checking the TikTok post")
        if st.get("status") == "PUBLISH_COMPLETE":
            break
        if st.get("status") == "FAILED":
            raise RuntimeError(f"TikTok did not publish the video: {st.get('fail_reason') or 'no reason given'}")
        time.sleep(5)
    else:
        raise RuntimeError("TikTok took too long to publish the video.")
    ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
    user = info.get("creator_username") or ""
    url = f"https://www.tiktok.com/@{user}/video/{ids[0]}" if ids and user else ""
    if note:
        acc["detail"] = "Posts are private until this server's TikTok app is reviewed"
        _put(acc)
    return str(ids[0]) if ids else box["publish_id"], url
