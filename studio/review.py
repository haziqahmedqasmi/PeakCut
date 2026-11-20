"""Small, timestamped contact sheets of a saved live buffer for visual review."""
import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, LABEL = 320, 180, 24


def snapshot(segments, start, end, folder):
    """Pin complete pieces before the rolling buffer removes them. Caller holds its lock.

    Hard links keep the bytes without duplicating a ten-minute recording. A copy is used
    on drives that do not support links. Everything lives in the caller's temporary folder.
    """
    folder = Path(folder)
    chosen = [s for s in segments if s[2] > start and s[1] < end]
    if not chosen or chosen[0][1] > start + 2 or chosen[-1][2] < end - 2:
        raise ValueError("The complete review window is no longer in the buffer.")
    if any(b[1] - a[2] > 2 for a, b in zip(chosen, chosen[1:])):
        raise ValueError("The recording has a gap in the review window.")
    names = []
    for i, (path, _a, _b) in enumerate(chosen):
        name = f"piece{i:04d}.ts"
        target = folder / name
        try:
            os.link(path, target)
        except OSError:
            shutil.copyfile(path, target)
        names.append(f"file '{name}'\n")
    manifest = folder / "pieces.txt"
    manifest.write_text("".join(names), encoding="utf-8")
    (folder / "pieces.json").write_text(json.dumps([(f"piece{i:04d}.ts", a, b) for i, (_p, a, b) in enumerate(chosen)]), encoding="utf-8")
    return manifest, chosen[0][1]


def storyboard(manifest, base, start, end, step=10):
    """Read actual pictures, with stream seconds printed above each one.

    The overview samples every ten seconds. The second check samples a proposed clip
    every two seconds. Missing or truncated pictures fail the review; they never pass it.
    """
    duration = end - start
    if duration <= 0 or start < base - 2 or step < 1:
        raise ValueError("Invalid video review window.")
    count = math.ceil(duration / step)
    if count > 100:
        raise ValueError("The video review window is too large.")
    # Resolution switches at breaks must not reset the sampling clock halfway through a review.
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "concat", "-safe", "0",
         "-reinit_filter", "0", "-i", str(manifest),
         "-t", str(duration),
         "-vf", f"trim=start={max(0, start-base)}:duration={duration},setpts=PTS-STARTPTS,"
                  f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{step})',"
                  f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2",
         "-fps_mode", "vfr", "-frames:v", str(count), "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=180)
    size = W * H * 3
    frames = len(proc.stdout) // size
    if proc.returncode or frames < max(1, count - 1):
        raise ValueError("Could not read the pictures needed for visual review.")
    columns = min(5, frames)
    sheet = Image.new("RGB", (columns * W, math.ceil(frames / columns) * (H + LABEL)), "#121214")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for i in range(frames):
        x, y = (i % columns) * W, (i // columns) * (H + LABEL)
        draw.text((x + 6, y + 2), f"{start + i * step:.1f}s", font=font, fill="white")
        sheet.paste(Image.frombytes("RGB", (W, H), proc.stdout[i * size:(i + 1) * size]), (x, y + LABEL))
    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=85)
    return out.getvalue()


def pin_clip(manifest, start, end, target):
    """Keep just a selected clip's pieces until its render can run, even after a restart."""
    target = Path(target)
    target.mkdir(exist_ok=True)
    folder = Path(manifest).parent
    rows = json.loads((folder / "pieces.json").read_text(encoding="utf-8"))
    pinned = []
    for name, a, b in rows:
        if b <= start - 8 or a >= end + 8:
            continue
        src, dest = folder / name, target / name
        try:
            os.link(src, dest)
        except OSError:
            shutil.copyfile(src, dest)
        pinned.append((str(dest), a, b))
    if not pinned or pinned[0][1] > start + 2 or pinned[-1][2] < end - 2:
        raise ValueError("The selected clip's video could not be preserved.")
    return pinned
