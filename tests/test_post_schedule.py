"""Small, isolated checks for changing a post's scheduled time."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
with tempfile.TemporaryDirectory(prefix="peakcut-post-schedule-") as data:
    os.environ["PEAKCUT_DATA"] = data
    os.environ["PEAKCUT_FETCH_MODEL"] = "0"
    from studio import ctx, posting

    posting._posts[:] = []
    posting._mirror = lambda post: None
    now = time.time()
    base = {"user": "owner", "job": "job", "clip": "clip", "creator": "creator",
            "account": "account", "account_name": "Account", "platform": "youtube",
            "status": "scheduled", "title": "Title", "views": None}
    a = dict(base, id="a", due_at=now + 3600)
    b = dict(base, id="b", due_at=now + 7200)
    posting._posts.extend((a, b))

    def refused(pid, due, expected):
        try:
            posting.reschedule(pid, due, expected)
        except ValueError:
            return
        raise AssertionError("The change should have been refused")

    with ctx.as_user("owner"):
        posting.reschedule("b", now + 1800, b["due_at"])
        assert b["due_at"] == now + 1800
        assert {k: b[k] for k in base} == base
        assert ctx.version() > 0
        refused("b", now - 1, b["due_at"])
        refused("b", now + 9000, now + 7200)
        posting.creators.all_creators = lambda: []
        posting.jobs.list_jobs = lambda: []
        posting.accounts.settings = lambda: {"paused": False}
        posting.alerts.configured = lambda: False
        posting.live.active_mine = lambda: []
        assert [p["id"] for p in posting.overview()["posts"]] == ["b", "a"]
        a["due_at"] = now - 1
        refused("a", now + 9000, a["due_at"])
        a["status"] = "posting"
        refused("a", now + 9000, a["due_at"])
        a["status"] = "posted"
        a["posted_at"] = now
        refused("a", now + 9000, a["due_at"])
        assert [p["id"] for p in posting.overview()["posts"]] == ["a", "b"]
    with ctx.as_user("another"):
        refused("b", now + 9000, b["due_at"])
    saved = json.loads((Path(data) / "posts.json").read_text(encoding="utf-8"))
    assert next(p for p in saved if p["id"] == "b")["due_at"] == now + 1800
    print("PASS post schedule changes, sorting, persistence, and access checks")
