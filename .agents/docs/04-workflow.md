# Issue・Project開発フロー

```text
Issue作成・Project登録（Backlog）
→ 着手許可・Ready
→ 実装（In progress）
→ PR（In review）↔ 修正（In progress）
→ マージ・Issue完了・Done
```

1. Issueを作成し、Projectへ `Backlog` 登録。詳細は[イシュー管理](02-issues.md)を参照。
2. Issue作成のみの依頼は着手許可を待つ。実装まで依頼された場合は追加確認不要。許可後、依存解決済みなら `Ready`。作業開始時に `In progress`。
3. ブランチ戦略に従って実装・検証し、設計・実装・検証の節目でIssueへ進捗を記録する。詳細は[イシュー管理](02-issues.md)を参照し、PRからIssueを参照する。基準は[完了条件・検証基準](05-quality.md)を参照。
4. PR作成後は `In review`。修正時は `In progress` に戻し、再確認時に `In review`。
5. マージと完了条件を確認し、Issueを閉じてProjectを `Done` にする。

## レビュー指摘

指摘の基準と異議への対応は[レビュールール](07-review.md)を参照。

- コード箇所はPRのインラインコメント、全体所見はレビュー総評に記録。
- マージ前の必須修正は同じPRで対応。
- 別途対応する改善はIssue化し、Projectの `Backlog` に登録してPRから参照。
- PR指摘はIssueへ重複記載しない。中断時は再開に必要な情報のみIssueへ記録。

## 中断

Issueを開いたままProjectを `In progress` にし、引き継ぎ情報をIssueコメントに残す。形式は[イシュー管理](02-issues.md)を参照。
