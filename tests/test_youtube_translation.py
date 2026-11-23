"""Account-scoped choice to use YouTube's automatic English translations; no network calls."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_data = tempfile.TemporaryDirectory(prefix="pc-yt-translation-")
os.environ.update(PEAKCUT_DATA=_data.name, DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0")
from studio import accounts, ctx, jobs, media


class Video:
    def __init__(self, language="pt-BR", automatic=None, subtitles=None):
        self.info = {"language": language, "automatic_captions": automatic or {}, "subtitles": subtitles or {}}
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extract_info(self, *args, **kwargs):
        return self.info

    def urlopen(self, url):
        self.requested.append(url)
        return io.BytesIO(json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 1000,
                                                 "segs": [{"utf8": "spoken words"}]}]}).encode())


def track(name):
    return [{"ext": "json3", "url": "https://captions.invalid/" + name}]


class TranslationChoice(unittest.TestCase):
    def test_original_track_has_priority_in_both_states(self):
        for enabled in (True, False):
            video = Video(automatic={"en": track("translated"), "pt-orig": track("original")})
            with patch.object(media, "_ydl", return_value=video):
                words, language = media.youtube_words("https://youtube.com/watch?v=test", allow_translation=enabled)
            self.assertEqual(language, "pt")
            self.assertEqual(words[0]["w"], "spoken")
            self.assertEqual(video.requested, ["https://captions.invalid/original"])

    def test_translated_only_track_is_available_when_enabled(self):
        video = Video(automatic={"en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            words, language = media.youtube_words("https://youtube.com/watch?v=test", allow_translation=True)
        self.assertEqual(language, "en")
        self.assertEqual(words[0]["w"], "spoken")

    def test_translated_only_track_is_not_used_when_disabled(self):
        video = Video(automatic={"en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            self.assertIsNone(media.youtube_words("https://youtube.com/watch?v=test", allow_translation=False))
        self.assertEqual(video.requested, [])

    def test_translated_text_keeps_the_spoken_language_separate(self):
        video = Video(automatic={"en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            transcript = media.youtube_words("https://youtube.com/watch?v=test", allow_translation=True)
        self.assertIsInstance(transcript, tuple)
        words, language = transcript
        self.assertTrue(words)
        self.assertEqual(language, "en")
        self.assertEqual(transcript.spoken_language, "pt-BR")
        self.assertTrue(transcript.translated)

    def test_original_english_automatic_captions_still_work_when_disabled(self):
        video = Video(language="en", automatic={"en": track("original")})
        with patch.object(media, "_ydl", return_value=video):
            words, language = media.youtube_words("https://youtube.com/watch?v=test", allow_translation=False)
        self.assertEqual(language, "en")
        self.assertTrue(words)

    def test_unusable_original_track_can_fall_back_when_enabled(self):
        video = Video(automatic={"pt-orig": [{"ext": "vtt", "url": "https://captions.invalid/vtt"}],
                                 "en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            result = media.youtube_words("https://youtube.com/watch?v=test", allow_translation=True)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "en")

    def test_original_english_marker_is_available_without_language_metadata(self):
        video = Video(language=None, automatic={"en-orig": track("original")})
        with patch.object(media, "_ydl", return_value=video):
            self.assertTrue(media.youtube_english_words("https://youtube.com/watch?v=test", allow_translation=False))

    def test_english_translation_helper_respects_switch(self):
        video = Video(automatic={"en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            self.assertIsNone(media.youtube_english_words("https://youtube.com/watch?v=test", allow_translation=False))
            self.assertTrue(media.youtube_english_words("https://youtube.com/watch?v=test", allow_translation=True))

    def test_human_written_english_subtitles_remain_available(self):
        video = Video(subtitles={"en": track("human")}, automatic={"en": track("translated")})
        with patch.object(media, "_ydl", return_value=video):
            self.assertTrue(media.youtube_english_words("https://youtube.com/watch?v=test", allow_translation=False))
        self.assertEqual(video.requested, ["https://captions.invalid/human"])

    def test_settings_are_boolean_persisted_and_account_scoped(self):
        with ctx.as_user("1111111111111111"):
            self.assertIs(accounts.settings()["youtube_auto_translate"], True)
            self.assertIs(accounts.save_settings({"youtube_auto_translate": False})["youtube_auto_translate"], False)
            accounts._settings_mem.pop(ctx.uid(), None)
            self.assertIs(accounts.settings()["youtube_auto_translate"], False)
            for invalid in ("false", 0, None):
                with self.assertRaises(ValueError):
                    accounts.save_settings({"youtube_auto_translate": invalid})
        with ctx.as_user("2222222222222222"):
            self.assertIs(accounts.settings()["youtube_auto_translate"], True)

    def test_job_keeps_its_choice_after_settings_change(self):
        with ctx.as_user("3333333333333333"):
            accounts.save_settings({"youtube_auto_translate": False})
            job = jobs.create({"type": "file", "name": "video.mp4"}, {}, enqueue=False)
            accounts.save_settings({"youtube_auto_translate": True})
            self.assertIs(job["youtube_auto_translate"], False)
            saved = json.loads((Path(job["dir"]) / "job.json").read_text(encoding="utf-8"))
            self.assertIs(saved["youtube_auto_translate"], False)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _data.cleanup()
