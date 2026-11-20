"""Buffered live reviews must select quality, skip uncertainty, and preserve saved footage."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, ".")

def check(name, condition):
    assert condition, name
    print("PASS " + name)

with tempfile.TemporaryDirectory(prefix="pc-live-quality-") as temp:
    os.environ.update(PEAKCUT_DATA=temp, DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0")
    from PIL import Image
    from studio import ai, auth, brain, creators, ctx, db, jobs, live, posting, review
    db.init()
    with ctx.as_user(auth.local_user().id):
        creator = creators.create({"name":"Quality test", "live_quality_mode":True, "live_review_min":10,
                                   "live_focus":"GTA Vice City gameplay", "min_len":30, "max_len":60,
                                   "min_score":90, "post_min_score":90, "live_per_hour":2,
                                   "live_clip_interval_sec":30, "trigger_words":"clip that", "must_clip_min":15,
                                   "autopost":True, "accounts":["test"]})
        check("review batch settings are saved", creator["live_quality_mode"] and creator["live_review_min"]==10)
        check("review duration is bounded to ten to fifteen minutes", creators._clean({"live_review_min":1})["live_review_min"]==10 and creators._clean({"live_review_min":90})["live_review_min"]==15)
        try: creators._clean({"live_quality_mode":"false"})
        except ValueError: check("quality mode requires a real boolean", True)
        else: raise AssertionError("string boolean accepted")
        def fresh():
            j=jobs.create({"type":"file","name":"Buffered live","url":""}, {}, enqueue=False, creator=creator["id"],kind="live")
            j.update(title="Lacy gameplay",uploader="Lacy",ai={})
            return live.Session(j,dict(creator))
        s=fresh()
        with patch.object(s,"_ask_ai",return_value=True) as ask:
            s.t_done=599; s._quality_step()
            check("no review before ten minutes have been transcribed",not ask.called)
            s.t_done=600; s._quality_step()
            check("the first complete batch covers the whole ten minutes", ask.call_args.args[0]==(0,600))
            s._quality_step(); check("polling never repeats a completed batch",ask.call_count==1)
            s.t_done=1200; s._quality_step()
            check("the next batch overlaps one minute at its boundary",ask.call_args.args[0]==(540,1200))
            s.t_done=1300;s.ai_busy=1;s._quality_step()
            check("a busy review never starts another one",ask.call_count==2)
            s.ai_busy=0;s.ended=True;s._quality_step()
            check("a stream ending reviews its remaining complete story",ask.call_args.args[0]==(1140,1300))
        s=fresh();s.t_done=600
        with patch.object(s,"_ask_ai",return_value=False): s._quality_step()
        check("a review not started cannot advance the cursor",s.review_end==0)
        s._interval_step();s._triggers([{"w":"clip","s":50,"e":51},{"w":"that","s":51,"e":52}])
        check("quality mode overrides fixed intervals and spoken trigger bypasses",not s.manual and s.said is None)
        s.user_stopped=True
        with patch.object(s,"_ask_ai") as ask: s._quality_step()
        check("a stopped recording makes no new batch",not ask.called)

        candidate={"start":120,"end":165,"score":95,"game_relevant":True,"creator_focused":True,
                   "hook":"WHAT A SAVE","title":"A surprising save","description":"A close call.",
                   "category":"clutch","reason":"Clear surprise and payoff","hashtags":[]}
        def answer(value): return patch.object(ai,"see",return_value=(json.dumps(value),10,10))
        with answer([candidate]) as see:
            picks,_=brain.judge_buffer([],0,600,b"jpeg","title","Lacy","GTA Vice City")
            check("a 45-second strong moment keeps its chosen duration",len(picks)==1 and picks[0]["end"]-picks[0]["start"]==45)
            check("pictures and focus reach the review prompt",see.call_args.args[1]==b"jpeg" and "GTA Vice City" in see.call_args.args[0])
            check("the prompt forbids ads and promises no output quota","commercials" in see.call_args.args[0] and "No required clip count" in see.call_args.args[0])
        for name, bad in [("off-topic gameplay",{"game_relevant":False}), ("absent creator",{"creator_focused":False}),
                          ("uncertain boolean",{"creator_focused":"true"}), ("weak score",{"score":89}),
                          ("past the recorded window",{"end":640}), ("too much padding",{"end":181}),
                          ("too short",{"end":130}), ("invalid time",{"start":"nan"}), ("empty hook",{"hook":""})]:
            with answer([dict(candidate,**bad)]):
                picks,_=brain.judge_buffer([],0,600,b"jpeg","title","Lacy","GTA")
                check(name+" is rejected",not picks)
        with answer([]):
            check("an empty batch makes no candidate",not brain.judge_buffer([],0,600,b"jpeg","title","Lacy","GTA")[0])
        with answer({"approved":"true","score":99}):
            check("the close check also needs a real approval boolean",not brain.check_buffer_clip([],candidate,b"jpeg","Lacy","GTA")[0]["approved"])
        with answer({"approved":True,"score":89}):
            check("the close check cannot approve a weak moment",not brain.check_buffer_clip([],candidate,b"jpeg","Lacy","GTA")[0]["approved"])

        plain=brain._tidy(candidate,120,165)
        def run_review(cands, verdict=None, error=None):
            s=fresh()
            with patch.object(review,"snapshot",return_value=(Path(temp)/"manifest",0)), patch.object(review,"storyboard",return_value=b"jpeg"), \
                 patch.object(brain,"judge_buffer",return_value=(copy.deepcopy(cands),10),side_effect=error), \
                 patch.object(brain,"check_buffer_clip",return_value=(verdict or {"approved":True,"score":94,"reason":"Clean payoff"},10)), \
                 patch.object(s,"_add_clip") as add:
                s._quality_check((0,600))
                return s,add
        s,add=run_review([]);check("no hook produces no render",not add.called)
        s,add=run_review([plain],{"approved":False,"score":20,"reason":"Commercial break"})
        check("a moving ad or break rejected by visual review is never rendered",not add.called)
        s,add=run_review([plain],error=RuntimeError("AI unavailable"))
        check("an unavailable AI never falls back to a forced or loudness clip",not add.called and "last_error" in s.job["ai"])
        s,add=run_review([plain]);check("two approvals create one reviewed clip",add.call_count==1 and add.call_args.args[0]["quality_review"]["approved"])
        check("final clip uses the lower of the independent scores",add.call_args.args[0]["score"]==94)
        s=fresh();s.job["clips"]=[dict(plain,made_at=live.time.time(),quality_review={"approved":True})]*2
        with patch.object(brain,"judge_buffer") as judge: s._quality_check((0,600))
        check("the hourly clip cap cannot be bypassed by forced-clip settings",not judge.called)
        s=fresh();s.job["clips"]=[dict(plain)]
        with patch.object(review,"snapshot",return_value=(Path(temp)/"m",0)),patch.object(review,"storyboard",return_value=b"j"),patch.object(brain,"judge_buffer",return_value=([plain],10)),patch.object(s,"_add_clip") as add:
            s._quality_check((0,600))
        check("overlapping batch windows never duplicate a moment",not add.called)
        with patch.object(posting,"enqueue") as enqueue:
            posting.on_clip_ready(s.job,dict(plain,auto_interval=True))
            check("old interval clips cannot automatically publish in quality mode",not enqueue.called)
            posting.on_clip_ready(s.job,dict(plain,quality_review={"approved":True,"version":1,"score":95}))
            check("a reviewed strong clip can queue automatically",enqueue.call_count==1)

        # Real ffmpeg: sampled images must outlive rolling-buffer deletion, without changing source media.
        src=Path(temp)/"piece.ts"
        subprocess.run(["ffmpeg","-nostdin","-y","-v","error","-f","lavfi","-i","testsrc2=size=320x180:rate=10:duration=6","-c:v","libx264","-preset","ultrafast","-f","mpegts",str(src)],check=True)
        snap=Path(temp)/"pinned";snap.mkdir()
        manifest,base=review.snapshot([(src,0,6),(src,6,12),(src,12,18),(src,18,24)],0,24,snap)
        src.unlink()
        jpeg=review.storyboard(manifest,base,0,24,step=2)
        image=Image.open(io.BytesIO(jpeg))
        check("actual video pictures remain readable after rolling pieces expire",image.format=="JPEG" and image.width==1600 and image.height==612)
        jpeg=review.storyboard(manifest,base,7,17,step=2)
        check("a close review can seek into the pinned buffer",Image.open(io.BytesIO(jpeg)).height==204)
        session=fresh();session.ended=True
        retained=review.pin_clip(manifest,7,17,Path(session.dir)/"clips"/"clip001.review")
        restart=fresh()
        restart.job["clips"]=[dict(plain,id="clip001",status="queued",review_pieces=retained)]
        jobs.save(restart.job)
        jobs._load_file(Path(restart.job["dir"])/"job.json",ctx.uid())
        recovered=jobs.get(restart.job["id"])
        check("restart recovery finds pinned clips even with an empty rolling buffer",recovered.get("recover") and recovered["clips"][0]["status"]=="queued")
        check("private source paths are absent from the browser's clip data","review_pieces" not in jobs.public(recovered)["clips"][0])
        for piece in snap.glob("*.ts"): piece.unlink()
        check("approved clip pieces survive expiration of the review snapshot",all(Path(p).exists() for p,_a,_b in retained))
        session.job.update(width=320,height=180)
        selected=dict(plain,id="clip001",start=7,end=17,review_pieces=retained,layout="crop")
        with patch.object(session,"_align",return_value=0),patch.object(jobs,"render_one") as render:
            session._render(selected)
        check("a queued or recovered render uses its preserved pieces",render.called and Path(selected["src"]).exists())
        check("temporary preserved pieces are released after the source is saved",not selected.get("review_pieces"))
        try: review.snapshot([(snap/'piece0000.ts',5,11)],0,24,Path(temp))
        except ValueError: check("missing footage fails the review instead of passing",True)
        else: raise AssertionError("missing footage accepted")

        # Streams can switch resolution and frame rate at an ad boundary. The review clock must not reset.
        mixed=Path(temp)/"mixed";mixed.mkdir()
        pieces=[]
        for i,(size,rate) in enumerate((("320x180",10),("640x360",20))):
            piece=mixed/f"format{i}.ts"
            subprocess.run(["ffmpeg","-nostdin","-y","-v","error","-f","lavfi","-i",
                            f"testsrc2=size={size}:rate={rate}:duration=6","-c:v","libx264","-preset","ultrafast",
                            "-f","mpegts",str(piece)],check=True)
            pieces.append((piece,i*6,(i+1)*6))
        pinned=mixed/"pinned";pinned.mkdir()
        manifest,base=review.snapshot(pieces,0,12,pinned)
        jpeg=review.storyboard(manifest,base,0,12,step=2)
        image=Image.open(io.BytesIO(jpeg))
        check("resolution and frame-rate changes preserve the full review timeline",image.width==1600 and image.height==408)

print("ALL PASSED")
