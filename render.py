"""disko's status as text: a few lines for /disko and the CLI, one line for the agent's context."""

def clock(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def track(t: dict) -> str:
    name = f"{t.get('artist')} – {t.get('title')}" if t.get("artist") else str(t.get("title"))
    facts = [f"{t['bpm']:g} BPM" if t.get("bpm") else "", t.get("key") or ""]
    facts = ", ".join(f for f in facts if f)
    return f"{name} ({facts})" if facts else name


def is_active(status: dict) -> bool:
    return "error" not in status and status.get("now") is not None and status.get("state") != "stopped"


def status_text(s: dict) -> str:
    if "error" in s:
        return s["error"]
    library = s.get("library") or {}
    lines = []
    now, nxt = s.get("now"), s.get("next")
    if now is None:
        lines.append(f"disko is ready, nothing playing yet ({s.get('state')}).")
    else:
        state = s.get("state") if s.get("playing", True) or s.get("state") == "stopped" else "paused"
        lines.append(f"▶ {track(now)}   {clock(now.get('position_s'))} in, {clock(now.get('remaining_s'))} left   [{state}]")
    if nxt is not None:
        when = "mixing in now" if nxt.get("mixing") else f"in {clock(nxt.get('starts_in_s'))}"
        lines.append(f"⏭ {track(nxt)}   {nxt.get('technique')} {when}")
    elif now is not None and s.get("state") != "stopped":
        lines.append("⏭ Kev is choosing the next track…")
    lines.append(f"energy: {s.get('energy')} · {plural(s.get('tracks_played', 0), 'track')}, {s.get('set_minutes', 0):g} min"
                 f" · library {library.get('name')} ({library.get('tracks')} tracks)"
                 f" · out: {(s.get('playback') or {}).get('device')}")
    if s.get("notice"):
        lines.append(f"disko says: {s['notice'].get('text')}")
    return "\n".join(lines)


def started_text(result: dict) -> str:
    """disko answers a start with the opener, before anything is audible."""
    if "error" in result:
        return result["error"]
    if "starting" not in result:
        return status_text(result)
    device = (result.get("playback") or {}).get("device")
    return f"Starting the set with {track(result['starting'])}" + (f" on {device}." if device else ".") + \
        " Kev takes it from there; /disko shows what's playing."


def tracks_text(result: dict) -> str:
    if "error" in result:
        return result["error"]
    found = result.get("tracks") or []
    if not found:
        return "No playable tracks match."
    lines = [f"{t.get('id')}  {track(t)}" for t in found]
    more = result.get("total", len(found)) - len(found)
    if more > 0:
        lines.append(f"… and {more} more")
    return "\n".join(lines)


def context_line(s: dict) -> str:
    """One line for the agent while a set runs; empty when there is nothing worth saying."""
    if not is_active(s):
        return ""
    now, nxt = s["now"], s.get("next")
    paused = "" if s.get("playing", True) else " (paused)"
    line = f"[disko] Now playing{paused}: {track(now)}, {clock(now.get('remaining_s'))} left."
    if nxt is not None:
        when = "mixing in now" if nxt.get("mixing") else f"in {clock(nxt.get('starts_in_s'))}"
        line += f" Next: {track(nxt)}, {nxt.get('technique')} {when}."
    line += f" Energy: {s.get('energy')}. {plural(s.get('tracks_played', 0), 'track')} played."
    return line + " (Live from the disko plugin; use the disko_* tools to change anything.)"
