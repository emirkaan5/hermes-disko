"""The tools the agent gets. Handlers return JSON strings and never raise."""
import json

from . import client, launcher


def _json(fn):
    def handler(args: dict, **kwargs) -> str:
        try:
            result = fn(args or {})
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
        return json.dumps(result, ensure_ascii=False)
    return handler


def _limit(args: dict) -> int:
    try:
        return max(1, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        return 20


NO_ARGS = {"type": "object", "properties": {}}

TOOLS = [
    ("disko_status", lambda a: client.status(), {
        "description": "What the disko DJ set is doing now: state, the track playing and what is queued next, the "
                       "energy direction, which music sources the library uses and what disko may do with them, "
                       "and where the music plays. Read this before and after changing anything.",
        "parameters": NO_ARGS}),
    ("disko_find_tracks", lambda a: client.tracks(str(a.get("query") or ""), _limit(a)), {
        "description": "Search the playable tracks in disko's library (title or artist), e.g. to choose an opener.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "words from the title or artist; empty lists the first tracks"},
            "limit": {"type": "integer", "description": "at most this many (1-50, default 20)"}}}}),
    ("disko_start", lambda a: client.start(a.get("track")), {
        "description": "Start a DJ set on the Mac running disko; Kev, its AI DJ, picks and mixes every track after "
                       "the first. Plays out loud on that Mac. Confirm with the user first, and tell them the "
                       "sources and playback location from the reply.",
        "parameters": {"type": "object", "properties": {
            "track": {"type": "string",
                      "description": "opening track: its id or part of its title; omit to open on the calmest track"}}}}),
    ("disko_set_energy", lambda a: client.energy(a.get("energy")), {
        "description": "Steer the set's energy: Kev rethinks its next pick in that direction.",
        "parameters": {"type": "object", "properties": {
            "energy": {"type": "string", "enum": ["down", "hold", "up"]}}, "required": ["energy"]}}),
    ("disko_pick_again", lambda a: client.post("pick-again"), {
        "description": "Ask Kev for a different next track (drops a queued mix that hasn't started).",
        "parameters": NO_ARGS}),
    ("disko_pause", lambda a: client.post("pause"), {"description": "Pause playback.", "parameters": NO_ARGS}),
    ("disko_resume", lambda a: client.post("resume"), {
        "description": "Resume playback (also resumes a stopped set's current track).", "parameters": NO_ARGS}),
    ("disko_stop", lambda a: client.post("stop"), {
        "description": "Stop the set: playback stops and Kev stops picking. disko itself stays open.",
        "parameters": NO_ARGS}),
    ("disko_launch", lambda a: launcher.launch(str(a.get("folder") or ""), bool(a.get("window")),
                                               spotify=str(a.get("spotify") or "")), {
        "description": "Open disko on this machine with a folder of music or a Spotify playlist, when disko_status "
                       "says it isn't running. Give exactly one of folder or spotify. disko analyses new music "
                       "first (a folder: about half a second per track; Spotify: a 30 s preview of each track), so "
                       "it may report ready=false: check disko_status again. This starts a background app that "
                       "keeps running; it does not start the music (use disko_start). Confirm the music with the "
                       "user. Spotify needs Spotify audio turned on in disko by the user; if the reply says it is "
                       "off, pass that on and don't try to turn it on.",
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string", "description": "path to a folder of audio files, e.g. ~/Music/Friday"},
            "spotify": {"type": "string",
                        "description": "a Spotify playlist instead of a folder: `liked` for Liked Songs, a playlist "
                                       "link (open.spotify.com/playlist/...) or its id"},
            "window": {"type": "boolean",
                       "description": "true shows disko's decks on the Mac's screen; default false (no window)"}}}}),
    ("disko_quit", lambda a: launcher.quit(), {
        "description": "Quit the disko that disko_launch started (the music stops). A disko the user opened "
                       "themselves is left alone.",
        "parameters": NO_ARGS}),
]


def register(ctx):
    for name, fn, schema in TOOLS:
        ctx.register_tool(name=name, toolset="disko", schema={"name": name, **schema}, handler=_json(fn),
                          emoji="🪩")
