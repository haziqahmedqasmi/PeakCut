"""Secrets at rest: the AI key, the tokens PeakCut posts with, and the platform apps' secrets are kept encrypted in
their files, not as plain text.

    PEAKCUT_SECRET_KEY        the key that locks them: a long random value (or PEAKCUT_SECRET_KEY_FILE, a file holding it)

Make one with:  python -c "import secrets; print(secrets.token_urlsafe(32))"

On a server, keep that key outside the data folder (in .env, or a file under secrets/): then a copy of the data
folder, a backup, or a file sent by mistake does not give the secrets away. With no key set, PeakCut makes one for
itself in the data folder (secret.key, readable only by the app). That still keeps secrets out of single files and
out of anything that only copies some of them, but whoever has the whole data folder has the key too, so it is the
weaker choice. Lose the key and the saved secrets cannot be read: they have to be typed in again.

Encryption is Fernet (AES-128 with a message check), from the `cryptography` package. A sealed value looks like
"enc:v1:..."; a value without that start is plain text from an older version and is still read, then sealed the next
time it is saved."""
import base64
import hashlib
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config

PREFIX = "enc:v1:"
_lock = threading.Lock()
_fernet = {"f": None, "source": ""}
problem = {"text": ""}          # set when something sealed could not be opened (wrong or lost key)


def write_private(path, text: str) -> None:
    """Write a file that only this program's user can read, from the first byte: made with those permissions, not
    changed afterwards, so there is never a moment when it can be read by anyone else."""
    path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    finally:
        try:
            os.chmod(path, 0o600)        # a file that already existed keeps its old permissions otherwise
        except OSError:
            pass


def _from_text(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def _make() -> tuple:
    given = config.secret_key()
    if given:
        return _from_text(given), "environment"
    f = config.DATA_DIR / "secret.key"
    if not f.is_file():
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)       # two processes starting together: one wins
            with os.fdopen(fd, "w", encoding="ascii") as out:
                out.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
        except FileExistsError:
            pass
    return _from_text(f.read_text(encoding="ascii").strip()), "data folder"


def _get() -> Fernet:
    with _lock:
        if _fernet["f"] is None:
            _fernet["f"], _fernet["source"] = _make()
        return _fernet["f"]


def where() -> str:
    """Where the key comes from: 'environment' (the better choice) or 'data folder'."""
    _get()
    return _fernet["source"]


def is_sealed(text) -> bool:
    return isinstance(text, str) and text.startswith(PREFIX)


def seal(text):
    """The text, locked. Empty stays empty, and what is already locked is not locked again."""
    if not isinstance(text, str) or not text or is_sealed(text):
        return text
    return PREFIX + _get().encrypt(text.encode("utf-8")).decode("ascii")


def unseal(text):
    """The text, opened. Plain text from an older version comes back as it is. A sealed value that cannot be opened
    (the key was changed or lost) comes back empty, and `problem` says so, so the page can ask for it again."""
    if not is_sealed(text):
        return text
    try:
        return _get().decrypt(text[len(PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        problem["text"] = ("A saved key or token could not be read, because PEAKCUT_SECRET_KEY is not the one it was "
                           "saved with. Enter it again in Settings.")
        return ""


def reset_for_tests() -> None:
    with _lock:
        _fernet.update(f=None, source="")
    problem["text"] = ""
