"""Portuguese model compatibility, pooling and remote fallback; no real model or network."""
import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

DATA = Path(tempfile.mkdtemp(prefix="pc-pt-asr-")).resolve()
os.environ.update(PEAKCUT_DATA=str(DATA), DATABASE_URL="", PEAKCUT_FETCH_MODEL="0")
os.environ.pop("PEAKCUT_MODELS", None)
sys.path.insert(0, ".")
from studio import config, fetch_model, transcribe  # noqa: E402


class PortugueseSpeech(unittest.TestCase):
    def setUp(self):
        transcribe._pools.clear()
        transcribe._remote_lacks.clear()
        transcribe._remote_down_until = 0

    def test_registry_and_legacy_directories(self):
        self.assertEqual(fetch_model.DEFAULT, "en")
        self.assertTrue(fetch_model.known("pt"))
        self.assertEqual(fetch_model.folder("pt"), config.ASR_MULTILINGUAL_DIR)
        self.assertTrue(fetch_model.MODELS["pt"][2].endswith(config.ASR_MULTILINGUAL_DIR.name + ".tar.bz2"))
        with patch.object(fetch_model, "_fetch") as fetch:
            fetch_model.ensure(config.ASR_MULTILINGUAL_DIR)
            fetch.assert_called_once_with("pt")
        self.assertFalse(fetch_model.ready(config.ASR_MULTILINGUAL_DIR))
        with patch.object(fetch_model, "_fetch") as fetch:
            fetch_model.ensure(model_dir=config.ASR_MULTILINGUAL_DIR)
            fetch.assert_called_once_with("pt")
        self.assertFalse(fetch_model.ready(model_dir=config.ASR_MULTILINGUAL_DIR))

    def test_multilingual_download_is_whole_and_uses_upstream_timeouts(self):
        name = config.ASR_MULTILINGUAL_DIR.name
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:bz2") as tar:
            for part in ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"):
                item = tarfile.TarInfo(name + "/" + part)
                item.size = 5
                tar.addfile(item, io.BytesIO(b"model"))
        response = io.BytesIO(data.getvalue())
        response.headers = {"Content-Length": str(len(data.getvalue()))}
        with patch.object(fetch_model.urllib.request, "urlopen", return_value=response) as opening:
            fetch_model.ensure(config.ASR_MULTILINGUAL_DIR)
        self.assertEqual(opening.call_args.args[0], fetch_model.MODELS["pt"][2])
        self.assertEqual(opening.call_args.kwargs["timeout"], fetch_model.WAIT)
        self.assertTrue(fetch_model.ready("pt"))
        self.assertEqual(list(config.MODELS_DIR.iterdir()), [config.ASR_MULTILINGUAL_DIR])
        shutil.rmtree(config.ASR_MULTILINGUAL_DIR)

    def test_incomplete_archive_is_not_installed(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:bz2") as tar:
            item = tarfile.TarInfo(config.ASR_MULTILINGUAL_DIR.name + "/tokens.txt")
            item.size = 1
            tar.addfile(item, io.BytesIO(b"x"))
        response = io.BytesIO(data.getvalue())
        response.headers = {}
        with patch.object(fetch_model.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "whole model"):
                fetch_model.ensure("pt")
        self.assertFalse(fetch_model.ready("pt"))
        self.assertFalse(config.ASR_MULTILINGUAL_DIR.exists())
        self.assertFalse(fetch_model.state["pt"]["doing"])
        self.assertEqual(list(config.MODELS_DIR.iterdir()), [])

    def test_new_keeps_portuguese_automatic_and_arabic_opt_in(self):
        class Library:
            class OfflineRecognizer:
                from_transducer = staticmethod(lambda **kwargs: kwargs)
        with (patch.object(transcribe, "_sherpa", return_value=Library),
              patch.object(transcribe, "_model_files", return_value={}),
              patch.object(fetch_model, "ensure") as ensure):
            for lang, auto in (("en", True), ("pt", True), ("ar", False)):
                transcribe._new(lang)
                ensure.assert_called_with(lang, start=auto)

    def test_multilingual_keyword_and_source_language(self):
        audio = transcribe.np.full(16000, 1000, dtype=transcribe.np.int16)
        for kwargs, lang in (({"multilingual": True}, "pt"), ({"lang": "pt"}, "pt"),
                             ({"lang": "ar", "multilingual": True}, "ar"), ({}, "en")):
            with (patch.object(transcribe, "REMOTE", ""),
                  patch.object(transcribe, "_take_now", return_value=object()) as take,
                  patch.object(transcribe, "decode_pieces", return_value=[(["▁Olá", "▁ação"], [0.1, 0.5])]) as decode):
                words = transcribe.transcribe(audio, **kwargs)
            take.assert_called_with(lang)
            self.assertEqual(decode.call_args.args[2], lang)
            self.assertEqual([w["w"] for w in words], ["Olá", "ação"])
            self.assertEqual([w["s"] for w in words], [0.1, 0.5])

    def test_separate_pool_and_release_after_decoder_failure(self):
        with patch.object(transcribe, "_new", side_effect=lambda lang: object()):
            pt, en = transcribe._take("pt"), transcribe._take("en")
        self.assertIsNot(pt, en)
        with patch.object(transcribe, "_decode_local", side_effect=RuntimeError("bad audio")):
            with self.assertRaises(RuntimeError):
                transcribe.decode_pieces([], pt, "pt")
        self.assertEqual(transcribe._pool("pt").free.qsize(), 1)
        self.assertFalse(transcribe.unload("en"))
        self.assertTrue(transcribe.unload("pt"))
        transcribe._give_back(en, "en")

    def test_idle_pool_eviction_keeps_english_and_busy_languages(self):
        pt = transcribe._pool("pt")
        en = transcribe._pool("en")
        ar = transcribe._pool("ar")
        pt.used = en.used = ar.used = 0
        ar.made = 1  # still transcribing, so it must stay
        transcribe._pool("en")
        self.assertNotIn("pt", transcribe._pools)
        self.assertIs(transcribe._pools["en"], en)
        self.assertIs(transcribe._pools["ar"], ar)

    def test_old_remote_english_is_refused_and_portuguese_falls_back(self):
        class Reply:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"pieces": [{"tokens": ["▁hello"], "timestamps": [0.0]}]}
        import httpx
        audio = transcribe.np.full(16000, 1000, dtype=transcribe.np.int16)
        with (patch.object(transcribe, "REMOTE", "https://worker.invalid"),
              patch.object(httpx, "post", return_value=Reply()) as remote,
              patch.object(transcribe, "_take_now", return_value=object()),
              patch.object(transcribe, "decode_pieces", return_value=[(["▁Olá"], [0.1])]) as decode):
            words = transcribe.transcribe(audio, lang="pt")
        self.assertEqual(remote.call_args.kwargs["headers"]["X-Lang"], "pt")
        self.assertEqual(words[0]["w"], "Olá")
        self.assertEqual(decode.call_args.args[2], "pt")
        self.assertIn("pt", transcribe._remote_lacks)
        self.assertNotIn("en", transcribe._remote_lacks)
        self.assertEqual(transcribe._remote_down_until, 0)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
