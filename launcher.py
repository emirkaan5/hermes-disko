"""Start disko on a music folder, and quit the disko this plugin started.

The command is fixed: `<python> -m live --headless|--control --playlist <folder>`, run in the
disko checkout from the plugin's settings. No shell is involved and the only argument that comes
from the conversation is the folder, which must be an existing directory. disko keeps running
after Hermes exits; its output goes to a log file in the plugin's data folder.
"""
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import client

WAIT_S = 20.0
_started: subprocess.Popen | None = None


def _log_path() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir("disko") / "disko.log"
    except Exception:  # outside Hermes (the tests)
        return Path(tempfile.gettempdir()) / "hermes-disko.log"


def command(folder: Path, window: bool) -> tuple[list[str], Path] | str:
    """The argv and working directory, or a sentence saying what is missing."""
    root = client.setting("disko_dir", "DISKO_DIR")
    if not root:
        return ("the plugin doesn't know where disko is installed. Set `disko_dir` in the disko plugin's "
                "settings to your disko checkout (the folder that contains live/).")
    root = Path(root).expanduser()
    if not (root / "live" / "__main__.py").exists():
        return f"{root} isn't a disko checkout (no live/__main__.py)"
    python = Path(client.setting("disko_python", "DISKO_PYTHON", root / ".venv-live" / "bin" / "python")).expanduser()
    if not python.exists():
        return f"disko's Python isn't at {python}; set `disko_python` in the plugin's settings"
    mode = "--control" if window else "--headless"
    return [str(python), "-m", "live", mode, "--playlist", str(folder)], root


def launch(folder: str, window: bool = False, wait_s: float = WAIT_S) -> dict:
    global _started
    if client.setting("control_url", "DISKO_CONTROL_URL"):
        return {"error": "this plugin is pointed at a remote disko (control_url); start it on that machine"}
    if "error" not in client.status(timeout=3):
        return {"error": "disko is already running; use disko_status, or disko_quit first to change folder"}
    if not folder:
        return {"error": "which music folder? Give the path to a folder of audio files."}
    path = Path(folder).expanduser()
    if not path.is_dir():
        return {"error": f"{path} isn't a folder on this machine"}
    built = command(path.resolve(), window)
    if isinstance(built, str):
        return {"error": built}
    argv, cwd = built
    log = _log_path()
    try:
        with open(log, "ab") as out:
            _started = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                        start_new_session=True)
    except OSError as e:
        return {"error": f"couldn't start disko: {e}"}
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _started.poll() is not None:
            return {"error": f"disko exited right away (code {_started.returncode})", "log": str(log),
                    "last_output": _tail(log)}
        status = client.status(timeout=3)
        if "error" not in status:
            return {"launched": True, "folder": str(path), "window": window, **status}
        time.sleep(0.5)
    return {"launched": True, "ready": False, "folder": str(path), "window": window, "log": str(log),
            "note": "disko is still getting ready (a new folder is analysed first, about half a second per "
                    "track). Check disko_status again shortly.", "last_output": _tail(log)}


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


def _tail(path: Path, lines: int = 6) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
