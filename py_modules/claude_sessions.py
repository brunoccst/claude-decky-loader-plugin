"""Claude Code session helpers for the Decky backend.

Kept free of any `decky` import so it can be unit tested on a normal machine.

Sessions are read straight from Claude Code's transcript store
(`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`). Prompts are sent by
running the Claude Code CLI in print mode with `--output-format stream-json`,
resuming the session when one is given.
"""

import asyncio
import json
import os
import shutil
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

PERMISSION_MODES = ["", "acceptEdits", "plan", "auto", "bypassPermissions"]

DEFAULT_SETTINGS: Dict[str, Any] = {
    "claude_path": "",
    "permission_mode": "",
    "model": "",
    "default_cwd": "",
    "remote_server": "",  # "1" keeps the Remote Control server running
}

# Text Claude Code injects into user turns that isn't something the person typed.
_NOISE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<system-reminder>",
    "Caveat: The messages below were generated",
)

MAX_TEXT = 4000


def claude_home(user_home: str) -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(user_home, ".claude")


def projects_dir(user_home: str) -> str:
    return os.path.join(claude_home(user_home), "projects")


# --------------------------------------------------------------------------- #
# Locating the CLI
# --------------------------------------------------------------------------- #

def candidate_paths(user_home: str) -> List[str]:
    return [
        os.path.join(user_home, ".local", "bin", "claude"),
        os.path.join(user_home, ".claude", "local", "claude"),
        os.path.join(user_home, ".npm-global", "bin", "claude"),
        os.path.join(user_home, ".volta", "bin", "claude"),
        os.path.join(user_home, ".bun", "bin", "claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ]


def find_claude(user_home: str, configured: str = "") -> Optional[str]:
    if configured:
        return configured if os.access(configured, os.X_OK) else None
    for path in candidate_paths(user_home):
        if os.access(path, os.X_OK):
            return path
    return shutil.which("claude", path=subprocess_env(user_home)["PATH"])


def subprocess_env(user_home: str) -> Dict[str, str]:
    """Environment for the CLI.

    Decky's Python is a frozen (PyInstaller) build that points
    LD_LIBRARY_PATH at its own bundle, which breaks node and other native
    binaries, so the original value is restored. PATH gets the usual user
    install locations because the backend doesn't inherit a login shell.
    """
    env = dict(os.environ)
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env["HOME"] = user_home
    extra = [
        os.path.join(user_home, ".local", "bin"),
        os.path.join(user_home, ".npm-global", "bin"),
        os.path.join(user_home, ".volta", "bin"),
        os.path.join(user_home, ".bun", "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    current = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    env["PATH"] = os.pathsep.join(current + [p for p in extra if p not in current])
    return env


# --------------------------------------------------------------------------- #
# Transcript parsing
# --------------------------------------------------------------------------- #

def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _tool_summary(name: str, tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return name
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return f"{name}: {_clip(value, 300)}"
    return name


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def is_noise(text: str) -> bool:
    return text.lstrip().startswith(_NOISE_PREFIXES)


def render_entry(entry: Dict[str, Any], include_tools: bool = True) -> List[Dict[str, Any]]:
    """Turn one transcript / stream-json line into display messages."""
    kind = entry.get("type")
    if kind not in ("user", "assistant") or entry.get("isSidechain") or entry.get("isMeta"):
        return []
    message = entry.get("message") or {}
    role = message.get("role") or kind
    ts = entry.get("timestamp")
    content = message.get("content")
    out: List[Dict[str, Any]] = []

    if isinstance(content, str):
        if content.strip() and not is_noise(content):
            out.append({"role": role, "kind": "text", "text": _clip(content), "ts": ts})
        return out
    if not isinstance(content, list):
        return out

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if text.strip() and not (role == "user" and is_noise(text)):
                out.append({"role": role, "kind": "text", "text": _clip(text), "ts": ts})
        elif btype == "tool_use" and include_tools:
            out.append({
                "role": role,
                "kind": "tool_use",
                "text": _tool_summary(block.get("name", "tool"), block.get("input")),
                "ts": ts,
            })
        elif btype == "tool_result" and include_tools:
            text = _tool_result_text(block.get("content"))
            out.append({
                "role": "tool",
                "kind": "tool_result",
                "text": _clip(text, 600) if text else "(no output)",
                "error": bool(block.get("is_error")),
                "ts": ts,
            })
    return out


def _read_lines(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def session_summary(path: str) -> Optional[Dict[str, Any]]:
    """Cheap metadata for the session list."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    cwd = None
    first_prompt = None
    title = None
    custom_title = None
    turns = 0
    try:
        for entry in _read_lines(path):
            kind = entry.get("type")
            if kind == "custom-title" and entry.get("customTitle"):
                custom_title = entry["customTitle"]
            elif kind == "ai-title" and entry.get("aiTitle"):
                title = entry["aiTitle"]
            elif kind == "summary" and entry.get("summary") and not title:
                title = entry["summary"]
            elif kind in ("user", "assistant") and not entry.get("isSidechain"):
                cwd = cwd or entry.get("cwd")
                if kind == "user" and not entry.get("isMeta"):
                    for msg in render_entry(entry, include_tools=False):
                        turns += 1
                        if first_prompt is None:
                            first_prompt = msg["text"]
    except OSError:
        return None
    if turns == 0:
        return None
    stat = os.stat(path)
    label = custom_title or title or (first_prompt or "").splitlines()[0][:120] or session_id
    return {
        "id": session_id,
        "title": label,
        "cwd": cwd or "",
        "project": os.path.basename(cwd.rstrip("/")) if cwd else "",
        "updated": stat.st_mtime,
        "turns": turns,
    }


def find_session_file(user_home: str, session_id: str) -> Optional[str]:
    if not session_id or "/" in session_id or session_id.startswith("."):
        return None
    root = projects_dir(user_home)
    try:
        dirs = os.listdir(root)
    except OSError:
        return None
    for d in dirs:
        path = os.path.join(root, d, session_id + ".jsonl")
        if os.path.isfile(path):
            return path
    return None


def list_sessions(user_home: str, limit: int = 50) -> List[Dict[str, Any]]:
    root = projects_dir(user_home)
    files = []
    try:
        for d in os.listdir(root):
            pdir = os.path.join(root, d)
            if not os.path.isdir(pdir):
                continue
            for name in os.listdir(pdir):
                if name.endswith(".jsonl"):
                    full = os.path.join(pdir, name)
                    files.append((os.path.getmtime(full), full))
    except OSError:
        return []
    files.sort(reverse=True)
    out = []
    for _, path in files:
        summary = session_summary(path)
        if summary:
            out.append(summary)
            if len(out) >= limit:
                break
    return out


def get_session(user_home: str, session_id: str, max_messages: int = 200) -> Optional[Dict[str, Any]]:
    path = find_session_file(user_home, session_id)
    if not path:
        return None
    summary = session_summary(path) or {"id": session_id, "title": session_id, "cwd": "", "project": "", "updated": 0, "turns": 0}
    messages: List[Dict[str, Any]] = []
    for entry in _read_lines(path):
        messages.extend(render_entry(entry))
    summary["truncated"] = len(messages) > max_messages
    summary["messages"] = messages[-max_messages:]
    return summary


def recent_cwds(user_home: str, limit: int = 15) -> List[str]:
    seen: List[str] = []
    for s in list_sessions(user_home, limit=200):
        if s["cwd"] and s["cwd"] not in seen and os.path.isdir(s["cwd"]):
            seen.append(s["cwd"])
            if len(seen) >= limit:
                break
    return seen


# --------------------------------------------------------------------------- #
# Running prompts
# --------------------------------------------------------------------------- #

def build_command(claude: str, prompt: str, session_id: Optional[str], settings: Dict[str, Any]) -> List[str]:
    cmd = [claude, "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if session_id:
        cmd += ["--resume", session_id]
    mode = settings.get("permission_mode") or ""
    if mode in PERMISSION_MODES and mode:
        cmd += ["--permission-mode", mode]
    if settings.get("model"):
        cmd += ["--model", settings["model"]]
    return cmd


Emit = Callable[[str, Dict[str, Any]], Awaitable[None]]


class Job:
    def __init__(self, cwd: str, session_id: Optional[str], prompt: str):
        self.id = uuid.uuid4().hex[:12]
        self.cwd = cwd
        self.session_id = session_id
        self.prompt = prompt
        self.status = "running"  # running | done | error | stopped
        self.messages: List[Dict[str, Any]] = [
            {"role": "user", "kind": "text", "text": _clip(prompt), "ts": None}
        ]
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.cost: Optional[float] = None
        self.started = time.time()
        self.proc: Optional[asyncio.subprocess.Process] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "session_id": self.session_id,
            "status": self.status,
            "messages": self.messages,
            "result": self.result,
            "error": self.error,
            "cost": self.cost,
            "started": self.started,
        }


class JobManager:
    def __init__(self, user_home: str, emit: Emit, logger=None):
        self.user_home = user_home
        self.emit = emit
        self.logger = logger
        self.jobs: Dict[str, Job] = {}

    def running_for(self, session_id: str) -> Optional[Job]:
        for job in self.jobs.values():
            if job.session_id == session_id and job.status == "running":
                return job
        return None

    async def start(self, claude: str, prompt: str, session_id: Optional[str], cwd: str, settings: Dict[str, Any]) -> Job:
        if session_id and self.running_for(session_id):
            raise RuntimeError("This session is already working on a prompt.")
        if not os.path.isdir(cwd):
            raise RuntimeError(f"Folder not found: {cwd}")
        job = Job(cwd, session_id, prompt)
        self.jobs[job.id] = job
        self._prune()
        job.proc = await asyncio.create_subprocess_exec(
            *build_command(claude, prompt, session_id, settings),
            cwd=cwd,
            env=subprocess_env(self.user_home),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        asyncio.get_event_loop().create_task(self._pump(job))
        return job

    async def _pump(self, job: Job) -> None:
        assert job.proc and job.proc.stdout and job.proc.stderr
        stderr_task = asyncio.ensure_future(job.proc.stderr.read())
        try:
            while True:
                line = await job.proc.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                await self._handle(job, event)
            code = await job.proc.wait()
            stderr = (await stderr_task).decode("utf-8", "replace").strip()
            if job.status == "running":
                if code == 0 and not job.error:
                    job.status = "done"
                else:
                    job.status = "error"
                    job.error = job.error or stderr[-1000:] or f"Claude exited with code {code}"
        except Exception as exc:  # keep the job record consistent whatever happens
            job.status = "error"
            job.error = str(exc)
            if self.logger:
                self.logger.exception("job %s failed", job.id)
        await self.emit("claude_job", {"job_id": job.id, "type": "end", "job": job.to_dict()})

    async def _handle(self, job: Job, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            job.session_id = event.get("session_id") or job.session_id
            await self.emit("claude_job", {"job_id": job.id, "type": "init", "session_id": job.session_id})
        elif etype in ("assistant", "user"):
            msgs = render_entry(event)
            if msgs:
                job.messages.extend(msgs)
                await self.emit("claude_job", {"job_id": job.id, "type": "messages", "messages": msgs})
        elif etype == "result":
            job.result = event.get("result")
            job.cost = event.get("total_cost_usd")
            job.session_id = event.get("session_id") or job.session_id
            if event.get("is_error") or (event.get("subtype") and event.get("subtype") != "success"):
                job.error = job.result or event.get("subtype")

    async def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != "running" or not job.proc:
            return False
        job.status = "stopped"
        try:
            job.proc.terminate()
            try:
                await asyncio.wait_for(job.proc.wait(), 5)
            except asyncio.TimeoutError:
                job.proc.kill()
        except ProcessLookupError:
            pass
        return True

    async def stop_all(self) -> None:
        for job_id in list(self.jobs):
            await self.stop(job_id)

    def _prune(self, keep: int = 30) -> None:
        finished = [j for j in self.jobs.values() if j.status != "running"]
        finished.sort(key=lambda j: j.started)
        for job in finished[: max(0, len(self.jobs) - keep)]:
            self.jobs.pop(job.id, None)
