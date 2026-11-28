"""A small webcam is found wherever the streamer put it.

The game here is not noise but a zooming fractal: big shapes and sharp edges that move, like a real
game. Over a game like that, the side borders of a small webcam add up to little when measured down the
whole height of the frame, which is how a real stream once had its webcam missed (top and bottom found,
sides not). The webcam itself is not a flat colour either: a person in it moves about a little.

Each layout is a place a streamer really puts a camera: a corner, halfway down one side, along the top
or bottom, with a margin round it, and a phone held upright.

Runs against the code directly, with its own empty data folder and no AI."""
import os, subprocess, sys, tempfile
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-facecam-pos-")
os.environ["PEAKCUT_AI_KEY"] = ""
sys.path.insert(0, ".")
from studio import facecam
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

work = tempfile.mkdtemp(prefix="pc-facecam-pos-work-")
FW, FH = 1280, 720
GAME = "mandelbrot=s=640x360:r=25:start_scale=2:end_scale=0.0002,scale=1280:720"


def cam(w, h, to_bottom=0.0):
    """A webcam picture: a lit room with a person in it who sways and nods. With to_bottom (how much of the
    width the body takes), the body runs down past the bottom of the picture, as a streamer sitting close
    to the camera does."""
    pw, ph = (max(20, int(w * to_bottom)), h) if to_bottom else (max(20, w // 4), max(30, h * 5 // 8))
    return (f"color=c=0x5A3E8C:s={w}x{h}:r=25,"
            f"drawbox=x='{(w - pw) // 2}+{w // 8}*sin(2*t)':y='{h // 6}+{h // 20}*sin(3*t)'"
            f":w={pw}:h={ph}:color=0xD9A27A:t=fill")


def make(name, w, h, x, y, to_bottom=0.0):
    path = os.path.join(work, name + ".mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", GAME, "-f", "lavfi", "-i", cam(w, h, to_bottom),
                    "-filter_complex", f"[0:v][1:v]overlay={x}:{y}[v]", "-map", "[v]", "-frames:v", "100",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path], check=True)
    return path


# (name, width, height, x, y, the corner or edge it should be reported on[, the person reaches the bottom])
LAYOUTS = [
    ("left side, halfway down", 256, 160, 0, 300, "left"),
    ("right side, halfway down", 256, 160, FW - 256, 260, "right"),
    ("top-left corner", 256, 160, 0, 0, "top-left"),
    ("top-right corner", 288, 176, FW - 288, 0, "top-right"),
    ("bottom-left corner", 256, 160, 0, FH - 160, "bottom-left"),
    ("bottom-right corner", 256, 160, FW - 256, FH - 160, "bottom-right"),
    ("top, in the middle", 300, 168, 490, 0, "top"),
    ("bottom, in the middle", 300, 168, 490, FH - 168, "bottom"),
    ("with a margin all round", 256, 160, 40, 40, "middle"),
    ("a phone held upright", 160, 284, FW - 200, 220, "middle"),
    # the person moves right up against the webcam's bottom border, so the strip just inside it moves too
    ("the person reaches the bottom border", 256, 160, 0, 300, "left", 0.25),
    ("the person reaches the bottom, in a corner", 256, 160, FW - 256, FH - 160, "bottom-right", 0.25),
    ("broad shoulders across most of the bottom", 256, 160, 0, 300, "left", 0.6),
]

for name, w, h, x, y, where, *rest in LAYOUTS:
    box = facecam.detect(make(name.replace(" ", "_").replace(",", ""), w, h, x, y, *rest)) or {}
    check(f"{name}: found", bool(box), True)
    check(f"{name}: on the right part of the frame", box.get("corner"), where)
    check(f"{name}: in the right place", abs(box.get("x", -99) - x) <= 4 and abs(box.get("y", -99) - y) <= 4, True)
    check(f"{name}: the right size", abs(box.get("w", 0) - w) <= 6 and abs(box.get("h", 0) - h) <= 6, True)
    check(f"{name}: sure of it", box.get("conf"), "high")

# ---- the same game with no webcam: nothing is invented out of the game's own shapes
plain = os.path.join(work, "plain.mp4")
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", GAME, "-frames:v", "100",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", plain], check=True)
check("a busy game with no webcam finds none", (facecam.detect(plain) or {}).get("conf", "none"), "none")

# ---- the model writes its box in different ways; all of them mean the same box
from studio import brain
for text, want in [("CAM x=0% y=42% w=20% h=23%", (0.0, 0.42, 0.2, 0.23)),
                   ("CAM x=0% y=420% w=200% h=230%", (0.0, 0.42, 0.2, 0.23)),
                   ("CAM x=0.0 y=0.42 w=0.20 h=0.23", (0.0, 0.42, 0.2, 0.23))]:
    m = brain._FACE_BOX.search(text)
    got = brain._face_box_parts([float(v) for v in m.groups()])
    check(f"the model's box is read the same way: {text}",
          got and all(abs(a - b) < 1e-6 for a, b in zip(got, want)), True)
check("numbers that make no box are not a box", brain._face_box_parts([90.0, 90.0, 50.0, 50.0]), None)

print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
