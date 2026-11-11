---
name: disko-dj
description: Run and steer a disko AI DJ set on the user's Mac (launch, start, status, energy, pick again, pause, stop)
version: 0.2.0
license: AGPL-3.0
platforms: [macos]
metadata:
  hermes:
    tags: [Music, DJ, Local]
    requires_tools: [disko_status, disko_start]
---
# disko DJ

disko is a desktop AI DJ. Its model, Kev, picks every next track and plans the mix into it;
the music plays out loud on the Mac running disko. These tools steer it; they don't play audio
anywhere else.

## When to Use
The user asks to put music on, start, check on, steer (more or less energy, a different next
track), pause or stop a DJ set, or asks what is playing.

## Quick Reference
- `disko_status`: state, now playing, what's queued next, energy, sources and their permissions, playback location
- `disko_launch(folder, window?)`: open disko on a music folder when it isn't running
- `disko_find_tracks(query)`: playable tracks, to choose an opener
- `disko_start(track?)`: start the set (Kev opens on the calmest track if none is given)
- `disko_set_energy("down" | "hold" | "up")`, `disko_pick_again()`
- `disko_pause()`, `disko_resume()`, `disko_stop()`, `disko_quit()`
- The user can do all of this themselves with `/disko` (try `/disko help`).

## Procedure
1. Call `disko_status` first. If disko isn't running, ask which music folder to use and call
   `disko_launch`. If it answers `ready: false`, disko is analysing the folder: wait and check
   `disko_status` again rather than launching twice.
2. Before `disko_start`, confirm with the user. Tell them which library and sources it will use
   (`sources`, with what disko is permitted to do with each) and that it plays on that Mac
   (`playback`).
3. Map requests to energy: "more energy / bangers / peak time" is `up`; "chill / wind down" is
   `down`; "keep it here" is `hold`. "Skip this next one / something else" is `disko_pick_again`.
4. After any change, call `disko_status` and report only what it says. Kev's reasons are not
   exposed here; don't invent explanations for its picks.

## Pitfalls
- Only report state from tool results or the `[disko]` line in the message. A `notice` in the
  status is disko's own message; quote it.
- An `error` from a tool means nothing changed. Don't retry a refused command in a loop.
- `disko_pick_again` changes the *next* track. There is no way to cut the current track short.
- `disko_launch` needs `disko_dir` in the plugin's settings. If it says so, tell the user;
  don't guess paths.
- If Hermes runs in a container, `localhost` is the container: disko must be started with
  `--control-tcp HOST:PORT`, and the plugin's control URL and token set.

## Verification
`disko_status` shows `state` = `playing`, `queued` or `mixing` while a set runs, and `stopped`
after `disko_stop`.
