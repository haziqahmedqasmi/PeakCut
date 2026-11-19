"""Check caption preferences and the subtitle events sent to the encoder."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, ".")
WORDS = [{"w": "spoken", "s": 1.0, "e": 1.5}]
FIXED = [{"w": "polished", "s": 1.0, "e": 1.5}]


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS {name}")


def has_dialogue(ass, style, text=""):
    return any(line.startswith("Dialogue: ") and f",{style}," in line and text in line
               for line in ass.splitlines())


def rejects_caption_value(call):
    try:
        call()
    except HTTPException as error:
        return error.status_code == 400 and "captions" in str(error.detail).lower()
    return False


def main(data):
    os.environ.update(PEAKCUT_DATA=data, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
    from studio import auth, brain, config, creators, ctx, db, jobs, render, roman, server, transcribe

    db.init()
    try:
        user = auth.local_user().id
        source = {"type": "file", "name": "Caption test"}
        clip = {"id": "clip1", "start": 0.0, "end": 4.0, "status": "done", "hook": "KEEP THE HOOK",
                "title": "A clip", "description": "", "hashtags": [], "score": 95}

        with ctx.as_user(user):
            creator = creators.create({"name": "Caption test creator", "captions": False})
            inherited = jobs.create(source, {}, enqueue=False, creator=creator["id"])
            live_job = jobs.create(source, {}, enqueue=False, creator=creator["id"], kind="live")
            override = jobs.create(source, {"captions": True}, enqueue=False, creator=creator["id"])
            check("videos and live jobs inherit the creator's caption preference",
                  inherited["options"]["captions"] is False and live_job["options"]["captions"] is False)
            check("a job can override its creator's caption preference", override["options"]["captions"] is True)

            job = jobs.create(source, {}, enqueue=False)
            check("the default is to show captions", job["options"]["captions"] is True)
            job["options"].pop("captions")
            job.update(status="done", source_path=str(Path(data) / "source.mp4"), width=1920, height=1080,
                       duration=4.0, clips=[clip])
            jobs.save(job)

        with (ctx.as_user(user), patch.object(render, "_render_in", return_value={"file": "clip1.mp4"}) as encode,
              patch.object(brain, "english_captions", return_value=FIXED) as prepare,
              patch.object(roman, "words", wraps=roman.words) as romanize,
              patch.object(jobs._q, "put") as queue):
            jobs.render_one(job, clip, WORDS)
            ass = encode.call_args.args[-1]
            check("an older saved clip still renders spoken captions",
                  clip["status"] == "done" and has_dialogue(ass, "Cap", "POLISHED"))
            check("caption preparation is cached for the clip", prepare.call_count == 1 and clip["fixed_words"] == FIXED)

            for value in ("false", 0, None):
                check(f"the clip editor and creator settings reject {value!r} as a caption preference",
                      rejects_caption_value(lambda: server.rerender(job["id"], clip["id"], {"captions": value}))
                      and rejects_caption_value(lambda: server.new_creator({"name": "Invalid", "captions": value})))
            check("invalid caption changes do not queue a render", not queue.called)

            prepare.reset_mock()
            romanize.reset_mock()
            response = server.rerender(job["id"], clip["id"], {"captions": False})
            check("the clip editor can turn captions off", response["clips"][0]["captions"] is False)
            check("turning captions off queues the clip for rendering", queue.call_args.args[0] == ("clip", job["id"], clip["id"]))
            jobs.render_one(job, clip, WORDS)
            ass = encode.call_args.args[-1]
            check("turning off captions keeps the hook and skips caption processing",
                  clip["status"] == "done" and not has_dialogue(ass, "Cap") and has_dialogue(ass, "Hook", "KEEP THE HOOK")
                  and not prepare.called and not romanize.called)
            saved = json.loads((Path(job["dir"]) / "job.json").read_text(encoding="utf-8"))["clips"][0]
            check("the saved clip retains both the preference and the cached words",
                  saved["captions"] is False and saved["fixed_words"] == FIXED)

            response = server.rerender(job["id"], clip["id"], {"captions": True})
            check("the clip editor can turn captions back on", response["clips"][0]["captions"] is True)
            jobs.render_one(job, clip, WORDS)
            check("turning captions back on uses the cached words",
                  clip["status"] == "done" and has_dialogue(encode.call_args.args[-1], "Cap", "POLISHED")
                  and not prepare.called)

            render.render_clip(Path(data) / "source.mp4", Path(data) / "clips", "direct",
                               dict(clip, captions=False), WORDS, 1920, 1080)
            ass = encode.call_args.args[-1]
            check("the renderer honors captions off even when it receives spoken words",
                  not has_dialogue(ass, "Cap") and has_dialogue(ass, "Hook", "KEEP THE HOOK"))

            check("the default caption language is English", server.get_settings()["caption_language"] == "en")
            check("an unknown caption language is rejected",
                  rejects_caption_value(lambda: server.put_settings({"caption_language": "unknown"})))
            check("Brazilian Portuguese is saved for this owner",
                  server.put_settings({"caption_language": "pt-BR"})["caption_language"] == "pt-BR")
            pt_job = jobs.create(source, {}, enqueue=False)
            pt_live = jobs.create(source, {}, enqueue=False, creator=creator["id"], kind="live")
            check("videos and live recordings save the chosen caption language",
                  pt_job["caption_language"] == pt_live["caption_language"] == "pt-BR")
            with ctx.as_user("abcdef0123456789"):
                check("caption language settings belong to the account", server.get_settings()["caption_language"] == "en")

            job["lang"] = "en"
            response = server.rerender(job["id"], clip["id"], {})
            check("remaking an older clip takes the current caption language",
                  response["clips"][0].get("caption_language") == "pt-BR")
            with (patch.object(brain, "portuguese_captions",
                               return_value=[{"w": "Português", "s": 1.0, "e": 1.5}]) as portuguese,
                  patch.object(brain, "translate_caption_words",
                               return_value=[{"w": "FIQUE LIGADO", "s": 0, "e": 1}]) as translate_hook,
                  patch.object(config, "ai_ready", return_value=True)):
                jobs.render_one(job, clip, WORDS)
                check("remaking a clip replaces its English cache with Portuguese",
                      portuguese.called and clip["fixed_for"][-1] == "pt-BR"
                      and has_dialogue(encode.call_args.args[-1], "Cap", "PORTUGUÊS"))
                check("the opening text follows the caption language",
                      translate_hook.called and has_dialogue(encode.call_args.args[-1], "Hook", "FIQUE LIGADO"))
                jobs.render_one(job, clip, WORDS)
                check("the translated opening text is cached", translate_hook.call_count == 1)
            with patch.object(config, "ai_ready", return_value=False):
                check("Portuguese clips have a Portuguese opening text without AI",
                      jobs._caption_hook(pt_job, {"hook": "WAIT FOR IT"}) == "ESPERA SÓ")
            with patch.object(brain, "portuguese_captions", side_effect=RuntimeError("Translation unavailable")):
                clip.pop("fixed_for")
                jobs.render_one(job, clip, WORDS)
                check("a failed Portuguese translation reports an error",
                      clip["status"] == "error" and clip["error"] == "Translation unavailable")

            pt_job.update(source_path=job["source_path"], width=1920, height=1080, duration=4.0, lang="pt-BR")
            pt_clip = dict(clip, fixed_for=[0.0, 4.0, "en2"], fixed_words=FIXED)
            pt_job["clips"] = [pt_clip]
            render_words = [{"w": "Português", "s": 1.0, "e": 1.5}]
            jobs.render_one(pt_job, pt_clip, render_words)
            check("Brazilian Portuguese reaches the on-screen captions even with an old English cache",
                  pt_clip["status"] == "done" and has_dialogue(encode.call_args.args[-1], "Cap", "PORTUGUÊS"))

            with patch.object(brain, "ask_ai", return_value=(["Olá, Brasil!"], "test", 0)) as ask:
                translated = brain.portuguese_captions(WORDS, "en")
                check("translation requests Brazilian Portuguese and keeps the original timing",
                      "Brazilian Portuguese" in ask.call_args.args[0] and [w["w"] for w in translated] == ["Olá,", "Brasil!"]
                      and translated[0]["s"] == WORDS[0]["s"] and translated[-1]["e"] == WORDS[0]["e"])
            with (patch.object(transcribe, "_decode_multilingual",
                               return_value=[(["▁Olá", "▁Brasil"], [0.1, 0.5])]) as decode,
                  patch.object(transcribe, "_take_now", side_effect=AssertionError("English model used")),
                  patch.object(transcribe, "_decode_remote", side_effect=AssertionError("English worker used"))):
                spoken = transcribe.transcribe(transcribe.np.full(16000, 1000, dtype=transcribe.np.int16),
                                                multilingual=True)
                check("Portuguese speech uses the multilingual model and keeps accents and timestamps",
                      decode.call_count == 1 and [w["w"] for w in spoken] == ["Olá", "Brasil"]
                      and spoken[0]["s"] == 0.1 and spoken[1]["s"] == 0.5)
    finally:
        db.engine.dispose()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="pc-captions-") as data:
        main(data)
