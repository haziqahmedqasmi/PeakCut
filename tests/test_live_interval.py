"""Check fixed interval boundaries, automatic posting, and the rendered credit guard."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")

def check(name, condition):
    assert condition, name
    print("PASS " + name)

with tempfile.TemporaryDirectory(prefix="pc-live-interval-") as temp:
    os.environ.update(PEAKCUT_DATA=temp, DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0")
    from studio import auth, brain, creators, ctx, db, jobs, live, post_text, posting, render
    db.init()
    with ctx.as_user(auth.local_user().id):
        creator = creators.create({"name": "Lacy interval test", "live_clip_interval_sec": 30, "credit_name": "LACY",
                                   "credit_handle": "@LacyHimself", "credit_tiktok_handle": "@lacy", "credit_hashtag": "#LACY",
                                   "autopost": True, "accounts": ["test-channel"], "post_gap_seconds": 30, "daily_cap": 0})
        job = jobs.create({"type": "file", "name": "Live test", "url": ""}, {}, enqueue=False, creator=creator["id"], kind="live")
        job.update(title="Live GTA marathon", uploader="Lacy", ai={})
        session = live.Session(job, creator)
        session.t_done, session.samples = 29.9, 96 * live.SR
        session._interval_step()
        check("a partial interval waits for its full 30 seconds", not session.manual)
        session.t_done = 95
        session._interval_step()
        check("three full intervals have contiguous 30-second boundaries",
              [(r["start"], r["end"]) for r in session.manual] == [(0, 30), (30, 60), (60, 90)])
        session._interval_step()
        check("polling again cannot duplicate an interval", len(session.manual) == 3)
        session._triggers([{"w": "clip", "s": 15, "e": 16}, {"w": "it", "s": 16, "e": 17}])
        check("interval mode does not also create spoken-trigger clips", len(session.manual) == 3 and session.said is None)
        with patch.object(brain, "describe", return_value={"title": "AI suggested title", "hook": "AI hook", "description": "AI caption", "hashtags": [], "score": 8}), patch.object(session, "_add_clip") as add:
            session._manual_step()
            made = [call.args[0] for call in add.call_args_list]
            check("every interval receives an AI title and caption without a highlight selection gate",
                  len(made) == 3 and all(c["title"] == "AI suggested title" and c["description"] == "AI caption" and c["auto_interval"] for c in made))
        session.user_stopped, session.t_done = True, 125
        session._interval_step()
        check("stopping a recording stops the automatic interval scheduler", not session.manual)
        session = live.Session(dict(job, interval_clip_end=90), creator)
        session.t_done = 125
        session._interval_step()
        check("a saved interval cursor resumes without backfilling completed intervals", [(r["start"], r["end"]) for r in session.manual] == [(90, 120)])

        clip = {"id": "credit001", "start": 0, "end": 30, "score": 0, "auto_interval": True,
                "title": "AI title", "description": "AI caption", "hook": "A big moment", "hashtags": [],
                "captions": False, "status": "queued", "style": "clean", "layout": "crop"}
        job.update(source_path=str(Path(temp) / "source.mp4"), width=640, height=360, duration=30, clips=[clip])
        with patch.object(render, "render_clip", return_value={"file": "credit001.mp4", "cover": "credit001.jpg"}) as encode, patch.object(posting, "enqueue") as enqueue:
            jobs.render_one(job, clip, [])
            check("the encoder receives LACY in the visible heading even with speech captions off",
                  encode.call_args.args[3]["hook"].startswith("LACY | ") and clip["rendered_credit_name"] == "LACY")
            posting.on_clip_ready(job, clip)
            check("interval clips queue automatically even with a zero highlight score", enqueue.called)
        text, _ = post_text.compose(clip, creator, "youtube", "GTA 6")
        check("published credit includes both the creator name and platform mention", "Credit: LACY (@LacyHimself)" in text and "#LACY" in text)
        old = dict(clip)
        old.pop("rendered_credit_name")
        try:
            post_text.compose(old, creator, "youtube")
        except post_text.CreditError:
            check("old clips without rendered credit must be rendered again", True)
        else:
            raise AssertionError("Missing on-screen credit was allowed")
        with patch.object(posting, "_mine", return_value=[{"account": "test-channel", "status": "scheduled", "due_at": 1000}]), patch.object(posting.time, "time", return_value=1000):
            check("30-second post spacing overrides the previous minutes-based setting",
                  posting._next_slot("test-channel", 45, 0, 30) == 1030)
        check("settings preserve the unlimited local daily cap and 30-second interval", creator["daily_cap"] == 0 and creator["live_clip_interval_sec"] == 30)
