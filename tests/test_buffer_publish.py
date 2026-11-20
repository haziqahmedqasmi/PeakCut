"""No external traffic: exercise hosting, attribution and asynchronous publishing."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, ".")


def check(name, condition):
    assert condition, name
    print("PASS " + name)


def rejects(fn, kind=ValueError):
    try:
        fn()
    except kind:
        return True
    return False


with tempfile.TemporaryDirectory(prefix="pc-buffer-") as temp:
    os.environ.update(PEAKCUT_DATA=temp, DATABASE_URL="", PEAKCUT_ACCOUNTS="0",
                      PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0")
    from studio import accounts, auth, buffer_publish as bp, creators, ctx, db, jobs, posting
    db.init()
    with ctx.as_user(auth.local_user().id):
        s = bp.save_settings({"buffer_key": "private-buffer-key", "cloud_key": "123456", "cloud_secret": "private-cloud-secret",
                              "cloud_name": "demo", "hashtags": "#LACY #GTA6 #GTAVI #GTA6Online #GTA5Online"})
        check("settings never return API credentials", s["buffer_key"] is True and s["cloud_secret"] is True and s["cloud_key"] is True)
        bp.save_settings({"buffer_key": "", "cloud_secret": ""})
        check("blank secret fields keep saved credentials", bp.settings()["buffer_key"] == "private-buffer-key")
        check("credentials are saved with private permissions", (accounts._dir() / "buffer.json").stat().st_mode & 0o777 == 0o600)
        check("a sixth TikTok hashtag is rejected", rejects(lambda: bp.save_settings({"hashtags": "#a #b #c #d #e #f"})))
        with ctx.as_user("another_user"):
            check("another user cannot read the saved credentials", not bp.settings()["buffer_key"])
        with patch.object(bp, "channels", return_value=[{"id": "real", "name": "TikTok channel", "organization": "org"}]):
            check("an unowned channel cannot be connected", rejects(lambda: bp.connect("unowned")))
            bp.connect("real")
        acc = accounts.get("buffer_real")
        check("Buffer is stored as a TikTok publishing destination", acc["platform"] == "tiktok" and acc["publisher"] == "buffer")
        c = creators.create({"name": "Lacy", "credit_name": "LACY", "credit_handle": "@LacyHimself", "credit_tiktok_handle": "@lacy",
                             "credit_hashtag": "#LACY", "post_hashtags": "#GTA6 #SomeOtherRequiredYoutubeTag",
                             "game": "GTA 6", "autopost": True, "accounts": [acc["id"]],
                             "live_clip_interval_sec": 30, "post_gap_seconds": 30, "daily_cap": 0})
        clip = {"id": "clip0001", "title": "Lacy plays #Gaming", "description": "A highlight #extra.", "hashtags": ["#more"],
                "status": "done", "file": "clip0001.mp4", "score": 99, "rendered_credit_name": "LACY", "auto_interval": True}
        text = bp.caption(clip, c)
        check("TikTok keeps exactly the selected five tags and creator credit", text.count("#") == 5 and "@lacy" in text and "Credit: LACY" in text and "#SomeOtherRequiredYoutubeTag" not in text)
        bp.save_settings({"hashtags": "#a #b #c #d #e"})
        check("a missing mandatory creator hashtag cannot be silently dropped", rejects(lambda: bp.caption(clip, c)))
        bp.save_settings({"hashtags": "#LACY #GTA6 #GTAVI #GTA6Online #GTA5Online"})
        job = jobs.create({"type": "file", "name": "GTA 6 sample"}, {}, enqueue=False, creator=c["id"])
        job["clips"] = [clip]
        jobs.save(job)
        path = Path(job["dir"]) / "clips" / clip["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mock video")
        with patch.object(posting.alerts, "notify"):
            p = posting.enqueue(job, clip, [acc["id"]])[0]
            second = dict(clip, id="clip0002")
            check("live generation holds at most one pending TikTok clip", posting.enqueue(job, second, [acc["id"]]) == [])
            with patch.object(bp, "host_video") as host:
                creators.update(c["id"], {"credit_tiktok_handle": ""})
                posting._post(p)
                check("credit is checked before any video leaves the Mac", p["status"] == "failed" and not host.called)
            creators.update(c["id"], {"credit_tiktok_handle": "@lacy"})
            posting.retry(p["id"])
            with patch.object(bp, "host_video", return_value="https://res.cloudinary.com/demo/video/upload/clip.mp4") as host, patch.object(bp, "publish", return_value={"id": "remote", "status": "sending"}) as publish:
                posting._post(p)
                check("Buffer acceptance waits for publication confirmation", p["status"] == "publishing" and p["remote_id"] == "remote" and not p.get("posted_at"))
                check("hosted URL and credited caption reach the publisher", host.call_count == 1 and "@lacy" in publish.call_args.args[2] and p["media_url"] == publish.call_args.args[1])
            with patch.object(bp, "status", return_value={"id": "remote", "status": "sent", "externalLink": "https://www.tiktok.com/@channel/video/123"}):
                posting._poll_buffer(p)
            check("only confirmed sent posts become posted", p["status"] == "posted" and p["url"].startswith("https://www.tiktok.com/"))
            p2 = posting.enqueue(job, second, [acc["id"]], now=True)[0]
            check("TikTok pacing remains separate from 30 second YouTube pacing", p2["due_at"] >= p["posted_at"] + 179 * 60)
            posting.cancel(p2["id"])
            job["clips"].append(second)
            p3 = posting.enqueue(job, second, [acc["id"]])[0]
            with patch.object(bp, "host_video", return_value=p["media_url"]), patch.object(bp, "publish", side_effect=bp.PublishError("Response lost")) as publish:
                posting._post(p3)
            check("an uncertain create response is never automatically resubmitted", p3["status"] == "failed" and publish.call_count == 1 and p3["buffer_attempted"])
            check("an uncertain submission blocks local retry to prevent duplicates", rejects(lambda: posting.retry(p3["id"])))
            pending = dict(p, id="status-only", status="publishing", checks=0)
            with patch.object(bp, "status", side_effect=RuntimeError("Unavailable")), patch.object(bp, "publish") as publish:
                posting._poll_buffer(pending)
            check("status failure backs off without creating another post", pending["status"] == "publishing" and pending["next_check"] > time.time() + 50 and not publish.called)
            pending2 = dict(pending)
            posting._buffer_result(pending2, {"status": "error"})
            check("a Buffer publishing error is shown as failed", pending2["status"] == "failed")
        response = httpx.Response(200, json={"secure_url": "https://res.cloudinary.com/demo/video/upload/clip.mp4"})
        with patch.object(bp.httpx, "post", return_value=response) as post:
            url = bp.host_video(path, "hosting-test")
            call = post.call_args.kwargs
            check("Cloudinary receives a signed upload without exposing its secret", len(call["data"]["signature"]) == 64 and "cloud_secret" not in call["data"] and "private-cloud-secret" not in str(call["data"]))
            check("stable public hosting URL is returned", url.startswith("https://res.cloudinary.com/"))
        with patch.object(bp.httpx, "post", return_value=httpx.Response(200, json={"secure_url": "http://localhost/video.mp4"})):
            check("nonpublic hosting URLs are rejected", rejects(lambda: bp.host_video(path, "bad-url")))
        accounts._write("buffer-hosting-usage.json", {"month": time.strftime("%Y-%m", time.gmtime()), "bytes": 1000 * 1024 * 1024})
        with patch.object(bp.httpx, "post") as post:
            check("hosting budget stops traffic before upload", rejects(lambda: bp.host_video(path, "over-budget")) and not post.called)
        with patch.object(bp.httpx, "post", side_effect=httpx.ReadTimeout("private-buffer-key")):
            try:
                bp.publish(acc, url, text)
            except bp.PublishError as e:
                check("ambiguous remote errors do not expose credentials", "private-buffer-key" not in str(e) and "may already exist" in str(e))
        with patch.object(bp, "_graphql", return_value={"createPost": {"post": {"id": "post", "status": "sending"}}}) as gql:
            bp.publish(acc, url, text)
            payload = gql.call_args.args[1]["input"]
            check("API request selects automatic publishing, not a phone reminder", payload["schedulingType"] == "automatic" and payload["mode"] == "shareNow" and payload["needsApproval"] is False)
        from fastapi.testclient import TestClient
        from studio import config, server
        client = TestClient(server.app, base_url=config.PUBLIC_URL)
        result = client.get("/api/buffer")
        check("settings API shows saved state without leaking secret values", result.status_code == 200 and result.json()["buffer_key"] is True and "private-buffer-key" not in result.text)
        check("another website cannot overwrite integration credentials", client.put("/api/buffer", json={"buffer_key": "attacker"}, headers={"Origin": "https://evil.example"}).status_code == 403 and bp.settings()["buffer_key"] == "private-buffer-key")
        check("settings API refuses too many TikTok hashtags", client.put("/api/buffer", json={"hashtags": "#a #b #c #d #e #f"}, headers={"Origin": config.PUBLIC_URL}).status_code == 400)
        accounts.save_settings({"paused": False, "paused_platforms": ["youtube", "unknown", "youtube"]})
        check("platform pauses are validated and deduplicated", accounts.settings()["paused_platforms"] == ["youtube"])
        check("invalid pause settings are rejected", rejects(lambda: accounts.save_settings({"paused_platforms": "youtube"})))
        check("the settings API exposes the platform pause", client.get("/api/settings").json()["paused_platforms"] == ["youtube"])
        yt = {"id": "paused-youtube", "account": "youtube-test", "platform": "youtube", "user": ctx.uid(), "status": "scheduled", "due_at": time.time()-60}
        tt = {"id": "active-tiktok", "account": acc["id"], "platform": "tiktok", "user": ctx.uid(), "status": "scheduled", "due_at": time.time()-60}
        def one_tick():
            with patch.object(posting.time, "sleep", side_effect=[None, StopIteration]):
                try:
                    posting._run()
                except StopIteration:
                    pass
        with patch.object(posting, "_posts", [yt, tt]), patch.object(posting, "_post", side_effect=lambda p: p.update(status="posted")) as upload:
            one_tick()
            check("the real dispatch loop leaves YouTube queued while publishing TikTok", yt["status"] == "scheduled" and tt["status"] == "posted" and upload.call_count == 1)
            accounts.save_settings({"paused": True})
            tt["status"] = "scheduled"
            upload.reset_mock()
            one_tick()
            check("the global pause still blocks every platform", tt["status"] == "scheduled" and yt["status"] == "scheduled" and not upload.called)
        with ctx.as_user("another_user"):
            check("platform pauses are private to each user", accounts.settings()["paused_platforms"] == [])
        with patch.object(posting, "_posts", []):
            first = posting.enqueue(job, clip, [acc["id"]])[0]
            check("archive support keeps the automatic one-clip queue limit", posting.enqueue(job, second, [acc["id"]]) == [])
            extra = posting.enqueue(job, second, [acc["id"]], backlog=True)
            check("an explicitly requested archive queues more clips with TikTok pacing", len(extra) == 1 and extra[0]["due_at"] >= first["due_at"] + 180*60)
            check("repeating an archive request does not duplicate clips", posting.enqueue(job, second, [acc["id"]], backlog=True) == [])
            first.update(status="failed", buffer_attempted=True)
            check("archive requests never resubmit an ambiguous Buffer upload", posting.enqueue(job, clip, [acc["id"]], backlog=True) == [])
        bp.save_settings({"gap_minutes":0,"daily_cap":15})
        check("zero optional spacing is supported", bp.settings()["gap_minutes"] == 0)
        bp.save_settings({"daily_cap":100})
        check("the TikTok rolling cap cannot exceed 15", bp.settings()["daily_cap"] == 15)
        now = time.time()
        cap_settings = dict(bp.settings(), daily_cap=2, gap_minutes=0)
        check("two recent posts hold the next one for a full rolling day", posting._buffer_slot([now-600, now-300], cap_settings, now) >= now-600+86401)
        check("expired submissions release rolling capacity", posting._buffer_slot([now-90000, now-300], cap_settings, now) == now)
        check("midnight does not reset a rolling cap", posting._buffer_slot([86300, 86350], cap_settings, 86500) >= 172701)
        accounts.save_settings({"paused": False, "paused_platforms": ["youtube"]})
        one = dict(p, id="one-in-flight", status="scheduled", due_at=now-120)
        two = dict(p, id="next-upload", status="scheduled", due_at=now-60)
        for row in (one, two):
            for key in ("submitted_at", "posted_at", "buffer_attempted", "remote_id"):
                row.pop(key, None)
        def accept(row):
            row.update(status="publishing", buffer_attempted=True, submitted_at=time.time(), remote_id="accepted")
        with patch.object(posting, "_posts", [one, two]), patch.object(posting, "_post", side_effect=accept) as upload:
            one_tick()
            check("overdue clips upload one at a time even with no extra spacing", upload.call_count == 1 and one["status"] == "publishing" and two["status"] == "scheduled")
            check("the waiting clip is kept instead of failed or discarded", two["due_at"] >= now+29)
            one.update(status="posted", posted_at=time.time())
            two["due_at"] = now-1
            one_tick()
            check("the next clip starts when the first publication is confirmed", upload.call_count == 2 and two["status"] == "publishing")
        history = [dict(p, id="past-a", status="posted", submitted_at=now-600, buffer_attempted=True),
                   dict(p, id="past-b", status="failed", submitted_at=now-300, buffer_attempted=True)]
        waiting = dict(two, id="after-offline", status="scheduled", due_at=now-30)
        for key in ("submitted_at", "posted_at", "buffer_attempted", "remote_id"):
            waiting.pop(key, None)
        bp.save_settings({"daily_cap":2})
        with patch.object(posting, "_posts", history + [waiting]), patch.object(posting, "_post") as upload:
            one_tick()
            check("the dispatch loop enforces the rolling cap on overdue queues", not upload.called and waiting["due_at"] >= now-600+86401)
            check("an uncertain prior submission still counts toward the cap", waiting["status"] == "scheduled")
        with patch.object(posting, "_posts", []):
            bp.save_settings({"gap_minutes":180,"daily_cap":8})
            records=[]
            for i in range(4):
                records += posting.enqueue(job, dict(clip, id="archive-"+str(i)), [acc["id"]], backlog=True)
            records[0].update(status="posted", submitted_at=now-300, posted_at=now-280, buffer_attempted=True, url="https://www.tiktok.com/@test/video/1")
            ids = [r["id"] for r in records]
            result = client.put("/api/buffer", json={"gap_minutes":0,"daily_cap":2}, headers={"Origin":config.PUBLIC_URL})
            check("saving a new pace reschedules every waiting archive clip", result.status_code == 200 and result.json()["rescheduled"] == 3 and [r["id"] for r in records] == ids)
            check("pace changes leave the published clip untouched", records[0]["url"].endswith('/video/1') and records[0]["status"] == "posted")
            check("zero spacing releases the next waiting clip promptly", records[1]["due_at"] < time.time()+3)
            check("remaining archive clips wait for rolling capacity", records[2]["due_at"] >= now-300+86401)
            check("pacing changes preserve the YouTube pause", accounts.settings()["paused_platforms"] == ["youtube"])
            posting._buffer_result(records[1], {"status":"error"})
            check("a publishing rejection pauses TikTok and preserves other pauses", set(accounts.settings()["paused_platforms"]) == {"youtube","tiktok"})
        pending = dict(p, status="publishing", checks=3, status_failures=0)
        with patch.object(bp, "status", return_value={"status":"sending"}):
            posting._poll_buffer(pending)
        check("healthy processing keeps short status checks", 50 < pending["next_check"]-time.time() < 70)
        with patch.object(bp, "status", side_effect=RuntimeError("offline")):
            posting._poll_buffer(pending)
            posting._poll_buffer(pending)
        check("repeated network failures still back off", pending["next_check"] > time.time()+590)
        with patch.object(bp, "status", return_value={"status":"sending"}):
            posting._poll_buffer(pending)
        check("a healthy response restores fast checks", pending["status_failures"] == 0 and 50 < pending["next_check"]-time.time() < 70)
        pending.update(next_check=time.time()+3600, remote_id="already-created")
        with patch.object(posting, "_posts", [pending]), patch.object(posting.threading, "Thread"), patch.object(jobs, "on_clip_ready", []), patch.object(bp, "publish") as publish:
            posting.start()
        check("restart checks accepted posts promptly without submitting again", pending["status"] == "publishing" and pending["remote_id"] == "already-created" and pending["next_check"] <= time.time()+30 and not publish.called)
    check("tests never used the real installation's data", str(posting._FILE).startswith(temp))
