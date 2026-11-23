"""Claude subscription through Claude Code. CLI responses are replaced; no account or network is used."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# A throwaway data folder with sign-in off, set before PeakCut reads its settings.
DATA = tempfile.mkdtemp(prefix="pc-claude-test-")
os.environ.update(PEAKCUT_DATA=DATA, PEAKCUT_STORAGE_FILE=str(Path(DATA) / "storage.json"), DATABASE_URL="", PEAKCUT_ACCOUNTS="0", PEAKCUT_FETCH_MODEL="0",
                  PEAKCUT_UPDATE_CHECK="0", PEAKCUT_PUBLIC_URL="http://127.0.0.1:8765", PEAKCUT_AI_KEY="", PEAKCUT_AI_KEY_FILE="")

from studio import claude_account, claude_cli  # noqa: E402

SIGNED_IN = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "owner@example.com",
             "orgName": "Owner's Organization", "subscriptionType": "max"}


def result(text='{"items":["ok"]}', usage=None, error=False, subtype="success"):
    return json.dumps({"type": "result", "subtype": subtype, "is_error": error, "result": text,
                       "usage": usage or {"input_tokens": 20, "cache_creation_input_tokens": 5,
                                          "cache_read_input_tokens": 7, "output_tokens": 3}})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.command = patch.object(claude_cli, "command", return_value=["claude.exe"]).start()
        patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-secret", "ANTHROPIC_BASE_URL": "https://other.example",
                                "CLAUDE_CODE_USE_BEDROCK": "1"}).start()
        self.addCleanup(patch.stopall)
        claude_cli.forget_status()
        self.addCleanup(claude_cli.forget_status)
        self.calls = []
        self.output, self.exitcode, self.login = result(), 0, SIGNED_IN
        self.inspect = lambda args, kw: None
        patch.object(claude_cli.subprocess, "run", side_effect=self.fake_run).start()

    def fake_run(self, args, **kw):
        self.calls.append((args, kw))
        if args[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(args, 0 if self.login.get("loggedIn") else 1, json.dumps(self.login), "")
        self.prompt = kw["stdin"].read().decode("utf-8")
        self.inspect(args, kw)
        return subprocess.CompletedProcess(args, self.exitcode, self.output, "private diagnostic")

    def test_answer_usage_and_prompt_on_stdin(self):
        prompt = "ação " + "x" * 40000 + " $(do-not-run)"
        self.assertEqual(claude_cli.run("default", prompt), ('{"items":["ok"]}', 32, 3))
        args, kw = self.calls[-1]
        self.assertNotIn(prompt, args)
        self.assertEqual(self.prompt, prompt)
        self.assertFalse(kw.get("shell", False))
        self.assertNotIn("--model", args)
        self.assertNotIn("--bare", args)     # bare mode never reads the subscription sign-in
        for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--system-prompt"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertEqual(args[args.index("--setting-sources") + 1], "")
        self.assertEqual(args[args.index("--output-format") + 1], "json")
        self.assertTrue(Path(kw["cwd"]).name.startswith("pc-claude-"))

    def test_child_uses_the_plan_not_an_api_key(self):
        claude_cli.run("sonnet", "hello", reasoning_effort="high")
        for args, kw in self.calls:
            env = kw["env"]
            self.assertFalse(any(name.startswith("ANTHROPIC_") for name in env))
            self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", env)
            self.assertEqual(env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        args = self.calls[-1][0]
        self.assertEqual(args[args.index("--model") + 1], "sonnet")
        self.assertEqual(args[args.index("--effort") + 1], "high")

    def test_status_shows_account_and_rejects_api_logins(self):
        state = claude_cli.status(refresh=True)
        self.assertTrue(state["subscription"])
        self.assertEqual(state["account"], {"email": "owner@example.com", "plan": "max", "org": "Owner's Organization"})
        self.login = {"loggedIn": True, "authMethod": "api_key"}
        state = claude_cli.status(refresh=True)
        self.assertFalse(state["subscription"])
        self.assertEqual(state["error"], claude_cli.API_LOGIN)
        self.login = {"loggedIn": False, "authMethod": "none"}
        self.assertEqual(claude_cli.status(refresh=True)["error"], claude_cli.LOGIN)
        calls = len(self.calls)
        with self.assertRaisesRegex(claude_cli.ClaudeError, "claude auth login"):
            claude_cli.run("default", "hello")
        self.assertEqual(len(self.calls), calls)       # no request is made while signed out

    def test_image_goes_in_a_stream_message(self):
        text, sent, wrote = claude_cli.run("default", "where is the camera?", image=b"jpeg-bytes")
        args = self.calls[-1][0]
        self.assertEqual(args[args.index("--input-format") + 1], "stream-json")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        message = json.loads(self.prompt)
        blocks = message["message"]["content"]
        self.assertEqual(blocks[0]["type"], "image")
        self.assertEqual(blocks[0]["source"]["media_type"], "image/jpeg")
        self.assertEqual(blocks[1], {"type": "text", "text": "where is the camera?"})

    def test_event_stream_answer_is_read(self):
        self.output = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                                 json.dumps({"type": "assistant", "message": {}}), result("top left")])
        self.assertEqual(claude_cli.run("default", "look", image=b"x")[0], "top left")

    def test_errors_are_actionable_and_redacted(self):
        for reason, expected in [("API Error: 429 rate_limit_error sk-private", "limit"),
                                 ("Failed to authenticate: OAuth session expired sk-private", "claude auth login"),
                                 ("model not found: claude-nope sk-private", "model")]:
            with self.subTest(reason=reason):
                self.output, self.exitcode = result(reason, error=True), 1
                with self.assertRaisesRegex(claude_cli.ClaudeError, expected) as caught:
                    claude_cli.run("default", "hello")
                self.assertNotIn("sk-private", str(caught.exception))
                self.assertNotIn("private diagnostic", str(caught.exception))

    def test_missing_or_empty_answer_is_not_success(self):
        for output in ("", "not json", result("   "), result(subtype="error_max_turns")):
            with self.subTest(output=output[:30]):
                self.output = output
                with self.assertRaises(claude_cli.ClaudeError):
                    claude_cli.run("default", "hello")

    def test_timeout_has_a_finite_budget_and_releases_the_lock(self):
        def timeout(args, kw):
            self.assertLessEqual(kw["timeout"], 0.5)
            raise subprocess.TimeoutExpired(args, kw["timeout"], output="secret")
        claude_cli.status()
        self.inspect = timeout
        with self.assertRaisesRegex(claude_cli.ClaudeError, "timed out"):
            claude_cli.run("default", "hello", timeout=0.5)
        self.inspect = lambda args, kw: None
        self.assertEqual(claude_cli.run("default", "hello")[1:], (32, 3))

    def test_queue_wait_respects_the_call_deadline(self):
        claude_cli._lock.acquire()
        try:
            with self.assertRaisesRegex(claude_cli.ClaudeError, "timed out"):
                claude_cli.run("default", "hello", timeout=0.01)
            self.assertFalse(self.calls)
        finally:
            claude_cli._lock.release()

    def test_missing_executable_explains_installation(self):
        self.command.return_value = []
        self.assertFalse(claude_cli.status(refresh=True)["installed"])
        with self.assertRaisesRegex(claude_cli.ClaudeError, "Install Claude Code"):
            claude_cli.run("default", "hello")

    def test_invalid_effort_is_refused(self):
        with self.assertRaises(ValueError):
            claude_cli.run("default", "hello", reasoning_effort="ultra")
        self.assertFalse(self.calls)


class DiscoveryTests(unittest.TestCase):
    def test_npm_launcher_runs_the_native_program(self):
        name = "claude.exe" if sys.platform == "win32" else "claude"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "claude.cmd").write_text("@echo off", encoding="utf-8")
            binary = root / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / name
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"")
            with patch.object(claude_cli.shutil, "which", return_value=str(root / "claude.cmd")):
                self.assertEqual(claude_cli.command(), [str(binary.resolve())])

    def test_override_that_does_not_exist_finds_nothing(self):
        with patch.dict(os.environ, {"PEAKCUT_CLAUDE_COMMAND": "no-such-claude-here"}):
            self.assertEqual(claude_cli.command(), [])


class FakeLogin:
    """Stands in for `claude auth login`: prints the sign-in link and waits for a code or the browser."""
    def __init__(self, link="https://claude.com/cai/oauth/authorize?code=true&state=abc"):
        self.link, self.code, self.returncode, self.terminated = link, None, None, False
        self.stdin = self
        self.stdout = iter([])

    def make(self, args, **kw):
        self.args, self.kw = args, kw
        out = self
        lines = ["Opening browser to sign in…\n", f"If the browser didn't open, visit: \x1b]8;;{self.link}\x07{self.link}\x1b]8;;\x07\n"]
        if self.link is None:
            lines = ["Opening browser to sign in…\n"]

        class Out:
            def __iter__(self):
                yield from lines
                while out.returncode is None:
                    time.sleep(0.01)

            def close(self):
                pass
        self.stdout = Out()
        return self

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            time.sleep(min(timeout or 0, 0.05))
            if self.returncode is None:
                raise subprocess.TimeoutExpired("claude", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    kill = terminate

    def write(self, text):
        self.code = text
        self.returncode = 0          # Claude Code accepts the code and finishes

    def flush(self):
        pass

    def close(self):
        pass


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        patch.object(claude_cli, "command", return_value=["claude.exe"]).start()
        self.state = {"installed": True, "subscription": False, "error": claude_cli.LOGIN, "account": None}
        patch.object(claude_cli, "status", side_effect=lambda **kw: dict(self.state)).start()
        self.fake = FakeLogin()
        self.popen = patch.object(claude_account.subprocess, "Popen", side_effect=self.fake.make).start()
        self.connection = claude_account.Connection()
        self.addCleanup(self.connection.close)
        self.addCleanup(patch.stopall)

    def test_connect_shows_the_official_link_and_reuses_it(self):
        state = self.connection.start()
        self.assertEqual(state["state"], "pending")
        self.assertEqual(state["auth_url"], self.fake.link)
        self.assertEqual(self.fake.args[1:], ["auth", "login", "--claudeai"])
        self.assertFalse(any(name.startswith("ANTHROPIC_") for name in self.fake.kw["env"]))
        self.assertEqual(self.connection.start()["auth_url"], self.fake.link)
        self.assertEqual(self.popen.call_count, 1)

    def test_pasted_code_finishes_the_sign_in(self):
        self.connection.start()
        with self.assertRaises(ValueError):
            self.connection.send_code("no hash here")
        self.assertIsNone(self.fake.code)
        self.state = {"installed": True, "subscription": True, "error": "", "account": {"email": "owner@example.com", "plan": "pro", "org": ""}}
        state = self.connection.send_code("  abc-DEF_123#state-456 ")
        self.assertEqual(self.fake.code, "abc-DEF_123#state-456\n")
        self.assertEqual(state["state"], "connected")
        self.assertEqual(state["account"]["email"], "owner@example.com")

    def test_browser_sign_in_is_noticed_by_polling(self):
        self.connection.start()
        self.state = {"installed": True, "subscription": True, "error": "", "account": {"email": "a@example.com", "plan": "max", "org": ""}}
        self.fake.returncode = 0
        self.assertEqual(self.connection.status()["state"], "connected")

    def test_failed_sign_in_is_safe_and_can_be_retried(self):
        self.connection.start()
        self.fake.returncode = 1
        state = self.connection.status()
        self.assertEqual(state["state"], "error")
        self.assertEqual(state["error"], claude_account.FAILED)
        self.fake = FakeLogin()
        self.popen.side_effect = self.fake.make
        self.assertEqual(self.connection.start()["state"], "pending")

    def test_unexpected_destination_is_rejected(self):
        self.fake.link = "https://claude.ai.evil.example/oauth"
        with self.assertRaisesRegex(claude_cli.ClaudeError, "sign-in link"):
            claude_account.LINK_SECONDS = 0.3
            try:
                self.connection.start()
            finally:
                claude_account.LINK_SECONDS = 30
        self.assertTrue(self.fake.terminated)

    def test_cancel_stops_the_login_program(self):
        self.connection.start()
        state = self.connection.cancel()
        self.assertTrue(self.fake.terminated)
        self.assertEqual(state["state"], "disconnected")

    def test_expired_link_closes_the_login(self):
        self.connection.start()
        self.connection.deadline = time.monotonic() - 1
        state = self.connection.status()
        self.assertEqual(state["state"], "error")
        self.assertIn("expired", state["error"])
        self.assertTrue(self.fake.terminated)

    def test_links_are_read_from_terminal_output(self):
        link = "https://platform.claude.com/oauth/authorize?x=1"
        self.assertEqual(claude_account._link(f"visit: \x1b]8;;{link}\x07{link}\x1b]8;;\x07"), link)
        self.assertIsNone(claude_account._link("visit: https://user:pw@claude.ai/x"))
        self.assertIsNone(claude_account._link("visit: http://claude.ai/x"))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        from studio import ai, config
        self.ai = ai
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patch.object(config, "DATA_DIR", Path(self.directory.name)).start()
        patch.object(config, "AI_PROVIDER", "claude_sub").start()
        patch.object(config, "AI_MODELS", ["default"]).start()
        patch.object(config, "AI_BASE_URL", "https://stale.example").start()
        patch.dict(ai._saved, {"at": None, "data": {}}).start()
        patch.object(claude_cli, "status", return_value={"installed": True, "subscription": True, "error": "", "account": None}).start()
        self.addCleanup(patch.stopall)

    def test_save_needs_no_key_and_keeps_the_effort(self):
        saved = self.ai.save("claude_sub", "sonnet", "https://stale.example", "sk-ant-secret", reasoning_effort="medium")
        self.assertTrue(saved["ready"])
        self.assertFalse(saved["has_key"])
        self.assertEqual(self.ai.base_url(), "")
        self.assertEqual(self.ai.current()["reasoning_effort"], "medium")
        self.assertFalse(next(p for p in saved["providers"] if p["id"] == "claude_sub")["needs_key"])
        self.assertNotIn("sk-ant-secret", (Path(self.directory.name) / "ai.json").read_text())
        with self.assertRaises(ValueError):
            self.ai.save("claude_sub", "sonnet", reasoning_effort="ultra")

    def test_text_calls_ask_for_json_and_parse_it(self):
        with patch.object(claude_cli, "run", return_value=('```json\n{"items":["ok"]}\n```', 20, 3)) as run:
            schema = {"type": "ARRAY", "items": {"type": "STRING"}}
            self.assertEqual(self.ai.call("default", "hello", schema, 12), (["ok"], 20, 3))
            self.assertIn("JSON Schema", run.call_args.args[1])
            self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_vision_uses_the_same_subscription(self):
        with patch.object(claude_cli, "run", return_value=("top left", 11, 2)) as run:
            self.assertEqual(self.ai.see("look", b"jpeg", 8), ("top left", 11, 2))
            self.assertEqual(run.call_args.kwargs["image"], b"jpeg")

    def test_not_ready_when_signed_out(self):
        with patch.object(claude_cli, "status", return_value={"installed": True, "subscription": False, "error": claude_cli.LOGIN, "account": None}):
            self.assertFalse(self.ai.ready())
            with self.assertRaisesRegex(claude_cli.ClaudeError, "claude auth login"):
                self.ai.test()

    def test_subscription_test_and_errors(self):
        with patch.object(claude_cli, "run", return_value=('{"items":["ok"]}', 9, 1)):
            self.assertEqual(self.ai.test_claude_sub(["sonnet"], "low"), {"ok": True, "model": "sonnet", "reasoning_effort": "low", "tokens": 10})
        reason = "Claude subscription usage limit reached. Try again later or choose another model."
        self.assertEqual(self.ai.explain(claude_cli.ClaudeError(reason)), reason)
        self.assertEqual(self.ai.list_models("claude_sub")[0], "default")


class RouteTests(unittest.TestCase):
    """The settings page routes, with sign-in off (the built-in owner is the admin)."""
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        from studio import ai, config
        patch.object(config, "DATA_DIR", Path(self.directory.name)).start()
        patch.dict(ai._saved, {"at": None, "data": {}}).start()
        patch.object(claude_cli, "status", return_value={"installed": True, "subscription": True, "error": "",
                                                         "account": {"email": "owner@example.com", "plan": "max", "org": ""}}).start()
        self.addCleanup(patch.stopall)
        from fastapi.testclient import TestClient
        from studio import db, server
        db.init()
        self.client = TestClient(server.app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})
        self.ai = ai

    def test_use_tests_then_saves_the_model(self):
        state = self.client.get("/api/ai/claude").json()
        self.assertEqual(state["state"], "connected")
        self.assertFalse(state["active"])
        self.assertEqual(self.client.get("/api/ai/claude/models").json()["models"][0]["id"], "fable")
        self.assertEqual(self.client.post("/api/ai/claude/use", json={"models": ["nope"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/ai/claude/use", json={"models": ["haiku"], "reasoning_effort": "high"}).status_code, 400)
        with patch.object(claude_cli, "run", return_value=('{"items":["ok"]}', 9, 1)):
            r = self.client.post("/api/ai/claude/use", json={"models": ["sonnet"], "reasoning_effort": "low"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.ai.provider(), "claude_sub")
        self.assertEqual(self.ai.models(), ["sonnet"])
        state = self.client.get("/api/ai/claude").json()
        self.assertTrue(state["active"])
        self.assertEqual(state["reasoning_effort"], "low")

    def test_failed_test_keeps_the_old_settings(self):
        self.ai.save("ollama", "llama3.1")
        with patch.object(claude_cli, "run", side_effect=claude_cli.ClaudeError(claude_cli.LOGIN)):
            r = self.client.post("/api/ai/claude/use", json={"models": ["sonnet"]})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["detail"], claude_cli.LOGIN)
        self.assertEqual(self.ai.provider(), "ollama")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        import shutil
        shutil.rmtree(DATA, ignore_errors=True)
