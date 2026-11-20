"""Caption fonts: every font in the list is bundled with its licence, the caption step really draws with it,
it comes out the same size as the style's own font, and the choice is kept for videos, creators and clips.
Runs against the code directly, with its own empty data folder."""
import os, re, shutil, subprocess, sys, tempfile
from pathlib import Path
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-fonts-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import creators, jobs, render
fails = []
def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}")); ok or fails.append(n)

FONT_DIR = Path("studio/fonts")
WORDS = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("this is a very good clip".split())]

# ---- the files
check("every caption font is bundled", [f["file"] for f in render.FONTS.values() if not (FONT_DIR / f["file"]).is_file()], [])
licences = " ".join(p.name.lower() for p in FONT_DIR.glob("*.txt"))
check("every caption font comes with its licence",
      [f["file"] for f in render.FONTS.values() if f["file"].split("-")[0].lower() not in licences], [])
check("every bundled font file is in the list", sorted(p.name for p in FONT_DIR.glob("*.ttf")), sorted(f["file"] for f in render.FONTS.values()))

# ---- size: capitals stand as tall in every font as in the style's own font
for style in render.STYLES:
    own = render.caption_style(style)
    f0 = render._Font.get(own["font"])
    want = f0.cap * own["size"] / (f0.asc + f0.desc)
    off = []
    for k in render.FONTS:
        st = render.caption_style(style, k)
        f = render._Font.get(st["font"])
        got = f.cap * st["size"] / (f.asc + f.desc)
        if st["size"] not in (60, 260) and abs(got - want) > want * 0.03:
            off.append((k, round(got), round(want)))
    check(f"{style}: capitals are the same height in every font", off, [])
check("no font picked: the style's own font and size", render.caption_style("hype", ""), render.STYLES["hype"])
check("a font that does not exist: the style's own font", render.caption_style("hype", "comic-sans"), render.STYLES["hype"])

ass = render.build_ass(WORDS, 0, 3, "BIG TEXT", "bounce", [], 300, 1380, font="bebas")
check("the captions use the picked font", re.search(r"^Style: Cap,([^,]+),", ass, re.M)[1], "Bebas Neue")
check("...the big text keeps its own", re.search(r"^Style: Hook,([^,]+),", ass, re.M)[1], render.HOOK["font"])
check("...and the words still fit across the frame",
      all(0 <= float(x) <= render.W for x in re.findall(r"\\pos\(([\d.]+),", render.build_ass(WORDS, 0, 3, "", "hype", [], 300, 1380, font="pixel"))))

# ---- the caption step really finds each font (by the name inside the file), not a stand-in from the system
if shutil.which("ffmpeg"):
    work = Path(tempfile.mkdtemp(prefix="pkf"))
    (work / "f").mkdir()
    for p in FONT_DIR.glob("*.ttf"):
        shutil.copyfile(p, work / "f" / p.name)
    styles = "\n".join(f"Style: S{i},{f['name']},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,0,0,0,1"
                       for i, f in enumerate(render.FONTS.values()))
    events = "\n".join(f"Dialogue: 0,0:00:00.00,0:00:01.00,S{i},,0,0,0,,Hi" for i in range(len(render.FONTS)))
    (work / "t.ass").write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n\n[V4+ Styles]\n"
                                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                                "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                                f"MarginR, MarginV, Encoding\n{styles}\n\n[Events]\n"
                                f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n{events}\n", encoding="utf-8")
    log = subprocess.run(["ffmpeg", "-hide_banner", "-v", "debug", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
                          "-vf", "ass=t.ass:fontsdir=f", "-frames:v", "1", "-f", "null", "-"],
                         cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    picked = dict(re.findall(r"fontselect: \((.+?), \d+, \d+\) -> ([^,]+),", log))
    check("ffmpeg draws every caption font from its own file",
          {f["name"]: picked.get(f["name"]) for f in render.FONTS.values() if picked.get(f["name"]) != f["file"][:-4]}, {})
    shutil.rmtree(work, ignore_errors=True)
else:
    print("SKIP ffmpeg is not installed here, so the fonts were not drawn")

# ---- the choice is kept
check("a video can pick a font", jobs.clean_options({"font": "bangers"}).get("font"), "bangers")
check("...an empty choice is kept, so a video can go back to the style's own font", jobs.clean_options({"font": ""}).get("font"), "")
check("...a made-up font is not", jobs.clean_options({"font": "../../etc"}).get("font"), "")
check("a creator can pick a font", creators._clean({"font": "kanit"}).get("font"), "kanit")
check("...not a made-up one", creators._clean({"font": "nope"}).get("font"), "")

# ---- fonts people bring: the my-fonts folder
from studio import config
mine = config.MY_FONTS_DIR
check("the my-fonts folder is made by itself", mine.is_dir())
render.install_fonts()
check("...with a note on how to use it", (mine / "README.txt").is_file())
raw = (FONT_DIR / "Bangers-Regular.ttf").read_bytes()      # a font of "their own": Bangers, renamed Bangerz
(mine / "Mine.ttf").write_bytes(raw.replace("Bangers".encode("utf-16-be"), "Bangerz".encode("utf-16-be")).replace(b"Bangers", b"Bangerz"))
(mine / "Same as built in.ttf").write_bytes(raw)                                    # a copy of a font that is already there
(mine / "broken.ttf").write_bytes(b"\x00\x01\x00\x00" + os.urandom(400))            # not really a font
(mine / "notes.txt").write_text("hello", encoding="utf-8")
listed = render.all_fonts()
check("a font put in the folder can be picked", listed.get("my:Mine.ttf", {}).get("name"), "Bangerz Regular")
check("...a copy of a built-in one is not listed twice, a broken file and other files are left out",
      sorted(k for k in listed if k.startswith("my:")), ["my:Mine.ttf"])
check("a video can pick it", jobs.clean_options({"font": "my:Mine.ttf"}).get("font"), "my:Mine.ttf")
check("...a creator too", creators._clean({"font": "my:Mine.ttf"}).get("font"), "my:Mine.ttf")
check("...but no file outside the folder", jobs.clean_options({"font": "my:../fonts/Anton-Regular.ttf"}).get("font"), "")
st = render.caption_style("bounce", "my:Mine.ttf")
check("the captions use it, measured from its own file", (st["font"], Path(st["font_path"]).name), ("Bangerz Regular", "Mine.ttf"))
check("...at the same size as the font it was made from", st["size"], render.caption_style("bounce", "bangers")["size"])

if shutil.which("ffmpeg"):
    # the whole way through: a short test video made into a clip with the brought font
    work = Path(tempfile.mkdtemp(prefix="pkm"))
    src = work / "in.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=3", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    clip = {"start": 0, "end": 3, "hook": "", "style": "bounce", "font": "my:Mine.ttf", "layout": "blur"}
    seen = {}
    real = render._render_in
    def spy(work_dir, *a, **kw):
        seen["fonts"] = kw.get("own_font")
        out = real(work_dir, *a, **kw)
        seen["files"] = sorted(p.name for p in (work_dir / "f").iterdir())
        ass = a[7]                                   # the captions it was given, drawn again to see which font is used
        (work_dir / "check.ass").write_text(ass, encoding="utf-8")
        log = subprocess.run(["ffmpeg", "-hide_banner", "-v", "debug", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=1",
                              "-vf", "ass=check.ass:fontsdir=f", "-frames:v", "1", "-f", "null", "-"],
                             cwd=work_dir, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
        seen["picked"] = dict(re.findall(r"fontselect: \((.+?), \d+, \d+\) -> ([^,]+),", log)).get("Bangerz Regular")
        seen["ass"] = ass
        return out
    render._render_in = spy
    try:
        made = render.render_clip(src, work / "out", "c1", clip, WORDS, 640, 360)
    finally:
        render._render_in = real
    check("a clip is made with a brought font", (work / "out" / made.get("video", "c1.mp4")).is_file() or (work / "out" / "c1.mp4").is_file())
    check("...its file goes into the work folder under a name of its own", "own.ttf" in seen.get("files", []))
    check("...and ffmpeg draws the captions with it", seen.get("picked"), "Bangerz-Regular")
    shutil.rmtree(work, ignore_errors=True)

(mine / "Mine.ttf").unlink()
check("a font taken out of the folder drops off the list", "my:Mine.ttf" in render.all_fonts(), False)
check("...and a clip that used it is made in the style's own font", render.caption_style("bounce", "my:Mine.ttf")["font"],
      render.STYLES["bounce"]["font"])

import time
config.SYSTEM_FONTS = True
t = time.time()
sysf = {k: v for k, v in render.all_fonts().items() if k.startswith("sys:")}
print(f"     ({len(sysf)} fonts found on this computer in {time.time() - t:.1f} s)")
check("with PEAKCUT_SYSTEM_FONTS=1 this computer's fonts are listed, each by a name the captions can use",
      all(v["name"] and "," not in v["name"] for v in sysf.values()))
t = time.time(); render.all_fonts()
check("...and the list is not read again while nothing changed", time.time() - t < 1.0)
config.SYSTEM_FONTS = False

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
