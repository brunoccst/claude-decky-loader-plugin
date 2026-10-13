import asyncio
import json
import os
import subprocess

import decky

import claude_sessions as cs
import releases
import remote_control as rc


class Plugin:
    settings: dict = dict(cs.DEFAULT_SETTINGS)
    jobs: cs.JobManager
    remotes: rc.RemoteManager

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def _main(self):
        self._load_settings()
        self.jobs = cs.JobManager(decky.DECKY_USER_HOME, self._emit, decky.logger)
        self.remotes = rc.RemoteManager(decky.DECKY_USER_HOME, self._emit, decky.logger)
        decky.logger.info("Claude Sessions loaded")
        if self.settings.get("remote_server") == "1":
            result = await self.start_remote(rc.SERVER)
            if result.get("error"):
                decky.logger.warning("Remote Control server didn't start: %s", result["error"])

    async def _unload(self):
        if hasattr(self, "jobs"):
            await self.jobs.stop_all()
        if hasattr(self, "remotes"):
            await self.remotes.stop_all()

    async def _uninstall(self):
        pass

    async def _emit(self, event: str, payload: dict):
        await decky.emit(event, payload)

    # ------------------------------------------------------------------ #
    # Settings
    # ------------------------------------------------------------------ #

    def _settings_path(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

    def _load_settings(self):
        try:
            with open(self._settings_path()) as fh:
                stored = json.load(fh)
            self.settings = {**cs.DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in cs.DEFAULT_SETTINGS}}
        except (OSError, ValueError):
            self.settings = dict(cs.DEFAULT_SETTINGS)

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

    def _save_settings(self):
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        with open(self._settings_path(), "w") as fh:
            json.dump(self.settings, fh, indent=2)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

    async def get_version(self) -> str:
        return decky.DECKY_PLUGIN_VERSION

    async def get_releases(self) -> dict:
        try:
            found = await asyncio.get_event_loop().run_in_executor(None, releases.fetch_releases)
            return {"releases": found}
        except Exception as exc:
            decky.logger.warning("update check failed: %s", exc)
            return {"error": str(exc)}

    def _claude(self):
        return cs.find_claude(decky.DECKY_USER_HOME, self.settings.get("claude_path", ""))

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
                        env=cs.subprocess_env(decky.DECKY_USER_HOME),
                    ),
                )
                version = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else None
            except Exception as exc:
                decky.logger.warning("claude --version failed: %s", exc)
        return {
            "claude_path": claude,
            "version": version,
            "projects_dir": cs.projects_dir(decky.DECKY_USER_HOME),
            "has_projects": os.path.isdir(cs.projects_dir(decky.DECKY_USER_HOME)),
            "home": decky.DECKY_USER_HOME,
        }

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    async def list_sessions(self, limit: int = 50, cwd: str = "") -> list:
        sessions = await asyncio.get_event_loop().run_in_executor(
            None, cs.list_sessions, decky.DECKY_USER_HOME, int(limit), cwd or ""
        )
        remote = set(self.remotes.active_keys())
        for s in sessions:
            job = self.jobs.running_for(s["id"])
            s["running_job"] = job.id if job else None
            s["remote"] = s["id"] in remote
        return sessions

    async def list_projects(self) -> list:
        return await asyncio.get_event_loop().run_in_executor(None, cs.list_projects, decky.DECKY_USER_HOME)

    async def get_session(self, session_id: str, max_messages: int = 200):
        session = await asyncio.get_event_loop().run_in_executor(
            None, cs.get_session, decky.DECKY_USER_HOME, session_id, int(max_messages)
        )
        if session:
            job = self.jobs.running_for(session_id)
            session["running_job"] = job.to_dict() if job else None
        return session

    async def recent_folders(self) -> list:
        folders = await asyncio.get_event_loop().run_in_executor(None, cs.recent_cwds, decky.DECKY_USER_HOME)
        default = self.settings.get("default_cwd") or decky.DECKY_USER_HOME
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
            return {"error": "Claude Code CLI not found. Install it on this device or set its path in settings."}
        if session_id:
            session = cs.get_session(decky.DECKY_USER_HOME, session_id, 1)
            if not session:
                return {"error": "Session not found."}
            cwd = session["cwd"] or cwd
            if self.remotes.get(session_id):
                return {"error": "This session is on Remote Control. Continue it from the Claude app, or stop Remote Control first."}
        cwd = cwd or self.settings.get("default_cwd") or decky.DECKY_USER_HOME
        try:
            job = await self.jobs.start(claude, prompt, session_id or None, cwd, self.settings)
        except Exception as exc:
            decky.logger.exception("send_prompt failed")
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
            return {"error": "Claude Code CLI not found. Install it on this device or set its path in settings."}
        if key == rc.SERVER:
            name = "Steam Deck"
            cwd = self.settings.get("default_cwd") or decky.DECKY_USER_HOME
            self.settings["remote_server"] = "1"
            self._save_settings()
        else:
            session = cs.get_session(decky.DECKY_USER_HOME, key, 1)
            if not session:
                return {"error": "Session not found."}
            if self.jobs.running_for(key):
                return {"error": "Wait for the current prompt to finish first."}
            name = (session["title"] or "Steam Deck session")[:60]
            cwd = session["cwd"] or decky.DECKY_USER_HOME
        try:
            remote = await self.remotes.start(claude, key, name, cwd, self.settings)
        except Exception as exc:
            decky.logger.exception("start_remote failed")
            return {"error": str(exc)}
        return {"remote": remote.to_dict()}

    async def answer_remote(self, key: str, text: str) -> bool:
        return await self.remotes.answer(key, text)

    async def stop_remote(self, key: str) -> bool:
        if key == rc.SERVER:
            self.settings["remote_server"] = ""
            self._save_settings()
        return await self.remotes.stop(key)
