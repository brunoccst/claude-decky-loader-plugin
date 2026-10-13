import asyncio
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

import remote_control as rc  # noqa: E402

# Stands in for `claude remote-control`: asks for confirmation on the terminal,
# then prints a colored session URL and stays up like the real server.
FAKE_CLAUDE = r"""#!/bin/sh
[ -t 0 ] || { echo "not a terminal"; exit 3; }
printf 'Remote Control lets you continue from your phone.\r\nEnable Remote Control? (y/n) '
read answer
[ "$answer" = y ] || exit 1
printf '\033[1mSession URL:\033[0m \033[36mhttps://claude.ai/code/session_01ABC?env=1\033[0m\r\n'
sleep 30
"""


class Helpers(unittest.TestCase):
    def test_strip_and_find(self):
        raw = "\x1b]8;;https://claude.ai/code/session_x\x07link\x1b]8;;\x07 \x1b[32mok\x1b[0m"
        self.assertEqual(rc.find_url(raw), "https://claude.ai/code/session_x")
        self.assertEqual(rc.strip_ansi(raw), "link ok")
        self.assertEqual(rc.strip_ansi("old\rnew"), "new")

    def test_pending_question(self):
        self.assertEqual(rc.pending_question("intro\nTrust /home/deck? [y/N] "), "Trust /home/deck? [y/N]")
        self.assertIsNone(rc.pending_question("Enable Remote Control? (y/n)\nStarting…"))

    def test_build_command(self):
        settings = {"permission_mode": "acceptEdits", "model": "opus"}
        self.assertEqual(
            rc.build_command("claude", rc.SERVER, "Steam Deck", settings),
            ["claude", "remote-control", "--name", "Steam Deck", "--permission-mode", "acceptEdits", "--model", "opus"],
        )
        self.assertEqual(
            rc.build_command("claude", "abc", "Fix it", {"permission_mode": "bogus"}),
            ["claude", "--remote-control", "Fix it", "--resume", "abc"],
        )


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = os.path.join(self.tmp.name, "claude")
        with open(self.claude, "w") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(self.claude, os.stat(self.claude).st_mode | stat.S_IEXEC)
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    async def _emit(self, event, payload):
        self.events.append(payload)

    async def _until(self, check, timeout=5.0):
        end = asyncio.get_event_loop().time() + timeout
        while not check():
            if asyncio.get_event_loop().time() > end:
                self.fail(f"timed out; events: {self.events}")
            await asyncio.sleep(0.05)

    def test_question_answer_url_stop(self):
        async def run():
            manager = rc.RemoteManager(self.tmp.name, self._emit)
            remote = await manager.start(self.claude, rc.SERVER, "Deck", self.tmp.name, {})
            await self._until(lambda: remote.status == "question")
            self.assertEqual(remote.question, "Enable Remote Control? (y/n)")
            self.assertTrue(await manager.answer(rc.SERVER, "y\r"))
            await self._until(lambda: remote.status == "running")
            self.assertEqual(remote.url, "https://claude.ai/code/session_01ABC?env=1")
            self.assertEqual(manager.active_keys(), [rc.SERVER])
            self.assertTrue(await manager.stop(rc.SERVER))
            await self._until(lambda: self.events[-1]["status"] == "exited")
            self.assertIsNone(remote.error)
            self.assertIsNone(manager.get(rc.SERVER))

        asyncio.run(run())

    def test_failure_reports_output(self):
        async def run():
            manager = rc.RemoteManager(self.tmp.name, self._emit)
            remote = await manager.start(self.claude, "abc", "Deck", self.tmp.name, {})
            await self._until(lambda: remote.status == "question")
            await manager.answer("abc", "n\r")
            await self._until(lambda: remote.status == "exited")
            self.assertIn("Enable Remote Control?", remote.error)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
