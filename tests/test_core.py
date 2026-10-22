import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core import (
    Repository, Store, UserError, Workspace, discover_repositories,
    import_definition, make_repository, portable_definition,
    name_repositories,
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

    def test_repository_folders_use_defaults_without_creating_files(self):
        store = Store(self.root / "config", self.root / "state")
        with patch("core.default_roots", return_value=[str(self.root)]):
            self.assertEqual(store.repository_folders(), [str(self.root)])
        self.assertFalse(store.config_dir.exists())
        self.assertFalse(store.state_dir.exists())

    def test_repository_folders_persist_without_changing_workspaces(self):
        store = Store(self.root / "config", self.root / "state")
        store.save(self.workspace([self.repo("project")]))
        original = store.path.read_bytes()
        store.save_repository_folders([str(self.root)])
        restored = Store(store.config_dir, store.state_dir)
        self.assertEqual(restored.repository_folders(), [str(self.root.resolve())])
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(stat.S_IMODE((store.config_dir / "settings.json").stat().st_mode), 0o600)
        self.assertFalse(store.state_dir.exists())
        self.assertEqual(list(store.config_dir.glob(".settings.json-*")), [])

    def test_repository_folders_normalize_tilde_relative_paths_and_duplicates(self):
        folder = self.root / "code"
        folder.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(folder, target_is_directory=True)
        store = Store(self.root / "config", self.root / "state")
        relative = os.path.relpath(folder, Path.cwd())
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            store.save_repository_folders(["~/code", str(folder), str(alias), relative])
        self.assertEqual(store.repository_folders(), [str(folder.resolve())])

    def test_empty_repository_folders_disable_default_discovery(self):
        store = Store(self.root / "config", self.root / "state")
        store.save_repository_folders([])
        with patch("core.default_roots", return_value=[str(self.root)]):
            self.assertEqual(Store(store.config_dir, store.state_dir).repository_folders(), [])
        self.assertFalse(store.path.exists())

    def test_missing_saved_repository_folder_can_be_kept_or_removed(self):
        absent = self.root / "external-drive"
        present = self.root / "code"
        added = self.root / "other-code"
        for folder in (absent, present, added):
            folder.mkdir()
        store = Store(self.root / "config", self.root / "state")
        store.save_repository_folders([str(absent), str(present)])
        expected = [str(absent.resolve()), str(present.resolve())]
        absent.rmdir()
        original = (store.config_dir / "settings.json").read_bytes()
        self.assertEqual(store.repository_folders(), expected)
        self.assertEqual((store.config_dir / "settings.json").read_bytes(), original)
        store.save_repository_folders(expected + [str(added)])
        self.assertIn(str(absent.resolve()), store.repository_folders())
        store.save_repository_folders([str(present), str(added)])
        self.assertEqual(store.repository_folders(), [str(present.resolve()), str(added.resolve())])
        with self.assertRaises(UserError):
            store.save_repository_folders([str(absent)])

    def test_invalid_repository_folders_do_not_replace_settings(self):
        store = Store(self.root / "config", self.root / "state")
        store.save_repository_folders([str(self.root)])
        settings = store.config_dir / "settings.json"
        original = settings.read_bytes()
        regular = self.root / "file"
        regular.write_text("not a folder")
        control = self.root / "bad\nfolder"
        control.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(control, target_is_directory=True)
        for folders in (None, "folder", [None], [1], [""], ["bad\nfolder"], [str(alias)], [str(regular)], [str(self.root / "missing")]):
            with self.subTest(folders=folders), self.assertRaises(UserError):
                store.save_repository_folders(folders)
            self.assertEqual(settings.read_bytes(), original)

    def test_corrupt_folder_settings_cannot_be_read_or_overwritten(self):
        store = Store(self.root / "config", self.root / "state")
        store.config_dir.mkdir()
        settings = store.config_dir / "settings.json"
        invalid = [
            "{broken", "[]", '{"version": true, "repository_folders": []}',
            '{"version": 2, "repository_folders": []}', '{"version": 1}',
            '{"version": 1, "repository_folders": "folder"}',
            '{"version": 1, "repository_folders": [null]}',
            '{"version": 1, "repository_folders": ["relative"]}',
            json.dumps({"version": 1, "repository_folders": [str(self.root / "bad\nfolder")]}),
        ]
        for value in invalid:
            with self.subTest(value=value):
                settings.write_text(value)
                with self.assertRaisesRegex(UserError, "left unchanged"):
                    store.repository_folders()
                with self.assertRaisesRegex(UserError, "left unchanged"):
                    store.save_repository_folders([str(self.root)])
                self.assertEqual(settings.read_text(), value)
        settings.write_bytes(b"\xff")
        with self.assertRaisesRegex(UserError, "left unchanged"):
            store.repository_folders()
        self.assertEqual(settings.read_bytes(), b"\xff")
        self.assertFalse(store.path.exists())

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
        self.assertEqual(list(store.config_dir.glob(".workspaces.json-*")), [])

    def test_concurrent_saves_keep_every_workspace(self):
        repo = self.repo("project")
        store = Store(self.root / "config", self.root / "state")
        original_load = store.load

        def slow_load():
            workspaces = original_load()
            time.sleep(0.02)
            return workspaces

        store.load = slow_load
        threads = [
            threading.Thread(target=store.save, args=(Workspace("w" + str(index), "W" + str(index), [repo]),))
            for index in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(item.id for item in original_load()), ["w" + str(index) for index in range(6)])

    def test_remembering_the_agent_keeps_changes_saved_meanwhile(self):
        store = Store(self.root / "config", self.root / "state")
        stale = self.workspace([self.repo("project")])
        store.save(stale)
        edited = self.workspace(stale.repos + [self.repo("added")])
        store.save(edited)
        store.set_agent(stale.id, "claude")
        saved = store.load()[0]
        self.assertEqual(saved.agent, "claude")
        self.assertEqual(len(saved.repos), 2)
        store.delete(stale.id)
        store.set_agent(stale.id, "codex")
        self.assertEqual(store.load(), [])

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

    def test_nested_search_folder_is_scanned_from_its_own_root(self):
        nested = self.repo("Documents/git/api")
        parent = str(self.root)
        git_folder = str(self.root / "Documents" / "git")
        for roots in ([parent, git_folder], [git_folder, parent]):
            with self.subTest(roots=roots):
                actual = {repo.path for repo in discover_repositories(roots)}
                self.assertEqual(actual, {nested.path})

    def test_overlapping_search_folders_deduplicate_symlinked_checkouts(self):
        nested = self.repo("Documents/git/api")
        git_folder = self.root / "Documents" / "git"
        alias = self.root / "shortcut"
        alias.symlink_to(git_folder, target_is_directory=True)
        (git_folder / "loop").symlink_to(self.root, target_is_directory=True)
        roots = [str(self.root), str(alias), str(git_folder)]
        self.assertEqual([repo.path for repo in discover_repositories(roots)], [nested.path])

    def test_new_checkout_aliases_are_stable_and_reserve_existing_names(self):
        existing = self.repo("z/api")
        another = self.repo("a/api")
        named_suffix = self.repo("b/api-2")
        selected = [existing, another, named_suffix]
        expected = {existing.path: "api", another.path: "api-3", named_suffix.path: "api-2"}
        for ordering in (selected, list(reversed(selected))):
            aliases = name_repositories(ordering, [existing])
            self.assertEqual({repo.path: repo.name for repo in aliases}, expected)
        self.assertEqual(another.name, "api")

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
