import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

import claude_sessions as cs  # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def write_transcript(home: str, cwd: str, session_id: str = SESSION_ID) -> str:
    pdir = os.path.join(home, ".claude", "projects", cwd.replace("/", "-"))
    os.makedirs(pdir, exist_ok=True)
    lines = [
        {"type": "user", "isMeta": True, "cwd": cwd, "message": {"role": "user", "content": "<command-name>/init</command-name>"}},
        {"type": "user", "cwd": cwd, "timestamp": "t1", "message": {"role": "user", "content": "Fix the login bug"}},
        {"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "Looking at it."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        ]}},
        {"type": "user", "cwd": cwd, "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [{"type": "text", "text": "main.py"}]},
        ]}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent"}]}},
        {"type": "ai-title", "aiTitle": "Login bug fix", "sessionId": session_id},
    ]
    path = os.path.join(pdir, session_id + ".jsonl")
    with open(path, "w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
        fh.write("not json\n")
    return path


FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
sid = args[args.index("--resume") + 1] if "--resume" in args else "new-session-id"
prompt = args[args.index("-p") + 1]
def out(d): print(json.dumps(d), flush=True)
out({"type": "system", "subtype": "init", "session_id": sid})
out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "echo: " + prompt}]}})
out({"type": "result", "subtype": "success", "result": "echo: " + prompt, "session_id": sid, "total_cost_usd": 0.01})
"""


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.cwd = os.path.join(self.home, "proj")
        os.makedirs(self.cwd)
        write_transcript(self.home, self.cwd)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_sessions(self):
        sessions = cs.list_sessions(self.home)
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s["id"], SESSION_ID)
        self.assertEqual(s["title"], "Login bug fix")
        self.assertEqual(s["cwd"], self.cwd)
        self.assertEqual(s["project"], "proj")
        self.assertEqual(s["turns"], 1)

    def test_projects_and_filter(self):
        other = os.path.join(self.home, "other")
        os.makedirs(other)
        write_transcript(self.home, other, "99999999-2222-3333-4444-555555555555")
        write_transcript(self.home, self.cwd, "88888888-2222-3333-4444-555555555555")
        projects = {p["cwd"]: p for p in cs.list_projects(self.home)}
        self.assertEqual(projects[self.cwd]["sessions"], 2)
        self.assertEqual(projects[self.cwd]["name"], "proj")
        self.assertEqual(projects[other]["sessions"], 1)
        self.assertTrue(projects[other]["exists"])
        self.assertEqual(len(cs.list_sessions(self.home, cwd=other)), 1)
        self.assertEqual(len(cs.list_sessions(self.home, cwd=self.cwd)), 2)

    def test_get_session_messages(self):
        session = cs.get_session(self.home, SESSION_ID)
        kinds = [(m["role"], m["kind"]) for m in session["messages"]]
        self.assertEqual(kinds, [
            ("user", "text"),
            ("assistant", "text"),
            ("assistant", "tool_use"),
            ("tool", "tool_result"),
        ])
        self.assertEqual(session["messages"][2]["text"], "Bash: ls -la")
        self.assertEqual(session["messages"][3]["text"], "main.py")

    def test_rejects_path_traversal(self):
        self.assertIsNone(cs.get_session(self.home, "../../etc/passwd"))

    def test_recent_cwds(self):
        self.assertEqual(cs.recent_cwds(self.home), [self.cwd])

    def test_build_command(self):
        cmd = cs.build_command("claude", "hi", "abc", {"permission_mode": "acceptEdits", "model": ""})
        self.assertEqual(cmd, ["claude", "-p", "hi", "--output-format", "stream-json", "--verbose",
                               "--resume", "abc", "--permission-mode", "acceptEdits"])
        self.assertNotIn("--permission-mode", cs.build_command("claude", "hi", None, {"permission_mode": "bogus"}))

    def test_env_restores_ld_library_path(self):
        os.environ["LD_LIBRARY_PATH"] = "/frozen"
        os.environ["LD_LIBRARY_PATH_ORIG"] = "/orig"
        try:
            env = cs.subprocess_env(self.home)
            self.assertEqual(env["LD_LIBRARY_PATH"], "/orig")
            self.assertIn(os.path.join(self.home, ".local", "bin"), env["PATH"])
        finally:
            os.environ.pop("LD_LIBRARY_PATH")
            os.environ.pop("LD_LIBRARY_PATH_ORIG")


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.claude = os.path.join(self.home, "claude")
        with open(self.claude, "w") as fh:
            fh.write(FAKE_CLAUDE.replace("/usr/bin/env python3", sys.executable))
        os.chmod(self.claude, os.stat(self.claude).st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, session_id):
        events = []
        done = asyncio.Event()

        async def emit(name, payload):
            events.append(payload)
            if payload["type"] == "end":
                done.set()

        async def go():
            mgr = cs.JobManager(self.home, emit)
            job = await mgr.start(self.claude, "hello", session_id, self.home, {})
            await asyncio.wait_for(done.wait(), 10)
            return job

        return asyncio.run(go()), events

    def test_resume_job(self):
        job, events = self.run_job("abc")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.session_id, "abc")
        self.assertEqual(job.result, "echo: hello")
        self.assertEqual([m["text"] for m in job.messages], ["hello", "echo: hello"])
        self.assertEqual([e["type"] for e in events], ["init", "messages", "end"])

    def test_new_session_job_learns_id(self):
        job, _ = self.run_job(None)
        self.assertEqual(job.session_id, "new-session-id")


if __name__ == "__main__":
    unittest.main()
