"""New layouts retain edited sequences, censorship and embedded post text."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")
TEMP = tempfile.TemporaryDirectory(prefix="pc-layout-merge-")
os.environ.update(PEAKCUT_DATA=TEMP.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
from studio import accounts, ctx, render, tools  # noqa: E402


class LayoutMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = tools.setup()
        if missing:
            raise unittest.SkipTest(f"Missing video tools: {', '.join(missing)}")
        cls.source = Path(TEMP.name) / "source.mp4"
        # Both sides have distinct colours, so keeping the wrong side is visible.
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=red:s=320x180:r=12:d=4,drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                        "-c:v", "libx264", "-c:a", "aac", "-t", "4", str(cls.source)], check=True)
        render.install_fonts()

    def test_new_layouts_keep_sequence_sound_and_metadata(self):
        # Removing metadata, using [0:v] instead of the joined picture, dropping
        # mute, or treating clip-relative audio times as source times breaks this.
        with ctx.as_user("1111333355557777"):
            accounts.save_settings({"censorship_mode": "audio", "censorship_words": "bad"})
            with patch.multiple(render, W=180, H=320, _qsv_ok=False), patch.object(render.config, "OUT_FPS", 12):
                for layout in ("left", "right", "fit", "split"):
                    for joined in (False, True):
                        with self.subTest(layout=layout, joined=joined):
                            clip = {"start": 1., "end": 3., "layout": layout, "captions": False,
                                    "title": "Título português", "description": "Descrição do corte"}
                            speech = [{"w": "bad", "s": 1.6, "e": 2.0}]
                            segments = [(self.source, 2., 1.), (self.source, 0., 1.)] if joined else None
                            name = f"{layout}_{int(joined)}"
                            result = render.render_clip(self.source, Path(TEMP.name) / "out", name, clip, [],
                                                        320, 180, segments=segments, speech_words=speech)
                            self.assertEqual(result["layout"], layout)
                            output = Path(TEMP.name) / "out" / result["file"]
                            info = json.loads(subprocess.check_output([
                                "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)]))
                            self.assertAlmostEqual(float(info["format"]["duration"]), 2., delta=.12)
                            video = next(s for s in info["streams"] if s["codec_type"] == "video")
                            self.assertEqual((video["width"], video["height"]), (180, 320))
                            tags = info["format"]["tags"]
                            self.assertEqual(tags.get("title"), "Título português")
                            self.assertEqual(tags.get("description"), "Descrição do corte")
                            self.assertEqual(tags.get("comment"), "Descrição do corte")
                            samples = np.frombuffer(subprocess.check_output([
                                "ffmpeg", "-v", "error", "-i", str(output), "-ac", "1", "-ar", "16000",
                                "-f", "s16le", "pipe:1"]), dtype="<i2")
                            self.assertGreater(np.max(np.abs(samples[1600:4800])), 10)
                            self.assertLess(np.max(np.abs(samples[10400:14400])), 5)
                            self.assertGreater(np.max(np.abs(samples[20800:25600])), 10)
                            frame = np.frombuffer(subprocess.check_output([
                                "ffmpeg", "-v", "error", "-ss", "0.3", "-i", str(output), "-frames:v", "1",
                                "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]), dtype=np.uint8).reshape(320, 180, 3)
                            if layout == "left":
                                self.assertGreater(int(frame[160, 90, 0]), 200)
                                self.assertLess(int(frame[160, 90, 2]), 30)
                            elif layout == "right":
                                self.assertGreater(int(frame[160, 90, 2]), 200)
                                self.assertLess(int(frame[160, 90, 0]), 30)
                            elif layout == "fit":
                                self.assertLess(int(frame[30].max()), 10)
                                self.assertGreater(int(frame[160, 30, 0]), 200)
                                self.assertGreater(int(frame[160, 150, 2]), 200)
                            else:
                                self.assertGreater(int(frame[80, 90, 0]), 200)
                                self.assertGreater(int(frame[240, 90, 2]), 200)

    def test_standalone_render_uses_defaults_without_inheriting_account_censorship(self):
        # Rendering a local file without a web session has no account preferences.
        with ctx.as_user("2222444466668888"):
            accounts.save_settings({"censorship_mode": "both", "censorship_words": "bad"})
        clip = {"start": 0., "end": 2., "layout": "fit", "hook": "bad",
                "title": "Título fixo", "description": "Descrição fixa", "fixed_description": True,
                "fixed_hook": True}
        with ctx.as_user(None), patch.multiple(render, W=180, H=320, _qsv_ok=False):
            result = render.render_clip(self.source, Path(TEMP.name) / "out", "standalone", clip,
                                        [{"w": "bad", "s": .6, "e": 1.}], 320, 180)
        output = Path(TEMP.name) / "out" / result["file"]
        ass = output.with_suffix(".ass").read_text(encoding="utf-8")
        self.assertIn("BAD", ass)
        self.assertIn("bad", ass)
        tags = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_format", "-of", "json", str(output)]))["format"]["tags"]
        self.assertEqual(tags.get("title"), "Título fixo")
        self.assertEqual(tags.get("description"), "Descrição fixa")
        self.assertEqual(tags.get("comment"), "Descrição fixa")
        samples = np.frombuffer(subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(output), "-ac", "1", "-ar", "16000",
            "-f", "s16le", "pipe:1"]), dtype="<i2")
        self.assertGreater(np.max(np.abs(samples[10400:14400])), 10)

    def test_signed_in_render_does_not_hide_account_setting_failures(self):
        with ctx.as_user("2222444466668888"), patch.object(accounts, "settings", side_effect=OSError("read failed")):
            with self.assertRaisesRegex(OSError, "read failed"):
                render.render_clip(self.source, Path(TEMP.name) / "out", "failed", {"start": 0., "end": 2.},
                                   [], 320, 180)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        TEMP.cleanup()
