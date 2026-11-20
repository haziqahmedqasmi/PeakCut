"""One clip out of a long HLS video (Twitch VODs): the playlist is cut down to the few pieces the clip needs.

A long Twitch video is HLS: one init piece, then thousands of ten-second pieces. Asking ffmpeg to seek into that
whole playlist came back as a file with no streams in it at all, which the app reported as "This file has no video
track". These checks cover the shortcut that replaced it, and the fallback that keeps other services working.
Runs against the code directly; nothing leaves this machine."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-hls-")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import media  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


# a playlist shaped like Twitch's: an init piece, then 30 ten-second pieces with relative addresses
PIECES = 30
PLAYLIST = "\n".join(["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:10", "#EXT-X-PLAYLIST-TYPE:EVENT",
                      "#EXT-X-MEDIA-SEQUENCE:0", '#EXT-X-MAP:URI="init-0.mp4"']
                     + [line for i in range(PIECES) for line in ("#EXTINF:10.000,", f"{i}.mp4")]
                     + ["#EXT-X-ENDLIST"]) + "\n"
MANIFEST = "https://cdn.example/abcdef_720p60/index-muted-ABC123.m3u8"

read = media._read_playlist(PLAYLIST)
check("every piece is read", read is not None and len(read[0]) == PIECES, str(read and len(read[0])))
check("the init piece is found", read is not None and read[1] == "init-0.mp4", str(read and read[1]))
check("pieces know where they start", read is not None and read[0][0][0] == 0.0 and read[0][7][0] == 70.0,
      str(read and read[0][7][0]))
check("a playlist of byte ranges is left alone", media._read_playlist("#EXTM3U\n#EXT-X-BYTERANGE:100@0\n0.ts\n") is None)
check("a playlist with nothing in it is left alone", media._read_playlist("#EXTM3U\n#EXT-X-ENDLIST\n") is None)


class FakeAnswer:
    def __init__(self, text):
        self._bytes = text.encode("utf-8")

    def read(self):
        return self._bytes


asked = []
real_urlopen = media.urllib.request.urlopen


def fake_urlopen(request, timeout=None):
    asked.append(getattr(request, "full_url", str(request)))
    return FakeAnswer(PLAYLIST)


media.urllib.request.urlopen = fake_urlopen
try:
    where = Path(tempfile.mkdtemp(prefix="pc-hls-small-")) / "_pieces_clip01.m3u8"
    # a 20 second clip at 131s, padded 20s either side: pieces 11 to 17 are enough
    small, began = media._small_playlist(MANIFEST, 131.0, 151.0, where)
    check("the playlist is asked for once", asked == [MANIFEST], str(asked))
    check("a playlist is written", small is not None and Path(small).is_file())
    text = Path(small).read_text(encoding="utf-8") if small else ""
    check("only the pieces the clip needs", text.count("#EXTINF:") == 7, str(text.count("#EXTINF:")))
    check("it begins where the app is told it begins", began == 110.0, str(began))
    check("the init piece is kept and made a full address",
          '#EXT-X-MAP:URI="https://cdn.example/abcdef_720p60/init-0.mp4"' in text, text[:400])
    check("piece addresses are made full", "https://cdn.example/abcdef_720p60/11.mp4" in text, text[:600])
    check("the piece numbers carry on from the original", "#EXT-X-MEDIA-SEQUENCE:11" in text, text[:400])
    check("it ends, so ffmpeg stops asking", text.rstrip().endswith("#EXT-X-ENDLIST"))

    # a clip near the very end must not run past the playlist
    last, last_began = media._small_playlist(MANIFEST, 295.0, 320.0, where)
    last_text = Path(last).read_text(encoding="utf-8") if last else ""
    check("a clip at the end stops at the end", last is not None and "30.mp4" not in last_text, last_text[-300:])
finally:
    media.urllib.request.urlopen = real_urlopen


# ---- which way download_section goes
real_hls, real_part = media._hls_playlist, media._fetch_hls_part
real_ydl = media._ydl
calls = []


class FakeYdl:
    def __init__(self, opts=None):
        calls.append(opts or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        calls.append({"downloaded": list(urls)})


out = Path(tempfile.mkdtemp(prefix="pc-hls-out-"))
try:
    # 1. HLS: the shortcut is used and yt-dlp is never asked for a range
    calls.clear()
    media._hls_playlist = lambda url, height: MANIFEST
    media._fetch_hls_part = lambda m3u8, d, stem, a, b, exact: out / "clip01.mp4"
    (out / "clip01.mp4").write_bytes(b"x" * 8192)
    media._ydl = FakeYdl
    got = media.download_section("https://www.twitch.tv/videos/1", out, "clip01", 10.0, 20.0)
    check("an HLS video uses the shortcut", Path(got).name == "clip01.mp4")
    check("yt-dlp is not asked for a range download", calls == [], str(calls))

    # 2. HLS but a playlist the shortcut does not understand: the old way still runs
    calls.clear()
    media._fetch_hls_part = lambda *a, **k: None
    try:
        media.download_section("https://www.twitch.tv/videos/1", out, "clip02", 10.0, 20.0)
        check("an unusual playlist falls back to yt-dlp", False, "no error raised")
    except RuntimeError:
        check("an unusual playlist falls back to yt-dlp", any("download_ranges" in c for c in calls if isinstance(c, dict)))

    # 3. not HLS at all (YouTube and friends): unchanged behaviour
    calls.clear()
    media._hls_playlist = lambda url, height: ""
    try:
        media.download_section("https://www.youtube.com/watch?v=x", out, "clip03", 10.0, 20.0)
    except RuntimeError:
        pass
    check("a video that is not HLS is left to yt-dlp", any("download_ranges" in c for c in calls if isinstance(c, dict)))

    # 4. the service refusing to say what it is must not break the download
    calls.clear()

    def boom(url, height):
        raise ValueError("not today")

    media._hls_playlist = boom
    try:
        media.download_section("https://www.twitch.tv/videos/1", out, "clip04", 10.0, 20.0)
    except RuntimeError:
        pass
    check("a service that will not say is handled", any("download_ranges" in c for c in calls if isinstance(c, dict)))
finally:
    media._hls_playlist, media._fetch_hls_part, media._ydl = real_hls, real_part, real_ydl

print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
