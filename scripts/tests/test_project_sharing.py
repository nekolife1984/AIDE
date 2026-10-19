"""Sharing skill contracts and neutral installer/package distribution fixtures.

The init workflow is executed by an AI, not a Python CLI. Text contracts do
not claim to exercise an actual agent's approval or multi-file save behavior.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from scripts import aide_install


ROOT = aide_install.REPO_ROOT
SENTINEL = "fixture-live-project-do-not-distribute"
SHARED = {
    "schema_version": 1,
    "repository": "fixture-owner/fixture-repository",
    "project_defaults": {"title": None, "visibility": "PRIVATE"},
    "project": {
        "owner": "fixture-owner", "owner_type": "User", "number": 7,
        "node_id": SENTINEL, "title": SENTINEL,
        "url": "https://github.com/users/fixture-owner/projects/7",
        "visibility": "PRIVATE", "repository": "fixture-owner/fixture-repository",
    },
}


class SharingSkillContractTests(unittest.TestCase):
    def test_explicit_choice_and_separate_publication_approval(self):
        text = (ROOT / ".agents/skills/aide-init/SKILL.md").read_text()
        section = text.split("### 共有方式の選択と保存先切替", 1)[1]
        for contract in (
            "**共有する**", "**共有しない**", "既定の提案は非共有",
            "無回答を承認に変換しない", "方式の選択だけでは書き込みの承認とせず",
            "公開範囲への個別の明示承認", "Private Repository", "Projectのアクセス権は別途必要",
            "Git履歴やfork／テンプレートにも残る", "勝手にstage・commit・pushしない",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, section)

    def test_both_migration_directions_require_rechecks_and_recovery(self):
        text = (ROOT / ".agents/skills/aide-init/SKILL.md").read_text()
        section = text.split("### 共有方式の選択と保存先切替", 1)[1]
        for contract in (
            "非共有→共有", "共有→非共有", "変更・削除対象", "個別に承認",
            "内容が承認時と同じローカル設定だけを削除", "共有先を中立値へ戻す",
            "過去のGit履歴からは削除されない", "承認時から変わっていない",
            "変更前bytesと権限", "新規作成は排他的", "他者の変更を上書きしない",
            "復旧を検証するまで通常の設定解決へ進めない", "mode `0600`",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, section)

    def test_shared_schema_and_no_silent_local_override(self):
        skill = (ROOT / ".agents/skills/aide-init/SKILL.md").read_text()
        doc = (ROOT / ".agents/docs/aide/03-project.md").read_text()
        for text in (skill, doc):
            self.assertIn("同じ値でも競合として停止", text)
            self.assertIn("フォールバックせず停止", text)
            self.assertIn("認証情報", text)
        self.assertIn("8キーだけ", skill)
        self.assertIn("bool不可", skill)
        self.assertIn("originと不一致", skill)
        self.assertIn("JSON内容が一致しなければ停止", doc)
        self.assertIn("リンク側で初期化・設定更新しない", doc)
        self.assertIn("scripts/templates/project.json", doc)


class NeutralDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.target.mkdir()
        relatives = set(aide_install.load_manifest()) | {
            "scripts/templates/project.json", "scripts/templates/gitignore",
            "scripts/aide_install_manifest.json", "scripts/aide_install.py",
            "package.json", "README.md", "bin/aide-install.js",
        }
        for relative in relatives:
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        # A normal user repo may have opted into shared settings. Neither this
        # nor its private override may become an installer/package source.
        for relative in (".agents/project.json", ".agents/project.local.json"):
            (self.source / relative).write_text(json.dumps(SHARED))

    def apply(self):
        output = io.StringIO()
        with mock.patch.object(aide_install, "REPO_ROOT", self.source), mock.patch.object(
            aide_install, "MANIFEST", self.source / "scripts/aide_install_manifest.json"
        ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = aide_install.main([str(self.target), "--apply"])
        return code, output.getvalue()

    def test_installer_uses_static_neutral_source_not_shared_clone(self):
        code, output = self.apply()
        self.assertEqual(code, 0, output)
        self.assertNotIn(SENTINEL, output)
        config = json.loads((self.target / ".agents/project.json").read_text())
        self.assertEqual(config, aide_install.NEUTRAL_PROJECT_CONFIG)
        self.assertFalse((self.target / ".agents/project.local.json").exists())

    def test_destination_shared_settings_are_preserved_without_value_output(self):
        destination = self.target / ".agents/project.json"
        destination.parent.mkdir()
        original = json.dumps(SHARED).encode()
        destination.write_bytes(original)
        code, output = self.apply()
        self.assertEqual(code, 1, output)
        self.assertNotIn(SENTINEL, output)
        self.assertEqual(destination.read_bytes(), original)
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_contaminated_static_template_fails_before_any_writes(self):
        (self.source / "scripts/templates/project.json").write_text(json.dumps(SHARED))
        code, output = self.apply()
        self.assertEqual(code, 2, output)
        self.assertNotIn(SENTINEL, output)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_duplicate_json_keys_cannot_hide_live_values_in_template_bytes(self):
        neutral = json.dumps(aide_install.NEUTRAL_PROJECT_CONFIG)
        for payload in (
            '{"repository":"' + SENTINEL + '",' + neutral[1:],
            neutral.replace('"title": null', '"title":"' + SENTINEL + '","title":null'),
        ):
            with self.subTest(payload_kind='duplicate-key'):
                (self.source / "scripts/templates/project.json").write_text(payload)
                code, output = self.apply()
                self.assertEqual(code, 2, output)
                self.assertNotIn(SENTINEL, output)
                self.assertEqual(list(self.target.iterdir()), [])

    @unittest.skipUnless(shutil.which("npm"), "npm is required")
    def test_actual_npm_archive_omits_both_live_files(self):
        packages = self.root / "packages"
        packages.mkdir()
        result = subprocess.run(
            ["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(packages)],
            cwd=self.source, env=dict(os.environ, npm_config_cache=str(self.root / "npm-cache")),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        package = values[0] if isinstance(values, list) else next(iter(values.values()))
        with tarfile.open(packages / package["filename"]) as archive:
            paths = set(archive.getnames())
            self.assertNotIn("package/.agents/project.json", paths)
            self.assertNotIn("package/.agents/project.local.json", paths)
            neutral = archive.extractfile("package/scripts/templates/project.json").read()
            self.assertEqual(json.loads(neutral), aide_install.NEUTRAL_PROJECT_CONFIG)
            for member in archive.getmembers():
                if member.isfile():
                    self.assertNotIn(SENTINEL.encode(), archive.extractfile(member).read(), member.name)
