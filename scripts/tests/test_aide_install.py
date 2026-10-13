import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import scripts.aide_install as aide_install


def _normalize_one_terminal_newline(value: bytes) -> bytes:
    if value.endswith(b"\r\n"):
        return value[:-2]
    if value.endswith(b"\n"):
        return value[:-1]
    return value


def _extract_legacy_migration_candidates(
    skill: bytes,
) -> tuple[list[bytes], list[tuple[bytes | None, bytes, bytes, bytes]]]:
    start = skill.index("旧版がルート".encode("utf-8"))
    end = skill.index("案内削除を依頼された場合".encode("utf-8"), start)
    lines = skill[start:end].splitlines(keepends=True)
    headings = []
    code_blocks = []
    opening_fence = None
    current_heading = None
    language = b""
    body_lines = []

    for line in lines:
        line_content = line.removesuffix(b"\n").removesuffix(b"\r")
        unindented = line_content.lstrip(b" ")
        indent = len(line_content) - len(unindented)
        if opening_fence is None:
            heading_separator = unindented[4:5] if unindented.startswith(b"####") else b""
            if indent <= 3 and heading_separator in (b" ", b"\t"):
                current_heading = unindented[4:].lstrip(b" \t")
                headings.append(current_heading)
            if indent <= 3 and unindented.startswith((b"```", b"~~~")):
                marker_char = unindented[:1]
                marker_length = len(unindented) - len(unindented.lstrip(marker_char))
                if marker_length >= 3:
                    opening_fence = unindented[:marker_length]
                    language = unindented[marker_length:].strip()
                    body_lines = []
        elif (
            indent <= 3
            and unindented.startswith(opening_fence)
            and not unindented[len(opening_fence) :].strip()
        ):
            code_blocks.append(
                (current_heading, opening_fence, language, b"".join(body_lines))
            )
            opening_fence = None
            current_heading = None
        else:
            body_lines.append(line)

    if opening_fence is not None:
        raise ValueError("migration section has an unclosed code fence")
    return headings, code_blocks


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
        project_config = json.loads((self.target / ".agents/project.json").read_text(encoding="utf-8"))
        self.assertIsNone(project_config["repository"])
        self.assertIsNone(project_config["project"])
        self.assertEqual(project_config["project_defaults"], {"title": None, "visibility": "PRIVATE"})
        self.assertFalse((self.target / ".agents/project.local.json").exists())

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
        template = self.target / ".agents/templates/aide/AGENTS.md.template"
        self.assertTrue(template.is_file())
        self.assertIn(relative, template.read_text(encoding="utf-8"))
        self.assertFalse((self.target / "AGENTS.md").exists())

        from scripts.check_markdown_links import validate_repository

        errors, _ = validate_repository(self.target)
        self.assertEqual(errors, [])
        self.assertTrue((self.target / ".agents/project.json").is_file())
        self.assertFalse((self.target / ".agents/project.local.json").exists())

    def test_rerun_reports_identical_and_preserves_files(self):
        self.assertEqual(self.run_cli("--apply")[0], 0)
        before = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        code, output = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertIn("同一", output)
        after = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_rerun_moves_legacy_docs_and_removes_duplicate_old_paths(self):
        for old, new in aide_install.LEGACY_DOCS.items():
            legacy = self.target / old
            legacy.parent.mkdir(parents=True, exist_ok=True)
            content = (aide_install.REPO_ROOT / new).read_bytes()
            if old.endswith("00_index.md"):
                content = content.replace(b"../../skills/", b"../skills/")
            legacy.write_bytes(content)

        code, output = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("移動", output)
        self.assertTrue((self.target / ".agents/docs/00_index.md").is_file())

        code, _ = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertFalse((self.target / ".agents/docs/00_index.md").exists())
        self.assertEqual(
            (self.target / ".agents/docs/aide/00_index.md").read_bytes(),
            (aide_install.REPO_ROOT / ".agents/docs/aide/00_index.md").read_bytes(),
        )

        # Recreate obsolete duplicates to verify an upgrade removes old paths.
        for old in aide_install.LEGACY_DOCS:
            legacy = self.target / old
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("obsolete copy\n", encoding="utf-8")
        code, _ = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertFalse(any((self.target / old).exists() for old in aide_install.LEGACY_DOCS))

    def test_dry_run_reports_legacy_cleanup_and_apply_removes_it(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        old = self.target / old_relative
        new = self.target / new_relative
        old.parent.mkdir(parents=True)
        new.parent.mkdir(parents=True)
        old.write_text("customized legacy content\n", encoding="utf-8")
        new.write_bytes((aide_install.REPO_ROOT / new_relative).read_bytes())

        code, output = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(f"[旧配置の削除対象] {old_relative}", output)
        self.assertEqual(old.read_text(encoding="utf-8"), "customized legacy content\n")

        code, output = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertIn(f"[旧配置の削除対象] {old_relative}", output)
        self.assertFalse(old.exists())

    def test_conflict_marks_legacy_cleanup_as_not_applied(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        old = self.target / old_relative
        new = self.target / new_relative
        old.parent.mkdir(parents=True)
        new.parent.mkdir(parents=True)
        old.write_text("legacy content\n", encoding="utf-8")
        new.write_text("custom canonical content\n", encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 1)
        self.assertIn(f"[削除候補（競合のため未適用）] {old_relative}", output)
        self.assertEqual(old.read_text(encoding="utf-8"), "legacy content\n")
        self.assertEqual(new.read_text(encoding="utf-8"), "custom canonical content\n")

    def test_conflict_prevents_all_writes_and_shows_integration_proposals(self):
        (self.target / "AGENTS.md").write_text("custom agents\n", encoding="utf-8")
        (self.target / ".gitignore").write_text("custom ignore\n", encoding="utf-8")
        conflict_path = " .agents/docs/aide/00_index.md".strip()
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
            {Path("AGENTS.md"), Path(".agents/docs/aide/00_index.md")},
        )

    def test_manifest_includes_only_neutral_project_template(self):
        manifest = aide_install.load_manifest()
        self.assertIn(".agents/project.json", manifest)
        self.assertNotIn(".agents/project.local.json", manifest)
        self.assertFalse(any("token" in path.lower() or ".env" in path for path in manifest))

        config = json.loads((aide_install.REPO_ROOT / ".agents/project.json").read_text(encoding="utf-8"))
        self.assertIsNone(config["repository"])
        self.assertIsNone(config["project"])

    def test_template_clone_has_neutral_project_configuration(self):
        clone = self.target / "template-clone"
        result = subprocess.run(
            ["git", "clone", "--no-hardlinks", str(aide_install.REPO_ROOT), str(clone)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads((clone / ".agents/project.json").read_text(encoding="utf-8"))
        self.assertIsNone(config["repository"])
        self.assertIsNone(config["project"])
        self.assertFalse((clone / ".agents/project.local.json").exists())

    def test_existing_project_config_conflicts_without_overwrite_or_value_output(self):
        relative = ".agents/project.json"
        config_path = self.target / relative
        config_path.parent.mkdir(parents=True)
        sentinel = "fixture-private-project-value"
        original = json.dumps(
            {
                "schema_version": 1,
                "repository": "fixture-owner/fixture-repository",
                "project_defaults": {"title": sentinel, "visibility": "PRIVATE"},
                "project": {"number": 999, "title": sentinel},
            }
        )
        config_path.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 1)
        self.assertIn(f"[競合] {relative}", output)
        self.assertNotIn(sentinel, output)
        self.assertEqual(config_path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(config_path.parent.iterdir()), [config_path])

    def test_tracked_project_config_is_neutral_template(self):
        import json

        config = json.loads((aide_install.REPO_ROOT / ".agents/project.json").read_text(encoding="utf-8"))
        self.assertTrue(config.get("repository") is None)
        self.assertTrue(config.get("project") is None)
        self.assertEqual(config.get("project_defaults"), {"title": None, "visibility": "PRIVATE"})

    def test_local_project_config_is_ignored_and_gitignore_proposal_includes_it(self):
        relative = ".agents/project.local.json"
        ignore_text = (aide_install.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        template_text = (aide_install.REPO_ROOT / "scripts/templates/gitignore").read_text(encoding="utf-8")

        self.assertIn(relative, ignore_text)
        self.assertIn(relative, template_text)
        self.assertIn(relative, aide_install.SPECIAL_FILES[".gitignore"])
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", "--", relative],
            cwd=aide_install.REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    def test_aide_init_documents_local_project_config_precedence(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn(".agents/project.local.json", skill)
        self.assertIn("trackedな`.agents/project.json`", skill)
        self.assertIn("ローカル設定がない場合だけ", skill)

    def test_aide_init_fails_closed_and_protects_local_config(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_text(
            encoding="utf-8"
        )
        contracts = (
            "ローカル設定が存在するのに不完全・不正・origin不一致なら"
            "テンプレートへフォールバックせず停止する",
            "mode `0600`",
            "git check-ignore -q --no-index -- .agents/project.local.json",
            "ローカル設定をIssue、PR、ログ、installer/NPM配布物へ出力しない",
        )

        for contract in contracts:
            with self.subTest(contract=contract):
                self.assertIn(contract, skill)

    def test_aide_init_limits_project_url_reporting_to_user_response(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Project URLをユーザーへの直接応答でのみ報告する", skill)
        self.assertIn("Issue・PR・ログにはProject URLやローカル設定値を記録せず", skill)

    def test_root_agents_guides_local_project_config_precedence(self):
        agents = (aide_install.REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn(".agents/project.local.json` があればそれを", agents)
        self.assertIn("なければテンプレート設定 `.agents/project.json`", agents)

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
        self.assertEqual((self.target / ".agents/templates/aide/AGENTS.md.template").read_bytes(),
                         (aide_install.REPO_ROOT / ".agents/templates/aide/AGENTS.md.template").read_bytes())

    def test_manifest_installs_template_but_never_root_agents_file(self):
        manifest = aide_install.load_manifest()
        self.assertIn(".agents/templates/aide/AGENTS.md.template", manifest)
        self.assertNotIn("AGENTS.md", manifest)
        self.assertEqual(self.run_cli("--apply")[0], 0)
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_agents_template_is_a_tag_free_markdown_section(self):
        template = (aide_install.REPO_ROOT / ".agents/templates/aide/AGENTS.md.template").read_text(
            encoding="utf-8"
        )
        self.assertTrue(template.startswith("## AIDEの利用案内\n"))
        self.assertIn("### GitHub Project\n", template)
        self.assertNotIn("<!--", template)
        self.assertNotIn("-->", template)
        self.assertNotIn("# 開発ルール", template)

    def test_aide_init_describes_tag_free_hierarchy_aware_section_management(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("管理タグは `AGENTS.md` に出力しない", skill)
        self.assertIn("見出し階層", skill)
        self.assertIn("以前のコメントタグ", skill)
        self.assertIn("完全な文書", skill)
        self.assertIn("ファイル末尾にある改行コード1つ", skill)
        self.assertIn("既存の案内外の記載が保たれている", skill)

    def test_aide_init_recognizes_only_exact_frozen_legacy_agents_documents(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_bytes()
        versions = (
            ("旧配布版（#41、0757dbe）", "legacy_agents_0757dbe.md"),
            ("旧配布版（#61、127874c）", "legacy_agents_127874c.md"),
            ("後続配布版（PR #66後、fa60a6a）", "legacy_agents_current.md"),
        )

        candidate_headings, code_blocks = _extract_legacy_migration_candidates(skill)
        self.assertEqual(
            [heading.encode("utf-8") for heading, _ in versions], candidate_headings
        )
        self.assertEqual(
            len(versions), len(code_blocks), "all migration-section fences must be candidates"
        )
        self.assertEqual(
            [b"```"] * len(versions), [fence for _, fence, _, _ in code_blocks]
        )
        self.assertEqual(
            [b"markdown"] * len(versions), [language for _, _, language, _ in code_blocks]
        )

        known_candidates: set[bytes] = set()
        for (heading, filename), (documented_heading, _, _, documented) in zip(
            versions, code_blocks
        ):
            with self.subTest(version=heading):
                self.assertEqual(heading.encode("utf-8"), documented_heading)
                fixture = (Path(__file__).parent / "fixtures" / filename).read_bytes()
                normalized_documented = _normalize_one_terminal_newline(documented)
                normalized_fixture = _normalize_one_terminal_newline(fixture)
                self.assertEqual(normalized_documented, normalized_fixture)
                known_candidates.add(normalized_fixture)
                for ending in (b"", b"\n", b"\r\n"):
                    self.assertIn(_normalize_one_terminal_newline(normalized_fixture + ending), known_candidates)

        self.assertEqual(len(versions), len(known_candidates), "legacy candidates must be distinct")

        for filename in (
            "legacy_agents_0757dbe.md",
            "legacy_agents_127874c.md",
            "legacy_agents_current.md",
        ):
            fixture = (Path(__file__).parent / "fixtures" / filename).read_bytes()
            mutations = (
                fixture.replace("開発ルール".encode("utf-8"), "開発規則".encode("utf-8"), 1),
                fixture + "\n利用者の追記\n".encode("utf-8"),
                _normalize_one_terminal_newline(fixture) + b"\n\n",
                fixture.replace(b"## GitHub Project", b"### GitHub Project", 1),
                _normalize_one_terminal_newline(fixture) + b"\r",
                fixture.replace(b"\n", b"\r\n", 1),
            )
            for mutated in mutations:
                with self.subTest(fixture=filename, mutation=mutated[:32]):
                    self.assertNotIn(_normalize_one_terminal_newline(mutated), known_candidates)

    def test_aide_init_candidate_census_detects_every_migration_fence(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_bytes()
        versions = (
            ("旧配布版（#41、0757dbe）", "legacy_agents_0757dbe.md"),
            ("旧配布版（#61、127874c）", "legacy_agents_127874c.md"),
            ("後続配布版（PR #66後、fa60a6a）", "legacy_agents_current.md"),
        )
        expected_headings = [heading.encode("utf-8") for heading, _ in versions]
        expected_candidates = []
        for heading, filename in versions:
            fixture = (Path(__file__).parent / "fixtures" / filename).read_bytes()
            expected_candidates.append(
                (
                    heading.encode("utf-8"),
                    b"```",
                    b"markdown",
                    _normalize_one_terminal_newline(fixture),
                )
            )
        def normalize_candidates(candidates):
            return [
                (heading, fence, language, _normalize_one_terminal_newline(body))
                for heading, fence, language, body in candidates
            ]

        newline = bytes((10,))
        actual_headings, actual_candidates = _extract_legacy_migration_candidates(skill)
        actual_candidates = normalize_candidates(actual_candidates)
        self.assertEqual(expected_headings, actual_headings)
        self.assertEqual(expected_candidates, actual_candidates)

        anchor = "案内削除を依頼された場合".encode("utf-8")
        extra_blocks = (
            b"```text" + newline + b"extra candidate" + newline + b"```" + newline,
            b"```" + newline + b"extra candidate" + newline + b"```" + newline,
            b"~~~markdown" + newline + b"extra candidate" + newline + b"~~~" + newline,
            b"   ```markdown" + newline + b"extra candidate" + newline + b"   ```" + newline,
        )
        for addition in extra_blocks:
            with self.subTest(extra_fence=addition[:32]):
                position = skill.index(anchor)
                mutated = skill[:position] + newline + addition + skill[position:]
                headings, candidates = _extract_legacy_migration_candidates(mutated)
                self.assertEqual(expected_headings, headings)
                self.assertNotEqual(expected_candidates, normalize_candidates(candidates))

        tab_heading = newline + b"####\tUnexpected candidate" + newline
        position = skill.index(anchor)
        mutated = skill[:position] + tab_heading + skill[position:]
        headings, candidates = _extract_legacy_migration_candidates(mutated)
        self.assertEqual(expected_headings + [b"Unexpected candidate"], headings)
        self.assertEqual(expected_candidates, normalize_candidates(candidates))

        second_heading = b"#### " + expected_headings[1] + newline * 2
        moved = skill.replace(second_heading, b"", 1)
        first_marker = b"#### " + expected_headings[0] + newline * 2 + b"```markdown" + newline
        moved = moved.replace(
            first_marker,
            b"#### "
            + expected_headings[0]
            + newline * 2
            + b"#### "
            + expected_headings[1]
            + newline * 2
            + b"```markdown"
            + newline,
            1,
        )
        headings, candidates = _extract_legacy_migration_candidates(moved)
        self.assertEqual(expected_headings, headings)
        self.assertNotEqual(expected_candidates, normalize_candidates(candidates))

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
        package = packages[0] if isinstance(packages, list) else next(iter(packages.values()))
        packaged_paths = {entry["path"] for entry in package["files"]}
        self.assertNotIn("AGENTS.md", packaged_paths)
        self.assertIn(".agents/project.json", packaged_paths)
        self.assertNotIn(".agents/project.local.json", packaged_paths)
        for relative in aide_install.load_manifest():
            source = aide_install.source_path(relative).relative_to(aide_install.REPO_ROOT)
            if relative == ".gitignore":
                source = aide_install.SOURCE_FALLBACKS[relative]
            self.assertIn(source.as_posix(), packaged_paths)

    @unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "Node.js and npm are required")
    def test_published_npm_cli_bootstraps_neutral_project_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_dir = root / "packages"
            package_dir.mkdir()
            env = dict(os.environ, npm_config_cache=str(root / "npm-cache"))
            result = subprocess.run(
                ["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(package_dir)],
                cwd=aide_install.REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(result.stdout)[0]
            archive_path = package_dir / package["filename"]
            extracted = root / "extracted"
            extracted.mkdir()
            with tarfile.open(archive_path, "r:gz") as archive:
                for member in archive.getmembers():
                    member_path = PurePosixPath(member.name)
                    self.assertFalse(member_path.is_absolute())
                    self.assertNotIn("..", member_path.parts)
                    if member.isdir():
                        continue
                    self.assertTrue(member.isfile(), member.name)
                    destination = extracted.joinpath(*member_path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise AssertionError(f"Unable to read package member: {member.name}")
                    destination.write_bytes(source.read())

            target = root / "existing-repository"
            target.mkdir()
            git_result = subprocess.run(
                ["git", "init", "--initial-branch=main", str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(git_result.returncode, 0, git_result.stderr)
            cli = extracted / "package/bin/aide-install.js"
            cli_result = subprocess.run(
                ["node", str(cli), str(target), "--apply"],
                env=dict(env, PYTHON=sys.executable),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_result.returncode, 0, cli_result.stderr)
            config = json.loads((target / ".agents/project.json").read_text(encoding="utf-8"))
            self.assertIsNone(config["repository"])
            self.assertIsNone(config["project"])
            self.assertEqual(config["project_defaults"], {"title": None, "visibility": "PRIVATE"})
            self.assertFalse((target / ".agents/project.local.json").exists())
            self.assertFalse((target / "AGENTS.md").exists())

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
