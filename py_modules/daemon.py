"""Installs the Claude Sessions service as a systemd user service.

The plugin's own folder is owned by root and replaced on every plugin update,
so the service runs from a copy in `~/.local/share/claude-sessions`. The copy
is refreshed, and the service restarted, only when the plugin ships different
code, so a plugin reload doesn't interrupt Remote Control or a running prompt.

Kept free of any `decky` import so it can be unit tested on a normal machine.
"""

import os
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Tuple

from claude_sessions import subprocess_env

UNIT = "claude-sessions.service"
FILES = ("service.py", "claude_sessions.py", "remote_control.py")
BUS_WAIT = 60  # seconds


def install_dir(user_home: str) -> str:
    return os.path.join(user_home, ".local", "share", "claude-sessions")


def unit_path(user_home: str) -> str:
    return os.path.join(user_home, ".config", "systemd", "user", UNIT)


def unit_text(python: str, script: str) -> str:
    return (
        "[Unit]\n"
        "Description=Claude Sessions: Claude Code prompts and Remote Control for the Steam Deck\n"
        "\n"
        "[Service]\n"
        f"ExecStart={python} {script}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def find_python() -> Optional[str]:
    # Decky's own Python is a frozen build inside the plugin loader, so the
    # service uses the system interpreter.
    for path in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if os.access(path, os.X_OK):
            return path
    return None


def _write_if_changed(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as fh:
            if fh.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return True


def sync_files(src_dir: str, user_home: str, python: str) -> bool:
    """Copy the service code and unit file into place. True when anything changed."""
    changed = False
    dest = install_dir(user_home)
    for name in FILES:
        with open(os.path.join(src_dir, name), "rb") as fh:
            changed |= _write_if_changed(os.path.join(dest, name), fh.read())
    text = unit_text(python, os.path.join(dest, "service.py"))
    changed |= _write_if_changed(unit_path(user_home), text.encode())
    return changed


def systemctl_env(user_home: str) -> Dict[str, str]:
    env = subprocess_env(user_home)
    # Decky's backend doesn't run in a login session, so point systemctl at
    # this user's systemd instance.
    runtime = f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    return env


def _run(cmd: List[str], user_home: str) -> Tuple[bool, str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=systemctl_env(user_home))
    except Exception as exc:
        return False, str(exc)
    return out.returncode == 0, (out.stderr or out.stdout).strip()


def migrate_settings(old_file: str, new_file: str) -> None:
    """Carry settings over from the plugin's own settings folder the first time."""
    if os.path.exists(new_file) or not os.path.exists(old_file):
        return
    os.makedirs(os.path.dirname(new_file), exist_ok=True)
    shutil.copyfile(old_file, new_file)


def install(src_dir: str, user_home: str, logger=None) -> Optional[str]:
    """Install, update and start the service. Returns an error message, or None."""
    if os.getuid() == 0:
        return "The plugin backend runs as root, so it can't start a user service."
    python = find_python()
    if not python:
        return "No system Python found for the background service."
    try:
        changed = sync_files(src_dir, user_home, python)
    except OSError as exc:
        return f"Couldn't copy the service files: {exc}"
    # Decky can load plugins at boot before the user's systemd instance is up.
    bus = systemctl_env(user_home)["DBUS_SESSION_BUS_ADDRESS"].replace("unix:path=", "")
    for _ in range(int(BUS_WAIT / 0.5)):
        if os.path.exists(bus):
            break
        time.sleep(0.5)
    if changed:
        ok, err = _run(["systemctl", "--user", "daemon-reload"], user_home)
        if not ok:
            return f"systemctl daemon-reload failed: {err}"
    ok, err = _run(["systemctl", "--user", "enable", UNIT], user_home)
    if not ok:
        return f"Couldn't enable the background service: {err}"
    action = "restart" if changed else "start"
    ok, err = _run(["systemctl", "--user", action, UNIT], user_home)
    if not ok:
        return f"Couldn't {action} the background service: {err}"
    # Keep user services running between sessions (such as while switching
    # between Game Mode and Desktop Mode). Not fatal when polkit says no.
    ok, err = _run(["loginctl", "enable-linger"], user_home)
    if not ok and logger:
        logger.info("loginctl enable-linger failed: %s", err)
    return None


def uninstall(user_home: str) -> None:
    _run(["systemctl", "--user", "disable", "--now", UNIT], user_home)
    try:
        os.remove(unit_path(user_home))
    except OSError:
        pass
    shutil.rmtree(install_dir(user_home), ignore_errors=True)
    _run(["systemctl", "--user", "daemon-reload"], user_home)
