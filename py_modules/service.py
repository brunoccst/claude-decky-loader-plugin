"""The Claude Sessions service: prompts, Remote Control and settings.

This runs as a systemd user service (see `daemon.py`), so Remote Control and
running prompts keep going when Decky Loader restarts, updates, or breaks
after a Steam update. The Decky plugin is only a front end: it talks to the
service over a Unix socket with newline-delimited JSON:

* request  `{"id": 1, "method": "list_sessions", "args": {"limit": 50}}`
* response `{"id": 1, "result": ...}` or `{"id": 1, "error": "message"}`
* event    `{"event": "claude_job", "payload": {...}}`, sent to every client

When the service can't be installed, the plugin runs `Service` in its own
process instead, as earlier versions did.

Uses only the standard library and has no `decky` import, so it runs on the
Deck's system Python and can be unit tested anywhere.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from typing import Any, Awaitable, Callable, Dict, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import claude_sessions as cs  # noqa: E402
import remote_control as rc  # noqa: E402

Emit = Callable[[str, Dict[str, Any]], Awaitable[None]]

# Methods the plugin may call, local or over the socket.
METHODS = (
    "get_settings",
    "set_settings",
    "get_status",
    "list_sessions",
    "list_projects",
    "get_session",
    "recent_folders",
    "send_prompt",
    "get_job",
    "list_jobs",
    "stop_job",
    "list_remotes",
    "get_remote",
    "start_remote",
    "answer_remote",
    "stop_remote",
)

NO_CLI = "Claude Code CLI not found. Install it on this device or set its path in settings."


def socket_path() -> str:
    # Not $XDG_RUNTIME_DIR: Decky's backend may carry another user's value.
    return os.environ.get("CLAUDE_SESSIONS_SOCKET") or f"/run/user/{os.getuid()}/claude-sessions.sock"


def settings_path(user_home: str) -> str:
    return os.path.join(user_home, ".config", "claude-sessions", "settings.json")


class Service:
    def __init__(self, user_home: str, settings_file: str, emit: Emit, logger=None):
        self.home = user_home
        self.settings_file = settings_file
        self.emit = emit
        self.logger = logger or logging.getLogger("claude-sessions")
        self.settings: Dict[str, Any] = dict(cs.DEFAULT_SETTINGS)
        self.jobs = cs.JobManager(user_home, emit, self.logger)
        self.remotes = rc.RemoteManager(user_home, emit, self.logger)
        self._load_settings()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if self.settings.get("remote_server") == "1":
            result = await self.start_remote(rc.SERVER)
            if result.get("error"):
                self.logger.warning("Remote Control server didn't start: %s", result["error"])

    async def stop(self) -> None:
        await self.jobs.stop_all()
        await self.remotes.stop_all()

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #

    def _load_settings(self) -> None:
        try:
            with open(self.settings_file) as fh:
                stored = json.load(fh)
            self.settings = {**cs.DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in cs.DEFAULT_SETTINGS}}
        except (OSError, ValueError):
            self.settings = dict(cs.DEFAULT_SETTINGS)

    def _save_settings(self) -> None:
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        with open(self.settings_file, "w") as fh:
            json.dump(self.settings, fh, indent=2)

    async def get_settings(self) -> dict:
        return {**self.settings, "permission_modes": cs.PERMISSION_MODES}

    async def set_settings(self, settings: dict) -> dict:
        for key in cs.DEFAULT_SETTINGS:
            if key == "remote_server":  # owned by start_remote / stop_remote
                continue
            if key in settings and isinstance(settings[key], str):
                self.settings[key] = settings[key].strip()
        self._save_settings()
        return await self.get_settings()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    def _claude(self) -> Optional[str]:
        return cs.find_claude(self.home, self.settings.get("claude_path", ""))

    async def get_status(self) -> dict:
        claude = self._claude()
        version = None
        if claude:
            try:
                out = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [claude, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        env=cs.subprocess_env(self.home),
                    ),
                )
                text = (out.stdout or out.stderr).strip()
                version = text.splitlines()[0] if text else None
            except Exception as exc:
                self.logger.warning("claude --version failed: %s", exc)
        return {
            "claude_path": claude,
            "version": version,
            "projects_dir": cs.projects_dir(self.home),
            "has_projects": os.path.isdir(cs.projects_dir(self.home)),
            "home": self.home,
        }

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    async def list_sessions(self, limit: int = 50, cwd: str = "") -> list:
        sessions = await asyncio.get_event_loop().run_in_executor(
            None, cs.list_sessions, self.home, int(limit), cwd or ""
        )
        remote = set(self.remotes.active_keys())
        for s in sessions:
            job = self.jobs.running_for(s["id"])
            s["running_job"] = job.id if job else None
            s["remote"] = s["id"] in remote
        return sessions

    async def list_projects(self) -> list:
        return await asyncio.get_event_loop().run_in_executor(None, cs.list_projects, self.home)

    async def get_session(self, session_id: str, max_messages: int = 200):
        session = await asyncio.get_event_loop().run_in_executor(
            None, cs.get_session, self.home, session_id, int(max_messages)
        )
        if session:
            job = self.jobs.running_for(session_id)
            session["running_job"] = job.to_dict() if job else None
        return session

    async def recent_folders(self) -> list:
        folders = await asyncio.get_event_loop().run_in_executor(None, cs.recent_cwds, self.home)
        default = self.settings.get("default_cwd") or self.home
        return [default] + [f for f in folders if f != default]

    # ------------------------------------------------------------------ #
    # Prompts
    # ------------------------------------------------------------------ #

    async def send_prompt(self, session_id: str, cwd: str, prompt: str) -> dict:
        """Continue `session_id`, or start a new session in `cwd` when it is empty."""
        prompt = (prompt or "").strip()
        if not prompt:
            return {"error": "Type a prompt first."}
        claude = self._claude()
        if not claude:
            return {"error": NO_CLI}
        if session_id:
            session = cs.get_session(self.home, session_id, 1)
            if not session:
                return {"error": "Session not found."}
            cwd = session["cwd"] or cwd
            if self.remotes.get(session_id):
                return {"error": "This session is on Remote Control. Continue it from the Claude app, or stop Remote Control first."}
        cwd = cwd or self.settings.get("default_cwd") or self.home
        try:
            job = await self.jobs.start(claude, prompt, session_id or None, cwd, self.settings)
        except Exception as exc:
            self.logger.exception("send_prompt failed")
            return {"error": str(exc)}
        return {"job": job.to_dict()}

    async def get_job(self, job_id: str):
        job = self.jobs.jobs.get(job_id)
        return job.to_dict() if job else None

    async def list_jobs(self) -> list:
        return [j.to_dict() for j in self.jobs.jobs.values() if j.status == "running"]

    async def stop_job(self, job_id: str) -> bool:
        return await self.jobs.stop(job_id)

    # ------------------------------------------------------------------ #
    # Remote Control
    # ------------------------------------------------------------------ #

    async def list_remotes(self) -> list:
        return [r.to_dict() for r in self.remotes.remotes.values() if r.status != "exited"]

    async def get_remote(self, key: str):
        remote = self.remotes.remotes.get(key)
        return remote.to_dict() if remote else None

    async def start_remote(self, key: str) -> dict:
        """Start the Remote Control server (key "server") or put one session on Remote Control."""
        claude = self._claude()
        if not claude:
            return {"error": NO_CLI}
        if key == rc.SERVER:
            name = "Steam Deck"
            cwd = self.settings.get("default_cwd") or self.home
            self.settings["remote_server"] = "1"
            self._save_settings()
        else:
            session = cs.get_session(self.home, key, 1)
            if not session:
                return {"error": "Session not found."}
            if self.jobs.running_for(key):
                return {"error": "Wait for the current prompt to finish first."}
            name = (session["title"] or "Steam Deck session")[:60]
            cwd = session["cwd"] or self.home
        try:
            remote = await self.remotes.start(claude, key, name, cwd, self.settings)
        except Exception as exc:
            self.logger.exception("start_remote failed")
            return {"error": str(exc)}
        return {"remote": remote.to_dict()}

    async def answer_remote(self, key: str, text: str) -> bool:
        return await self.remotes.answer(key, text)

    async def stop_remote(self, key: str) -> bool:
        if key == rc.SERVER:
            self.settings["remote_server"] = ""
            self._save_settings()
        return await self.remotes.stop(key)

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #

    async def call(self, method: str, args: Any = None) -> Any:
        """Run one of METHODS with positional (list) or keyword (dict) arguments."""
        if method not in METHODS:
            raise ValueError(f"Unknown method: {method}")
        if isinstance(args, dict):
            return await getattr(self, method)(**args)
        return await getattr(self, method)(*(args or []))


# --------------------------------------------------------------------------- #
# Socket server
# --------------------------------------------------------------------------- #


class Server:
    """Serves a `Service` on a Unix socket and broadcasts its events."""

    def __init__(self, path: str):
        self.path = path
        self.service: Optional[Service] = None
        self.clients: Set[asyncio.StreamWriter] = set()
        self._server: Optional[asyncio.AbstractServer] = None

    async def broadcast(self, event: str, payload: Dict[str, Any]) -> None:
        line = (json.dumps({"event": event, "payload": payload}) + "\n").encode()
        for writer in list(self.clients):
            try:
                writer.write(line)
                await writer.drain()
            except Exception:
                self.clients.discard(writer)

    async def start(self, service: Service) -> None:
        self.service = service
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._client, path=self.path, limit=16 * 1024 * 1024)
        os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for writer in list(self.clients):
            writer.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                asyncio.ensure_future(self._handle(line, writer))
        except Exception:
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    async def _handle(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            request = json.loads(line)
            req_id = request.get("id")
        except ValueError:
            return
        try:
            assert self.service
            reply = {"id": req_id, "result": await self.service.call(request.get("method", ""), request.get("args"))}
        except Exception as exc:
            reply = {"id": req_id, "error": str(exc) or type(exc).__name__}
        try:
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
        except Exception:
            self.clients.discard(writer)


# --------------------------------------------------------------------------- #
# Client (used by the Decky plugin)
# --------------------------------------------------------------------------- #


class Client:
    """Calls the service over its socket and passes its events to `on_event`."""

    def __init__(self, path: str, on_event: Emit, logger=None):
        self.path = path
        self.on_event = on_event
        self.logger = logger
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Future] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    async def connect(self, attempts: int = 1, delay: float = 0.5) -> bool:
        async with self._lock:
            for attempt in range(attempts):
                if self._writer:
                    return True
                try:
                    reader, writer = await asyncio.open_unix_connection(self.path, limit=16 * 1024 * 1024)
                except OSError:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(delay)
                    continue
                self._writer = writer
                self._reader_task = asyncio.ensure_future(self._read(reader))
                return True
            return False

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "event" in msg:
                    try:
                        await self.on_event(msg["event"], msg.get("payload") or {})
                    except Exception:
                        if self.logger:
                            self.logger.exception("event handler failed")
                    continue
                future = self._pending.pop(msg.get("id"), None)
                if future and not future.done():
                    if "error" in msg:
                        future.set_exception(RuntimeError(msg["error"]))
                    else:
                        future.set_result(msg.get("result"))
        except Exception:
            pass
        finally:
            self._drop()

    def _drop(self) -> None:
        if self._writer:
            self._writer.close()
        self._writer = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("The Claude Sessions service stopped. Try again."))
        self._pending.clear()

    async def call(self, method: str, args: Any = None, timeout: float = 60) -> Any:
        if not await self.connect(attempts=6):
            raise RuntimeError("The Claude Sessions service isn't running.")
        self._next_id += 1
        req_id = self._next_id
        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        assert self._writer
        self._writer.write((json.dumps({"id": req_id, "method": method, "args": args or []}) + "\n").encode())
        await self._writer.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(req_id, None)

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        self._drop()


# --------------------------------------------------------------------------- #
# Entry point (systemd)
# --------------------------------------------------------------------------- #


async def run(path: str, user_home: str, settings_file: str) -> None:
    logger = logging.getLogger("claude-sessions")
    server = Server(path)
    service = Service(user_home, settings_file, server.broadcast, logger)
    await server.start(service)
    logger.info("Claude Sessions service listening on %s", path)
    await service.start()

    stopping = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    await stopping.wait()
    logger.info("stopping")
    await service.stop()
    await server.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    home = os.path.expanduser("~")
    asyncio.run(run(socket_path(), home, settings_path(home)))


if __name__ == "__main__":
    main()
