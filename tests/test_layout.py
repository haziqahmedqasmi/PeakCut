"""The bands layout: the game keeps its whole width, the webcam gets its own band.

The old behaviour - cut the picture to the middle of the screen and put the webcam in the corner - stays as the
"gameplay" layout, asked for by name. These checks pin the numbers, the fallbacks, and that a whole clip and a
preview picture can be made.

Runs against the code directly, with its own empty data folder."""
import os, shutil, subprocess, sys, tempfile, time
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-layout-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="",
                  PEAKCUT_AI_MODELS="")          # the tiny built-in database and no sign-in, as on a new install
sys.path.insert(0, ".")
from pathlib import Path
from PIL import Image
from studio import render
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

work = tempfile.mkdtemp(prefix="pc-layout-work-")
CAM = [900, 40, 300, 170]           # a webcam in the top-right of a 1280x720 stream


def make_source():
    """Four seconds of colour bars with a tone, so a real clip and a real picture can be made from it."""
    src = os.path.join(work, "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-t", "4",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", src], check=True)
    return src


try:
    # ---- the numbers: a 16:9 game gets a band across the full width, not a full-screen crop
    layout, fg_h, hook_y, cap_y = render.geometry(1920, 1080, "bands", CAM)
    check("bands is the layout that comes back", layout, "bands")
    check("the game band is the whole 1080 wide and 608 tall", (fg_h, render.W), (608, 1080))
    check("the game keeps its own shape, so it is not stretched", round(1080 / fg_h, 2), round(16 / 9, 2))
    game_top = hook_y + 118
    check("a short heading keeps the usual gap above the game", game_top - hook_y, 118)
    cam_h = render.facecam_height(CAM)
    check("the webcam keeps its own shape", cam_h, round(460 * 170 / 300))
    check("the webcam is below the game, never over it", game_top + fg_h + render.CAM_GAP, game_top + 608 + 30)
    check("the captions start below the webcam", game_top + fg_h + render.CAM_GAP + cam_h + 40 <= cap_y, True)

    # a tall webcam must not push the picture down past the captions
    tall = [40, 40, 300, 300]
    _, fg_h2, hook_y2, cap_y2 = render.geometry(1920, 1080, "bands", tall)
    top2 = hook_y2 + 118
    check("a tall webcam still fits above the captions",
          top2 + fg_h2 + render.CAM_GAP + render.facecam_height(tall, fg_h2) + 40 <= cap_y2, True)
    check("a tall webcam moves the game band up", top2 <= game_top, True)

    # a portrait phone webcam is shrunk until the words are clear of it, and keeps its own shape
    portrait = [40, 40, 180, 320]
    _, fg_h3, hook_y3, cap_y3 = render.geometry(1920, 1080, "bands", portrait)
    top3, cam_h3 = hook_y3 + 118, render.facecam_height(portrait, fg_h3)
    cam_w3 = render.facecam_width(portrait, fg_h3)
    check("a portrait webcam never reaches the captions", top3 + fg_h3 + render.CAM_GAP + cam_h3 + 40 <= cap_y3, True)
    check("the shrunken webcam keeps its shape", round(cam_w3 / cam_h3, 1), round(180 / 320, 1))

    # ---- a two-line heading gets the room it needs above the game, and stays inside the picture
    long_hook = "a much longer heading that cannot fit on one line at all"
    hh = render.hook_height(long_hook)
    check("a heading is measured", hh > 0, True)
    check("an empty heading takes no room", render.hook_height(""), 0)
    _, fg_h4, hook_y4, _ = render.geometry(1920, 1080, "bands", CAM, hh)
    band4 = render.band_top(fg_h4, render.facecam_height(CAM, fg_h4, hh), hh)
    check("the box of a two-line heading clears the game", hook_y4 + hh / 2 <= band4 - 10, True)
    check("a two-line heading is still on the picture", hook_y4 - hh / 2 >= 0, True)
    check("the filter puts the game below that heading",
          f"overlay=0:{band4}" in render._filter("bands", fg_h4, CAM, hh), True)

    # ---- the fallbacks: never lose the sides unless the plain crop was asked for
    check("bands without a webcam keep every pixel", render.layout_for({"layout": "bands"}, 1920, 1080)[0], "blur")
    check("gameplay without a webcam still fills the screen", render.layout_for({"layout": "gameplay"}, 1920, 1080)[0], "crop")
    check("gameplay with a webcam keeps the corner picture",
          render.layout_for({"layout": "gameplay", "facecam": CAM}, 1920, 1080)[0], "gameplay")
    check("bands with a webcam stay bands",
          render.layout_for({"layout": "bands", "facecam": CAM}, 1920, 1080)[0], "bands")
    check("a square stream fills the screen instead of banding",
          render.layout_for({"layout": "bands", "facecam": CAM}, 1080, 1080)[0], "gameplay")
    check("a webcam box too small to be a picture is ignored",
          render.layout_for({"layout": "bands", "facecam": [0, 0, 4, 4]}, 1920, 1080)[0], "blur")
    check("a broken webcam box is ignored", render.layout_for({"layout": "bands", "facecam": ["x"]}, 1920, 1080)[0], "blur")

    # ---- the filter: one frame drawn three ways, then put back together
    f = render._filter("bands", 608, CAM)
    check("the game is scaled to its band and cropped only there", "scale=1080:608" in f and "crop=1080:608" in f, True)
    check("blurred fill is behind both bands", "boxblur=6:2" in f, True)
    check("the webcam is cropped out of the source", "crop=300:170:900:40" in f, True)
    check("the webcam keeps its own shape in its band", "scale=460:261" in f, True)
    check("the picture is put back on the sound's clock", "setpts=PTS-STARTPTS" in f, True)   # a live piece can start 0.4 s in

    # ---- a picture from the middle of the clip, laid out as the clip will be
    src = make_source()
    clip = {"start": 1.0, "end": 3.5, "hook": "nothing is cut", "style": "bounce", "layout": "bands",
            "facecam": CAM, "captions": True}
    words = [{"w": "hello", "s": 1.2, "e": 1.7}, {"w": "there", "s": 1.8, "e": 2.4}]
    shot = render.still(src, os.path.join(work, "prev.jpg"), clip, words, 1280, 720)
    check("the preview picture is written", os.path.isfile(shot), True)
    im = Image.open(shot)
    check("the picture is a vertical clip frame", im.size, (1080, 1920))
    check("the picture is not blank", (lambda lo, hi: hi - lo > 20)(*im.convert("L").getextrema()), True)

    # ---- a whole clip, so the filter and the captions are proven together
    out = os.path.join(work, "clips")
    got = render.render_clip(src, out, "c1", clip, words, 1280, 720)
    check("the clip is made in the bands layout", got["layout"], "bands")
    check("the clip file is there", os.path.isfile(os.path.join(out, "c1.mp4")), True)
    check("the cover is there", os.path.isfile(os.path.join(out, "c1.jpg")), True)
    probe = subprocess.run(["ffmpeg", "-v", "error", "-i", os.path.join(out, "c1.mp4"),
                            "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("the clip plays without a filter error", probe.returncode == 0 and not probe.stderr.strip(), True)

    # ---- a clip asked for bands but with no webcam is made in the blurred fill
    plain = render.render_clip(src, out, "c2", dict(clip, layout="bands", facecam=None), words, 1280, 720)
    check("no webcam: the clip is made in the blurred fill", plain["layout"], "blur")

    # ---- the preview route: a picture made on demand and served to the page
    from fastapi.testclient import TestClient
    from studio import account, auth, ctx, db, jobs, server
    db.init()
    jid = "prevjob1234"
    d = Path(os.environ["PEAKCUT_DATA"]) / "jobs" / jid
    (d / "clips").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, d / "src.mp4")
    one = {"id": "c1", "status": "done", "start": 1.0, "end": 3.0, "hook": "a test clip", "style": "bounce",
           "layout": "bands", "facecam": CAM, "captions": True, "score": 80, "version": 1,
           "src": str(d / "src.mp4"), "src_offset": 0.0}
    job = {"id": jid, "user": auth.local_user().id, "dir": str(d), "kind": "video", "status": "done",
           "created": time.time(), "width": 1280, "height": 720, "creator": None, "lang": "en",
           "title": "a stream I tested on", "source": {"title": "a stream I tested on", "url": ""}, "clips": [one]}
    jobs._jobs[jid] = job
    jobs._by_user.setdefault(job["user"], {})[jid] = job
    stale = d / "clips" / "c1-prev-blur.jpg"
    stale.write_bytes(b"an old preview")            # left over from an earlier choice
    c = TestClient(server.app, base_url="http://127.0.0.1:8799")
    r = c.post(f"/api/jobs/{jid}/clips/c1/preview", json={"layout": "gameplay"})
    check("the preview route answers", r.status_code, 200)
    check("the picture is named for the clip and the layout", (r.json() or {}).get("preview"), "c1-prev-gameplay.jpg")
    check("the older picture of another layout is removed", stale.exists(), False)
    shot2 = c.get(f"/media/{jid}/c1-prev-gameplay.jpg")
    check("the page can load the picture", shot2.status_code, 200)
    check("it is served as a picture", shot2.headers.get("content-type", "").startswith("image/"), True)
    check("a layout PeakCut does not know is refused",
          c.post(f"/api/jobs/{jid}/clips/c1/preview", json={"layout": "nope"}).status_code, 400)
    check("an unknown clip is not found",
          c.post(f"/api/jobs/{jid}/clips/nope/preview", json={"layout": "gameplay"}).status_code, 404)
    check("nothing about the clip was changed", jobs._jobs[jid]["clips"][0]["status"], "done")

    # ---- deleting a clip takes its previews with it
    two = dict(one, id="c2", version=1)
    job["clips"].append(two)
    keep = d / "clips" / "c2-prev-bands.jpg"
    keep.write_bytes(b"a picture of the clip")
    jobs.save(job)
    with ctx.as_user(job["user"]):
        check("deleting the clip works", account.delete_clip(jid, "c2"), True)
    check("its preview is deleted too", keep.exists(), False)
    check("the other clip is left alone", jobs._jobs[jid]["clips"][0]["id"], "c1")
finally:
    shutil.rmtree(work, ignore_errors=True)      # the files are big; they must not be left behind

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
