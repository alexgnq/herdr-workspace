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
