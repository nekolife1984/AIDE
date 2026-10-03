---
name: aide-init
description: Use when initializing or reconciling a repository's GitHub Project using AIDE conventions.
---

# AIDE Init: GitHub Project

リポジトリ用GitHub Projectの作成・再利用・初期設定手順。

## 対象

Projectの作成、リポジトリへのリンク、AIDE標準Statusの設定。Issueの移行、自動追加Workflow、ラベル、ブランチ保護、Secret設定は対象外。

## 手順

### 1. キャッシュと対象の確認

`.agents/project.json`を読み、`schema_version`とGit remoteの`owner/repository`が一致することを確認する。認証とremoteは毎回確認する。

```sh
gh auth status
git remote get-url origin
```

- 認証には`project`権限が必要。不足時は`gh auth refresh -s project`を案内する。認証情報を尋ねたり保存したりしない。
- JSONが破損・未対応、remoteが不一致、Issueが無効、権限不足なら書き込まず停止する。
- `project`が設定済みなら、保存されたowner/numberで対象Projectを直接確認する。Project一覧は検索しない。title、visibility、node ID、Repositoryリンクがキャッシュと異なる場合は停止し、再探索・修復の承認を得る。
- `project`が`null`の場合だけ初回探索する。`gh repo view --json nameWithOwner,owner,hasIssuesEnabled`で対象を確定し、`gh project list --owner <owner> --closed --limit 1000 --format json`から候補を調べる。Projectのリンク先はGraphQLの`ProjectV2.repositories`で確認する。Organization Ownerでは`user`を`organization`に置き換える。
- 対象Repositoryにリンク済みのProjectが1つなら再利用。複数、閉鎖済み、または候補が曖昧なら停止して選択を依頼する。未リンクでRepository名と完全一致する空Projectが1つなら再利用候補。候補がなければ新規作成。

### 2. 計画と承認

書き込み前に、Owner、Repository、Project URLまたは作成名、可視性、リンク・Status変更を提示する。セットアップの明示依頼は、新しいPrivate Projectの作成・リンクと空Projectの初期設定への承認とする。Public化、既存項目の移動、既存Status変更、複数候補の選択は別途明示承認が必要。

### 3. 作成・リンク

`.agents/project.json`の`project_defaults.title`と`visibility`を使用する。未設定時はRepository名と`PRIVATE`を既定値にする。

```sh
gh project create --owner <owner> --title "<title>" --format json
gh project edit <number> --owner <owner> --description "リポジトリのIssue・PRの進捗管理" --visibility <visibility>
gh project link <number> --owner <owner> --repo <repository>
```

作成結果のProject番号を使う。途中失敗時は同じProjectを読み戻して再開し、重複作成しない。リンク済みなら再リンク不要。

### 4. Status設定

Statusの名称・順序・色:

| Status | 意味 | 色 |
|---|---|---|
| `Backlog` | 未着手 | `GRAY` |
| `Ready` | 着手可能・依存解決済み | `BLUE` |
| `In progress` | 作業中 | `YELLOW` |
| `In review` | PR確認中 | `ORANGE` |
| `Done` | 完了条件達成・マージ済み | `GREEN` |

Projectを作成またはリンクした直後、Statusを作成・変更する前に必ず既存フィールドを読み戻す。GitHubが作成時に`Status`を用意している場合があるため、確認前に`field-create`を実行しない。

```sh
gh project field-list <number> --owner <owner> --format json
```

- `Status` fieldがあり、選択肢も一致していれば変更しない。
- `Status` fieldがあれば再利用する。選択肢が異なる場合は`gh project item-list`で項目数を確認する。
- `Status` fieldがないことを確認した場合に限り作成する。

```sh
gh project field-create <number> --owner <owner> --name Status --data-type SINGLE_SELECT --single-select-options "Backlog,Ready,In progress,In review,Done" --format json
```

- `field-create`が予約名・重複などのエラーになった場合は再試行せず、`field-list`を再実行して既存フィールドを確認し、再利用または更新へ切り替える。別Projectを重複作成しない。
- 既存Statusの選択肢変更は空Projectに限り`updateProjectV2Field`を使用する。変更前にfield IDと全optionを読み、保持するoptionのIDを指定し、新規optionのIDはGitHubに発行させる。APIには望む選択肢全体を渡し、更新後に名前・順序・色・IDを読み戻す。未知のoptionがあれば停止。
- 項目があるProjectのStatus変更、option削除、項目移動は個別承認なしに行わない。

CLIやAPIの形式が不明な場合は、`gh <command> --help`とGitHub公式仕様を確認し、推測で書き込まない。

### 5. 読み戻し・キャッシュ更新

GitHubから再取得し、Owner、number、node ID、title、URL、visibility、Repositoryリンク、Statusの5選択肢と順序、意図しない項目変更がないことを確認する。成功レスポンスだけで完了扱いにしない。

初回または承認済み変更で情報が変わった場合のみ、検証後に`.agents/project.json`を更新する。途中失敗では成功状態を保存しない。可能ならatomic replaceを使う。設定済みキャッシュに変更がなければ再保存しない。

## `.agents/project.json`

Repository管理対象。非秘密の安定情報と既定値を保存し、Token、個人情報、変動するfield/option IDは保存しない。

```json
{
  "schema_version": 1,
  "repository": {"owner": "<owner>", "name": "<repository>"},
  "project_defaults": {"title": "<project-title>", "visibility": "PRIVATE"},
  "project": null
}
```

セットアップ後の`project`には`owner`、`owner_type`、`number`、`node_id`、`title`、`url`、`visibility`、`repository`（`owner/name`）を保存する。

## 注意事項

- Project StatusはIssue依存関係の代替ではない。
- Project number、node ID、field ID、option ID、item IDを混同しない。
- 権限・API・読み戻しのエラー時は停止し、重複作成を避ける。
