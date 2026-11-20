"""A free, rotating AI service: no model has to be chosen, and "go slowly" makes exactly the number of clips asked
for, one small request each, with a rest between them. The network is replaced; nothing leaves this machine."""
import json
import os
import re
import sys
import tempfile
import time
import types

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="pc-slow-")
os.environ["DATABASE_URL"] = ""
os.environ.update(PEAKCUT_AI_PROVIDER="", PEAKCUT_AI_MODELS="", PEAKCUT_AI_KEY="", PEAKCUT_AI_BASE_URL="")
sys.path.insert(0, ".")
import numpy as np  # noqa: E402

from studio import ai, brain, config, db  # noqa: E402

db.init()
fails = []


def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra and not ok else ""))
    ok or fails.append(name)


class Reply:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


LISTED = ["gemini-flash", "free-router", "zzz"]
asked = []         # (model, prompt) of every chat request
mode = {"fail": False}


def fake_get(url, headers=None, timeout=None, **_k):
    return Reply(200, {"data": [{"id": m} for m in LISTED]})


def fake_post(url, json=None, headers=None, timeout=None, **_k):  # noqa: A002
    prompt = json["messages"][0]["content"]
    asked.append((json.get("model"), prompt))
    if mode["fail"]:
        return Reply(500, {"error": "everything is busy"})
    if "<transcript>" not in prompt:                  # the settings page's little test
        return Reply(200, {"choices": [{"message": {"content": "[\"ok\"]"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    text = prompt.split("<transcript>")[1]
    times = [float(x) for x in re.findall(r"^\[(\d+(?:\.\d+)?)\]", text, re.M)]
    taken = [(float(a), float(b)) for a, b in re.findall(r"(\d+)s to (\d+)s", prompt)]
    start = next(t for t in times if all(not (t < b + 5 and t + 30 > a - 5) for a, b in taken))
    clip = {"start": start, "end": start + 30, "score": 70, "hook": "WOW", "title": "t", "description": "d", "hashtags": ["a"], "reason": "r"}
    return Reply(200, {"choices": [{"message": {"content": "{\"items\": [" + __import__("json").dumps(clip) + "]}"}}],
                       "usage": {"prompt_tokens": 50, "completion_tokens": 10}})


ai.httpx.get, ai.httpx.post = fake_get, fake_post
brain.time = types.SimpleNamespace(sleep=lambda s: None, time=time.time)      # no real resting inside the test

# ---- a custom service with no model chosen
out = ai.save("custom", "", "http://127.0.0.1:3001/v1", "", slow=True, wait=0)
check("a custom service can be saved without a model", out["ready"] and out["models"] == [] and out["model"] == "automatic", str(out))
check("slow mode and the wait are kept", (out["slow"], out["wait"]) == (True, 0), str(out))
check("the service's own automatic entry is used", ai.use_models() == ["free-router"], str(ai.use_models()))
ai._auto.update({"for": None, "at": 0.0})
LISTED[:] = ["alpha", "beta"]
check("without one, the first listed model is used", ai.use_models() == ["alpha"], str(ai.use_models()))
try:
    ai.save("openai", "", "", "sk-x1234567890")
    check("a service that is not custom still needs a model", False)
except ValueError:
    check("a service that is not custom still needs a model", True)
ai._auto.update({"for": None, "at": 0.0})
LISTED[:] = ["gemini-flash", "free-router", "auto", "zzz"]
check("auto is preferred over free-router when both are listed", ai.use_models() == ["auto"], str(ai.use_models()))
ai._auto.update({"for": None, "at": 0.0})
LISTED[:] = ["gemini-flash", "free-router", "zzz"]
check("free-router is used when there is no auto", ai.use_models() == ["free-router"], str(ai.use_models()))
ai._auto.update({"for": None, "at": 0.0})

t = ai.test()
check("the test works with no model chosen", t["ok"] and "free-router" in t["model"] and asked[-1][0] == "free-router", str(t))

# ---- exactly as many clips as asked, one request each
words = [{"w": f"w{i}", "s": i * 0.5, "e": i * 0.5 + 0.4} for i in range(2400)]          # twenty minutes of talk
db_ = np.zeros(1200)
asked.clear()
clips, info = brain.find_clips(words, db_, 1200.0, "T", "U", max_clips=5, min_len=15, max_len=60, log=lambda m: None)
check("exactly the number asked for", len(clips) == 5, str(len(clips)))
check("one request per clip", len(asked) == 5, str(len(asked)))
check("none overlap", all(a["end"] <= b["start"] for a, b in zip(clips, clips[1:])), str([(c["start"], c["end"]) for c in clips]))
check("later requests name what was already chosen", "Already chosen" in asked[-1][1] and "Already chosen" not in asked[0][1])
check("it asks for one moment, not a list", "ONE moment" in asked[0][1])
check("tokens are counted", info["tokens"] == 5 * 60 and info["model"] == "free-router", str(info))

# ---- the service is down: the count is still made, by loudness, and the page is told
mode["fail"] = True
asked.clear()
loud = np.abs(np.sin(np.arange(1200) / 7.0)) * 3
clips, info = brain.find_clips(words, loud, 1200.0, "T", "U", max_clips=4, min_len=15, max_len=60, log=lambda m: None)
check("with the AI down, the count is still made", len(clips) == 4, str(len(clips)))
check("and it says so", "loudness" in info["note"], str(info))
mode["fail"] = False

# ---- cancelled: nothing more is asked
asked.clear()
clips, info = brain.find_clips(words, db_, 1200.0, "T", "U", max_clips=5, log=lambda m: None, stop=lambda: True)
check("a cancelled video asks for nothing", asked == [] and clips == [])

# ---- the status line is told what is happening
said = []
clips, info = brain.find_clips(words, db_, 1200.0, "T", "U", max_clips=2, log=lambda m: None, status=said.append)
check("the page is told which clip is being found", any("clip 1 of 2" in s for s in said) and any("clip 2 of 2" in s for s in said), str(said))

# ---- the rest between requests is real
ai.save("custom", "", "http://127.0.0.1:3001/v1", None, slow=True, wait=2)
ai.note_call()
began = time.time()
ai.wait_turn()
ai.wait_turn()
took = time.time() - began
check("each turn waits for the pause since the last", 3.5 <= took < 6.5, f"{took:.1f}s")
check("no waiting when slow mode is off", (ai.save("custom", "", "http://127.0.0.1:3001/v1", None, slow=False, wait=2), ai.pace_seconds())[1] == 0)
check("a normal service is not slow unless asked", (ai.save("gemini", "gemini-2.5-flash", "", "k" * 20), ai.current()["slow"])[1] is False)

# ---- a router with no keys of its own is told apart from a refused key
why = ai.explain('HTTP 503 {"error":{"message":"No candidate model has a configured, usable provider key. Add provider API keys in the dashboard.","code":"no_providers_configured"}}')
check("a router with no provider keys is not blamed on your key", "no working keys" in why and "refused the key" not in why, why)
check("a router that has run out says so", "used up" in ai.explain('HTTP 429 {"error":{"message":"All models exhausted","code":"routing_exhausted"}}'))
check("a real refused key still says so", "refused the key" in ai.explain('HTTP 401 {"error":{"message":"Invalid API key"}}'))
tries = []


def refuses_json_mode(url, json=None, headers=None, timeout=None, **_k):  # noqa: A002
    tries.append(sorted(k for k in json if k in ("temperature", "response_format")))
    if "response_format" in json:
        return Reply(429, {"error": {"message": "1 platform cannot honor response_format"}})
    return Reply(200, {"choices": [{"message": {"content": "[\"ok\"]"}}], "usage": {}})


ai.httpx.post = refuses_json_mode
ai.save("groq", "m", "", "k" * 20, slow=False)
ai.call("m", "hi", brain.SCHEMA)
check("a 429 about response_format is retried without it", tries[-1] == [] and len(tries) == 3, str(tries))
tries.clear()
ai.save("custom", "m", "http://127.0.0.1:3001/v1", None, slow=False)
ai.call("m", "hi", brain.SCHEMA)
check("a custom service is not sent JSON mode at all", tries == [[]], str(tries))

# ---- a tired free service: the owner's retry rules and the slow mode's rest work together
from unittest.mock import patch  # noqa: E402

napped = []
real_time = brain.time
brain.time = types.SimpleNamespace(sleep=napped.append, time=time.time)
tired = [ai.AIError("HTTP 429 slow down"), ([{"hook": "h"}], 4, 2)]


def tired_call(model, prompt, schema, timeout=None):
    a = tired.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


ai.save("custom", "m", "http://127.0.0.1:3001/v1", None, slow=True, wait=45)
with patch.object(ai, "call", side_effect=tired_call), patch.object(ai, "wait_turn"):
    out, _m, _n = brain.ask_ai("p", paced=True)
check("slow mode waits the whole rest after a 'too many requests', not just the 20 seconds", (out, len(napped), sum(napped)) == ([{"hook": "h"}], 45, 45), str((len(napped), sum(napped))))

napped.clear()
tired[:] = [ai.AIError("HTTP 429 slow down"), ([{"hook": "h"}], 4, 2)]
with patch.object(ai, "call", side_effect=tired_call), patch.object(ai, "wait_turn"):
    looks = [0]

    def cancelled_after_the_first_look():
        looks[0] += 1
        return looks[0] > 1                # fine before the request, cancelled while resting after it
    try:
        brain.ask_ai("p", paced=True, stop=cancelled_after_the_first_look)
    except RuntimeError:
        pass
check("a video cancelled during the rest stops waiting at once", len(napped) == 0, str(len(napped)))

napped.clear()
tired[:] = [ai.AIError("HTTP 429 slow down"), ([{"hook": "h"}], 4, 2)]
ai.save("custom", "m", "http://127.0.0.1:3001/v1", None, slow=False, wait=45)
with patch.object(ai, "call", side_effect=tired_call):
    brain.ask_ai("p")
check("with slow mode off the owner's own wait is used", napped == [20.0], str(napped))
brain.time = real_time

print("ALL PASSED" if not fails else f"{len(fails)} failed")
import shutil  # noqa: E402

db.engine.dispose()
shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
sys.exit(1 if fails else 0)
