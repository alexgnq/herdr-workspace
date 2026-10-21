import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core import AGENTS, Repository, Store, UserError, Workspace, make_repository, name_repositories
from launcher import Herdr, Launcher, agent_arguments, prepare_directory, workspace_directory


class FakeHerdr:
    def __init__(self):
        self.calls = []
        self.workspaces = []
        self.panes = []
        self.start_error = None

    def call(self, *args):
        self.calls.append(args)
        if args[:2] == ("workspace", "list"):
            return {"workspaces": self.workspaces}
        if args[:2] == ("pane", "list"):
            return {"panes": self.panes}
        if args[:2] == ("workspace", "create"):
            return {"workspace": {"workspace_id": "w7"}, "tab": {"tab_id": "w7:t1"},
                    "root_pane": {"pane_id": "w7:p1", "terminal_id": "terminal-first"}}
        if args[:2] == ("tab", "create"):
            return {"tab": {"tab_id": "w7:t2"}, "root_pane": {"pane_id": "w7:p2", "terminal_id": "terminal-second"}}
        if args[:2] == ("agent", "start") and self.start_error:
            raise UserError(self.start_error)
        if args[:2] == ("workspace", "report-metadata"):
            return {}
        if args[:2] in (("agent", "start"), ("agent", "focus"), ("workspace", "focus"), ("tab", "focus")):
            return {}
        raise AssertionError("Unsupported Herdr command: " + str(args))


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / "config", self.root / "state")
        self.repo = self.root / "repo with 'quotes';$(echo no)"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.workspace = Workspace("platform", "Platform", [make_repository(self.repo)])
        self.backend = FakeHerdr()
        self.launcher = Launcher(self.store, self.backend, which=lambda name: "/bin/" + name)
        self.environment = patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/test/server.sock"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_starts_with_exact_repo_roots_and_normal_approvals(self):
        result = self.launcher.open(self.workspace)
        self.assertEqual(result.workspace_id, "w7")
        start = next(call for call in self.backend.calls if call[:2] == ("agent", "start"))
        self.assertEqual(start[start.index("--") + 1:],
                         ("--sandbox", "workspace-write", "--add-dir", str(self.repo)))
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", start)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p1"))
        created = next(call for call in self.backend.calls if call[:2] == ("workspace", "create"))
        self.assertNotEqual(created[created.index("--cwd") + 1], str(self.repo.parent))

    def test_every_agent_receives_all_directories(self):
        other = Repository(str(self.root / "other"), "other")
        for kind in ("codex", "claude", "copilot"):
            args = agent_arguments(kind, self.workspace.repos + [other])
            self.assertEqual(args.count("--add-dir"), 2)
            self.assertIn(str(self.repo), args)
            self.assertIn(other.path, args)

    def test_agents_without_folder_limits_get_no_directory_flags(self):
        self.assertEqual(agent_arguments("pi", self.workspace.repos), [])

    def test_agent_specific_environment_is_set_only_for_that_agent(self):
        for kind, expected in (("claude", True), ("codex", False), ("pi", False)):
            with self.subTest(agent=kind):
                self.backend.calls.clear()
                self.workspace.agent = kind
                self.launcher.open(self.workspace, new_agent=True)
                created = next(call for call in self.backend.calls if call[1] == "create")
                self.assertEqual("CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1" in created, expected)
                start = next(call for call in self.backend.calls if call[:2] == ("agent", "start"))
                self.assertEqual(start[start.index("--kind") + 1], kind)

    def test_every_agent_kind_is_supported_by_herdr(self):
        # Herdr 0.9.5 `agent start --kind` values; an adapter for anything else cannot launch.
        herdr_kinds = {"pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline", "omp",
                       "mastracode", "opencode", "copilot", "kimi", "kiro", "droid", "amp", "grok",
                       "hermes", "kilo", "qodercli", "qwen", "letta", "maki", "muse"}
        self.assertLessEqual(set(AGENTS), herdr_kinds)

    def test_reuses_existing_agent_after_its_directory_changes(self):
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p1"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_reuses_owned_agent_instead_of_a_manual_agent_of_the_same_kind(self):
        self.launcher.open(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [
            {"workspace_id": "w7", "pane_id": "w7:manual", "terminal_id": "manual-terminal", "cwd": str(self.root), "agent": "codex"},
            {"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"},
        ]
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p1"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_agent_in_generated_directory_is_not_assumed_to_have_workspace_permissions(self):
        root = prepare_directory(self.store, self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [
            {"workspace_id": "w7", "pane_id": "w7:manual", "terminal_id": "manual-terminal", "cwd": str(root), "agent": "codex"},
        ]
        self.launcher.open(self.workspace)
        self.assertTrue(any(call[:2] == ("tab", "create") for call in self.backend.calls))
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))

    def test_reset_server_does_not_reuse_manual_agent_with_stale_pane_id_and_root_cwd(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        root = workspace_directory(self.store, self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [
            {"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "manual-terminal", "cwd": str(root), "agent": "codex"},
        ]
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertTrue(any(call[:2] == ("tab", "create") for call in self.backend.calls))
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))
        self.backend.panes[0]["terminal_id"] = "restored-manual"
        self.backend.panes.append({"workspace_id": "w7", "pane_id": "w7:p2", "terminal_id": "restored-owned", "cwd": str(self.repo), "agent": "codex"})
        with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "startup"}):
            self.assertEqual(self.launcher.refresh(), 1)
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_new_agent_uses_a_new_tab_and_same_repo_set(self):
        root = prepare_directory(self.store, self.workspace)
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "cwd": str(root), "agent": "codex"}]
        self.launcher.open(self.workspace, new_agent=True)
        self.assertTrue(any(call[:2] == ("tab", "create") for call in self.backend.calls))
        self.assertFalse(any(call[:2] == ("workspace", "create") for call in self.backend.calls))
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))

    def test_different_agent_kinds_share_a_space_and_can_be_focused_independently(self):
        self.launcher.open(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.backend.calls.clear()
        self.workspace.agent = "copilot"
        self.assertEqual(self.launcher.open(self.workspace).workspace_id, "w7")
        created = next(call for call in self.backend.calls if call[:2] == ("tab", "create"))
        self.assertEqual(created[created.index("--workspace") + 1], "w7")
        started = next(call for call in self.backend.calls if call[:2] == ("agent", "start"))
        self.assertEqual(started[started.index("--kind") + 1], "copilot")
        self.assertFalse(any(call[:2] == ("workspace", "create") for call in self.backend.calls))
        self.backend.panes.append({"workspace_id": "w7", "pane_id": "w7:p2", "terminal_id": "terminal-second", "cwd": str(self.repo), "agent": "copilot"})
        for kind, pane in (("codex", "w7:p1"), ("copilot", "w7:p2")):
            self.backend.calls.clear()
            self.workspace.agent = kind
            self.launcher.open(self.workspace)
            self.assertEqual(self.backend.calls[-1], ("agent", "focus", pane))
            self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_socket_namespaces_do_not_reuse_other_sessions(self):
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "cwd": str(self.repo), "agent": "codex"}]
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/another/server.sock"}):
            self.launcher.open(self.workspace)
        self.assertTrue(any(call[:2] == ("workspace", "create") for call in self.backend.calls))

    def test_closed_workspace_record_is_not_reused(self):
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w9", "label": "Platform"}]
        self.launcher.open(self.workspace)
        self.assertTrue(any(call[:2] == ("workspace", "create") for call in self.backend.calls))

    def test_reset_server_cannot_focus_an_unrelated_agent_with_reused_ids(self):
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "different-terminal", "cwd": str(self.repo), "agent": "codex"}]
        self.launcher.open(self.workspace)
        self.assertTrue(any(call[:2] == ("workspace", "create") for call in self.backend.calls))

    def test_changed_repo_set_gets_separate_context(self):
        old = workspace_directory(self.store, self.workspace)
        another = self.root / "another"
        another.mkdir()
        subprocess.run(["git", "init", "-q", str(another)], check=True)
        self.workspace.repos.append(make_repository(another))
        self.assertNotEqual(old, workspace_directory(self.store, self.workspace))

    def add_repository(self, name):
        path = self.root / name
        path.mkdir()
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        self.workspace.repos.append(make_repository(path))
        return path

    def test_changed_repo_set_opens_a_new_tab_in_the_same_space(self):
        self.launcher.open(self.workspace)
        old_root = workspace_directory(self.store, self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(old_root), "agent": "codex"}]
        another = self.add_repository("another")
        self.backend.calls.clear()
        result = self.launcher.open(self.workspace)
        self.assertEqual(result.workspace_id, "w7")
        self.assertFalse(any(call[:2] == ("workspace", "create") for call in self.backend.calls))
        tab = next(call for call in self.backend.calls if call[:2] == ("tab", "create"))
        self.assertEqual(tab[tab.index("--workspace") + 1], "w7")
        self.assertEqual(tab[tab.index("--cwd") + 1], str(workspace_directory(self.store, self.workspace)))
        start = next(call for call in self.backend.calls if call[:2] == ("agent", "start"))
        self.assertIn(str(another), start)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))
        # The running agent keeps the folder, links and instructions it started with.
        self.assertTrue((old_root / "AGENTS.md").exists())
        self.assertNotIn("another", (old_root / "AGENTS.md").read_text())

    def test_reopening_after_a_repo_change_focuses_the_agent_with_the_new_set(self):
        self.launcher.open(self.workspace)
        old_root = workspace_directory(self.store, self.workspace)
        self.add_repository("another")
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(old_root), "agent": "codex"}]
        self.launcher.open(self.workspace)
        self.backend.panes.append({"workspace_id": "w7", "pane_id": "w7:p2", "terminal_id": "terminal-second",
                                   "cwd": str(workspace_directory(self.store, self.workspace)), "agent": "codex"})
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_refresh_after_a_repo_change_shows_the_new_set_in_the_same_space(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        old_root = workspace_directory(self.store, self.workspace)
        self.add_repository("another")
        self.store.save(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(old_root), "agent": "codex"}]
        self.backend.calls.clear()
        self.assertEqual(self.launcher.refresh(), 1)
        metadata = next(call for call in self.backend.calls if call[:2] == ("workspace", "report-metadata"))
        self.assertEqual(metadata[2], "w7")
        self.assertTrue(any("another" in argument for argument in metadata))

    def change_repos_after_opening(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        old_root = workspace_directory(self.store, self.workspace)
        self.add_repository("another")
        self.store.save(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        return old_root

    def test_unused_folder_of_an_earlier_repo_set_is_removed_on_open(self):
        old_root = self.change_repos_after_opening()
        self.launcher.open(self.workspace)
        self.assertFalse(old_root.exists())
        self.assertTrue(self.repo.is_dir())
        self.assertNotIn(old_root.name, self.launcher._registry())
        self.assertTrue(workspace_directory(self.store, self.workspace).is_dir())

    def test_folder_used_by_a_running_agent_is_kept(self):
        old_root = self.change_repos_after_opening()
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.launcher.open(self.workspace)
        self.assertTrue((old_root / "AGENTS.md").exists())

    def test_folder_with_notes_missing_from_the_current_folder_is_kept(self):
        old_root = self.change_repos_after_opening()
        self.launcher.open(self.workspace)
        another = self.add_repository("third")
        self.store.save(self.workspace)
        second_root = workspace_directory(self.store, Workspace(
            self.workspace.id, self.workspace.name, [r for r in self.workspace.repos if r.path != str(another)]))
        agents = second_root / "AGENTS.md"
        agents.write_text(agents.read_text() + "Written after the repo change.\n")
        self.launcher.open(self.workspace)
        self.assertFalse(old_root.exists())
        self.assertFalse(second_root.exists())
        self.assertIn("Written after the repo change.", (workspace_directory(self.store, self.workspace) / "AGENTS.md").read_text())

    def test_notes_written_by_an_old_agent_after_a_repo_change_keep_its_folder(self):
        old_root = self.change_repos_after_opening()
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(old_root), "agent": "codex"}]
        self.launcher.open(self.workspace)
        agents = old_root / "AGENTS.md"
        agents.write_text(agents.read_text() + "Only in the old folder.\n")
        self.backend.panes = []
        self.launcher.open(self.workspace)
        self.assertIn("Only in the old folder.", agents.read_text())

    def test_folder_owned_by_another_herdr_session_is_kept(self):
        old_root = self.change_repos_after_opening()
        with patch.dict(os.environ, {"HERDR_SOCKET_PATH": "/other/session.sock"}):
            self.backend.workspaces, self.backend.panes = [], []
            self.launcher.open(self.workspace)
        self.assertTrue((old_root / "AGENTS.md").exists())

    def test_folder_with_unknown_files_is_left_untouched(self):
        old_root = self.change_repos_after_opening()
        (old_root / "scratch.txt").write_text("agent output")
        self.launcher.open(self.workspace)
        self.assertEqual((old_root / "scratch.txt").read_text(), "agent output")
        self.assertTrue((old_root / "AGENTS.md").exists())
        self.assertTrue(any(path.is_symlink() for path in old_root.iterdir()))

    def test_closed_space_after_a_repo_change_creates_one_new_space(self):
        self.launcher.open(self.workspace)
        self.add_repository("another")
        self.backend.workspaces = []
        self.backend.panes = []
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(sum(call[:2] == ("workspace", "create") for call in self.backend.calls), 1)

    def test_repository_reordering_preserves_colliding_aliases(self):
        repositories = []
        for name in ("a b", "a-b"):
            path = self.root / name
            path.mkdir()
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            repositories.append(make_repository(path))
        self.workspace.repos = repositories
        root = prepare_directory(self.store, self.workspace)
        target = (root / "a-b").resolve()
        self.workspace.repos.reverse()
        self.assertEqual(root, prepare_directory(self.store, self.workspace))
        self.assertEqual(target, (root / "a-b").resolve())

    def test_reselecting_saved_checkouts_preserves_aliases_and_generated_directory(self):
        other = self.root / "api"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.workspace.repos = [Repository(str(self.repo), "primary-api"), make_repository(other)]
        original = list(self.workspace.repos)
        root = prepare_directory(self.store, self.workspace)
        discovered = [make_repository(repo.path) for repo in reversed(original)]
        self.workspace.repos = name_repositories(discovered, original)
        self.assertEqual(prepare_directory(self.store, self.workspace), root)
        self.assertEqual((root / "primary-api").resolve(), self.repo)
        self.assertEqual((root / "api").resolve(), other)

    def test_reserved_and_case_colliding_names_get_distinct_links(self):
        repositories = []
        for folder, name in (("one", "workspace.json"), ("two", "Api"), ("three", "api"), ("four", "agents.md")):
            path = self.root / folder
            path.mkdir()
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            repositories.append(Repository(str(path), name))
        self.workspace.repos = repositories
        root = prepare_directory(self.store, self.workspace)
        links = {item["checkout"]: item["link"] for item in json.loads((root / "workspace.json").read_text())["repositories"]}
        self.assertEqual(len({link.casefold() for link in links.values()}), 4)
        self.assertNotIn("workspace.json", links.values())
        self.assertNotIn("agents.md", links.values())
        for checkout, link in links.items():
            self.assertEqual((root / link).resolve(), Path(checkout))

    def test_notes_below_the_marker_survive_reopening_and_repo_changes(self):
        root = prepare_directory(self.store, self.workspace)
        agents = root / "AGENTS.md"
        agents.write_text(agents.read_text() + "\nThe API calls config over gRPC.\n")
        prepare_directory(self.store, self.workspace)
        self.assertIn("The API calls config over gRPC.", agents.read_text())
        self.assertEqual((root / "CLAUDE.md").read_text(), agents.read_text())
        another = self.root / "another"
        another.mkdir()
        subprocess.run(["git", "init", "-q", str(another)], check=True)
        self.workspace.repos.append(make_repository(another))
        changed = prepare_directory(self.store, self.workspace)
        self.assertNotEqual(changed, root)
        text = (changed / "AGENTS.md").read_text()
        self.assertIn("The API calls config over gRPC.", text)
        self.assertIn("another", text)
        self.assertEqual(text.count("This workspace contains"), 1)

    def test_legacy_generated_claude_file_becomes_a_link_to_agents(self):
        root = workspace_directory(self.store, self.workspace)
        root.mkdir(parents=True)
        (root / "CLAUDE.md").write_text("# Platform workspace\n")
        prepare_directory(self.store, self.workspace)
        self.assertEqual(os.readlink(root / "CLAUDE.md"), "AGENTS.md")

    def test_startup_prompt_keeps_agent_visible_without_retrying(self):
        self.backend.start_error = "Herdr: agent_not_ready: confirm workspace trust"
        result = self.launcher.open(self.workspace)
        self.assertIn("confirm workspace trust", result.message)
        self.assertEqual(self.backend.calls[-2:], [("workspace", "focus", "w7"), ("tab", "focus", "w7:t1")])
        self.assertEqual(sum(call[:2] == ("agent", "start") for call in self.backend.calls), 1)

    def test_missing_checkout_stops_before_any_herdr_calls(self):
        self.workspace.repos[0].path = str(self.root / "missing")
        with self.assertRaises(UserError):
            self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls, [])

    def test_launch_requires_herdr_but_configuration_does_not(self):
        with patch.dict(os.environ, {"HERDR_ENV": ""}):
            with self.assertRaisesRegex(UserError, "Herdr pane"):
                self.launcher.open(self.workspace)
            self.store.save(self.workspace)
        self.assertEqual(self.backend.calls, [])

    def test_generated_context_preserves_imported_names_and_original_checkout(self):
        self.workspace.repos[0].name = "service-api"
        root = prepare_directory(self.store, self.workspace)
        self.assertEqual((root / "service-api").resolve(), self.repo.resolve())
        self.assertIn("service-api", (root / "AGENTS.md").read_text())
        self.assertIn("read its AGENTS.md or CLAUDE.md", (root / "CLAUDE.md").read_text())
        self.assertFalse((self.repo / "AGENTS.md").exists())

    def test_workspace_instruction_symlinks_are_not_followed(self):
        root = workspace_directory(self.store, self.workspace)
        root.mkdir(parents=True)
        target = self.root / "outside"
        target.write_text("keep me")
        (root / "AGENTS.md").symlink_to(target)
        with self.assertRaises(UserError):
            prepare_directory(self.store, self.workspace)
        self.assertEqual(target.read_text(), "keep me")

    def test_generated_summary_maps_links_to_exact_shared_checkouts(self):
        root = prepare_directory(self.store, self.workspace)
        summary = json.loads((root / "workspace.json").read_text())
        self.assertEqual(summary["repo_access"], "symlinks-to-existing-checkouts")
        self.assertEqual(summary["repositories"][0]["checkout"], str(self.repo))
        link = root / summary["repositories"][0]["link"]
        (link / "test-change").write_text("shared edit")
        self.assertEqual((self.repo / "test-change").read_text(), "shared edit")

    def test_an_accidental_repo_copy_is_rejected(self):
        root = prepare_directory(self.store, self.workspace)
        summary = json.loads((root / "workspace.json").read_text())
        link = root / summary["repositories"][0]["link"]
        link.unlink()
        link.mkdir()
        (link / "copy-file").write_text("keep copy")
        with self.assertRaisesRegex(UserError, "already occupies"):
            prepare_directory(self.store, self.workspace)
        self.assertEqual((link / "copy-file").read_text(), "keep copy")

    def test_retargeted_repo_link_is_rejected(self):
        root = prepare_directory(self.store, self.workspace)
        summary = json.loads((root / "workspace.json").read_text())
        link = root / summary["repositories"][0]["link"]
        link.unlink()
        link.symlink_to(self.root)
        with self.assertRaisesRegex(UserError, "link changed"):
            prepare_directory(self.store, self.workspace)

    def test_refresh_updates_only_owned_spaces_without_focus_or_agent_launch(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}, {"workspace_id": "w9", "label": "Other"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.assertEqual(self.launcher.refresh(), 1)
        metadata = [call for call in self.backend.calls if call[:2] == ("workspace", "report-metadata")]
        self.assertEqual(len(metadata), 1)
        self.assertIn("w7", metadata[0])
        self.assertFalse(any(call[1] in ("create", "start", "focus") for call in self.backend.calls))

    def test_refresh_continues_past_a_broken_workspace_and_reports_it(self):
        other = self.root / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        broken = Workspace("broken", "Broken", [make_repository(other)])
        for workspace, space in ((broken, "w1"), (self.workspace, "w2")):
            self.store.save(workspace)
            self.backend.workspaces.append({"workspace_id": space, "label": workspace.name})
            self.backend.panes.append({"workspace_id": space, "pane_id": space + ":p1", "terminal_id": space,
                                       "cwd": str(prepare_directory(self.store, workspace)), "agent": "codex"})
        link = workspace_directory(self.store, broken) / "other"
        link.unlink()
        link.symlink_to(self.root)
        with self.assertRaisesRegex(UserError, "Refreshed 1 workspace.*Broken.*link changed"):
            self.launcher.refresh()
        metadata = [call for call in self.backend.calls if call[:2] == ("workspace", "report-metadata")]
        self.assertEqual([call[2] for call in metadata], ["w2"])

    def test_startup_refresh_recognizes_restored_pane_and_rebinds_terminal_id(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        self.backend.calls.clear()
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "restored-terminal", "cwd": str(self.repo), "agent": "codex"}]
        with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "startup"}):
            self.assertEqual(self.launcher.refresh(), 1)
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p1"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_startup_restores_ownership_for_every_agent_in_a_space(self):
        self.launcher.open(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "Platform"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.workspace.agent = "claude"
        self.launcher.open(self.workspace)
        self.store.save(self.workspace)
        self.backend.panes[0]["terminal_id"] = "restored-first"
        self.backend.panes.append({"workspace_id": "w7", "pane_id": "w7:p2", "terminal_id": "restored-second", "cwd": str(self.repo), "agent": "claude"})
        self.backend.panes.insert(0, {"workspace_id": "w7", "pane_id": "w7:manual", "terminal_id": "manual-terminal", "cwd": str(self.root), "agent": "claude"})
        with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "startup"}):
            self.assertEqual(self.launcher.refresh(), 1)
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p2"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))

    def test_renaming_a_space_keeps_its_checkout_identity_after_refresh_and_restart(self):
        self.store.save(self.workspace)
        self.launcher.open(self.workspace)
        self.backend.workspaces = [{"workspace_id": "w7", "label": "My platform work"}]
        self.backend.panes = [{"workspace_id": "w7", "pane_id": "w7:p1", "terminal_id": "terminal-first", "cwd": str(self.repo), "agent": "codex"}]
        self.assertEqual(self.launcher.refresh(), 1)
        self.backend.panes[0]["terminal_id"] = "restored-terminal"
        with patch.dict(os.environ, {"HERDR_PLUGIN_EVENT": "startup"}):
            self.assertEqual(self.launcher.refresh(), 1)
        self.backend.calls.clear()
        self.launcher.open(self.workspace)
        self.assertEqual(self.backend.calls[-1], ("agent", "focus", "w7:p1"))
        self.assertFalse(any(call[1] in ("create", "start") for call in self.backend.calls))


class TransportTests(unittest.TestCase):
    def test_quiet_metadata_and_reload_commands_accept_success_without_json(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("launcher.subprocess.run", return_value=completed):
            self.assertEqual(Herdr().call("workspace", "report-metadata", "w7"), {})
            self.assertEqual(Herdr().call("server", "reload-config"), {})
            with self.assertRaises(UserError):
                Herdr().call("workspace", "list")

    def test_parses_errors_from_stderr(self):
        completed = subprocess.CompletedProcess([], 1, "", json.dumps({"id": "1", "error": {"code": "agent_not_ready", "message": "Trust this workspace"}}))
        with patch("launcher.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(UserError, "Trust this workspace"):
                Herdr().call("agent", "start")

    def test_commands_use_arguments_without_a_shell(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps({"id": "1", "result": {"panes": []}}), "")
        with patch("launcher.subprocess.run", return_value=completed) as run:
            Herdr("/bin/herdr").call("workspace", "create", "--label", "$(touch nope)")
        self.assertEqual(run.call_args[0][0][-1], "$(touch nope)")
        self.assertFalse(run.call_args[1].get("shell", False))


if __name__ == "__main__":
    unittest.main()
