"""Post queue: decides when each finished clip goes to which account, posts it, and tracks views."""
import secrets
import threading
import time
from pathlib import Path

import json

from . import accounts, alerts, buffer_publish, config, creators, ctx, games, jobs, live, post_text

_lock = threading.RLock()
_FILE = config.DATA_DIR / "posts.json"
try:
    _posts: list = json.loads(_FILE.read_text(encoding="utf-8")) if _FILE.exists() else []
except ValueError:
    _posts = []


def _mine() -> list:
    u = ctx.uid()
    return [p for p in _posts if p.get("user") == u]
OPEN = ("scheduled", "posting", "publishing")


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


def _next_slot(account_id, gap_min, cap, gap_seconds=None):
    """Earliest time that respects the gap between posts and the daily cap for this account."""
    mine = [p for p in _mine() if p["account"] == account_id and (p["status"] in OPEN + ("posted",) or p.get("buffer_attempted") or (p.get("buffer_destination") and p["status"] == "failed"))]
    stamp = lambda p: p.get("posted_at") or p.get("submitted_at") or p["due_at"]
    t = max(time.time(), max((stamp(p) for p in mine), default=0) + (gap_min * 60 if gap_seconds is None else gap_seconds))
    for _ in range(30):
        if cap == 0 or sum(1 for p in mine if _day(stamp(p)) == _day(t)) < cap:
            break
        lt = time.localtime(t + 86400)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))  # 9:00 next day
    return t


def _buffer_events(account_id, planned=False, exclude=None):
    events = []
    for p in _mine():
        if p["account"] != account_id or p["id"] == exclude:
            continue
        if p.get("buffer_attempted") or p["status"] in ("posting", "publishing", "posted"):
            events.append(p.get("submitted_at") or p.get("posted_at") or p["due_at"])
        elif p["status"] == "failed" and p.get("buffer_destination"):
            events.append(p.get("submitted_at") or p["due_at"])
        elif planned and p["status"] == "scheduled":
            events.append(p["due_at"])
    return events


def _buffer_slot(events, settings, now=None):
    """Enforce a rolling 24-hour cap, including across midnight and restarts."""
    now = time.time() if now is None else now
    cap = max(1, min(15, int(settings["daily_cap"])))
    gap = max(0, settings["gap_minutes"]) * 60
    t = max(now, max(events, default=now - gap) + gap)
    if len(events) >= cap:
        t = max(t, sorted(events)[-cap] + 86401)
    return t


def replan_buffer_queue():
    """Apply a pace change to waiting clips without recreating any post."""
    changed = []
    settings = buffer_publish.settings()
    ids = {a["id"] for a in accounts._all() if a.get("publisher") == "buffer"}
    with _lock:
        for aid in ids:
            events = _buffer_events(aid)
            waiting = sorted((p for p in _mine() if p["account"] == aid and p["status"] == "scheduled"),
                             key=lambda p: (p["due_at"], p["created"], p["id"]))
            for p in waiting:
                p["due_at"] = _buffer_slot(events, settings)
                events.append(p["due_at"])
                changed.append(p)
        if changed:
            _save()
    for p in changed:
        _mirror(p)
    ctx.bump()
    return len(changed)


def _buffer_ready_at(p):
    now = time.time()
    if any(q["id"] != p["id"] and q["account"] == p["account"] and q["status"] in ("posting", "publishing") for q in _mine()):
        return now + 30
    return _buffer_slot(_buffer_events(p["account"], exclude=p["id"]), buffer_publish.settings(), now)


def _pause_tiktok():
    settings = accounts.settings()
    accounts.save_settings({"paused_platforms": list(dict.fromkeys([*settings["paused_platforms"], "tiktok"]))})


def enqueue(job, clip, account_ids, now=False, backlog=False) -> list:
    c = creators.get(job["creator"]) or creators.DEFAULTS
    made = []
    with _lock:
        for aid in account_ids:
            acc = accounts.get(aid)
            if not acc:
                continue
            via_buffer = acc.get("publisher") == "buffer"
            if via_buffer and not backlog and any(p["account"] == aid and p["status"] in OPEN for p in _mine()):
                continue  # one pending TikTok clip prevents a growing live-stream backlog
            if any(p["job"] == job["id"] and p["clip"] == clip["id"] and p["account"] == aid
                   and (p["status"] in OPEN + ("posted",) or p.get("buffer_attempted")) for p in _mine()):
                continue  # never post the same clip to the same account twice
            if via_buffer:
                s = buffer_publish.settings()
                due = _buffer_slot(_buffer_events(aid, planned=True), s)
            else:
                due = time.time() if now else _next_slot(aid, c["post_gap_min"], c["daily_cap"], c.get("post_gap_seconds") or None)
            p = {"id": secrets.token_hex(8), "user": ctx.uid(), "job": job["id"], "clip": clip["id"], "creator": job["creator"],
                 "account": aid, "account_name": acc["name"], "platform": acc["platform"], "status": "scheduled",
                 "due_at": due, "created": time.time(), "title": clip.get("title") or clip.get("hook") or "Clip",
                 "score": clip.get("score", 0), "tries": 0, "url": "", "remote_id": "", "error": None, "views": None}
            if via_buffer:
                p["buffer_destination"] = True
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
    approved = clip.get("quality_review", {})
    if c.get("live_quality_mode") and not (approved.get("approved") is True and approved.get("version") == 1
                                          and clip.get("score", 0) >= 90):
        return  # old interval clips and manual cuts need a separate, explicit posting decision
    interval_clip = clip.get("auto_interval") and c.get("live_clip_interval_sec", 0) > 0
    if clip.get("score", 0) < c["post_min_score"] and not interval_clip:
        return  # includes hand-made clips (score 0): those wait for you to press Post
    made = enqueue(job, clip, c["accounts"])
    if made:
        creators.log(c["id"], f"Queued for {len(made)} account(s): {clip.get('title') or clip.get('hook')}")


def _caption(clip, creator):
    # A description the person wrote themselves is posted as it is, without the AI's hashtags added to it, and
    # one that already ends with its hashtags does not get them twice.
    tags = "" if clip.get("fixed_description") or clip.get("hashtags_in_description") else " ".join("#" + h for h in clip.get("hashtags", []))
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
        title = clip.get("title") or clip.get("hook") or "Clip"
        game = job.get("game") or creator.get("game") or games.detect(job.get("title", ""))[0] or ""
        if acc.get("publisher") == "buffer":
            cap = buffer_publish.caption(clip, creator, game)
            if creator["privacy"] != "public":
                raise buffer_publish.PublishError("Buffer TikTok publishing is public. Choose a public creator or disconnect this destination.")
            if not p.get("media_url"):
                p["media_url"] = buffer_publish.host_video(path, p["id"])
                _save()
            # Persist an uncertain state before the network mutation. If the studio
            # closes after Buffer accepts it, startup will not submit a duplicate.
            p.update(status="posting", buffer_attempted=True, submitted_at=time.time(),
                     error="Check Buffer before retrying this interrupted submission.")
            _save()
            remote = buffer_publish.publish(acc, p["media_url"], cap)
            p.update(remote_id=remote["id"], status="publishing", submitted_at=time.time(),
                     next_check=time.time() + 30, checks=0, error=None)
            _buffer_result(p, remote)
            _save()
            _mirror(p)
            return
        cap, tags = post_text.compose(clip, creator, acc["platform"], game)
        if acc["platform"] == "youtube":
            rid, url = accounts.youtube_upload(acc, path, title, cap, tags, creator["privacy"])
        elif acc["platform"] == "instagram":
            rid, url = accounts.instagram_upload(acc, path, cap, job["id"])
        elif acc["platform"] == "tiktok":
            rid, url = accounts.tiktok_upload(acc, path, cap, creator["privacy"])
        else:
            raise RuntimeError("That kind of account is no longer supported.")
        p.update(status="posted", posted_at=time.time(), remote_id=rid, url=url, error=None)
        creators.log(p["creator"], f"Posted to {acc['name']}: {title}")
    except Exception as e:  # noqa: BLE001
        p["tries"] += 1
        p["error"] = str(e)[-300:]
        if p["tries"] < 3 and clip and acc and acc.get("publisher") != "buffer" and not isinstance(e, (post_text.CreditError, buffer_publish.PublishError)):
            p.update(status="scheduled", due_at=time.time() + 600)  # try again in 10 minutes
        else:
            p["status"] = "failed"
            if acc and acc.get("publisher") == "buffer" and isinstance(e, buffer_publish.PublishError):
                _pause_tiktok()
            creators.log(p["creator"], f"Post failed on {p['account_name']}: {p['error']}")
            alerts.notify(f"post failed on {p['account_name']} ({p['title']}): {p['error'][:200]}", kind="post")
    _save()
    _mirror(p)


def _buffer_result(p, remote):
    if remote.get("status") == "sent":
        p.update(status="posted", posted_at=time.time(), url=remote.get("externalLink") or "", error=None)
        creators.log(p["creator"], f"Posted to {p['account_name']}: {p['title']}")
    elif remote.get("status") in ("error", "draft", "needs_approval"):
        p.update(status="failed", error="TikTok posting paused: Buffer needs attention. Resolve this post in its dashboard before resuming.")
        _pause_tiktok()
        creators.log(p["creator"], f"Buffer needs attention: {p['title']}")


def _poll_buffer(p):
    acc = accounts.get(p["account"])
    if not acc or acc.get("publisher") != "buffer":
        p.update(status="failed", error="Account disconnected. Check Buffer: its accepted post may still publish.")
    else:
        try:
            _buffer_result(p, buffer_publish.status(p["remote_id"]))
            p["status_failures"] = 0
            if p["status"] == "publishing":
                p["error"] = None
        except Exception:
            p["status_failures"] = p.get("status_failures", 0) + 1
            p["error"] = "Waiting for Buffer's publication status. Check its dashboard if this continues."
        p["checks"] = p.get("checks", 0) + 1
        failures = p.get("status_failures", 0)
        delays = [60, 600, 3600, 21600]
        delay = delays[min(failures - 1, len(delays) - 1)] if failures else 60
        p["next_check"] = time.time() + delay
    _save()
    _mirror(p)


def _run():
    while True:
        time.sleep(15)
        for p in list(_posts):
            if p["status"] == "publishing" and p.get("user") and p.get("next_check", 0) <= time.time():
                try:
                    with ctx.as_user(p["user"]):
                        _poll_buffer(p)
                except Exception:
                    pass
        due = [p for p in list(_posts) if p["status"] == "scheduled" and p["due_at"] <= time.time() and p.get("user")]
        for p in due:
            try:
                with ctx.as_user(p["user"]):
                    settings = accounts.settings()
                    if settings["paused"] or p["platform"] in settings["paused_platforms"]:
                        continue
                    with _lock:
                        if p["status"] != "scheduled":
                            continue
                        acc = accounts.get(p["account"])
                        if acc and acc.get("publisher") == "buffer":
                            ready = _buffer_ready_at(p)
                            if ready > time.time() + 0.5:
                                p["due_at"] = ready
                                _save()
                                _mirror(p)
                                continue
                        p["status"] = "posting"
                    _post(p)
            except Exception:  # noqa: BLE001
                pass


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
        acc = accounts.get(p["account"])
        if acc and acc.get("publisher") == "buffer" and p.get("buffer_attempted"):
            raise ValueError("This post was submitted to Buffer. Check or retry it there to avoid publishing a duplicate.")
        if acc and acc.get("publisher") == "buffer":
            s = buffer_publish.settings()
            p["status"] = "failed"
            due = _buffer_slot(_buffer_events(p["account"], planned=True, exclude=p["id"]), s)
        else:
            due = time.time()
        p.update(status="scheduled", due_at=due, tries=0, error=None)
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
    return {"paused": accounts.settings()["paused"], "paused_platforms": accounts.settings()["paused_platforms"], "creators": rows, "posts": show, "daily": daily,
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
            elif p["status"] == "publishing":
                p["next_check"] = min(p.get("next_check", 0), time.time() + 30)
        _save()
    jobs.on_clip_ready.append(on_clip_ready)
    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_stats, daemon=True).start()
    threading.Thread(target=alerts.digest_loop, args=(digest_text,), daemon=True).start()
    threading.Thread(target=alerts.keep_awake_loop,
                     args=(lambda: bool(live.active()) or any(p["status"] in OPEN for p in _posts),), daemon=True).start()
