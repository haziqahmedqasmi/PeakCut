"""Connect a Claude account (Pro or Max plan) with Claude Code's own sign-in. Claude Code keeps all credentials.

PeakCut starts `claude auth login --claudeai`, shows the official sign-in link it prints, and waits. When the browser
is on the same computer the sign-in comes back by itself; otherwise the page shown after signing in gives a code,
which the owner pastes into PeakCut and which is handed to Claude Code unchanged."""
import atexit
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from . import claude_cli

FAILED = "The Claude connection did not finish. Try connecting again."
NO_LINK = "Claude Code did not give a sign-in link. Update Claude Code and try again, or run claude auth login in a terminal."
BAD_CODE = "That code was not accepted. Copy the whole code shown after signing in and paste it again."
LOGIN_SECONDS = 600
LINK_SECONDS = 30
LOGIN_HOSTS = {"claude.ai", "claude.com", "platform.claude.com", "console.anthropic.com"}
_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")
_CODE = re.compile(r"[A-Za-z0-9_\-.~]{1,600}#[A-Za-z0-9_\-.~]{1,200}")


def _link(line: str):
    """The official sign-in link in one line of output, or None. Terminal colour and link codes are removed."""
    for match in re.finditer(r"https://[^\s\x07\x1b]+", _ESCAPES.sub("", line)):
        url = match.group(0)
        parsed = urlparse(url)
        if parsed.hostname in LOGIN_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443):
            return url
    return None


class Login:
    """One running `claude auth login`, read on its own thread so its output never fills up and blocks it."""
    def __init__(self, args):
        self.found = threading.Event()
        self.url = None
        self.bad_code = False
        self.process = subprocess.Popen([*args, "auth", "login", "--claudeai"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                        errors="replace", env=claude_cli._env(),
                                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self.reader = threading.Thread(target=self._read, daemon=True, name="peakcut-claude-login")
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                if "invalid code" in line.lower():
                    self.bad_code = True
                if self.url is None:
                    self.url = _link(line)
                    if self.url:
                        self.found.set()
        except (OSError, ValueError):
            pass
        finally:
            self.found.set()

    def running(self):
        return self.process.poll() is None

    def send(self, code: str):
        self.bad_code = False
        try:
            self.process.stdin.write(code + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise claude_cli.ClaudeError(FAILED) from None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.reader.join(timeout=1)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class Connection:
    def __init__(self):
        self.lock = threading.RLock()
        self.login = None
        self.deadline = 0
        self.expiry = None
        self.notice = ""

    def _state(self, state, account=None, error=""):
        return {"state": state, "installed": bool(claude_cli.command()), "account": account,
                "auth_url": self.login.url if state == "pending" and self.login else None,
                "error": error or (self.notice if state == "pending" else "")}

    def _stop(self):
        if self.expiry:
            self.expiry.cancel()
            self.expiry = None
        if self.login:
            self.login.close()
        self.login, self.deadline, self.notice = None, 0, ""

    def _expire(self, login):
        with self.lock:
            if self.login is login:
                self.status()

    def status(self, refresh=False):
        with self.lock:
            if self.login:
                login = self.login
                if login.running():
                    if time.monotonic() < self.deadline:
                        self.notice = BAD_CODE if login.bad_code else ""
                        return self._state("pending")
                    self._stop()
                    claude_cli.forget_status()
                    current = claude_cli.status(refresh=True)
                    return self._state("connected" if current["subscription"] else "error", current["account"],
                                       error="The sign-in link expired." + (" The current Claude account is still connected."
                                                                           if current["subscription"] else " Connect Claude again."))
                code = login.process.returncode
                self._stop()
                claude_cli.forget_status()
                if code != 0:
                    return self._state("error", error=FAILED)
                refresh = True
            current = claude_cli.status(refresh=refresh)
            if not current["installed"]:
                return self._state("unavailable", error=claude_cli.INSTALL)
            if current["subscription"]:
                return self._state("connected", current["account"])
            # Signed out is not a problem to report; an API-key sign-in is.
            return self._state("disconnected", error=current["error"] if current["error"] == claude_cli.API_LOGIN else "")

    def start(self):
        with self.lock:
            if self.login and self.login.running():
                current = self.status()
                if current["state"] == "pending":
                    return current
            self._stop()
            args = claude_cli.command()
            if not args:
                raise claude_cli.ClaudeError(claude_cli.INSTALL)
            try:
                login = Login(args)
            except OSError:
                raise claude_cli.ClaudeError(claude_cli.INSTALL) from None
            login.found.wait(LINK_SECONDS)
            if not login.url:
                login.close()
                raise claude_cli.ClaudeError(NO_LINK)
            self.login, self.deadline = login, time.monotonic() + LOGIN_SECONDS
            self.expiry = threading.Timer(LOGIN_SECONDS, self._expire, args=(login,))
            self.expiry.daemon = True
            self.expiry.start()
            claude_cli.forget_status()
            return self._state("pending")

    def send_code(self, code):
        """Hand the code from the sign-in page to Claude Code, then give it a moment to finish."""
        code = str(code or "").strip()
        if not _CODE.fullmatch(code):
            raise ValueError("Paste the whole code shown after signing in. It has a # in the middle.")
        with self.lock:
            login = self.login
            if not login or not login.running():
                raise ValueError("There is no sign-in waiting for a code. Connect Claude again.")
            login.send(code)
        until = time.monotonic() + 15
        while time.monotonic() < until and login.running() and not login.bad_code:
            try:
                login.process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        return self.status()

    def cancel(self):
        with self.lock:
            self._stop()
            return self.status(refresh=True)

    def close(self):
        with self.lock:
            self._stop()


connection = Connection()
atexit.register(connection.close)
