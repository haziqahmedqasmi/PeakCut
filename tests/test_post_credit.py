"""Required attribution is checked at upload time; no platform is contacted."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")


def check(name, condition):
    assert condition, name
    print("PASS " + name)


def rejects(call):
    from studio.post_text import CreditError
    try:
        call()
    except CreditError:
        return True
    return False


with tempfile.TemporaryDirectory(prefix="pc-post-credit-") as temp:
    os.environ.update(PEAKCUT_DATA=temp, DATABASE_URL="", PEAKCUT_ACCOUNTS="0",
                      PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0")
    from studio import accounts, auth, creators, ctx, db, jobs, post_text, posting

    credit = {"require_credit": True, "credit_handle": "@OriginalCreator", "credit_tiktok_handle": "@TikTokCreator", "credit_hashtag": "#OriginalCreator",
              "post_footer": "Clipped with the creator's required credit.", "game": "GTA 6"}
    clip = {"id": "clip0001", "title": "A game moment", "description": "A highlight.",
            "hashtags": ["#Gaming", "gaming", "GTA6", "bad tag", ""], "score": 99,
            "status": "done", "file": "clip0001.mp4"}
    for platform, limit in (("youtube", 4900), ("instagram", 2200), ("tiktok", 2200)):
        handle_key = "credit_tiktok_handle" if platform == "tiktok" else "credit_handle"
        text, tags = post_text.compose(dict(clip, description="x" * 9000), credit, platform)
        check(f"{platform}: long descriptions keep creator mention, creator hashtag and GTA VI tags",
              len(text) <= limit and all(t in text for t in
              (credit[handle_key], "#OriginalCreator", "#GTA6", "#GTAVI", "#GrandTheftAutoVI", "#RockstarGames")))
        check(f"{platform}: hashtags use one # and do not repeat",
              "##" not in text and tags.count("Gaming") == 1 and tags.count("GTA6") == 1)
        for missing in (handle_key, "credit_hashtag"):
            check(f"{platform}: missing {missing} blocks posting",
                  rejects(lambda: post_text.compose(clip, dict(credit, **{missing: ""}), platform)))
    for field, value in (("credit_handle", "@creator extra"), ("credit_hashtag", "#creator-name")):
        check(f"invalid {field} is rejected", rejects(lambda: post_text.compose(clip, dict(credit, **{field: value}), "youtube")))
    check("an overlong required credit footer blocks rather than losing attribution",
          rejects(lambda: post_text.compose(clip, dict(credit, post_footer="x" * 6000), "youtube")))
    text, _ = post_text.compose(dict(clip, hashtags=[]), dict(credit, game="Fortnite"), "youtube")
    check("GTA VI hashtags are not added to another game's clips", "#GTA6" not in text and "#Fortnite" in text)
    text, _ = post_text.compose(dict(clip, hashtags=[]), dict(credit, game=""), "youtube", "GTA 6")
    check("the job's detected game also supplies GTA VI tags", "#GTAVI" in text)
    text, _ = post_text.compose(clip, dict(credit, post_hashtags="#campaign, #Campaign #required"), "youtube")
    check("extra campaign hashtags are required and deduplicated", text.count("#campaign") == 1 and "#required" in text)
    text, _ = post_text.compose(clip, credit, "tiktok")
    check("TikTok uses its own creator handle", "@TikTokCreator" in text and "@OriginalCreator" not in text)
    check("too many required tags block instead of dropping required tags",
          rejects(lambda: post_text.compose(clip, dict(credit, post_hashtags=" ".join("#tag" + str(i) for i in range(20))), "youtube")))

    own, _ = post_text.compose(dict(clip, description="My custom description", fixed_description=True), {}, "youtube")
    check("existing creators without a credit policy retain their custom description", own == "My custom description")
    db.init()
    with ctx.as_user(auth.local_user().id):
        c = creators.create(dict(credit, name="Credit checks", autopost=True, accounts=["yt-test"]))
        check("credit settings are stored by the creator API cleaner",
              creators.get(c["id"])["credit_handle"] == "@OriginalCreator")
        c = creators.update(c["id"], {"credit_handle": "OriginalCreator", "credit_hashtag": "OriginalCreator",
                                      "post_hashtags": "#campaign, Campaign"})
        check("settings normalize optional prefixes and duplicate hashtags",
              c["credit_handle"] == "@OriginalCreator" and c["credit_hashtag"] == "#OriginalCreator"
              and c["post_hashtags"] == "#campaign")
        job = jobs.create({"type": "file", "name": "GTA 6 sample"}, {}, enqueue=False, creator=c["id"])
        job["clips"] = [dict(clip)]
        jobs.save(job)
        acc = {"id": "yt-test", "name": "Test channel", "platform": "youtube"}
        with patch.object(accounts, "get", return_value=acc), patch.object(accounts, "youtube_upload", return_value=("test-id", "https://example.test/clip")) as upload, patch.object(posting.alerts, "notify"):
            posting.on_clip_ready(job, job["clips"][0])
            queued = posting._mine()[-1]
            check("automatic clips use the posting queue", queued["status"] == "scheduled")
            creators.update(c["id"], {"credit_handle": "", "credit_hashtag": ""})
            posting._post(queued)
            check("even previously queued clips cannot upload without current credit settings",
                  not upload.called and queued["status"] == "failed" and queued["tries"] == 1
                  and "Posting blocked" in queued["error"])
            creators.update(c["id"], {"credit_handle": "@CorrectCreator", "credit_hashtag": "#CorrectCreator"})
            posting.retry(queued["id"])
            posting._post(queued)
            args = upload.call_args.args
            check("fixing credit and retrying uses the current creator in the actual YouTube payload",
                  queued["status"] == "posted" and "@CorrectCreator" in args[3] and "#CorrectCreator" in args[3]
                  and "#GTAVI" in args[3] and "GTAVI" in args[4] and args[5] == "public")
        for platform in ("instagram", "tiktok"):
            acc = {"id": platform + "-test", "name": "Test account", "platform": platform}
            with patch.object(accounts, "get", return_value=acc), patch.object(accounts, platform + "_upload", return_value=("test-id", "https://example.test/clip")) as upload:
                queued = posting.enqueue(job, job["clips"][0], [acc["id"]], now=True)[0]
                posting._post(queued)
                text = upload.call_args.args[2]
                mention = "@TikTokCreator" if platform == "tiktok" else "@CorrectCreator"
                check(f"{platform}: the actual upload gets the protected credit and tags",
                      queued["status"] == "posted" and mention in text and "#CorrectCreator" in text and "#GTA6" in text)
    check("all upload tests used isolated data", str(posting._FILE).startswith(temp))
