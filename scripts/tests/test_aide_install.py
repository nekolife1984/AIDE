import contextlib
import errno
import hashlib
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
    start = skill.index("ルート `AGENTS.md` 全体として配布".encode("utf-8"))
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

    def test_historical_legacy_docs_report_diffs_and_unchanged_rerun_succeeds(self):
        fixture_dir = Path(__file__).parent / "fixtures/legacy_docs_71e41cc"
        fixtures = {
            ".agents/docs/01-branches.md": "94b3ef4ffaff95f4f487ec139befa12fa232183a25838653e3e9968b35077c06",
            ".agents/docs/02-issues.md": "d4455911a4ebf4b919bbeaff32f6aa74cef94e63fd5425bcbf07307f66bd0a69",
            ".agents/docs/03-project.md": "1fbaabcd226e505c378645060677063fed429549b864b30e4ba9d488abcd427d",
            ".agents/docs/05-quality.md": "241c195bd97cd5f4368a4504b597f0051ffeec470317dca3a0fb820f2e4be62a",
            ".agents/docs/07-review.md": "b7762f0e3c9ebaac36314b23d6e361985bc135e853896ed446c98aec46834978",
        }
        for old, expected_digest in fixtures.items():
            content = (fixture_dir / Path(old).name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected_digest, old)
            legacy = self.target / old
            legacy.parent.mkdir(parents=True, exist_ok=True)
            if old.endswith("05-quality.md"):
                content = content.replace(b"\n", b"\r\n")
            legacy.write_bytes(content)

        before_dry_run = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        code, output = self.run_cli("--dry-run")

        self.assertEqual(code, 0, output)
        for old in fixtures:
            new = aide_install.LEGACY_DOCS[old]
            self.assertIn(f"[移動（配布内容に差分あり: {old}）] {new}", output)
            self.assertIn(f"--- 既存 ({old})", output)
            self.assertTrue((self.target / old).is_file())
            self.assertFalse((self.target / new).exists())
        after_dry_run = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_dry_run, after_dry_run)

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        before_rerun = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        for old in fixtures:
            new = aide_install.LEGACY_DOCS[old]
            expected = (fixture_dir / Path(old).name).read_bytes()
            if old.endswith("05-quality.md"):
                expected = expected.replace(b"\n", b"\r\n")
            self.assertFalse((self.target / old).exists())
            self.assertEqual((self.target / new).read_bytes(), expected)

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        for old in fixtures:
            new = aide_install.LEGACY_DOCS[old]
            self.assertIn(f"[移行済み旧版（統合案を確認）] {new}", output)
        after_rerun = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_rerun, after_rerun)

    def test_edited_historical_migration_target_remains_a_conflict(self):
        relative = ".agents/docs/aide/01-branches.md"
        historical = (
            Path(__file__).parent
            / "fixtures/legacy_docs_71e41cc/01-branches.md"
        ).read_bytes()
        edited = historical.replace(b"main", b"MAIN", 1)
        self.assertNotEqual(edited, historical)
        target = self.target / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(edited)
        before_dry_run = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }

        code, output = self.run_cli("--dry-run")

        self.assertEqual(code, 1)
        self.assertIn(f"[競合] {relative}", output)
        after_dry_run = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_dry_run, after_dry_run)

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 1)
        self.assertIn(f"[競合] {relative}", output)
        after_apply = {
            path.relative_to(self.target): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_dry_run, after_apply)
        self.assertNotIn("移行済み旧版", output)
        self.assertEqual(target.read_bytes(), edited)
        self.assertFalse((self.target / ".agents/project.json").exists())

    def test_migrated_legacy_doc_preserves_root_relative_convention_link(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / old_relative
        legacy.parent.mkdir(parents=True)
        legacy.write_text("[Conventions](../../docs/convention.md)\n", encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / new_relative
        self.assertTrue(migrated.is_file())
        self.assertIn("[Conventions](../../../docs/convention.md)", migrated.read_text(encoding="utf-8"))
        self.assertEqual((migrated.parent / "../../../docs/convention.md").resolve(), convention.resolve())

    def test_migration_keeps_nested_quote_fence_marker_as_code_inside_list_quote_fence(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- > ```markdown\n"
            "  > > ```\n"
            "  > [Example](../../docs/missing.md)\n"
            "  > ```\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("  > > ```\n  > [Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_keeps_blank_line_inside_list_fence_as_code(self):

        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- ```markdown\n"
            "\n"
            "  [Example](../../docs/missing.md)\n"
            "  ```\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("  [Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_does_not_treat_definition_after_inline_code_paragraph_as_link(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "`inline code`\n"
            "[reference]: ../../docs/missing.md\n"
            "[reference]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8"),
            original,
        )

    def test_migration_rewrites_valid_inline_link_with_title(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = '[Conventions](../../docs/convention.md "reference")\n'
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn(
            '[Conventions](../../../docs/convention.md "reference")',
            migrated.read_text(encoding="utf-8"),
        )

    def test_migration_ignores_malformed_inline_link_without_closing_paren(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "[Page](../../docs/missing.md\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Page](../../docs/missing.md", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_html_href_rewrite_escapes_query_ampersands(self):
        page = self.target / "docs/page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = '<a href="../../docs/page.md?x=1&amp;copy;=1#section">Page</a>\n'
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn(
            'href="../../../docs/page.md?x=1&amp;copy;=1#section"', content
        )
        self.assertNotIn('href="../../../docs/page.md?x=1&copy;=1#section"', content)

    def test_migration_markdown_query_preserves_html_entity_encoding(self):
        page = self.target / "docs/page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "[Page](../../docs/page.md?x=1&amp;copy;=1#frag&amp;copy;=2)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn(
            "[Page](../../../docs/page.md?x=1&amp;copy;=1#frag&amp;copy;=2)", content
        )
        self.assertNotIn("?x=1&copy;=1", content)

        from cmarkgfm import cmark, github_flavored_markdown_to_html
        from html.parser import HTMLParser

        class LinkParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.hrefs = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.hrefs.extend(value for name, value in attrs if name == "href")

        rendered = github_flavored_markdown_to_html(
            content, options=cmark.Options.CMARK_OPT_UNSAFE
        )
        links = LinkParser()
        links.feed(rendered)
        self.assertIn(
            "../../../docs/page.md?x=1&copy;=1#frag&copy;=2", links.hrefs
        )

    def test_migration_closes_list_quote_fence_after_tab_separator(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- > ```markdown\n"
            "  >\t[Example](../../docs/missing.md)\n"
            "  >\t```\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_preserves_tab_separated_quote_marker_inside_nested_fence(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- > > ````markdown\n"
            "  >\t> ```\n"
            "  > > [Example](../../docs/missing.md)\n"
            "  >\t> ````\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("  > > [Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_markdown_query_preserves_semicolonless_entity_like_text(self):
        page = self.target / "docs/page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "[Page](../../docs/page.md?x=1&copy)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        from cmarkgfm import cmark, github_flavored_markdown_to_html
        from html.parser import HTMLParser

        class LinkParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.hrefs = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.hrefs.extend(value for name, value in attrs if name == "href")

        def rendered_href(value: str) -> str:
            rendered = github_flavored_markdown_to_html(
                value, options=cmark.Options.CMARK_OPT_UNSAFE
            )
            parser = LinkParser()
            parser.feed(rendered)
            return parser.hrefs[0]

        self.assertEqual(rendered_href(original), "../../docs/page.md?x=1&copy")
        self.assertEqual(rendered_href(content), "../../../docs/page.md?x=1&copy")

    def test_migration_closes_two_space_top_level_fence_with_unindented_marker(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "  ```markdown\n"
            "[Example](../../docs/missing.md)\n"
            "```\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_ends_list_raw_html_block_before_outer_link(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- <div>\n"
            "  embedded content\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_ignores_escaped_html_tag_but_rewrites_real_neighbor(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "\\<a href=\"../../docs/missing.md\">literal</a>\n"
            '<a href="../../docs/convention.md">real</a>\n'
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("\\<a href=\"../../docs/missing.md\">literal</a>", content)
        self.assertIn('href="../../../docs/convention.md"', content)

    def test_migration_ignores_reference_definition_with_unescaped_inner_angle(self):
        convention = self.target / "docs/convention file.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "[bad]: <../../docs/missing<fake.md>\n"
            "\n"
            "[good]: <../../docs/convention file.md>\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[bad]: <../../docs/missing<fake.md>", content)
        self.assertIn("[good]: <../../../docs/convention%20file.md>", content)

    def test_migration_rewrites_inline_angle_destination_with_spaces(self):
        convention = self.target / "docs/convention file.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "[Conventions](<../../docs/convention file.md>)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[Conventions](<../../../docs/convention%20file.md>)", content)

    def test_migration_ignores_inline_angle_destination_with_line_ending(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "[linebreak](<../../docs/missing\npath>)\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[linebreak](<../../docs/missing\npath>)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_preserves_blockquote_fence_marker_as_code_inside_top_level_fence(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "```markdown\n"
            "> ```\n"
            "[Example](../../docs/missing.md)\n"
            "```\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("> ```\n[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_keeps_unindented_content_in_indented_top_level_fence(self):

        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "  ```markdown\n"
            "[Example](../../docs/missing.md)\n"
            "  ```\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_keeps_fence_inside_blockquote_nested_in_list(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- > ```markdown\n"
            "  > [Example](../../docs/missing.md)\n"
            "  > ```\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("  > [Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_does_not_extend_unmatched_backtick_across_block_boundary(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "prose with unmatched `\n# [Conventions](../../docs/convention.md) `\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("# [Conventions](../../../docs/convention.md) `", content)

    def test_migration_preserves_lazy_continuation_inside_nested_blockquote_code_span(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "> > unmatched `\n"
            "> [Example](../../docs/missing.md) `\n"
            "\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("> [Example](../../docs/missing.md) `", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_ignores_comment_syntax_inside_inline_textarea(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "before <textarea><!--\n"
            "[Conventions](../../docs/convention.md)\n"
            "</textarea>"
            '<a href="../../docs/convention.md">Conventions</a>\n'
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("before <textarea><!--", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)
        self.assertIn('href="../../../docs/convention.md"', content)

    def test_migration_preserves_multiline_code_span_within_paragraph(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "`example\n"
            "[Example](../../docs/missing.md)`\n"
            "\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_ignores_tag_shaped_text_inside_raw_script_content(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        script = self.target / "docs/script.js"
        script.write_text("// script resource\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            '<script src="../../docs/script.js">\n'
            "const example = '<a href=\"../../docs/missing.md\">';\n"
            "</script>\n"
            '<a href="../../docs/convention.md">Conventions</a>\n'
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn('<script src="../../../docs/script.js">', content)
        self.assertIn("const example = '<a href=\"../../docs/missing.md\">';", content)
        self.assertIn('href="../../../docs/convention.md"', content)

    def test_migration_preserves_markdown_shaped_text_inside_raw_p_block(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<p>\n"
            "[Example](../../docs/missing.md)\n"
            "</p>\n"
            "\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_does_not_treat_text_prefix_as_thematic_break(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "paragraph\n"
            "---not-a-break\n"
            "[reference]: ../../docs/missing.md\n"
            "[reference]\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("---not-a-break\n[reference]: ../../docs/missing.md", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_ignores_angle_destination_with_unescaped_less_than(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "[Malformed](<../../docs/missing<fake.md>)\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Malformed](<../../docs/missing<fake.md>)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_rewrites_link_after_fence_ended_by_blockquote_boundary(self):

        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "> ```\n"
            "> example code\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Conventions](../../../docs/convention.md)", content)
        self.assertIn("> ```\n> example code\n", content)

    def test_migration_does_not_scan_markdown_link_shaped_html_attribute(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            '<span data-note="[demo](../../docs/missing.md)">text</span>\n'
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn('<span data-note="[demo](../../docs/missing.md)">text</span>', content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_preserves_markdown_link_shaped_text_in_html_pre_block(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<pre>\n"
            "[Example](../../docs/missing.md)\n"
            "</pre>\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_rewrites_link_after_fence_ended_by_list_boundary(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- ```\n"
            "  example code\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Conventions](../../../docs/convention.md)", content)
        self.assertIn("- ```\n  example code\n", content)

    def test_migration_rewrites_reference_destination_with_escaped_gt(self):
        page = self.target / "docs/foo>bar.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = r"[page]: <../../docs/foo\>bar.md>" + "\n\n[page]\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[page]: <../../../docs/foo%3Ebar.md>", content)
        self.assertIn("[page]", content)

    def test_migration_rewrites_reference_definition_after_blockquote_comment(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "> <!-- note -->\n"
            "> [convention]: ../../docs/convention.md\n"
            "\n"
            "[convention]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("> [convention]: ../../../docs/convention.md", content)

    def test_migration_rewrites_reference_definition_after_list_comment(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- <!-- note -->\n"
            "  [convention]: ../../docs/convention.md\n"
            "\n"
            "  [convention]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("  [convention]: ../../../docs/convention.md", content)

    def test_migration_rewrites_reference_definition_with_escaped_closing_bracket_label(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        label = r"conven\]tion"
        original = f"[{label}]: ../../docs/convention.md\n\n[Conventions][{label}]\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn(f"[{label}]: ../../../docs/convention.md", content)

    def test_migration_rewrites_reference_definition_after_html_comment(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<!-- migration note -->\n"
            "[convention]: ../../docs/convention.md\n"
            "\n"
            "[convention]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        content = (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8")
        self.assertIn("[convention]: ../../../docs/convention.md", content)
        self.assertIn("[convention]", content)

    def test_migration_rewrites_reference_definition_after_heading(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "# Links\n"
            "[conventions]: ../../docs/convention.md\n\n"
            "[Conventions][conventions]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[conventions]: ../../../docs/convention.md", content)

    def test_migration_rewrites_reference_definition_after_thematic_break(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "---\n"
            "[conventions]: ../../docs/convention.md\n\n"
            "[Conventions][conventions]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn(
            "[conventions]: ../../../docs/convention.md",
            migrated.read_text(encoding="utf-8"),
        )

    def test_migration_rewrites_html_link_inside_pre_block_but_not_markdown_syntax(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<pre>\n"
            '<a href="../../docs/convention.md">Conventions</a>\n'
            "[Example](../../docs/missing.md)\n"
            "</pre>\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn('href="../../../docs/convention.md"', content)
        self.assertIn("[Example](../../docs/missing.md)", content)

    def test_migration_rewrites_reference_definition_inside_blockquote(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "> [conventions]: ../../docs/convention.md\n\n[Conventions][conventions]\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("> [conventions]: ../../../docs/convention.md", content)
        self.assertEqual((migrated.parent / "../../../docs/convention.md").resolve(), convention.resolve())

    def test_migration_rewrites_reference_definition_inside_list_item(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "- [conventions]: ../../docs/convention.md\n\n  [Conventions][conventions]\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("- [conventions]: ../../../docs/convention.md", content)
        self.assertEqual((migrated.parent / "../../../docs/convention.md").resolve(), convention.resolve())

    def test_migration_does_not_treat_inline_html_paragraph_followup_as_reference_definition(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<span>paragraph content</span>\n"
            "[conventions]: ../../docs/missing.md\n"
            "[conventions]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8"),
            original,
        )

    def test_migration_does_not_treat_paragraph_continuation_as_reference_definition(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "ordinary `inline code` paragraph\n"
            "[conventions]: ../../docs/missing.md\n"
            "[conventions]\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8"),
            original,
        )

    def test_migration_rejects_broken_link_in_list_continuation_inside_blockquote(self):

        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "> - item\n>     [Missing](../../docs/missing.md)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("リンク先が存在しません", output)
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)
        self.assertFalse((self.target / ".agents/docs/aide/01-branches.md").exists())

    def test_migration_rewrites_link_after_unclosed_comment_marker_in_multiline_code_span(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "`<!--\n"
            "code span continues`\n"
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn("[Conventions](../../../docs/convention.md)", migrated.read_text(encoding="utf-8"))

    def test_migration_rewrites_link_after_comment_marker_inside_quoted_html_attribute(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            '<span data-note="<!--">example</span>\n'
            "[Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn('<span data-note="<!--">example</span>', content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_preserves_escaped_hash_as_fragment_delimiter(self):
        target = self.target / "docs/page.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Section\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        slash = chr(92)
        legacy.write_text(
            "[Section](../../docs/page.md" + slash + "#section)\n",
            encoding="utf-8",
        )

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn("[Section](../../../docs/page.md#section)", migrated.read_text(encoding="utf-8"))

    def test_migration_preserves_escaped_question_as_query_delimiter(self):
        target = self.target / "docs/page.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Section\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        slash = chr(92)
        legacy.write_text(
            "[Section](../../docs/page.md" + slash + "?view=print#section)\n",
            encoding="utf-8",
        )

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn(
            "[Section](../../../docs/page.md?view=print#section)",
            migrated.read_text(encoding="utf-8"),
        )

    def test_migration_decodes_html_entity_in_markdown_destination_for_lookup(self):
        target = self.target / "docs/foo&bar.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Target\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("[Target](../../docs/foo&amp;bar.md)\n", encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("../../../docs/foo%26bar.md", content)
        self.assertEqual((migrated.parent / "../../../docs/foo&bar.md").resolve(), target.resolve())

    def test_migration_decodes_html_entity_in_html_href_for_lookup(self):
        target = self.target / "docs/foo&bar.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Target\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('<a href="../../docs/foo&amp;bar.md">Target</a>\n', encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn('href="../../../docs/foo%26bar.md"', content)
        self.assertEqual((migrated.parent / "../../../docs/foo&bar.md").resolve(), target.resolve())

    def test_migration_rejects_broken_link_after_comment_containing_fence(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "<!--\n"
            "```markdown\n"
            "comment example\n"
            "-->\n"
            "[Missing](../../docs/missing.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("リンク先が存在しません", output)
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.target / ".agents/docs/aide/01-branches.md").exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_migration_rejects_broken_link_after_fence_with_unclosed_comment(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "```html\n"
            "<!-- unclosed comment example\n"
            "```\n"
            "[Missing](../../docs/missing.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("リンク先が存在しません", output)
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_migration_ignores_href_text_in_other_html_attribute_value(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "<span data-note='href=\"../../docs/example.md\"'>note</span>\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8"),
            original,
        )

    def test_migration_scans_nested_list_fence_without_masking_following_links(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- item\n"
            "  - ```markdown\n"
            "    [Example](../../docs/missing.md)\n"
            "    ```\n"
            "- [Conventions](../../docs/convention.md)\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("[Example](../../docs/missing.md)", content)
        self.assertIn("[Conventions](../../../docs/convention.md)", content)

    def test_migration_scans_fence_nested_directly_in_list_item(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "- ```markdown\n"
            "  [Example](../../docs/missing.md)\n"
            "  ```\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        self.assertEqual(
            (self.target / ".agents/docs/aide/01-branches.md").read_text(encoding="utf-8"),
            original,
        )

    def test_migration_preserves_percent_encoded_path_punctuation(self):
        target = self.target / "docs/foo#bar?q.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Target\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            "[Target](../../docs/foo%23bar%3Fq.md?mode=1#section)\n",
            encoding="utf-8",
        )

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn(
            "../../../docs/foo%23bar%3Fq.md?mode=1#section",
            migrated.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            (migrated.parent / "../../../docs/foo#bar?q.md").resolve(), target.resolve()
        )

    def test_migration_accepts_canonical_target_created_by_another_planned_migration(self):
        legacy_target = self.target / ".agents/docs/02-issues.md"
        legacy_target.parent.mkdir(parents=True)
        legacy_target.write_text("# Issues\n", encoding="utf-8")
        canonical_target = ".agents/docs/aide/02-issues.md"
        old = ".agents/docs/01-branches.md"
        new = ".agents/docs/aide/01-branches.md"
        content = b"[Issues](02-issues.md)\n"

        rewritten = aide_install._preserve_migrated_links(
            self.target,
            old,
            new,
            content,
            {".agents/docs/02-issues.md": canonical_target},
            [],
            [(".agents/docs/02-issues.md", canonical_target)],
        )

        self.assertEqual(rewritten, content)
        self.assertFalse((self.target / canonical_target).exists())

    def test_migration_link_target_already_cleaned_maps_to_canonical_before_existence_check(self):
        legacy_dir = self.target / ".agents/docs"
        canonical_dir = legacy_dir / "aide"
        legacy_dir.mkdir(parents=True)
        canonical_dir.mkdir(parents=True)
        legacy = legacy_dir / "01-branches.md"
        canonical_target = canonical_dir / "02-issues.md"
        canonical_target.write_bytes(
            aide_install.source_path(".agents/docs/aide/02-issues.md").read_bytes()
        )
        legacy.write_text("[Issues](02-issues.md#起票)\n", encoding="utf-8")

        code, output = self.run_cli("--apply")

        migrated = canonical_dir / "01-branches.md"
        self.assertEqual(code, 0, output)
        self.assertIn("[Issues](02-issues.md#起票)", migrated.read_text(encoding="utf-8"))
        self.assertEqual((migrated.parent / "02-issues.md").resolve(), canonical_target.resolve())

    def test_migration_scans_nested_image_inside_link_label(self):
        convention = self.target / "docs/image.png"
        convention.parent.mkdir(parents=True)
        convention.write_bytes(b"fixture")
        page = self.target / "docs/page.md"
        page.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            "[Page with ![icon](../../docs/image.png)](../../docs/page.md)\n",
            encoding="utf-8",
        )

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("![icon](../../../docs/image.png)", content)
        self.assertEqual(
            (migrated.parent / "../../../docs/image.png").resolve(), convention.resolve()
        )

    def test_migration_decodes_backslash_escaped_destination_punctuation(self):
        target = self.target / "docs/foo(bar).md"
        target.parent.mkdir(parents=True)
        target.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("[Page](../../docs/foo" + chr(92) + "(bar" + chr(92) + ").md)\n", encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        content = migrated.read_text(encoding="utf-8")
        self.assertIn("../../../docs/foo%28bar%29.md", content)
        self.assertEqual(
            (migrated.parent / "../../../docs/foo(bar).md").resolve(), target.resolve()
        )

    def test_migration_does_not_treat_html_comments_or_prose_as_attributes(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            '<!-- href="../../docs/not-a-link.md" -->\n'
            'The text href="../../docs/not-a-link.md" is an example.\n'
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertEqual(migrated.read_text(encoding="utf-8"), original)

    def test_migration_scans_real_html_href_attribute(self):
        target = self.target / "docs/page.md"
        target.parent.mkdir(parents=True)
        target.write_text("# Page\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('<a href="../../docs/page.md">Page</a>\n', encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertIn('href="../../../docs/page.md"', migrated.read_text(encoding="utf-8"))

    def test_migration_does_not_treat_backtick_with_invalid_info_string_as_fence(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "``` example ` invalid info\n[Missing](../../docs/missing.md)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("修正が必要", output)
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.target / ".agents/docs/aide/01-branches.md").exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_migration_does_not_treat_tab_indented_backticks_as_fence(self):
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = "\t```\n# Heading\n[Missing](../../docs/missing.md)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("修正が必要", output)
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.target / ".agents/docs/aide/01-branches.md").exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_migration_link_to_cleaned_legacy_duplicate_targets_canonical_file(self):
        legacy_dir = self.target / ".agents/docs"
        canonical_dir = legacy_dir / "aide"
        legacy_dir.mkdir(parents=True)
        canonical_dir.mkdir(parents=True)
        legacy_source = legacy_dir / "01-branches.md"
        legacy_target = legacy_dir / "02-issues.md"
        canonical_target = canonical_dir / "02-issues.md"
        legacy_source.write_text("[Issues](02-issues.md#setup)\n", encoding="utf-8")
        legacy_target.write_text("# Old issues\n", encoding="utf-8")
        canonical_target.write_bytes(
            aide_install.source_path(".agents/docs/aide/02-issues.md").read_bytes()
        )

        code, output = self.run_cli("--apply")

        migrated = canonical_dir / "01-branches.md"
        self.assertEqual(code, 0, output)
        self.assertFalse(legacy_target.exists())
        self.assertTrue(canonical_target.is_file())
        self.assertIn("[Issues](02-issues.md#setup)", migrated.read_text(encoding="utf-8"))
        self.assertEqual((migrated.parent / "02-issues.md").resolve(), canonical_target.resolve())

    def test_migration_leaves_indented_code_example_unchanged(self):
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy = self.target / ".agents/docs/01-branches.md"
        legacy.parent.mkdir(parents=True)
        original = (
            "    [Indented example](../../docs/convention.md)\n"
            "> ```markdown\n"
            "> [Quoted fence example](../../docs/convention.md)\n"
            "> ```\n"
        )
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = self.target / ".agents/docs/aide/01-branches.md"
        self.assertEqual(migrated.read_text(encoding="utf-8"), original)

    def test_migration_preserves_sibling_fragments_external_links_and_code_examples(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        convention = self.target / "docs/convention.md"
        convention.parent.mkdir(parents=True)
        convention.write_text("# Conventions\n", encoding="utf-8")
        legacy_dir = self.target / ".agents/docs"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "02-issues.md").write_text("# Setup\n", encoding="utf-8")
        (legacy_dir / "01-branches.md").write_text(
            "# Heading\n"
            "[Conventions](../../docs/convention.md)\n"
            "[Sibling](02-issues.md#setup)\n"
            "[This heading](#heading)\n"
            "[External](https://example.test/path?q=1#part)\n"
            "`[Inline example](../../docs/convention.md)`\n"
            "```markdown\n"
            "[Fenced example](../../docs/convention.md)\n"
            "```\n",
            encoding="utf-8",
        )

        code, output = self.run_cli("--apply")

        self.assertEqual(code, 0, output)
        migrated = (self.target / new_relative).read_text(encoding="utf-8")
        self.assertIn("[Conventions](../../../docs/convention.md)", migrated)
        self.assertIn("[Sibling](02-issues.md#setup)", migrated)
        self.assertIn("[This heading](#heading)", migrated)
        self.assertIn("[External](https://example.test/path?q=1#part)", migrated)
        self.assertIn("`[Inline example](../../docs/convention.md)`", migrated)
        self.assertIn("[Fenced example](../../docs/convention.md)", migrated)
        self.assertEqual(migrated.count("](../../docs/convention.md)"), 2)

    def test_migration_fails_before_success_for_broken_local_link(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        legacy = self.target / old_relative
        legacy.parent.mkdir(parents=True)
        original = "[Missing](../../docs/missing.md)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("修正が必要", output)
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.target / new_relative).exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_migration_fails_before_success_for_link_outside_repository(self):
        old_relative = ".agents/docs/01-branches.md"
        new_relative = ".agents/docs/aide/01-branches.md"
        legacy = self.target / old_relative
        legacy.parent.mkdir(parents=True)
        original = "[Outside](../../../outside.md)\n"
        legacy.write_text(original, encoding="utf-8")

        code, output = self.run_cli("--apply")

        self.assertNotEqual(code, 0)
        self.assertIn("修正が必要", output)
        self.assertIn("リポジトリ外", output)
        self.assertTrue(legacy.is_file())
        self.assertFalse((self.target / new_relative).exists())
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)

    def test_rerun_moves_legacy_docs_and_removes_duplicate_old_paths(self):
        for old, new in aide_install.LEGACY_DOCS.items():
            legacy = self.target / old
            legacy.parent.mkdir(parents=True, exist_ok=True)
            content = (aide_install.REPO_ROOT / new).read_bytes()
            # 旧配置fixtureでは正本文書からスキルへの相対パスを一段浅くする。
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
        self.assertIn("両方未設定の場合だけ", skill)

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

        self.assertIn("03-project.md#読み取り専用の設定解決", agents)
        self.assertIn("主作業ツリー", agents)
        self.assertIn("リンク作業ツリーでは初期化・設定更新を行わない", agents)

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

        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", return_value="yes"):
            code, output = self.run_cli("--apply")
        self.assertEqual(code, 0)
        self.assertIn("統合案", output)
        self.assertEqual(agents.read_text(encoding="utf-8"), "custom agents\n")
        self.assertTrue(gitignore.read_text(encoding="utf-8").startswith("custom ignore\n"))
        self.assertIn("/.agents/project.local.json", gitignore.read_text(encoding="utf-8"))
        template = aide_install.REPO_ROOT / ".agents/templates/aide/AGENTS.md.template"
        self.assertEqual(
            (self.target / ".agents/templates/aide/AGENTS.md.template").read_bytes(),
            template.read_bytes(),
        )
        installed_guidance = (self.target / ".agents/templates/aide/AGENTS.md.template").read_text(
            encoding="utf-8"
        )
        self.assertIn("03-project.md#読み取り専用の設定解決", installed_guidance)

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

    def test_aide_init_recognizes_only_exact_current_agents_document(self):
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_bytes()
        versions = (
            ("現行配布版（PR #66後、fa60a6a）", "legacy_agents_current.md"),
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

        self.assertEqual(len(versions), len(known_candidates), "candidates must be distinct")

        for filename in ("legacy_agents_current.md",):
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
            ("現行配布版（PR #66後、fa60a6a）", "legacy_agents_current.md"),
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

        candidate_heading = b"#### " + expected_headings[0] + newline * 2
        moved = skill.replace(candidate_heading, b"", 1)
        position = moved.index(anchor)
        moved = moved[:position] + candidate_heading + moved[position:]
        headings, candidates = _extract_legacy_migration_candidates(moved)
        self.assertEqual(expected_headings, headings)
        self.assertNotEqual(expected_candidates, normalize_candidates(candidates))

    def test_aide_init_only_keeps_current_agents_fixture_and_approval_boundary(self):
        fixtures = Path(__file__).parent / "fixtures"
        self.assertEqual(
            sorted(path.name for path in fixtures.glob("legacy_agents_*.md")),
            ["legacy_agents_current.md"],
        )
        skill = (aide_install.REPO_ROOT / ".agents/skills/aide-init/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("一致する候補がない、複数候補に曖昧、一部編集・追記済み", skill)
        self.assertIn("既知版と推測せず停止する", skill)
        self.assertIn("一意に一致した場合も自動更新せず", skill)
        self.assertIn("承認前は `AGENTS.md` を作成・変更しない", skill)

    def test_rollback_unlink_failure_reports_incomplete_recovery_and_remaining_path(self):
        real_open = aide_install.os.open
        real_unlink = Path.unlink
        calls = 0
        remaining = self.target.resolve() / ".agents/docs/aide/00_index.md"

        def fail_on_second_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, "simulated initial write failure")
            return real_open(*args, **kwargs)

        def deny_remaining_unlink(path, *args, **kwargs):
            if path == remaining:
                raise PermissionError(errno.EACCES, "sensitive rollback detail", str(path))
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(aide_install.os, "open", side_effect=fail_on_second_open), mock.patch.object(
            Path, "unlink", autospec=True, side_effect=deny_remaining_unlink
        ):
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 2)
        self.assertTrue(remaining.is_file())
        self.assertEqual(remaining.read_bytes(), aide_install.source_path(".agents/docs/aide/00_index.md").read_bytes())
        self.assertIn("simulated initial write failure", output)
        self.assertIn("Rollback incomplete", output)
        self.assertIn("manual recovery required", output)
        self.assertIn("remove file: .agents/docs/aide/00_index.md", output)
        self.assertIn(f"errno={errno.EACCES}", output)
        self.assertIn("Remaining paths:", output)
        self.assertNotIn("sensitive rollback detail", output)
        self.assertNotIn("ロールバックしました", output)
        self.assertNotIn("導入完了:", output)

    def test_rollback_directory_failure_reports_empty_remaining_directory(self):
        real_open = aide_install.os.open
        real_rmdir = Path.rmdir
        remaining = self.target.resolve() / ".agents/docs/aide"
        calls = 0

        def fail_on_second_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, "simulated initial write failure")
            return real_open(*args, **kwargs)

        def deny_directory_rmdir(path, *args, **kwargs):
            if path == remaining:
                raise PermissionError(errno.EACCES, "sensitive directory detail", str(path))
            return real_rmdir(path, *args, **kwargs)

        with mock.patch.object(aide_install.os, "open", side_effect=fail_on_second_open), mock.patch.object(
            Path, "rmdir", autospec=True, side_effect=deny_directory_rmdir
        ):
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 2)
        self.assertTrue(remaining.is_dir())
        self.assertEqual(list(remaining.iterdir()), [])
        self.assertIn("Rollback incomplete", output)
        self.assertIn("remove directory: .agents/docs/aide", output)
        self.assertIn("Remaining paths:\n  - .agents\n  - .agents/docs\n  - .agents/docs/aide", output)
        self.assertIn("simulated initial write failure", output)
        self.assertNotIn("sensitive directory detail", output)
        self.assertNotIn("ロールバックしました", output)

    def test_rollback_continues_removing_other_files_after_unlink_failure(self):
        real_open = aide_install.os.open
        real_unlink = Path.unlink
        first = self.target.resolve() / ".agents/docs/aide/00_index.md"
        remaining = self.target.resolve() / ".agents/docs/aide/01-branches.md"
        calls = 0

        def fail_on_third_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError(errno.ENOSPC, "simulated third write failure")
            return real_open(*args, **kwargs)

        def deny_second_file_unlink(path, *args, **kwargs):
            if path == remaining:
                raise PermissionError(errno.EACCES, "sensitive rollback detail", str(path))
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(aide_install.os, "open", side_effect=fail_on_third_open), mock.patch.object(
            Path, "unlink", autospec=True, side_effect=deny_second_file_unlink
        ):
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 2)
        self.assertFalse(first.exists())
        self.assertTrue(remaining.is_file())
        self.assertIn("Rollback incomplete", output)
        self.assertIn("remove file: .agents/docs/aide/01-branches.md", output)
        remaining_report = output.split("Remaining paths:\n", 1)[1].split("Restore targets", 1)[0]
        self.assertIn(".agents/docs/aide/01-branches.md", remaining_report)
        self.assertNotIn(".agents/docs/aide/00_index.md", remaining_report)
        self.assertIn("remove directory:", output)
        self.assertNotIn("sensitive rollback detail", output)

    def test_migration_rollback_reports_restore_write_and_rename_failures(self):
        real_replace = aide_install.os.replace
        real_write_bytes = Path.write_bytes
        for failure_kind in ("write", "rename"):
            with self.subTest(failure_kind=failure_kind), tempfile.TemporaryDirectory(dir=self.target) as temp:
                root = Path(temp).resolve()
                first = root / ".agents/docs/01-branches.md"
                second = root / ".agents/docs/02-issues.md"
                destination = root / ".agents/docs/aide/01-branches.md"
                first.parent.mkdir(parents=True)
                original = b"[Guide](../../guide.md)\n"
                first.write_bytes(original)
                second.write_bytes(b"# Second document\n")
                (root / "guide.md").write_bytes(b"# Guide\n")

                def fail_replace(source, target):
                    if Path(source) == second:
                        raise OSError(errno.ENOSPC, "simulated migration failure")
                    if failure_kind == "rename" and Path(source) == destination and Path(target) == first:
                        raise PermissionError(errno.EACCES, "sensitive rename detail", str(target))
                    return real_replace(source, target)

                def fail_restore_write(path, data):
                    if failure_kind == "write" and path == destination and data == original:
                        raise PermissionError(errno.EACCES, "sensitive restore detail", str(path))
                    return real_write_bytes(path, data)

                output = io.StringIO()
                with mock.patch.object(aide_install.os, "replace", side_effect=fail_replace), mock.patch.object(
                    Path, "write_bytes", autospec=True, side_effect=fail_restore_write
                ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    code = aide_install.main([str(root), "--apply"])
                text = output.getvalue()

                self.assertEqual(code, 2)
                self.assertFalse(first.exists())
                self.assertTrue(destination.is_file())
                expected = original if failure_kind == "rename" else b"[Guide](../../../guide.md)\n"
                self.assertEqual(destination.read_bytes(), expected)
                self.assertEqual(second.read_bytes(), b"# Second document\n")
                self.assertFalse((root / ".agents/docs/aide/00_index.md").exists())
                self.assertIn("simulated migration failure", text)
                self.assertIn("Rollback incomplete", text)
                self.assertIn("restore document: .agents/docs/01-branches.md <- .agents/docs/aide/01-branches.md", text)
                self.assertIn("Restore targets requiring confirmation:\n  - .agents/docs/01-branches.md", text)
                self.assertIn("Remaining paths:\n  - .agents/docs/aide\n  - .agents/docs/aide/01-branches.md", text)
                self.assertNotIn("sensitive", text)
                self.assertNotIn("ロールバックしました", text)

    def test_migration_failure_restores_original_bytes_when_rollback_succeeds(self):
        root = self.target.resolve()
        first = root / ".agents/docs/01-branches.md"
        second = root / ".agents/docs/02-issues.md"
        first.parent.mkdir(parents=True)
        first.write_bytes(b"[Guide](../../guide.md)\n")
        second.write_bytes(b"# Second document\n")
        (root / "guide.md").write_bytes(b"# Guide\n")
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        before_dirs = {p.relative_to(root) for p in root.rglob("*") if p.is_dir()}
        real_replace = aide_install.os.replace

        def fail_second_migration(source, target):
            if Path(source) == second:
                raise OSError(errno.ENOSPC, "simulated migration failure")
            return real_replace(source, target)

        with mock.patch.object(aide_install.os, "replace", side_effect=fail_second_migration):
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 2)
        after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        after_dirs = {p.relative_to(root) for p in root.rglob("*") if p.is_dir()}
        self.assertEqual(before, after)
        self.assertEqual(before_dirs, after_dirs)
        self.assertIn("simulated migration failure", output)
        self.assertIn("ロールバックしました", output)
        self.assertNotIn("Rollback incomplete", output)

    def test_rollback_path_inspection_failure_preserves_original_error(self):
        real_open = aide_install.os.open
        real_unlink = Path.unlink
        real_lstat = Path.lstat
        remaining = self.target.resolve() / ".agents/docs/aide/00_index.md"
        calls = 0
        rollback_started = False

        def fail_on_second_open(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.ENOSPC, "simulated initial write failure")
            return real_open(*args, **kwargs)

        def deny_unlink(path, *args, **kwargs):
            nonlocal rollback_started
            if path == remaining:
                rollback_started = True
                raise PermissionError(errno.EACCES, "sensitive rollback detail", str(path))
            return real_unlink(path, *args, **kwargs)

        def deny_lstat(path, *args, **kwargs):
            if rollback_started and path == remaining:
                raise PermissionError(errno.EACCES, "sensitive inspection detail", str(path))
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(aide_install.os, "open", side_effect=fail_on_second_open), mock.patch.object(
            Path, "unlink", autospec=True, side_effect=deny_unlink
        ), mock.patch.object(Path, "lstat", autospec=True, side_effect=deny_lstat):
            code, output = self.run_cli("--apply")

        self.assertEqual(code, 2)
        self.assertTrue(remaining.is_file())
        self.assertIn("simulated initial write failure", output)
        self.assertIn("Rollback incomplete", output)
        self.assertIn(".agents/docs/aide/00_index.md (state unavailable)", output)
        self.assertNotIn("sensitive", output)

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
        self.assertNotIn(".agents/project.json", packaged_paths)
        self.assertIn("scripts/templates/project.json", packaged_paths)
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
            packages = json.loads(result.stdout)
            package = packages[0] if isinstance(packages, list) else next(iter(packages.values()))
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
