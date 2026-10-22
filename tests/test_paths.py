import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paths import directory_completions


class DirectoryCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def folder(self, name):
        path = self.root / name
        path.mkdir(parents=True)
        return path

    def test_empty_and_tilde_start_at_home_without_scanning(self):
        with patch("paths.os.scandir") as scan:
            self.assertEqual(directory_completions(""), ["~/"])
            self.assertEqual(directory_completions("~"), ["~/"])
        scan.assert_not_called()

    def test_absolute_prefix_matches_directory_names_case_insensitively(self):
        self.folder("Development")
        self.folder("dev tools")
        self.folder("other")
        (self.root / "device.txt").write_text("file")
        self.assertEqual(directory_completions(str(self.root / "DE")),
                         [str(self.root / "dev tools") + "/", str(self.root / "Development") + "/"])

    def test_home_prefix_keeps_tilde_and_completes_one_component(self):
        self.folder("code/api")
        self.folder("code/app")
        self.folder("code/app/nested")
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            self.assertEqual(directory_completions("~/co"), ["~/code/"])
            self.assertEqual(directory_completions("~/code/a"), ["~/code/api/", "~/code/app/"])
            self.assertEqual(directory_completions("~/"), ["~/code/"])

    def test_relative_prefix_and_trailing_slash_keep_the_input_spelling(self):
        self.folder("code/api")
        self.folder("code/web app")
        original = Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(directory_completions("co"), ["code/"])
            self.assertEqual(directory_completions("./co"), ["./code/"])
            self.assertEqual(directory_completions("code/"), ["code/api/", "code/web app/"])
            self.assertEqual(directory_completions("code//w"), ["code//web app/"])
            self.assertEqual(directory_completions("code/../co"), ["code/../code/"])
        finally:
            os.chdir(original)

    def test_hidden_folders_need_a_dot_prefix(self):
        self.folder(".config")
        self.folder(".cache")
        self.folder("code")
        self.assertEqual(directory_completions(str(self.root) + "/"), [str(self.root / "code") + "/"])
        self.assertEqual(directory_completions(str(self.root) + "/.c"),
                         [str(self.root / ".cache") + "/", str(self.root / ".config") + "/"])

    def test_directory_symlinks_are_kept_but_files_broken_links_and_cycles_are_skipped(self):
        target = self.folder("target/child")
        (self.root / "alias").symlink_to(target.parent, target_is_directory=True)
        (self.root / "broken").symlink_to(self.root / "absent")
        (self.root / "loop").symlink_to("loop")
        (self.root / "file").write_text("file")
        (self.root / "file-link").symlink_to(self.root / "file")
        self.folder("bad\nname")
        self.assertEqual(directory_completions(str(self.root) + "/"),
                         [str(self.root / "alias") + "/", str(self.root / "target") + "/"])
        self.assertEqual(directory_completions(str(self.root / "alias") + "/"),
                         [str(self.root / "alias/child") + "/"])
        self.assertEqual(directory_completions(str(self.root / "loop") + "/"), [])

    def test_missing_inaccessible_or_malformed_paths_return_no_suggestions(self):
        self.assertEqual(directory_completions(str(self.root / "missing") + "/"), [])
        for value in (None, 5, [], "bad\npath", "bad\x00path", "~not-a-user/folder"):
            with self.subTest(value=value):
                self.assertEqual(directory_completions(value), [])
        with patch("paths.os.scandir", side_effect=PermissionError("denied")):
            self.assertEqual(directory_completions(str(self.root) + "/"), [])

    def test_root_prefix_lists_direct_children(self):
        suggestions = directory_completions("/")
        top_level = "/" + self.root.resolve().parts[1] + "/"
        self.assertIn(top_level, suggestions)
        self.assertTrue(all(path.startswith("/") and path.rstrip("/").count("/") == 1
                            for path in suggestions))


if __name__ == "__main__":
    unittest.main()
