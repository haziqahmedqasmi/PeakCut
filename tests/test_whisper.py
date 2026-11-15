"""Mixed speech keeps its words and timings. No models or network needed for these checks."""
import os
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")
DATA = tempfile.TemporaryDirectory(prefix="pc-whisper-")
os.environ["PEAKCUT_DATA"] = DATA.name
os.environ["DATABASE_URL"] = ""
os.environ["PEAKCUT_ASR_BACKEND"] = "whisper"
os.environ["PEAKCUT_WHISPER_MODEL"] = "medium"

import numpy as np
from studio import transcribe


class MixedSpeechTests(unittest.TestCase):
    def test_empty_audio_needs_no_model(self):
        from studio import whisper
        progress = []
        with patch.object(whisper, "_model", None), patch("faster_whisper.WhisperModel", side_effect=AssertionError("loaded for silence")):
            self.assertEqual(whisper.transcribe(np.array([], dtype=np.int16), progress.append), [])
        self.assertEqual(progress, [1.0])

    def test_failed_download_reports_error_and_releases_setup(self):
        from studio import fetch_model
        with patch("faster_whisper.utils.download_model", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RuntimeError, "multilingual speech model"):
                fetch_model.ensure()
        self.assertFalse(fetch_model.state["doing"])
        self.assertTrue(fetch_model.state["error"])

    def test_parakeet_backend_still_uses_existing_path(self):
        with patch.object(transcribe.config, "ASR_BACKEND", "parakeet"), patch("studio.whisper.transcribe", side_effect=AssertionError("Whisper used for Parakeet")):
            self.assertEqual(transcribe.transcribe(np.zeros(16000, dtype=np.int16)), [])

    def test_remote_worker_rejects_whisper_before_warmup(self):
        from studio import asr_worker
        with patch.object(asr_worker, "TOKEN", "test-token"), patch.object(transcribe, "_recognizer") as warmup, patch.object(asr_worker.uvicorn, "run") as serve:
            with self.assertRaisesRegex(SystemExit, "PEAKCUT_ASR_BACKEND=parakeet"):
                asr_worker.main()
            warmup.assert_not_called()
            serve.assert_not_called()

    def test_english_only_whisper_model_is_rejected(self):
        from studio import whisper
        with patch.object(whisper, "_model", None), patch("faster_whisper.WhisperModel", return_value=SimpleNamespace(model=SimpleNamespace(is_multilingual=False))):
            with self.assertRaisesRegex(ValueError, "multilingual"):
                whisper.transcribe(np.ones(16000, dtype=np.int16))
            self.assertIsNone(whisper._model)

    def test_setup_fetches_selected_model_not_parakeet(self):
        from studio import fetch_model
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
            Path(DATA.name, name).write_text("test")
        with patch("faster_whisper.utils.download_model", return_value=DATA.name) as download:
            self.assertTrue(fetch_model.ready())
            download.assert_called_once_with("medium", cache_dir=str(fetch_model.config.MODELS_DIR / "whisper"), local_files_only=True)
        with patch("faster_whisper.utils.download_model", side_effect=[FileNotFoundError(), FileNotFoundError(), DATA.name, DATA.name]) as download:
            fetch_model.ensure()
            self.assertFalse(download.call_args_list[2].kwargs.get("local_files_only", False))
            self.assertTrue(download.call_args.kwargs["local_files_only"])
            self.assertFalse(fetch_model.state["doing"])
            self.assertEqual(fetch_model.state["percent"], 100)
            self.assertEqual(fetch_model.state["error"], "")

    def test_partial_model_is_not_ready(self):
        from studio import fetch_model
        with tempfile.TemporaryDirectory() as partial, patch("faster_whisper.utils.download_model") as download:
            download.return_value = partial
            Path(partial, "config.json").write_text("{}")
            self.assertFalse(fetch_model.ready())

    def test_partial_offline_snapshot_cannot_finish_setup(self):
        from studio import fetch_model
        with tempfile.TemporaryDirectory() as partial, patch("faster_whisper.utils.download_model", return_value=partial):
            Path(partial, "config.json").write_text("{}")
            with self.assertRaisesRegex(RuntimeError, "multilingual speech model"):
                fetch_model.ensure()
            self.assertFalse(fetch_model.ready())
        self.assertFalse(fetch_model.state["doing"])
        self.assertLess(fetch_model.state["percent"], 100)
        self.assertTrue(fetch_model.state["error"])

    def test_downloaded_medium_text_vocabulary_is_ready(self):
        from studio import fetch_model
        with tempfile.TemporaryDirectory() as folder, patch("faster_whisper.utils.download_model", return_value=folder):
            for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                Path(folder, name).write_text("test")
            self.assertTrue(fetch_model.ready())

    def test_mixed_words_keep_word_timings_without_translation(self):
        from studio import whisper
        calls = []
        def infer(audio, **options):
            calls.append(options)
            self.assertEqual(audio.dtype, np.float32)
            return iter([SimpleNamespace(end=1.0, words=[
                SimpleNamespace(word=" आज", start=0.1, end=0.4),
                SimpleNamespace(word=" meeting", start=0.5, end=0.9),
            ])]), None
        progress = []
        with patch.object(whisper, "_model", SimpleNamespace(transcribe=infer)):
            out = whisper.transcribe(np.ones(16000, dtype=np.int16), progress.append)
        self.assertEqual(out, [{"w": "आज", "s": 0.1, "e": 0.4}, {"w": "meeting", "s": 0.5, "e": 0.9}])
        self.assertEqual(calls[0]["task"], "transcribe")
        self.assertIsNone(calls[0]["language"])
        self.assertTrue(calls[0]["multilingual"])
        self.assertTrue(calls[0]["word_timestamps"])
        self.assertEqual(progress[-1], 1.0)

    def test_live_request_enters_inference_before_long_job_finishes(self):
        from studio import whisper
        first_entered, second_entered, release_first = (threading.Event() for _ in range(3))

        def segments():
            if not first_entered.is_set():
                first_entered.set()
                if not release_first.wait(10):
                    raise AssertionError("first request was not released")
            else:
                second_entered.set()
            yield SimpleNamespace(end=1.0, words=[SimpleNamespace(word=" meeting", start=0.1, end=0.9)])

        model = SimpleNamespace(model=SimpleNamespace(is_multilingual=True),
                                transcribe=lambda *a, **kw: (segments(), None))
        with patch.object(whisper, "_model", None), patch("faster_whisper.WhisperModel", return_value=model) as load:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(transcribe.transcribe, np.ones(16000, dtype=np.int16))
                try:
                    self.assertTrue(first_entered.wait(5), "first request did not enter inference")
                    second = pool.submit(transcribe.transcribe, np.ones(16000, dtype=np.int16))
                    self.assertTrue(second_entered.wait(5), "live request blocked behind the long job")
                    self.assertFalse(first.done())
                    self.assertEqual(second.result(timeout=5), [{"w": "meeting", "s": 0.1, "e": 0.9}])
                finally:
                    release_first.set()
                self.assertEqual(first.result(timeout=5), [{"w": "meeting", "s": 0.1, "e": 0.9}])
            load.assert_called_once()
            self.assertEqual(load.call_args.kwargs["num_workers"], 2)

    def test_selected_backend_receives_audio_and_progress(self):
        audio = np.ones(16000, dtype=np.int16)
        progress = lambda p: None
        words = [{"w": "आज", "s": 0.1, "e": 0.4}, {"w": "meeting", "s": 0.5, "e": 0.9}]
        # The inference engine is the external seam; public routing must not use English Parakeet or its remote.
        with patch("studio.whisper.transcribe", side_effect=lambda a, p: words if a is audio and p is progress else []), patch.object(transcribe, "_take_now", side_effect=AssertionError("English-only recognizer used for mixed speech")):
            self.assertEqual(transcribe.transcribe(audio, progress), words)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MixedSpeechTests)
    names = unittest.defaultTestLoader.getTestCaseNames(MixedSpeechTests)
    result = unittest.TextTestRunner().run(suite)
    if result.wasSuccessful():
        for name in names:
            print("PASS " + name)
    DATA.cleanup()
    sys.exit(not result.wasSuccessful())
