import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

import daemon  # noqa: E402
import service as svc  # noqa: E402

# Prints one stream-json reply, like `claude -p ... --output-format stream-json`.
FAKE_CLAUDE = r"""#!/bin/sh
echo '{"type":"system","subtype":"init","session_id":"s1"}'
echo '{"type":"result","subtype":"success","result":"hi","session_id":"s1"}'
"""


class SocketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.path = os.path.join(self.home, "s.sock")
        self.server = svc.Server(self.path)
        self.settings_file = svc.settings_path(self.home)
        await self.server.start(svc.Service(self.home, self.settings_file, self.server.broadcast))
        self.events = []

        async def on_event(event, payload):
            self.events.append((event, payload))

        self.client = svc.Client(self.path, on_event)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.tmp.cleanup()

    async def test_calls_and_settings(self):
        self.assertEqual(await self.client.call("list_sessions", [10, ""]), [])
        saved = await self.client.call("set_settings", [{"model": " opus ", "remote_server": "1"}])
        self.assertEqual(saved["model"], "opus")
        self.assertEqual(saved["remote_server"], "")
        with open(self.settings_file) as fh:
            self.assertEqual(json.load(fh)["model"], "opus")
        self.assertEqual((await self.client.call("get_settings", {}))["model"], "opus")

    async def test_errors(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown method"):
            await self.client.call("_save_settings")
        result = await self.client.call("send_prompt", ["", self.home, "  "])
        self.assertEqual(result, {"error": "Type a prompt first."})

    async def test_job_events_reach_client(self):
        fake = os.path.join(self.home, "claude")
        with open(fake, "w") as fh:
            fh.write(FAKE_CLAUDE)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        await self.client.call("set_settings", [{"claude_path": fake}])
        started = await self.client.call("send_prompt", ["", self.home, "hello"])
        job_id = started["job"]["id"]
        for _ in range(50):
            if any(p.get("type") == "end" for _, p in self.events):
                break
            await asyncio.sleep(0.1)
        end = [p for e, p in self.events if e == "claude_job" and p.get("type") == "end"]
        self.assertEqual(end[0]["job_id"], job_id)
        self.assertEqual(end[0]["job"]["status"], "done")
        self.assertEqual(end[0]["job"]["session_id"], "s1")

    async def test_client_without_server(self):
        await self.client.close()
        await self.server.close()
        client = svc.Client(self.path, None)
        with self.assertRaisesRegex(RuntimeError, "isn't running"):
            await client.call("list_jobs", timeout=1)


class DaemonTest(unittest.TestCase):
    def test_sync_files_only_reports_real_changes(self):
        with tempfile.TemporaryDirectory() as home:
            src = os.path.join(os.path.dirname(__file__), "..", "py_modules")
            self.assertTrue(daemon.sync_files(src, home, "/usr/bin/python3"))
            self.assertFalse(daemon.sync_files(src, home, "/usr/bin/python3"))
            with open(daemon.unit_path(home)) as fh:
                unit = fh.read()
            script = os.path.join(daemon.install_dir(home), "service.py")
            self.assertIn(f"ExecStart=/usr/bin/python3 {script}", unit)
            for name in daemon.FILES:
                self.assertTrue(os.path.exists(os.path.join(daemon.install_dir(home), name)))

    def test_migrate_settings(self):
        with tempfile.TemporaryDirectory() as home:
            old = os.path.join(home, "old.json")
            new = svc.settings_path(home)
            daemon.migrate_settings(old, new)
            self.assertFalse(os.path.exists(new))
            with open(old, "w") as fh:
                fh.write('{"model": "opus"}')
            daemon.migrate_settings(old, new)
            with open(new) as fh:
                self.assertEqual(json.load(fh)["model"], "opus")


if __name__ == "__main__":
    unittest.main()
