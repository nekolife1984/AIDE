#!/usr/bin/env python3
"""Validate local links in AIDE README and development documentation."""

import argparse
import ctypes
import html.parser
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urlsplit

from cmarkgfm import github_flavored_markdown_to_html
from cmarkgfm import cmark as _cmark
from cmarkgfm.cmark import Options


HEADING_TAGS = frozenset("h1 h2 h3 h4 h5 h6".split())
VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
SOURCE_LINE_RE = re.compile(r"^(\d+):")
_NATIVE_CMARK = ctypes.CDLL(_cmark._cmark.__file__)


def _native_function(name: str, result, *arguments):
    function = getattr(_NATIVE_CMARK, name)
    function.restype = result
    function.argtypes = list(arguments)
    return function


_NODE_TYPE = _native_function("cmark_node_get_type_string", ctypes.c_char_p, ctypes.c_void_p)
_NODE_START_LINE = _native_function("cmark_node_get_start_line", ctypes.c_int, ctypes.c_void_p)
_NODE_START_COLUMN = _native_function("cmark_node_get_start_column", ctypes.c_int, ctypes.c_void_p)
_NODE_URL = _native_function("cmark_node_get_url", ctypes.c_char_p, ctypes.c_void_p)
_NODE_LITERAL = _native_function("cmark_node_get_literal", ctypes.c_char_p, ctypes.c_void_p)
_NODE_FIRST_CHILD = _native_function("cmark_node_first_child", ctypes.c_void_p, ctypes.c_void_p)
_NODE_NEXT = _native_function("cmark_node_next", ctypes.c_void_p, ctypes.c_void_p)
_NODE_FREE = _native_function("cmark_node_free", None, ctypes.c_void_p)


def _source_line(markdown: str, line: int, column: int) -> int:
    """Translate cmark's paragraph-relative inline column after a hard break."""
    separators = re.findall(r"\r\n|\r|\n", markdown)
    lines = re.split(r"\r\n|\r|\n", markdown)
    while 1 <= line <= len(separators):
        line_length = len(lines[line - 1].encode("utf-8"))
        offset = line_length + 1
        if column <= offset:
            break
        column -= offset
        line += 1
    return line


def _heading_slug(text: str) -> str:
    """Build the GitHub-style slug from rendered heading text."""
    characters = []
    for character in text.lower():
        category = unicodedata.category(character)
        if character in " -" or category[0] in "LNM":
            characters.append(character)
    return "".join(characters).replace(" ", "-")


class _RenderedGfm(html.parser.HTMLParser):
    """Collect rendered GFM links and headings without re-parsing Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, int]] = []
        self.headings: Set[str] = set()
        self.anchors: Set[str] = set()
        self._occurrences: Dict[str, int] = {}
        self._used_heading_slugs: Set[str] = set()
        self._tag_stack: List[Tuple[str, int]] = []
        self._heading_tag: Optional[str] = None
        self._heading_line = 1
        self._heading_text: List[str] = []

    @property
    def _current_line(self) -> int:
        return self._tag_stack[-1][1] if self._tag_stack else 1

    def _finish_heading(self) -> None:
        base_slug = _heading_slug("".join(self._heading_text))
        slug = base_slug
        while slug in self._used_heading_slugs:
            occurrence = self._occurrences.get(base_slug, 0) + 1
            self._occurrences[base_slug] = occurrence
            slug = "{}-{}".format(base_slug, occurrence)
        self._occurrences.setdefault(base_slug, 0)
        self._used_heading_slugs.add(slug)
        self.headings.add(slug)
        self._heading_tag = None
        self._heading_text = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        source_position = attributes.get("data-sourcepos", "") or ""
        source_match = SOURCE_LINE_RE.match(source_position)
        line = int(source_match.group(1)) if source_match else self._current_line

        anchor = attributes.get("id")
        if anchor:
            self.anchors.add(anchor)
        named_anchor = attributes.get("name") if tag == "a" else None
        if named_anchor is not None:
            self.anchors.add(named_anchor)

        if tag == "a":
            href = attributes.get("href")
            if href is not None:
                self.links.append((href, line))
        elif tag == "img":
            source = attributes.get("src")
            if source is not None:
                self.links.append((source, line))
            alt = attributes.get("alt")
            if alt and self._heading_tag is not None:
                self._heading_text.append(alt)

        if tag in HEADING_TAGS and source_position:
            self._heading_tag = tag
            self._heading_text = []

        if tag not in VOID_TAGS:
            self._tag_stack.append((tag, line))

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == self._heading_tag:
            self._finish_heading()

        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index][0] == tag:
                del self._tag_stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._heading_tag is not None:
            self._heading_text.append(data)


class _RawHtmlLinks(html.parser.HTMLParser):
    """Collect actual links and anchors in a raw HTML AST node."""

    def __init__(self, start_line: int) -> None:
        super().__init__(convert_charrefs=True)
        self.start_line = start_line
        self.links: List[Tuple[str, int]] = []
        self.anchors: Set[str] = set()

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        line = self.start_line + self.getpos()[0] - 1
        anchor = attributes.get("id")
        if anchor:
            self.anchors.add(anchor)
        named_anchor = attributes.get("name") if tag == "a" else None
        if named_anchor is not None:
            self.anchors.add(named_anchor)
        href = attributes.get("href") if tag == "a" else None
        source = attributes.get("src") if tag == "img" else None
        target = href if href is not None else source
        if target is not None:
            self.links.append((target, line))

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)


def _gfm_source_links(markdown: str) -> Tuple[List[Tuple[str, int]], Set[str]]:
    """Collect link source lines and raw HTML anchors from cmark's GFM AST."""
    _cmark.core_extensions_ensure_registered()
    parser = _cmark.parser_new(options=Options.CMARK_OPT_SOURCEPOS)
    root = None
    to_pointer = lambda node: int(_cmark._cmark.ffi.cast("uintptr_t", node))
    try:
        for name in ("table", "autolink", "tagfilter", "strikethrough", "tasklist"):
            extension = _cmark.find_syntax_extension(name)
            if extension is not None:
                _cmark.parser_attach_syntax_extension(parser, extension)
        _cmark.parser_feed(parser, markdown)
        root = _cmark.parser_finish(parser)
        if root == _cmark._cmark.ffi.NULL:
            raise ValueError("cmark returned an empty syntax tree")

        links: List[Tuple[str, int]] = []
        anchors: Set[str] = set()
        pending = [to_pointer(root)]
        while pending:
            pointer = pending.pop()
            node_type_bytes = _NODE_TYPE(pointer)
            node_type = node_type_bytes.decode("ascii") if node_type_bytes else ""
            line = _NODE_START_LINE(pointer)

            if node_type in ("link", "image"):
                target = _NODE_URL(pointer)
                if target:
                    column = _NODE_START_COLUMN(pointer)
                    source_line = _source_line(markdown, line, column) if line > 0 and column > 0 else line
                    links.append((target.decode("utf-8"), source_line))
            elif node_type in ("html_inline", "html_block"):
                literal = _NODE_LITERAL(pointer)
                if literal:
                    raw_html = _RawHtmlLinks(max(1, line))
                    raw_html.feed(literal.decode("utf-8"))
                    raw_html.close()
                    links.extend(raw_html.links)
                    anchors.update(raw_html.anchors)

            children = []
            child = _NODE_FIRST_CHILD(pointer)
            while child:
                children.append(child)
                child = _NODE_NEXT(child)
            pending.extend(reversed(children))
        return links, anchors
    finally:
        if root is not None and root != _cmark._cmark.ffi.NULL:
            _NODE_FREE(to_pointer(root))
        _cmark.parser_free(parser)


def _render_gfm(markdown: str) -> _RenderedGfm:
    """Render GFM for local parsing; the HTML is never executed or served."""
    rendered = github_flavored_markdown_to_html(
        markdown, options=Options.CMARK_OPT_SOURCEPOS | Options.CMARK_OPT_UNSAFE
    )
    parser = _RenderedGfm()
    parser.feed(rendered)
    parser.close()
    parser.links, raw_anchors = _gfm_source_links(markdown)
    parser.anchors.update(raw_anchors)
    return parser


def heading_slugs(markdown: str) -> Set[str]:
    """Return heading and explicit-anchor identifiers from a Markdown document."""
    parsed = _render_gfm(markdown)
    return parsed.headings | parsed.anchors


def _relative_to_root(path: Path, root: Path) -> Optional[Path]:
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return relative


def _check_target(
    root: Path,
    source: Path,
    target: str,
    rendered_source: _RenderedGfm,
) -> Optional[str]:
    try:
        parsed = urlsplit(target)
    except ValueError:
        return "has an invalid URL"

    if parsed.scheme or parsed.netloc or target.startswith("//"):
        return None

    target_path = unquote(parsed.path)
    if target_path.startswith("/"):
        resolved = root / target_path.lstrip("/")
    elif target_path:
        resolved = source.parent / target_path
    else:
        resolved = source

    if _relative_to_root(resolved, root) is None:
        return "resolves outside the repository"
    if not resolved.exists():
        return "does not exist"
    if not parsed.fragment or resolved.is_dir() or resolved.suffix.lower() not in (".md", ".markdown"):
        return None

    try:
        if resolved.resolve() == source.resolve():
            anchors = rendered_source.headings | rendered_source.anchors
        else:
            anchors = heading_slugs(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        return "cannot read linked Markdown file ({})".format(error)

    fragment = unquote(parsed.fragment)
    if fragment not in anchors:
        return "does not contain heading anchor {!r}".format(fragment)
    return None


def validate_text(root: Path, source: Path, markdown: str) -> List[str]:
    """Validate rendered GFM links that resolve to local repository paths."""
    try:
        rendered = _render_gfm(markdown)
    except (TypeError, ValueError, RuntimeError) as error:
        return ["{}: cannot parse Markdown ({})".format(source, error)]

    try:
        source_label = source.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        source_label = source

    errors = []
    for target, line in rendered.links:
        problem = _check_target(root, source, target, rendered)
        if problem:
            errors.append("{}:{}: link {!r} {}".format(source_label, line, target, problem))
    return errors


def markdown_files(root: Path) -> List[Path]:
    """Select the README and AIDE development-rule Markdown documents."""
    candidates = [root / "README.md", root / "AGENTS.md"]
    for directory in (root / ".agents" / "docs", root / ".agents" / "skills"):
        if directory.is_dir():
            candidates.extend(directory.rglob("*.md"))
    return sorted({path for path in candidates if path.is_file()})


def validate_repository(root: Path) -> Tuple[List[str], int]:
    errors = []
    documents = markdown_files(root)
    if not documents:
        return ["No README or development-rule Markdown documents were found."], 0

    resolved_root = root.resolve()
    for document in documents:
        try:
            resolved_document = document.resolve(strict=True)
            resolved_document.relative_to(resolved_root)
        except ValueError:
            try:
                document_label = document.relative_to(root)
            except ValueError:
                document_label = document
            errors.append("{}: document resolves outside the repository".format(document_label))
            continue
        except OSError as error:
            errors.append("{}: cannot resolve document ({})".format(document, error))
            continue

        try:
            markdown = resolved_document.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append("{}: cannot read document ({})".format(document, error))
            continue
        errors.extend(validate_text(root, document, markdown))
    return errors, len(documents)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local Markdown links in AIDE documentation.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to validate (default: the parent of this script)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    errors, count = validate_repository(root)
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        print("Found {} broken local link(s) in {} Markdown file(s).".format(len(errors), count), file=sys.stderr)
        return 1

    print("Validated {} Markdown file(s): no broken local links.".format(count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
