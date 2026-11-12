"""The plugin against a fake disko on a real socket. Run: python -m unittest discover tests"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import fake_disko  # noqa: E402


def load_plugin():
    spec = importlib.util.spec_from_file_location("disko_plugin", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["disko_plugin"] = module
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()
client, launcher, render = plugin.client, sys.modules["disko_plugin.launcher"], plugin.render


class Ctx:
    """What register() needs from Hermes."""

    def __init__(self, settings):
        self.settings, self.tools, self.hooks, self.commands, self.cli, self.skills = settings, {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, callback, **kwargs):
        self.hooks[name] = callback

    def register_command(self, name, handler, **kwargs):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, **kwargs):
        self.cli[name] = (setup_fn, handler_fn)

    def register_skill(self, name, path):
        self.skills[name] = path


class PluginCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dk")
        self.folder = Path(self.tmp.name) / "c"
        self.settings = {"control_dir": str(self.folder)}
        for name in ("DISKO_CONTROL_URL", "DISKO_CONTROL_TOKEN", "DISKO_CONTROL_DIR", "DISKO_DIR", "DISKO_PYTHON"):
            os.environ.pop(name, None)
        self.ctx = Ctx(self.settings)
        plugin.register(self.ctx)
        self.addCleanup(self.tmp.cleanup)

    def slash(self, text):
        return self.ctx.commands["disko"](text, session_id="s", platform="telegram")

    def disko(self, **kwargs):
        d = fake_disko.FakeDisko(self.folder, **kwargs)
        self.addCleanup(d.close)
        return d

    def tool(self, name, **args):
        return json.loads(self.ctx.tools[name][1](args, task_id="t", session_id="s"))


class RegistrationTest(PluginCase):
    def test_manifest_matches_what_is_registered(self):
        manifest = (ROOT / "plugin.yaml").read_text()
        declared = re.search(r"provides_tools:\n((?:  - \w+\n)+)", manifest).group(1).split()[1::2]
        self.assertEqual(sorted(declared), sorted(self.ctx.tools))
        self.assertEqual(list(self.ctx.hooks), ["pre_llm_call"])
        self.assertIn("disko", self.ctx.commands)
        self.assertIn("disko", self.ctx.cli)
        self.assertTrue(self.ctx.skills["disko-dj"].exists())

    def test_schemas_are_json_schema_objects(self):
        for name, (schema, _) in self.ctx.tools.items():
            self.assertEqual(schema["name"], name)
            self.assertTrue(schema["description"])
            self.assertEqual(schema["parameters"]["type"], "object")
            json.dumps(schema)

    def test_versions_agree(self):
        version = re.search(r"^version: (\S+)", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
        skill = (ROOT / "skills" / "disko-dj" / "SKILL.md").read_text()
        self.assertIn(f"version: {version}", skill)


class ControlTest(PluginCase):
    def test_not_running(self):
        for name in ("disko_status", "disko_start", "disko_stop", "disko_pick_again"):
            out = self.tool(name)
            self.assertIn("isn't running", out["error"])
        self.assertIn("isn't running", self.ctx.commands["disko"](""))

    def test_a_set_from_start_to_stop(self):
        self.disko()
        self.assertEqual(self.tool("disko_status")["state"], "start")
        self.assertEqual(self.tool("disko_find_tracks", query="night")["tracks"][0]["id"], "local:b2")
        started = self.tool("disko_start", track="night")
        self.assertEqual(started["starting"]["title"], "Night Drive")
        self.assertEqual(started["playback"]["device"], "MacBook Speakers")
        status = self.tool("disko_status")
        self.assertEqual((status["state"], status["next"]["title"]), ("queued", "Peak Hour"))
        self.assertEqual(self.tool("disko_set_energy", energy="up")["energy"], "lift")
        self.assertEqual(self.tool("disko_pick_again")["next"]["title"], "Morning Glass")
        self.assertFalse(self.tool("disko_pause")["playing"])
        self.assertTrue(self.tool("disko_resume")["playing"])
        self.assertEqual(self.tool("disko_stop")["state"], "stopped")

    def test_refusals_come_back_as_errors(self):
        self.disko()
        self.assertIn("nothing is playing", self.tool("disko_pause")["error"])
        self.assertIn("energy is", self.tool("disko_set_energy", energy="sideways")["error"])
        self.assertIn("no playable track", self.tool("disko_start", track="zzz")["error"])

    def test_bad_arguments_never_raise(self):
        self.disko()
        self.assertEqual(self.tool("disko_find_tracks", limit="lots")["total"], 3)
        self.assertEqual(json.loads(self.ctx.tools["disko_status"][1](None))["state"], "start")

    def test_local_socket_needs_no_token(self):
        d = self.disko()
        self.assertFalse((self.folder / "control.token").exists())
        self.assertEqual(self.tool("disko_start")["starting"]["title"], "Morning Glass")
        self.assertEqual(d.seen_auth, [None])  # no Authorization header on the socket

    def test_a_token_file_is_never_opened(self):
        import builtins
        import io
        from unittest import mock
        self.disko()
        (self.folder / "control.token").mkdir()  # reading it would fail, and so would the request
        opened = []

        def guard(real):
            def wrapped(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)) and "control.token" in os.fsdecode(file):
                    opened.append(file)
                    raise PermissionError("the plugin must not open control.token")
                return real(file, *args, **kwargs)
            return wrapped

        with mock.patch.object(builtins, "open", guard(builtins.open)), \
                mock.patch.object(io, "open", guard(io.open)), mock.patch.object(os, "open", guard(os.open)):
            self.assertEqual(self.tool("disko_status")["state"], "start")
            self.assertEqual(self.tool("disko_start", track="peak")["starting"]["title"], "Peak Hour")
            hook = self.ctx.hooks["pre_llm_call"](session_id="s", user_message="?", is_first_turn=False)
            self.assertIn("Peak Hour", hook["context"])
        self.assertEqual(opened, [])

    def test_older_disko_that_wants_a_token(self):
        d = self.disko(legacy=True)
        error = self.tool("disko_status")["error"]
        self.assertIn("older version", error)
        self.assertIn("update disko", error)
        self.assertIn("older version", self.ctx.commands["disko"](""))
        self.assertEqual(d.set.state, "start")
        self.assertEqual(set(d.seen_auth), {None})  # the plugin still sends no token

    def test_dead_socket_reads_as_not_running(self):
        d = self.disko()
        d.server.shutdown()
        d.server.server_close()  # the socket file stays behind, like after a crash
        self.assertIn("isn't running", self.tool("disko_status")["error"])

    def test_remote_disko_over_tcp(self):
        d = fake_disko.FakeDisko(self.folder, tcp=True)
        self.addCleanup(d.close)
        self.settings["control_url"] = d.url
        self.assertIn("token isn't", self.tool("disko_status")["error"])
        self.assertEqual(d.seen_auth, [])  # nothing sent without a token
        os.environ["DISKO_CONTROL_TOKEN"] = "wrong"
        self.addCleanup(os.environ.pop, "DISKO_CONTROL_TOKEN", None)
        error = self.tool("disko_status")["error"]
        self.assertIn("token", error)
        self.assertNotIn("older version", error)  # that hint is for the local socket only
        os.environ["DISKO_CONTROL_TOKEN"] = d.token
        self.assertEqual(self.tool("disko_start")["starting"]["title"], "Morning Glass")
        self.assertEqual(d.seen_auth[-1], f"Bearer {d.token}")


class CommandTest(PluginCase):
    def test_slash_command(self):
        d = self.disko()
        self.assertIn("nothing playing yet", self.slash(""))
        self.assertIn("Starting the set with Bo – Night Drive (124 BPM, 9A) on MacBook Speakers.",
                      self.slash("start night"))
        out = self.slash("")
        self.assertIn("▶ Bo – Night Drive (124 BPM, 9A)   1:35 in, 2:31 left", out)
        self.assertIn("⏭ Cy – Peak Hour (128 BPM, 10A)   Bass Swap in 0:42", out)
        self.assertIn("1 track, 3.5 min", out)
        self.assertIn("energy: lift", self.slash("up"))
        self.assertIn("energy: cool down", self.slash("energy down"))
        self.slash("next")
        self.assertEqual(d.set.picks, 1)
        self.assertIn("[paused]", self.slash("pause"))
        self.assertIn("local:c3  Cy – Peak Hour", self.slash("tracks peak"))
        self.assertIn("[stopped]", self.slash("stop"))
        self.assertIn("/disko launch", self.slash("help"))
        self.assertIn("I don't know", self.slash("dance"))

    def test_cli(self):
        import argparse
        import contextlib
        import io
        self.disko()
        setup, handler = self.ctx.cli["disko"]
        parser = argparse.ArgumentParser()
        setup(parser)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(handler(parser.parse_args(["start", "peak"])), 0)
        self.assertIn("Peak Hour", out.getvalue())


class HookTest(PluginCase):
    def hook(self):
        return self.ctx.hooks["pre_llm_call"](session_id="s", user_message="what is this?", is_first_turn=False)

    def test_silent_unless_a_set_is_running(self):
        self.assertIsNone(self.hook())  # no disko
        self.disko()
        self.assertIsNone(self.hook())  # open, nothing playing
        self.tool("disko_start", track="night")
        context = self.hook()["context"]
        self.assertIn("Now playing: Bo – Night Drive (124 BPM, 9A), 2:31 left.", context)
        self.assertIn("Next: Cy – Peak Hour (128 BPM, 10A), Bass Swap in 0:42. Energy: hold. 1 track played.", context)
        self.tool("disko_pause")
        self.assertIn("Now playing (paused)", self.hook()["context"])
        self.tool("disko_stop")
        self.assertIsNone(self.hook())

    def test_can_be_turned_off(self):
        self.disko()
        self.tool("disko_start")
        self.settings["now_playing_context"] = False
        self.assertIsNone(self.hook())


class LaunchTest(PluginCase):
    def setUp(self):
        super().setUp()
        self.checkout = Path(self.tmp.name) / "disko"
        (self.checkout / "live").mkdir(parents=True)
        (self.checkout / "live" / "__main__.py").write_text(
            f"import sys\nsys.path.insert(0, {str(Path(__file__).parent)!r})\n"
            "import fake_disko\nsys.exit(fake_disko.main(sys.argv))\n")
        self.music = Path(self.tmp.name) / "Friday"
        self.music.mkdir()
        os.environ["DISKO_CONTROL_DIR"] = str(self.folder)  # the launched disko inherits it
        self.addCleanup(os.environ.pop, "DISKO_CONTROL_DIR", None)
        os.environ["DISKO_FAKE_SPOTIFY_AUDIO"] = "on"
        self.addCleanup(os.environ.pop, "DISKO_FAKE_SPOTIFY_AUDIO", None)
        self.addCleanup(launcher.quit)

    def configure(self):
        self.settings.update(disko_dir=str(self.checkout), disko_python=sys.executable)

    def launch_error(self):
        return self.tool("disko_launch", folder=str(self.music))["error"]

    def assertNotFound(self, error):
        self.assertIn("Open disko once", error)
        self.assertIn("disko_dir", error)

    def test_needs_to_know_where_disko_is(self):
        self.assertNotFound(self.launch_error())  # no settings, no install record
        self.settings["disko_dir"] = str(self.music)
        self.assertIn("isn't a disko checkout", self.launch_error())

    def test_finds_disko_from_its_install_record(self):
        fake_disko.write_install(self.folder, self.checkout, sys.executable)
        out = self.tool("disko_launch", folder=str(self.music))
        self.assertTrue(out.get("launched"), out)
        self.assertEqual(out["library"]["name"], "Friday")

    def test_disko_dir_overrides_the_install_record(self):
        other = Path(self.tmp.name) / "old"
        (other / "live").mkdir(parents=True)
        (other / "live" / "__main__.py").write_text("import sys\nsys.exit('the install record was used')\n")
        fake_disko.write_install(self.folder, other, sys.executable)
        self.configure()
        out = self.tool("disko_launch", folder=str(self.music))
        self.assertTrue(out.get("launched"), out)
        self.tool("disko_quit")
        self.settings["disko_dir"] = str(self.music)  # a wrong override is an error, not a fallback
        self.assertIn("isn't a disko checkout", self.launch_error())

    def test_disko_python_setting_wins_over_the_record(self):
        fake_disko.write_install(self.folder, self.checkout, Path(self.tmp.name) / "gone" / "python")
        self.assertNotFound(self.launch_error())  # the record's Python has moved
        self.settings["disko_python"] = sys.executable
        self.assertTrue(self.tool("disko_launch", folder=str(self.music)).get("launched"))

    def test_a_bad_install_record_reads_as_not_found(self):
        record = self.folder / "install.json"
        good = {"version": 1, "root": str(self.checkout), "python": sys.executable}
        bad = ["{not json", "[]", "null", json.dumps({**good, "version": 2}), json.dumps({**good, "version": "1"}),
               json.dumps({**good, "version": True}), json.dumps({k: v for k, v in good.items() if k != "version"}),
               json.dumps({**good, "root": 42}), json.dumps({**good, "python": None}),
               json.dumps({**good, "root": str(self.music)}), json.dumps({**good, "root": "relative/disko"}),
               json.dumps({**good, "root": str(Path(self.tmp.name) / "missing")})]
        self.folder.mkdir(parents=True, exist_ok=True)
        for text in bad:
            record.write_text(text)
            with self.subTest(record=text):
                self.assertNotFound(self.launch_error())
        record.unlink()
        record.mkdir()  # unreadable
        self.assertNotFound(self.launch_error())

    def test_folder_must_exist(self):
        self.configure()
        self.assertIn("isn't a folder", self.tool("disko_launch", folder=str(self.music / "nope"))["error"])
        self.assertIn("which music", self.tool("disko_launch")["error"])

    def test_launch_play_quit(self):
        self.configure()
        out = self.tool("disko_launch", folder=str(self.music))
        self.assertTrue(out.get("launched"), out)
        self.assertEqual(out["library"]["name"], "Friday")
        self.assertIn("already running", self.tool("disko_launch", folder=str(self.music))["error"])
        self.assertEqual(self.tool("disko_start")["starting"]["title"], "Morning Glass")
        self.assertEqual(self.tool("disko_quit"), {"quit": True})
        self.assertIn("isn't running", self.tool("disko_status")["error"])

    def test_launch_that_dies_reports_its_output(self):
        self.configure()
        (self.music / "broken").touch()
        out = self.tool("disko_launch", folder=str(self.music))
        self.assertIn("exited right away", out["error"])
        self.assertIn("indexing failed", out["last_output"])

    def test_spotify_playlist_names(self):
        pid = "37i9dQZF1DXcBWIGoYBM5M"
        for text, want in [("liked", "liked"), (" Liked Songs ", "liked"), (pid, pid),
                           (f"spotify:playlist:{pid}", pid),
                           (f"https://open.spotify.com/playlist/{pid}?si=abc", pid),
                           (f"open.spotify.com/intl-de/playlist/{pid}", pid),
                           ("", None), ("--playlist", None), (pid + "x", None), (f"spotify:album:{pid}", None),
                           (f"https://open.spotify.com/album/{pid}", None), ("https://open.spotify.com/playlist/", None),
                           ("liked; rm -rf ~", None), (f"https://evil.example/playlist/{pid}", None)]:
            with self.subTest(text=text):
                self.assertEqual(launcher.spotify_playlist(text), want)

    def test_launch_on_spotify(self):
        self.configure()
        out = self.tool("disko_launch", spotify="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        self.assertTrue(out.get("launched"), out)
        self.assertEqual(out["spotify"], "37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(out["library"]["name"], "Spotify 37i9dQZF1DXcBWIGoYBM5M")
        self.assertEqual(self.tool("disko_quit"), {"quit": True})

    def test_spotify_refusals(self):
        self.configure()
        self.assertIn("not both", self.tool("disko_launch", folder=str(self.music), spotify="liked")["error"])
        self.assertIn("isn't a Spotify playlist", self.tool("disko_launch", spotify="my chill mix")["error"])
        os.environ["DISKO_FAKE_SPOTIFY_AUDIO"] = "off"
        error = self.tool("disko_launch", spotify="liked")["error"]
        self.assertIn("Spotify audio is off", error)
        self.assertIn("Only the user can turn it on", error)
        (self.music / "broken").touch()  # a later failure is reported as itself, not as the earlier one
        self.assertIn("exited right away", self.tool("disko_launch", folder=str(self.music))["error"])

    def test_slash_launch_on_spotify(self):
        self.configure()
        self.assertIn("Spotify liked", self.slash("launch --spotify liked"))
        self.assertIn("already running", self.slash("launch --spotify liked"))
        self.assertEqual(self.slash("quit"), "disko has quit.")

    def test_environment_is_an_allowlist(self):
        self.configure()
        env = launcher.environment({
            "PATH": "/usr/bin", "HOME": "/Users/dj", "LANG": "en_US.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp",
            "KEV_URL": "http://127.0.0.1:8009", "HF_HOME": "/hf", "DISKO_BEATPORT_STREAMING": "1",
            "OPENAI_API_KEY": "sk-1", "ANTHROPIC_API_KEY": "sk-2", "OPENROUTER_API_KEY": "sk-3", "GITHUB_TOKEN": "gh",
            "AWS_SECRET_ACCESS_KEY": "aws", "DISKO_CONTROL_TOKEN": "tok", "DISKO_CONTROL_URL": "http://x",
            "PYTHONPATH": "/hermes", "VIRTUAL_ENV": "/hermes/venv", "DISKO_CONTROL_DIR": "/elsewhere"})
        self.assertEqual(env, {
            "PATH": "/usr/bin", "HOME": "/Users/dj", "LANG": "en_US.UTF-8", "LC_ALL": "C", "TMPDIR": "/tmp",
            "KEV_URL": "http://127.0.0.1:8009", "HF_HOME": "/hf", "DISKO_BEATPORT_STREAMING": "1",
            "DISKO_CONTROL_DIR": str(self.folder)})  # where this plugin looks, whatever the environment says

    def test_launched_disko_gets_no_api_keys(self):
        self.configure()
        secrets = {"OPENROUTER_API_KEY": "sk-or-test", "ANTHROPIC_API_KEY": "sk-ant-test", "DISKO_CONTROL_TOKEN": "t"}
        for name, value in secrets.items():
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)
        self.assertTrue(self.tool("disko_launch", folder=str(self.music)).get("launched"))
        seen = json.loads((self.folder / "env.json").read_text())
        for name, value in secrets.items():
            self.assertNotIn(name, seen)
            self.assertNotIn(value, seen.values())
        self.assertEqual(seen["HOME"], os.environ["HOME"])
        self.assertEqual(seen["DISKO_CONTROL_DIR"], str(self.folder))

    def test_quit_leaves_someone_elses_disko_alone(self):
        d = self.disko()
        self.assertIn("wasn't started by this plugin", self.tool("disko_quit")["error"])
        self.assertEqual(self.tool("disko_status")["state"], d.set.state)


if __name__ == "__main__":
    unittest.main()
