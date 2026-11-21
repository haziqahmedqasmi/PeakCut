"""Secrets at rest: the AI key, posting tokens, the Discord and Telegram settings and the platform apps' secrets are
kept locked in their files. Runs against the code directly, with its own empty data folder."""
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="pc-vault-")
os.environ.update(PEAKCUT_DATA=DATA, DATABASE_URL="", PEAKCUT_AI_PROVIDER="gemini", PEAKCUT_AI_MODELS="", PEAKCUT_AI_KEY="",
                  PEAKCUT_AI_KEY_FILE="", PEAKCUT_SECRET_KEY_FILE="")
os.environ.pop("PEAKCUT_SECRET_KEY", None)
sys.path.insert(0, ".")
from studio import accounts, ai, config, ctx, vault  # noqa: E402

fails = []


def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)


def use_key(value):
    """Switch the key in use, as a restart with a different PEAKCUT_SECRET_KEY would."""
    if value is None:
        os.environ.pop("PEAKCUT_SECRET_KEY", None)
    else:
        os.environ["PEAKCUT_SECRET_KEY"] = value
    config._kept.clear()
    vault.reset_for_tests()


posix = os.name != "nt"

# ---- the basics
s = vault.seal("my-secret-value")
check("a sealed value is not the plain text", ("my-secret-value" in s, vault.is_sealed(s)), (False, True))
check("it opens again", vault.unseal(s), "my-secret-value")
check("sealing twice does not seal twice", vault.seal(s), s)
check("empty stays empty", (vault.seal(""), vault.unseal("")), ("", ""))
check("plain text from an older version is read as it is", vault.unseal("plain-old-value"), "plain-old-value")
check("with no key set, PeakCut keeps one in the data folder", (vault.where(), (Path(DATA) / "secret.key").is_file()), ("data folder", True))
if posix:
    check("...readable only by the app", stat.S_IMODE((Path(DATA) / "secret.key").stat().st_mode), 0o600)
    old = os.umask(0)                                    # a permissive umask must not matter
    f = Path(DATA) / "private.txt"
    vault.write_private(f, "x")
    check("a private file is never readable by others, even with a permissive umask", stat.S_IMODE(f.stat().st_mode), 0o600)
    f.chmod(0o666)
    vault.write_private(f, "y")
    check("...and an existing file is made private again", (stat.S_IMODE(f.stat().st_mode), f.read_text()), (0o600, "y"))
    os.umask(old)

# ---- a key from the environment, and what a wrong one does
use_key("a-long-random-key-for-the-test-1234567890")
check("the key can come from the environment", vault.where(), "environment")
sealed_env = vault.seal("token-one")
check("it opens with the same key", vault.unseal(sealed_env), "token-one")
use_key("a-different-key-entirely-0987654321-abcdef")
check("a different key cannot open it, and says so instead of giving rubbish", (vault.unseal(sealed_env), bool(vault.problem["text"])), ("", True))
use_key("a-long-random-key-for-the-test-1234567890")
check("...and the right one still can", vault.unseal(sealed_env), "token-one")

# ---- accounts: posting tokens and the Discord and Telegram settings
UID = "usertest01"
with ctx.as_user(UID):
    accounts._write("accounts.json", [{"id": "a1", "platform": "youtube", "name": "Chan", "added": 1,
                                       "access_token": "AT-secret-1", "refresh_token": "RT-secret-1", "token": "IG-secret-1"}])
    raw = (accounts._dir() / "accounts.json").read_text()
    check("posting tokens are not in the file as plain text", any(x in raw for x in ("AT-secret-1", "RT-secret-1", "IG-secret-1")), False)
    check("...but the name and id still are, for anything that reads the file", ("Chan" in raw, '"a1"' in raw), (True, True))
    a = accounts.get("a1")
    check("the tokens come back whole when PeakCut needs them", (a["access_token"], a["refresh_token"], a["token"]),
          ("AT-secret-1", "RT-secret-1", "IG-secret-1"))
    if posix:
        check("the file is private", stat.S_IMODE((accounts._dir() / "accounts.json").stat().st_mode), 0o600)
    accounts.save_settings({"discord_webhook": "https://discord.com/api/webhooks/1/abcdef", "telegram_token": "123:TELEGRAMsecret", "digest_hour": 7})
    raw = (accounts._dir() / "settings.json").read_text()
    check("the Discord and Telegram secrets are locked too", ("abcdef" in raw, "TELEGRAMsecret" in raw, '"digest_hour": 7' in raw), (False, False, True))
    accounts._settings_mem.clear()
    st = accounts.settings()
    check("...and open again from the file", (st["discord_webhook"], st["telegram_token"]),
          ("https://discord.com/api/webhooks/1/abcdef", "123:TELEGRAMsecret"))
    check("the page is still only told whether they are set", accounts.public_settings()["telegram_token"], True)

    # an older version saved them as plain text: locked at the next start, and not again after that
    folder = accounts._dir()
    (folder / "accounts.json").write_text(json.dumps([{"id": "a2", "platform": "tiktok", "name": "T", "added": 2,
                                                       "access_token": "OLD-plain-AT", "refresh_token": "OLD-plain-RT"}]))
    (folder / "settings.json").write_text(json.dumps({"telegram_token": "OLD-plain-telegram", "paused": False}))
    check("an older, plain-text file is still read", accounts.get("a2")["access_token"], "OLD-plain-AT")
n = accounts.seal_existing()
with ctx.as_user(UID):
    raw = (accounts._dir() / "accounts.json").read_text() + (accounts._dir() / "settings.json").read_text()
    check("at start, plain-text secrets from an older version are locked", (n >= 2, any(x in raw for x in ("OLD-plain-AT", "OLD-plain-RT", "OLD-plain-telegram"))), (True, False))
    check("...and still open", accounts.get("a2")["refresh_token"], "OLD-plain-RT")
check("running it again changes nothing", accounts.seal_existing(), 0)

# ---- the platform apps' secrets
accounts.save_app("youtube", "clientid-123", "client-secret-xyz-789")
raw = (Path(DATA) / "apps.json").read_text()
check("an app's secret is locked in its file, its id is not", ("client-secret-xyz-789" in raw, "clientid-123" in raw), (False, True))
check("...and PeakCut gets both back", accounts.app_keys("google"), ("clientid-123", "client-secret-xyz-789"))
(Path(DATA) / "apps.json").write_text(json.dumps({"google": {"id": "clientid-123", "secret": "legacy-secret-plain"}}))
accounts._apps_seen["at"] = None
check("a plain-text apps file is still read", accounts.app_keys("google"), ("clientid-123", "legacy-secret-plain"))
check("...and is locked at the next start", (accounts.seal_existing(), "legacy-secret-plain" in (Path(DATA) / "apps.json").read_text()), (1, False))
check("...without losing it", accounts.app_keys("google"), ("clientid-123", "legacy-secret-plain"))

# ---- the AI key
KEY = "ai-key-abcdefgh12345678"
ai.save("gemini", "model-a", "", KEY)
raw = (Path(DATA) / "ai.json").read_text()
check("the AI key is locked in ai.json", (KEY in raw, "enc:v1:" in raw), (False, True))
check("...and PeakCut uses it as before", (ai.key(), ai.ready()), (KEY, True))
check("the dashboard still only sees the last four characters", ai.public()["key_hint"], "5678")
(Path(DATA) / "ai.json").write_text(json.dumps({"provider": "gemini", "models": ["model-a"], "base_url": "", "key": "legacy-ai-key-plain-0000"}))
ai._saved["at"] = None
check("a plain-text key from an older version is used", ai.key(), "legacy-ai-key-plain-0000")
raw = (Path(DATA) / "ai.json").read_text()
check("...and locked in the file as soon as it is read", ("legacy-ai-key-plain-0000" in raw, "enc:v1:" in raw), (False, True))
check("...and still works after that", (ai.key(), ai.ready()), ("legacy-ai-key-plain-0000", True))
use_key("yet-another-key-not-the-one-used-before-55")
ai._saved["at"] = None
check("with the wrong key the AI shows as not set up, so the person can enter it again", (ai.key(), ai.ready()), ("", False))

print("ALL PASSED" if not fails else f"{len(fails)} failed")
shutil.rmtree(DATA, ignore_errors=True)
sys.exit(1 if fails else 0)
