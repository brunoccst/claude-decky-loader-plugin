import asyncio
import os

import decky

import daemon
import releases
import service as svc


class Plugin:
    """Front end for the Claude Sessions service.

    Prompts, Remote Control and settings live in `py_modules/service.py`,
    which normally runs as a systemd user service so it keeps going when
    Decky restarts, updates or breaks. This class forwards the UI's calls to
    it. If the service can't be set up, the same code runs in this process.
    """

    backend = None  # svc.Client, or svc.Service when running in-process
    service_error = None

    async def _main(self):
        home = decky.DECKY_USER_HOME
        settings_file = svc.settings_path(home)
        daemon.migrate_settings(os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json"), settings_file)

        src = os.path.dirname(os.path.abspath(svc.__file__))
        loop = asyncio.get_event_loop()
        self.service_error = await loop.run_in_executor(None, daemon.install, src, home, decky.logger)
        # Connect even when installing failed: systemd may already be running
        # the service from an earlier install.
        client = svc.Client(svc.socket_path(), self._emit, decky.logger)
        if await client.connect(attempts=20, delay=0.5):
            if self.service_error:
                decky.logger.warning("service install: %s", self.service_error)
            self.service_error = None
            self.backend = client
            decky.logger.info("Claude Sessions loaded; using the background service")
            return
        self.service_error = self.service_error or (
            "The background service didn't start. See `journalctl --user -u claude-sessions`."
        )

        decky.logger.warning("background service unavailable, running in the plugin: %s", self.service_error)
        local = svc.Service(home, settings_file, self._emit, decky.logger)
        self.backend = local
        await local.start()

    async def _unload(self):
        # The background service keeps running across plugin reloads and
        # updates; only an in-process fallback stops with the plugin.
        if isinstance(self.backend, svc.Service):
            await self.backend.stop()
        elif isinstance(self.backend, svc.Client):
            await self.backend.close()

    async def _uninstall(self):
        await asyncio.get_event_loop().run_in_executor(None, daemon.uninstall, decky.DECKY_USER_HOME)

    async def _emit(self, event: str, payload: dict):
        await decky.emit(event, payload)

    async def _call(self, method: str, args):
        if self.backend is None:
            raise RuntimeError("Claude Sessions is still starting. Try again in a moment.")
        return await self.backend.call(method, args)

    # ------------------------------------------------------------------ #
    # Plugin-only calls
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

    async def get_status(self) -> dict:
        status = await self._call("get_status", [])
        status["service"] = "background" if isinstance(self.backend, svc.Client) else "plugin"
        status["service_error"] = self.service_error
        return status


def _forward(method: str):
    async def call(self, *args, **kwargs):
        return await self._call(method, kwargs or list(args))

    call.__name__ = method
    return call


# Everything else the UI calls is answered by the service.
for _method in svc.METHODS:
    if _method != "get_status":
        setattr(Plugin, _method, _forward(_method))
