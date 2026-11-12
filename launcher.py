"""Start disko on a music folder or a Spotify playlist, and quit the disko this plugin started.

The command is fixed: `<python> -m live --headless|--control --playlist <folder>` (or
`--spotify <playlist>`), run in the disko checkout. That checkout comes from the `disko_dir`
setting when set, otherwise from the install record disko writes on every start (<control
folder>/install.json: its folder and Python, not a credential). No shell is involved and the only
argument that comes from the conversation is the folder, which must be an existing directory, or
the Spotify playlist, which must be `liked` or a 22-character Spotify id (a spotify: URI or an
open.spotify.com link is reduced to its id here). disko keeps running after Hermes exits; its
output goes to a log file in the plugin's data folder.

disko gets a minimal environment, never Hermes's own (which holds every provider API key): the basics
a process needs (PATH, HOME, USER, LOGNAME, LANG, LC_*, TMPDIR) and only the variables disko documents
(DISKO_*, KEV_URL, KEV_RUN, HF_HOME, HF_HUB_CACHE), less the plugin's own DISKO_CONTROL_URL and
DISKO_CONTROL_TOKEN. DISKO_CONTROL_DIR is set to the folder this plugin talks to.

Spotify audio is the listener's own switch in disko (`python -m live.sources.spotify audio on`).
The plugin never turns it on; when it is off, disko refuses to start and the launch says so.
"""
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from . import client

WAIT_S = 20.0
INSTALL_NAME, INSTALL_VERSION = "install.json", 1
NOT_FOUND = ("the plugin can't find disko on this machine. Open disko once here (it then registers itself), "
             "or set `disko_dir` in the disko plugin's settings to your disko checkout (the folder that "
             "contains live/).")
SPOTIFY_ID = re.compile(r"[0-9A-Za-z]{22}")
SPOTIFY_AUDIO_OFF = "Spotify audio is off"
ENV_NAMES = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR",
                       "KEV_URL", "KEV_RUN", "HF_HOME", "HF_HUB_CACHE"})
ENV_PREFIXES = ("LC_", "DISKO_")
ENV_DENIED = frozenset({"DISKO_CONTROL_URL", "DISKO_CONTROL_TOKEN"})  # the plugin's settings, not disko's
_started: subprocess.Popen | None = None


def _log_path() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir("disko") / "disko.log"
    except Exception:  # outside Hermes (the tests)
        return Path(tempfile.gettempdir()) / "hermes-disko.log"


def _install_record() -> tuple[Path, Path] | None:
    """(root, python) from disko's install.json; None for anything missing, odd or from another version."""
    try:
        record = json.loads((client.folder() / INSTALL_NAME).read_text())
        version, root, python = record["version"], record["root"], record["python"]
        if type(version) is not int or version != INSTALL_VERSION:
            return None
        if not (isinstance(root, str) and isinstance(python, str)):
            return None
        root, python = Path(root), Path(python)
        if not (root.is_absolute() and python.is_absolute()):
            return None
        return root, python
    except Exception:
        return None


def spotify_playlist(text: str) -> str | None:
    """`liked` or a bare playlist id out of what the user said; None if it is neither."""
    text = (text or "").strip()
    if text.lower() in ("liked", "liked songs"):
        return "liked"
    if text.startswith("spotify:playlist:"):
        text = text.removeprefix("spotify:playlist:")
    elif "open.spotify.com" in text:
        parts = [p for p in urlparse(text if "://" in text else "https://" + text).path.split("/") if p]
        text = parts[parts.index("playlist") + 1] if "playlist" in parts[:-1] else ""
    return text if SPOTIFY_ID.fullmatch(text) else None


def environment(source=None) -> dict:
    """What disko is started with: an allowlist of the current environment (see the module docstring)."""
    source = os.environ if source is None else source
    env = {name: value for name, value in source.items()
           if (name in ENV_NAMES or name.startswith(ENV_PREFIXES)) and name not in ENV_DENIED}
    env["DISKO_CONTROL_DIR"] = str(client.folder())
    return env


def command(source: list[str], window: bool) -> tuple[list[str], Path] | str:
    """The argv and working directory, or a sentence saying what is missing."""
    override = client.setting("disko_dir", "DISKO_DIR")
    if override:  # the user's explicit choice wins over what disko registered
        root = Path(override).expanduser()
        if not (root / "live" / "__main__.py").exists():
            return f"{root} isn't a disko checkout (no live/__main__.py); fix `disko_dir` in the plugin's settings"
        python = root / ".venv-live" / "bin" / "python"
    else:
        found = _install_record()
        if found is None or not (found[0] / "live" / "__main__.py").exists():
            return NOT_FOUND
        root, python = found
        if not (client.setting("disko_python", "DISKO_PYTHON") or python.exists()):
            return NOT_FOUND  # a record left by a disko that has since moved
    python = Path(client.setting("disko_python", "DISKO_PYTHON", python)).expanduser()
    if not python.exists():
        return f"disko's Python isn't at {python}; set `disko_python` in the plugin's settings"
    mode = "--control" if window else "--headless"
    return [str(python), "-m", "live", mode, *source], root


def launch(folder: str = "", window: bool = False, wait_s: float = WAIT_S, spotify: str = "") -> dict:
    global _started
    if client.setting("control_url", "DISKO_CONTROL_URL"):
        return {"error": "this plugin is pointed at a remote disko (control_url); start it on that machine"}
    if "error" not in client.status(timeout=3):
        return {"error": "disko is already running; use disko_status, or disko_quit first to change the music"}
    if folder and spotify:
        return {"error": "give a music folder or a Spotify playlist, not both"}
    if spotify:
        playlist = spotify_playlist(spotify)
        if playlist is None:
            return {"error": f"{spotify!r} isn't a Spotify playlist: give `liked` (Liked Songs), a playlist "
                             "link or its id"}
        source, opened = ["--spotify", playlist], {"spotify": playlist}
    elif folder:
        path = Path(folder).expanduser()
        if not path.is_dir():
            return {"error": f"{path} isn't a folder on this machine"}
        source, opened = ["--playlist", str(path.resolve())], {"folder": str(path)}
    else:
        return {"error": "which music? Give the path to a folder of audio files, or a Spotify playlist."}
    built = command(source, window)
    if isinstance(built, str):
        return {"error": built}
    argv, cwd = built
    log = _log_path()
    try:
        with open(log, "ab") as out:
            start = out.tell()  # the log is shared across launches: read only what this one writes
            _started = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=out, start_new_session=True)
    except OSError as e:
        return {"error": f"couldn't start disko: {e}"}
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _started.poll() is not None:
            tail = _tail(log, start)
            if SPOTIFY_AUDIO_OFF in tail:
                return {"error": "Spotify audio is off in disko, so it can't play Spotify tracks. Only the "
                                 "user can turn it on, on the Mac: on the Spotify page of disko's welcome "
                                 "screen, or with `.venv-live/bin/python -m live.sources.spotify audio on` in "
                                 "the disko folder. Both explain what it means first.", "log": str(log)}
            return {"error": f"disko exited right away (code {_started.returncode})", "log": str(log),
                    "last_output": tail}
        status = client.status(timeout=3)
        if "error" not in status:
            return {"launched": True, **opened, "window": window, **status}
        time.sleep(0.5)
    getting_ready = ("disko is fetching a 30 s preview of each track in the Spotify playlist to learn its "
                     "tempo and key" if spotify else
                     "a new folder is analysed first, about half a second per track")
    return {"launched": True, "ready": False, **opened, "window": window, "log": str(log),
            "note": f"disko is still getting ready ({getting_ready}). Check disko_status again shortly.",
            "last_output": _tail(log, start)}


def quit() -> dict:
    """Stop the disko this Hermes process launched. One started by hand is left alone."""
    global _started
    if _started is None or _started.poll() is not None:
        _started = None
        if "error" not in client.status(timeout=3):
            return {"error": "that disko wasn't started by this plugin; quit it on the Mac (or use disko_stop "
                             "to stop the music)"}
        return {"quit": True, "note": "disko wasn't running"}
    try:
        os.killpg(_started.pid, signal.SIGTERM)
        _started.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(_started.pid, signal.SIGKILL)
        _started.wait(timeout=5)
    except OSError as e:
        return {"error": f"couldn't quit disko: {e}"}
    _started = None
    return {"quit": True}


def _tail(path: Path, start: int = 0, lines: int = 6) -> str:
    """The last lines written to the log from byte `start` on."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            text = f.read().decode(errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""
