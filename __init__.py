"""disko for Hermes: run and steer a disko AI DJ set from any Hermes chat.

disko (https://github.com/emirkaan5/disko) is a desktop AI DJ: its model, Kev, picks each next
track and plans the mix into it, and the music plays out loud on the Mac running it. This plugin
is the remote control:

- tools (tools.py): status, search, start, energy, pick again, pause, resume, stop, launch, quit
- /disko and `hermes disko` (commands.py): the same controls with no model turn
- a pre_llm_call hook: while a set runs, one line saying what's playing, so the agent can talk
  about the music without calling a tool first
- a bundled skill (skills/disko-dj) with the procedure and the pitfalls

It talks only to disko's local control socket (client.py) and uses the standard library alone.
"""
from pathlib import Path

from . import client, commands, render, tools

CONTEXT_TIMEOUT_S = 1.5


def _now_playing(**kwargs):
    """pre_llm_call: a line of live set state. Costs one stat() when disko isn't running."""
    try:
        if not client.setting("now_playing_context", default=True) or not client.socket_present():
            return None
        line = render.context_line(client.status(timeout=CONTEXT_TIMEOUT_S))
        return {"context": line} if line else None
    except Exception:
        return None


def register(ctx):
    client.configure(ctx.get_config)
    tools.register(ctx)
    commands.register(ctx)
    ctx.register_hook("pre_llm_call", _now_playing)
    skills = Path(__file__).parent / "skills"
    for child in sorted(skills.iterdir()):
        if child.is_dir() and (child / "SKILL.md").exists():
            ctx.register_skill(child.name, child / "SKILL.md")
