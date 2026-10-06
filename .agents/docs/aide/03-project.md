# GitHub Project管理

## 役割

Issueは目的と完了条件、PRは変更と検証結果、Projectは進捗の管理に使う。

## 登録

- 作成したIssueは必ずProjectへ追加し、`Backlog`に設定。
- 既存Issueの追加時も`Backlog`に設定。
- 登録後、IssueとStatusを読み戻して確認。
- 原則1リポジトリにつきProjectは1つ。

## Status

| Status | 意味 |
|---|---|
| `Backlog` | 未着手 |
| `Ready` | 着手可能・依存解決済み |
| `In progress` | 作業中 |
| `In review` | PR確認中 |
| `Done` | 完了条件を満たし、マージ済み |

依存関係はIssue間に設定し、Statusで代用しない。`Done`の前に完了条件とマージを確認する。
