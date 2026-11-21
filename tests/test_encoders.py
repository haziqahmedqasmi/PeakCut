"""Video card encoding: a card is tried first, one that fails is left out from then on, and the processor always
finishes the job. ffmpeg is replaced, so no card and no video is needed."""
import os, shutil, sys, tempfile
DATA = tempfile.mkdtemp(prefix="pc-enc-")
os.environ.update(PEAKCUT_DATA=DATA, DATABASE_URL="")
sys.path.insert(0, ".")
from pathlib import Path
from types import SimpleNamespace
from studio import render
fails = []
def check(n, got, want=True):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

render._listed = "V..... h264_nvenc\n V..... h264_qsv\n V..... libx264\n"
check("the cards this ffmpeg has come first, the processor last", [l for l, _ in render.encoders_to_try()], ["nvenc", "qsv", "x264"])
os.environ["PEAKCUT_HW_ENCODE"] = "0"
check("cards can be switched off", [l for l, _ in render.encoders_to_try()], ["x264"])
del os.environ["PEAKCUT_HW_ENCODE"]

calls = []
def fake(cmd, cwd=None, **kw):
    enc = cmd[cmd.index("-c:v") + 1] if "-c:v" in cmd else "copy"
    calls.append(enc)
    if enc in ("h264_nvenc", "h264_qsv"):
        return SimpleNamespace(returncode=1, stderr="no device", stdout="")
    if cmd[-1].endswith(".mp4"):
        (Path(cwd) / cmd[-1]).write_bytes(b"x")
    return SimpleNamespace(returncode=0, stderr="", stdout="")
render.subprocess.run = fake
render.run = lambda cmd, cwd=None, **kw: (Path(cwd) / cmd[-1]).write_bytes(b"x")
def make(n):
    work, out = Path(tempfile.mkdtemp(dir=DATA)), Path(tempfile.mkdtemp(dir=DATA))
    return render._render_in(work, Path("in.mp4"), out, n, 0, 10, "full", 1000, "[Script Info]\n"), out
r, out = make("c1")
check("a card that fails does not stop the clip", (r["file"], (out / "c1.mp4").exists(), calls), ("c1.mp4", True, ["h264_nvenc", "h264_qsv", "libx264"]))
calls.clear(); make("c2")
check("...and is not tried again", calls, ["libx264"])
print("ALL PASSED" if not fails else f"{len(fails)} failed")
shutil.rmtree(DATA, ignore_errors=True); sys.exit(1 if fails else 0)
