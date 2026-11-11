"""Talks to a running disko over its local control interface: HTTP+JSON on a Unix socket in
~/.disko. disko checks that whoever connects to that socket is the same OS user, so local
requests carry no token and the plugin reads no credential file.

When Hermes can't see that folder (a container, another machine), `control_url` points at a
disko started with --control-tcp and the token comes from Hermes's secret store.

Standard library only. Nothing here raises: every call returns a dict, {"error": ...} on failure.
"""
import http.client
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlencode, urlparse

TIMEOUT_S = 25.0
SOCKET_NAME = "control.sock"
NOT_RUNNING = ("disko isn't running. Launch it with disko_launch, or open disko on the Mac "
               "(or run `python -m live --headless --playlist FOLDER`).")
OLD_DISKO = ("this disko is an older version that still wants a token on its local socket; update disko "
             "(the plugin doesn't read disko's token file)")

_settings = lambda key, default=None: default  # replaced by register() with ctx.get_config


def configure(get_config):
    global _settings
    _settings = get_config


def setting(key: str, env: str | None = None, default=""):
    """A plugin setting, then its environment variable, then the default."""
    try:
        value = _settings(key, None)
    except Exception:
        value = None
    if value in (None, "") and env:
        value = os.environ.get(env)
    return default if value in (None, "") else value


def _secret(name: str) -> str | None:
    try:
        from agent.secret_scope import get_secret
    except ImportError:  # outside Hermes (the tests)
        return os.environ.get(name)
    return get_secret(name)


def folder() -> Path:
    return Path(setting("control_dir", "DISKO_CONTROL_DIR", Path.home() / ".disko")).expanduser()


def socket_present() -> bool:
    return bool(setting("control_url", "DISKO_CONTROL_URL")) or (folder() / SOCKET_NAME).exists()


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


def request(method: str, path: str, body: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
    try:
        url = setting("control_url", "DISKO_CONTROL_URL")
        headers = {}
        if url:
            token = _secret("DISKO_CONTROL_TOKEN")
            if not token:
                return {"error": "a control URL is set but its token isn't (set Control token in the plugin's "
                                 "settings, or DISKO_CONTROL_TOKEN)"}
            headers["Authorization"] = f"Bearer {token}"
            u = urlparse(url)
            conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
        else:
            sock = folder() / SOCKET_NAME
            if not sock.exists():
                return {"error": NOT_RUNNING, "running": False}
            conn = _UnixConnection(str(sock), timeout)  # disko checks we're the same user; no token
        try:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
        finally:
            conn.close()
        if response.status == 401 and not url:  # only an older disko wants a token locally
            return {"error": OLD_DISKO}
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            payload = {"result": payload}
        if response.status != 200 and "error" not in payload:
            payload = {"error": f"disko answered {response.status}", **payload}
        return payload
    except (ConnectionRefusedError, FileNotFoundError):
        return {"error": NOT_RUNNING, "running": False}
    except Exception as e:
        return {"error": f"couldn't reach disko: {type(e).__name__}: {e}"}


def status(timeout: float = TIMEOUT_S) -> dict:
    return request("GET", "/status", timeout=timeout)


def tracks(query: str = "", limit: int = 20) -> dict:
    return request("GET", "/tracks?" + urlencode({"q": query, "limit": limit}))


def start(track: str | None = None) -> dict:
    return request("POST", "/start", {"track": track or None})


def energy(direction: str) -> dict:
    return request("POST", "/energy", {"energy": direction})


def post(command: str) -> dict:
    """pause, resume, stop or pick-again."""
    return request("POST", f"/{command}", {})
