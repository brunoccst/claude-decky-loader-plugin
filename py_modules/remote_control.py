"""Claude Code Remote Control processes run by the Decky backend.

Remote Control keeps a local `claude` process connected to claude.ai so the
session can be driven from the Claude app or claude.ai/code. Run from a
Desktop Mode terminal, that process dies when the Deck switches to Game Mode
(the desktop session ends). Run from the Decky backend, which is a system
service, it survives mode switches.

Two kinds are supported:

* the server (`claude remote-control`), which lets you start new sessions in
  a folder from another device;
* one existing session (`claude --remote-control <name> --resume <id>`).

Both expect a terminal, so they run in a pseudo-terminal. When the CLI asks a
question there (such as `Enable Remote Control? (y/n)` the first time), the
question is shown in the plugin and only answered when the user says so.

Kept free of any `decky` import so it can be unit tested on a normal machine.
"""

import asyncio
import os
import re
import signal
import subprocess
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from claude_sessions import PERMISSION_MODES, subprocess_env

SERVER = "server"
EVENT = "claude_remote"
TAIL_CHARS = 4000

_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC (titles, hyperlinks)
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b[@-Z\\-_]"  # other two-byte escapes
)
_URL = re.compile(r"https://claude\.ai/code[^\s\x07\x1b'\"<>)\]]*")
# A question the CLI waits on, such as "Enable Remote Control? (y/n)" or "Trust /home/deck? [y/N]".
_QUESTION = re.compile(r"(\(y/n\)|\[y/N\]|\[Y/n\])\s*:?\s*$", re.IGNORECASE)

Emit = Callable[[str, Dict[str, Any]], Awaitable[None]]


def strip_ansi(text: str) -> str:
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    # A lone carriage return redraws the line; keep what came last.
    return "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))


def find_url(raw: str) -> Optional[str]:
    """The session URL, from the raw output (hyperlinks) or the visible text."""
    found = _URL.findall(raw) or _URL.findall(strip_ansi(raw))
    return found[-1] if found else None


def pending_question(text: str) -> Optional[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and _QUESTION.search(lines[-1]):
        return lines[-1]
    return None


def build_command(claude: str, key: str, name: str, settings: Dict[str, Any]) -> List[str]:
    mode = settings.get("permission_mode") or ""
    if key == SERVER:
        cmd = [claude, "remote-control", "--name", name]
    else:
        cmd = [claude, "--remote-control", name, "--resume", key]
    if mode and mode in PERMISSION_MODES:
        cmd += ["--permission-mode", mode]
    if settings.get("model"):
        cmd += ["--model", settings["model"]]
    return cmd


class Remote:
    def __init__(self, key: str, name: str, cwd: str):
        self.key = key
        self.name = name
        self.cwd = cwd
        self.status = "starting"  # starting | running | question | exited
        self.url: Optional[str] = None
        self.question: Optional[str] = None
        self.error: Optional[str] = None
        self.started = time.time()
        self.raw = ""
        self.proc: Optional[subprocess.Popen] = None
        self.fd: Optional[int] = None

    def tail(self) -> str:
        return strip_ansi(self.raw)[-TAIL_CHARS:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "cwd": self.cwd,
            "status": self.status,
            "url": self.url,
            "question": self.question,
            "error": self.error,
            "started": self.started,
            "output": "\n".join(self.tail().splitlines()[-12:]),
        }


class RemoteManager:
    def __init__(self, user_home: str, emit: Emit, logger=None):
        self.user_home = user_home
        self.emit = emit
        self.logger = logger
        self.remotes: Dict[str, Remote] = {}

    def get(self, key: str) -> Optional[Remote]:
        remote = self.remotes.get(key)
        return remote if remote and remote.status != "exited" else None

    def active_keys(self) -> List[str]:
        return [k for k, r in self.remotes.items() if r.status != "exited"]

    async def start(self, claude: str, key: str, name: str, cwd: str, settings: Dict[str, Any]) -> Remote:
        if self.get(key):
            return self.remotes[key]
        if not os.path.isdir(cwd):
            raise RuntimeError(f"Folder not found: {cwd}")
        remote = Remote(key, name, cwd)
        master, slave = os.openpty()
        env = subprocess_env(self.user_home)
        env.setdefault("TERM", "xterm-256color")
        try:
            remote.proc = subprocess.Popen(
                # stty sizes the terminal wide enough that the session URL isn't
                # wrapped. (Decky's bundled Python may lack termios/fcntl.)
                ["/bin/sh", "-c", 'stty cols 200 rows 50 2>/dev/null; exec "$@"', "sh"]
                + build_command(claude, key, name, settings),
                cwd=cwd,
                env=env,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        remote.fd = master
        self.remotes[key] = remote
        loop = asyncio.get_event_loop()
        loop.add_reader(master, self._on_output, remote)
        loop.create_task(self._wait(remote))
        return remote

    def _on_output(self, remote: Remote) -> None:
        try:
            data = os.read(remote.fd, 65536) if remote.fd is not None else b""
        except OSError:
            data = b""
        if not data:
            self._close_fd(remote)
            return
        remote.raw = (remote.raw + data.decode("utf-8", "replace"))[-64 * 1024:]
        url = find_url(remote.raw)
        question = pending_question(remote.tail())
        changed = url != remote.url or question != remote.question
        remote.url = url or remote.url
        remote.question = question
        if remote.status != "exited":
            remote.status = "question" if question else ("running" if remote.url else "starting")
        if changed:
            asyncio.ensure_future(self._notify(remote))

    def _close_fd(self, remote: Remote) -> None:
        if remote.fd is None:
            return
        try:
            asyncio.get_event_loop().remove_reader(remote.fd)
        except Exception:
            pass
        try:
            os.close(remote.fd)
        except OSError:
            pass
        remote.fd = None

    async def _wait(self, remote: Remote) -> None:
        assert remote.proc
        code = await asyncio.get_event_loop().run_in_executor(None, remote.proc.wait)
        # Let the reader pick up the last output before the terminal closes.
        await asyncio.sleep(0.2)
        self._close_fd(remote)
        if remote.status != "exited":
            remote.status = "exited"
            remote.question = None
            if code not in (0, -signal.SIGTERM, -signal.SIGHUP):
                lines = [line for line in remote.tail().splitlines() if line.strip()]
                remote.error = "\n".join(lines[-6:]) or f"Claude exited with code {code}"
        if self.logger:
            self.logger.info("remote control %s exited with %s", remote.key, code)
        await self._notify(remote)

    async def _notify(self, remote: Remote) -> None:
        await self.emit(EVENT, remote.to_dict())

    async def answer(self, key: str, text: str) -> bool:
        remote = self.get(key)
        if not remote or remote.fd is None:
            return False
        os.write(remote.fd, text.encode())
        remote.question = None
        remote.status = "running" if remote.url else "starting"
        await self._notify(remote)
        return True

    async def stop(self, key: str) -> bool:
        remote = self.get(key)
        if not remote or not remote.proc:
            return False
        remote.status = "exited"
        remote.question = None
        try:
            os.killpg(remote.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(loop.run_in_executor(None, remote.proc.wait), 8)
        except asyncio.TimeoutError:
            try:
                os.killpg(remote.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True

    async def stop_all(self) -> None:
        for key in list(self.remotes):
            await self.stop(key)
