"""Fork/upstream compatibility: localized prompts, fixed text, edited speech and subtitle preferences."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, ".")
# Keep nested creator/job paths within the Windows path limit, including in sandboxed runners.
_data = tempfile.TemporaryDirectory(prefix="pcmc-", dir=".")
os.environ.update(PEAKCUT_DATA=str(Path(_data.name).resolve()).replace("\\", "/"), DATABASE_URL="", PEAKCUT_FETCH_MODEL="0")

from studio import accounts, brain, config, creators, ctx, db, fetch_model, hashtags, jobs, live, media, notices, posting, tools, transcribe  # noqa: E402


class MergeClips(unittest.TestCase):
    def test_remaking_post_text_replaces_embedded_hashtags_and_preserves_fixed_description(self):
        with ctx.as_user("e" * 32), patch.object(accounts, "settings", return_value={"caption_language": "en"}), patch.object(jobs._q, "put"):
            job = jobs.create({"type": "file", "name": "sample"}, {}, enqueue=False)
            clip = {"id": "clip1", "start": 0, "end": 10, "status": "done", "description": "My words\n\n#old",
                    "hashtags": ["old"], "hashtags_in_description": True}
            job.update(status="done", clips=[clip])
            jobs.request_render(job["id"], clip["id"], {"hashtags": "#new new"})
            self.assertEqual(posting._caption(clip, {"post_footer": ""}), "My words\n\n#new")
            saved = json.loads(Path(job["dir"], "job.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["clips"][0]["description"], "My words\n\n#new")
            clip.update(status="done", fixed_description=True, description="My exact words #mine")
            jobs.request_render(job["id"], clip["id"], {"hashtags": "other"})
            self.assertEqual(posting._caption(clip, {"post_footer": ""}), "My exact words #mine")

    def test_caption_translation_uses_text_language_and_local_clip_override(self):
        words = [{"w": "English", "s": 1, "e": 2}]
        job = {"lang": "pt-BR", "transcript_lang": "en", "caption_language": "pt-BR"}
        clip = {"start": 0, "end": 5}
        with patch.object(brain, "portuguese_captions", return_value=words) as translate:
            jobs._caption_words(job, clip, words, 0)
            self.assertEqual(translate.call_args.args[1], "en")
            clip["words_language"] = "pt-BR"
            jobs._caption_words(job, clip, words, 0)
            self.assertEqual(translate.call_args.args[1], "pt-BR")
            self.assertEqual(translate.call_count, 2, "changing the source text language invalidates the caption cache")
        clip = {"start": 0, "end": 5}
        with patch.object(brain, "portuguese_captions", side_effect=RuntimeError("translation unavailable")):
            with self.assertRaises(RuntimeError):
                jobs._caption_words(job, clip, words, 0)

    def test_translated_borrowed_transcripts_never_choose_english_asr_for_portuguese_sound(self):
        class Borrowed(tuple):
            def __new__(cls, words, spoken):
                out = super().__new__(cls, (words, "en"))
                out.spoken_language, out.translated = spoken, True
                return out

        words = [{"w": "English", "s": i + 1, "e": i + 1.5} for i in range(8)]
        for source_language, ready, expected_model, translated in (("pt-BR", True, "pt", True), ("pt-BR", False, None, True),
                                                                   ("", True, None, True), ("pt-BR", True, "pt", False)):
            with self.subTest(spoken=source_language, ready=ready, translated=translated), ExitStack() as stack:
                stack.enter_context(ctx.as_user("f" * 32))
                stack.enter_context(patch.object(accounts, "settings", return_value={"caption_language": "pt-BR"}))
                job = jobs.create({"type": "url", "url": "https://youtube.com/watch?v=test", "name": "sample"}, {"lang": "en"}, enqueue=False)
                audio = Path(job["dir"], "borrow.mp3")
                audio.touch()
                local_words = [{"w": "Português", "s": i, "e": i + 0.5} for i in range(3)]
                clip = {"start": 1, "end": 10, "hook": "HOOK", "title": "Title", "description": "Description", "score": 80}
                for target, attribute, value in ((config, "YT_TRANSCRIPT", True), (config, "SKIP_STATIC", False)):
                    stack.enter_context(patch.object(target, attribute, value))
                for attribute, value in (("info", {"kind": "video", "duration": 30, "title": "sample", "uploader": "", "is_live": False}),
                                         ("download", audio), ("download_audio", audio), ("save_thumb", False),
                                         ("load_audio", np.zeros(30 * 16000, dtype=np.int16)),
                                         ("probe", {"width": 1920, "height": 1080, "duration": 30}),
                                         ("youtube_words", Borrowed(words, source_language) if translated else (words, source_language))):
                    stack.enter_context(patch.object(media, attribute, return_value=value))
                stack.enter_context(patch.object(media, "extract_audio"))
                stack.enter_context(patch.object(transcribe, "model_ready", return_value=True))
                stack.enter_context(patch.object(fetch_model, "ready", return_value=ready))
                stack.enter_context(patch.object(jobs, "_covers", return_value=True))
                stack.enter_context(patch.object(brain, "find_clips", return_value=([clip], {"model": "test"})))
                stack.enter_context(patch.object(creators, "log"))
                stack.enter_context(patch.object(notices, "add"))
                decoded = stack.enter_context(patch.object(transcribe, "transcribe", return_value=local_words))
                rendered = stack.enter_context(patch.object(jobs, "render_one"))
                jobs._run_job(job)
                self.assertEqual(job["lang"], source_language)
                self.assertEqual(job["transcript_lang"], "en" if translated else source_language)
                if expected_model:
                    self.assertEqual(decoded.call_args.kwargs["lang"], expected_model)
                    self.assertEqual(rendered.call_args.args[1]["words_language"], source_language)
                else:
                    decoded.assert_not_called()

    def test_portuguese_post_text_keeps_game_rules(self):
        answer = {"title": "Uma jogada incrível", "description": "A reação foi ótima", "hashtags": ["ação"]}
        with patch.object(brain, "ask_ai", return_value=([answer], "test", 0)) as ai:
            out = brain.post_text("uma bela jogada aconteceu agora", game="gta 6", caption_language="pt-BR")
        prompt = ai.call_args.args[0]
        self.assertIn("Brazilian Portuguese", prompt)
        self.assertIn("This stream is Grand Theft Auto VI", prompt)
        self.assertEqual(out["title"], answer["title"])

    def test_arabic_context_and_output_language_coexist(self):
        prompt = brain._clip_rules(15, 60, "مرحبا " * 30, "pt-BR")
        self.assertIn("Brazilian Portuguese", prompt)
        self.assertIn("English words", prompt)
        self.assertNotIn("{language}", prompt)

    def test_fixed_hook_and_description_survive_language_change_exactly(self):
        clip = {"start": 0, "end": 10, "hook": "Veja @canal #ad", "title": "Old title",
                "description": "Primeira linha\nhttps://example.com #campanha " + "x" * 400,
                "hashtags": ["campanha"], "highlight_words": [], "fixed_hook": True, "fixed_description": True}
        translated = {"hook": "CHANGED", "title": "Novo título", "description": "CHANGED",
                      "hashtags": ["changed"], "highlight_words": []}
        with patch.object(brain, "ask_ai", return_value=(translated, "test", 0)):
            out = brain.translate_clip_text(clip, "pt-BR")
        self.assertEqual(out["hook"], clip["hook"])
        self.assertEqual(out["description"], clip["description"])
        self.assertEqual(out["hashtags"], clip["hashtags"])
        self.assertEqual(out["title"], "Novo título")

    def test_translated_description_and_hashtag_list_stay_together_without_duplicates(self):
        clip = {"start": 0, "end": 10, "hook": "OLD", "title": "Old title", "description": "Old text #old",
                "hashtags": ["old"], "highlight_words": [], "hashtags_in_description": True}
        answer = {"hook": "AÇÃO", "title": "Nova jogada", "description": "Uma jogada incrível #old #ação",
                  "hashtags": ["ação", "AÇÃO", "vitória"], "highlight_words": []}
        with patch.object(brain, "ask_ai", return_value=(answer, "test", 0)):
            out = brain.translate_clip_text(clip, "pt-BR")
        self.assertEqual(out["hashtags"], ["ação", "vitória"])
        self.assertEqual(out["description"], "Uma jogada incrível\n\n#ação #vitória")

    def test_spoken_words_follow_selected_source_segments_in_playback_order(self):
        with tempfile.TemporaryDirectory() as d:
            words = [{"w": "first", "s": 1, "e": 1.5}, {"w": "DISCARDED", "s": 8, "e": 8.5},
                     {"w": "later", "s": 20, "e": 20.5}, {"w": "second", "s": 2, "e": 2.5}]
            Path(d, "words.json").write_text(json.dumps(words), encoding="utf-8")
            clip = {"start": 0, "end": 22, "segments": [[20, 22], [0, 4], [20, 22]]}
            self.assertEqual(jobs.spoken_in({"dir": d}, clip), "later first second later")

    def test_portuguese_hashtags_keep_accents_and_skip_common_words(self):
        text = "você você então então também também porque porque não não ação ação vitória vitória"
        tags = hashtags.make(text, "Ação e vitória", "João Oficial")
        self.assertIn("ação", tags)
        self.assertIn("vitória", tags)
        self.assertIn("joão", tags)
        self.assertFalse(set(tags) & {"você", "então", "também", "porque", "não"})
        self.assertEqual(len(tags), len(set(tags)))

    def test_hashtags_use_selected_segments(self):
        clip = {"start": 0, "end": 25, "segments": [[20, 25], [0, 3]], "description": "A moment"}
        words = [{"w": "discarded", "s": i, "e": i + 0.5} for i in range(5, 15)]
        words += [{"w": "vitória", "s": i, "e": i + 0.5} for i in (1, 2, 21)]
        hashtags.apply(clip, words)
        self.assertNotIn("discarded", clip["hashtags"])
        self.assertIn("vitória", clip["hashtags"])

    def test_job_snapshots_translation_preference_and_source_language(self):
        with ctx.as_user("a" * 32), patch.object(accounts, "settings", return_value={"caption_language": "pt-BR", "youtube_auto_translate": False}):
            job = jobs.create({"type": "file", "name": "sample"}, {"lang": "en"}, enqueue=False)
        self.assertIs(job["youtube_auto_translate"], False)
        self.assertEqual(job["options"]["lang"], "en")
        self.assertEqual(job["caption_language"], "pt-BR")
        saved = json.loads(Path(job["dir"], "job.json").read_text(encoding="utf-8"))
        self.assertIs(saved["youtube_auto_translate"], False)

    def test_original_only_english_cache_does_not_reuse_automatic_translation(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "words_en.json").write_text(json.dumps([{"w": "AUTOMATIC", "s": 1, "e": 2}]), encoding="utf-8")
            job = {"dir": d, "source": {"type": "url", "url": "https://youtube.com/watch?v=test"}, "youtube_auto_translate": False}
            with patch.object(media, "youtube_english_words", return_value=[{"w": "HUMAN", "s": 1, "e": 2}]) as fetch:
                self.assertEqual(jobs._english_part(job, {"start": 0, "end": 5})[0]["w"], "HUMAN")
                jobs._english_part(job, {"start": 0, "end": 5})
            fetch.assert_called_once_with(job["source"]["url"], allow_translation=False)

    def test_portuguese_default_and_explicit_source_language_are_independent(self):
        with ctx.as_user("b" * 32), patch.object(accounts, "settings", return_value={"caption_language": "pt-BR", "youtube_auto_translate": True}):
            job = jobs.create({"type": "file", "name": "sample"}, {}, enqueue=False)
        self.assertEqual(job["options"]["lang"], "pt")
        self.assertEqual(jobs.speech_language({"caption_language": "pt-BR", "options": {}}), "pt")
        self.assertEqual(jobs.speech_language({"caption_language": "pt-BR", "options": {"lang": "en"}}), "en")
        self.assertEqual(jobs.speech_language({"caption_language": "en", "options": {"lang": "pt"}}), "pt")
        self.assertEqual(jobs.speech_language({"caption_language": "en", "lang": "pt-BR", "options": {"lang": "en"}}), "pt")
        self.assertEqual(jobs.speech_language({"caption_language": "en", "lang": "fr", "options": {"lang": "pt"}}), "pt")

    def test_old_and_new_creators_default_to_portuguese_without_overwriting_explicit_english(self):
        with tempfile.TemporaryDirectory() as d:
            for cid, extra in (("legacy", {}), ("english", {"lang": "en"})):
                folder = Path(d, cid)
                folder.mkdir()
                Path(folder, "creator.json").write_text(json.dumps({"id": cid, "name": cid, **extra}), encoding="utf-8")
            with ctx.as_user("c" * 32), patch.object(creators, "_root", return_value=Path(d)), patch.object(creators, "_mem", {}), \
                    patch.object(accounts, "settings", return_value={"caption_language": "pt-BR"}):
                self.assertEqual(creators.get("legacy")["lang"], "pt")
                self.assertEqual(creators.get("english")["lang"], "en")
                self.assertEqual(creators.create({"name": "New Portuguese"})["lang"], "pt")
                self.assertEqual(creators.create({"name": "New English", "lang": "en"})["lang"], "en")

    def test_live_portuguese_can_start_before_first_model_download(self):
        creator = {**creators.DEFAULTS, "id": "portuguese-live", "name": "Portuguese", "lang": "pt", "channel": "https://twitch.tv/test"}
        with ctx.as_user("d" * 32), patch.object(accounts, "settings", return_value={"caption_language": "en"}), \
                patch.object(creators, "get", return_value=creator), patch.object(tools, "missing", return_value=[]), \
                patch.object(fetch_model, "ready", return_value=False), patch.object(live.Session, "start"):
            job = live.start_session(creator)
        self.assertEqual(job["lang"], "pt")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        db.engine.dispose()
        _data.cleanup()
