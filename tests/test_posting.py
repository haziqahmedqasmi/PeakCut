"""Posting: uploads run side by side but never two to one account, a refusal is not tried again, anything else is tried
again later and later, and a platform's own "wait" is respected. Runs against the code directly, with its own empty
data folder and the platforms replaced: nothing outside it is touched."""
import os
import shutil
import sys
import tempfile
import threading
import time

DATA = tempfile.mkdtemp(prefix="pc-posting-")
os.environ.update(PEAKCUT_DATA=DATA, DATABASE_URL="", PEAKCUT_POST_WORKERS="3", PEAKCUT_POST_TRIES="4")
sys.path.insert(0, ".")
import json  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from studio import accounts, alerts, auth, config, creators, ctx, db, jobs, posting  # noqa: E402
db.init()

fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


UID = "usertest01"

# ---- what a platform's answer means
class R:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text, self.headers = code, text, headers or {}


def failure(code, text="", headers=None):
    return accounts.http_failure(code, text, "Uploading", R(code, text, headers))


check("a bad request is a refusal: never tried again", failure(400, "bad").permanent, True)
check("...so is access taken away", (failure(401).permanent, failure(403, "forbidden").permanent), (True, True))
check("...and a video that does not exist", failure(404).permanent, True)
check("a busy platform is tried again", (failure(500).permanent, failure(503).permanent, failure(408).permanent), (False, False, False))
check("a limit is tried again, after the wait the platform asked for", (failure(429, "slow down", {"Retry-After": "90"}).permanent, failure(429, "x", {"Retry-After": "90"}).wait), (False, 90.0))
check("a daily quota (403) is tried again later, not given up on", (failure(403, "quotaExceeded").permanent, failure(403, "quotaExceeded").wait), (False, accounts.QUOTA_WAIT))
check("the message still says what failed and why", str(failure(400, "bad video")), "Uploading failed (400): bad video")
check("it is still a RuntimeError, so older code that catches those works", isinstance(failure(400), RuntimeError), True)

# ---- how long to wait
config.POST_BACKOFF = (120, 600, 2700, 10800)
delays = [posting.retry_delay(n) for n in (1, 2, 3, 4, 9)]
check("each failure waits longer than the last", delays[:4], lambda d: all(a < b for a, b in zip(d, d[1:])))
check("the first wait is about 2 minutes, with some give either way", delays[0], lambda x: 96 <= x <= 144)
check("after the list runs out, the last wait is kept", delays[4], lambda x: 8640 <= x <= 12960)
check("the platform's own wait wins when it is longer", posting.retry_delay(1, 7200), 7200)
check("...and is not used when it is shorter", posting.retry_delay(1, 5), lambda x: x >= 96)

# ---- a failed post: refused or tried again
job = {"id": "j1", "creator": None, "dir": DATA, "clips": [{"id": "c1", "status": "done", "file": "c1.mp4", "title": "T",
                                                          "description": "D", "hashtags": ["a"], "hook": "H"}]}
acc = {"id": "a1", "platform": "youtube", "name": "Chan"}
logs, alerted = [], []
jobs.get = lambda jid: job
jobs.save = lambda j: None
accounts.get = lambda aid: acc
creators.get = lambda cid: None
creators.log = lambda cid, text: logs.append(text)
alerts.notify = lambda text, kind="": alerted.append(text)
_real_save = posting._save
posting._save = lambda: None
config.POST_TRIES = 4
outcome = {"error": None}


def upload(*a, **k):
    if outcome["error"]:
        raise outcome["error"]
    return "rid", "https://example/watch"


accounts.youtube_upload = upload


def new_post():
    return {"id": os.urandom(4).hex(), "user": UID, "job": "j1", "clip": "c1", "creator": None, "account": "a1", "account_name": "Chan",
            "platform": "youtube", "status": "posting", "due_at": 0, "title": "T", "tries": 0, "error": None}


with ctx.as_user(UID):
    p = new_post()
    outcome["error"] = None
    posting._post(p)
    check("a post that works is posted", (p["status"], p["url"]), ("posted", "https://example/watch"))

    p = new_post()
    outcome["error"] = accounts.PostError("Uploading failed (400): rejected", permanent=True)
    posting._post(p)
    check("a refused post fails at once, and the person is told", (p["status"], p["tries"], len(alerted)), ("failed", 1, 1))

    p = new_post()
    outcome["error"] = ConnectionError("connection reset")
    t0 = time.time()
    posting._post(p)
    check("a dropped connection is tried again, a couple of minutes later", (p["status"], p["tries"]), ("scheduled", 1))
    check("...with a wait of about 2 minutes", p["due_at"] - t0, lambda x: 90 <= x <= 150)
    posting._post(p)
    check("...and longer the second time", (p["tries"], p["due_at"] - time.time()), lambda g: g[0] == 2 and 480 <= g[1] <= 730)
    posting._post(p)
    posting._post(p)
    check("...until it has had its tries, then it fails", (p["tries"], p["status"]), (4, "failed"))

    p = new_post()
    outcome["error"] = accounts.PostError("Uploading failed (429): slow", wait=7200)
    posting._post(p)
    check("a platform that says wait two hours is given two hours", p["due_at"] - time.time(), lambda x: 7100 <= x <= 7300)

    p = new_post()
    acc_backup, accounts.get = accounts.get, lambda aid: None
    posting._post(p)
    accounts.get = acc_backup
    check("an account that was disconnected fails at once", (p["status"], p["tries"]), ("failed", 1))

# ---- uploads side by side, one account at a time
posting._post = None
runs, lock = [], threading.Lock()


def slow_post(p):
    with lock:
        runs.append(("start", p["id"], time.time()))
    time.sleep(0.5)
    with lock:
        runs.append(("end", p["id"], time.time()))
    p["status"] = "posted"


posting._post = slow_post
posting._posts.clear()
mk = lambda pid, user, account, due=0: {"id": pid, "user": user, "account": account, "status": "scheduled", "due_at": due}  # noqa: E731
posting._posts.extend([mk("p1", UID, "A", 1), mk("p2", UID, "B", 2), mk("p3", UID, "A", 3), mk("p4", UID, "C", 4), mk("p5", UID, "D", 5)])
started = posting._dispatch()
check("with 3 workers, 3 uploads start at once and the rest wait", started, 3)
check("...never two for the same account (the second clip of A waits)", sorted(r[1] for r in runs if r[0] == "start"), ["p1", "p2", "p4"])
check("a post still waiting is still scheduled", [p["status"] for p in posting._posts if p["id"] in ("p3", "p5")], ["scheduled", "scheduled"])
for _ in range(100):
    if len(posting._busy) == 0:
        break
    time.sleep(0.05)
check("the three uploads ran side by side, not one after another", max(r[2] for r in runs if r[0] == "start") < min(r[2] for r in runs if r[0] == "end"), True)
check("an upload that finishes wakes the loop straight away", posting._wake.is_set(), True)
posting._wake.clear()
started = posting._dispatch()
for _ in range(100):
    if len(posting._busy) == 0:
        break
    time.sleep(0.05)
check("then the waiting ones go", (started, sorted(p["status"] for p in posting._posts)), (2, ["posted"] * 5))
when = {(r[0], r[1]): r[2] for r in runs}
check("the second clip for account A only started after the first had finished", when[("start", "p3")] >= when[("end", "p1")], True)
check("nothing is left marked as busy", posting._busy, set())

# ---- a slow upload does not hold up another person's
runs.clear()
posting._posts[:] = [mk("q1", UID, "A", 1), mk("q2", "usertest02", "A", 2)]
posting._dispatch()
for _ in range(100):
    if len(posting._busy) == 0:
        break
    time.sleep(0.05)
check("the same account id for two different people is two different accounts", sorted(r[1] for r in runs if r[0] == "start"), ["q1", "q2"])

# ---- paused
with ctx.as_user(UID):
    accounts.save_settings({"paused": True})
posting._posts[:] = [mk("r1", UID, "A", 1)]
check("while posting is paused, nothing starts", (posting._dispatch(), posting._posts[0]["status"]), (0, "scheduled"))
with ctx.as_user(UID):
    accounts.save_settings({"paused": False})

# ---- posts live in the database
posting._save = _real_save
def stored():
    with db.engine.connect() as c:
        return {r.id: json.loads(r.data) for r in c.execute(sa.select(db.posts))}


posting._posts[:] = []
posting._snap.clear()
with db.engine.begin() as c:
    c.execute(db.posts.delete())
posting._posts.extend([mk("s1", UID, "A", 1), mk("s2", UID, "A", 1)])
posting._save()
check("posts are saved to the database", sorted(stored()), ["s1", "s2"])
posting._posts[0]["status"] = "posted"
posting._save()
check("a change is saved", stored()["s1"]["status"], "posted")
posting._posts[:] = [p for p in posting._posts if p["id"] != "s2"]
posting._save()
check("a removed post is removed from the database", sorted(stored()), ["s1"])
posting._posts[:] = []
posting._load()
check("they come back after a restart", [p["id"] for p in posting._posts], ["s1"])

# one process takes a due post; a second one that also saw it as due must not
posting._posts[:] = [mk("c1", UID, "A", 1)]
posting._save()
check("a due post can be taken once", (posting._claim(dict(posting._posts[0])), posting._claim(dict(posting._posts[0]))), (True, False))

# posts.json from an older version is moved in once
posting._posts[:] = []
posting._snap.clear()
with db.engine.begin() as c:
    c.execute(db.posts.delete())
posting._FILE.write_text(json.dumps([mk("old1", UID, "A", 1)]), encoding="utf-8")
posting._load()
check("posts from an older version are moved in", (sorted(stored()), posting._FILE.exists()), (["old1"], False))

# ---- sign-in limits are counted in the database
for _ in range(3):
    auth.throttle("login-ip:test-shared", 3, 600)
try:
    auth.throttle("login-ip:test-shared", 3, 600)
    blocked = False
except auth.AuthError:
    blocked = True
auth._attempts.clear()      # the in-memory counts are gone, as after a restart
try:
    auth.throttle("login-ip:test-shared", 3, 600)
    still = False
except auth.AuthError:
    still = True
check("too many attempts are refused", blocked, True)
check("...and still refused after a restart", still, True)

# ---- does the score predict views
good = [{"status": "posted", "score": 50 + i * 3, "views": 100 + i * 80} for i in range(15)]
sc = posting.score_check(good)
check("a score that predicts views is recognised", (sc["agree"], sc["n"]), (1.0, 15))
check("...and the point from which clips pay is suggested", isinstance(sc["suggested"], int) and sc["suggested"] >= 50, True)
noise = [{"status": "posted", "score": 50 + i * 3, "views": 1000 - i * 50} for i in range(15)]
check("a score that predicts nothing gets no advice", (posting.score_check(noise)["agree"], posting.score_check(noise)["suggested"]), (0.0, None))
check("too few posts: no verdict", posting.score_check(good[:5]), {"n": 5, "agree": None, "suggested": None})

print("ALL PASSED" if not fails else f"{len(fails)} failed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
