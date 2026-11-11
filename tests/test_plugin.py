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
        for name in ("DISKO_CONTROL_URL", "DISKO_CONTROL_TOKEN", "DISKO_CONTROL_DIR", "DISKO_DIR"):
            os.environ.pop(name, None)
        self.ctx = Ctx(self.settings)
        plugin.register(self.ctx)
        self.addCleanup(self.tmp.cleanup)

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

    def test_stale_token_is_an_error(self):
        d = self.disko()
        (self.folder / "control.token").write_text("stale")
        self.assertIn("token", self.tool("disko_status")["error"])
        self.assertEqual(d.set.state, "start")

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
        os.environ["DISKO_CONTROL_TOKEN"] = d.token
        self.addCleanup(os.environ.pop, "DISKO_CONTROL_TOKEN", None)
        self.assertEqual(self.tool("disko_start")["starting"]["title"], "Morning Glass")


class CommandTest(PluginCase):
    def slash(self, text):
        return self.ctx.commands["disko"](text, session_id="s", platform="telegram")

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
        self.addCleanup(launcher.quit)

    def configure(self):
        self.settings.update(disko_dir=str(self.checkout), disko_python=sys.executable)

    def test_needs_to_know_where_disko_is(self):
        self.assertIn("disko_dir", self.tool("disko_launch", folder=str(self.music))["error"])
        self.settings["disko_dir"] = str(self.music)
        self.assertIn("isn't a disko checkout", self.tool("disko_launch", folder=str(self.music))["error"])

    def test_folder_must_exist(self):
        self.configure()
        self.assertIn("isn't a folder", self.tool("disko_launch", folder=str(self.music / "nope"))["error"])
        self.assertIn("which music folder", self.tool("disko_launch")["error"])

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

    def test_quit_leaves_someone_elses_disko_alone(self):
        d = self.disko()
        self.assertIn("wasn't started by this plugin", self.tool("disko_quit")["error"])
        self.assertEqual(self.tool("disko_status")["state"], d.set.state)


if __name__ == "__main__":
    unittest.main()
