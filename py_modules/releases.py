"""GitHub release lookup for the plugin's updater.

Done in the backend with the standard library so it has a real timeout and
logs its failures, instead of going through Decky's fetch proxy from the UI.
"""

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List

RELEASES_URL = "https://api.github.com/repos/brunoccst/claude-decky-loader-plugin/releases?per_page=10"
ASSET_NAME = "claude-sessions.zip"
TIMEOUT = 15

_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # SteamOS / Arch
    "/etc/pki/tls/certs/ca-bundle.crt",
)


def _ssl_context() -> ssl.SSLContext:
    if os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context()
    # Decky's frozen Python may not find the system CA store on its own.
    try:
        import certifi  # bundled with Decky's aiohttp

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for path in _CA_FILES:
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def parse_releases(releases: List[Dict[str, Any]], asset_name: str = ASSET_NAME) -> List[Dict[str, str]]:
    """Published releases that carry the plugin zip, as {version, url, hash}."""
    out = []
    for release in releases:
        if release.get("draft"):
            continue
        asset = next((a for a in release.get("assets") or [] if a.get("name") == asset_name), None)
        if not asset:
            continue
        out.append({
            "version": str(release.get("tag_name", "")).lstrip("v"),
            "url": asset.get("browser_download_url", ""),
            "hash": str(asset.get("digest") or "").replace("sha256:", "", 1),
        })
    return out


def fetch_releases(url: str = RELEASES_URL, timeout: float = TIMEOUT) -> List[Dict[str, str]]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "claude-sessions-decky-plugin",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as res:
            return parse_releases(json.load(res))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("GitHub returned 404. The repository has to be public for updates.") from exc
        raise RuntimeError(f"GitHub returned {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Couldn't reach GitHub: {exc.reason}") from exc
