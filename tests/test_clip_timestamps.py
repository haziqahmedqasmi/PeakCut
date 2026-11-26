"""Clip timestamps stay in order before ffmpeg runs (Issue #115).

Covers every place a clip's start and end can cross over or collapse to zero:
fetched video pieces that do not cover the clip, AI timestamps with end <= start,
audio alignment, live span clamping, audio extraction, and rendering.

Runs against the code directly with a temporary data folder. No ffmpeg, no network."""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

_data_dir = None
if not os.environ.get("PEAKCUT_DATA"):
    _data_dir = tempfile.mkdtemp(prefix="pc-clip-times-")
    os.environ["PEAKCUT_DATA"] = _data_dir

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import brain, jobs, live, render  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


try:
    # ---- _fetch_piece_once: a fetched section ending before clip start must fail cleanly without inverting clip
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "clips").mkdir()
        piece = root / "clips" / "c1.src.mp4"
        piece.write_bytes(b"fake")
        job = {
            "id": "j1",
            "dir": str(root),
            "duration": 3600.0,
            "source": {"type": "url", "url": "https://example.com/v"},
            "options": {"quality": 720},
        }
        # Reproduces Issue #115: clip is 2102.71..2132.71, but the fetched section ends at 2096.71.
        clip = {"id": "c1", "start": 2102.71, "end": 2132.71, "status": "rendering"}
        with mock.patch.object(jobs.media, "download_section", return_value=piece), \
             mock.patch.object(jobs.media, "extract_audio"), \
             mock.patch.object(jobs.media, "load_audio", return_value=[0]), \
             mock.patch.object(jobs.media, "find_offset", return_value=2076.71), \
             mock.patch.object(jobs.media, "probe", return_value={"duration": 20.0, "width": 1280, "height": 720}):
            jobs._fetch_piece_once(job, clip, [0])
        check("short fetched section marks clip as error without inverting timestamps",
              clip["status"] == "error" and "does not cover this clip" in (clip.get("error") or "")
              and (clip["start"], clip["end"]) == (2102.71, 2132.71) and not piece.exists(),
              repr(clip))

    # ---- _piece_again: a short re-fetched section raises cleanly and removes the unusable file
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "clips").mkdir()
        piece = root / "clips" / "c1.src.mp4"
        piece.write_bytes(b"fake")
        job = {
            "id": "j1",
            "dir": str(root),
            "duration": 3600.0,
            "source": {"type": "url", "url": "https://example.com/v"},
            "options": {"quality": 720},
        }
        clip = {"id": "c1", "start": 2102.71, "end": 2132.71}
        raised = None
        with mock.patch.object(jobs.media, "download_section", return_value=piece), \
             mock.patch.object(jobs.media, "probe", return_value={"duration": 4.0, "width": 1280, "height": 720}):
            try:
                jobs._piece_again(job, clip)
            except RuntimeError as e:
                raised = str(e)
        check("re-fetched short section raises cleanly without mutating clip",
              bool(raised) and "does not cover this clip" in raised
              and (clip["start"], clip["end"]) == (2102.71, 2132.71) and not piece.exists(),
              f"{raised!r} {clip!r}")

    # ---- render_clip: rejects inverted and zero-length clips before creating folders or calling ffmpeg
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        errors = []
        for start, end in ((2102.71, 2096.71), (10.0, 10.0)):
            try:
                render.render_clip(Path(tmp) / "src.mp4", out, "c1", {"start": start, "end": end}, [], 1280, 720)
            except ValueError as e:
                errors.append(str(e))
        check("render_clip rejects inverted and zero-length spans before ffmpeg",
              len(errors) == 2 and all("Invalid clip timestamps" in msg for msg in errors) and not out.exists(),
              repr(errors))

    # ---- _clip_sound: rejects inverted and zero-length audio ranges before calling ffmpeg
    sound_errors = []
    for a, b in ((20.0, 15.0), (15.0, 15.0)):
        try:
            jobs._clip_sound(Path("dummy.mp4"), a, b)
        except ValueError as e:
            sound_errors.append(str(e))
    check("_clip_sound rejects inverted and zero-length audio ranges",
          len(sound_errors) == 2 and all("Invalid clip audio range" in msg for msg in sound_errors),
          repr(sound_errors))

    # ---- brain.judge_signals: drops AI moments whose end <= start
    ai_out = [
        {"start": 2102.71, "end": 2096.71, "score": 90, "reason": "inverted"},
        {"start": 100.0, "end": 100.0, "score": 80, "reason": "zero"},
        {"start": 110.0, "end": 135.0, "score": 85, "reason": "valid"},
    ]
    with mock.patch.object(brain, "ask_ai", return_value=(ai_out, "test-model", 10)):
        moments, spent = brain.judge_signals([0.0] * 20, None, 100.0, 140.0)
    check("judge_signals drops AI moments with end <= start",
          spent == 10 and len(moments) == 1 and (moments[0]["start"], moments[0]["end"]) == (110.0, 135.0),
          repr(moments))

    # ---- brain.listen: falls back to picked span if quiet-edge snapping collapses start/end
    ai_listen = {
        "start": 10.0,
        "end": 25.0,
        "language": "en",
        "spoken": "en",
        "phrases": [{"text": "hello world", "start": 10.2, "end": 24.5}],
        "hook": "",
        "payoff": "end",
        "payoff_end": 24.5,
    }
    with mock.patch.object(brain, "can_listen", return_value=True), \
         mock.patch.object(brain, "ask_ai", return_value=(ai_listen, "test-model", 5)), \
         mock.patch.object(brain, "_quiet", side_effect=[20.0, 19.0]):
        got = brain.listen((b"x", "audio/ogg"), [0] * 16000, 40.0, 10.0, 25.0, 15, 60)
    check("listen falls back to picked span when quiet snapping inverts endpoints",
          (got["start"], got["end"]) == (10.0, 25.0), repr(got))

    # ---- live._clip_span: returns None when the clamped span is inverted or empty
    check("live _clip_span keeps valid spans and drops empty/inverted ones",
          live._clip_span({"start": 100, "end": 130}, 92, 138) == (100, 130)
          and live._clip_span({"start": 2102.71, "end": 2096.71}, 2000, 2200) is None
          and live._clip_span({"start": 100, "end": 130}, 135, 200) is None)

    # ---- render_one: marks an inverted clip as error without invoking render_clip
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = root / "src.mp4"
        src.write_bytes(b"fake")
        clip = {"id": "c1", "start": 2102.71, "end": 2096.71, "status": "queued"}
        job = {"id": "j1", "creator": "test-creator", "dir": str(root),
               "source_path": str(src), "duration": 3000.0, "clips": [clip]}
        with mock.patch.object(jobs, "save"), \
             mock.patch.object(jobs.creators, "log"), \
             mock.patch.object(jobs.render, "render_clip") as render_mock:
            jobs.render_one(job, clip, [])
        check("render_one marks inverted clip as error without calling render_clip",
              not render_mock.called and clip["status"] == "error"
              and "Invalid clip timestamps" in (clip.get("error") or ""),
              repr(clip))
finally:
    if _data_dir:
        shutil.rmtree(_data_dir, ignore_errors=True)

print("\n" + (f"{len(fails)} failed" if fails else "All checks passed"))
sys.exit(1 if fails else 0)
