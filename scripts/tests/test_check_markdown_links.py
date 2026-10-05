import tempfile
import os
import subprocess
import unittest
from pathlib import Path

from scripts.check_markdown_links import heading_slugs, validate_repository, validate_text

GATE_SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "require_ci_checks.sh"


class MarkdownLinkValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "README.md"
        self.source.write_text("# Home\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_detects_missing_local_file(self):
        errors = validate_text(self.root, self.source, "[guide](docs/missing.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("docs/missing.md", errors[0])

    def test_reports_link_line_within_multiline_paragraph(self):
        markdown = "First line\n[bad](missing.md)\n"

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:2:"), errors[0])

    def test_reports_link_after_escaped_hard_break_on_actual_source_line(self):
        markdown = "First line " + chr(92) + "\n[bad](missing.md)\n"

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:2:"), errors[0])

    def test_source_line_mapping_ignores_unicode_line_separator(self):
        markdown = "a\u2028" + chr(92) + "\n[bad](missing.md)\n"

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:2:"), errors[0])

    def test_source_line_mapping_handles_crlf_hard_break(self):
        markdown = "First line " + chr(92) + "\r\n[bad](missing.md)\r\n"

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:2:"), errors[0])

    def test_reports_link_line_within_multiline_raw_html_block(self):
        markdown = "before\n<div>\n<a href=\"missing.md\">bad</a>\n</div>\n"

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("README.md:3:"), errors[0])

    def test_required_gate_fails_if_either_validation_did_not_succeed(self):
        cases = (
            ("success", "success", 0),
            ("failure", "success", 1),
            ("success", "failure", 1),
            ("success", "cancelled", 1),
        )
        for unit_result, links_result, expected in cases:
            with self.subTest(unit=unit_result, links=links_result):
                env = os.environ.copy()
                env["UNIT_TESTS_RESULT"] = unit_result
                env["MARKDOWN_LINKS_RESULT"] = links_result
                result = subprocess.run(
                    ["bash", str(GATE_SCRIPT)],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_required_check_checks_out_repository_before_running_gate(self):
        workflow = (GATE_SCRIPT.parents[1] / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        required_job = workflow.split("  required_check:\n", 1)[1]
        checkout = required_job.find("uses: actions/checkout@")
        gate = required_job.find("run: bash .github/scripts/require_ci_checks.sh")

        self.assertGreaterEqual(checkout, 0)
        self.assertGreater(gate, checkout)

    def test_detects_missing_heading_anchor(self):
        (self.root / "guide.md").write_text("# Setup\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](guide.md#missing)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("#missing", errors[0])

    def test_does_not_treat_paragraph_after_empty_heading_as_heading(self):
        (self.root / "guide.md").write_text("#\nMissing\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](guide.md#missing)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("#missing", errors[0])

    def test_detects_missing_path_with_percent_encoded_colon(self):
        errors = validate_text(self.root, self.source, "[guide](missing%3Afile.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("missing%3Afile.md", errors[0])

    def test_ignores_indented_code_block_links(self):
        errors = validate_text(self.root, self.source, "    [example](missing.md)\n")

        self.assertEqual(errors, [])

    def test_does_not_treat_hanging_paragraph_indent_as_code(self):
        errors = validate_text(
            self.root, self.source, "Paragraph continues\n    [guide](missing.md)\n"
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("missing.md", errors[0])

    def test_ignores_html_comments_and_hidden_headings(self):
        (self.root / "guide.md").write_text(
            "<!--\n# Hidden\n-->\n# Visible\n", encoding="utf-8"
        )
        text = "<!-- [example](missing.md) -->\n[hidden](guide.md#hidden)\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(len(errors), 1)
        self.assertIn("#hidden", errors[0])

    def test_accepts_setext_heading_anchor(self):
        (self.root / "guide.md").write_text(
            "Installation\n============\n", encoding="utf-8"
        )

        errors = validate_text(self.root, self.source, "[guide](guide.md#installation)\n")

        self.assertEqual(errors, [])

    def test_preserves_unicode_lowercase_when_building_heading_anchor(self):
        (self.root / "guide.md").write_text("# Straße\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](guide.md#straße)\n")

        self.assertEqual(errors, [])

    def test_heading_slug_preserves_separator_after_removed_emoji(self):
        anchors = heading_slugs("# 😄 emoji\n")

        self.assertIn("-emoji", anchors)
        self.assertNotIn("emoji", anchors)

    def test_duplicate_heading_slugs_avoid_collisions_with_existing_suffixes(self):
        anchors = heading_slugs("# Foo\n# Foo\n# Foo 1\n")

        self.assertEqual(anchors, {"foo", "foo-1", "foo-1-1"})

    def test_removes_punctuation_from_heading_anchor(self):
        (self.root / "guide.md").write_text("# Foo_Bar!\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](guide.md#foobar)\n")

        self.assertEqual(errors, [])

    def test_decodes_html_character_references_in_destinations(self):
        (self.root / "foo&bar.md").write_text("# Home\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](foo&amp;bar.md)\n")

        self.assertEqual(errors, [])

    def test_accepts_existing_file_and_heading_anchor(self):
        (self.root / "guide.md").write_text("# Setup\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[guide](guide.md#setup)\n")

        self.assertEqual(errors, [])

    def test_ignores_external_links_and_code_examples(self):
        text = (
            "[external](https://example.com/missing)\n"
            "```markdown\n"
            "[example](missing.md)\n"
            "```\n"
            "Inline example: `[example](missing.md)`\n"
        )

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_fenced_code_info_line_does_not_close_fence(self):
        text = (
            "```markdown\n"
            "```python\n"
            "[example](missing.md)\n"
            "```\n"
            "[actual](missing.md)\n"
        )

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(len(errors), 1)
        self.assertIn(":5:", errors[0])

    def test_ignores_fenced_code_nested_in_second_level_list(self):
        text = (
            "- outer\n"
            "  - inner\n"
            "    ~~~md\n"
            "    [fake](missing.md)\n"
            "    ~~~\n"
        )

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_accepts_atx_heading_inside_list_item(self):
        (self.root / "guide.md").write_text("- # Foo\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[anchor](guide.md#foo)\n")

        self.assertEqual(errors, [])

    def test_does_not_treat_lazy_blockquote_followed_by_thematic_break_as_heading(self):
        (self.root / "guide.md").write_text("> Foo\n---\n", encoding="utf-8")

        errors = validate_text(self.root, self.source, "[anchor](guide.md#foo)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("#foo", errors[0])

    def test_ignores_tilde_fence_nested_in_blockquote(self):
        text = "> ~~~markdown\n> [ghost](missing.md)\n> ~~~\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_ignores_fence_nested_in_multiple_blockquotes(self):
        text = (
            "> >   ~~~markdown\n"
            "> >   [ghost](missing.md)\n"
            "> >   ~~~\n"
            "> > [actual](missing.md)\n"
        )

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(len(errors), 1)
        self.assertIn(":4:", errors[0])
        self.assertIn("missing.md", errors[0])

    def test_validates_links_in_blockquotes_outside_code_fences(self):
        errors = validate_text(self.root, self.source, "> [guide](missing.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("missing.md", errors[0])

    def test_ignores_markdown_links_and_headings_inside_raw_html_blocks(self):
        (self.root / "guide.md").write_text(
            "<script>\n# Ghost\n</script>\n", encoding="utf-8"
        )
        text = "<div>\n[ghost](missing.md)\n</div>\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

        hidden_anchor = validate_text(
            self.root, self.source, "[ghost](guide.md#ghost)\n"
        )
        self.assertEqual(len(hidden_anchor), 1)
        self.assertIn("#ghost", hidden_anchor[0])

    def test_validates_explicit_anchor_in_raw_html(self):
        markdown = (
            '<div name="phantom"></div>\n'
            '<a name="legacy"></a>\n\n'
            '[bad](#phantom)\n'
        )

        errors = validate_text(self.root, self.source, markdown)

        self.assertEqual(len(errors), 1)
        self.assertIn("#phantom", errors[0])

        valid = validate_text(
            self.root, self.source, '<a name="legacy"></a>\n[ok](#legacy)\n'
        )
        self.assertEqual(valid, [])

    def test_reference_links_in_headings_use_rendered_link_text(self):
        (self.root / "guide.md").write_text(
            "# [GitHub][gh]\n\n[gh]: https://github.com\n", encoding="utf-8"
        )

        errors = validate_text(self.root, self.source, "[guide](guide.md#github)\n")

        self.assertEqual(errors, [])

    def test_accepts_setext_heading_inside_list_item(self):
        (self.root / "guide.md").write_text(
            "- # Foo\n- Bar\n  ---\nbaz\n", encoding="utf-8"
        )

        errors = validate_text(self.root, self.source, "[guide](guide.md#bar)\n")

        self.assertEqual(errors, [])

    def test_heading_anchor_keeps_visible_inline_code_text(self):
        (self.root / "guide.md").write_text(
            "# Install `foo`\n", encoding="utf-8"
        )

        errors = validate_text(self.root, self.source, "[guide](guide.md#install-foo)\n")

        self.assertEqual(errors, [])

    def test_html_comment_opener_inside_code_span_does_not_hide_links(self):
        errors = validate_text(self.root, self.source, "`<!--` [guide](missing.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("missing.md", errors[0])

    def test_ignores_link_syntax_with_escaped_opening_bracket(self):
        errors = validate_text(self.root, self.source, r"\[literal](missing.md)" + "\n")

        self.assertEqual(errors, [])

    def test_unescapes_backslash_escaped_slash_in_destination(self):
        directory = self.root / "dir"
        directory.mkdir()
        (directory / "file.md").write_text("# File\n", encoding="utf-8")

        errors = validate_text(
            self.root,
            self.source,
            "[guide](dir" + chr(92) + "/file.md)\n",
        )

        self.assertEqual(errors, [])

    def test_link_destination_can_contain_balanced_parentheses(self):
        (self.root / "guide (quick).md").write_text("# Setup\n", encoding="utf-8")

        errors = validate_text(
            self.root,
            self.source,
            "[guide](<guide (quick).md>)\n[guide](guide%20(quick).md)\n",
        )

        self.assertEqual(errors, [])

    def test_multiline_inline_code_is_not_parsed_as_a_link(self):
        (self.root / "actual.md").write_text("# Actual\n", encoding="utf-8")
        text = "`[example](missing.md)\ncontinued` [actual](actual.md)\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_supports_reference_links_and_duplicate_heading_anchors(self):
        (self.root / "guide.md").write_text(
            "# Setup\n\n# Setup\n", encoding="utf-8"
        )
        text = "[first setup][setup]\n[second setup](guide.md#setup-1)\n\n[setup]: guide.md#setup\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_duplicate_heading_slugs_follow_document_order(self):
        (self.root / "guide.md").write_text(
            "Setup\n-----\n\n# Setup\n", encoding="utf-8"
        )
        text = "[first](guide.md#setup)\n[second](guide.md#setup-1)\n"

        errors = validate_text(self.root, self.source, text)

        self.assertEqual(errors, [])

    def test_rejects_links_that_escape_repository_root(self):
        outside = self.root.parent / "outside.md"

        errors = validate_text(self.root, self.source, "[outside](../outside.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("outside the repository", errors[0])

    def test_rejects_symlink_that_resolves_outside_repository(self):
        with tempfile.TemporaryDirectory(dir=str(self.root.parent)) as outside_directory:
            outside = Path(outside_directory)
            (self.root / "escape").symlink_to(outside, target_is_directory=True)

            errors = validate_text(self.root, self.source, "[outside](escape/missing.md)\n")

        self.assertEqual(len(errors), 1)
        self.assertIn("outside the repository", errors[0])
    def test_rejects_discovered_markdown_symlink_outside_repository(self):
        with tempfile.TemporaryDirectory(dir=str(self.root.parent)) as outside_directory:
            outside_document = Path(outside_directory) / "outside.md"
            outside_document.write_text("[bad](missing.md)\n", encoding="utf-8")
            self.source.unlink()
            self.source.symlink_to(outside_document)

            errors, count = validate_repository(self.root)

        self.assertEqual(count, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("outside the repository", errors[0])


if __name__ == "__main__":
    unittest.main()
