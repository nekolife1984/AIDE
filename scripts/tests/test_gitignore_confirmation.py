import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import aide_install


class GitignoreConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self.ignore = self.root / ".gitignore"
        self.ignore.write_bytes(b"custom/\n")

    def run_cli(self, mode="--apply", answer="yes", tty=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with mock.patch("sys.stdin.isatty", return_value=tty), mock.patch("builtins.input", return_value=answer) as prompt:
                code = aide_install.main([str(self.root), mode])
        return code, output.getvalue(), prompt.call_count

    def assert_unchanged(self, original):
        self.assertEqual(self.ignore.read_bytes(), original)
        self.assertFalse((self.root / ".agents").exists())

    def test_confirm_preserves_bytes_mode_and_ignores_config(self):
        for original in (b"custom/", b"custom/\n", b"custom/\r\n", b"", b"# non-UTF-8: \xff\n"):
            with self.subTest(original=original):
                self.ignore.write_bytes(original)
                self.ignore.chmod(0o640)
                code, output, prompts = self.run_cli()
                self.assertEqual(code, 0, output)
                self.assertEqual(prompts, 1)
                self.assertTrue(self.ignore.read_bytes().startswith(original))
                self.assertEqual(self.ignore.stat().st_mode & 0o777, 0o640)
                self.assertTrue(aide_install.project_config_is_ignored(self.root))
                if b"\r\n" in original:
                    self.assertNotIn(b"\n", self.ignore.read_bytes().replace(b"\r\n", b""))

    def test_decline_and_noninteractive_do_not_write_any_files(self):
        for answer, tty in (("no", True), ("", True), ("yes", False)):
            with self.subTest(answer=answer, tty=tty):
                original = self.ignore.read_bytes()
                code, output, prompts = self.run_cli(answer=answer, tty=tty)
                self.assertNotEqual(code, 0, output)
                self.assertEqual(prompts, int(tty))
                self.assert_unchanged(original)

    def test_eof_does_not_write(self):
        original = self.ignore.read_bytes()
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=EOFError):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aide_install.main([str(self.root), "--apply"]), 1)
        self.assert_unchanged(original)

    def test_dry_run_displays_append_without_prompt_or_writes(self):
        original = self.ignore.read_bytes()
        code, output, prompts = self.run_cli(mode="--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("追記案", output)
        self.assertEqual(prompts, 0)
        self.assert_unchanged(original)

    def test_equivalent_ignore_rules_skip_confirmation_and_append(self):
        for original in (b"/.agents/\n", b"*.local.json\n", b"/.agents/project.local.json\n"):
            with self.subTest(original=original):
                self.ignore.write_bytes(original)
                code, output, prompts = self.run_cli(tty=False)
                self.assertEqual(code, 0, output)
                self.assertEqual(prompts, 0)
                self.assertEqual(self.ignore.read_bytes(), original)

    def test_negation_is_overridden_by_confirmed_append(self):
        self.ignore.write_bytes(b"*.json\n!.agents/project.local.json\n")
        code, output, prompts = self.run_cli()
        self.assertEqual(code, 0, output)
        self.assertEqual(prompts, 1)
        self.assertTrue(aide_install.project_config_is_ignored(self.root))

    def test_nested_negation_rolls_back_all_changes(self):
        agents = self.root / ".agents"
        agents.mkdir()
        (agents / ".gitignore").write_bytes(b"!project.local.json\n")
        original = self.ignore.read_bytes()
        code, output, _ = self.run_cli()
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.ignore.read_bytes(), original)
        self.assertEqual(list(agents.iterdir()), [agents / ".gitignore"])

    def test_changed_during_confirmation_stops_without_overwriting(self):
        def change(_):
            self.ignore.write_bytes(b"user change\n")
            return "yes"
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=change):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(aide_install.main([str(self.root), "--apply"]), 0)
        self.assert_unchanged(b"user change\n")

    def test_symlink_created_during_confirmation_is_not_followed(self):
        outside = self.root / "outside"
        outside.write_bytes(b"keep\n")
        def change(_):
            self.ignore.unlink()
            self.ignore.symlink_to(outside)
            return "yes"
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", side_effect=change):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(aide_install.main([str(self.root), "--apply"]), 0)
        self.assertEqual(outside.read_bytes(), b"keep\n")
        self.assertFalse((self.root / ".agents").exists())

    def test_missing_gitignore_keeps_noninteractive_creation(self):
        self.ignore.unlink()
        code, output, prompts = self.run_cli(tty=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(prompts, 0)
        self.assertTrue(aide_install.project_config_is_ignored(self.root))

    def test_git_failure_stops_without_writes(self):
        original = self.ignore.read_bytes()
        with mock.patch.object(aide_install.subprocess, "run", side_effect=FileNotFoundError):
            code, _, prompts = self.run_cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(prompts, 0)
        self.assert_unchanged(original)

    def test_content_changed_before_append_rolls_back_installed_files(self):
        real_install = aide_install.install
        def change_before_install(root, additions, migrations, cleanup, append):
            self.ignore.write_bytes(b"new user rule\n")
            return real_install(root, additions, migrations, cleanup, append)
        with mock.patch.object(aide_install, "install", side_effect=change_before_install):
            code, output, _ = self.run_cli()
        self.assertNotEqual(code, 0, output)
        self.assert_unchanged(b"new user rule\n")

    def test_failure_after_append_restores_original(self):
        original = self.ignore.read_bytes()
        real_check = aide_install.project_config_is_ignored
        calls = 0
        def fail_after_append(root):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise aide_install.InstallError("fixture failure after append")
            return real_check(root)
        with mock.patch.object(aide_install, "project_config_is_ignored", side_effect=fail_after_append):
            code, output, _ = self.run_cli()
        self.assertNotEqual(code, 0, output)
        self.assert_unchanged(original)


if __name__ == "__main__":
    unittest.main()
