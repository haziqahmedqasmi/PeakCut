"""One-click Connect for YouTube, Instagram and TikTok, and posting to each. The platforms are replaced by
stand-ins answering the way their documentation says, so nothing leaves this machine."""
import json, os, sys, tempfile, time
from urllib.parse import parse_qs, urlparse
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-connect-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="1", PEAKCUT_PUBLIC_URL="https://clips.example.com", PEAKCUT_VERIFY_EMAIL="0",
                  GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", GOOGLE_CLIENT_FILE="", PEAKCUT_IG_APP_ID="", PEAKCUT_IG_APP_SECRET="",
                  PEAKCUT_IG_CONFIG_ID="", PEAKCUT_META_APP_ID="", PEAKCUT_META_APP_SECRET="", PEAKCUT_META_CONFIG_ID="",
                  PEAKCUT_TIKTOK_CLIENT_KEY="", PEAKCUT_TIKTOK_CLIENT_SECRET="")
sys.path.insert(0, ".")
from pathlib import Path
import sqlalchemy as sa
from fastapi.testclient import TestClient
from studio import accounts, auth, config, ctx, db, server
db.init()
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

BASE = "https://clips.example.com"
ORIGIN = {"Origin": BASE}
def person(email, admin=False):
    u, _ = auth._create(email, email.split("@")[0], password_hash="x")
    if admin:
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == u.id).values(is_admin=True))
    cl = TestClient(server.app, base_url=BASE, follow_redirects=False)
    cl.cookies.set(auth.COOKIE, auth.start_session(u.id))
    return u, cl
boss, a = person("boss@example.com", admin=True)
user, b = person("user@example.com")

# ---- nothing is set up: the buttons say so, and only an admin may set it up
st = b.get("/api/status").json()
check("nothing can be connected before setup", st["connect"], {"youtube": False, "instagram": False, "tiktok": False})
check("a normal user cannot see the setup", b.get("/api/apps").status_code, 404)
check("...or change it", b.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd", "client_secret": "x"}, headers=ORIGIN).status_code, 404)
r = b.get("/oauth/instagram/start")
check("Connect before setup comes back with a reason", (r.status_code, "connect_error" in r.headers.get("location", "")), (303, True))
check("setup from another website is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "abcd1234", "client_secret": "s"},
                                                    headers={"Origin": "https://evil.example"}).status_code, 403)
check("a bad id is refused", a.put("/api/apps", json={"platform": "tiktok", "client_id": "a b<", "client_secret": "s"}, headers=ORIGIN).status_code, 400)
for pf, cid in (("youtube", "g-client-123.apps"), ("instagram", "1234567890"), ("tiktok", "awtiktokkey123")):
    r = a.put("/api/apps", json={"platform": pf, "client_id": cid, "client_secret": f"{pf}-secret-value"}, headers=ORIGIN)
    check(f"{pf} app saved", (r.status_code, r.json()[pf]["ready"]), (200, True))
apps = a.get("/api/apps").json()
check("secrets are never sent back", any("secret-value" in json.dumps(v) for v in apps.values()), False)
check("old flow keeps Instagram app ID label", (apps["instagram"]["id_label"], apps["instagram"]["secret_label"]),
      ("Instagram app ID", "Instagram app secret"))
check("each platform shows the address to register", apps["tiktok"]["redirect"], BASE + "/oauth/tiktok/callback")
r = a.put("/api/apps", json={"platform": "tiktok", "client_id": "", "client_secret": "tiktok-secret-value2"}, headers=ORIGIN)
check("a new secret alone keeps the saved id", accounts.app_keys("tiktok"), ("awtiktokkey123", "tiktok-secret-value2"))
check("everyone can connect now", b.get("/api/status").json()["connect"], {"youtube": True, "instagram": True, "tiktok": True})

# ---- YouTube cookies, for servers YouTube refuses
from studio import media
COOK = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsecret-cookie-value\n"
check("a normal user cannot save YouTube cookies", b.put("/api/youtube-cookies", json={"text": COOK}, headers=ORIGIN).status_code, 404)
check("something that is not a cookies file is refused", a.put("/api/youtube-cookies", json={"text": "hello"}, headers=ORIGIN).status_code, 400)
r = a.put("/api/youtube-cookies", json={"text": COOK}, headers=ORIGIN)
check("cookies saved", (r.status_code, r.json()["saved"]), (200, True))
check("the cookies are never sent back", "secret-cookie-value" in a.get("/api/youtube-cookies").text, False)
check("downloads use them", media.cookie_opts(), {"cookiefile": str(config.YT_COOKIES)})
a.put("/api/youtube-cookies", json={"remove": True}, headers=ORIGIN)
check("...until they are removed", (config.YT_COOKIES.exists(), media.cookie_opts()), (False, {}))

# ---- the platforms, as stand-ins
class R:
    def __init__(self, code, data=None, headers=None, text=None):
        self.status_code, self._d, self.headers = code, data, headers or {}
        self.text = text if text is not None else json.dumps(data)
    def json(self):
        if self._d is None:
            raise ValueError
        return self._d
calls, uploads, tt = [], [], {"audited": False, "status": 0}
def post(url, data=None, json=None, headers=None, params=None, timeout=None, content=None):
    calls.append(("POST", url, data or json, headers or {}))
    if url == "https://oauth2.googleapis.com/token":
        return R(200, {"access_token": "yt-access", "refresh_token": "yt-refresh", "expires_in": 3600})
    if url == "https://api.instagram.com/oauth/access_token" or url.endswith("/oauth/access_token"):
        return R(200, {"access_token": "ig-suat", "token_type": "bearer", "expires_in": 5184000})
    if url.endswith("/oauth/token/"):
        if data.get("grant_type") == "refresh_token":
            return R(200, {"access_token": "tt-access-2", "refresh_token": "tt-refresh-2", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
        return R(200, {"access_token": "tt-access", "refresh_token": "tt-refresh", "expires_in": 7200, "open_id": "oid", "scope": "user.info.basic,video.publish"})
    if url.endswith("/creator_info/query/"):
        return R(200, {"data": {"creator_username": "streamerclips", "creator_nickname": "Streamer Clips",
                                "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/video/init/"):
        lv = json["post_info"]["privacy_level"]
        tt["init"] = json
        if lv != "SELF_ONLY" and not tt["audited"]:
            return R(403, {"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "private only"}})
        return R(200, {"data": {"publish_id": "v_pub_1", "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1"}, "error": {"code": "ok"}})
    if url.endswith("/post/publish/status/fetch/"):
        tt["status"] += 1
        if tt["status"] < 2:
            return R(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}})
        return R(200, {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300] if tt["audited"] else []}, "error": {"code": "ok"}})
    if url.endswith("/17841/media") or url.endswith("/old123/media"):
        return R(200, {"id": "container1"})
    if url.endswith("/17841/media_publish") or url.endswith("/old123/media_publish"):
        return R(200, {"id": "media1"})
    return R(404, {})
def get(url, params=None, headers=None, timeout=None):
    calls.append(("GET", url, params, headers or {}))
    if url.endswith("/youtube/v3/channels"):
        return R(200, {"items": [{"id": "UC123", "snippet": {"title": "My Channel"}}]})
    if url.endswith("/oauth/access_token"):
        if params and params.get("grant_type") == "fb_exchange_token":
            if params.get("fb_exchange_token") == "ig-ex-fail":
                return R(400, {"error": {"message": "exchange rejected"}})
            return R(200, {"access_token": "ig-long-fb", "token_type": "bearer", "expires_in": 5184000})
        if params and params.get("code") == "igcode-user":
            return R(200, {"access_token": "ig-short-user", "token_type": "bearer", "expires_in": 3600})
        if params and params.get("code") == "igcode-dbg-fail":
            return R(200, {"access_token": "ig-dbg-fail", "token_type": "bearer", "expires_in": 3600})
        if params and params.get("code") == "igcode-ex-fail":
            return R(200, {"access_token": "ig-ex-fail", "token_type": "bearer", "expires_in": 3600})
        if params and params.get("code") and params.get("code") != "igcode":
            return R(400, {"error": {"message": "bad code"}})
        return R(200, {"access_token": "ig-suat", "token_type": "bearer", "expires_in": 5184000})
    if url.endswith("/debug_token"):
        tok_in = (params or {}).get("input_token", "")
        if tok_in in ("ig-dbg-fail", "ig-ex-fail"):
            return R(500, {"error": {"message": "debug failed"}})
        if tok_in == "ig-short-user":
            return R(200, {"data": {"type": "USER", "is_valid": True, "expires_at": int(time.time() + 3600)}})
        return R(200, {"data": {"type": "SYSTEM_USER", "is_valid": True, "expires_at": 0}})
    if url.endswith("/me/assigned_instagram_accounts"):
        auth_hdr = (headers or {}).get("Authorization", "")
        if "token_err" in auth_hdr:
            return R(403, {"error": {"message": "missing permission"}})
        if "token_page_fallback" in auth_hdr or "token_none" in auth_hdr:
            return R(200, {"data": []})
        return R(200, {"data": [{"id": "17841", "username": "clipper", "name": "Clipper Studio"}]})
    if url.endswith("/me/accounts"):
        auth_hdr = (headers or {}).get("Authorization", "")
        if "token_err" in auth_hdr:
            return R(403, {"error": {"message": "missing permission"}})
        if "token_none" in auth_hdr:
            return R(200, {"data": []})
        return R(200, {"data": [{"id": "page1", "name": "Creator Page", "instagram_business_account": {"id": "987654", "username": "page_ig"}}]})
    if url.endswith("/17841"):
        return R(200, {"id": "17841", "username": "clipper", "name": "Clipper Studio"})
    if url == "https://graph.instagram.com/access_token":
        return R(200, {"access_token": "ig-long", "token_type": "bearer", "expires_in": 5183944})
    if url == "https://graph.instagram.com/refresh_access_token":
        return R(200, {"access_token": "old-tok-refreshed"})
    if url == "https://graph.instagram.com/media1/insights":
        return R(200, {"data": [{"name": "views", "values": [{"value": 42}]}]})
    if url.endswith("/me"):
        return R(200, {"id": "17841", "user_id": "17841", "username": "clipper", "account_type": "MEDIA_CREATOR"})
    if url.endswith("/container1"):
        return R(200, {"status_code": "FINISHED"})
    if url.endswith("/media1"):
        return R(200, {"permalink": "https://www.instagram.com/reel/abc/"})
    return R(404, {})
def put(url, content=None, headers=None, timeout=None):
    uploads.append((url, len(content), headers))
    return R(201, None, text="")
accounts.httpx.post, accounts.httpx.get, accounts.httpx.put = post, get, put
accounts.time.sleep = lambda s: None

# ---- connecting: the button goes to the platform's own page, and back
def connect(cl, pf, code="code1"):
    r = cl.get(f"/oauth/{pf}/start")
    loc = urlparse(r.headers["location"])
    state = parse_qs(loc.query)["state"][0]
    back = cl.get(f"/oauth/{pf}/callback", params={"code": code, "state": state})
    return loc, back
loc, back = connect(b, "youtube")
check("YouTube: Google's page, asking to upload", (loc.netloc, "youtube.upload" in parse_qs(loc.query)["scope"][0]), ("accounts.google.com", True))
check("YouTube: back on Connected apps, connected", (back.status_code, back.headers["location"].startswith("/app/settings/apps?connected=youtube")), (303, True))
loc, back = connect(b, "instagram", "igcode#_")
q = parse_qs(loc.query)
check("Instagram (no config_id): existing Instagram Login page, with its own permissions",
      (loc.netloc, loc.path, q["client_id"][0], q["scope"][0]),
      ("www.instagram.com", "/oauth/authorize", "1234567890", "instagram_business_basic,instagram_business_content_publish"))
check("Instagram: connected, with the #_ Instagram adds to the code removed", "connected=instagram" in back.headers["location"])
with ctx.as_user(user.id):
    check("Instagram Login account uses graph.instagram.com and keeps long token",
          (accounts.get("ig_17841")["api_host"], accounts.get("ig_17841")["token"]), (accounts.LEGACY_IG, "ig-long"))
loc, back = connect(b, "tiktok")
q = parse_qs(loc.query)
check("TikTok: TikTok's own page, asking to post", (loc.netloc, q["client_key"][0], q["scope"][0]), ("www.tiktok.com", "awtiktokkey123", "user.info.basic,video.publish"))
check("TikTok: connected", "connected=tiktok" in back.headers["location"])
accs = {x["platform"]: x for x in b.get("/api/accounts").json()}
check("all three accounts are listed with their names", (accs["youtube"]["name"], accs["instagram"]["name"], accs["tiktok"]["name"]),
      ("My Channel", "@clipper", "@streamerclips"))
check("tokens never reach the page", any(t in b.get("/api/accounts").text for t in ("ig-suat", "ig-long", "tt-access", "yt-refresh")), False)
check("someone else's accounts are not mine", a.get("/api/accounts").json(), [])
r = b.get("/oauth/tiktok/callback", params={"code": "x", "state": "made-up"})
check("a made-up return is refused", "connect_error" in r.headers["location"])
r = b.get("/oauth/tiktok/callback", params={"error": "access_denied"})
check("cancelling on the platform says so", "cancelled" in r.headers["location"])
r_ig_cancel = b.get("/oauth/instagram/callback", params={"error": "access_denied", "error_reason": "user_denied"})
check("cancelling on Meta says so", "cancelled" in r_ig_cancel.headers["location"])
r = a.put("/api/apps", json={"platform": "instagram", "client_id": "", "client_secret": "", "config_id": "cfg12345"}, headers=ORIGIN)
check("instagram config_id saved without retyping keys, labels switch to Meta App ID",
      (r.status_code, accounts.app_keys("instagram"), accounts.app_config_id("instagram"), r.json()["instagram"]["id_label"]),
      (200, ("1234567890", "instagram-secret-value"), "cfg12345", "Meta App ID"))
loc_cfg, back_cfg = connect(b, "instagram", "igcode#_")
q_cfg = parse_qs(loc_cfg.query)
check("Meta OAuth dialog passes config_id when configured", (loc_cfg.netloc, q_cfg.get("config_id", [""])[0], "scope" in q_cfg),
      ("www.facebook.com", "cfg12345", False))
with ctx.as_user(user.id):
    saved_unified = accounts.get("ig_17841")
check("Facebook Login for Business finishes and saves Meta Graph account",
      ("connected=instagram" in back_cfg.headers["location"], saved_unified["api_host"], saved_unified["token"]),
      (True, accounts.META_GRAPH, "ig-suat"))
calls.clear()
loc_u, back_u = connect(b, "instagram", "igcode-user")
with ctx.as_user(user.id):
    saved_user = accounts.get("ig_17841")
check("short-lived USER token exchanges for long-lived token",
      ("connected=instagram" in back_u.headers["location"],
       any(c[2] and c[2].get("grant_type") == "fb_exchange_token" and c[2].get("fb_exchange_token") == "ig-short-user" for c in calls if c[0] == "GET"),
       saved_user["token"], saved_user.get("expires", 0) > time.time() + 5_000_000),
      (True, True, "ig-long-fb", True))
calls.clear()
loc_df, back_df = connect(b, "instagram", "igcode-dbg-fail")
with ctx.as_user(user.id):
    saved_df = accounts.get("ig_17841")
check("failed debug_token defaults to USER and still exchanges for long-lived token",
      ("connected=instagram" in back_df.headers["location"],
       any(c[2] and c[2].get("grant_type") == "fb_exchange_token" and c[2].get("fb_exchange_token") == "ig-dbg-fail" for c in calls if c[0] == "GET"),
       saved_df["token"]),
      (True, True, "ig-long-fb"))
loc_ef, back_ef = connect(b, "instagram", "igcode-ex-fail")
check("failed long-lived exchange raises real error instead of saving short-lived token",
      "connect_error" in back_ef.headers["location"] and "Keeping+the+Meta+sign-in+failed" in back_ef.headers["location"], True)
with ctx.as_user(user.id):
    suat_acc = accounts.instagram_add("direct-suat-token-value")
    fb_page_acc = accounts._instagram_save("token_page_fallback")
    granular_acc = accounts._instagram_save("token_granular", target_ids=["17841"])
    try:
        accounts._instagram_save("token_none")
        check("missing professional account raises instead of falling back to /me", False)
    except ValueError as e:
        check("missing professional account raises instead of falling back to /me", "No Instagram Professional account was found" in str(e))
    try:
        accounts._instagram_save("token_err")
        check("failed discovery steps surface step names in final error", False)
    except ValueError as e:
        msg = str(e)
        check("failed discovery steps surface step names in final error",
              "No Instagram Professional account was found" in msg and "assigned_instagram_accounts" in msg and "me/accounts" in msg)
prev_env_cfg = os.environ.get("PEAKCUT_IG_CONFIG_ID", "")
os.environ["PEAKCUT_IG_CONFIG_ID"] = "envcfg999"
try:
    r_clr = a.put("/api/apps", json={"platform": "instagram", "client_id": "", "client_secret": "", "config_id": ""}, headers=ORIGIN)
    with ctx.as_user(user.id):
        back_legacy_url = urlparse(accounts.connect_url("instagram"))
    check("clearing config_id restores Instagram app ID labels and Instagram Login even with env fallback set",
          (r_clr.status_code, accounts.app_config_id("instagram"), r_clr.json()["instagram"]["id_label"], back_legacy_url.netloc),
          (200, "", "Instagram app ID", "www.instagram.com"))
finally:
    os.environ["PEAKCUT_IG_CONFIG_ID"] = prev_env_cfg
check("instagram_add supports direct SUAT", (suat_acc["name"], suat_acc["token_type"]), ("@clipper", "SUAT"))
check("fallback to Facebook Page Instagram account works", (fb_page_acc["id"], fb_page_acc["name"]), ("ig_987654", "@page_ig"))
check("granular_scopes target_ids discovery works", (granular_acc["id"], granular_acc["name"]), ("ig_17841", "@clipper"))
with ctx.as_user(user.id):
    expired_acc = {"id": "ig_expired", "token": "old", "expires": time.time() - 100, "api_host": accounts.META_GRAPH}
    try:
        accounts._ig_token(expired_acc)
        check("expired Meta token raises", False)
    except RuntimeError as e:
        check("expired Meta token raises", "expired" in str(e).lower())
loc = urlparse(a.get("/oauth/tiktok/start").headers["location"])
r = b.get("/oauth/tiktok/callback", params={"code": "c", "state": parse_qs(loc.query)["state"][0]})
check("one person's sign-in cannot be finished by another", "connect_error" in r.headers["location"])
check("an unknown platform does not exist", b.get("/oauth/myspace/start").status_code, 404)
check("missing api_host defaults to graph.instagram.com",
      (accounts._ig_host({"token": "t"}), accounts._ig_host({"api_host": accounts.META_GRAPH})),
      (accounts.LEGACY_IG, accounts.META_GRAPH))

# ---- the one-hour links Instagram fetches clips from
with ctx.as_user(user.id):
    jd = config.user_dir(user.id) / "jobs" / "job-12345678" / "clips"
jd.mkdir(parents=True)
(jd.parent / "job.json").write_text(json.dumps({"id": "job-12345678", "user": user.id, "dir": str(jd.parent), "clips": [], "status": "done",
                                                "creator": None, "created": time.time(), "options": {}, "source": {"type": "file"}}), encoding="utf-8")
(jd / "clip01.mp4").write_bytes(b"\x00" * 6_000)
from studio import jobs
jobs.load_user(user.id)
with ctx.as_user(user.id):
    link = accounts.clip_link("job-12345678", "clip01.mp4")
anon = TestClient(server.app, base_url=BASE)
path = urlparse(link).path
check("a signed link serves the clip to anyone, no sign-in", (anon.get(path).status_code, len(anon.get(path).content)), (200, 6000))
bad = path.replace(".mp4", "x.mp4")
check("a changed link does not", anon.get(bad).status_code, 404)
check("a made-up link does not", anon.get("/pub/abc.def.mp4").status_code, 404)
with ctx.as_user(user.id):
    old = accounts.clip_link("job-12345678", "clip01.mp4", seconds=-5)
check("an expired link does not", anon.get(urlparse(old).path).status_code, 404)

# ---- posting
with ctx.as_user(user.id):
    ig, ttacc = accounts.get(accs["instagram"]["id"]), accounts.get(accs["tiktok"]["id"])
    calls.clear()
    rid, url = accounts.instagram_upload(ig, jd / "clip01.mp4", "My caption", "job-12345678")
    made = next(c for c in calls if c[1].endswith("/17841/media"))
    check("Instagram: a Reel from the signed link", (made[2]["media_type"], made[2]["video_url"].startswith(BASE + "/pub/")), ("REELS", True))
    check("Instagram: published, with its link", (rid, url), ("media1", "https://www.instagram.com/reel/abc/"))

    old_saved = {"id": "ig_old123", "platform": "instagram", "name": "@oldacc", "added": time.time(),
                 "user_id": "old123", "token": "old-tok", "token_time": time.time()}
    calls.clear()
    rid_old, _ = accounts.instagram_upload(old_saved, jd / "clip01.mp4", "Old caption", "job-12345678")
    check("old saved Instagram account posts through graph.instagram.com",
          (any(c[1] == "https://graph.instagram.com/old123/media" for c in calls),
           any(c[1] == "https://graph.instagram.com/old123/media_publish" for c in calls),
           rid_old),
          (True, True, "media1"))
    calls.clear()
    old_stale = dict(old_saved, token_time=time.time() - 8 * 86400)
    accounts._put(old_stale)
    refreshed_tok = accounts._ig_token(old_stale)
    check("old saved Instagram account refreshes weekly via graph.instagram.com",
          (refreshed_tok, accounts.get("ig_old123")["token"],
           any(c[1] == "https://graph.instagram.com/refresh_access_token" for c in calls)),
          ("old-tok-refreshed", "old-tok-refreshed", True))
    calls.clear()
    old_views = accounts.instagram_views(old_saved, "media1")
    check("old saved Instagram account reads views via graph.instagram.com",
          (old_views, any(c[1] == "https://graph.instagram.com/media1/insights" for c in calls)),
          (42, True))

    uploads.clear(); tt.update(audited=False, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption #fyp", "public")
    check("TikTok: an app not yet reviewed posts privately instead of failing", (tt["init"]["post_info"]["privacy_level"], url), ("SELF_ONLY", ""))
    check("TikTok: the whole file in one piece, with the right range", (len(uploads), uploads[0][2]["Content-Range"]), (1, "bytes 0-5999/6000"))
    check("TikTok: the account says why posts are private", "private" in accounts.get(ttacc["id"]).get("detail", ""))
    tt.update(audited=True, status=0)
    rid, url = accounts.tiktok_upload(ttacc, jd / "clip01.mp4", "My caption", "public")
    check("TikTok: once reviewed, public, with the video's link", (tt["init"]["post_info"]["privacy_level"], url),
          ("PUBLIC_TO_EVERYONE", "https://www.tiktok.com/@streamerclips/video/7300"))
    check("TikTok: big files go in pieces, the last taking the rest", accounts._tt_chunks(150_000_123), (10_000_000, 15))
    acc = accounts.get(ttacc["id"]); acc["expires"] = 0; accounts._put(acc)
    accounts._tt_token(accounts.get(ttacc["id"]))
    check("TikTok: an old sign-in is renewed, and the new refresh token kept", accounts.get(ttacc["id"])["refresh_token"], "tt-refresh-2")

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; db.engine.dispose(); shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
