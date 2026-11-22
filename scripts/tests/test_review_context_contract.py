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
            '前提条件の現実味', '意図的に作らないと起きないか', '強い権限や特殊な状態',
            '影響の可逆性', '履歴・バックアップ・再生成', '不可逆な影響は厳格に見る',
            '既存の緩和策', '他の層で既に止まっているか', '単体の弱さだけで実害と判断しない',
            '個人情報・認証情報・金銭に関するデータや法規制が絡む事項',
            'データの性質を必ず明示する', '個人・ローカル限定でも水準を下げない',
            '試作から本番への移行も文脈の変更として扱う',
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        self.assertEqual(text.count('- 利用文脈：想定ユーザー'), 2)

    def test_workflow_transmits_context_and_tracks_rejection(self):
        text = (ROOT / '.agents/skills/aide-workflow/SKILL.md').read_text(encoding='utf-8')
        for phrase in ('想定ユーザー・動作環境', '結果と利用文脈', '過剰な必須指摘の不採用', '文脈変更時に再評価'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
