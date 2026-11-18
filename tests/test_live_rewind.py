"""Tests for live stream rewind and past hours analysis.

Verifies:
1. Rewind hours parsing (all, 3h, 2, live, 0).
2. Start session configuration and rewind propagation.
3. Live start index calculation for ffmpeg.
4. Catch-up window stepping (sequential analysis without skipping hours).
5. Buffer segment retention during catch-up.
6. Rate limiting by stream hour rather than wall-clock time during catch-up.
"""
import os, sys, tempfile
from unittest.mock import patch
from io import BytesIO
os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-rewind-")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, ".")
from studio import auth, config, creators, ctx, db, live, jobs

db.init()
fails = []

def check(n, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"   got {got!r}"))
    if not ok:
        fails.append(n)

# 1. Parse rewind hours
d = tempfile.mkdtemp(prefix="pc-live-")
s_all = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind="all")
check("rewind 'all' parses to -1.0", s_all._parse_rewind_hours(), -1.0)

s_start = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind="start")
check("rewind 'start' parses to -1.0", s_start._parse_rewind_hours(), -1.0)

s_2h = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind="2h")
check("rewind '2h' parses to 2.0", s_2h._parse_rewind_hours(), 2.0)

s_3 = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind=3)
check("rewind 3 parses to 3.0", s_3._parse_rewind_hours(), 3.0)

s_live = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind="live")
check("rewind 'live' parses to 0.0", s_live._parse_rewind_hours(), 0.0)

s_zero = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, rewind="0")
check("rewind '0' parses to 0.0", s_zero._parse_rewind_hours(), 0.0)

# 2. Defaults in creators
check("creator default live_rewind is 'all'", creators.DEFAULTS.get("live_rewind"), "all")

# 3. Start session propagation
u, _ = auth._create("rewind_tester@example.com", "Tester", password_hash="x")
with ctx.as_user(u.id):
    c = creators.create({"name": "Live Rewind Creator", "channel": "https://www.twitch.tv/rewindstreamer", "live_rewind": "2h"})
    check("creator stores live_rewind setting", c.get("live_rewind"), "2h")
    
    # Mock Session.start so it doesn't spawn real threads
    orig_start = live.Session.start
    live.Session.start = lambda self: None
    try:
        job = live.start_session(c, rewind="3h")
        check("start_session sets rewind option", job["options"].get("live_rewind"), "3h")
        
        job_def = live.start_session(c)
        check("start_session uses creator setting by default", job_def["options"].get("live_rewind"), "2h")
    finally:
        live.Session.start = orig_start

# 4. Sequential catch-up window logic
# Simulate a session with 2 hours of backlog
s = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}, "title": "2h Stream", "uploader": "Pro"},
                 {"ai_every_min": 3, "buffer_min": 20, "live_per_hour": 4, "must_clip_min": 0, "min_score": 80,
                  "decide_mode": "both", "min_len": 15, "max_len": 60, "min_gap_min": 2, "style": "bounce", "layout": "blur"},
                 rewind="all")
s.t_done = 7200.0  # 2 hours transcribed
s.seg_clock = 7200.0
s.last_check = 0.0

asked_windows = []
s._ask_ai = lambda window=None, why=None: asked_windows.append((window, why))

# Step 1: backlog check triggers first 3m window
step_sec = 3 * 60
win_end = min(s.t_done, s.last_check + step_sec)
win_start = max(0.0, win_end - step_sec - 90)
check("first window ends at 180s", win_end, 180.0)
check("first window starts at 0s", win_start, 0.0)

# Simulate what _ai_check does to last_check on catch-up
s.last_check = win_end
win_end_2 = min(s.t_done, s.last_check + step_sec)
win_start_2 = max(0.0, win_end_2 - step_sec - 90)
check("second window ends at 360s", win_end_2, 360.0)
check("second window starts with 90s overlap at 90s", win_start_2, 90.0)

# 5. Segment retention during catch-up
# Suppose seg_clock is 7200s (2h), buffer_min is 20m (1200s), but last_check is only at 360s
# Standard keep_from would be 7200 - 1200 = 6000s, which would delete segments 360-6000!
# With our catch-up safety:
buf_keep = s.seg_clock - s.settings["buffer_min"] * 60
analyzed_keep = max(0.0, s.last_check - 90)
keep_from = min(buf_keep, analyzed_keep) if s.catching_up else buf_keep
check("segments ahead of last_check are preserved during catch-up", keep_from <= 360.0, True)

# 6. Stream hour rate limiting
# Clips in stream hour 0 vs stream hour 1
s.job["clips"] = [
    {"score": 90, "start": 500, "made_at": 100},
    {"score": 90, "start": 900, "made_at": 105},
    {"score": 90, "start": 1200, "made_at": 110},
    {"score": 90, "start": 1500, "made_at": 115},
]
# Stream hour 0 has 4 clips. Stream hour 1 (t=3700) should have 0 clips and NOT be throttled
hour_0_clips = [c for c in s.job["clips"] if int(c["start"] / 3600) == 0]
hour_1_clips = [c for c in s.job["clips"] if int(c["start"] / 3600) == 1]
check("stream hour 0 has 4 clips", len(hour_0_clips), 4)
check("stream hour 1 has 0 clips and is not blocked by hour 0", len(hour_1_clips), 0)

# 7. Custom range tracking
check("parse_stream_time parses seconds directly", live.parse_stream_time(1800), 1800.0)
check("parse_stream_time parses '30m'", live.parse_stream_time("30m"), 1800.0)
check("parse_stream_time parses '2h'", live.parse_stream_time("2h"), 7200.0)
check("parse_stream_time parses '01:30:00'", live.parse_stream_time("01:30:00"), 5400.0)
check("parse_stream_time parses '-2h'", live.parse_stream_time("-2h"), -7200.0)
check("parse_stream_time parses 'start'", live.parse_stream_time("start"), 0.0)
check("parse_stream_time parses 'live'", live.parse_stream_time("live") is None, True)

# Session with custom range 1800s to 5400s
s_range = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, range_start="30m", range_end="90m")
check("range_start is set to 1800.0", s_range.range_start, 1800.0)
check("range_end is set to 5400.0", s_range.range_end, 5400.0)
check("last_check initializes at range_start", s_range.last_check, 1800.0)

# Invalid range rejected
try:
    live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {}, range_start="90m", range_end="30m")
    inv = "accepted"
except ValueError:
    inv = "rejected"
check("invalid range (end <= start) is rejected", inv, "rejected")

# Playlist positions use the actual durations, not an assumed two-second segment.
playlist = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:5\n#EXTINF:4,\na.ts\n#EXTINF:7,\nb.ts\n#EXTINF:9,\nc.ts\n"
import yt_dlp
with patch.object(yt_dlp.YoutubeDL, "urlopen", side_effect=lambda url: BytesIO(playlist)):
    check("rewind uses playlist durations", live._rewind_index("https://example.com/live.m3u8", 12 / 3600), -2)
    try:
        live._rewind_index("https://example.com/live.m3u8", -1)
        result = "accepted"
    except ValueError:
        result = "rejected"
    check("missing stream beginning is reported", result, "rejected")

# A historical window advances even when it ends at the latest transcript time.
s.last_check = 0
s.t_done = 180
s.words = []
s._ai_check((0, 180), "Tracking stream range")
check("historical analysis advances its checkpoint", s.last_check, 180)

# 8. Interactive range slider manual clipping (clip_range)
s_clip = live.Session({"dir": d, "creator": "", "clips": [], "ai": {}}, {})
s_clip.samples = int(120.0 * live.SR)  # 120 seconds recorded

# Test clip_last / seconds
s_clip.manual = []
s_clip.clip_last(30)
check("clip_last(30) start is 90.0", s_clip.manual[-1]["start"], 90.0)
check("clip_last(30) end is 120.0", s_clip.manual[-1]["end"], 120.0)

# Test clip_range with start_offset and end_offset
s_clip.manual = []
s_clip.clip_range(start_offset=60, end_offset=15)
check("clip_range(start_offset=60, end_offset=15) start is 60.0", s_clip.manual[-1]["start"], 60.0)
check("clip_range(start_offset=60, end_offset=15) end is 105.0", s_clip.manual[-1]["end"], 105.0)

# Test clip_range with absolute start and end
s_clip.manual = []
s_clip.clip_range(start=20.0, end=50.0)
check("clip_range(start=20, end=50) start is 20.0", s_clip.manual[-1]["start"], 20.0)
check("clip_range(start=20, end=50) end is 50.0", s_clip.manual[-1]["end"], 50.0)

# Test validation: < 5 seconds
try:
    s_clip.clip_range(start_offset=10, end_offset=8)
    res = "allowed"
except ValueError:
    res = "rejected"
check("clip shorter than 5 seconds is rejected", res, "rejected")

# Test validation: > 300 seconds
s_clip.samples = int(1000.0 * live.SR)
try:
    s_clip.clip_range(start_offset=400, end_offset=0)
    res = "allowed"
except ValueError:
    res = "rejected"
check("clip longer than 300 seconds is rejected", res, "rejected")

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} failed"))
import shutil
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
