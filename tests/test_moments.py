"""Times written the YouTube way, exact moments, YouTube's Most replayed, Stop and Retry, and hiding swear words.

Runs against the code directly, with its own empty data folder. Nothing is downloaded."""
import os, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-moments-")
sys.path.insert(0, ".")
from studio import clean, jobs, media, stamps
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)
def raises(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None

# ---- one time, the YouTube way
check("h:mm:ss", stamps.parse_time("1:05:10"), 3910.0)
check("mm:ss", stamps.parse_time("2:30"), 150.0)
check("units", stamps.parse_time("1h5m10s"), 3910.0)
check("minutes only", stamps.parse_time("12m"), 720.0)
check("plain seconds", stamps.parse_time("3910"), 3910.0)
check("seconds with s", stamps.parse_time("3910s"), 3910.0)
check("no minute 75", raises(lambda: stamps.parse_time("1:75")), lambda e: e and "59" in e)
check("words are not a time", raises(lambda: stamps.parse_time("soon")), lambda e: bool(e))
check("written back like YouTube", stamps.fmt(3910), "1:05:10")
check("under an hour has no hours", stamps.fmt(150), "2:30")

# ---- a list of moments, one per line
ms = stamps.parse_moments("01:05:10 – 01:06:09  Funny moment\n\n2:30-3:15 Opening scene\n"
                          "https://youtu.be/VIDEO_ID?t=3910\nBest part: 1h5m10s to 1h6m")
check("four moments", len(ms), 4)
check("start and end read", (ms[0]["start"], ms[0]["end"]), (3910.0, 3969.0))
check("the words become the note", ms[0]["note"], "Funny moment")
check("a short dash works", (ms[1]["start"], ms[1]["end"], ms[1]["note"]), (150.0, 195.0, "Opening scene"))
check("a link with t= is a start", (ms[2]["start"], ms[2]["end"]), (3910.0, None))
check("a note before the times", ms[3]["note"], "Best part")
check("end before start is refused", raises(lambda: stamps.parse_moments("3:38 - 2:39")), lambda e: e and "after the start" in e)
check("a line with numbers but no time is refused", raises(lambda: stamps.parse_moments("clip at 12")), lambda e: bool(e))
check("plain words are skipped", stamps.parse_moments("my favourite bits"), [])
check("too many is refused", raises(lambda: stamps.parse_moments("\n".join(f"{i}:00-{i}:30" for i in range(1, 30)))),
      lambda e: e and "more than" in e)

# ---- the options a video is made with
o = jobs.clean_options({"moments": "1:05:10 – 1:06:09 Best part", "replayed": True, "clean": True,
                        "range_start": "2:30", "range_end": "1h"})
check("moments are stored as times", o["moments"], [{"start": 3910.0, "end": 3969.0, "note": "Best part"}])
check("a range can be written like YouTube", (o["range_start"], o["range_end"]), (150.0, 3600.0))
check("Most replayed and clean are switches", (o["replayed"], o["clean"]), (True, True))
check("a switch must be on or off", raises(lambda: jobs.clean_options({"clean": "yes"})), lambda e: bool(e))
check("bad moments are refused with a reason", raises(lambda: jobs.clean_options({"moments": "3:38-2:39"})),
      lambda e: e and "after the start" in e)
check("old seconds still work", jobs.clean_options({"range_start": 120})["range_start"], 120.0)

# ---- YouTube's Most replayed graph
heat = [{"start": i * 538.0, "end": (i + 1) * 538.0, "value": v}
        for i, v in enumerate([1.0, 0.2, 0.3, 0.97, 0.9, 0.1] + [0.1] * 94)]
picks = media.most_replayed(heat, 2, 180, 53800)
check("the opening is never a pick", all(a > 538 for a, b, v in picks), True)
check("the highest peak is picked", any(v == 0.97 for a, b, v in picks), True)
check("its next-door point is not picked as well", any(v == 0.9 for a, b, v in picks), False)
check("each pick is the asked width", all(abs((b - a) - 180) < 0.01 for a, b, v in picks), True)
check("picks come in time order", [a for a, b, v in picks] == sorted(a for a, b, v in picks), True)
inside = media.most_replayed(heat, 3, 180, 53800, lo=2000, hi=4000)
check("a chosen part keeps the picks inside it", all(2000 <= a and b <= 4000 for a, b, v in inside), True)
check("no graph, no picks", media.most_replayed([], 3, 180, 1000), [])

# ---- moments become windows
job = {"options": {**jobs.DEFAULTS, "moments": stamps.parse_moments("1:00-1:30 a\n5:00 b\n99:00-99:30 past the end")}}
notes = []
w = jobs._moment_windows(job, {"duration": 3000, "heatmap": []}, notes)
check("each moment is a window, past the end left out", [(x["start"], x["end"]) for x in w], [(60.0, 90.0), (300.0, 360.0)])
check("a start with no end gets the longest clip length", w[1]["end"] - w[1]["start"], 60.0)
check("the left-out one is said", any("past the end" in n for n in notes), True)
check("exact moments are not re-chosen", any(x["pick"] for x in w), False)
rj = {"options": {**jobs.DEFAULTS, "replayed": True}}
check("no graph falls back to the usual way", jobs._moment_windows(rj, {"duration": 3000, "heatmap": []}, []), None)
check("and says why", "Most replayed" in rj.get("notice", ""), True)
rw = jobs._moment_windows(rj, {"duration": 53800, "heatmap": heat}, [])
check("the graph gives windows the AI chooses inside", bool(rw) and all(x["pick"] for x in rw), True)

# ---- Stop and Retry
jobs._jobs["j1"] = {"id": "j1", "user": "u", "kind": "video", "status": "queued", "stage": "Waiting", "clips": [],
                    "creator": None, "dir": tempfile.mkdtemp(), "source": {"type": "url"}}
check("a waiting video stops straight away", (jobs.stop("j1"), jobs._jobs["j1"]["status"]), (True, "stopped"))
check("a stopped video starts again", (jobs.retry("j1"), jobs._jobs["j1"]["status"]), (True, "queued"))
jobs._jobs["j1"]["status"] = "running"
check("a running video is asked to stop", (jobs.stop("j1"), jobs._jobs["j1"].get("stop")), (True, True))
try:
    jobs._check_stop(jobs._jobs["j1"])
    stopped = False
except jobs.Stopped:
    stopped = True
check("the next check stops it", stopped, True)
jobs._jobs["j1"]["status"] = "done"
check("a finished video can't be stopped", jobs.stop("j1"), False)
check("a finished video isn't retried", jobs.retry("j1"), False)
jobs._jobs["j1"]["kind"] = "live"
jobs._jobs["j1"]["status"] = "running"
check("a live recording has its own Stop", jobs.stop("j1"), False)

# ---- swear words
check("the f-word is starred", clean.text("THAT WAS FUCKING CRAZY"), "THAT WAS F****** CRAZY")
check("a slur is starred", clean.text("what is up, nigga?"), "what is up, n****?")
check("innocent words are left", clean.text("class assess bass shiitake Dickens Arsenal cocktail spicy"),
      "class assess bass shiitake Dickens Arsenal cocktail spicy")
cw = clean.words([{"w": "fuck", "s": 1.0, "e": 1.3}, {"w": "off", "s": 1.3, "e": 1.5}])
check("caption timings are kept", [(x["w"], x["s"], x["e"]) for x in cw], [("f***", 1.0, 1.3), ("off", 1.3, 1.5)])

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
