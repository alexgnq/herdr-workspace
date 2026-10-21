from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from core import Repository, UserError, Workspace
from sidebar import DEFAULT_ROWS, MAX_REPOSITORIES, TOKEN_KEYS, install_sidebar, publish_workspace, repository_tokens, sidebar_rows


class SidebarTests(unittest.TestCase):
    def workspace(self, count=4):
        return Workspace("platform", "Platform", [Repository("/repos/repo" + str(n), "repo" + str(n)) for n in range(count)])

    def test_repo_tree_has_clear_last_branch_and_bounded_values(self):
        values = repository_tokens(self.workspace())
        self.assertEqual(values["mr_repo1"], "├─ repo0")
        self.assertEqual(values["mr_repo4"], "└─ repo3")
        self.assertNotIn("mr_more", values)
        workspace = self.workspace(10)
        workspace.repos[0].name = "x" * 200
        values = repository_tokens(workspace)
        self.assertEqual(len(values["mr_repo1"]), 80)
        self.assertEqual(values["mr_more"], "└─ + 2 more repositories")

    def test_metadata_patch_clears_previous_extra_rows(self):
        herdr = Mock()
        publish_workspace(herdr, "w7", self.workspace(1))
        args = herdr.call.call_args.args
        self.assertEqual(args[:5], ("workspace", "report-metadata", "w7", "--source", "herdr-multirepo"))
        self.assertIn("mr_repo1=└─ repo0", args)
        self.assertIn("mr_repo8", args)
        self.assertIn("mr_more", args)
        self.assertEqual(args.count("--clear-token"), 8)

    def test_layout_preserves_custom_rows_and_is_idempotent(self):
        original = [["state_icon", {"token": "workspace", "bold": True}], ["$custom"]]
        result = sidebar_rows(original)
        self.assertEqual(result[:2], original)
        self.assertEqual(sidebar_rows(result), result)
        self.assertEqual(len(result), 11)
        self.assertEqual(original, [["state_icon", {"token": "workspace", "bold": True}], ["$custom"]])

    def test_adds_config_without_changing_theme_and_backup_is_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = 'onboarding = false\n\n[theme]\nname = "dracula"\nauto_switch = false\n'
            path.write_text(original)
            self.assertTrue(install_sidebar(path))
            self.assertTrue(path.read_text().startswith(original.rstrip()))
            backup = path.with_name("config.toml.before-herdr-multirepo")
            self.assertEqual(backup.read_text(), original)
            self.assertFalse(install_sidebar(path))
            self.assertEqual(backup.read_text(), original)

    def test_existing_styled_rows_are_retained_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = '''[theme]
name = "dracula"
[ui.sidebar.spaces]
row_gap = 1
rows = [
  ["state_icon", { token = "workspace", fg = "#89b4fa", bold = true }],
  ["$custom"], # a custom line
]
[ui.sound]
enabled = false
'''
            path.write_text(original)
            self.assertTrue(install_sidebar(path))
            result = path.read_text()
            self.assertIn('["state_icon", { token = "workspace", fg = "#89b4fa", bold = true }]', result)
            self.assertIn('["$custom"]', result)
            self.assertIn("row_gap = 1", result)
            self.assertIn("[ui.sound]\nenabled = false", result)
            self.assertFalse(install_sidebar(path))

    def test_rows_inline_array_and_quoted_brackets_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[ui.sidebar.spaces]\nrows = [["workspace"], ["$bracket[or]#tag"]] # keep suffix\n')
            self.assertTrue(install_sidebar(path))
            self.assertIn('["$bracket[or]#tag"]', path.read_text())
            self.assertIn("# keep suffix", path.read_text())

    def test_fails_without_writing_if_existing_rows_leave_no_room(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = '[ui.sidebar.spaces]\nrows = [' + ','.join('["workspace"]' for _ in range(8)) + ']\n'
            path.write_text(original)
            with self.assertRaisesRegex(UserError, "16 rows"):
                install_sidebar(path)
            self.assertEqual(path.read_text(), original)
            self.assertFalse(path.with_name("config.toml.before-herdr-multirepo").exists())

    def test_incomplete_rows_leave_config_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            original = '[ui.sidebar.spaces]\nrows = [["workspace"]\n'
            path.write_text(original)
            with self.assertRaises(UserError):
                install_sidebar(path)
            self.assertEqual(path.read_text(), original)

    def test_adding_rows_to_existing_section_without_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[ui.sidebar.spaces]\nrow_gap = 1\n[theme]\nname = "dracula"\n')
            self.assertTrue(install_sidebar(path))
            self.assertIn("row_gap = 1", path.read_text())
            self.assertEqual(path.read_text().count("[ui.sidebar.spaces]"), 1)

    def test_resolves_config_symlink_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "real.toml"
            path.write_text('[theme]\nname = "dracula"\n')
            link = Path(directory) / "config.toml"
            link.symlink_to(path)
            self.assertTrue(install_sidebar(link))
            self.assertTrue(link.is_symlink())
            self.assertIn("$mr_repo1", path.read_text())

    def test_preserves_custom_styles_on_existing_repo_tokens(self):
        styled = {"token": "$mr_repo1", "fg": "#89b4fa", "dim": True}
        rows = sidebar_rows([["workspace"], [styled]])
        self.assertEqual(rows[1], [styled])
        self.assertNotIn(["$mr_repo1"], rows)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text('[ui.sidebar.spaces]\nrows = [["workspace"], [{ token = "$mr_repo1", fg = "#89b4fa", dim = true }]]\n')
            self.assertTrue(install_sidebar(path))
            self.assertIn('[{ token = "$mr_repo1", fg = "#89b4fa", dim = true }]', path.read_text())
            self.assertEqual(path.read_text().count('"$mr_repo1"'), 1)
            self.assertFalse(install_sidebar(path))


if __name__ == "__main__":
    unittest.main()
