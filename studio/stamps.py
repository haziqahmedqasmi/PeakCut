"""Times written the way YouTube writes them.

People copy times out of YouTube: "1:05:10", "12:05", "1h5m10s", or a whole link with "&t=3910s". A list of
moments looks like the chapters under a video:

    01:05:10 – 01:06:09  Funny moment
    2:30-3:15 Opening scene
    https://youtu.be/VIDEO_ID?t=3910

Each line becomes one moment: where it starts, where it ends (or None, when only a start was given) and the words
that were written next to the times, kept as a note."""
import re

MAX_MOMENTS = 20

# 1:05:10  12:05  1:05:10.5   or   1h5m10s  12m  45s  3910s
_COLON = r"\d{1,3}(?::\d{1,2}){1,2}(?:\.\d+)?"
_UNITS = r"(?:\d+h)(?:\d+m)?(?:\d+(?:\.\d+)?s)?|(?:\d+m)(?:\d+(?:\.\d+)?s)?|\d+(?:\.\d+)?s"
_TIME = re.compile(rf"(?<![\w:.])({_COLON}|{_UNITS})(?![\w:])", re.I)
_LINK_T = re.compile(r"[?&#]t=([0-9hms.]+)", re.I)
_DASHES = "-–—~|>"


def parse_time(text) -> float:
    """Seconds from "1:05:10", "65:10", "1h5m10s", "3910s" or a plain number of seconds."""
    if isinstance(text, (int, float)):
        v = float(text)
    else:
        t = str(text).strip().lower()
        if not t:
            raise ValueError("A time is empty.")
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            v = float(t)
        elif re.fullmatch(_COLON, t):
            parts = [float(p) for p in t.split(":")]
            if any(p >= 60 for p in parts[1:]):
                raise ValueError(f"{text} isn't a time: minutes and seconds go up to 59.")
            v = 0.0
            for p in parts:
                v = v * 60 + p
        else:
            m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", t)
            if not m or not any(m.groups()):
                raise ValueError(f"{text} isn't a time. Write it like 1:05:10 or 1h5m10s.")
            h, mi, s = (float(g or 0) for g in m.groups())
            v = h * 3600 + mi * 60 + s
    if v != v or v < 0 or v > 1_000_000:
        raise ValueError(f"{text} isn't a time.")
    return round(v, 2)


def fmt(seconds: float) -> str:
    """Seconds as YouTube shows them: 1:05:10, or 12:05 under an hour."""
    s = int(round(max(0.0, float(seconds))))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _times_in(line: str):
    """The times written in a line, in order, with where each one sits."""
    out = []
    for m in _TIME.finditer(line):
        out.append((m.start(), m.end(), parse_time(m.group(1))))
    return out


def parse_moments(text, limit=MAX_MOMENTS) -> list:
    """Every moment in a block of text, one per line: [{"start", "end" (or None), "note"}].

    Raises ValueError, in words a person can act on, for a line that has times but makes no sense."""
    if isinstance(text, list):
        return _check_list(text, limit)
    out = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        link = _LINK_T.search(line)
        if link and ("youtu" in line.lower() or line.lower().startswith("http")):
            start = parse_time(link.group(1))
            note = re.sub(r"https?://\S+", "", line).strip(" " + _DASHES + ":")
            out.append({"start": start, "end": None, "note": note[:200]})
        else:
            found = _times_in(line)
            if not found:
                if re.search(r"\d", line):
                    raise ValueError(f'"{line[:60]}" has no time in it. Write it like 1:05:10 – 1:06:09.')
                continue
            start = found[0][2]
            end = found[1][2] if len(found) > 1 else None
            cut = line[:found[0][0]] + " " + line[(found[1] if len(found) > 1 else found[0])[1]:]
            note = re.sub(r"\s+", " ", cut).strip(" " + _DASHES + ":,")
            if end is not None and end <= start:
                raise ValueError(f'"{line[:60]}": the end has to come after the start.')
            out.append({"start": start, "end": end, "note": note[:200]})
        if len(out) > limit:
            raise ValueError(f"That's more than {limit} moments. Add the rest as another video.")
    return out


def _check_list(items, limit) -> list:
    out = []
    for x in items[:limit + 1]:
        if not isinstance(x, dict):
            raise ValueError("Those moments aren't valid.")
        start = parse_time(x.get("start", ""))
        end = None if x.get("end") in (None, "") else parse_time(x["end"])
        if end is not None and end <= start:
            raise ValueError(f"The moment at {fmt(start)}: the end has to come after the start.")
        out.append({"start": start, "end": end, "note": str(x.get("note") or "")[:200]})
    if len(out) > limit:
        raise ValueError(f"That's more than {limit} moments. Add the rest as another video.")
    return out
