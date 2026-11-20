"""Hindi and Urdu captions come out in Roman letters (Hinglish): the same words at the same moments, never
translated. The AI is replaced by a stand-in, so nothing leaves this machine."""
import os, re, struct, sys, tempfile
from pathlib import Path
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-roman-"); os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import brain, config, render, roman
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x and not ok else "")); ok or fails.append(n)
def ws(text):
    return [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate(text.split())]
def text(words):
    return " ".join(w["w"] for w in words)

# ---- the spelling rules on their own
for hi, want in [("चूना जो उसको", "chuna jo usko"), ("कहना करता समझना", "kahna karta samajhna"), ("आज", "aaj"),
                 ("ज़िंदगी", "zindagi"), ("नहीं यार", "nahi yaar"), ("बात", "baat"), ("हमारा", "hamara"),
                 ("GTA गेम में 1000", "GTA gem mein 1000")]:
    check(f"{hi} -> {want}", roman.to_roman(hi) == want, roman.to_roman(hi))
check("English is left alone", roman.to_roman("Hello there, 25 cars!") == "Hello there, 25 cars!")

# ---- with no AI: every Hindi word is spelled out, at the same moment
def no_ai(*a, **k):
    raise RuntimeError("The AI is not set up on this server.")
brain.ask_ai = no_ai
src = ws("अरे चूना जो उसको लगा")
out = brain.fix_caption_words(src, "test")
check("without AI, Hindi captions come out in Roman letters", text(out) == "are chuna jo usko laga", text(out))
check("...with every word at its own time", [(w["s"], w["e"]) for w in out] == [(w["s"], w["e"]) for w in src])
check("short clips are spelled out too", text(brain.fix_caption_words(ws("जो उसको"), "")) == "jo usko")
out = text(brain.fix_caption_words(ws("یار یہ کیا ہے بھائی"), ""))
check("without AI, Urdu captions come out in Roman letters too", out == "yaar ye kya hai bhai", out)
check("Urdu punctuation and digits become plain ones", roman.to_roman("ہاں، ۱۰۰۰ روپے؟") == "haan, 1000 rupay?", roman.to_roman("ہاں، ۱۰۰۰ روپے؟"))

# ---- with the AI: its Hinglish is used, a translation is not, Urdu and other languages are handled
answers = {}
def fake_ai(prompt, schema=None, purpose=""):
    return answers["now"], "m", 10
brain.ask_ai = fake_ai
answers["now"] = ["Arre", "choona", "jo", "usko", "laga."]
check("the AI's Hinglish spelling is used", text(brain.fix_caption_words(src, "")) == "Arre choona jo usko laga.")
answers["now"] = ["Hey", "lime", "that", "him", "hit"]
out = text(brain.fix_caption_words(src, ""))
check("a translation is refused: the word is spelled out instead", out == "are chuna jo usko laga", out)
answers["now"] = ["Arre", "चूना", "jo", "usko", "laga"]
check("Hindi the AI left in its script is still spelled out", text(brain.fix_caption_words(src, "")) == "Arre chuna jo usko laga")
urdu = ws("یار یہ کیا ہے")
answers["now"] = ["Yaar", "ye", "kya", "hai"]
check("Urdu script becomes the AI's Roman spelling", text(brain.fix_caption_words(urdu, "")) == "Yaar ye kya hai")
spanish = ws("hola amigo que pasa")
answers["now"] = ["Hola", "amigo,", "¿qué", "pasa?"]
check("Spanish stays Spanish", text(brain.fix_caption_words(spanish, "")) == "Hola amigo, ¿qué pasa?")
answers["now"] = ["one", "two"]
check("an answer of the wrong length keeps the words, in Roman letters", text(brain.fix_caption_words(src, "")) == "are chuna jo usko laga")

# ---- what the AI writes for the screen
c = brain._tidy({"hook": "चूना लगा", "title": "उसको चूना", "highlight_words": ["चूना", "boss"], "score": 90}, 1, 20)
check("hook, title and highlighted words are in Roman letters", (c["hook"], c["title"], c["highlight_words"]) == ("CHUNA LAGA", "usko chuna", ["chuna", "boss"]), str(c))

# ---- the language rule: English stays, Hindi/Urdu in Roman letters, everything else translated into English
check("which languages are translated", [brain.needs_translation(x) for x in ("en", "en-US", "hi", "ur", "es", "ar", "")]
      == [False, False, False, False, True, True, False])
def by_purpose(prompt, schema=None, purpose=""):
    if purpose == "Caption translation":
        n = int(prompt.split("Lines (")[1].split(")")[0])
        return [f"English line {i + 1}" for i in range(n)], "m", 10
    return answers["now"], "m", 10
brain.ask_ai = by_purpose
es = [{"w": w, "s": s, "e": s + 0.4} for w, s in (("hola", 0.0), ("amigo", 0.5), ("que", 1.0), ("pasa", 1.5), ("hoy.", 2.0),
                                                     ("vamos", 5.0), ("a", 5.5), ("ganar", 6.0), ("esto", 6.5))]
yt_en = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("hi friend what is up today".split())]
answers["now"] = ["Hi", "friend,", "what", "is", "up", "today?"]
out = brain.english_captions(es, "es", "", youtube_english=lambda: yt_en)
check("Spanish with YouTube's English captions: those are used, tidied by the AI", text(out) == "Hi friend, what is up today?", text(out))
out = brain.english_captions(es, "es", "", youtube_english=lambda: None)
check("Spanish with no English from YouTube: the AI translates it", text(out) == "English line 1 English line 2", text(out))
check("...each English line on the moment its line was said", out[0]["s"] == 0.0 and out[-1]["s"] >= 5.0 and out[-1]["e"] <= 6.95, str(out))
brain.ask_ai = no_ai
ar = ws("مرحبا يا صديقي كيف حالك")
out = brain.english_captions(ar, "ar", "", youtube_english=lambda: None)
check("Arabic with neither: still in English letters", not any(roman.needs_roman(w["w"]) for w in out) and len(out) == 5, text(out))
out = brain.english_captions(ws("जो उसको"), "hi", "")
check("Hindi is never translated, only spelled in Roman letters", text(out) == "jo usko", text(out))
brain.ask_ai = by_purpose
answers["now"] = ["Hello", "there", "friend."]
check("English stays English", text(brain.english_captions(ws("hello there friend"), "en", "")) == "Hello there friend.")

# ---- Cyrillic captions keep real Cyrillic and use the bundled Cyrillic fonts
for sample in ("Привет мир", "Ёжик", "buster Привет"):
    check(f"Cyrillic kept: {sample}",
          roman.to_roman(sample) == sample and not roman.needs_roman(sample)
          and roman.has_cyrillic(sample) and roman.drawable(sample),
          roman.to_roman(sample))
check("mixed Cyrillic and Greek keeps Cyrillic and spells out Greek",
      roman.to_roman("Привет Ελλάδα") == "Привет Ellada", roman.to_roman("Привет Ελλάδα"))

brain.ask_ai = no_ai
cyr_src = ws("Привет мир Ёжик")
check("without AI, Cyrillic captions stay in Cyrillic",
      text(brain.fix_caption_words(cyr_src, "test", "en")) == "Привет мир Ёжик")
brain.ask_ai = fake_ai
answers["now"] = ["Привет,", "мир", "Ёжик!"]
check("with AI, Cyrillic punctuation fixes are accepted",
      text(brain.fix_caption_words(cyr_src, "", "en")) == "Привет, мир Ёжик!")
answers["now"] = ["Hello", "world", "Hedgehog"]
check("with AI, an English rewrite of Cyrillic words is refused",
      text(brain.fix_caption_words(cyr_src, "", "en")) == "Привет мир Ёжик")
cyr_clip = brain._tidy({"hook": "это безумие", "title": "Привет мир", "description": "Ёжик",
                        "highlight_words": ["Привет", "buster"], "score": 90}, 1, 20)
check("hook, title, description and highlight words keep Cyrillic",
      (cyr_clip["hook"], cyr_clip["title"], cyr_clip["description"], cyr_clip["highlight_words"])
      == ("ЭТО БЕЗУМИЕ", "Привет мир", "Ёжик", ["Привет", "buster"]), str(cyr_clip))


def _style_font(ass, style_name):
    m = re.search(rf"^Style: {style_name},([^,]+),", ass, re.M)
    return m.group(1) if m else ""


def _cmap_codepoints(ttf_path: Path) -> set:
    d = ttf_path.read_bytes()
    n = struct.unpack(">H", d[4:6])[0]
    tables = {}
    for i in range(n):
        tag, _, off, ln = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
        tables[tag] = (off, ln)
    off, ln = tables[b"cmap"]
    cm = d[off:off + ln]
    cps = set()
    for i in range(struct.unpack(">H", cm[2:4])[0]):
        _, _, sub_off = struct.unpack(">HHI", cm[4 + 8 * i: 12 + 8 * i])
        if struct.unpack(">H", cm[sub_off:sub_off + 2])[0] == 4:
            seg2 = struct.unpack(">H", cm[sub_off + 6: sub_off + 8])[0]
            seg = seg2 // 2
            ends = struct.unpack(f">{seg}H", cm[sub_off + 14: sub_off + 14 + seg2])
            starts = struct.unpack(f">{seg}H", cm[sub_off + 16 + seg2: sub_off + 16 + 2 * seg2])
            for s, e in zip(starts, ends):
                if s != 0xFFFF:
                    cps.update(range(s, e + 1))
    return cps


# Cyrillic font selection across styles, mixed Latin+Cyrillic, highlights, and hooks
ass_bounce = render.build_ass(ws("Привет мир Ёжик"), 0, 5, "ENGLISH HOOK", "bounce", [], 300, 1380)
check("bounce selects Montserrat ExtraBold for Cyrillic captions while English hook keeps Poppins",
      _style_font(ass_bounce, "Cap") == "Montserrat ExtraBold"
      and _style_font(ass_bounce, "Hook") == "Poppins ExtraBold"
      and "ПРИВЕТ" in ass_bounce and "ЁЖИК" in ass_bounce and "\\q0" not in ass_bounce,
      f"Cap={_style_font(ass_bounce, 'Cap')} Hook={_style_font(ass_bounce, 'Hook')}")

ass_hype = render.build_ass(ws("buster Привет"), 0, 5, "ЭТО БЕЗУМИЕ", "hype", ["Привет"], 300, 1380)
check("hype selects Oswald Bold for mixed Latin+Cyrillic captions and highlights Cyrillic words",
      _style_font(ass_hype, "Cap") == "Oswald Bold"
      and _style_font(ass_hype, "Hook") == "Montserrat ExtraBold"
      and "BUSTER" in ass_hype and "ПРИВЕТ" in ass_hype
      and f"\\c&H{render.STYLES['hype']['strong']}&}}ПРИВЕТ" in ass_hype,
      f"Cap={_style_font(ass_hype, 'Cap')} Hook={_style_font(ass_hype, 'Hook')}")

ass_clean = render.build_ass(ws("buster Привет"), 0, 5, "", "clean", [], 300, 1380)
check("clean selects Montserrat ExtraBold and keeps original Cyrillic case",
      _style_font(ass_clean, "Cap") == "Montserrat ExtraBold"
      and "buster" in ass_clean and "Привет" in ass_clean)

# Purely Latin captions keep each style's original font unchanged
for style_name, want_font in (("bounce", "Poppins ExtraBold"), ("hype", "Anton"), ("clean", "Poppins ExtraBold")):
    ass_lat = render.build_ass(ws("Hello world niño été"), 0, 5, "BIG HOOK", style_name, [], 300, 1380)
    check(f"Latin captions in {style_name} keep {want_font}",
          _style_font(ass_lat, "Cap") == want_font and _style_font(ass_lat, "Hook") == "Poppins ExtraBold",
          _style_font(ass_lat, "Cap"))
check("Archivo Black stays unchanged for Latin and switches for Cyrillic",
      render._font_for("Archivo Black", "Hello") == "Archivo Black"
      and render._font_for("Archivo Black", "Привет") == "Montserrat ExtraBold")
outside_cyr = [{"w": "Привет", "s": 0.0, "e": 0.5}, {"w": "Hello", "s": 5.0, "e": 5.5}]
check("Cyrillic words outside the clip range do not change a Latin clip's font",
      _style_font(render.build_ass(outside_cyr, 4.0, 7.0, "", "hype", [], 300, 1380), "Cap") == "Anton")

# Bundled Cyrillic TTF files and OFL licenses exist on disk and cover Russian, Ukrainian, and Latin
fonts_dir = Path(render.__file__).resolve().parent / "fonts"
required_cps = (set(range(0x0410, 0x0450)) | {0x0401, 0x0451}
                | {ord(c) for c in "ЄІЇҐєіїґABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"})
render.install_fonts()
for font_name, license_file in (("Montserrat ExtraBold", "OFL-montserrat.txt"), ("Oswald Bold", "OFL-oswald.txt")):
    ttf = fonts_dir / render.FONT_FILES[font_name]
    lic = fonts_dir / license_file
    cps = _cmap_codepoints(ttf) if ttf.exists() else set()
    missing = required_cps - cps
    check(f"bundled {font_name} TTF and OFL licence exist and cover Cyrillic + Latin",
          ttf.exists() and lic.exists() and not missing and (config.FONTS_DIR / ttf.name).exists(),
          f"missing={len(missing)}")

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil; shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
