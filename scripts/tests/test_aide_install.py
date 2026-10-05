import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.aide_install as aide_install


class AideInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = aide_install.main([str(self.target), *args])
        return code, output.getvalue()

    def test_dry_run_does_not_modify_target_then_apply_installs_manifest(self):
        code, output = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", output)
        self.assertEqual(list(self.target.iterdir()), [])

        code, _ = self.run_cli("--apply")
        self.assertEqual(code, 0)
        for relative in aide_install.load_manifest():
            self.assertTrue((self.target / relative).is_file(), relative)
        self.assertFalse((self.target / ".agents/project.json").exists())

    def test_workflow_skill_is_distributed_with_resolvable_guidance(self):
        relative = ".agents/skills/aide-workflow/SKILL.md"
        code, output = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(relative, output)
        self.assertEqual(list(self.target.iterdir()), [])

        self.assertEqual(self.run_cli("--apply")[0], 0)
        skill = self.target / relative
        self.assertTrue(skill.is_file())
        self.assertEqual(skill.read_bytes(), (aide_install.REPO_ROOT / relative).read_bytes())
        template = self.target / ".agents/templates/AGENTS.md.template"
        self.assertTrue(template.is_file())
        self.assertIn(relative, template.read_text(encoding="utf-8"))
        self.assertFalse((self.target / "AGENTS.md").exists())

        from scripts.check_markdown_links import validate_repository

        errors, _ = validate_repository(self.target)
        self.assertEqual(errors, [])
        self.assertFalse((self.target / ".agents/project.json").exists())

    def test_rerun_reports_identical_and_preserves_files(self):
        self.assertEqual(self.run_cli("--apply")[0], 0)
        before = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        code, output = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertIn("同一", output)
        after = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_conflict_prevents_all_writes_and_shows_integration_proposals(self):
        (self.target / "AGENTS.md").write_text("custom agents\n", encoding="utf-8")
        (self.target / ".gitignore").write_text("custom ignore\n", encoding="utf-8")
        conflict_path = " .agents/docs/00_index.md".strip()
        conflict = self.target / conflict_path
        conflict.parent.mkdir(parents=True)
        conflict.write_text("custom index\n", encoding="utf-8")

        code, output = self.run_cli("--apply")
        self.assertEqual(code, 1)
        self.assertIn("競合", output)
        self.assertIn("統合案", output)
        self.assertEqual((self.target / "AGENTS.md").read_text(encoding="utf-8"), "custom agents\n")
        self.assertEqual((self.target / ".gitignore").read_text(encoding="utf-8"), "custom ignore\n")
        self.assertEqual(
            {path.relative_to(self.target) for path in self.target.rglob("*.md")},
            {Path("AGENTS.md"), Path(".agents/docs/00_index.md")},
        )

    def test_project_configuration_and_secrets_are_not_in_manifest(self):
        manifest = aide_install.load_manifest()
        self.assertNotIn(".agents/project.json", manifest)
        self.assertFalse(any("token" in path.lower() or ".env" in path for path in manifest))

    def test_gitignore_template_fallback_is_available_for_npm_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = root / "scripts/templates/gitignore"
            template.parent.mkdir(parents=True)
            template.write_bytes((aide_install.REPO_ROOT / "scripts/templates/gitignore").read_bytes())
            with mock.patch.object(aide_install, "REPO_ROOT", root):
                self.assertEqual(aide_install.source_path(".gitignore"), template)
                self.assertEqual(aide_install.source_path(".gitignore").read_bytes(), template.read_bytes())

    def test_existing_agents_is_not_touched_by_installer(self):
        agents = self.target / "AGENTS.md"
        gitignore = self.target / ".gitignore"
        agents.write_text("custom agents\n", encoding="utf-8")
        gitignore.write_text("custom ignore\n", encoding="utf-8")

        code, output = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertIn("統合案", output)
        self.assertEqual(agents.read_text(encoding="utf-8"), "custom agents\n")
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "custom ignore\n")
        self.assertEqual((self.target / ".agents/templates/AGENTS.md.template").read_bytes(),
                         (aide_install.REPO_ROOT / ".agents/templates/AGENTS.md.template").read_bytes())

    def test_manifest_installs_template_but_never_root_agents_file(self):
        manifest = aide_install.load_manifest()
        self.assertIn(".agents/templates/AGENTS.md.template", manifest)
        self.assertNotIn("AGENTS.md", manifest)
        self.assertEqual(self.run_cli("--apply")[0], 0)
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_agents_template_is_a_marked_copy_of_repository_guidance(self):
        template = (aide_install.REPO_ROOT / ".agents/templates/AGENTS.md.template").read_bytes()
        begin = b"<!-- BEGIN AIDE-MANAGED GUIDANCE -->\n"
        end = b"<!-- END AIDE-MANAGED GUIDANCE -->\n"
        self.assertTrue(template.startswith(begin))
        self.assertTrue(template.endswith(end))
        self.assertEqual(template.count(begin), 1)
        self.assertEqual(template.count(end), 1)
        self.assertEqual(
            template[len(begin) : -len(end)].rstrip(b"\n"),
            (aide_install.REPO_ROOT / "AGENTS.md").read_bytes().rstrip(b"\n"),
        )

    def test_copy_failure_rolls_back_created_files_and_directories(self):
        real_open = aide_install.os.open
        calls = 0

        def fail_on_second_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated write failure")
            return real_open(*args, **kwargs)

        with mock.patch.object(aide_install.os, "open", side_effect=fail_on_second_open):
            code, output = self.run_cli("--apply")
        self.assertEqual(code, 2)
        self.assertIn("ロールバック", output)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_manifest_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            manifest.write_text('{"files": ["../outside"]}', encoding="utf-8")
            with mock.patch.object(aide_install, "MANIFEST", manifest):
                with self.assertRaises(aide_install.InstallError):
                    aide_install.load_manifest()

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for CLI shim test")
    def test_node_cli_shim_forwards_arguments_to_python_installer(self):
        shim = aide_install.REPO_ROOT / "bin" / "aide-install.js"
        result = subprocess.run(
            ["node", str(shim), str(self.target), "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertEqual(list(self.target.iterdir()), [])

    @unittest.skipUnless(shutil.which("npm"), "npm is required for package distribution test")
    def test_npm_package_contains_all_manifest_sources(self):
        import json
        import os

        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, npm_config_cache=temp)
            result = subprocess.run(
                ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"],
                cwd=aide_install.REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = json.loads(result.stdout)
        self.assertEqual(len(packages), 1)
        packaged_paths = {entry["path"] for entry in packages[0]["files"]}
        self.assertNotIn("AGENTS.md", packaged_paths)
        for relative in aide_install.load_manifest():
            source = aide_install.source_path(relative).relative_to(aide_install.REPO_ROOT)
            if relative == ".gitignore":
                source = aide_install.SOURCE_FALLBACKS[relative]
            self.assertIn(source.as_posix(), packaged_paths)

    def test_symlink_destination_is_rejected_before_writing(self):
        outside = self.target.parent / f"{self.target.name}-outside"
        outside.mkdir()
        try:
            (self.target / ".agents").symlink_to(outside, target_is_directory=True)
            code, output = self.run_cli("--apply")
            self.assertEqual(code, 2)
            self.assertIn("symlink", output)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            (self.target / ".agents").unlink(missing_ok=True)
            outside.rmdir()


if __name__ == "__main__":
    unittest.main()
