"""Regression test for FFmpeg ASS subtitle filter parsing."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

with tempfile.TemporaryDirectory(prefix="pc-render-") as data:
    os.environ.update(PEAKCUT_DATA=data, DATABASE_URL="")

    from studio import config, render

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required for this rendering test")

    filters = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True, text=True, check=True
    ).stdout

    has_ass = any(
        len(line.split()) > 1 and line.split()[1] == "ass"
        for line in filters.splitlines()
    )
    if not has_ass:
        raise RuntimeError("FFmpeg must be built with libass support")

    config.FONTS_DIR.mkdir(parents=True, exist_ok=True)

    root = Path(data)
    source = root / "source.mp4"
    work = root / "work"
    output = root / "clips"

    work.mkdir()
    output.mkdir()

    # Create a short synthetic video with audio.
    subprocess.run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=15:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(source)
    ], check=True)

    # A minimal valid ASS subtitle script.
    ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,30,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.20,0:00:01.20,Default,,0,0,0,,ASS filter regression test
"""

    result = render._render_in(
        work, source, output, "clip01",
        0.0, 1.5, "crop", config.OUT_H,
        ass, meta={}
    )

    rendered = output / result["file"]
    assert rendered.is_file() and rendered.stat().st_size > 0

    print("PASS FFmpeg renders a video with ASS subtitles")
    print(f"Output: {rendered}")
