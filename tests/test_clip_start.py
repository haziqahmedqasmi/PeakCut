"""A clip's first frame is the moment the clip starts, not a still picture repeated to fill a gap.

After a seek, ffmpeg hands over its first frame still carrying the timestamp it had in the file — a keyframe
can be a second or two before the moment asked for. The fps filter then fills from zero by repeating that
frame, so the clip opens on a frozen picture and comes out longer than it was asked for.
Runs against the code directly, with its own empty data folder."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
WORK = Path(tempfile.mkdtemp(prefix="pc-clipstart-"))
os.environ.update(PEAKCUT_DATA=str(WORK), DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import config, render  # noqa: E402

fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def unrebased(chain):
    """Every part of a filter graph that sets a frame rate without first putting its first frame at zero."""
    bad = []
    for part in chain.split(";"):
        if f"fps={config.OUT_FPS}" in part and "setpts=PTS-STARTPTS" not in part:
            bad.append(part.strip())
    return bad


# ---- every layout starts the clip's clock before it fixes the frame rate
for layout in render.LAYOUTS:
    chain = render._filter(layout, 1080, [0, 0, 300, 170] if layout == "gameplay" else None)
    check(f"{layout} puts the first frame at the start", chain.split(",")[0], "[0:v]setpts=PTS-STARTPTS")
    check(f"{layout} never sets a frame rate before it", unrebased(chain), [])

# ---- the streamer's cam, stacked and cut from the picture
CAM = {"stack": True, "bh": 560, "by": 0, "sw": 300, "sh": 300, "sx": 900, "sy": 40, "bw": 1080, "bx": 0,
       "mask": None, "border": None}
check("a stacked cam never sets a frame rate before starting the clock", unrebased(render.cam_filter(CAM, "gameplay", 1080)), [])
CAM["stack"] = False
check("a cam cut from the picture never does either", unrebased(render.cam_filter(CAM, "gameplay", 1080)), [])

# ---- a clip whose first frame arrives late comes out at exactly the rate asked for
SRC = WORK / "late.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "nullsrc=s=320x180:r=30,noise=alls=30:allf=t",
                "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-g", "150", "-pix_fmt", "yuv420p",
                str(SRC)], check=True)
WANT = 3 * config.OUT_FPS


def frames(chain):
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(SRC), "-t", "3", "-an",
                          "-vf", chain, "-f", "rawvideo", "-"], capture_output=True).stdout or b""
    return len(raw) // (160 * 90)


LATE = "setpts=PTS+1.833/TB"          # the first frame arrives 1.833s into the file's clock, as after a seek
check("without starting the clock the frame count is wrong",
      frames(f"{LATE},fps={config.OUT_FPS},scale=160:90,format=gray"), lambda v: v != WANT)
check("with it, exactly the frames three seconds hold",
      frames(f"{LATE},setpts=PTS-STARTPTS,fps={config.OUT_FPS},scale=160:90,format=gray"), WANT)
check("and a clip that already starts at zero is unchanged",
      frames(f"setpts=PTS-STARTPTS,fps={config.OUT_FPS},scale=160:90,format=gray"), WANT)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
