"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover."""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config, roman
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed
def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"blur": "Full frame + blurred fill", "zoom": "Zoomed in + blurred fill", "crop": "Fill the screen (centre crop)",
           "left": "Fill the screen, keep the left side", "right": "Fill the screen, keep the right side",
           "fit": "Full frame on black", "split": "Split screen: left half on top, right half below (two people)",
           "gameplay": "Gameplay fills the screen, facecam in the corner",
           "bands": "Whole game width, facecam below. Cuts nothing"}
_FILLS = ("crop", "left", "right", "gameplay")      # layouts where the picture covers the whole frame

CAP_Y = 1430        # where captions sit in the layouts that leave a band at the bottom
GAME_TOP = 300      # where the game band starts in the bands layout, under the heading
GAME_FLOOR = 150    # the highest the game band may be pushed up to make room below
GAME_MAX = 820      # tallest the game band gets, so the facecam and the captions still fit under it
CAM_W = 460         # facecam width in the bands layout, where it gets a band of its own
CAM_GAP = 30        # space between the game and the facecam under it
CAM_CLEAR = 40      # clear space kept between the facecam band and the captions


def _font_dir() -> Path:
    """Where the caption fonts are: the copy next to the data, or the ones bundled with the code."""
    d = config.FONTS_DIR
    return d if any(d.glob("*.ttf")) else Path(__file__).resolve().parent / "fonts"


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def facecam_height(facecam=None, fg_h=None, hook_h=0) -> int:
    """How tall the webcam band is, from its own shape (16:9 when it is not known).

    With the game band's height given, the picture is shrunk if a portrait webcam would otherwise run into
    the captions: a smaller face is better than a face under the words."""
    nat = int(round(CAM_W * 9 / 16))
    if facecam and len(facecam) == 4 and float(facecam[2] or 0) > 0:
        nat = int(round(CAM_W * float(facecam[3]) / float(facecam[2])))
    nat = max(2, nat)
    if fg_h is None:
        return nat
    return max(2, min(nat, CAP_Y - CAM_CLEAR - max(GAME_FLOOR, int(round(hook_h)) + 28) - int(fg_h) - CAM_GAP))


def facecam_width(facecam=None, fg_h=None, hook_h=0) -> int:
    """How wide the webcam band is: its own width, narrower when its height had to be shrunk."""
    nat, h = facecam_height(facecam), facecam_height(facecam, fg_h, hook_h)
    return max(2, int(round(CAM_W * h / nat)))


def band_top(fg_h, cam=None, hook_h=0) -> int:
    """Where the game band starts: below the heading, and high enough that the camera and the captions fit."""
    highest = CAP_Y - CAM_CLEAR - int(cam or facecam_height()) - CAM_GAP - int(fg_h)
    want = max(GAME_TOP, int(round(hook_h)) + 28)
    return max(GAME_FLOOR, min(highest, want))


def geometry(src_w, src_h, layout, facecam=None, hook_h=0):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    if src_h >= src_w and layout != "crop":
        # already vertical or square: a band would gain nothing, so the whole frame fills the screen
        layout = "gameplay" if layout == "bands" else "crop"
    if layout not in LAYOUTS:
        layout = "blur"
    if layout in ("blur", "fit"):
        fg_h = min(H, int(round(W * src_h / src_w / 2)) * 2)
    elif layout == "zoom":
        fg_h = 1000
    elif layout == "bands":
        fg_h = min(GAME_MAX, int(round(W * src_h / src_w / 2)) * 2)      # 608 for a 16:9 stream
    else:
        fg_h = H
    top = (H - fg_h) // 2
    if layout in _FILLS:
        return layout, fg_h, 300, 1380
    if layout == "bands":
        # The game is never covered: the heading sits above it and the camera in its own band below.
        band = band_top(fg_h, facecam_height(facecam, fg_h, hook_h), hook_h)
        return layout, fg_h, band - max(118, int(round(hook_h)) // 2 + 16), CAP_Y
    if layout == "split":
        return layout, fg_h, 230, H // 2        # captions sit on the line between the two people
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


def _hook_shape(hook):
    """The heading as it is drawn: its font, size, lines, line height and the height of its white box."""
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    words = hook.split()
    if not words:
        return f, size, [], 0.0, 0
    lines = [" ".join(words)]
    if f.width(lines[0], size) > HOOK["max_w"] and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    return f, size, lines, line_h, line_h * len(lines) + 2 * HOOK["pad_y"]


def hook_height(hook) -> int:
    """How tall the heading's white box is, so the game band can start below it and not under it."""
    return int(round(_hook_shape(_clean(hook or ""))[4]))


def _hook_events(hook, dur, y, fade=160):
    """The heading: dark text in a white rounded box that fits it, on one line or two balanced ones."""
    f, size, lines, line_h, bh = _hook_shape(hook)
    if not lines:
        return []
    widest = max(f.width(l, size) for l in lines)
    bw = widest + 2 * HOOK["pad_x"]
    x0, y0, r = (W - bw) / 2, y - bh / 2, min(HOOK["radius"], bh / 2)
    x1, y1 = x0 + bw, y0 + bh
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(x0 + r)} {n(y0)} l {n(x1 - r)} {n(y0)} b {n(x1)} {n(y0)} {n(x1)} {n(y0)} {n(x1)} {n(y0 + r)} "
           f"l {n(x1)} {n(y1 - r)} b {n(x1)} {n(y1)} {n(x1)} {n(y1)} {n(x1 - r)} {n(y1)} "
           f"l {n(x0 + r)} {n(y1)} b {n(x0)} {n(y1)} {n(x0)} {n(y1)} {n(x0)} {n(y1 - r)} "
           f"l {n(x0)} {n(y0 + r)} b {n(x0)} {n(y0)} {n(x0)} {n(y0)} {n(x0 + r)} {n(y0)}")
    span = f"{_ts(0)},{_ts(dur)}"
    ev = [f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an7\\pos(0,0)\\fad({fade},0)\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}"]
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{cy:.0f})\\fad({fade},0)\\fs{size}}}{line}")
    return ev


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, fade=160) -> str:
    st = STYLES.get(style_name, STYLES["bounce"])
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    strong = {_key(h) for h in highlight if _key(h)}
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{st['outline']},0,5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = end - start
    ev = _hook_events(_clean(hook), dur, hook_y, fade)
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            # A script the caption font cannot draw (Japanese, Thai, Chinese...): its words cannot be measured, so
            # they are not placed one by one. The line is drawn whole and centred, in a font the system has for it.
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:                                  # a long phrase: shrink it to fit, never run off the frame
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]   # a space, plus room for both outlines
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        p = st["pop"]
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            p = max(104, min(st["pop"], 100 + int(2200 / max(wd, 1))))   # long words grow less, so they never touch their neighbours
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if _key(w["w"]) in strong else st["base"]
            if a - g_start >= 0.02:                                                   # waiting to be said
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:                                                         # being said: colour and a quick pop, in front
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{st['active']}&\\fscx94\\fscy94"
                          f"\\t(0,90,\\fscx{p}\\fscy{p})\\t(90,200,\\fscx{p - 5}\\fscy{p - 5})}}{t}")
            if g_end - b >= 0.02:                                                     # said
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 5}\\fscy{p - 5}"
                          f"\\t(0,90,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 380            # how wide the facecam is in the 1080-wide frame
PIP_MARGIN = 26


def _box(facecam):
    """The webcam box as four whole numbers, or None when it is missing or too small to be a picture."""
    if not facecam or len(facecam) != 4:
        return None
    try:
        x, y, w, h = (int(round(float(v))) for v in facecam)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 8 and h > 8 and x >= 0 and y >= 0 else None


def _filter(layout, fg_h, facecam=None, hook_h=0):
    face = _box(facecam)
    if layout == "bands" and face:
        # The game keeps its whole width in its own band, and the webcam gets a band under it, so nothing of
        # either picture is cut off. The frame behind them is the same moment, blurred, so it never looks empty.
        fx, fy, fw, fh = face
        ch, cw = facecam_height(face, fg_h, hook_h), facecam_width(face, fg_h, hook_h)
        top = band_top(fg_h, ch, hook_h)
        return (f"[0:v]fps={config.OUT_FPS},setpts=PTS-STARTPTS,split=3[bg][g][fc];"
                f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
                f"scale={W}:{H},eq=brightness=-0.2:saturation=1.12[base];"
                f"[g]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[game];"
                f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={cw}:{ch},setsar=1[p];"
                f"[base][game]overlay=0:{top}[b];"
                f"[b][p]overlay={(W - cw) // 2}:{top + fg_h + CAM_GAP},setsar=1[v0];")
    if layout == "gameplay" and face:
        # The game fills the screen, and the webcam sits in the top corner of it, so a viewer sees
        # the play and the face at the same time.
        fx, fy, fw, fh = face
        pip_h = max(2, int(round(PIP_W * fh / fw)))
        return (f"[0:v]fps={config.OUT_FPS},split=2[base][fc];"
                f"[base]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[b];"
                f"[fc]crop={fw}:{fh}:{fx}:{fy},scale={PIP_W}:{pip_h},setsar=1[p];"
                f"[b][p]overlay={W - PIP_W - PIP_MARGIN}:{PIP_MARGIN}[v0];")
    fill = f"[0:v]fps={config.OUT_FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if layout in ("crop", "bands") or (layout == "gameplay" and not face):
        return fill + ",setsar=1[v0];"
    if layout == "left":
        return fill + ":0:0,setsar=1[v0];"
    if layout == "right":
        return fill + ":in_w-out_w:0,setsar=1[v0];"
    if layout == "fit":
        return f"[0:v]fps={config.OUT_FPS},scale={W}:{fg_h},pad={W}:{H}:0:(oh-ih)/2:black,setsar=1[v0];"
    if layout == "split":
        half = f"scale={W}:{H // 2}:force_original_aspect_ratio=increase,crop={W}:{H // 2}"
        return (f"[0:v]fps={config.OUT_FPS},split=2[l][r];[l]crop=iw/2:ih:0:0,{half}[t];"
                f"[r]crop=iw/2:ih:iw/2:0,{half}[u];[t][u]vstack,setsar=1[v0];")
    return (f"[0:v]fps={config.OUT_FPS},split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=6:2,"
            f"scale={W}:{H},eq=brightness=-0.14:saturation=1.15[bg];"
            f"[b]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h}[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v0];")


def _hook_y(layout, hook_y, facecam=None):
    """Where the heading goes. In the gameplay layout the webcam sits in the top corner, so a heading
    across the top would run under it: it goes just below the picture instead."""
    if layout == "gameplay" and facecam and len(facecam) == 4 and facecam[2] > 8:
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 32
    return hook_y


def layout_for(clip, src_w, src_h, hook_h=0):
    """The layout a clip will really be made in, with the fallbacks: (layout, band height, hook y, caption y).

    Bands need a webcam to give a band to. Without one they become the blurred fill, which keeps every pixel
    of the game. Gameplay, asked for by name, keeps filling the screen without the corner picture."""
    face = _box(clip.get("facecam"))
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, clip.get("layout", "blur"), face, hook_h)
    if layout == "bands" and not face:
        return geometry(src_w, src_h, "blur")
    if layout == "gameplay" and not face:
        return geometry(src_w, src_h, "crop")
    return layout, fg_h, hook_y, cap_y


_qsv_ok = None
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


def _clip_ass(clip: dict, words: list, src_w: int, src_h: int, fade: int = 160):
    """The caption file for a clip, the layout it will really be made in, and how tall its heading is."""
    hh = hook_height(clip.get("hook") or "")
    layout, fg_h, hook_y, cap_y = layout_for(clip, src_w, src_h, hh)
    face = _box(clip.get("facecam"))
    hook_y = _hook_y(layout, hook_y, face)
    ass = build_ass(words if clip.get("captions", True) else [], clip["start"], clip["end"], clip.get("hook", ""),
                    clip.get("style", "bounce"), clip.get("highlight_words", []), hook_y, cap_y, fade=fade)
    return layout, fg_h, face, ass, hh


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    layout, fg_h, face, ass, hook_h = _clip_ass(clip, words, src_w, src_h)
    # The clip is made in a short work folder and moved into place afterwards. The caption step of ffmpeg cannot
    # open files whose full path is longer than 260 characters on Windows, which a data folder deep inside other
    # folders easily reaches. Inside the work folder every name is short and relative (a drive colon would also
    # break the filter syntax).
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        # The title and description are also written into the file itself, for any place that reads them from it.
        meta = {"title": clip.get("title") or "", "description": clip.get("description") or "", "comment": clip.get("description") or ""}
        return _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, face=face, hook_h=hook_h, meta=meta)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def still(src: Path, out: Path, clip: dict, words: list, src_w: int, src_h: int) -> Path:
    """One frame from the middle of the clip, laid out exactly as the clip will be, written as a picture.

    `clip` is the same shape render_clip gets: start and end already measured from the start of `src`.
    The heading is shown from the first frame here, so the picture shows what a viewer sees at the start."""
    out = Path(out)
    work = Path(tempfile.mkdtemp(prefix="pk"))
    try:
        layout, fg_h, face, ass, hook_h = _clip_ass(clip, words, src_w, src_h, fade=0)
        name = "p"
        (work / f"{name}.ass").write_text(ass, encoding="utf-8")
        (work / "f").mkdir()
        for f in _font_dir().glob("*.ttf"):
            shutil.copyfile(f, work / "f" / f.name)
        at = clip["start"] + (clip["end"] - clip["start"]) / 2
        fc = _filter(layout, fg_h, face, hook_h) + f"[v0]ass={name}.ass:fontsdir=f[v]"
        p = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, at):.2f}", "-i", str(src),
                            "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", "-q:v", "3", f"{name}.jpg"],
                           cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode != 0:
            raise RuntimeError(f"Preview failed: {p.stderr[-800:]}")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work / f"{name}.jpg"), str(out))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str, face=None, hook_h=0, meta=None) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in _font_dir().glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)
    fc = _filter(layout, fg_h, face, hook_h) + f"[v0]ass={name}.ass:fontsdir=f[v]"
    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(src),
            "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    for k, v in (meta or {}).items():
        if v:
            base += ["-metadata", f"{k}={v}"]
    base += ["-movflags", "+faststart", "-pix_fmt"]
    encoders = []
    if _qsv_ok is not False:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "23", "-preset", "fast"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-crf", "21", "-preset", "veryfast"]))
    err = None
    for label, enc in encoders:
        p = subprocess.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    else:
        raise RuntimeError(f"Render failed: {err}")
    cover_t = min(1.2, (end - start) / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", f"{name}.jpg"], cwd=work)
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": layout}
