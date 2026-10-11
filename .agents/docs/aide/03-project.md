# GitHub Project管理

## 役割

Issueは目的と完了条件、PRは変更と検証結果、Projectは進捗の管理に使う。

## 登録

- 作成したIssueは必ずProjectへ追加し、`Backlog`に設定。
- 既存Issueの追加時も`Backlog`に設定。
- 登録後、IssueとStatusを読み戻して確認。
- 原則1リポジトリにつきProjectは1つ。

## 作業ツリー間の設定管理

`.agents/project.json`はGit管理対象の共有設定であり、主作業ツリーでのみ更新する。リンク作業ツリーでは読み取り専用として扱い、`project.json`が未設定（`project`が`null`）の間は`aide-init`を実行しない。

リンク作業ツリーを作成する前に、主作業ツリーで必要な`aide-init`を完了し、設定対象のRepositoryとProjectを確認する。リンク作業ツリーで設定を変更すると主作業ツリーと内容が分岐するため、更新が必要な場合は主作業ツリーで変更し、通常のブランチとPRの手順で共有する。

## Status

| Status | 意味 |
|---|---|
| `Backlog` | 未着手 |
| `Ready` | 着手可能・依存解決済み |
| `In progress` | 作業中 |
| `In review` | PR確認中 |
| `Done` | 完了条件を満たし、マージ済み |

依存関係はIssue間に設定し、Statusで代用しない。`Done`の前に完了条件とマージを確認する。
