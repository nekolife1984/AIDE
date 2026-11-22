import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ReviewContextContractTests(unittest.TestCase):
    def test_context_and_rejection_are_part_of_review_rules(self):
        text = (ROOT / '.agents/docs/aide/07-review.md').read_text(encoding='utf-8')
        for phrase in (
            '想定ユーザー', '動作環境', 'データの性質', '個人・ローカル限定',
            '不特定多数・外部アクセスあり', '原則として必須にしない',
            '責任者の最終判断', '文脈が変わった場合', '再評価',
            '単に対応コストが高いだけでは拒否しない', '推測で安全と決めつけない',
            '過剰と判断した理由／判断者・合意／状態：採用せず',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertEqual(text.count('- 利用文脈：想定ユーザー'), 2)

    def test_workflow_transmits_context_and_tracks_rejection(self):
        text = (ROOT / '.agents/skills/aide-workflow/SKILL.md').read_text(encoding='utf-8')
        for phrase in ('想定ユーザー・動作環境', '結果と利用文脈', '過剰な必須指摘の不採用', '文脈変更時に再評価'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
