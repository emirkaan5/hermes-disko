"""/disko in any Hermes chat and `hermes disko` in the terminal: the same controls without a model turn."""
import shlex

from . import client, launcher, render

USAGE = """/disko                     what's playing
/disko start [track]       start the set (Kev picks the opener if you don't)
/disko up | down | hold    steer the energy
/disko next                ask Kev for a different next track
/disko pause | resume | stop
/disko tracks [words]      search the library
/disko launch <folder> [--window]   open disko on a music folder
/disko quit                quit the disko this plugin launched"""

ENERGY = {"up": "up", "down": "down", "hold": "hold", "lift": "up", "cool": "down"}
SIMPLE = {"pause": "pause", "resume": "resume", "play": "resume", "stop": "stop", "next": "pick-again",
          "skip": "pick-again", "again": "pick-again", "pick-again": "pick-again"}


def run(raw_args: str) -> str:
    try:
        words = shlex.split(raw_args or "")
    except ValueError:
        words = (raw_args or "").split()
    verb, rest = (words[0].lower(), words[1:]) if words else ("status", [])
    if verb in ("status", "now", "np"):
        return render.status_text(client.status())
    if verb in ("help", "-h", "--help", "?"):
        return USAGE
    if verb == "start":
        return render.started_text(client.start(" ".join(rest) or None))
    if verb in ENERGY:
        return render.status_text(client.energy(ENERGY[verb]))
    if verb == "energy" and rest and rest[0].lower() in ENERGY:
        return render.status_text(client.energy(ENERGY[rest[0].lower()]))
    if verb in SIMPLE:
        return render.status_text(client.post(SIMPLE[verb]))
    if verb in ("tracks", "find", "search"):
        return render.tracks_text(client.tracks(" ".join(rest), 20))
    if verb == "launch":
        window = "--window" in rest
        folder = " ".join(w for w in rest if w != "--window")
        result = launcher.launch(folder, window)
        if "error" in result:
            return result["error"]
        if result.get("ready") is False:
            return f"disko is starting on {result['folder']}; it analyses new music first. Try /disko in a moment."
        return "disko is open.\n" + render.status_text(result)
    if verb == "quit":
        result = launcher.quit()
        return result.get("error") or "disko has quit."
    return f"I don't know '/disko {verb}'.\n{USAGE}"


def _slash(raw_args: str = "", **kwargs) -> str:
    try:
        return run(raw_args)
    except Exception as e:  # a slash command never takes the gateway down
        return f"disko plugin error: {type(e).__name__}: {e}"


def _cli_setup(parser):
    parser.add_argument("words", nargs="*", metavar="COMMAND",
                        help="status (default), start [track], up, down, hold, next, pause, resume, stop, "
                             "tracks [words], launch FOLDER, quit")
    parser.add_argument("--window", action="store_true", help="with launch: show disko's window")


def _cli(args) -> int:
    words = list(getattr(args, "words", None) or [])
    if getattr(args, "window", False):
        words.append("--window")
    text = _slash(shlex.join(words))
    print(text.replace("/disko", "hermes disko") if words[:1] == ["help"] else text)
    return 0


def register(ctx):
    ctx.register_command("disko", handler=_slash, args_hint="[start|up|down|next|pause|resume|stop|tracks|launch|quit]",
                         description="Control the disko AI DJ: what's playing, start, energy, next, pause, stop")
    ctx.register_cli_command(name="disko", help="Control the disko AI DJ from the terminal",
                             setup_fn=_cli_setup, handler_fn=_cli)
