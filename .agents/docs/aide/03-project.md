# GitHub Project管理

## 役割

Issueは目的と完了条件、PRは変更と検証結果、Projectは進捗の管理に使う。

## 登録

- 作成したIssueは必ずProjectへ追加し、`Backlog`に設定。
- 既存Issueの追加時も`Backlog`に設定。
- 登録後、IssueとStatusを読み戻して確認。
- 原則1リポジトリにつきProjectは1つ。

## Project設定の管理

`.agents/project.json`はGit管理対象のテンプレート設定で、`repository`と`project`は`null`に保つ。clone固有のライブ設定はgitignore対象の`.agents/project.local.json`に保存し、ローカルファイルはGit・Issue・PR・installerへ含めない。

有効な設定は`.agents/project.local.json`があればそれを使い、なければ`.agents/project.json`のテンプレート設定を初期化の基準にする。ローカル設定が存在するのに不完全・不正・originと不一致の場合は、テンプレートへ黙ってフォールバックせず停止する。`aide-init`は確認済みのライブ設定をローカル上書きへ保存し、trackedなテンプレート設定を変更しない。

`aide-init`と両設定ファイルの更新は主作業ツリーでのみ行う。リンク作業ツリーでは`.agents/project.json`と`.agents/project.local.json`を更新せず、ローカル設定がない場合も`aide-init`を実行しない。必要な初期化・更新はリンク作業ツリーを作成する前に主作業ツリーで完了する。

## Status

| Status | 意味 |
|---|---|
| `Backlog` | 未着手 |
| `Ready` | 着手可能・依存解決済み |
| `In progress` | 作業中 |
| `In review` | PR確認中 |
| `Done` | 完了条件を満たし、マージ済み |

依存関係はIssue間に設定し、Statusで代用しない。`Done`の前に完了条件とマージを確認する。
