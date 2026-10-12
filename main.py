import asyncio
import json
import os
import subprocess

import decky

import claude_sessions as cs


class Plugin:
    settings: dict = dict(cs.DEFAULT_SETTINGS)
    jobs: cs.JobManager

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def _main(self):
        self._load_settings()
        self.jobs = cs.JobManager(decky.DECKY_USER_HOME, self._emit, decky.logger)
        decky.logger.info("Claude Sessions loaded")

    async def _unload(self):
        if hasattr(self, "jobs"):
            await self.jobs.stop_all()

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
            if key in settings and isinstance(settings[key], str):
                self.settings[key] = settings[key].strip()
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        with open(self._settings_path(), "w") as fh:
            json.dump(self.settings, fh, indent=2)
        return await self.get_settings()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #

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

    async def list_sessions(self, limit: int = 50) -> list:
        sessions = await asyncio.get_event_loop().run_in_executor(
            None, cs.list_sessions, decky.DECKY_USER_HOME, int(limit)
        )
        for s in sessions:
            job = self.jobs.running_for(s["id"])
            s["running_job"] = job.id if job else None
        return sessions

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
