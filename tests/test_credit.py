"""Every automatic post names the streamer the clip came from, so credit is never forgotten."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-credit-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import posting
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)

yt = {"name": "Typical Gamer", "channel": "https://www.youtube.com/@TypicalGamer", "channels": {}, "post_footer": ""}
check("a YouTube channel is credited by its @handle", posting.credit(yt) == "🎥 Credit: @TypicalGamer", posting.credit(yt))
tw = {"name": "x", "channel": "https://www.twitch.tv/somestreamer", "channels": {}}
check("a Twitch channel is credited by name and site", posting.credit(tw) == "🎥 Credit: somestreamer (www.twitch.tv)", posting.credit(tw))
check("no link: the channel's own name", posting.credit({"name": "x", "channel": ""}, "Carry") == "🎥 Credit: Carry")

clip = {"description": "His first chase went so wrong", "hashtags": ["gta6", "vicecity"]}
cap = posting._caption(clip, yt)
check("the credit sits right under the description", cap.split("\n\n")[:2] == ["His first chase went so wrong", "🎥 Credit: @TypicalGamer"], cap)
check("...with the hashtags after it", cap.endswith("#gta6 #vicecity"), cap)
foot = dict(yt, post_footer="🎥 Clip from @TypicalGamer, watch the full stream")
check("not repeated when the footer already names them", posting._caption(clip, foot).count("@TypicalGamer") == 1, posting._caption(clip, foot))

off = dict(yt, post_credit=False)
check("turned off: no credit line", "Credit" not in posting._caption(clip, off), posting._caption(clip, off))
old = {k: v for k, v in yt.items()}
check("creators saved before the setting existed are credited", "@TypicalGamer" in posting._caption(clip, old))

# ---- the page shows the same line the post will carry
from studio import jobs
job = {"id": "j1", "creator": None, "uploader": "CarryisLive", "source": {"type": "url"}, "clips": [], "status": "done"}
check("a video with no creator shows its channel as the credit", jobs.public(job)["credit"] == "🎥 Credit: CarryisLive", jobs.public(job).get("credit"))
check("the page and the post agree", posting.credit_line(yt) == "🎥 Credit: @TypicalGamer" and posting.credit_line(off) == "")

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
