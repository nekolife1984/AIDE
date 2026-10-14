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

有効な設定は次の読み取り専用手順で解決する。ローカル設定が存在するのに不完全・不正・originと不一致の場合は、テンプレートへフォールバックせず停止する。`aide-init`は主側で確認済みのライブ設定を保存し、trackedなテンプレート設定を変更しない。

`aide-init`と両設定ファイルの更新は主作業ツリーでのみ行う。リンク作業ツリーでは`.agents/project.json`と`.agents/project.local.json`を更新せず、ローカル設定がない場合も`aide-init`を実行しない。必要な初期化・更新はリンク作業ツリーを作成する前に主作業ツリーで完了する。

### 読み取り専用の設定解決

1. `GIT_DIR`・`GIT_WORK_TREE`・`GIT_COMMON_DIR`による実行位置の上書きがあれば停止する。現在の作業ツリーで次を個別に実行し、失敗・想定外出力・bareでは停止する。パスは実体パスへ揃えて比較する。

```sh
git rev-parse --is-bare-repository
git rev-parse --path-format=absolute --show-toplevel
git rev-parse --path-format=absolute --git-dir
git rev-parse --path-format=absolute --git-common-dir
git worktree list --porcelain -z
```

2. `worktree list`はNUL区切りの属性・空属性によるレコード境界として読み、先頭レコードの`worktree`絶対パスを主作業ツリー候補にする。空白や改行で分割しない。候補が欠落・bare・不存在・symlinkなら停止する。候補で上の`rev-parse`4件を再実行し、rootが候補自身、git directoryとcommon Git directoryが同じ、common Git directoryが現在側と同じであることを確認する。別clone、親ディレクトリからの推測、任意パスの指定で代用しない。
3. 現在側と主側それぞれで`git remote get-url origin`を取得し、秘密を含むURLを表示せず、ホスト・owner・Repositoryを照合する。SSHとHTTPSの表記差だけを許し、形式不明・不一致では停止する。両側の`.agents/project.json`は通常ファイルで中立なテンプレート値のままでなければ停止する。
4. 設定の取得元は主側の`.agents/project.local.json`だけとする。リンク側のローカル設定が存在する場合は、同じ内容やsymlinkでも曖昧な独自配置として停止する。主側設定がない場合、主側ではテンプレートを基準に初期化案を提示できるが、リンク側では停止して主側での初期化・照合を依頼する。設定をコピーせず、リンク側で初期化・設定更新しない。
5. 読む前に両側の`.agents`からファイルまでがsymlinkでなく、通常のディレクトリ／ファイルであることを確認する。主側ローカル設定はmode `0600`、Gitで追跡されていないこと、主側の`git check-ignore -q --no-index -- .agents/project.local.json`が成功することを確認する。UTF-8 JSON・[完全なschema](../../skills/aide-init/SKILL.md#アウトプット)・originとの整合を検証し、余分な認証情報や不完全値を許さない。配置・権限・内容の不正や読めない場合は停止する。
6. [ホスト固定と照合](../../skills/aide-init/SKILL.md#実行)の条件に従い、GitHubの読み取り専用照会でRepository・Projectの識別情報・リンク・可視性・開放状態・Statusを確認する。ローカル検証だけでGitHub照合済みと扱わない。設定値を出力しない。判断不能・不一致・設定やGitの状態変化では停止して主側で再確認し、参照のために設定やProject構成を書き換えない。参照と、別途依頼されたIssue／Projectの進捗操作は区別する。

## Status

| Status | 意味 |
|---|---|
| `Backlog` | 未着手 |
| `Ready` | 着手可能・依存解決済み |
| `In progress` | 作業中 |
| `In review` | PR確認中 |
| `Done` | 完了条件を満たし、マージ済み |

依存関係はIssue間に設定し、Statusで代用しない。`Done`の前に完了条件とマージを確認する。
