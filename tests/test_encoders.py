"""Video encoders: the graphics card is tried first, and a clip is always made. An encoder that fails or freezes is
passed over for the next one; one that never worked is skipped from then on; one that worked before is tried again;
PEAKCUT_HW_ENCODE=0 uses the processor only. Nothing is downloaded and no graphics card is needed."""
import os, subprocess, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-enc-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from pathlib import Path   # noqa: E402
from studio import media, render   # noqa: E402
fails = []


def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)


d = Path(os.environ["PEAKCUT_DATA"])
src = d / "src.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=3:r=30", "-f", "lavfi", "-i", "sine=d=3",
                "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
clip = {"start": 0.0, "end": 2.0, "style": "clean", "layout": "blur", "hook": "", "captions": False}
REAL = list(render.ENCODERS)
X264 = [e for e in REAL if e[0] == "x264"]
n = 0


def make():
    global n
    n += 1
    out = d / f"out{n}"
    got = render.render_clip(src, out, f"c{n}", dict(clip), [], 1280, 720)
    f = out / got["file"]
    return f.is_file() and media.probe(f).get("height") == 1920


def fresh(encoders):
    render.ENCODERS = encoders
    render._enc_ok.clear()


# the encoders this machine really has
fresh(REAL)
check("a clip is made with the encoders this machine has", make())
print("  used:", [k for k, v in render._enc_ok.items() if v] or "none recorded")

# an encoder that does not exist: passed over, then skipped from then on
BAD = ("bad", ["yuv420p", "-c:v", "no_such_encoder"])
fresh([BAD] + X264)
check("a broken encoder is passed over and the clip is still made", make())
check("...and it is skipped from then on", render._enc_ok.get("bad") is False and render._enc_ok.get("x264") is True)

# one that worked before and fails once (a busy card) is tried again next time
fresh([BAD] + X264)
render._enc_ok["bad"] = True
check("an encoder that worked before and fails once still gives a clip", make())
check("...and is not switched off", render._enc_ok.get("bad") is True)

# a frozen encoder: stopped, and the next one makes the clip
real_run = subprocess.run


def frozen_run(cmd, *a, **k):
    if "frozen_encoder" in cmd:
        raise subprocess.TimeoutExpired(cmd, render.RENDER_SECONDS)
    return real_run(cmd, *a, **k)


render.subprocess.run = frozen_run
fresh([("frozen", ["yuv420p", "-c:v", "frozen_encoder"])] + X264)
try:
    check("a frozen encoder is stopped and the next one makes the clip", make())
except subprocess.TimeoutExpired:
    check("a frozen encoder is stopped and the next one makes the clip", False, "the timeout was not caught")
render.subprocess.run = real_run

# PEAKCUT_HW_ENCODE=0: graphics cards are not tried at all
fresh([BAD] + X264)
render.HW_ENCODE = False
check("with PEAKCUT_HW_ENCODE=0 only the processor is used", make() and "bad" not in render._enc_ok)
render.HW_ENCODE = True

# every encoder failing is a plain error, and none is switched off for it
fresh([BAD, ("x264", ["yuv420p", "-c:v", "no_such_encoder_either"])])
try:
    make()
    check("when every encoder fails, it says so", False, "no error")
except RuntimeError as e:
    check("when every encoder fails, it says so", "Render failed" in str(e))
check("...and none is switched off for it", "bad" not in render._enc_ok)

render.ENCODERS = REAL
print(f"{len(fails)} failed" if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
