#!/usr/bin/env python3
"""Safely install reusable AIDE guidance into an existing repository."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "aide_install_manifest.json"
SPECIAL_FILES = {
    "AGENTS.md": "既存内容を保持します。AIDEの案内を追加する場合は、次を既存ルールに統合してください:\n\n# 開発ルール\n\n[開発ルール目次](.agents/docs/00_index.md)を参照\n",
    ".gitignore": "既存内容を保持します。必要に応じて次を既存ルールへ統合してください:\n\n# ローカル環境設定（例示ファイルは追跡可能）\n.env\n.env.*\n!.env.example\n",
}
SOURCE_FALLBACKS = {
    ".gitignore": Path("scripts") / "templates" / "gitignore",
}


class InstallError(Exception):
    """A safe installation cannot be completed."""


def source_path(relative: str) -> Path:
    source = REPO_ROOT / relative
    if source.is_file():
        return source
    fallback = SOURCE_FALLBACKS.get(relative)
    fallback_path = REPO_ROOT / fallback if fallback else None
    return fallback_path if fallback_path and fallback_path.is_file() else source


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


def classify(root: Path, files: list[str]) -> tuple[dict[str, str], list[str]]:
    statuses: dict[str, str] = {}
    conflicts: list[str] = []
    for relative in files:
        source = source_path(relative)
        destination = safe_destination(root, relative)
        parent = destination.parent
        while parent != root and parent != parent.parent:
            if parent.exists() and not parent.is_dir():
                statuses[relative] = f"競合（親パスがディレクトリではありません: {parent.relative_to(root)}）"
                conflicts.append(relative)
                break
            parent = parent.parent
        if relative in statuses:
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
        elif relative in SPECIAL_FILES:
            statuses[relative] = "統合案を提示（既存ファイルは保持）"
        else:
            statuses[relative] = "競合"
            conflicts.append(relative)
    return statuses, conflicts


def install(root: Path, additions: list[str]) -> None:
    created: list[Path] = []
    created_dirs: list[Path] = []
    try:
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
    except (OSError, InstallError) as exc:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
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
    except (OSError, InstallError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2

    print(f"対象: {target}")
    print("モード: 適用" if args.apply else "モード: dry-run（ファイル変更なし）")
    for relative, status in statuses.items():
        print(f"[{status}] {relative}")
        if relative in SPECIAL_FILES and status == "統合案を提示（既存ファイルは保持）":
            print(SPECIAL_FILES[relative])
        elif relative in conflicts and (target / relative).is_file():
            source_text = source_path(relative).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            target_text = (target / relative).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            print("".join(difflib.unified_diff(target_text, source_text, fromfile="既存", tofile="AIDE提案")))

    if conflicts:
        print("競合があるため、部分適用を避けて全ての書き込みを停止しました:")
        for relative in conflicts:
            print(f"  - {relative}")
        print("競合ファイルを退避・統合してから再実行してください。")
        return 1
    if not args.apply:
        print("適用するには同じコマンドに --apply を指定してください。")
        return 0

    additions = [path for path, status in statuses.items() if status == "追加"]
    try:
        install(target, additions)
    except InstallError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    print(f"導入完了: {len(additions)}ファイルを追加しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
