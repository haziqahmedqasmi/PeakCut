"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import random
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import json

import sqlalchemy as sa

from . import accounts, alerts, config, creators, ctx, db, jobs, live, logs

log = logs.get("posting")
_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"      # where older versions kept the posts: read once, then moved into the database
_posts: list = []                           # the working copy; the database holds the same posts (see _save and _load)
_snap: dict = {}                            # post id -> the JSON last written for it, so only what changed is written


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")


def _row(p) -> dict:
    return {"user_id": str(p.get("user") or ""), "status": str(p.get("status") or "")[:12], "due_at": float(p.get("due_at") or 0),
            "data": json.dumps(p, sort_keys=True)}


def _save():
    """Write what changed since the last save to the database: only the posts that differ from what is already
    there, not the whole list. A failed write keeps everything in memory and is tried again by the next save."""
    with _lock:
        now = {p["id"]: json.dumps(p, sort_keys=True) for p in _posts if p.get("id")}
        changed = [p for p in _posts if p.get("id") and _snap.get(p["id"]) != now[p["id"]]]
        gone = [i for i in _snap if i not in now]
        if not changed and not gone:
            return
        try:
            with db.engine.begin() as c:
                for p in changed:
                    vals = _row(p)
                    if not c.execute(db.posts.update().where(db.posts.c.id == p["id"]).values(**vals)).rowcount:
                        c.execute(db.posts.insert().values(id=p["id"], **vals))
                if gone:
                    c.execute(db.posts.delete().where(db.posts.c.id.in_(gone)))
        except Exception:  # noqa: BLE001
            log.exception("posting step failed")
            return
        for i in gone:
            _snap.pop(i, None)
        _snap.update({p["id"]: now[p["id"]] for p in changed})


def _load():
    """Read the posts from the database, and move in the ones an older version kept in posts.json (once: that file
    is then renamed, and the posts are in the database from then on)."""
    with _lock:
        try:
            with db.engine.connect() as c:
                rows = c.execute(sa.select(db.posts.c.data)).scalars().all()
        except Exception:  # noqa: BLE001 - no table yet: nothing saved so far
            rows = []
        loaded = []
        for text in rows:
            try:
                p = json.loads(text)
                if isinstance(p, dict) and p.get("id"):
                    loaded.append(p)
            except ValueError:
                continue
        _posts[:] = loaded
        _snap.clear()
        _snap.update({p["id"]: json.dumps(p, sort_keys=True) for p in loaded})
        if _FILE.is_file():
            try:
                old = json.loads(_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old = None
            if isinstance(old, list):
                have = {p["id"] for p in _posts}
                _posts.extend(p for p in old if isinstance(p, dict) and p.get("id") and p["id"] not in have)
                _save()
                try:
                    _FILE.replace(_FILE.with_name("posts.json.moved"))
                except OSError:
                    pass


def _claim(p) -> bool:
    """Take a scheduled post for this process, in the database, in one step: of two processes that both see it as
    due, only one gets True, so a clip is never sent twice. If the database cannot be asked, posting goes ahead."""
    try:
        vals = _row(dict(p, status="posting"))
        with db.engine.begin() as c:
            t = db.posts
            if c.execute(t.update().where(sa.and_(t.c.id == p["id"], t.c.status == "scheduled")).values(**vals)).rowcount:
                return True
            if c.execute(sa.select(t.c.status).where(t.c.id == p["id"])).first() is None:
                c.execute(t.insert().values(id=p["id"], **vals))      # not written yet: it is ours
                return True
            return False
    except Exception:  # noqa: BLE001
        return True


def forget(uid: str):
    """Drop everything queued or recorded for a deleted account, so nothing is posted or retried for it any more."""
    with _lock:
        for p in _posts:
            if p.get("user") == uid:
                p["status"] = "deleted"          # the posting loop may be holding this one already: it then skips it
        _posts[:] = [p for p in _posts if p.get("user") != uid]
        _save()


def _day(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _mirror(p):
    """Copy the post's state onto its clip so the clip card can show it."""
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    if clip is not None:
        clip.setdefault("posts", {})[p["id"]] = {k: p.get(k) for k in ("account_name", "platform", "status", "url", "due_at", "error")}
        jobs.save(job)


def _next_slot(account_id, gap_min, cap):
    """Earliest time that respects the gap between posts and the daily cap for this account."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)]
    t = max(time.time(), max((p.get("posted_at") or p["due_at"] for p in mine), default=0) + gap_min * 60)
    for _ in range(30):
        if sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            break
        lt = time.localtime(t + 86400)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))  # 9:00 next day
    return t


def enqueue(job, clip, account_ids, now=False) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and p["status"] in OPEN + ("posted",) for p in _mine()):
                continue  # never post the same clip to the same account twice
            due = time.time() if now else _next_slot(aid, c["post_gap_min"], c["daily_cap"])
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
    if made:
        _wake.set()               # one may be due this moment: no waiting for the next look
    return made


def on_clip_ready(job, clip):
    """Called once when a clip first finishes rendering."""
    c = creators.get(job["creator"])
    if not c or not c["autopost"] or not c["accounts"]:
        return
    if clip.get("score", 0) < c["post_min_score"]:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def _caption(clip, creator):
    # A description the person wrote themselves is posted as it is, without the AI's hashtags added to it, and
    # one that already ends with its hashtags does not get them twice.
    tags = "" if clip.get("fixed_description") or clip.get("hashtags_in_description") else " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


def retry_delay(tries: int, wait=None) -> float:
    """Seconds to wait before post number `tries`+1: longer after each failure (2 minutes, 10, 45, then 3 hours),
    a little different each time so many failed posts do not all come back at the same moment, and never less than
    the platform itself asked for."""
    steps = config.POST_BACKOFF
    base = steps[min(max(tries, 1), len(steps)) - 1] * random.uniform(0.8, 1.2)
    return max(base, float(wait or 0))


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise accounts.PostError("The clip no longer exists or is not finished.", permanent=True)
        if not acc:
            raise accounts.PostError("That account was disconnected.", permanent=True)
        path = Path(job["dir"]) / "clips" / clip["file"]
        creator = creators.get(job["creator"]) or dict(creators.DEFAULTS, name="clips")
        title, cap = clip.get("title") or clip.get("hook") or "Clip", _caption(clip, creator)
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap + "\n\n#Shorts", clip.get("hashtags", []), creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, f"{title}\n\n{cap}", job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, f"{title}\n\n{cap}", creator["privacy"])
        else:
            raise accounts.PostError("That kind of account is no longer supported.", permanent=True)
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        # A refusal (a rejected video, access taken away) is not tried again: it would fail the same way. Anything
        # else (a dropped connection, a busy platform, a limit) is, later each time, up to POST_TRIES tries.
        if not getattr(e, "permanent", False) and p["tries"] < config.POST_TRIES and clip and acc:
            delay = retry_delay(p["tries"], getattr(e, "wait", None))
            p.update(status="scheduled", due_at=time.time() + delay)
            creators.log(p["creator"], f"Post to {p['account_name']} failed, trying again in {round(delay / 60)} min: {p['error'][:150]}")
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


# Uploads run on a few workers, so one slow upload (a big clip, a platform that takes minutes to say yes) does not hold
# up everyone else's. Two clips for the same account never go at once: platforms limit that, and the order stays right.
_wake = threading.Event()        # set when something is due now, or an upload has finished: look again straight away
_busy: set = set()               # (user, account) with an upload running right now
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(config.POST_WORKERS, thread_name_prefix="post")
    return _pool


def _work(p, key):
    try:
        with ctx.as_user(p["user"]):
            _post(p)
    except Exception:  # noqa: BLE001
        log.exception("posting step failed")
    finally:
        with _lock:
            _busy.discard(key)
        _wake.set()


def _dispatch() -> int:
    """Start every post that is due and has a free worker and a free account, oldest first. Returns how many started."""
    pool, started = _get_pool(), 0
    with _lock:
        due = sorted((p for p in _posts if p["status"] == "scheduled" and p["due_at"] <= time.time() and p.get("user")),
                     key=lambda p: p["due_at"])
    for p in due:
        key = (p["user"], p["account"])
        with _lock:
            if len(_busy) >= config.POST_WORKERS:
                break
            if key in _busy or p["status"] != "scheduled":
                continue
        try:
            with ctx.as_user(p["user"]):
                if accounts.settings()["paused"]:
                    continue
        except Exception:  # noqa: BLE001
            continue
        with _lock:
            if p["status"] != "scheduled" or key in _busy or len(_busy) >= config.POST_WORKERS:
                continue
            if not _claim(p):             # another process has taken it
                continue
            p["status"] = "posting"
            _busy.add(key)
        pool.submit(_work, p, key)
        started += 1
    return started


def _run():
    while True:
        _wake.wait(15)
        _wake.clear()
        try:
            _dispatch()
        except Exception:  # noqa: BLE001
            log.exception("posting step failed")


def _stats_for(recent):
    for aid in {p["account"] for p in recent if p["platform"] == "youtube"}:
        acc = accounts.get(aid)
        if acc:
            views = accounts.youtube_views(acc, [p["remote_id"] for p in recent if p["account"] == aid])
            for p in recent:
                if p["account"] == aid and p["remote_id"] in views:
                    p["views"] = views[p["remote_id"]]
    for p in recent:
        acc = accounts.get(p["account"]) if p["platform"] == "instagram" else None
        if acc:
            v = accounts.instagram_views(acc, p["remote_id"])
            if v is not None:
                p["views"] = v


def _stats():
    while True:
        for uid in {p.get("user") for p in _posts if p.get("user")}:
            try:
                with ctx.as_user(uid):
                    _stats_for([p for p in _mine() if p["status"] == "posted" and time.time() - p["posted_at"] < 14 * 86400])
            except Exception:  # noqa: BLE001
                pass
        _save()
        time.sleep(1800)


def cancel(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] != "scheduled":
            raise ValueError("Only posts that are still waiting can be cancelled.")
        p["status"] = "cancelled"
        _save()
    _mirror(p)


def retry(pid):
    with _lock:
        p = next((p for p in _mine() if p["id"] == pid), None)
        if not p or p["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled posts can be retried.")
        p.update(status="scheduled", due_at=time.time(), tries=0, error=None)
        _save()
    _mirror(p)
    _wake.set()


def score_check(posts, min_posts: int = 12) -> dict:
    """How well the AI's score has predicted views on posts that have been up for a while.

    Returns {"n", "agree", "suggested"}: `agree` is the share of pairs of clips where the higher score also got
    more views (0.5 is no better than a coin; 1.0 is always right), and `suggested` is the lowest score from which
    the clips did at least as well as the typical one, or None when there is not enough to say. It is only shown
    as advice: nothing is changed by it."""
    rows = [(float(p["score"]), float(p["views"])) for p in posts
            if p.get("status") == "posted" and p.get("views") is not None and p.get("score")]
    if len(rows) < min_posts:
        return {"n": len(rows), "agree": None, "suggested": None}
    won = tie = 0
    for i, (s1, v1) in enumerate(rows):
        for s2, v2 in rows[i + 1:]:
            if s1 == s2 or v1 == v2:
                continue
            tie += 1
            won += (s1 > s2) == (v1 > v2)
    agree = round(won / tie, 2) if tie else None
    typical = sorted(v for _, v in rows)[len(rows) // 2]
    suggested = None
    if agree is not None and agree >= 0.55:        # the score means something here: find where it starts to pay
        for cut in sorted({s for s, _ in rows}):
            above = sorted(v for s, v in rows if s >= cut)
            if len(above) >= max(4, len(rows) // 4) and above[len(above) // 2] >= typical:
                suggested = int(cut)
                break
    return {"n": len(rows), "agree": agree, "suggested": suggested}


def overview() -> dict:
    today = _day(time.time())
    _posts = _mine()  # everything below is about the signed-in user only
    rows = []
    all_cs, all_js = creators.all_creators(), jobs.list_jobs()
    for c in all_cs:
        js = [j for j in all_js if j["creator"] == c["id"]]
        clips = [k for j in js for k in j["clips"] if k.get("status") == "done"]
        ps = [p for p in _posts if p["creator"] == c["id"]]
        views = sum(p["views"] or 0 for p in ps)
        rows.append({
            "id": c["id"], "name": c["name"],
            "state": "recording" if live.active_for(c["id"]) else "waiting" if c["auto_watch"] and c["channel"] else "idle",
            "autopost": c["autopost"] and bool(c["accounts"]),
            "clips_today": sum(1 for k in clips if _day(k.get("made_at", 0)) == today), "clips_total": len(clips),
            "posted_today": sum(1 for p in ps if p["status"] == "posted" and _day(p["posted_at"]) == today),
            "posted_total": sum(1 for p in ps if p["status"] == "posted"),
            "queued": sum(1 for p in ps if p["status"] in OPEN), "failed": sum(1 for p in ps if p["status"] == "failed"),
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2),
            "score_check": score_check(ps)})
    names = {c["id"]: c["name"] for c in all_cs}
    queue = sorted((p for p in _posts if p["status"] in OPEN), key=lambda p: p["due_at"])
    done = sorted((p for p in _posts if p["status"] in ("posted", "failed")), key=lambda p: -(p.get("posted_at") or p["due_at"]))[:15]
    show = [{**{k: v for k, v in p.items() if k != "user"}, "creator_name": names.get(p["creator"], "")} for p in queue + done]
    # last 14 days, oldest first, for the home graph
    days = [_day(time.time() - 86400 * i) for i in range(13, -1, -1)]
    made, posted, views = {d: 0 for d in days}, {d: 0 for d in days}, {d: 0 for d in days}
    for j in all_js:
        for k in j["clips"]:
            d = _day(k.get("made_at") or j["created"])
            if k.get("status") == "done" and d in made:
                made[d] += 1
    for p in _posts:
        if p["status"] == "posted" and _day(p["posted_at"]) in posted:
            posted[_day(p["posted_at"])] += 1
            views[_day(p["posted_at"])] += p["views"] or 0
    daily = [{"day": d, "clips": made[d], "posted": posted[d], "views": views[d]} for d in days]
    return {"paused": accounts.settings()["paused"], "creators": rows, "posts": show, "daily": daily,
            "alerts": alerts.configured(), "recording": len(live.active_mine())}


def digest_text() -> str:
    o = overview()
    lines = ["PeakCut daily summary"]
    for r in o["creators"]:
        lines.append(f"- {r['name']}: {r['state']}, {r['clips_today']} clips today, {r['posted_today']} posted today, "
                     f"{r['queued']} waiting, {r['failed']} failed, {r['views']} views")
    if not o["creators"]:
        lines.append("No creators set up yet.")
    if o["paused"]:
        lines.append("Posting is paused.")
    return "\n".join(lines)


def start():
    _load()
    with _lock:
        for p in _posts:
            if p["status"] == "posting":  # interrupted mid-upload last time
                p.update(status="failed", error="The studio closed during this upload. Check the account, then retry.")
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
