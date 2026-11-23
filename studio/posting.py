"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import math
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import json

from . import accounts, alerts, config, creators, ctx, jobs, live

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting")
SCHEDULE_FIELDS = ("post_gap_min", "daily_cap_enabled", "daily_cap", "post_window_enabled", "post_start_time", "post_end_time")


def _save():
    with _lock:
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_posts, indent=1), encoding="utf-8")
        tmp.replace(_FILE)


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


def _window(c):
    return (c["post_start_time"], c["post_end_time"]) if c.get("post_window_enabled") else None


def _bounds(t, window):
    day = datetime.fromtimestamp(t).date()
    return tuple(datetime.combine(day, datetime.strptime(s, "%H:%M").time()).timestamp() for s in window)


def _next_day(t):
    day = datetime.fromtimestamp(t).date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).timestamp()


def _next_slot(account_id, gap_min, cap=None, window=None, *, earliest=0, exclude=()):
    """Reserve the next time for this account. A window and daily limit divide the day into equal slots."""
    mine = [p for p in _mine() if p["account"] == account_id and p["status"] in OPEN + ("posted",)
            and p["id"] not in exclude]
    last = max((p.get("posted_at") or p["due_at"] for p in mine), default=0)
    t = max(time.time(), earliest, last + (0.001 if window and cap else gap_min * 60))
    while True:
        if window:
            start, end = _bounds(t, window)
            t = max(t, start)
            if t >= end:
                t = _next_day(t)
                continue
            if cap:
                step = (end - start) / cap
                index = math.ceil((t - start) / step - 1e-9)
                if index >= cap:
                    t = _next_day(t)
                    continue
                t = start + index * step
        if cap is None or sum(1 for p in mine if _day(p.get("posted_at") or p["due_at"]) == _day(t)) < cap:
            return t
        t = _next_day(t)


def _scheduled_at(account_id, c, *, earliest=0, exclude=()):
    cap = c["daily_cap"] if c.get("daily_cap_enabled", False) else None
    return _next_slot(account_id, c["post_gap_min"], cap, _window(c), earliest=earliest, exclude=exclude)


def _reschedule_accounts(account_ids):
    """Keep waiting clips in order when times change or the app missed a window."""
    with _lock:
        pending = sorted((p for p in _mine() if p["account"] in account_ids and p["status"] == "scheduled"
                          and not p.get("immediate")), key=lambda p: (p["due_at"], p["created"]))
        excluded = {p["id"] for p in pending}
        changed = []
        for p in pending:
            c = creators.get(p["creator"]) or creators.DEFAULTS
            due = _scheduled_at(p["account"], c, earliest=p.get("retry_at", 0), exclude=excluded)
            if due != p["due_at"]:
                p["due_at"] = due
                changed.append(p)
            excluded.remove(p["id"])
        if changed:
            _save()
            for p in changed:
                _mirror(p)
            ctx.bump()


def reschedule(creator_id):
    with _lock:
        affected = {p["account"] for p in _mine() if p["creator"] == creator_id and p["status"] == "scheduled"}
        _reschedule_accounts(affected)


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
            due = time.time() if now else _scheduled_at(aid, c)
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None,
                 "immediate": bool(now)}
            _posts.append(p)
            made.append(p)
        _save()
    for p in made:
        _mirror(p)
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
    tags = " ".join("#" + h for h in clip.get("hashtags", []))
    return "\n\n".join(x for x in (clip.get("description", ""), tags, creator.get("post_footer", "")) if x)


def _post(p):
    job = jobs.get(p["job"])
    clip = next((c for c in job["clips"] if c["id"] == p["clip"]), None) if job else None
    acc = accounts.get(p["account"])
    try:
        if not clip or clip.get("status") != "done":
            raise RuntimeError("The clip no longer exists or is not finished.")
        if not acc:
            raise RuntimeError("That account was disconnected.")
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
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc and acc["platform"] != "youtube":
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
            p["retry_at"] = p["due_at"]
            c = creators.get(p["creator"]) or creators.DEFAULTS
            if _window(c) and not p.get("immediate"):
                with _lock:
                    p["due_at"] = _scheduled_at(p["account"], c, earliest=p["retry_at"], exclude={p["id"]})
        else:
            p["status"] = "failed"
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _window_available(p, c, now):
    """Check actual sends too: a paused or stopped app may have missed its planned times."""
    window = _window(c)
    if not window or p.get("immediate"):
        return True
    start, end = _bounds(now, window)
    if not start <= now < end:
        return False
    sent = [q for q in _mine() if q["id"] != p["id"] and q["account"] == p["account"]
            and q["status"] in ("posted", "posting")]
    stamps = [q.get("posted_at") or q["due_at"] for q in sent]
    if c.get("daily_cap_enabled"):
        cap = c["daily_cap"]
        if sum(_day(t) == _day(now) for t in stamps) >= cap:
            return False
        step = (end - start) / cap
        slot_start = start + math.floor((now - start) / step) * step
        return not any(slot_start <= t < end for t in stamps)
    return all(t + c["post_gap_min"] * 60 <= now for t in stamps)


def _run_due():
    due = sorted((p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time()
                  and p.get("user")), key=lambda p: p["due_at"])
    for p in due:
        try:
            with ctx.as_user(p["user"]):
                if accounts.settings()["paused"]:
                    continue
                with _lock:
                    if p["status"] != "scheduled" or p["due_at"] > time.time():
                        continue
                    c = creators.get(p["creator"]) or creators.DEFAULTS
                    if not _window_available(p, c, time.time()):
                        _reschedule_accounts({p["account"]})
                        continue
                    p["status"] = "posting"
                _post(p)
        except Exception:  # noqa: BLE001
            pass


def _run():
    while True:
        time.sleep(15)
        _run_due()


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
        c = creators.get(p["creator"]) or creators.DEFAULTS
        due = _scheduled_at(p["account"], c, exclude={p["id"]}) if _window(c) and not p.get("immediate") else time.time()
        p.update(status="scheduled", due_at=due, tries=0, error=None)
        p.pop("retry_at", None)
        _save()
    _mirror(p)


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
            "views": views, "earnings": round(views / 1000 * c["rate_per_1k"], 2)})
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
