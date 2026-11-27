"""Style previews for a pasted video link: grabbing 2-3 frames, spotting framing/facecam, switching moments,
and serving the preview video, stills and blurred fill without downloading the full video."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-linksamp-")
os.environ.update(DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0")
sys.path.insert(0, ".")

import cv2                                       # noqa: E402
import numpy as np                               # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402
from studio import auth, db, facecam, media, server, templates  # noqa: E402

db.init()
fails = []


def check(n, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(n)


d = Path(os.environ["PEAKCUT_DATA"])
vid = d / "sample_src.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=16:r=24",
                "-f", "lavfi", "-i", "sine=d=16", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac", str(vid)], check=True)

# 1) 2-3 frame framing analysis: skips blank frames, distinguishes cam vs podcast vs screen
p0, p1, p2 = d / "f0.jpg", d / "f1.jpg", d / "f2.jpg"
cv2.imwrite(str(p0), np.zeros((720, 1280, 3), dtype=np.uint8))
rng = np.random.default_rng(11)
b1 = rng.integers(20, 230, (720, 1280, 3), dtype=np.uint8)
b2 = rng.integers(20, 230, (720, 1280, 3), dtype=np.uint8)
cv2.rectangle(b1, (16, 16), (316, 216), (255, 255, 255), 3)
cv2.rectangle(b2, (16, 16), (316, 216), (255, 255, 255), 3)
cv2.imwrite(str(p1), b1)
cv2.imwrite(str(p2), b2)

screen_res = facecam.analyze_sample([p0, p1, p2])
check("black frame skipped when picking best frame", screen_res["kind"] == "screen" and screen_res["best"] in (1, 2), str(screen_res))

orig_faces = facecam._faces
try:
    facecam._faces = lambda grays: [[], [(110, 60, 96, 96)], [(114, 62, 98, 98)]]
    cam_res = facecam.analyze_sample([p0, p1, p2])
    check("stationary corner cam found from 2-3 frames",
          cam_res["kind"] == "cam" and cam_res["cam"] is not None and cam_res["cam"]["x"] < 0.1 and cam_res["best"] in (1, 2),
          str(cam_res))

    facecam._faces = lambda grays: [[], [(260, 180, 130, 130), (880, 185, 125, 125)], [(265, 182, 130, 130), (882, 184, 128, 128)]]
    pod_res = facecam.analyze_sample([p0, p1, p2])
    check("two separated speakers classified as podcast", pod_res["kind"] == "podcast" and pod_res["cam"] is None, str(pod_res))
finally:
    facecam._faces = orig_faces

# 2) Server endpoints: extract 3 frames, cut 5s sample clip, serve files, switch moments
auth.local_user()
orig_stream = media.stream_info
yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
media.stream_info = lambda u: {"url": str(vid), "headers": {}, "protocol": "file", "width": 1280, "height": 720,
                               "duration": 16.0, "is_live": False, "title": "Gameplay Demo", "proxy": ""}
try:
    c = TestClient(server.app, base_url="http://127.0.0.1:8765", headers={"origin": "http://127.0.0.1:8765"})
    check("disallowed link refused", c.post("/api/templates/link-sample", json={"url": "https://example.com/v"}).status_code, 400)
    r = c.post("/api/templates/link-sample", json={"url": yt_url})
    meta = r.json() if r.status_code == 200 else {}
    key = meta.get("key", "")
    check("link sample built 3 frames and clip", r.status_code == 200 and key and meta.get("frames") == 3 and meta.get("title") == "Gameplay Demo", str(meta))

    for ext, ctype in [("mp4", "video/mp4"), ("jpg", "image/jpeg"), ("blur.jpg", "image/jpeg"),
                       ("f0.jpg", "image/jpeg"), ("f1.jpg", "image/jpeg"), ("f2.jpg", "image/jpeg")]:
        rf = c.get(f"/api/templates/link-sample/{key}/{ext}")
        check(f"serves {ext}", rf.status_code == 200 and len(rf.content) > 1000 and rf.headers.get("content-type", "").startswith(ctype))

    check("invalid sample key rejected", c.get("/api/templates/link-sample/bad-key/jpg").status_code, 404)
    rm = c.post("/api/templates/link-sample", json={"url": yt_url, "moment": 2})
    check("switching moment updates active preview", rm.status_code == 200 and rm.json().get("active") == 2)
    rc = c.post(f"/api/templates/link-sample/{key}/cam")
    check("re-running cam finder works on cached pasted video", rc.status_code == 200 and "sample_cam" in rc.json())
finally:
    media.stream_info = orig_stream
    db.engine.dispose()

import shutil  # noqa: E402
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
