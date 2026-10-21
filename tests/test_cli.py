import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core import PLUGIN_ID, Store
from herdr_workspaces import main


class CliTests(unittest.TestCase):
    def test_version_comes_from_the_plugin_manifest(self):
        manifest = (Path(__file__).parent.parent / "herdr-plugin.toml").read_text()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main(["--version"])
        self.assertIn('version = "' + output.getvalue().strip() + '"', manifest)

    def test_same_name_repositories_can_be_created_and_shared_by_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = [root / "one" / "api", root / "two" / "api"]
            targets = [root / "team" / "renamed-one", root / "team" / "renamed-two"]
            remotes = ["https://example.com/one/api.git", "https://example.com/two/api.git"]
            for paths in (sources, targets):
                for path, remote in zip(paths, remotes):
                    path.mkdir(parents=True)
                    subprocess.run(["git", "init", "-q", str(path)], check=True)
                    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
            definition = root / "shared.json"
            with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(root / "config"), "HERDR_PLUGIN_STATE_DIR": str(root / "state")}):
                errors = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                    self.assertEqual(main(["create", "Platform", str(sources[1]), str(sources[0])]), 0, errors.getvalue())
                    self.assertEqual(main(["export", "Platform", str(definition)]), 0)
                aliases = {repo.path: repo.name for repo in Store().load()[0].repos}
                self.assertEqual(aliases, {str(sources[0].resolve()): "api", str(sources[1].resolve()): "api-2"})
            with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(root / "team-config"), "HERDR_PLUGIN_STATE_DIR": str(root / "team-state")}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", str(definition), "--base", str(root / "team")]), 0)
                imported = {repo.name: repo.path for repo in Store().load()[0].repos}
                self.assertEqual(imported, {"api": str(targets[0].resolve()), "api-2": str(targets[1].resolve())})

    def test_team_export_import_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "alex" / "service"
            teammate = root / "team" / "service"
            for path in (repo, teammate):
                path.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(path)], check=True)
                subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "https://example.com/team/service.git"], check=True)
            definition = root / "platform.herdr-workspace.json"
            with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(root / "config"), "HERDR_PLUGIN_STATE_DIR": str(root / "state")}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["create", "Platform", str(repo), "--agent", "claude"]), 0)
                    self.assertEqual(main(["export", "Platform", str(definition)]), 0)
                original = definition.read_text()
                self.assertNotIn(str(root), original)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(["export", "Platform", str(definition)]), 1)
                self.assertEqual(definition.read_text(), original)
            with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(root / "team-config"), "HERDR_PLUGIN_STATE_DIR": str(root / "team-state")}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", str(definition), "--base", str(root / "team"), "--agent", "copilot"]), 0)
                saved = Store().load()[0]
                self.assertEqual(saved.name, "Platform")
                self.assertEqual(saved.agent, "copilot")
                self.assertEqual(saved.repos[0].path, str(teammate.resolve()))

    def test_standalone_default_paths_match_herdr_plugin_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment.pop("HERDR_PLUGIN_CONFIG_DIR", None)
            environment.pop("HERDR_PLUGIN_STATE_DIR", None)
            environment.update({"XDG_CONFIG_HOME": temporary + "/config", "XDG_STATE_HOME": temporary + "/state"})
            with patch.dict(os.environ, environment, clear=True):
                store = Store()
            self.assertEqual(store.config_dir, Path(temporary) / "config" / "herdr" / "plugins" / "config" / PLUGIN_ID)
            self.assertEqual(store.state_dir, Path(temporary) / "state" / "herdr" / "plugins" / PLUGIN_ID)
            self.assertFalse(store.config_dir.exists())


if __name__ == "__main__":
    unittest.main()
