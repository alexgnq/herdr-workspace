import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import (
    Repository, Store, UserError, Workspace, discover_repositories,
    import_definition, make_repository, portable_definition,
)


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def repo(self, path, remote=None):
        path = self.root / path
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(path)], check=True)
        if remote:
            subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
        return make_repository(str(path))

    def workspace(self, repos):
        return Workspace("platform", "Platform", repos, "codex")

    def test_store_does_not_create_directories_on_read(self):
        store = Store(self.root / "config", self.root / "state")
        self.assertEqual(store.load(), [])
        self.assertFalse(store.config_dir.exists())
        self.assertFalse(store.state_dir.exists())

    def test_atomic_save_update_and_delete(self):
        repo = self.repo("project")
        store = Store(self.root / "config", self.root / "state")
        workspace = self.workspace([repo])
        store.save(workspace)
        workspace.agent = "claude"
        store.save(workspace)
        self.assertEqual(len(store.load()), 1)
        self.assertEqual(store.load()[0].agent, "claude")
        self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
        store.delete(workspace.id)
        self.assertEqual(store.load(), [])
        self.assertEqual(list(store.config_dir.glob(".workspaces-*")), [])

    def test_corrupt_store_is_left_unchanged(self):
        store = Store(self.root)
        store.path.write_text("{broken")
        with self.assertRaisesRegex(UserError, "left unchanged"):
            store.save(self.workspace([self.repo("project")]))
        self.assertEqual(store.path.read_text(), "{broken")

    def test_malformed_store_is_left_unchanged(self):
        store = Store(self.root)
        value = json.dumps({"version": 1, "workspaces": [{"id": "platform", "name": "Platform", "repos": [None]}]})
        store.path.write_text(value)
        with self.assertRaises(UserError):
            store.load()
        self.assertEqual(store.path.read_text(), value)

    def test_plugin_paths_override_xdg_defaults(self):
        with patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": str(self.root / "config"), "HERDR_PLUGIN_STATE_DIR": str(self.root / "state")}):
            store = Store()
        self.assertEqual(store.config_dir, self.root / "config")
        self.assertEqual(store.state_dir, self.root / "state")

    def test_export_strips_credentials_and_all_local_paths(self):
        repo = self.repo("project", "https://secret-user:secret-token@github.com/team/project.git?token=other-secret#fragment")
        exported = portable_definition(self.workspace([repo]))
        serialized = json.dumps(exported)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("fragment", serialized)
        self.assertEqual(exported["repositories"][0]["remote"], "https://github.com/team/project.git")

    def test_export_omits_local_remotes(self):
        repo = self.repo("project", str(self.root / "private"))
        exported = portable_definition(self.workspace([repo]))
        self.assertEqual(exported["repositories"], [{"name": "project"}])

    def test_portable_import_with_other_root_and_protocol(self):
        original = self.repo("alex/project", "git@github.com:team/project.git")
        target = self.repo("teammate/project", "https://github.com/team/project.git")
        exported = portable_definition(self.workspace([original]))
        imported = import_definition(exported, str(self.root / "teammate"))
        self.assertEqual(imported.repos[0].path, target.path)
        self.assertEqual(imported.name, "Platform")
        self.assertEqual(imported.agent, "codex")

    def test_import_matches_remote_when_folder_name_differs(self):
        target = self.repo("teammate/renamed", "https://github.com/team/project.git")
        data = {"version": 1, "name": "Platform", "repositories": [{"name": "project", "remote": "git@github.com:team/project.git"}]}
        imported = import_definition(data, str(self.root / "teammate"))
        self.assertEqual(imported.repos[0].path, target.path)
        self.assertEqual(imported.repos[0].name, "project")

    def test_import_rejects_wrong_remote_with_same_folder_name(self):
        self.repo("project", "https://github.com/someone-else/project.git")
        data = {"version": 1, "name": "Platform", "repositories": [{"name": "project", "remote": "https://github.com/team/project.git"}]}
        with self.assertRaisesRegex(UserError, "different Git remote"):
            import_definition(data, str(self.root))

    def test_import_reports_missing_repository_without_cloning(self):
        data = {"version": 1, "name": "Platform", "repositories": [{"name": "missing", "remote": "https://github.com/team/missing.git"}]}
        with self.assertRaisesRegex(UserError, "was not found"):
            import_definition(data, str(self.root))
        self.assertFalse((self.root / "missing").exists())

    def test_import_rejects_path_traversal(self):
        for name in ("../secret", "/tmp/project", "..", "folder\\project"):
            data = {"version": 1, "name": "Platform", "repositories": [{"name": name}]}
            with self.subTest(name=name), self.assertRaises(UserError):
                import_definition(data, str(self.root))

    def test_shell_like_names_are_passed_as_arguments(self):
        repo = self.repo("project; $(touch UNEXPECTED)")
        exported = portable_definition(self.workspace([repo]))
        imported = import_definition(exported, str(self.root))
        self.assertEqual(imported.repos[0].path, repo.path)
        self.assertFalse((self.root / "UNEXPECTED").exists())

    def test_discovery_is_bounded_and_supports_worktrees(self):
        self.repo("first")
        self.repo("org/second")
        self.repo("too/deep/third")
        self.repo(".hidden/fourth")
        worktree = self.root / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /some/repo/.git/worktrees/example\n")
        (self.root / "alias").symlink_to(self.root / "first", target_is_directory=True)
        discovered = discover_repositories([str(self.root)])
        self.assertEqual({repo.name for repo in discovered}, {"first", "second", "worktree"})
        self.assertTrue(all(repo.remote is None for repo in discovered))

    def test_control_characters_are_rejected(self):
        path = self.root / "bad\nname"
        path.mkdir()
        (path / ".git").mkdir()
        with self.assertRaises(UserError):
            make_repository(str(path))

    def test_ambiguous_remote_requires_a_more_specific_root(self):
        self.repo("one", "https://github.com/team/project.git")
        self.repo("two", "git@github.com:team/project.git")
        data = {"version": 1, "name": "Platform", "repositories": [{"name": "project", "remote": "https://github.com/team/project.git"}]}
        with self.assertRaisesRegex(UserError, "Several checkouts"):
            import_definition(data, str(self.root))


if __name__ == "__main__":
    unittest.main()
