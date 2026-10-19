#!/usr/bin/env python3
"""Safely install reusable AIDE guidance into an existing repository."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import os
import posixpath
import re
import stat
import subprocess
import tempfile
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "aide_install_manifest.json"
HTML_BLOCK_TAGS = frozenset(
    "address article aside base basefont blockquote body caption center col colgroup dd details dialog dir div dl dt fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header hr html iframe legend li link main menu menuitem meta nav noframes ol optgroup option p param search section summary table tbody td tfoot th thead title tr track ul".split()
)
SPECIAL_FILES = {
    ".gitignore": "既存内容を保持します。必要に応じて次を既存ルールへ統合してください:\n\n# ローカル環境設定（例示ファイルは追跡可能）\n.env\n.env.*\n!.env.example\n\n# cloneごとのGitHub Project設定\n/.agents/project.local.json\n",
}
LOCAL_PROJECT_CONFIG = ".agents/project.local.json"
PROJECT_TEMPLATE = Path("scripts") / "templates" / "project.json"
NEUTRAL_PROJECT_CONFIG = {
    "schema_version": 1,
    "repository": None,
    "project_defaults": {"title": None, "visibility": "PRIVATE"},
    "project": None,
}
SOURCE_FALLBACKS = {
    ".gitignore": Path("scripts") / "templates" / "gitignore",
}
LEGACY_DOCS = {
    f".agents/docs/{name}.md": f".agents/docs/aide/{name}.md"
    for name in (
        "00_index",
        "01-branches",
        "02-issues",
        "03-project",
        "04-workflow",
        "05-quality",
        "06-security",
        "07-review",
        "08-commit-messages",
    )
}
# 71e41cc由来の旧docsをリンク保持移行した内容のSHA-256（CRLFは正規化）。
LEGACY_DOC_MIGRATION_SHA256 = {
    ".agents/docs/aide/01-branches.md": "94b3ef4ffaff95f4f487ec139befa12fa232183a25838653e3e9968b35077c06",
    ".agents/docs/aide/02-issues.md": "d4455911a4ebf4b919bbeaff32f6aa74cef94e63fd5425bcbf07307f66bd0a69",
    ".agents/docs/aide/03-project.md": "1fbaabcd226e505c378645060677063fed429549b864b30e4ba9d488abcd427d",
    ".agents/docs/aide/05-quality.md": "241c195bd97cd5f4368a4504b597f0051ffeec470317dca3a0fb820f2e4be62a",
    ".agents/docs/aide/07-review.md": "b7762f0e3c9ebaac36314b23d6e361985bc135e853896ed446c98aec46834978",
}


class InstallError(Exception):
    """A safe installation cannot be completed."""


def source_path(relative: str) -> Path:
    if relative == ".agents/project.json":
        # Never distribute the source clone's potentially shared live settings.
        return REPO_ROOT / PROJECT_TEMPLATE
    source = REPO_ROOT / relative
    if source.is_file():
        return source
    fallback = SOURCE_FALLBACKS.get(relative)
    fallback_path = REPO_ROOT / fallback if fallback else None
    return fallback_path if fallback_path and fallback_path.is_file() else source


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def load_manifest() -> list[str]:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = value["files"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise InstallError(f"マニフェストを読み込めません: {exc}") from exc
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise InstallError("マニフェストのfilesは文字列配列である必要があります")
    if len(files) != len(set(files)):
        raise InstallError("マニフェストに重複したパスがあります")
    for item in files:
        relative = Path(item)
        if relative.is_absolute() or ".." in relative.parts:
            raise InstallError(f"不正なマニフェストパス: {item}")
        if not source_path(item).is_file():
            raise InstallError(f"コピー元が存在しません: {item}")
        if item == ".agents/project.json":
            try:
                content = source_path(item).read_text(encoding="utf-8")
                value = json.loads(content, object_pairs_hook=_unique_json_object)
            except (OSError, UnicodeError, ValueError):
                raise InstallError("配布用Projectテンプレートを読み込めません") from None
            if value != NEUTRAL_PROJECT_CONFIG or type(value.get("schema_version")) is not int:
                raise InstallError("配布用Projectテンプレートが中立値ではありません")
    return files


def safe_destination(root: Path, relative: str) -> Path:
    destination = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"symlinkを含むコピー先は扱えません: {current}")
    if not destination.resolve().is_relative_to(root):
        raise InstallError(f"コピー先が対象ディレクトリ外です: {destination}")
    return destination


def _normalize_line_endings(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n")


def _migrated_legacy_doc(old: str, content: bytes) -> bytes:
    if old == ".agents/docs/00_index.md":
        return content.replace(b"../skills/", b"../../skills/")
    return content


def _is_known_migrated_legacy_doc(relative: str, content: bytes) -> bool:
    expected = LEGACY_DOC_MIGRATION_SHA256.get(relative)
    if expected is None:
        return False
    digest = hashlib.sha256(_normalize_line_endings(content)).hexdigest()
    return digest == expected


def _markdown_container_content(
    line: str, *, parse_lists: bool = True
) -> tuple[str, int, int, int]:
    content = line.rstrip("\r\n")
    source_offset = 0
    list_indent = 0
    quote_depth = 0
    while True:
        stripped = content.lstrip(" ")
        indent = len(content) - len(stripped)
        if indent <= 3 and stripped.startswith(">"):
            consumed = indent + 1
            if consumed < len(content) and content[consumed] in (" ", "\t"):
                consumed += 1
            content = content[consumed:]
            source_offset += consumed
            quote_depth += 1
            continue
        if parse_lists:
            list_marker = re.match(r" {0,3}(?:[-+*]|[0-9]{1,9}[.)])[ \t]+", content)
            if list_marker:
                consumed = list_marker.end()
                list_indent += consumed
                content = content[consumed:]
                source_offset += consumed
                continue
        return content, list_indent, quote_depth, source_offset


def _markdown_code_mask(
    markdown: str,
    *,
    raw_html_ranges: list[tuple[int, int]] | None = None,
    paragraph_breaks: set[int] | None = None,
) -> bytearray:
    """Mask code spans, code blocks, and comments outside HTML tags."""
    mask = bytearray(len(markdown))
    fence_start = None
    fence_character = ""
    fence_length = 0
    fence_container_indent = 0
    fence_quote_depth = 0
    active_list_indent = 0
    inline_code_start = None
    inline_code_length = 0
    comment_start = None
    comment_standalone = False
    active_tag_end = 0
    raw_html_end_tag = None
    raw_html_until_blank = False
    raw_html_start = None
    raw_html_container_indent = 0
    raw_html_quote_depth = 0
    raw_inline_end_offset: int | None = None
    offset = 0

    def container_prefix(
        line: str, expected_quote_depth: int
    ) -> tuple[str, int, int]:
        content = line.rstrip("\r\n")
        source_offset = 0
        quote_depth = 0
        while quote_depth < expected_quote_depth:
            stripped = content.lstrip(" ")
            indent = len(content) - len(stripped)
            if indent > 3 or not stripped.startswith(">"):
                break
            consumed = indent + 1
            if consumed < len(content) and content[consumed] in (" ", "\t"):
                consumed += 1
            content = content[consumed:]
            source_offset += consumed
            quote_depth += 1
        return content, source_offset, quote_depth

    def indentation_width(value: str) -> int:
        width = 0
        for character in value:
            if character == " ":
                width += 1
            elif character == "\t":
                width = (width // 4 + 1) * 4
            else:
                break
        return width

    def container_baseline(
        container_offset: int, list_indent: int, quote_depth: int, active_list_indent: int
    ) -> int:
        if quote_depth or list_indent:
            return container_offset
        return active_list_indent

    def escaped(index: int) -> bool:
        backslashes = 0
        index -= 1
        while index >= 0 and markdown[index] == "\\":
            backslashes += 1
            index -= 1
        return backslashes % 2 == 1

    def html_tag_end(start: int) -> int | None:
        if start >= len(markdown) or markdown[start] != "<" or escaped(start):
            return None
        cursor = start + 1
        if cursor < len(markdown) and markdown[cursor] == "/":
            cursor += 1
        name_start = cursor
        while cursor < len(markdown) and (markdown[cursor].isalnum() or markdown[cursor] in "-:_"):
            cursor += 1
        if cursor == name_start or (
            cursor < len(markdown)
            and not markdown[cursor].isspace()
            and markdown[cursor] not in "/>"
        ):
            return None
        quote_character = ""
        while cursor < len(markdown):
            character = markdown[cursor]
            if quote_character:
                if character == quote_character:
                    quote_character = ""
            elif character in ('"', "'"):
                quote_character = character
            elif character == ">":
                return cursor + 1
            cursor += 1
        return None

    def backtick_run_end(start: int, end: int) -> int:
        cursor = start + 1
        while cursor < end and markdown[cursor] == "`":
            cursor += 1
        return cursor

    def find_closing_backtick(start: int, run_length: int, end: int) -> int | None:
        cursor = start
        while cursor < end:
            if markdown[cursor] != "`":
                cursor += 1
                continue
            run_end = backtick_run_end(cursor, end)
            if run_end - cursor == run_length:
                return run_end
            cursor = run_end
        return None

    def is_block_boundary(content: str) -> bool:
        stripped_content = content.lstrip(" \t")
        if not stripped_content:
            return True
        if re.match(r"#{1,6}(?:[ \t]|$)", stripped_content):
            return True
        if re.match(r"(?:\*[ \t]*){3,}$|(?:-[ \t]*){3,}$|(?:_[ \t]*){3,}$", stripped_content):
            return True
        if re.match(r"={1,}[ \t]*$", stripped_content):
            return True
        if re.match(r"(?:`{3,}|~{3,})", stripped_content):
            return True
        if stripped_content.startswith("<!--"):
            return True
        html_open = re.match(r"<([A-Za-z][A-Za-z0-9-]*)\b", stripped_content)
        if html_open and html_open.group(1).casefold() in HTML_BLOCK_TAGS:
            return True
        return False

    def inline_code_paragraph_end(
        line_index: int,
        line_end: int,
        content: str,
        list_indent: int,
        quote_depth: int,
        container_offset: int,
        indent: int,
    ) -> int:
        if is_block_boundary(content):
            return line_end
        active_indent = list_indent or active_list_indent
        base_column = container_offset + indent
        next_offset = line_end
        for next_line in lines[line_index + 1 :]:
            next_start = next_offset
            if not next_line.strip():
                return next_start
            next_content, next_list_indent, next_quote_depth, next_container_offset = (
                _markdown_container_content(next_line)
            )
            next_indent = indentation_width(next_content)
            relative_indent = next_indent - active_indent
            if (
                next_list_indent
                or next_quote_depth > quote_depth
                or (active_indent and next_container_offset + next_indent < base_column)
                or relative_indent >= 4
                or is_block_boundary(next_content)
            ):
                return next_start
            next_offset += len(next_line)
        return len(markdown)

    lines = markdown.splitlines(keepends=True)
    for line_index, line in enumerate(lines):
        line_start = offset
        line_end = line_start + len(line)
        parse_lists = fence_start is None
        if fence_start is not None and fence_quote_depth > 0:
            content, container_offset, quote_depth = container_prefix(line, fence_quote_depth)
            list_indent = 0
        elif fence_start is not None:
            content = line.rstrip("\r\n")
            container_offset = 0
            list_indent = 0
            quote_depth = 0
        else:
            content, list_indent, quote_depth, container_offset = _markdown_container_content(
                line, parse_lists=parse_lists
            )
        stripped = content.lstrip(" \t")
        indent = indentation_width(content)
        scan_start = line_start
        skip_block_syntax = False

        if fence_start is not None:
            current_container_column = container_offset + indent
            left_container = bool(content.strip()) and (
                quote_depth < fence_quote_depth
                or (
                    active_list_indent > 0
                    and current_container_column < fence_container_indent
                )
            )
            if left_container:
                mask[fence_start:line_start] = b"\x01" * (line_start - fence_start)
                fence_start = None
                if paragraph_breaks is not None:
                    paragraph_breaks.add(line_start)
            else:
                relative_indent = current_container_column - fence_container_indent
                if 0 <= relative_indent <= 3 and stripped.startswith(fence_character * fence_length):
                    closing_length = len(stripped) - len(stripped.lstrip(fence_character))
                    if closing_length >= fence_length and not stripped[closing_length:].strip():
                        mask[fence_start:line_end] = b"\x01" * (line_end - fence_start)
                        fence_start = None
                        if paragraph_breaks is not None:
                            paragraph_breaks.add(line_start)
                        offset = line_end
                        continue
                if fence_start is not None:
                    if paragraph_breaks is not None:
                        paragraph_breaks.add(line_start)
                    offset = line_end
                    continue

        if raw_html_end_tag is not None:
            mask[line_start:line_end] = b"\x01" * (line_end - line_start)
            if paragraph_breaks is not None:
                paragraph_breaks.add(line_start)
            closing = re.search(r"</\s*" + re.escape(raw_html_end_tag) + r"\s*>", content, re.IGNORECASE)
            if closing:
                if raw_html_ranges is not None and raw_html_start is not None:
                    raw_html_ranges.append((raw_html_start, line_end))
                raw_html_end_tag = None
                raw_html_start = None
            offset = line_end
            continue
        if raw_html_until_blank:
            current_container_column = container_offset + indent
            left_container = bool(content.strip()) and (
                quote_depth < raw_html_quote_depth
                or (
                    raw_html_container_indent > 0
                    and current_container_column < raw_html_container_indent
                )
            )
            if not content.strip() or left_container:
                if raw_html_ranges is not None and raw_html_start is not None:
                    raw_html_ranges.append((raw_html_start, line_start))
                raw_html_until_blank = False
                raw_html_start = None
                raw_html_container_indent = 0
                raw_html_quote_depth = 0
            else:
                mask[line_start:line_end] = b"\x01" * (line_end - line_start)
                if paragraph_breaks is not None:
                    paragraph_breaks.add(line_start)
                offset = line_end
                continue

        if comment_start is not None:
            closing = markdown.find("-->", line_start, line_end)
            if closing < 0:
                mask[comment_start:line_end] = b"\x01" * (line_end - comment_start)
                offset = line_end
                continue
            comment_end = closing + 3
            mask[comment_start:comment_end] = b"\x01" * (comment_end - comment_start)
            if (
                paragraph_breaks is not None
                and comment_standalone
                and not markdown[comment_end:line_end].strip()
            ):
                paragraph_breaks.add(line_end)
            comment_start = None
            comment_standalone = False
            scan_start = comment_end
            skip_block_syntax = True
        elif inline_code_length:
            skip_block_syntax = True
        elif active_tag_end > line_start:
            scan_start = min(active_tag_end, line_end)
            skip_block_syntax = True

        if not skip_block_syntax:
            if list_indent:
                active_list_indent = list_indent
            elif content.strip() and indent < active_list_indent:
                active_list_indent = 0
            container_indent = list_indent or active_list_indent
            relative_indent = indent - container_indent
            if relative_indent >= 4:
                mask[line_start:line_end] = b"\x01" * (line_end - line_start)
                if paragraph_breaks is not None:
                    paragraph_breaks.add(line_start)
                offset = line_end
                continue

            html_indent = indent if list_indent else relative_indent
            html_open = re.match(r"<([A-Za-z][A-Za-z0-9-]*)\b", stripped)
            if 0 <= html_indent <= 3 and html_open:
                tag_name = html_open.group(1).lower()
                if tag_name in {"pre", "script", "style", "textarea"}:
                    mask[line_start:line_end] = b"\x01" * (line_end - line_start)
                    if paragraph_breaks is not None:
                        paragraph_breaks.add(line_start)
                    if not re.search(
                        r"</\s*" + re.escape(tag_name) + r"\s*>",
                        content,
                        re.IGNORECASE,
                    ):
                        raw_html_end_tag = tag_name
                        raw_html_start = line_start
                    elif raw_html_ranges is not None:
                        raw_html_ranges.append((line_start, line_end))
                    offset = line_end
                    continue
                if tag_name in HTML_BLOCK_TAGS:
                    mask[line_start:line_end] = b"\x01" * (line_end - line_start)
                    if paragraph_breaks is not None:
                        paragraph_breaks.add(line_start)
                    raw_html_until_blank = True
                    raw_html_start = line_start
                    raw_html_container_indent = container_baseline(
                        container_offset, list_indent, quote_depth, active_list_indent
                    )
                    raw_html_quote_depth = quote_depth
                    offset = line_end
                    continue

            fence_indent = indent if list_indent else relative_indent
            if 0 <= fence_indent <= 3 and stripped[:1] in ("`", "~"):
                marker = stripped[:1]
                run_length = len(stripped) - len(stripped.lstrip(marker))
                info = stripped[run_length:]
                if run_length >= 3 and not (marker == "`" and "`" in info):
                    if paragraph_breaks is not None:
                        paragraph_breaks.add(line_start)
                    fence_start = line_start
                    fence_character = marker
                    fence_length = run_length
                    fence_container_indent = container_baseline(
                        container_offset, list_indent, quote_depth, active_list_indent
                    )
                    fence_quote_depth = quote_depth
                    offset = line_end
                    continue

        cursor = scan_start
        while cursor < line_end:
            if raw_inline_end_offset is not None and cursor >= raw_inline_end_offset:
                raw_inline_end_offset = None
            if inline_code_length:
                assert inline_code_start is not None
                closing = find_closing_backtick(cursor, inline_code_length, line_end)
                if closing is None:
                    mask[inline_code_start:line_end] = b"\x01" * (line_end - inline_code_start)
                    cursor = line_end
                    break
                mask[inline_code_start:closing] = b"\x01" * (closing - inline_code_start)
                inline_code_start = None
                inline_code_length = 0
                cursor = closing
                continue

            tag_start = cursor
            tag_end = html_tag_end(tag_start)
            if tag_end is not None:
                if tag_end > line_end:
                    active_tag_end = tag_end
                    cursor = line_end
                    break
                cursor = tag_end
                opening_tag = re.match(
                    r"<([A-Za-z][A-Za-z0-9:-]*)\b", markdown[tag_start:tag_end]
                )
                if opening_tag and opening_tag.group(1).casefold() in {
                    "script",
                    "style",
                    "textarea",
                    "title",
                } and markdown[tag_start + 1 : tag_start + 2] != "/":
                    tag_name = opening_tag.group(1)
                    closing = re.search(
                        r"</\s*" + re.escape(tag_name) + r"\s*>",
                        markdown[tag_end:],
                        re.IGNORECASE,
                    )
                    close_start = (
                        len(markdown)
                        if closing is None
                        else tag_end + closing.start()
                    )
                    if raw_html_ranges is not None:
                        raw_html_ranges.append((tag_end, close_start))
                    raw_inline_end_offset = close_start
                    cursor = tag_end
                    continue
                continue

            if (
                markdown.startswith("<!--", cursor)
                and not escaped(cursor)
                and not (raw_inline_end_offset is not None and cursor < raw_inline_end_offset)
            ):
                closing = markdown.find("-->", cursor + 4, line_end)
                if closing < 0:
                    comment_start = cursor
                    comment_standalone = not markdown[line_start + container_offset:cursor].strip()
                    mask[cursor:line_end] = b"\x01" * (line_end - cursor)
                    cursor = line_end
                    break
                comment_end = closing + 3
                mask[cursor:comment_end] = b"\x01" * (comment_end - cursor)
                if (
                    paragraph_breaks is not None
                    and not markdown[line_start + container_offset:cursor].strip()
                    and not markdown[comment_end:line_end].strip()
                ):
                    paragraph_breaks.add(line_end)
                cursor = comment_end
                continue

            if markdown[cursor] == "`" and not escaped(cursor):
                run_end = backtick_run_end(cursor, line_end)
                run_length = run_end - cursor
                closing = find_closing_backtick(run_end, run_length, line_end)
                if closing is not None:
                    mask[cursor:closing] = b"\x01" * (closing - cursor)
                    cursor = closing
                    continue
                paragraph_end = inline_code_paragraph_end(
                    line_index,
                    line_end,
                    content,
                    list_indent,
                    quote_depth,
                    container_offset,
                    indent,
                )
                if find_closing_backtick(run_end, run_length, paragraph_end) is None:
                    cursor = run_end
                    continue
                inline_code_start = cursor
                inline_code_length = run_length
                mask[cursor:line_end] = b"\x01" * (line_end - cursor)
                cursor = line_end
                break
            cursor += 1
        offset = line_end

    if fence_start is not None:
        mask[fence_start:] = b"\x01" * (len(markdown) - fence_start)
    if raw_html_ranges is not None and raw_html_start is not None:
        raw_html_ranges.append((raw_html_start, len(markdown)))
    return mask


def _html_link_destination_spans(
    markdown: str,
    code_mask: bytearray,
    raw_html_ranges: list[tuple[int, int]],
) -> tuple[set[tuple[int, int, bool]], list[tuple[int, int]]]:
    destinations: set[tuple[int, int, bool]] = set()
    tag_spans: list[tuple[int, int]] = []

    def inside_raw_html(index: int) -> bool:
        return any(start <= index < end for start, end in raw_html_ranges)

    def escaped(index: int) -> bool:
        backslashes = 0
        index -= 1
        while index >= 0 and markdown[index] == "\\":
            backslashes += 1
            index -= 1
        return backslashes % 2 == 1

    index = 0
    while index < len(markdown):
        if (
            (code_mask[index] and not inside_raw_html(index))
            or markdown[index] != "<"
            or escaped(index)
        ):
            index += 1
            continue
        if markdown.startswith("<!--", index):
            comment_end = markdown.find("-->", index + 4)
            index = len(markdown) if comment_end < 0 else comment_end + 3
            continue

        tag_end = index + 1
        if tag_end < len(markdown) and markdown[tag_end] == "/":
            tag_end += 1
        if tag_end >= len(markdown) or not markdown[tag_end].isalpha():
            index += 1
            continue
        quote_character = ""
        while tag_end < len(markdown):
            character = markdown[tag_end]
            if quote_character:
                if character == quote_character:
                    quote_character = ""
            elif character in ('"', "'"):
                quote_character = character
            elif character == ">":
                break
            tag_end += 1
        if tag_end >= len(markdown):
            index += 1
            continue

        tag_start = index
        tag = markdown[tag_start : tag_end + 1]
        tag_name_match = re.match(r"</?([A-Za-z][A-Za-z0-9:-]*)", tag)
        tag_name = tag_name_match.group(1).casefold() if tag_name_match else ""
        is_closing_tag = tag.startswith("</")
        tag_spans.append((tag_start, tag_end + 1))
        position = 1
        if position < len(tag) and tag[position] == "/":
            position += 1
        while position < len(tag) and (tag[position].isalnum() or tag[position] in "-:_"):
            position += 1

        while position < len(tag) - 1:
            while position < len(tag) - 1 and tag[position].isspace():
                position += 1
            if position >= len(tag) - 1 or tag[position] == "/":
                break
            attribute_start = position
            while (
                position < len(tag) - 1
                and not tag[position].isspace()
                and tag[position] not in ("=", "/", ">")
            ):
                position += 1
            if position == attribute_start:
                position += 1
                continue
            attribute_name = tag[attribute_start:position].casefold()
            while position < len(tag) - 1 and tag[position].isspace():
                position += 1
            if position >= len(tag) - 1 or tag[position] != "=":
                continue
            position += 1
            while position < len(tag) - 1 and tag[position].isspace():
                position += 1
            if position >= len(tag) - 1:
                break
            if tag[position] in ('"', "'"):
                quote_character = tag[position]
                position += 1
                value_start = position
                while position < len(tag) - 1 and tag[position] != quote_character:
                    position += 1
                value_end = position
                if position < len(tag) - 1:
                    position += 1
            else:
                value_start = position
                while position < len(tag) - 1 and not tag[position].isspace() and tag[position] != ">":
                    position += 1
                value_end = position
            if attribute_name in ("href", "src") and value_end > value_start:
                start = tag_start + value_start
                end = tag_start + value_end
                in_raw_block = any(
                    raw_start <= start and end <= raw_end
                    for raw_start, raw_end in raw_html_ranges
                )
                if in_raw_block or not any(code_mask[start:end]):
                    destinations.add((start, end, True))
        if tag_name in {"script", "style", "textarea", "title"} and not is_closing_tag:
            closing = re.search(
                r"</\s*" + re.escape(tag_name) + r"\s*>",
                markdown[tag_end + 1 :],
                re.IGNORECASE,
            )
            index = len(markdown) if closing is None else tag_end + 1 + closing.start()
        else:
            index = tag_end + 1
    return destinations, tag_spans


def _reference_definition_destination_spans(
    markdown: str, mask: bytearray, paragraph_breaks: set[int]
) -> list[tuple[int, int]]:
    def definition_destination_start(content: str) -> int | None:
        opening = re.match(r"^[ \t]{0,3}\[", content)
        if opening is None:
            return None
        label_start = opening.end()
        cursor = label_start
        while cursor < len(content):
            character = content[cursor]
            if character == "\\":
                cursor += 2
                continue
            if character == "[":
                return None
            if character == "]":
                if not content[label_start:cursor].strip() or cursor + 1 >= len(content):
                    return None
                if content[cursor + 1] != ":":
                    return None
                cursor += 2
                while cursor < len(content) and content[cursor] in " \t":
                    cursor += 1
                return cursor
            cursor += 1
        return None
    def escaped(index: int) -> bool:
        backslashes = 0
        index -= 1
        while index >= 0 and markdown[index] == "\\":
            backslashes += 1
            index -= 1
        return backslashes % 2 == 1

    def valid_angle_destination(start: int, end: int) -> bool:
        if markdown[start:start + 1] != "<" or markdown[end - 1:end] != ">":
            return True
        for index in range(start + 1, end - 1):
            if markdown[index] in "\r\n" or (markdown[index] == "<" and not escaped(index)):
                return False
        return True

    spans: list[tuple[int, int]] = []
    offset = 0
    paragraph_open = False
    active_list_indent = 0
    prior_context: tuple[int, int] | None = None

    for line in markdown.splitlines(keepends=True):
        content, list_indent, quote_depth, content_offset = _markdown_container_content(line)
        indent = len(content) - len(content.lstrip(" "))
        if list_indent:
            active_list_indent = list_indent
        elif content.strip() and indent < active_list_indent:
            active_list_indent = 0
        context = (quote_depth, active_list_indent)
        if context != prior_context or list_indent or offset in paragraph_breaks:
            paragraph_open = False
        prior_context = context

        if not content.strip():
            paragraph_open = False
            offset += len(line)
            continue

        destination_start = definition_destination_start(content)
        if destination_start is not None and not paragraph_open:
            destination_end = destination_start
            angle_destination = (
                destination_start < len(content) and content[destination_start] == "<"
            )
            valid_destination = destination_start < len(content)
            if angle_destination:
                cursor = destination_start + 1
                while cursor < len(content):
                    character = content[cursor]
                    if character in "\r\n":
                        valid_destination = False
                        break
                    if character == "\\":
                        cursor += 2
                        continue
                    if character == "<":
                        valid_destination = False
                        break
                    if character == ">":
                        cursor += 1
                        break
                    cursor += 1
                else:
                    valid_destination = False
                if valid_destination and cursor <= len(content) and content[cursor - 1] == ">":
                    destination_end = cursor
                else:
                    valid_destination = False
            else:
                while destination_end < len(content) and not content[destination_end].isspace():
                    destination_end += 1
                valid_destination = destination_end > destination_start

            start = offset + content_offset + destination_start
            end = offset + content_offset + destination_end
            if valid_destination and not any(mask[start:end]) and valid_angle_destination(start, end):
                if angle_destination:
                    start += 1
                    end -= 1
                spans.append((start, end))
            paragraph_open = False
        elif re.match(
            r"^ {0,3}(?:#{1,6}(?:[ \t]|$)|(?:\*[ \t]*){3,}$|(?:-[ \t]*){3,}$|(?:_[ \t]*){3,}$|={1,}[ \t]*$)",
            content,
        ):
            paragraph_open = False
        elif offset in paragraph_breaks:
            paragraph_open = False
        else:
            paragraph_open = True
        offset += len(line)
    return spans


def _markdown_link_destination_spans(markdown: str) -> list[tuple[int, int, bool]]:
    """Find inline, reference-definition, and raw-HTML link destinations."""
    raw_html_ranges: list[tuple[int, int]] = []
    paragraph_breaks: set[int] = set()
    code_mask = _markdown_code_mask(
        markdown,
        raw_html_ranges=raw_html_ranges,
        paragraph_breaks=paragraph_breaks,
    )
    mask = bytearray(code_mask)
    html_destinations, html_tags = _html_link_destination_spans(
        markdown, code_mask, raw_html_ranges
    )
    for start, end in html_tags:
        mask[start:end] = b"\x01" * (end - start)
    spans: set[tuple[int, int, bool]] = set(html_destinations)

    def active(index: int) -> bool:
        return 0 <= index < len(mask) and not mask[index]

    def escaped(index: int) -> bool:
        backslashes = 0
        index -= 1
        while index >= 0 and markdown[index] == "\\":
            backslashes += 1
            index -= 1
        return backslashes % 2 == 1

    def has_closing_inline_link_paren(position: int) -> bool:
        while position < len(markdown) and markdown[position].isspace():
            position += 1
        if position < len(markdown) and markdown[position] == ")":
            return True
        if position >= len(markdown) or markdown[position] not in ('"', "'", "("):
            return False
        opening = markdown[position]
        closing = ")" if opening == "(" else opening
        depth = 1 if opening == "(" else 0
        position += 1
        while position < len(markdown):
            character = markdown[position]
            if character == "\\":
                position += 2
                continue
            if opening == "(" and character == "(":
                depth += 1
            elif character == closing:
                if opening != "(" or depth == 1:
                    position += 1
                    break
                depth -= 1
            position += 1
        while position < len(markdown) and markdown[position].isspace():
            position += 1
        return position < len(markdown) and markdown[position] == ")"

    index = 0
    while index < len(markdown):
        if markdown[index] != "[" or not active(index) or escaped(index):
            index += 1
            continue
        depth = 1
        cursor = index + 1
        while cursor < len(markdown) and depth:
            if not active(cursor):
                cursor += 1
                continue
            if markdown[cursor] == "\\":
                cursor += 2
                continue
            if markdown[cursor] == "[":
                depth += 1
            elif markdown[cursor] == "]":
                depth -= 1
            cursor += 1
        if depth or cursor >= len(markdown) or markdown[cursor] != "(" or not active(cursor):
            index = cursor
            continue

        cursor += 1
        while cursor < len(markdown) and markdown[cursor].isspace():
            cursor += 1
        destination_span = None
        if cursor < len(markdown) and markdown[cursor] == "<":
            start = cursor + 1
            cursor = start
            malformed_angle_destination = False
            while cursor < len(markdown) and (markdown[cursor] != ">" or escaped(cursor)):
                if markdown[cursor] in "\r\n" or (
                    markdown[cursor] == "<" and not escaped(cursor)
                ):
                    malformed_angle_destination = True
                    break
                cursor += 1
            if cursor < len(markdown) and not malformed_angle_destination:
                destination_span = (start, cursor)
                cursor += 1
        else:
            start = cursor
            paren_depth = 0
            while cursor < len(markdown):
                if not active(cursor):
                    cursor += 1
                    continue
                character = markdown[cursor]
                if character == "\\":
                    cursor += 2
                    continue
                if character == "(":
                    paren_depth += 1
                elif character == ")":
                    if paren_depth == 0:
                        break
                    paren_depth -= 1
                elif character.isspace() and paren_depth == 0:
                    break
                cursor += 1
            if cursor > start:
                destination_span = (start, cursor)
        if destination_span is not None and has_closing_inline_link_paren(cursor):
            spans.add((*destination_span, False))
        index = index + 1

    spans.update((*span, False) for span in _reference_definition_destination_spans(markdown, code_mask, paragraph_breaks))
    return sorted(spans)


def _unescape_markdown_destination(destination: str) -> str:
    punctuation = "!\\\"#$%&'()*+,-./:;<=>?@[\\\\]^_`{|}~"
    result: list[str] = []
    index = 0
    while index < len(destination):
        if (
            ord(destination[index]) == 92
            and index + 1 < len(destination)
            and destination[index + 1] in punctuation
        ):
            result.append(destination[index + 1])
            index += 2
        else:
            result.append(destination[index])
            index += 1
    return "".join(result)


def _unescape_markdown_entities(destination: str) -> str:
    semicolonless = re.compile(
        r"&(?!(?:#(?:[0-9]+|[xX][0-9A-Fa-f]+)|[A-Za-z][A-Za-z0-9]+);)"
    )
    return html.unescape(semicolonless.sub("&amp;", destination))


def _preserve_migrated_links(
    root: Path,
    old: str,
    new: str,
    content: bytes,
    legacy_documents: dict[str, str],
    planned_additions: list[str],
    planned_migrations: list[tuple[str, str]],
) -> bytes:
    """Retarget rendered local links while preserving Markdown examples verbatim."""
    try:
        markdown = content.decode("utf-8")
    except UnicodeError as exc:
        raise InstallError(f"修正が必要です（移行元MarkdownをUTF-8として読めません）: {old}") from exc

    resolved_root = root.resolve()
    old_source = root / old
    new_source = root / new
    resolved_legacy_documents = {
        (root / source).resolve(): root / destination for source, destination in legacy_documents.items()
    }
    resolved_additions = {(root / relative).resolve() for relative in planned_additions}
    resolved_planned_migrations = {
        (root / destination).resolve() for _, destination in planned_migrations
    }
    replacements: list[tuple[int, int, str]] = []
    for start, end, is_html_attribute in _markdown_link_destination_spans(markdown):
        target = markdown[start:end]
        try:
            decoded_target = _unescape_markdown_destination(target)
            if is_html_attribute:
                decoded_target = html.unescape(decoded_target)
            else:
                decoded_target = _unescape_markdown_entities(decoded_target)
            parsed = urlsplit(decoded_target)
        except ValueError as exc:
            raise InstallError(f"修正が必要です（不正なMarkdownリンク）: {old}: {target}") from exc
        if parsed.scheme or parsed.netloc or target.startswith("//") or not parsed.path:
            continue
        if parsed.path.startswith("/"):
            continue

        original_path = unquote(parsed.path)
        original_target = (old_source.parent / original_path).resolve()
        try:
            original_target.relative_to(resolved_root)
        except ValueError as exc:
            raise InstallError(
                f"修正が必要です（リンク先がリポジトリ外です）: {old}: {target}"
            ) from exc

        relocated_target = resolved_legacy_documents.get(original_target, original_target)
        if (
            not relocated_target.exists()
            and relocated_target.resolve() not in resolved_additions
            and relocated_target.resolve() not in resolved_planned_migrations
        ):
            raise InstallError(
                f"修正が必要です（リンク先が存在しません）: {old}: {target}"
            )

        current_after_move = (new_source.parent / original_path).resolve()
        if current_after_move == relocated_target.resolve():
            continue

        relative_path = posixpath.relpath(
            relocated_target.as_posix(), new_source.parent.resolve().as_posix()
        )
        rewritten = quote(relative_path, safe="/-._~")
        query = parsed.query
        fragment = parsed.fragment
        if not is_html_attribute:
            query = query.replace("&", "&amp;")
            fragment = fragment.replace("&", "&amp;")
        replacement = urlunsplit(("", "", rewritten, query, fragment))
        if is_html_attribute:
            replacement = html.escape(replacement, quote=True)
        replacements.append((start, end, replacement))

    for start, end, replacement in reversed(replacements):
        markdown = markdown[:start] + replacement + markdown[end:]
    return markdown.encode("utf-8")


def classify(root: Path, files: list[str]) -> tuple[dict[str, str], list[str]]:
    statuses: dict[str, str] = {}
    conflicts: list[str] = []
    legacy_sources = {new: old for old, new in LEGACY_DOCS.items()}
    for relative in files:
        source = source_path(relative)
        destination = safe_destination(root, relative)
        legacy = legacy_sources.get(relative)
        legacy_path = safe_destination(root, legacy) if legacy else None
        parent = destination.parent
        while parent != root and parent != parent.parent:
            if parent.exists() and not parent.is_dir():
                statuses[relative] = f"競合（親パスがディレクトリではありません: {parent.relative_to(root)}）"
                conflicts.append(relative)
                break
            parent = parent.parent
        if relative in statuses:
            continue
        if not destination.exists() and legacy_path and legacy_path.exists():
            assert legacy is not None
            if not legacy_path.is_file():
                statuses[relative] = "競合（旧配置が通常ファイルではありません）"
                conflicts.append(relative)
            else:
                migrated = _migrated_legacy_doc(legacy, legacy_path.read_bytes())
                distributed = source.read_bytes()
                if _normalize_line_endings(migrated) == _normalize_line_endings(distributed):
                    statuses[relative] = f"移動（{legacy}）"
                else:
                    statuses[relative] = f"移動（配布内容に差分あり: {legacy}）"
            continue
        if not destination.exists():
            statuses[relative] = "追加"
            continue
        if not destination.is_file():
            statuses[relative] = "競合（通常ファイルではありません）"
            conflicts.append(relative)
            continue
        if source.read_bytes() == destination.read_bytes():
            statuses[relative] = "同一"
        elif _is_known_migrated_legacy_doc(relative, destination.read_bytes()):
            statuses[relative] = "移行済み旧版（統合案を確認）"
        elif relative in SPECIAL_FILES:
            statuses[relative] = "統合案を提示（既存ファイルは保持）"
        else:
            statuses[relative] = "競合"
            conflicts.append(relative)
    return statuses, conflicts


def project_config_is_ignored(root: Path) -> bool:
    """Ask Git, including nested ignore rules; never guess from text matches."""
    if any(name in os.environ for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")):
        raise InstallError("Git実行位置の環境変数があるためignore判定を停止しました")
    try:
        location = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=False,
        )
        if location.returncode:
            if os.path.lexists(root / ".git"):
                raise InstallError("Gitリポジトリを確認できないためignore判定を停止しました")
            # The installer also supports plain directories. Use a disposable
            # Git directory to evaluate their ignore files without modifying them.
            with tempfile.TemporaryDirectory(prefix="aide-ignore-") as git_dir:
                subprocess.run(
                    ["git", "init", "--bare", "--quiet", git_dir],
                    capture_output=True, check=True,
                )
                result = subprocess.run(
                    ["git", "--git-dir", git_dir, "--work-tree", str(root),
                     "check-ignore", "-q", "--no-index", "--", LOCAL_PROJECT_CONFIG],
                    cwd=root, capture_output=True, check=False,
                )
        elif Path(os.fsdecode(location.stdout).rstrip("\n")).resolve() != root:
            raise InstallError("既存.gitignoreの確認にはGitリポジトリのルートを指定してください")
        else:
            result = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "-q", "--no-index", "--", LOCAL_PROJECT_CONFIG],
                capture_output=True, check=False,
            )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InstallError("Gitによるignore判定に失敗しました") from exc
    if result.returncode not in (0, 1):
        raise InstallError("Gitによるignore判定に失敗しました")
    return result.returncode == 0


def plan_gitignore_append(root: Path) -> tuple[bytes, bytes] | None:
    if project_config_is_ignored(root):
        return None
    original = safe_destination(root, ".gitignore").read_bytes()
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    separator = b"" if not original or original.endswith(b"\n") else newline
    suffix = separator + b"# AIDE: clone-local GitHub Project configuration" + newline
    suffix += b"/.agents/project.local.json" + newline
    print("[追記案（適用前に確認）] .gitignore")
    # Show only new lines; existing ignore entries may contain private names.
    print("+++ .gitignore（既存内容を保持し末尾へ追記）")
    for line in suffix.decode("utf-8").splitlines():
        print("+" + line)
    return original, suffix


def install(
    root: Path,
    additions: list[str],
    migrations: list[tuple[str, str]],
    legacy_cleanup: list[str],
    gitignore_append: tuple[bytes, bytes] | None = None,
) -> None:
    created: list[Path] = []
    created_dirs: list[Path] = []
    moved: list[tuple[Path, Path, bytes]] = []
    appended = False
    try:
        migration_originals: dict[str, bytes] = {}
        migration_contents: dict[str, bytes] = {}
        for old, new in migrations:
            source = safe_destination(root, old)
            original = source.read_bytes()
            migration_originals[old] = original
            migration_contents[old] = _preserve_migrated_links(
                root, old, new, original, LEGACY_DOCS, additions, migrations
            )

        for relative in additions:
            destination = safe_destination(root, relative)
            current = root
            for part in Path(relative).parts[:-1]:
                current = current / part
                if not current.exists():
                    current.mkdir()
                    created_dirs.append(current)
                elif not current.is_dir():
                    raise InstallError(f"コピー先の親パスがディレクトリではありません: {current}")
            data = source_path(relative).read_bytes()
            # O_EXCL prevents replacing a file created after the preflight check.
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            created.append(destination)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
        for old, new in migrations:
            source = safe_destination(root, old)
            destination = safe_destination(root, new)
            current = root
            for part in Path(new).parts[:-1]:
                current = current / part
                if not current.exists():
                    current.mkdir()
                    created_dirs.append(current)
            os.replace(source, destination)
            moved.append((source, destination, migration_originals[old]))
            if migration_contents[old] != migration_originals[old]:
                destination.write_bytes(migration_contents[old])
        if gitignore_append is not None:
            original, suffix = gitignore_append
            destination = safe_destination(root, ".gitignore")
            fd = os.open(destination, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW)
            with os.fdopen(fd, "r+b") as output:
                if not stat.S_ISREG(os.fstat(output.fileno()).st_mode) or output.read() != original:
                    raise InstallError("承認後に.gitignoreが変更されたため停止しました")
                appended = True
                output.write(suffix)
                output.flush()
            if destination.read_bytes() != original + suffix or not project_config_is_ignored(root):
                raise InstallError("追記後もローカル設定が無視されません。下位のignoreルール等を確認してください")
        for relative in legacy_cleanup:
            safe_destination(root, relative).unlink(missing_ok=True)
    except (OSError, InstallError) as exc:
        rollback_failures: list[str] = []
        if appended and gitignore_append is not None:
            original, suffix = gitignore_append
            try:
                destination = safe_destination(root, ".gitignore")
                fd = os.open(destination, os.O_RDWR | os.O_NOFOLLOW)
                with os.fdopen(fd, "r+b") as output:
                    current = output.read()
                    if current != original + suffix and not (current.startswith(original) and (original + suffix).startswith(current)):
                        raise InstallError("追記後に.gitignoreが変更されています")
                    output.truncate(len(original))
            except (OSError, InstallError):
                rollback_failures.append("restore .gitignore: manual recovery required")
        restore_targets: list[Path] = []
        for source, destination, original in reversed(moved):
            try:
                destination.write_bytes(original)
                os.replace(destination, source)
            except OSError as rollback_exc:
                restore_targets.append(source)
                rollback_failures.append(
                    f"restore document: {source.relative_to(root).as_posix()} <- "
                    f"{destination.relative_to(root).as_posix()}; errno={rollback_exc.errno}"
                )
        for path in reversed(created):
            try:
                path.unlink()
            except OSError as rollback_exc:
                rollback_failures.append(
                    f"remove file: {path.relative_to(root).as_posix()}; errno={rollback_exc.errno}"
                )
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError as rollback_exc:
                rollback_failures.append(
                    f"remove directory: {path.relative_to(root).as_posix()}; errno={rollback_exc.errno}"
                )
        if rollback_failures:
            remaining_paths: list[str] = []
            candidates = {*created, *created_dirs, *(destination for _, destination, _ in moved)}
            for path in sorted(candidates):
                relative = path.relative_to(root).as_posix()
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    relative += " (state unavailable)"
                remaining_paths.append(relative)
            details = "\n".join(f"  - {failure}" for failure in rollback_failures)
            remaining = "\n".join(f"  - {path}" for path in remaining_paths) or "  - none"
            restore = "\n".join(
                f"  - {path.relative_to(root).as_posix()}" for path in restore_targets
            ) or "  - none"
            raise InstallError(
                f"Installation failed: {exc}\nRollback incomplete; manual recovery required.\n"
                f"Rollback failures:\n{details}\nRemaining paths:\n{remaining}\n"
                f"Restore targets requiring confirmation:\n{restore}"
            ) from exc
        raise InstallError(f"コピーに失敗しました。作成済みファイルをロールバックしました: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="導入先リポジトリのルート")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="計画だけを表示する（デフォルト）")
    mode.add_argument("--apply", action="store_true", help="確認済みファイルをコピーする")
    args = parser.parse_args(argv)

    try:
        target = args.target.expanduser().resolve(strict=True)
        if not target.is_dir():
            raise InstallError(f"導入先がディレクトリではありません: {target}")
        files = load_manifest()
        statuses, conflicts = classify(target, files)
        migrations = [
            (old, new)
            for old, new in LEGACY_DOCS.items()
            if statuses.get(new, "").startswith("移動（")
        ]
        migration_sources = {old for old, _ in migrations}
        legacy_cleanup = [
            old
            for old, new in LEGACY_DOCS.items()
            if old not in migration_sources and safe_destination(target, old).is_file()
            and safe_destination(target, new).is_file()
        ]
    except (OSError, InstallError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    print(f"対象: {target}")
    print("モード: 適用" if args.apply else "モード: dry-run（ファイル変更なし）")
    for relative, status in statuses.items():
        print(f"[{status}] {relative}")
        if relative in SPECIAL_FILES and status == "統合案を提示（既存ファイルは保持）":
            print(SPECIAL_FILES[relative])
        else:
            legacy_source = next(
                (old for old, new in LEGACY_DOCS.items() if new == relative), None
            )
            moving_legacy = status.startswith("移動（配布内容に差分あり:")
            moved_legacy_version = status.startswith("移行済み旧版")
            has_file_conflict = relative in conflicts and relative != ".agents/project.json"
            if moving_legacy or moved_legacy_version or has_file_conflict:
                current_path = (
                    target / legacy_source
                    if moving_legacy and legacy_source
                    else target / relative
                )
                if current_path.is_file():
                    source_text = source_path(relative).read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines(keepends=True)
                    target_text = current_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines(keepends=True)
                    print(
                        "".join(
                            difflib.unified_diff(
                                target_text,
                                source_text,
                                fromfile=f"既存 ({current_path.relative_to(target)})",
                                tofile="AIDE配布内容",
                            )
                        )
                    )

    for relative in legacy_cleanup:
        if conflicts:
            print(f"[削除候補（競合のため未適用）] {relative}")
        else:
            print(f"[旧配置の削除対象] {relative}")

    if conflicts:
        print("競合があるため、部分適用を避けて全ての書き込みを停止しました:")
        for relative in conflicts:
            print(f"  - {relative}")
        print("競合ファイルを退避・統合してから再実行してください。")
        return 1
    if not args.apply:
        if statuses.get(".gitignore") in ("同一", "統合案を提示（既存ファイルは保持）"):
            try:
                plan_gitignore_append(target)
            except (OSError, InstallError) as exc:
                print(f"エラー: {exc}", file=sys.stderr)
                return 2
        print("適用するには同じコマンドに --apply を指定してください。")
        return 0

    additions = [path for path, status in statuses.items() if status == "追加"]
    try:
        gitignore_append = None
        if ".gitignore" not in additions:
            gitignore_append = plan_gitignore_append(target)
        if gitignore_append is not None:
            if not sys.stdin.isatty():
                raise InstallError("追記には対話的な確認が必要です。TTYから再実行してください（変更なし）")
            try:
                answer = input(".gitignoreへ上記を追記しますか？ [y/N]: ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("追記を承認しなかったため、全ての書き込みを停止しました。")
                return 1
            if safe_destination(target, ".gitignore").read_bytes() != gitignore_append[0]:
                raise InstallError("承認後に.gitignoreが変更されたため停止しました")
        install(target, additions, migrations, legacy_cleanup, gitignore_append)
    except (OSError, InstallError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    print(
        f"導入完了: {len(additions)}ファイルを追加、{len(migrations)}ファイルを移動、"
        f"{len(legacy_cleanup)}ファイルの旧配置を削除しました。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
