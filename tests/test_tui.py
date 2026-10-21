import curses
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core import Repository, UserError, Workspace
from tui import Picker, clipped, plain


class FakeScreen:
    def __init__(self, keys, answers=(), choices=(), size=(24, 80)):
        self.keys = iter(keys)
        self.answers = iter(answers)
        self.choices = iter(choices)
        self.dimensions = size
        self.window = SimpleNamespace(refresh=lambda: None)
        self.errors = []
        self.writes = []
        self.titles = []
        self.prompts = []

    def usable(self):
        return True

    def start(self, *args):
        self.titles.append(args)

    def size(self):
        return self.dimensions

    def write(self, row, text, attr=0, col=2):
        self.writes.append((row, text, col))

    def footer(self, help_text, error=""):
        if error:
            self.errors.append(error)

    def key(self):
        return next(self.keys)

    def choose(self, *args, **kwargs):
        return next(self.choices, 1)

    def prompt(self, *args, **kwargs):
        self.prompts.append(args)
        return next(self.answers, None)


class PickerTests(unittest.TestCase):
    def workspace(self):
        return Workspace(id="platform", name="Platform", repos=[Repository(path="/tmp/repo", name="repo")], agent="codex")

    def test_cancel_after_changing_agent_preserves_saved_workspace(self):
        screen = FakeScreen([curses.KEY_UP, "\n", "\x1b"])
        store = Mock()
        launcher = Mock()
        workspace = self.workspace()
        self.assertIsNone(Picker(screen, store, launcher).edit(workspace))
        self.assertEqual(workspace.agent, "codex")
        store.save.assert_not_called()
        launcher.open.assert_not_called()

    def test_launch_error_keeps_picker_open_and_is_visible(self):
        screen = FakeScreen(["\n", "\x1b"])
        store = Mock()
        launcher = Mock()
        launcher.open.side_effect = UserError("Open this picker inside Herdr.")
        self.assertIsNone(Picker(screen, store, launcher).edit(self.workspace()))
        self.assertIn("Open this picker inside Herdr.", screen.errors)
        self.assertEqual(launcher.open.call_count, 1)

    def test_launch_uses_explicit_new_agent_choice(self):
        screen = FakeScreen(["\n"])
        store = Mock()
        launcher = Mock()
        launcher.open.return_value = SimpleNamespace(message="Opened Platform")
        message = Picker(screen, store, launcher).edit(self.workspace(), new_agent=True)
        self.assertEqual(message, "Opened Platform")
        self.assertTrue(launcher.open.call_args.kwargs["new_agent"])

    def test_new_agent_shortcut_only_asks_for_agent_and_inherits_repositories(self):
        screen = FakeScreen(["a"])
        store = Mock()
        workspace = self.workspace()
        store.load.return_value = [workspace]
        launcher = Mock()
        launcher.open.return_value = SimpleNamespace(message="Opened another agent")
        message = Picker(screen, store, launcher).run()
        self.assertEqual(message, "Opened another agent")
        launched = launcher.open.call_args.args[0]
        self.assertEqual(launched.agent, "claude")
        self.assertEqual(launched.repos, workspace.repos)
        self.assertEqual(workspace.agent, "codex")
        self.assertTrue(launcher.open.call_args.kwargs["new_agent"])

    def test_enter_chooses_any_agent_for_the_same_saved_workspace_and_remembers_choice(self):
        for index, kind in enumerate(("codex", "claude", "copilot")):
            with self.subTest(agent=kind):
                screen = FakeScreen(["\n"], choices=[index])
                screen.choose = Mock(side_effect=screen.choose)
                store, launcher = Mock(), Mock()
                workspace = self.workspace()
                workspace.agent = "copilot"
                store.load.return_value = [workspace]
                launcher.open.return_value = SimpleNamespace(message="Opened Platform")
                self.assertEqual(Picker(screen, store, launcher).run(), "Opened Platform")
                launched = launcher.open.call_args.args[0]
                self.assertEqual(launched.id, workspace.id)
                self.assertEqual(launched.name, workspace.name)
                self.assertEqual(launched.repos, workspace.repos)
                self.assertEqual(launched.agent, kind)
                self.assertFalse(launcher.open.call_args.kwargs["new_agent"])
                self.assertEqual(screen.choose.call_args.args[2], 2)
                store.set_agent.assert_called_once_with(workspace.id, kind)
                store.save.assert_not_called()
                self.assertEqual(workspace.agent, "copilot")

    def test_cancel_agent_choice_preserves_workspace_and_launches_nothing(self):
        for key in ("\n", "a"):
            with self.subTest(shortcut=key):
                screen = FakeScreen([key, "q"], choices=[None])
                store, launcher = Mock(), Mock()
                store.load.return_value = [self.workspace()]
                self.assertIsNone(Picker(screen, store, launcher).run())
                launcher.open.assert_not_called()
                store.save.assert_not_called()

    def test_failed_agent_open_keeps_picker_open_and_saved_agent_unchanged(self):
        screen = FakeScreen(["\n", "q"], choices=[2])
        store, launcher = Mock(), Mock()
        workspace = self.workspace()
        store.load.return_value = [workspace]
        launcher.open.side_effect = UserError("Install Copilot first.")
        self.assertIsNone(Picker(screen, store, launcher).run())
        self.assertIn("Install Copilot first.", screen.errors)
        self.assertEqual(workspace.agent, "codex")
        store.save.assert_not_called()

    def test_repo_editor_remains_accessible_from_saved_workspace(self):
        screen = FakeScreen(["r", "\x1b", "q"])
        store, launcher = Mock(), Mock()
        store.load.return_value = [self.workspace()]
        self.assertIsNone(Picker(screen, store, launcher).run())
        self.assertTrue(any(title[0] == "Platform" for title in screen.titles))
        launcher.open.assert_not_called()
        store.save.assert_not_called()

    def test_terminal_paths_are_printable_and_fit_display_columns(self):
        self.assertEqual(plain("repo\n\x1b[31m"), "repo  [31m")
        self.assertEqual(clipped("a界b", 3), "a界")
        self.assertEqual(clipped("a界b", 2), "a")
        self.assertEqual(clipped("a界b", 0), "")

    def test_remove_shortcut_requires_confirmation_and_only_deletes_selected_definition(self):
        screen = FakeScreen(["d", "q"], choices=[1])
        store, launcher = Mock(), Mock()
        workspace = self.workspace()
        store.load.side_effect = [[workspace], []]
        picker = Picker(screen, store, launcher)
        self.assertIsNone(picker.run())
        store.delete.assert_called_once_with(workspace.id)
        store.save.assert_not_called()
        launcher.open.assert_not_called()
        self.assertIn("from saved workspaces", picker.message)

    def test_keep_or_cancel_preserves_saved_workspace_and_defaults_to_keep(self):
        for choice in (0, None):
            with self.subTest(choice=choice):
                screen = FakeScreen(["d", "q"], choices=[choice])
                original_choose = screen.choose
                screen.choose = Mock(side_effect=original_choose)
                store, launcher = Mock(), Mock()
                store.load.return_value = [self.workspace()]
                self.assertIsNone(Picker(screen, store, launcher).run())
                self.assertEqual(screen.choose.call_args.kwargs["selected"], 0)
                self.assertEqual(screen.choose.call_args.kwargs["subtitle"], "Keeps repos and running spaces.")
                store.delete.assert_not_called()
                store.save.assert_not_called()
                launcher.open.assert_not_called()

    def test_new_workspace_guides_required_fields_before_review(self):
        screen = FakeScreen(["\n"], answers=["Platform"], choices=[1])
        store, launcher = Mock(), Mock()
        launcher.open.return_value = SimpleNamespace(message="Opened Platform")
        picker = Picker(screen, store, launcher)
        repos = [Repository("/tmp/api", "api"), Repository("/tmp/web", "web")]
        picker.repositories = Mock(return_value=repos)
        with patch("tui.checkout_branch", return_value="main"):
            self.assertEqual(picker.new_workspace(), "Opened Platform")
        self.assertIn("1/3", screen.prompts[0][0])
        self.assertIn("2/3", picker.repositories.call_args.kwargs["title"])
        saved = store.save.call_args.args[0]
        self.assertEqual(saved.name, "Platform")
        self.assertEqual(saved.agent, "claude")
        self.assertEqual(saved.repos, repos)
        self.assertTrue(any("Ready to open" in title[0] for title in screen.titles))

    def test_new_workspace_cancellation_never_saves(self):
        screen = FakeScreen([], answers=[None])
        store, launcher = Mock(), Mock()
        self.assertIsNone(Picker(screen, store, launcher).new_workspace())
        store.save.assert_not_called()
        launcher.open.assert_not_called()

    def test_repository_filter_is_inline_and_selects_the_exact_checkout(self):
        repos = [Repository("/tmp/team/api", "api"), Repository("/tmp/other/web", "web")]
        screen = FakeScreen(list("web") + [" ", "\n"])
        picker = Picker(screen, Mock(), Mock())
        with patch("tui.discover_repositories", return_value=repos), patch("tui.checkout_branch", return_value="feature/auth"):
            self.assertEqual(picker.repositories([]), [repos[1]])
        self.assertFalse(screen.prompts)
        self.assertTrue(any("feature/auth" in text for _, text, _ in screen.writes))
        self.assertTrue(any("/tmp/other/web" in text for _, text, _ in screen.writes))

    def test_same_name_checkouts_have_distinct_paths_and_branches(self):
        repos = [Repository("/tmp/main/service", "service"), Repository("/tmp/experiment/service", "service")]
        screen = FakeScreen(["\x1b"])
        picker = Picker(screen, Mock(), Mock())
        with patch("tui.discover_repositories", return_value=repos), patch("tui.checkout_branch", side_effect=["experiment", "main"]):
            self.assertIsNone(picker.repositories([]))
        rendered = "\n".join(text for _, text, _ in screen.writes)
        self.assertIn("/tmp/main/service", rendered)
        self.assertIn("/tmp/experiment/service", rendered)
        self.assertIn("[main]", rendered)
        self.assertIn("[experiment]", rendered)

    def test_same_name_checkouts_can_be_saved_without_renaming_an_existing_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            from core import Store, make_repository
            base = Path(directory)
            paths = [base / "code" / folder / "api" for folder in ("a-new", "z-existing")]
            for path in paths:
                path.mkdir(parents=True)
                subprocess.run(["git", "init", "-q", str(path)], check=True)
            existing = make_repository(paths[1])
            store = Store(base / "config", base / "state")
            picker = Picker(FakeScreen([" ", "\n"]), store, Mock())
            with patch.dict(os.environ, {"HOME": str(base)}):
                repos = picker.repositories([existing])
            store.save(Workspace("platform", "Platform", repos))
            saved = {repo.path: repo.name for repo in store.load()[0].repos}
            self.assertEqual(saved, {str(paths[0].resolve()): "api-2", str(paths[1].resolve()): "api"})
            self.assertEqual(existing.name, "api")

    def test_checkout_tree_shows_real_path_and_branch(self):
        screen = FakeScreen([])
        picker = Picker(screen, Mock(), Mock())
        with patch("tui.checkout_branch", return_value="feature/orders"):
            picker.tree([Repository("/tmp/platform/orders", "orders")], 5, 20)
        rendered = "\n".join(text for _, text, _ in screen.writes)
        self.assertIn("└─ orders", rendered)
        self.assertIn("feature/orders", rendered)
        self.assertIn("/tmp/platform/orders", rendered)

    def test_branch_lookup_is_cached_while_typing(self):
        repos = [Repository("/tmp/platform/api", "api")]
        screen = FakeScreen(["a", "p", "i", "\x1b"])
        picker = Picker(screen, Mock(), Mock())
        with patch("tui.discover_repositories", return_value=repos), patch("tui.checkout_branch", return_value="main") as lookup:
            self.assertIsNone(picker.repositories([]))
        self.assertEqual(lookup.call_count, 1)

    def test_tree_always_counts_hidden_repositories(self):
        screen = FakeScreen([])
        picker = Picker(screen, Mock(), Mock())
        repos = [Repository("/tmp/" + name, name) for name in ("api", "web", "events", "config")]
        with patch("tui.checkout_branch", return_value="main"):
            picker.tree(repos, 8, 14)
        rendered = "\n".join(text for _, text, _ in screen.writes)
        self.assertIn("+ 2 more", rendered)

    def test_small_terminal_editor_keeps_fields_above_footer(self):
        screen = FakeScreen(["\x1b"], size=(14, 38))
        picker = Picker(screen, Mock(), Mock())
        self.assertIsNone(picker.edit(self.workspace()))
        self.assertTrue(all(row < 10 for row, _, _ in screen.writes))


if __name__ == "__main__":
    unittest.main()
