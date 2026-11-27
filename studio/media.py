"""Getting video in: link download, probing, audio extraction, loudness."""
import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/149.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- when YouTube refuses this server
# YouTube sometimes refuses a server's downloads altogether ("sign in to confirm you're not a bot"). It is a
# refusal of one way of asking from one address, not of the video. So YouTube is asked in several ways, in order:
# straight from this machine; from this machine as another YouTube app (YouTube often still answers those); then
# through each address in PEAKCUT_YT_PROXIES, if you set any (optional: proxies of your own, comma-separated). A way
# that was refused is skipped for a few minutes. Only when every way is refused is the link turned away, with a
# clear message.
YT_PAUSE = 600
_yt_refused_until = 0.0
_ROUTE_REFUSED: dict = {}           # route name -> refused until
_ROUTE_LAST = ""                    # the last route that worked: tried first next time


def _routes() -> list:
    out = [("direct", {}), ("apps", {"extractor_args": {"youtube": {"player_client": ["web_safari", "mweb", "android_vr"]}}})]
    for n, p in enumerate(config.YT_PROXIES):
        out.append((f"via{n}", {"proxy": p}))
        out.append((f"via{n}-apps", {"proxy": p, "extractor_args": {"youtube": {"player_client": ["web_safari", "mweb", "android_vr"]}}}))
    if _ROUTE_LAST:
        out.sort(key=lambda r: r[0] != _ROUTE_LAST)
    return out


def _yt(url, work):
    """work(extra yt-dlp options) for a link. For YouTube, every route is tried in turn until one is not refused."""
    global _yt_refused_until, _ROUTE_LAST
    if not is_youtube(url):
        return work({})
    check_youtube(url)
    now = time.time()
    tried = 0
    for name, extra in _routes():
        if _ROUTE_REFUSED.get(name, 0) > now:
            continue
        tried += 1
        try:
            try:
                got = work(extra)
            except Exception as e:  # noqa: BLE001 - one refused download is often a hiccup: once more, on fresh links
                if not _DATA_REFUSED.search(str(e)) or _BOT_CHECK.search(str(e)):
                    raise
                time.sleep(3)
                got = work(extra)
        except Exception as e:  # noqa: BLE001
            if _BOT_CHECK.search(str(e)) or _DATA_REFUSED.search(str(e)) or (extra.get("proxy") and _PROXY_DOWN.search(str(e))):
                _ROUTE_REFUSED[name] = time.time() + YT_PAUSE
                continue
            raise
        _ROUTE_LAST = name
        return got
    # every way is refused right now: turn YouTube links away for a little while instead of each one waiting to fail
    _yt_refused_until = time.time() + (YT_PAUSE if tried else 60)
    raise YouTubeBlocked(YT_BLOCKED)


_PROXY_DOWN = re.compile(r"Unable to connect to proxy|ProxyError|Tunnel connection failed|Connection refused", re.I)
YT_BLOCKED = ("YouTube isn't letting PeakCut download videos right now. Upload the video file instead, or use a "
              "Twitch or Kick link. "
              "YouTube links will be tried again in a few minutes.")
_BOT_CHECK = re.compile(r"not a bot|confirm you.?re not|sign in to confirm(?! your age)", re.I)
# YouTube can also hand over the page and then refuse the video itself ("unable to download video data:
# HTTP Error 403: Forbidden"): that is a refusal too, so the next way is tried
_DATA_REFUSED = re.compile(r"HTTP Error 403|403: Forbidden|ffmpeg exited with code 8", re.I)   # a stretch of a video comes through ffmpeg, which reports the same refusal this way


class YouTubeBlocked(RuntimeError):
    pass


def is_youtube(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse((url or "").strip()).hostname or "").lower()
    return host in ("youtube.com", "youtu.be") or host.endswith((".youtube.com", ".youtu.be"))


def youtube_paused() -> bool:
    return time.time() < _yt_refused_until


def check_youtube(url: str):
    """Raise YouTubeBlocked straight away if YouTube is refusing this server right now."""
    if is_youtube(url) and youtube_paused():
        raise YouTubeBlocked(YT_BLOCKED)


def note_refusal(url: str, err) -> bool:
    """Is this error YouTube refusing the server? (The routes in _yt are already tried by then.)"""
    return bool(is_youtube(url) and (_BOT_CHECK.search(str(err)) or _DATA_REFUSED.search(str(err))))


def site_allowed(url: str) -> bool:
    """Links are fetched by the server, so only known video sites are accepted (never arbitrary addresses)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url.strip())
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == s or host.endswith("." + s) for s in config.ALLOWED_SITES)


def run(cmd, cwd=None, timeout=3600):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(str(cmd[0])).name} took too long and was stopped.")
    if p.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {p.stderr[-1500:]}")
    return p.stdout


class _Quiet:
    def debug(self, m): pass
    def info(self, m): pass
    def warning(self, m): pass
    def error(self, m): pass


def _ydl(opts):
    import yt_dlp
    # a channel or playlist link is answered as "a list" straight away, never walked video by video
    return yt_dlp.YoutubeDL({"noplaylist": True, "quiet": True, "no_warnings": True, "logger": _Quiet(),
                             "extract_flat": "in_playlist", "playlistend": 1, "socket_timeout": 20,
                             "concurrent_fragment_downloads": 4, **opts})


def _video_format(height):
    return f"bv*[height<={height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={height}]+ba/b[height<={height}]/b"


def _biggest(job_dir: Path, stem: str) -> Path:
    files = sorted((p for p in job_dir.glob(stem + ".*") if p.suffix not in (".part", ".ytdl")),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("Download finished but no file was produced.")
    return files[0]


def info(url: str) -> dict:
    """Title, channel and length, without downloading anything."""
    def work(x):
        with _ydl(x) as ydl:
            return ydl.extract_info(url, download=False)
    i = _yt(url, work)
    return {"title": i.get("title") or "", "uploader": i.get("uploader") or i.get("channel") or "",
            "duration": float(i.get("duration") or 0), "is_live": bool(i.get("is_live")),
            "thumbnail": i.get("thumbnail") or "", "kind": i.get("_type") or "video"}


def youtube_words(url: str):
    """YouTube's own transcript of a video, word by word: ([{'w', 's', 'e'}, ...], language), or None if the video
    has none. Only the transcript in the language that was spoken is used, never a translation."""
    if not is_youtube(url):
        return None
    import json
    def work(x):
        with _ydl(x) as ydl:
            i = ydl.extract_info(url, download=False)
            auto = i.get("automatic_captions") or {}
            # the track in the language that was spoken: never YouTube's translation of it into English
            lang = str(i.get("language") or "").strip().lower()
            base = lang.split("-")[0] if lang else ""
            candidates = [f"{lang}-orig", lang, f"{base}-orig", base]
            key = next((c for c in candidates if c and c in auto), None)
            if not key:
                key = next((k for k in auto if k.endswith("-orig")), None)
            track = next((f for f in auto.get(key) or [] if f.get("ext") == "json3" and f.get("url")), None) if key else None
            if not track:
                return None
            return ydl.urlopen(track["url"]).read(40_000_000), key
    got = _yt(url, work)
    if not got:
        return None
    raw, key = got
    return _caption_words(json.loads(raw)), key.replace("-orig", "")


def youtube_english_words(url: str):
    """YouTube's English captions of a video spoken in another language: ones a person wrote if there are any,
    otherwise YouTube's own translation of its transcript. Word list like youtube_words, or None."""
    if not is_youtube(url):
        return None
    import json
    def work(x):
        with _ydl(x) as ydl:
            i = ydl.extract_info(url, download=False)
            tracks = []
            for group in (i.get("subtitles") or {}, i.get("automatic_captions") or {}):
                for k in ("en", "en-US", "en-GB"):
                    tracks.append(next((f for f in group.get(k) or [] if f.get("ext") == "json3" and f.get("url")), None))
            track = next((t for t in tracks if t), None)
            return ydl.urlopen(track["url"]).read(40_000_000) if track else None
    raw = _yt(url, work)
    if not raw:
        return None
    words = _caption_words(json.loads(raw))
    return words or None


def _caption_words(data: dict) -> list:
    out = []
    for ev in data.get("events") or []:
        segs = [x for x in ev.get("segs") or [] if (x.get("utf8") or "").strip()]
        if not segs:
            continue
        t0, dur = float(ev.get("tStartMs") or 0), float(ev.get("dDurationMs") or 0)
        timed = len(segs) > 1 or "tOffsetMs" in segs[0]
        for x in segs:
            parts = x["utf8"].split()
            for n, w in enumerate(parts):
                if re.fullmatch(r"[\[(].*[\])]", w) or w in ("[", "]", "__"):
                    continue                      # [Music], [Applause], and words YouTube blanked out
                # a line with no word timings: its words are spread evenly over the time the line was on screen
                at = t0 + float(x.get("tOffsetMs") or 0) if timed else t0 + dur * n / max(1, len(parts))
                out.append({"w": w, "s": round(at / 1000, 2)})
    out.sort(key=lambda w: w["s"])
    for n, w in enumerate(out):
        nxt = out[n + 1]["s"] if n + 1 < len(out) else w["s"] + 0.4
        w["e"] = round(max(w["s"] + 0.08, min(nxt, w["s"] + 0.6)), 2)
    return out


_PREVIEWS: dict = {}


def _oembed(url: str) -> dict:
    """YouTube's public oEmbed answer for a video: title, channel and picture, without asking its player. Empty if
    it has none (a channel, a private video...)."""
    import urllib.parse
    import urllib.request
    try:
        q = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
        with urllib.request.urlopen(urllib.request.Request(q, headers={"User-Agent": "Mozilla/5.0"}), timeout=8) as r:
            d = json.loads(r.read(200_000))
    except Exception:  # noqa: BLE001
        return {}
    if not d.get("title"):
        return {}
    return {"title": d["title"], "uploader": d.get("author_name") or "", "duration": 0.0, "kind": "video",
            "is_live": "/live/" in urllib.parse.urlparse(url).path, "thumbnail": d.get("thumbnail_url") or ""}


def preview(url: str, deep: bool = True) -> dict:
    """What the user will get from a link, checked before any work starts. The thumbnail comes back inline
    (the page may only load images from this site). Cached, so retyping a link costs nothing. A YouTube link is
    looked up through oEmbed, never YouTube's player; with deep=False nothing else is ever asked."""
    url = url.strip()
    if url in _PREVIEWS:
        return _PREVIEWS[url]
    i = _oembed(url) if is_youtube(url) else {}
    if not i:
        if is_youtube(url) and not deep:
            raise ValueError("Couldn't open that video. Check the link is public and complete.")
        i = info(url)
    if i["kind"] != "video" or not i["title"]:
        raise ValueError("That link is a channel or playlist, not a single video.")
    thumb = ""
    t = i.get("thumbnail") or ""
    if t.startswith("https://"):
        import base64
        import urllib.request
        try:
            req = urllib.request.Request(t, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                data = r.read(600_000 + 1)
            if ctype in ("image/jpeg", "image/png", "image/webp") and len(data) <= 600_000:
                thumb = f"data:{ctype};base64," + base64.b64encode(data).decode()
        except Exception:
            thumb = ""
    out = {"title": i["title"][:200], "uploader": i["uploader"][:120], "duration": i["duration"],
           "is_live": i["is_live"], "thumb": thumb}
    if len(_PREVIEWS) > 300:
        _PREVIEWS.pop(next(iter(_PREVIEWS)))
    _PREVIEWS[url] = out
    return out


def save_thumb(job_dir: Path, url: str = "", video: Path = None) -> bool:
    """A small picture for the video list: the link's own thumbnail, or a frame from an uploaded file."""
    import base64
    out = Path(job_dir) / "clips" / "thumb.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if url:
            p = _PREVIEWS.get(url.strip()) or preview(url)
            if not p.get("thumb"):
                return False
            out.write_bytes(base64.b64decode(p["thumb"].split(",", 1)[1]))
            return True
        if video:
            d = probe(video).get("duration") or 0
            run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{min(3.0, d / 3):.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)])
            return out.is_file()
    except Exception:
        return False
    return False


def download_audio(url: str, job_dir: Path, on_progress=None) -> Path:
    """Audio only: a few seconds even for long videos, and all the AI needs to pick clips."""
    def hook(d):
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if on_progress and d.get("status") == "downloading" and total:
            on_progress(d.get("downloaded_bytes", 0) / total)

    def work(x):
        with _ydl({"format": "ba[ext=m4a]/ba/worst", "outtmpl": str(job_dir / "audio_src.%(ext)s"),
                   "progress_hooks": [hook], **x}) as ydl:
            ydl.download([url])
    _yt(url, work)
    return _biggest(job_dir, "audio_src")


def download(url: str, job_dir: Path, height=720) -> Path:
    """The whole video. Used for shorter videos, where this is quicker than fetching pieces."""
    def work(x):
        with _ydl({"format": _video_format(height), "merge_output_format": "mp4",
                   "outtmpl": str(job_dir / "source.%(ext)s"), **x}) as ydl:
            ydl.download([url])
    _yt(url, work)
    return _biggest(job_dir, "source")


def _hls_playlist(url: str, height: int) -> str:
    """The playlist address for this video, or "" when it is not an HLS stream. Twitch VODs are HLS: one init
    piece plus thousands of ten-second pieces. ffmpeg cannot be asked to seek into that from the outside; it
    starts somewhere else entirely and hands back a file with no picture in it."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}
    with _youtube_guard(url), _ydl({**opts, **cookie_opts()}) as ydl:
        data = ydl.extract_info(url, download=False) or {}
        choose = ydl.build_format_selector(_video_format(height))
        try:
            picked = list(choose({"formats": data.get("formats") or []}, data))
        except TypeError:            # this call has changed shape between yt-dlp releases
            picked = list(choose(data))
    best = picked[0] if picked else None
    if not best or "m3u8" not in str(best.get("protocol") or ""):
        return ""
    return str(best.get("url") or "")


def _read_playlist(text: str):
    """Every piece in a playlist as (seconds into the video, length, address), plus the init piece if the
    playlist has one. None when this is not the kind of playlist the shortcut below understands."""
    if "#EXT-X-BYTERANGE" in text:
        return None                  # pieces addressed by byte ranges: leave this one to yt-dlp
    pieces, init, at = [], "", 0.0
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    n = 0
    while n < len(lines):
        line = lines[n]
        if line.startswith("#EXT-X-MAP:"):
            found = re.search(r'URI="([^"]+)"', line)
            if found:
                init = found.group(1)
        elif line.startswith("#EXTINF:"):
            try:
                length = float(line.split(":", 1)[1].split(",")[0])
            except ValueError:
                return None
            n += 1
            while n < len(lines) and lines[n].startswith("#"):
                n += 1
            if n < len(lines):
                pieces.append((at, length, lines[n]))
                at += length
        n += 1
    return (pieces, init) if pieces else None


def _small_playlist(m3u8: str, start: float, end: float, where: Path, pad: float = 20.0):
    """A playlist of only the pieces one clip needs, written to disk. Returns (file, where it really starts),
    or (None, 0) when the playlist is not one this shortcut understands."""
    try:
        asked = urllib.request.Request(m3u8, headers={"User-Agent": _UA})
        text = urllib.request.urlopen(asked, timeout=30).read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - no playlist read, no shortcut: yt-dlp takes over
        return None, 0.0
    read = _read_playlist(text)
    if not read:
        return None, 0.0
    pieces, init = read
    base = m3u8.split("?")[0].rsplit("/", 1)[0] + "/"
    low, high = max(0.0, start - pad), end + pad
    keep = [piece for piece in pieces if piece[0] + piece[1] > low and piece[0] < high]
    if not keep:
        return None, 0.0
    out = ["#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{max(int(p[1]) for p in keep) + 1}",
           f"#EXT-X-MEDIA-SEQUENCE:{pieces.index(keep[0])}", "#EXT-X-PLAYLIST-TYPE:VOD"]
    if init:
        out.append(f'#EXT-X-MAP:URI="{urllib.parse.urljoin(base, init)}"')
    for _, length, address in keep:
        out.append(f"#EXTINF:{length:.3f},")
        out.append(urllib.parse.urljoin(base, address))
    out.append("#EXT-X-ENDLIST")
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text("\n".join(out) + "\n", encoding="utf-8")
    return where, keep[0][0]


def _fetch_hls_part(m3u8: str, out_dir: Path, stem: str, start: float, end: float, exact: bool):
    """One stretch of an HLS video, by giving ffmpeg a playlist that holds only that stretch. The seek is made
    inside those few pieces, so the init piece is never lost."""
    small, began = _small_playlist(m3u8, start, end, out_dir / ("_pieces_" + stem + ".m3u8"))
    if not small:
        return None
    target = out_dir / (stem + ".mp4")
    seek, span = max(0.0, start - began), max(0.2, end - start)
    cmd = ["ffmpeg", "-y", "-v", "error", "-protocol_whitelist", "file,http,https,tcp,tls,crypto,data",
           "-i", str(small), "-ss", f"{seek:.3f}", "-t", f"{span:.3f}"]
    if exact:
        # a cut at exactly the asked second: the picture has to be made again for that
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd += ["-f", "mp4", str(target)]
    try:
        run(cmd)
    finally:
        small.unlink(missing_ok=True)
    if not target.is_file() or target.stat().st_size < 4096:
        raise RuntimeError("The video service sent no picture for this part of the video. Try it again, or "
                           "upload the video file and clip that instead.")
    return target


def download_section(url: str, out_dir: Path, stem: str, start: float, end: float, height=720, exact=False) -> Path:
    """Just one stretch of the video. The cut lands on a keyframe, so find_offset() pins the true start; with
    exact=True the cut is made at exactly `start` instead (slower), for when there is nothing to match against."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # An HLS video, as Twitch VODs are: fetch only the pieces this clip needs. Asking ffmpeg to seek into the
    # whole playlist is what used to come back as a file with no streams in it ("This file has no video track").
    try:
        m3u8 = _hls_playlist(url, height)
    except Exception:  # noqa: BLE001 - not HLS, or the service would not say: the usual way may still work
        m3u8 = ""
    if m3u8:
        piece = _fetch_hls_part(m3u8, out_dir, stem, start, end, exact)
        if piece is not None:
            return piece
    from yt_dlp.utils import download_range_func
    opts = {"format": _video_format(height), "merge_output_format": "mp4", "outtmpl": str(out_dir / (stem + ".%(ext)s")),
            "download_ranges": download_range_func(None, [(max(0.0, start), end)])}
    if exact:
        opts["force_keyframes_at_cuts"] = True

    def work(x):
        with _ydl({**opts, **x}) as ydl:
            ydl.download([url])
    _yt(url, work)
    return _biggest(out_dir, stem)


def find_offset(full: np.ndarray, part: np.ndarray, expected: float, search=20.0) -> float:
    """Where in the full audio (seconds) a downloaded piece really begins, by matching the sound itself."""
    n = min(len(part), 30 * 16000)
    a0 = max(0, int((expected - search) * 16000))
    hay = full[a0: int((expected + search) * 16000) + n].astype(np.float32)
    needle = part[:n].astype(np.float32)
    if n < 16000 or len(hay) < n or float(np.abs(needle).mean()) < 20:
        return expected  # too short or silent to match: trust the requested time
    size = 1 << (len(hay) + n).bit_length()
    corr = np.fft.irfft(np.fft.rfft(hay, size) * np.conj(np.fft.rfft(needle, size)), size)[: len(hay) - n + 1]
    return (a0 + int(np.argmax(corr))) / 16000


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RuntimeError("This file has no video track.")
    if not any(s["codec_type"] == "audio" for s in data["streams"]):
        raise RuntimeError("This file has no audio track, so there is nothing to transcribe.")
    w, h = int(v["width"]), int(v["height"])
    if w * h > 3840 * 2160 or w < 16 or h < 16:
        raise RuntimeError("This video's resolution isn't supported (4K is the largest).")
    rot = str((v.get("side_data_list") or [{}])[0].get("rotation", "0"))
    if rot.lstrip("-") in ("90", "270"):
        w, h = h, w
    return {"width": w, "height": h, "duration": float(data["format"]["duration"])}


def extract_audio(src: Path, wav: Path):
    # first_pts=0 pads any gap before the audio starts, so second 0 of the wav is second 0 of the file
    # -t: never more audio than the longest video allowed, whatever the file claims about itself
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-t", str(int(config.MAX_VIDEO_HOURS * 3600) + 60),
         "-af", "aresample=async=1:first_pts=0", "-c:a", "pcm_s16le", str(wav)])


def load_audio(wav: Path) -> np.ndarray:
    data, sr = sf.read(str(wav), dtype="int16")
    assert sr == 16000
    return data


LOUDNESS_PIECE = 600       # seconds of audio worked out at a time


def loudness_per_second(audio: np.ndarray) -> np.ndarray:
    """RMS level in dB for each second of audio.

    Worked out ten minutes at a time. All at once, a day-long video needs a table of several gigabytes (one
    float per sample), which most computers cannot find room for; this way it needs about 40 MB, whatever the length."""
    n = len(audio) // 16000
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    out = np.empty(n, dtype=np.float32)
    for a in range(0, n, LOUDNESS_PIECE):
        b = min(n, a + LOUDNESS_PIECE)
        x = audio[a * 16000: b * 16000].reshape(b - a, 16000).astype(np.float32) / 32768.0
        out[a:b] = 20 * np.log10(np.sqrt((x ** 2).mean(axis=1) + 1e-10))
    return out
