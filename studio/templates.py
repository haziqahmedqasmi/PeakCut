"""Templates: how a clip looks. One template holds one choice of each thing: the caption style, font, colours, size
and place, whether the heading shows and where, and the video layout.

Ready-made templates (BUILTIN) can't be changed, only copied. Each account keeps its own list next to them, in
users/<user>/templates.json, plus which template is its default. A clip keeps a copy of its template's look (`look`),
so changing or deleting a template later never changes clips already made."""
import copy
import hashlib
import json
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, ctx

# The fonts a caption can use: the name the renderer knows it by -> the file in studio/fonts
FONTS = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Poppins Black": "Poppins-Black.ttf", "Anton": "Anton-Regular.ttf",
         "Archivo Black": "ArchivoBlack-Regular.ttf", "Bebas Neue": "BebasNeue-Regular.ttf", "Bangers": "Bangers-Regular.ttf",
         "Luckiest Guy": "LuckiestGuy-Regular.ttf", "Lilita One": "LilitaOne-Regular.ttf", "Titan One": "TitanOne-Regular.ttf",
         "Permanent Marker": "PermanentMarker-Regular.ttf", "Righteous": "Righteous-Regular.ttf"}
# how the word being said stands out: grows in its colour, sits in a box of that colour, the said words fill with it, or nothing
ANIMS = {"pop": "Pop", "box": "Box", "fill": "Fill", "none": "Plain"}
LAYOUTS = ("blur", "zoom", "crop", "left", "right", "fit", "split", "gameplay", "podcast")
MAX_OWN = 30

_CAP = {"show": True, "anim": "pop", "font": "Poppins ExtraBold", "color": "FFFFFF", "hi": "FFF000", "key": "FFFFFF", "outline": 7,
        "shadow": False, "size": 132, "per": 3, "upper": True, "pop": 112, "y": None, "bg": False, "bgc": "000000"}
# the heading: its font, text colour, and the rounded box behind it (on or off, and its colour)
_HOOK = {"show": True, "y": None, "font": "Poppins ExtraBold", "color": "0E1014", "bg": True, "bgc": "FFFFFF"}


def _look(cap=None, layout="blur", hook=None, cam=None):
    return {"cap": {**_CAP, **(cap or {})}, "hook": {**_HOOK, **(hook or {})}, "layout": layout, "cam": cam,
            "cam_src": None, "game_src": None}


# the three caption styles from before templates, exactly as they were drawn (render.STYLES had them)
LEGACY = {"bounce": {},
          "hype": {"font": "Anton", "size": 152, "hi": "FF3C3C", "key": "FFD700", "per": 2, "pop": 114},
          "clean": {"size": 100, "hi": "FFD76B", "outline": 5, "upper": False, "per": 4, "pop": 106}}
LEGACY_NAMES = {"bounce": "Bounce", "hype": "Hype", "clean": "Clean"}
LAYOUT_NAMES = {"blur": "Full + blur", "zoom": "Zoomed in", "crop": "Fill the screen", "left": "Fill, keep the left side",
                "right": "Fill, keep the right side", "fit": "Full frame on black", "split": "Split screen (two people)",
                "gameplay": "Gameplay + facecam in the corner", "podcast": "Podcast split (follows the camera)"}

BUILTIN = [
    {"id": "b-bounce", "name": "Bounce", "look": _look(LEGACY["bounce"])},
    {"id": "b-hype", "name": "Hype", "look": _look(LEGACY["hype"])},
    {"id": "b-clean", "name": "Clean", "look": _look(LEGACY["clean"])},
    {"id": "b-box", "name": "Box", "look": _look({"anim": "box", "font": "Poppins Black", "size": 118, "hi": "FF2E63",
                                                  "outline": 0, "shadow": True, "per": 3})},
    {"id": "b-fill", "name": "Fill", "look": _look({"anim": "fill", "font": "Titan One", "size": 112, "hi": "4ADE80",
                                                    "outline": 8, "upper": False, "per": 4})},
    {"id": "b-word", "name": "One word", "look": _look({"font": "Luckiest Guy", "size": 172, "hi": "FFE600", "outline": 9, "per": 1, "pop": 108})},
    {"id": "b-minimal", "name": "Minimal", "look": _look({"anim": "none", "size": 80, "outline": 0, "shadow": True,
                                                          "upper": False, "per": 5, "y": 76}, hook={"show": False})},
    {"id": "b-full", "name": "Full screen", "look": _look(LEGACY["hype"], layout="crop")},
    {"id": "b-podcast", "name": "Podcast split", "look": _look(LEGACY["bounce"], layout="podcast")},
    {"id": "b-cam", "name": "Cam on top", "look": _look(LEGACY["bounce"], layout="crop", cam={"x": 0, "y": 0, "w": 100, "h": 40})},
]
DEFAULT_ID = "b-bounce"
_BY_ID = {t["id"]: t for t in BUILTIN}

_lock = threading.RLock()
_mem: dict = {}


def _hex(v, d):
    v = str(v or "").strip().lstrip("#").upper()
    return v if re.fullmatch(r"[0-9A-F]{6}", v) else d


def _num(v, lo, hi, d):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return d


def _pct(v):
    """A place on the screen, as a share of its height (5 to 95), or None for the layout's own place."""
    if v in (None, "", "auto"):
        return None
    try:
        return max(5.0, min(95.0, round(float(v), 1)))
    except (TypeError, ValueError):
        return None


def clean_look(raw) -> dict:
    """A look, tidied: every value known and in range. Anything missing or broken takes the Bounce value."""
    raw = raw if isinstance(raw, dict) else {}
    c = raw.get("cap") if isinstance(raw.get("cap"), dict) else {}
    h = raw.get("hook") if isinstance(raw.get("hook"), dict) else {}
    cap = {"show": bool(c.get("show", True)), "anim": c.get("anim") if c.get("anim") in ANIMS else _CAP["anim"],
           "font": c.get("font") if c.get("font") in FONTS else _CAP["font"],
           "color": _hex(c.get("color"), _CAP["color"]), "hi": _hex(c.get("hi"), _CAP["hi"]),
           "key": _hex(c.get("key"), _hex(c.get("color"), _CAP["key"])),
           "outline": _num(c.get("outline"), 0, 14, _CAP["outline"]), "shadow": bool(c.get("shadow")),
           "size": _num(c.get("size"), 60, 200, _CAP["size"]), "per": _num(c.get("per"), 1, 5, _CAP["per"]),
           "upper": bool(c.get("upper", _CAP["upper"])),
           "pop": _num(c.get("pop"), 100, 120, _CAP["pop"]), "y": _pct(c.get("y")),
           "bg": bool(c.get("bg")), "bgc": _hex(c.get("bgc"), _CAP["bgc"])}
    hook = {"show": bool(h.get("show", True)), "y": _pct(h.get("y")),
            "font": h.get("font") if h.get("font") in FONTS else _HOOK["font"], "color": _hex(h.get("color"), _HOOK["color"]),
            "bg": bool(h.get("bg", True)), "bgc": _hex(h.get("bgc"), _HOOK["bgc"])}
    hook["outline"] = _num(h.get("outline"), 0, 14, 0 if hook["bg"] else 6)     # a black edge round the letters
    hook["size"] = _num(h.get("size"), 60, 160, 96)                                # 96: the classic heading's size
    hook["upper"] = bool(h.get("upper"))
    from . import facecam
    # drawn by hand on the video (shares of its frame): where the cam is in the stream (None: found by AI), and the
    # part of the video to show (None: all of it)
    cs = raw.get("cam_style") if isinstance(raw.get("cam_style"), dict) else {}
    # the cam's look: rounded corners (radius as % of the box's shorter side: 50 is fully round), and a border
    cam_style = {"r": _num(cs.get("r"), 0, 50, 0), "bw": _num(cs.get("bw"), 0, 24, 0), "bc": _hex(cs.get("bc"), "FFFFFF")}
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "blur"
    cam = None if layout == "podcast" else _cam(raw.get("cam"))     # the podcast split places the people itself
    return {"cap": cap, "hook": hook, "cam": cam, "cam_style": cam_style, "cam_src": facecam.clean(raw.get("cam_src")),
            "game_src": facecam.clean(raw.get("game_src")),
            "layout": layout}


def _cam(v):
    """Where the streamer's camera goes in the clip: {"x", "y", "w", "h"} as shares of the 9:16 frame (0-100), or None."""
    if not isinstance(v, dict):
        return None
    try:
        w, h = max(15.0, min(100.0, float(v["w"]))), max(8.0, min(100.0, float(v["h"])))
        x, y = max(0.0, min(100.0 - w, float(v["x"]))), max(0.0, min(100.0 - h, float(v["y"])))
    except (KeyError, TypeError, ValueError):
        return None
    return {"x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(h, 1)}


def legacy(style="bounce", layout="blur") -> dict:
    """The look of a clip made before templates, from its caption style and layout."""
    return _look(LEGACY.get(style, LEGACY["bounce"]), layout if layout in LAYOUTS else "blur")


def _name(v):
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f<>{}]+", " ", str(v or ""))).strip()[:40]


def _file():
    return config.user_dir(ctx.uid()) / "templates.json"


def _read() -> dict:
    u = ctx.uid()
    got = _mem.get(u)
    if got is None:
        try:
            raw = json.loads(_file().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        own = []
        for t in raw.get("list") or []:
            if isinstance(t, dict) and re.fullmatch(r"t-[0-9a-f]{8}", str(t.get("id", ""))):
                own.append({"id": t["id"], "name": _name(t.get("name")) or "My style", "look": clean_look(t.get("look"))})
        got = {"list": own[:MAX_OWN], "default": str(raw.get("default") or "")}
        _mem[u] = got
    return copy.deepcopy(got)


def _write(data: dict):
    with _lock:
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(f)
        _mem[ctx.uid()] = copy.deepcopy(data)
    ctx.bump()


def all_templates() -> dict:
    """Everything the Templates page and the pickers need: ready-made ones first, then the account's own."""
    from . import render                 # (render uses this module, so it is fetched here)
    d = _read()
    hq = lambda t: dict(t, hq=render.needed_height(t["look"]) > 1080)                         # noqa: E731
    return {"builtin": [hq(dict(t, builtin=True)) for t in copy.deepcopy(BUILTIN)], "own": [hq(t) for t in d["list"]],
            "default": default_id(d), "fonts": dict(FONTS), "anims": ANIMS, "layouts": LAYOUT_NAMES,
            # how big each font's letters are for its caption size, so the browser preview matches the clip
            "em": {n: round(render._Font.get(n).em(1), 4) for n in FONTS},
            "hk": {n: round(render.hook_scale(n), 4) for n in FONTS},
            "sample_cam": _sample_cam_quiet(), "sample_v": sample_v()}


def _sample_cam_quiet():
    f = config.user_dir(ctx.uid()) / "style-sample-cam.json"      # only what's already known: never slows the page
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("cam") if f.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def default_id(d=None) -> str:
    d = d or _read()
    ids = {t["id"] for t in d["list"]} | set(_BY_ID)
    return d["default"] if d["default"] in ids else DEFAULT_ID


def get(tid):
    """A template by id (ready-made or the account's own), or None."""
    if tid in _BY_ID:
        return copy.deepcopy(_BY_ID[tid])
    if not tid or ctx.uid_or_none() is None:
        return None
    return next((t for t in _read()["list"] if t["id"] == tid), None)


def pick(tid=None, creator=None):
    """(template id, look) for new clips: the one asked for, else the streamer's, else the account's default."""
    for t in (tid, (creator or {}).get("template")):
        got = get(t) if t else None
        if got:
            return got["id"], clean_look(got["look"])
    if ctx.uid_or_none() is None:
        return DEFAULT_ID, clean_look(_BY_ID[DEFAULT_ID]["look"])
    d = default_id()
    return d, clean_look(get(d)["look"])


def add(data: dict) -> dict:
    d = _read()
    if len(d["list"]) >= MAX_OWN:
        raise ValueError(f"You can keep up to {MAX_OWN} styles. Delete one to make room.")
    name = _name(data.get("name")) or "My style"
    t = {"id": "t-" + secrets.token_hex(4), "name": name, "look": clean_look(data.get("look"))}
    d["list"].append(t)
    if data.get("default"):
        d["default"] = t["id"]
    _write(d)
    return t


def edit(tid: str, data: dict) -> dict:
    d = _read()
    t = next((x for x in d["list"] if x["id"] == tid), None)
    if not t:
        raise KeyError("No such template")
    if "name" in data:
        t["name"] = _name(data["name"]) or t["name"]
    if "look" in data:
        t["look"] = clean_look(data["look"])
    _write(d)
    return t


def drop(tid: str):
    d = _read()
    if not any(x["id"] == tid for x in d["list"]):
        raise KeyError("No such template")
    d["list"] = [x for x in d["list"] if x["id"] != tid]
    if d["default"] == tid:
        d["default"] = ""
    _write(d)


def set_default(tid: str):
    if not get(tid):
        raise KeyError("No such template")
    d = _read()
    d["default"] = tid
    _write(d)


def for_legacy(style, layout) -> str:
    """The template that draws clips the way a streamer's old caption style and layout did: a ready-made one when
    there is one, otherwise one made for it in the account's list (once; the same look is reused)."""
    style = style if style in LEGACY else "bounce"
    layout = layout if layout in LAYOUTS else "blur"
    if layout == "blur":
        return "b-" + style
    if style == "hype" and layout == "crop":
        return "b-full"
    look = clean_look(legacy(style, layout))
    d = _read()
    same = next((t for t in d["list"] if t["look"] == look), None)
    if same:
        return same["id"]
    if len(d["list"]) >= MAX_OWN:
        return "b-" + style
    return add({"name": f"{LEGACY_NAMES[style]} · {LAYOUT_NAMES[layout]}", "look": look})["id"]


SAMPLE_SECONDS = 10


def _job_sources(j) -> list:
    """The files of one video or recording the previews can be cut from: the whole video, else its clips' footage,
    else the pieces of its recording (longest first)."""
    out = [j.get("source_path")] + [c.get("src") for c in reversed(j.get("clips") or []) if c.get("status") == "done"]
    buf = Path(j.get("dir") or "") / "buffer"
    # a recording still going: only its finished clips (the pieces being written now come out broken)
    if j.get("dir") and buf.is_dir() and j.get("status") != "live":
        out += [str(p) for p in sorted(buf.glob("*.ts"), key=lambda p: -p.stat().st_size)[:4]]
    return [x for x in out if x and Path(x).is_file()]


def sample_videos() -> list:
    """The account's videos and recordings that previews can play, newest first."""
    from . import creators, jobs
    picked = _picked()
    out = []
    for j in jobs.list_jobs():
        if not _job_sources(j):
            continue
        c = creators.get(j["creator"]) if j.get("creator") else None
        out.append({"id": j["id"], "title": j.get("title") or (j.get("source") or {}).get("name") or "Video", "kind": j.get("kind"),
                    "streamer": c["name"] if c else "", "created": j.get("created"), "picked": j["id"] == picked,
                    "live": j.get("status") == "live",        # recording right now (a past stream is just a recording)
                    "thumb": j.get("thumbUrl") or (f"/media/{j['id']}/thumb.jpg" if j.get("thumb") else f"/api/templates/videos/{j['id']}/thumb.jpg")})
        if len(out) >= 40:
            break
    return out


def video_thumb(job_id: str):
    """A picture of a video or recording for the list (made once from its footage), or None."""
    import subprocess
    from . import jobs, media
    j = next((x for x in jobs.list_jobs() if x["id"] == job_id), None)
    if not j:
        return None
    out = Path(j["dir"]) / "style-thumb.jpg"
    if out.is_file():
        return out
    for src in _job_sources(j)[:3]:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", src, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(out)],
                       capture_output=True, timeout=60, creationflags=media.NO_WINDOW)
        if out.is_file() and out.stat().st_size > 1000:
            return out
    return None


def _picked() -> str:
    f = config.user_dir(ctx.uid()) / "style-sample-pick.txt"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else ""


def pick_sample(job_id: str):
    """Previews play this video or recording from now on (made again now)."""
    from . import jobs
    j = next((x for x in jobs.list_jobs() if x["id"] == job_id), None)
    if not j or not _job_sources(j):
        raise KeyError("That video can't be used for previews.")
    d = config.user_dir(ctx.uid())
    (d / "style-sample-pick.txt").write_text(job_id, encoding="utf-8")
    for n in ("style-sample.txt", "style-sample-cam.json"):
        (d / n).unlink(missing_ok=True)
    sample()
    return sample_cam()


def sample_v() -> int:
    """Changes when the preview video does (so pages fetch the new one)."""
    f = config.user_dir(ctx.uid()) / "style-sample.mp4"
    return int(f.stat().st_mtime) if f.is_file() else 0


def _preview_stills(mp4: Path, jpg: Path, blur: Path):
    """First picture of a sample clip, plus the blurred version behind it."""
    import subprocess
    from . import media
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0", "-i", str(mp4), "-frames:v", "1", "-q:v", "4", str(jpg)],
                   capture_output=True, timeout=60, creationflags=media.NO_WINDOW, check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(jpg), "-vf", "scale=96:54,boxblur=6:2,eq=brightness=-0.14:saturation=1.15,scale=320:180",
                    "-q:v", "5", str(blur)], capture_output=True, timeout=60, creationflags=media.NO_WINDOW, check=True)


def sample() -> tuple:
    """(video, picture) the style previews play: ten silent seconds of the video the account picked for it, else its
    newest video, so people see their styles on their own footage; or moving colours when they have none yet.
    Made once and kept; a blurred picture of it is kept too (style-sample-blur.jpg), for the fill behind the video."""
    import subprocess
    from . import jobs, media
    d = config.user_dir(ctx.uid())
    d.mkdir(parents=True, exist_ok=True)
    mp4, jpg, made, blur = d / "style-sample.mp4", d / "style-sample.jpg", d / "style-sample.txt", d / "style-sample-blur.jpg"
    picked, cands = _picked(), []
    for j in jobs.list_jobs():
        if j["id"] == picked:
            cands = _job_sources(j) + cands
        else:
            cands += _job_sources(j)[:2]
        if len(cands) >= 12 and not picked:
            break
    cands = list(dict.fromkeys(cands))[:12]
    # kept once made, so previews don't change with every new video; made again when its video is gone (or when it
    # was the moving colours and there's a video now)
    if mp4.is_file() and mp4.stat().st_size > 20000 and jpg.is_file() and blur.is_file() and made.is_file():
        was = made.read_text(encoding="utf-8")
        if (was == "colours" and not cands) or (was != "colours" and Path(was).is_file()):
            return mp4, jpg
    tmp = d / "style-sample.tmp.mp4"

    def make(inp):
        subprocess.run(["ffmpeg", "-y", "-v", "error", *inp, "-an", "-vf", "scale=640:360:force_original_aspect_ratio=increase,crop=640:360,fps=24",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, timeout=120, creationflags=media.NO_WINDOW)
        return tmp.is_file() and tmp.stat().st_size > 20000          # a real few seconds of video, not an empty file

    ok, key = False, "colours"
    for src in cands:
        try:      # how long this file really is (a recording's piece is much shorter than the recording)
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", src],
                                       capture_output=True, text=True, timeout=30, creationflags=media.NO_WINDOW).stdout.strip() or 0)
        except (ValueError, subprocess.SubprocessError, OSError):
            continue
        if dur < 3:
            continue
        start = max(0.0, min(dur * 0.3, dur - SAMPLE_SECONDS - 1)) if dur > SAMPLE_SECONDS + 2 else 0.0
        if make(["-ss", f"{start:.1f}", "-t", str(SAMPLE_SECONDS), "-i", src]):
            ok, key = True, src
            break
    if not ok and not make(["-f", "lavfi", "-i", f"gradients=s=640x360:c0=0x5B3B2C:c1=0xC26A3A:c2=0x3A5A6B:c3=0xFF8B33:speed=0.03:d={SAMPLE_SECONDS}"]):
        raise RuntimeError("Couldn't make the style preview video")
    tmp.replace(mp4)
    _preview_stills(mp4, jpg, blur)
    made.write_text(key, encoding="utf-8")
    (d / "style-sample-cam.json").unlink(missing_ok=True)
    return mp4, jpg


def sample_cam():
    """Where the streamer's cam is in the preview video (so previews show the real cam in a style's cam box), or None."""
    from . import facecam
    d = config.user_dir(ctx.uid())
    f, mp4 = d / "style-sample-cam.json", d / "style-sample.mp4"
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("cam")
        except Exception:  # noqa: BLE001
            pass
    if not mp4.is_file():
        return None
    src = (d / "style-sample.txt").read_text(encoding="utf-8") if (d / "style-sample.txt").is_file() else ""
    try:      # looked for in the full video it was cut from: the preview itself is too small to read well
        cam = facecam.find([src if src != "colours" and Path(src).is_file() else mp4], 12)
    except Exception:  # noqa: BLE001
        cam = None
    f.write_text(json.dumps({"cam": cam}), encoding="utf-8")
    return cam


LINK_SAMPLE_SECONDS = 5
LINK_CACHE_MAX = 8
KIND_LABELS = {"cam": "Facecam found", "podcast": "2 speakers found", "person": "Person on camera",
               "screen": "Full-screen scene", "vertical": "Vertical video"}
_link_lock = threading.Lock()
_link_inflight: dict = {}
_stream_cache: dict = {}


def _link_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def _link_dir(key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        raise KeyError("Invalid sample key")
    return config.user_dir(ctx.uid()) / "link-samples" / key


def _prune_link_cache(root: Path):
    if not root.is_dir():
        return
    dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dirs[LINK_CACHE_MAX:]:
        shutil.rmtree(old, ignore_errors=True)


def _cached_stream(url: str) -> dict:
    now = time.time()
    with _link_lock:
        hit = _stream_cache.get(url)
        if hit and now < hit[0]:
            return hit[1]
    from . import media
    info = media.stream_info(url)
    with _link_lock:
        if len(_stream_cache) > 64:
            _stream_cache.pop(next(iter(_stream_cache)))
        _stream_cache[url] = (now + 240, info)
    return info


def _ffmpeg_net_args(sinfo: dict) -> list:
    args = []
    if sinfo.get("proxy"):
        args += ["-http_proxy", sinfo["proxy"]]
    headers = sinfo.get("headers") or {}
    ua = str(headers.get("User-Agent") or "").replace("\r", "").replace("\n", "")
    if ua:
        args += ["-user_agent", ua]
    extra = [f"{str(k).strip()}: {str(v).replace(chr(13), '').replace(chr(10), '')}"
             for k, v in headers.items() if k and v and str(k).lower() not in ("user-agent", "host", "connection", "accept-encoding")]
    if extra:
        args += ["-headers", "\r\n".join(extra) + "\r\n"]
    return args


def _sample_times(dur: float, is_live: bool) -> list:
    if is_live or dur <= 6:
        return [0.0]
    if dur <= 30:
        return [round(dur * f, 1) for f in (0.2, 0.5, 0.78)]
    return [round(dur * f, 1) for f in (0.22, 0.48, 0.72)]


def _render_sample_clip(inp_args: list, tmp_mp4: Path, out_mp4: Path, timeout: int = 45) -> bool:
    import subprocess
    from . import media
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inp_args, "-an",
                    "-vf", "scale=640:360:force_original_aspect_ratio=increase,crop=640:360,fps=24",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp_mp4)],
                   capture_output=True, timeout=timeout, creationflags=media.NO_WINDOW)
    if tmp_mp4.is_file() and tmp_mp4.stat().st_size > 12000:
        tmp_mp4.replace(out_mp4)
        return True
    tmp_mp4.unlink(missing_ok=True)
    return False


def _cut_moment_clip(url: str, sinfo: dict, t: float, d: Path, fallback_jpg: Path | None) -> None:
    import subprocess
    from . import media
    mp4, jpg, blur = d / "sample.mp4", d / "sample.jpg", d / "sample-blur.jpg"
    tmp_mp4 = d / "sample.tmp.mp4"
    dur = float(sinfo.get("duration") or 0)
    is_live = bool(sinfo.get("is_live"))
    seek = 0.0 if is_live else (max(0.0, min(t, max(0.0, dur - LINK_SAMPLE_SECONDS - 0.5))) if dur > LINK_SAMPLE_SECONDS + 1 else 0.0)
    ok = False
    if sinfo.get("url"):
        net = _ffmpeg_net_args(sinfo)
        ss = [] if is_live else ["-ss", f"{seek:.2f}"]
        ok = _render_sample_clip([*net, *ss, "-t", str(LINK_SAMPLE_SECONDS), "-i", sinfo["url"]], tmp_mp4, mp4)
    if not ok and not is_live:
        try:
            end = seek + LINK_SAMPLE_SECONDS + 1
            seg = media.download_section(url, d, "seg.tmp", seek, end, height=480)
            ok = _render_sample_clip(["-ss", "0", "-t", str(LINK_SAMPLE_SECONDS), "-i", str(seg)], tmp_mp4, mp4)
            seg.unlink(missing_ok=True)
        except media.YouTubeBlocked:
            raise
        except Exception:  # noqa: BLE001
            for p in d.glob("seg.tmp*"):
                p.unlink(missing_ok=True)
    if not ok and fallback_jpg and fallback_jpg.is_file():
        ok = _render_sample_clip(["-loop", "1", "-t", str(LINK_SAMPLE_SECONDS), "-i", str(fallback_jpg)], tmp_mp4, mp4, timeout=30)
    if not ok:
        raise RuntimeError("Couldn't grab a preview from that video.")
    _preview_stills(mp4, jpg, blur)


def link_sample(url: str, moment: int | None = None) -> dict:
    """Quickly grab 2-3 frames from inside a pasted video URL, detect framing/facecam, and cut a 5s preview clip."""
    import subprocess
    from . import facecam, media
    url = url.strip()
    key = _link_key(url)
    root = config.user_dir(ctx.uid()) / "link-samples"
    d = root / key
    meta_path, mp4, jpg, blur = d / "meta.json", d / "sample.mp4", d / "sample.jpg", d / "sample-blur.jpg"

    # Fast cache hit (or switch active moment in already-probed video)
    if moment is None and meta_path.is_file() and mp4.is_file() and mp4.stat().st_size > 12000 and jpg.is_file() and blur.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            d.touch()
            return meta
        except Exception:  # noqa: BLE001
            pass

    slot_key = (ctx.uid(), key, moment)
    with _link_lock:
        running = _link_inflight.get(slot_key)
        if running is None:
            owner = True
            running = {"ev": threading.Event(), "meta": None, "err": None}
            _link_inflight[slot_key] = running
        else:
            owner = False

    if not owner:
        running["ev"].wait(timeout=70)
        if running["err"] is not None:
            raise running["err"]
        if running["meta"] is not None:
            return running["meta"]

    try:
        d.mkdir(parents=True, exist_ok=True)
        # Switch between already-captured moments
        if moment is not None and meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            moments = meta.get("moments") or [0.0]
            idx = max(0, min(len(moments) - 1, int(moment)))
            if idx == meta.get("active") and mp4.is_file():
                running["meta"] = meta
                return meta
            sinfo = _cached_stream(url)
            fb = d / f"f{idx}.jpg"
            _cut_moment_clip(url, sinfo, moments[idx], d, fb if fb.is_file() else None)
            meta["active"] = idx
            meta["best_t"] = moments[idx]
            meta["v"] = int(time.time() * 1000)
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            running["meta"] = meta
            return meta

        sinfo = _cached_stream(url)
        dur = float(sinfo.get("duration") or 0)
        is_live = bool(sinfo.get("is_live"))
        vertical = bool(sinfo.get("height") and sinfo.get("width") and sinfo["height"] > sinfo["width"])
        ts = _sample_times(dur, is_live)
        net = _ffmpeg_net_args(sinfo)
        frames = [None] * len(ts)

        def grab_one(idx, t):
            out_f = d / f"f{idx}.jpg"
            ss = [] if is_live else ["-ss", f"{t:.2f}"]
            cmd = ["ffmpeg", "-y", "-v", "error", *net, *ss, "-i", sinfo["url"], "-frames:v", "1",
                   "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(out_f)]
            subprocess.run(cmd, capture_output=True, timeout=22, creationflags=media.NO_WINDOW)
            if out_f.is_file() and out_f.stat().st_size > 2000:
                frames[idx] = out_f
            else:
                out_f.unlink(missing_ok=True)

        if sinfo.get("url") and not is_live:
            with ThreadPoolExecutor(max_workers=min(3, len(ts))) as pool:
                futs = [pool.submit(grab_one, i, t) for i, t in enumerate(ts)]
                for f in futs:
                    f.result()

        valid_pairs = [(i, frames[i]) for i in range(len(ts)) if frames[i] is not None]
        seg_tmp = None
        if not valid_pairs:
            # Segmented manifest / live fallback: pull one brief stretch once and grab up to 3 frames from it
            t0 = 0.0 if is_live else ts[len(ts) // 2]
            t1 = (4.5 if is_live else min(dur or (t0 + 6.0), t0 + 6.0))
            seg_tmp = media.download_section(url, d, "seg.tmp", t0, t1, height=480)
            local_ts = [0.5, 2.0, 3.5]
            frames = [None] * len(local_ts)
            for idx, lt in enumerate(local_ts):
                out_f = d / f"f{idx}.jpg"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{lt:.2f}", "-i", str(seg_tmp), "-frames:v", "1",
                                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(out_f)],
                               capture_output=True, timeout=20, creationflags=media.NO_WINDOW)
                if out_f.is_file() and out_f.stat().st_size > 2000:
                    frames[idx] = out_f
            ts = [round(t0 + lt, 1) for lt in local_ts]
            valid_pairs = [(i, frames[i]) for i in range(len(ts)) if frames[i] is not None]

        # Normalize saved frame slots cleanly as f0..f{N-1}
        valid_paths = [p for _, p in valid_pairs]
        kept_ts = [ts[i] for i, _ in valid_pairs]
        for n, (_, p) in enumerate(valid_pairs):
            target = d / f"f{n}.jpg"
            if p != target:
                p.replace(target)
                valid_paths[n] = target

        if vertical:
            analysis = {"cam": None, "kind": "vertical", "best": 0}
        elif valid_paths:
            analysis = facecam.analyze_sample(valid_paths)
        else:
            analysis = {"cam": None, "kind": "screen", "best": 0}

        best_idx = max(0, min(len(valid_paths) - 1, int(analysis.get("best") or 0))) if valid_paths else 0
        best_t = kept_ts[best_idx] if kept_ts else 0.0
        fb_jpg = valid_paths[best_idx] if valid_paths else None

        if seg_tmp is not None and seg_tmp.is_file():
            tmp_mp4 = d / "sample.tmp.mp4"
            if not _render_sample_clip(["-ss", "0", "-t", str(LINK_SAMPLE_SECONDS), "-i", str(seg_tmp)], tmp_mp4, mp4):
                _cut_moment_clip(url, sinfo, best_t, d, fb_jpg)
            else:
                _preview_stills(mp4, jpg, blur)
            seg_tmp.unlink(missing_ok=True)
        else:
            _cut_moment_clip(url, sinfo, best_t, d, fb_jpg)
        for p in d.glob("seg.tmp*"):
            p.unlink(missing_ok=True)

        kind = analysis["kind"]
        meta = {"key": key, "v": int(time.time() * 1000), "url": url, "title": sinfo.get("title") or "",
                "kind": kind, "kind_label": KIND_LABELS.get(kind, "Full-screen scene"),
                "sample_cam": analysis.get("cam"), "duration": dur, "active": best_idx,
                "best_t": best_t, "moments": kept_ts, "frames": len(valid_paths)}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        _prune_link_cache(root)
        running["meta"] = meta
        return meta
    except Exception as e:
        running["err"] = e
        raise
    finally:
        with _link_lock:
            _link_inflight.pop(slot_key, None)
        running["ev"].set()


def link_sample_file(key: str, ext: str):
    """Cached preview video or picture for a pasted link sample."""
    if ext not in ("mp4", "jpg", "blur.jpg", "f0.jpg", "f1.jpg", "f2.jpg"):
        return None
    try:
        d = _link_dir(key)
    except KeyError:
        return None
    name = "sample.mp4" if ext == "mp4" else "sample.jpg" if ext == "jpg" else "sample-blur.jpg" if ext == "blur.jpg" else ext
    p = d / name
    return p if p.is_file() else None


def link_sample_cam(key: str) -> dict:
    """Re-run cam finder (including AI if configured) on the cached pasted-link frames/sample."""
    from . import facecam
    d = _link_dir(key)
    meta_path, mp4 = d / "meta.json", d / "sample.mp4"
    if not meta_path.is_file() or not mp4.is_file():
        raise KeyError("Preview expired")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    fpaths = [d / f"f{i}.jpg" for i in range(int(meta.get("frames") or 0)) if (d / f"f{i}.jpg").is_file()]
    cam = facecam.find([mp4, *fpaths], 12)
    meta["sample_cam"] = cam
    if cam:
        meta["kind"] = "cam"
        meta["kind_label"] = KIND_LABELS["cam"]
    elif meta.get("kind") == "cam":
        meta["kind"] = "screen"
        meta["kind_label"] = KIND_LABELS["screen"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return meta


def label(tid) -> str:
    t = get(tid)
    return t["name"] if t else ""
