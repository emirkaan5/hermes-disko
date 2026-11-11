"""A stand-in for disko's control interface (live/control.py): same socket, routes and status
shape, with a set that changes state when told to. Like disko, the Unix socket takes no token (disko
checks the peer is the same OS user) and no token file is written; TCP requires the bearer token.
`legacy=True` imitates an older disko that still wanted a token on the socket. Also runs as
`python -m live` inside a stub checkout, for the launcher tests."""
import json
import os
import secrets
import signal
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TRACKS = [
    {"id": "local:a1", "title": "Morning Glass", "artist": "Aya", "bpm": 120.0, "key": "8A"},
    {"id": "local:b2", "title": "Night Drive", "artist": "Bo", "bpm": 124.0, "key": "9A"},
    {"id": "local:c3", "title": "Peak Hour", "artist": "Cy", "bpm": 128.0, "key": "10A"},
]


class FakeSet:
    def __init__(self, name="Friday"):
        self.name, self.state, self.playing, self.energy = name, "start", False, "hold"  # as disko names them
        self.now, self.next, self.picks = None, None, 0

    def status(self):
        out = {"state": self.state, "playing": self.playing, "autopilot": True, "energy": self.energy,
               "set_minutes": 3.5 if self.now else 0, "tracks_played": 1 if self.now else 0,
               "library": {"name": self.name, "tracks": len(TRACKS), "unplayable": 0},
               "sources": [{"provider": "local", "label": "This Mac"}],
               "playback": {"where": "the sound output of the Mac running disko", "device": "MacBook Speakers"},
               "now": None, "next": None, "notice": None}
        if self.now:
            out["now"] = {**self.now, "position_s": 95.0, "remaining_s": 151.0, "set_bpm": self.now["bpm"]}
        if self.next:
            out["next"] = {**self.next, "technique": "Bass Swap", "out_bar": 65, "starts_in_s": 42.0, "mixing": False}
        return out

    def handle(self, method, path, query, body):
        """(code, payload) like disko: 409 with an error when the set refuses."""
        if (method, path) == ("GET", "/status"):
            return 200, self.status()
        if (method, path) == ("GET", "/tracks"):
            q = query.get("q", [""])[0].lower()
            hits = [t for t in TRACKS if q in f"{t['title']} {t['artist']}".lower()]
            return 200, {"total": len(hits), "tracks": hits[:int(query.get("limit", ["20"])[0])]}
        if method != "POST":
            return 404, {"error": f"no {method} {path}"}
        if path == "/start":
            q = (body.get("track") or "").lower()
            hits = [t for t in TRACKS if q in t["title"].lower() or q == t["id"]]
            if not hits:
                return 409, {"error": f"no playable track matches {body.get('track')!r}"}
            self.now, self.next, self.state, self.playing = hits[0], TRACKS[(TRACKS.index(hits[0]) + 1) % 3], "queued", True
            status = self.status()
            return 200, {"starting": hits[0], "sources": status["sources"], "playback": status["playback"]}
        elif path == "/energy":
            if body.get("energy") not in ("down", "hold", "up"):
                return 409, {"error": "energy is 'down', 'hold' or 'up' (or -1, 0, 1)"}
            self.energy = {"down": "cool down", "hold": "hold", "up": "lift"}[body["energy"]]
        elif path in ("/pause", "/resume"):
            if self.now is None:
                return 409, {"error": "nothing is playing"}
            self.playing = path == "/resume"
        elif path == "/stop":
            self.state, self.playing, self.next = "stopped", False, None
        elif path == "/pick-again":
            if self.next is None:
                return 409, {"error": "Kev can't pick again right now (no set playing, mid-transition, or DJing by hand)"}
            self.picks += 1
            self.next = TRACKS[(TRACKS.index(self.next) + 1) % 3]
        else:
            return 404, {"error": f"no POST {path}"}
        return 200, self.status()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _serve(self, method):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.seen_auth.append(self.headers.get("Authorization"))
        if self.server.needs_token and self.headers.get("Authorization") != f"Bearer {self.server.token}":
            code, payload = 401, {"error": "missing or wrong token"}
        else:
            url = urlparse(self.path)
            code, payload = self.server.set.handle(method, url.path, parse_qs(url.query), json.loads(raw or b"{}"))
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")


class _UnixServer(socketserver.UnixStreamServer):
    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


class FakeDisko:
    def __init__(self, folder, name="Friday", tcp=False, legacy=False):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(16)
        self.set = FakeSet(name)
        if tcp:
            self.server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
        else:
            self.server = _UnixServer(str(self.folder / "control.sock"), _Handler)
        self.server.token, self.server.set, self.server.needs_token = self.token, self.set, tcp or legacy
        self.server.seen_auth = []  # the Authorization header of every request, for the tests
        self.seen_auth = self.server.seen_auth
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://%s:%d" % self.server.server_address

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        (self.folder / "control.sock").unlink(missing_ok=True)


def write_install(folder, root, python, version=1):
    """disko's install record, as it writes it on every start."""
    path = Path(folder) / "install.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "root": str(root), "python": str(python)}))
    path.chmod(0o600)
    return path


def main(argv):
    """`python -m live --headless --playlist FOLDER`, as the launcher runs it."""
    folder = Path(argv[argv.index("--playlist") + 1])
    if (folder / "broken").exists():
        print("indexing failed: no audio here", file=sys.stderr)
        return 1
    write_install(os.environ["DISKO_CONTROL_DIR"], Path.cwd(), sys.executable)  # disko registers on every start
    disko = FakeDisko(os.environ["DISKO_CONTROL_DIR"], name=folder.name)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: done.set())
    done.wait()
    disko.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
