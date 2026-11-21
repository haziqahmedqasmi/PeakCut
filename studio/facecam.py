"""Where the facecam is.

A gaming stream is a game with a small webcam picture in one corner of it. The
short that comes out of it is better when the game fills the screen and the
webcam sits in the corner of it, instead of the whole 16:9 frame squeezed into
a band with blurred fill above and below.

The webcam is found by the one thing a webcam always has and a game almost never
has: a straight border that stays in the same place in every frame. A wall in
the game moves, a HUD bar changes, but the edge of the webcam box does not move
at all. A second thing tells the webcam apart from the game's own panels, which
have straight borders too: the picture inside a webcam is almost still, while
the game behind a panel keeps moving.

Metric: for every column and every row, how much the picture changes at that
line, counted over frames spread through the video. A border of the webcam box
stands out from the rest.

One frame can also be shown to the AI (`detect(path, ask=...)`), which is better
than any measurement at telling a person from a game character. Its answer is
measured again on the picture before it is used, so being wrong costs a search,
not a clip.

Only numpy and ffmpeg, which are already here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

W, H = 320, 180            # the picture is looked at small: only the lines matter
MAX_FRAMES = 24            # frames spread through the video
MIN_SIZE = 0.12            # the smallest a webcam may be, as a part of the width or height
MAX_SIZE = 0.60            # and the largest
ASPECT_MIN = 0.55          # the narrowest: a phone held upright is about 0.56
ASPECT_MAX = 3.20          # the widest: a banner or a ticker is wider than this
STILL_FRAC = 0.35          # how much of the game's own movement a webcam picture may have
STILL_MIN = 6.0            # and the floor, for a game that itself is almost still
STILL_SHARE = 0.30         # at most this much of a webcam picture may be moving, or the game is showing through
EDGE_FLUSH = 0.02          # a side this close to the frame's own edge is the edge
MATCH = 0.35               # how much a box the AI points at must cover a measured box to be that box
EDGE = 2.2                 # how much brighter than the average line a border must be


def _frames(path: str | Path, count: int = MAX_FRAMES) -> np.ndarray:
    """The video as a few grey frames, spread through it."""
    try:
        dur = _duration(path)
        rate = max(count / dur, 0.02) if dur > 0 else 0.2
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path),
             "-vf", f"fps={rate:.4f},scale={W}:{H},format=gray",
             "-frames:v", str(count), "-f", "rawvideo", "-"],
            capture_output=True, timeout=180)
    except Exception:  # noqa: BLE001 - no ffmpeg or an unreadable file: no facecam, not a failure
        return np.zeros((0, H, W), dtype=np.uint8)
    raw = proc.stdout or b""
    size = W * H
    n = len(raw) // size
    if n < 4:
        return np.zeros((0, H, W), dtype=np.uint8)
    return np.frombuffer(raw[:n * size], dtype=np.uint8).reshape(n, H, W)


def _duration(path: str | Path) -> float:
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=nw=1:nk=1", str(path)],
                              capture_output=True, text=True, timeout=60)
        return float((proc.stdout or "").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _profiles(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(vertical lines, horizontal lines): how much changes at each column and each row.

    The median over the frames is used, so one busy moment cannot invent a line."""
    if len(frames) < 4:
        return np.zeros(W), np.zeros(H)
    f = frames.astype(np.int16)
    gx = np.median(np.abs(np.diff(f, axis=2)).sum(axis=1), axis=0)   # change across each column boundary
    gy = np.median(np.abs(np.diff(f, axis=1)).sum(axis=2), axis=0)   # change across each row boundary
    return gx, gy


def _peaks(profile: np.ndarray, limit: int) -> list[tuple[int, float]]:
    """The lines that stand out: (where, how much)."""
    if len(profile) < 3:
        return []
    med = float(np.median(profile)) or 1.0
    out = []
    for i in range(1, len(profile) - 1):
        if profile[i] >= profile[i - 1] and profile[i] > profile[i + 1] and profile[i] > EDGE * med:
            out.append((i, float(profile[i] / med)))
    return sorted(out, key=lambda p: -p[1])[:limit]


def _size(path: str | Path) -> tuple[int, int]:
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "default=nw=1", str(path)],
                          capture_output=True, text=True, timeout=60)
    w = h = 0
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        try:
            if k.strip() == "width":
                w = int(v.strip())
            elif k.strip() == "height":
                h = int(v.strip())
        except ValueError:
            continue
    return w, h


def _stillness(frames: np.ndarray) -> np.ndarray:
    """How much each pixel changes over the frames.

    A webcam picture barely changes: a person sits in a room and shifts a little. The game changes every
    frame. This is the one thing that tells a webcam apart from the game's own panels, which have straight
    borders too and are drawn over the game with the game showing through them."""
    return frames.astype(np.float32).std(axis=0)


def _plausible(x0, y0, x1, y1, still, light, game, share=STILL_SHARE):
    """Could this rectangle be a webcam picture? Judged on its shape, its stillness and its light.

    Returns how much it moves, or None. Nothing here says which corner it is in: a webcam may sit in a
    corner, along one edge, or with a margin all round."""
    bw, bh = (x1 - x0) / W, (y1 - y0) / H
    if not (MIN_SIZE <= bw <= MAX_SIZE and MIN_SIZE <= bh <= MAX_SIZE):
        return None
    # measured in pixels: a 4:3 picture is 1.33, a phone held upright is 0.56
    aspect = (bw * W) / max(1e-6, bh * H)
    if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
        return None
    a, b, c, d = int(x0), int(y0), int(x1), int(y1)
    inside = still[b:d, a:c]
    if not inside.size:
        return None
    cut = max(STILL_MIN, STILL_FRAC * game)
    moving = float(np.median(inside))
    if moving > cut:
        return None                    # it changes with the game: it is the game
    if float((inside > cut).mean()) > share:
        return None                    # a good part of it moves: the game is showing through
    if float(light[b:d, a:c].mean()) < 0.30 * float(light.mean()):
        return None                    # a black hole, not a picture
    return moving


def _edge_maps(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(change across each column boundary, change across each row boundary), pixel by pixel.

    The median over the frames, like `_profiles`, but not yet added up, so a border can be measured
    over just the stretch of the picture it runs along."""
    f = frames.astype(np.int16)
    ex = np.median(np.abs(np.diff(f, axis=2)), axis=0).astype(np.float32)   # H x (W-1)
    ey = np.median(np.abs(np.diff(f, axis=1)), axis=0).astype(np.float32)   # (H-1) x W
    return ex, ey


def _lines(ex, ey, vpeaks, hpeaks, rounds: int = 2, keep: int = 16):
    """Every straight line that could be a side of the webcam: ({x: strength}, {y: strength}).

    A webcam a fifth of the frame tall has side borders only a fifth of the frame long. Added up down the
    whole height, the game's own edges drown them out: a small webcam in a busy game had its top and
    bottom found but never its sides, or the other way round. So, besides the lines that stand out over
    the whole frame, the columns are measured again between every two rows that could be a webcam's top
    and bottom, and the rows between every two columns. A line found that way can itself bound the next
    band, so this is done twice. Nothing here assumes which corner or edge the webcam is on."""
    xs = _thin({float(x): s for x, s in vpeaks}, keep)
    ys = _thin({float(y): s for y, s in hpeaks}, keep)
    for _ in range(rounds):
        nx, ny = dict(xs), dict(ys)
        yb = sorted({0.0, float(H)} | set(ys))
        for j, y0 in enumerate(yb):
            for y1 in yb[j + 1:]:
                if MIN_SIZE <= (y1 - y0) / H <= MAX_SIZE:
                    band = ex[int(y0) + 1:max(int(y0) + 2, int(y1) - 1)].sum(axis=0)  # off the top and bottom
                    for x, sc in _peaks(band, 6):
                        nx[float(x)] = max(nx.get(float(x), 0.0), sc)
        xb = sorted({0.0, float(W)} | set(xs))
        for i, x0 in enumerate(xb):
            for x1 in xb[i + 1:]:
                if MIN_SIZE <= (x1 - x0) / W <= MAX_SIZE:
                    band = ey[:, int(x0) + 1:max(int(x0) + 2, int(x1) - 1)].sum(axis=1)
                    for y, sc in _peaks(band, 6):
                        ny[float(y)] = max(ny.get(float(y), 0.0), sc)
        xs, ys = _thin(nx, keep), _thin(ny, keep)
    return xs, ys


def _thin(lines: dict, keep: int, gap: float = 3.0) -> dict:
    """The strongest lines, one per place: a line a pixel or two from a stronger one is the same edge.

    Without this, one busy edge in the game (a pillar, a seam) fills the whole list with copies of
    itself, and a webcam's quieter border never gets on it."""
    out = {}
    for x, sc in sorted(lines.items(), key=lambda kv: -kv[1]):
        if all(abs(x - y) > gap for y in out):
            out[x] = sc
            if len(out) >= keep:
                break
    return out


GAP = 2                     # pixels left alone either side of a border line, as it can be a pixel or two out
STRIP = 3                   # how deep, on the small grid, the picture is looked at on each side of a border
CONTRAST = 2.0              # how much more the game outside a border must move than the webcam inside it


def _sides(x0, y0, x1, y1, still, cut):
    """How clearly this rectangle is a picture set into the game: the weakest of its borders, or None.

    Just inside every side of a webcam the picture is still (a room); just outside every side that is not
    the frame's own edge, the game is moving. A line inside the webcam (a shelf, the edge of a lamp, the
    person's shoulder) has the still room on both sides of it, and a box that reaches past the webcam has
    the moving game just inside its border, so neither passes. Returns the weakest outside/inside ratio."""
    a, b, c, d = int(x0), int(y0), int(x1), int(y1)
    g, k = GAP, STRIP
    if c - a < 2 * (k + g) + 2 or d - b < 2 * (k + g) + 2:
        return None
    worst = 99.0
    # (strip just inside, strip just outside or None when the side is the frame's own edge). Each strip
    # keeps GAP pixels off the line, so a line found a pixel or two off still reads both sides right.
    for inside, outside in (
            (still[b:d, a + g:a + g + k], still[b:d, max(0, a - g - k):a - g] if a - g > 0 else None),
            (still[b:d, c - g - k:c - g], still[b:d, c + g:c + g + k] if c + g < W else None),
            (still[b + g:b + g + k, a:c], still[max(0, b - g - k):b - g, a:c] if b - g > 0 else None),
            (still[d - g - k:d - g, a:c], still[d + g:d + g + k, a:c] if d + g < H else None)):
        inner = float(np.median(inside))
        if inner > cut:
            return None                # the game reaches inside this side: the box is too big
        if outside is not None and outside.size:
            worst = min(worst, float(np.median(outside)) / (inner + 1.0))
    return worst if worst >= CONTRAST else None


def _local(vpeaks, hpeaks, still, light, lines=None):
    """Every rectangle the straight lines can make, that looks like a camera set into the game.

    `lines` are the candidate sides from `_lines`; without them only the lines that stand out over the
    whole frame are used. Returns them best first: the box whose borders most clearly separate a still
    picture from the moving game, then the one with the strongest weakest line, then the bigger."""
    game = float(np.median(still))
    cut = max(STILL_MIN, STILL_FRAC * game)
    vs, hs = lines if lines else ({float(x): s for x, s in vpeaks}, {float(y): s for y, s in hpeaks})
    xs = sorted({0.0, float(W)} | set(vs))
    ys = sorted({0.0, float(H)} | set(hs))
    out = []
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            if not (MIN_SIZE <= (x1 - x0) / W <= MAX_SIZE):
                continue
            wscore = [vs[v] for v in (x0, x1) if v in vs]
            for j, y0 in enumerate(ys):
                for y1 in ys[j + 1:]:
                    score = wscore + [hs[v] for v in (y0, y1) if v in hs]
                    if len(score) < 2:
                        continue       # a box needs two borders of its own, or it is just the frame
                    moving = _plausible(x0, y0, x1, y1, still, light, game)
                    if moving is None:
                        continue
                    clear = _sides(x0, y0, x1, y1, still, cut)
                    if clear is None:
                        continue
                    key = (min(clear, 4.0), min(score), (x1 - x0) * (y1 - y0))
                    out.append({"key": key, "x": x0, "y": y0, "x1": x1, "y1": y1,
                                "score": round(min(score), 2), "moving": moving})
    return sorted(out, key=lambda c: c["key"], reverse=True)


def _overlap(a, box) -> float:
    """How much two boxes cover the same ground: 1 when they are the same, 0 when they do not touch."""
    ax0, ay0, ax1, ay1 = a["x"], a["y"], a["x1"], a["y1"]
    bx, by, bw, bh = box
    bx0, by0, bx1, by1 = bx * W, by * H, (bx + bw) * W, (by + bh) * H
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


def _full_frame(path: str | Path) -> np.ndarray | None:
    """One grey frame at full size, from the middle of the video: where the borders are measured."""
    w, h = _size(path)
    if not w or not h:
        return None
    try:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, _duration(path) / 2.0):.1f}", "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=120).stdout or b""
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < w * h:
        return None
    return np.frombuffer(raw[:w * h], dtype=np.uint8).reshape(h, w).astype(np.float32)


def _best(profile: np.ndarray, approx: float, span: float) -> float:
    """The strongest change near a guessed line."""
    lo, hi = max(0, int(approx - span)), min(len(profile), int(approx + span) + 1)
    seg = profile[lo:hi]
    return float(lo + seg.argmax()) if len(seg) else float(approx)


def _snap(path: str | Path, box, w: int, h: int, span: float = 0.0):
    """A box on the small grid, put onto the borders that are really in the picture.

    A border the small grid found is a pixel or two out; a box a model proposed can be a good part of the
    frame out, which is what `span` is for. A side within a hair of the frame's own edge is the edge."""
    frame = _full_frame(path)
    if frame is None:
        return None
    fh, fw = frame.shape
    col = np.abs(np.diff(frame, axis=1)).sum(axis=0)
    row = np.abs(np.diff(frame, axis=0)).sum(axis=1)
    # The small grid is a quarter of the picture, so a border found on it is a few pixels out; one the
    # model proposed can be a good part of the frame out, which is what `span` is for.
    spread = max(4.0, span * max(fw, fh), 1.5 * max(fw / W, fh / H))

    def line(profile, approx, edge, extent):
        if abs(approx - edge) <= EDGE_FLUSH * extent:
            return float(edge)
        return _best(profile, approx, spread)

    x0, y0, x1, y1 = box
    return (line(col, x0 * fw / W, 0, fw), line(row, y0 * fh / H, 0, fh),
            line(col, x1 * fw / W, fw, fw), line(row, y1 * fh / H, fh, fh))


def _corner(x0, y0, x1, y1, w, h) -> str:
    """What part of the frame the box is on: its corner, its edge, or the middle."""
    near = EDGE_FLUSH
    left, right = x0 <= w * near, x1 >= w * (1 - near)
    top, bottom = y0 <= h * near, y1 >= h * (1 - near)
    if left and top:
        return "top-left"
    if right and top:
        return "top-right"
    if left and bottom:
        return "bottom-left"
    if right and bottom:
        return "bottom-right"
    if left or right:
        return "right" if right else "left"
    if top or bottom:
        return "bottom" if bottom else "top"
    return "middle"


def _finish(path: str | Path, found: dict, w: int, h: int, still: np.ndarray) -> dict | None:
    """The box in source pixels, with how sure it is that this is really a camera."""
    snapped = _snap(path, (found["x"], found["y"], found["x1"], found["y1"]), w, h)
    if not snapped:
        return None
    x0, y0, x1, y1 = snapped
    if x1 - x0 < w * MIN_SIZE or y1 - y0 < h * MIN_SIZE:
        return None
    inside = still[int(found["y"]):int(found["y1"]), int(found["x"]):int(found["x1"])]
    game = float(np.median(still))
    moving = float(np.median(inside)) if inside.size else 99.0
    # sure of it when the picture inside hardly moves while the game around it is racing
    conf = "high" if moving <= max(3.0, 0.25 * game) else "low"
    return {"x": int(round(x0)), "y": int(round(y0)), "w": int(round(x1 - x0)), "h": int(round(y1 - y0)),
            "corner": _corner(x0, y0, x1, y1, w, h), "score": found.get("score", 0.0), "conf": conf}


def _frame_jpeg(path: str | Path, width: int = 768) -> bytes:
    """One frame of the stream, small, as a JPEG: what a model is shown."""
    try:
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, _duration(path) / 2.0):.1f}", "-i", str(path),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-pix_fmt", "yuvj420p", "-q:v", "4",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=120).stdout or b""
    except Exception:  # noqa: BLE001
        return b""


def _asked(path: str | Path, ask):
    """One frame to the model: (did it look, its box as parts of the frame or None)."""
    jpeg = _frame_jpeg(path)
    if not jpeg:
        return False, None
    try:
        answer = ask(jpeg) or {}
    except Exception:  # noqa: BLE001 - a model that cannot look is not a failure
        return False, None
    return bool(answer.get("looked")), answer.get("box")


def _seen(path: str | Path, box, w: int, h: int, still: np.ndarray, light: np.ndarray) -> dict | None:
    """A box the model proposed, put onto the picture's real borders and checked like any other.

    A model reads a picture well but places a box roughly, and it can be wrong about the whole thing. So
    its box is only believed where the borders are really there and the picture inside really is still."""
    fx, fy, fw, fh = box
    snapped = _snap(path, (fx * W, fy * H, (fx + fw) * W, (fy + fh) * H), w, h, span=0.12)
    if not snapped:
        return None
    x0, y0, x1, y1 = snapped
    if x1 - x0 < w * MIN_SIZE or y1 - y0 < h * MIN_SIZE:
        return None
    small = (x0 * W / w, y0 * H / h, x1 * W / w, y1 * H / h)
    # The model has already said a person is here, so the picture inside is allowed to move more than a
    # measured box would be; what it may not be is the game itself.
    if _plausible(*small, still, light, float(np.median(still)), share=1.0) is None:
        return None
    found = {"x": small[0], "y": small[1], "x1": small[2], "y1": small[3], "score": 0.0}
    return _finish(path, found, w, h, still)


def detect(path: str | Path, ask=None) -> dict | None:
    """The facecam's box in the source, in source pixels, or None.

    The box is given as x, y, width, height, with the confidence in "score" and
    how sure we are that it is a camera in "conf".

    `ask` is shown one frame of the stream and says where the webcam is (brain.look_for_facecam). Its
    answer is measured again on the picture before it is believed, so a model that is wrong costs the
    search an idea, not a broken clip. Without `ask` nothing outside the file is looked at, which is how
    the tests run and how a job with no AI set still works."""
    frames = _frames(path)
    if len(frames) < 4:
        return None
    w, h = _size(path)
    if not w or not h:
        return None
    still = _stillness(frames)
    light = frames.astype(np.float32).mean(axis=0)
    looked, hinted = (False, None)
    if ask is not None:
        looked, hinted = _asked(path, ask)
        if looked and not hinted:
            return None                    # it looked, and there is no webcam on this stream
    vx, hy = _profiles(frames)
    vpeaks, hpeaks = _peaks(vx, 40), _peaks(hy, 40)
    lines = _lines(*_edge_maps(frames), vpeaks, hpeaks) if (vpeaks or hpeaks) else None
    found = _local(vpeaks, hpeaks, still, light, lines) if lines and (lines[0] or lines[1]) else []
    if looked and hinted:
        # The model is better than any measurement at saying "that is a person", and worse at saying
        # exactly where the lines are. So it picks which of the measured boxes it is, and only if it
        # matches none of them is its own box measured and used.
        match = max(found, key=lambda c: _overlap(c, hinted)) if found else None
        if match and _overlap(match, hinted) >= MATCH:
            return _finish(path, match, w, h, still)
        seen = _seen(path, hinted, w, h, still, light)
        if seen:
            return seen
    if not found:
        return None
    return _finish(path, found[0], w, h, still)
