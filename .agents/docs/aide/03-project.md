# GitHub Project管理

## 役割

Issueは目的と完了条件、PRは変更と検証結果、Projectは進捗の管理に使う。

## 登録

- 作成したIssueは必ずProjectへ追加し、`Backlog`に設定。
- 既存Issueの追加時も`Backlog`に設定。
- 登録後、IssueとStatusを読み戻して確認。
- 原則1リポジトリにつきProjectは1つ。

## Project設定の管理

`aide-init`で利用者が保存方式を選ぶ。**共有する**場合はGit管理対象の`.agents/project.json`に実際のRepository・Project設定を保存する。**共有しない**場合は`.agents/project.json`の`repository`と`project`を`null`に保ち、clone固有の設定をgitignore対象の`.agents/project.local.json`（mode `0600`）へ保存する。ローカルファイルはGit・Issue・PR・installerへ含めない。認証情報・token・field/option IDはいずれにも保存しない。

共有設定はRepositoryの読者とGit履歴へProjectの名前・URL・識別情報を公開する。ProjectがPrivateでもこの情報は共有されるが、Projectへのアクセス権は付与されない。共有の選択と公開範囲への明示承認を別途確認し、未回答や拒否を共有への同意とみなさない。非共有への切替でも過去のGit履歴は消えない。

有効な設定は次の読み取り専用手順で解決する。設定の不正や競合は他方へフォールバックせず停止する。保存先の選択・切替・更新は主側の`aide-init`だけで行い、通常の開発開始時には設定を変更しない。

AIDE自身をテンプレート源泉として配布する設定は中立のまま維持する。installer/npmは専用の中立テンプレート`scripts/templates/project.json`だけを供給し、配布元の`.agents/project.json`と`.agents/project.local.json`を梱包・コピーしない。共有設定を持つ別RepositoryをGitHubテンプレートやforkとして再利用すると設定も継承されるため、origin不一致で停止する。承認なしに中立化しない。

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
3. 現在側と主側それぞれで`git remote get-url origin`を取得し、秘密を含むURLを表示せず、ホスト・owner・Repositoryを照合する。SSHとHTTPSの表記差だけを許し、形式不明・不一致では停止する。読む前に両側の`.agents`から設定ファイルまでがsymlinkでなく、通常のディレクトリ／ファイルであることを確認する。両側の`.agents/project.json`はUTF-8 JSONとして中立値または完全な共有設定であり、JSON内容が一致しなければ停止する。リンク側の別Revisionの共有設定を独立に使わない。
4. リンク側のローカル設定が存在する場合は、同じ内容やsymlinkでも曖昧な独自配置として停止する。主側の`.agents/project.json`が完全な共有設定なら、それだけを取得元とする。主側にローカル設定も存在すれば同じ値でも競合として停止する。共有設定は両側でGit追跡対象かつ`git check-ignore -q --no-index -- .agents/project.json`がexit 1（無視されない）であることを確認する。これ以外の終了値は停止する。
5. 主側の`.agents/project.json`が中立値の場合だけ、主側の`.agents/project.local.json`を取得元にできる。主側ローカル設定はmode `0600`、Gitで追跡されていないこと、主側の`git check-ignore -q --no-index -- .agents/project.local.json`が成功することを確認する。どの取得元もUTF-8 JSON・[完全なschema](../../skills/aide-init/SKILL.md#アウトプット)・originとの整合を検証し、余分な認証情報や不完全値を許さない。ローカル設定が存在するのに不完全・不正・origin不一致ならフォールバックせず停止する。両方未設定なら、主側でのみ中立テンプレートを基準に共有／非共有の初期化案を提示できる。リンク側では停止して主側での初期化・照合を依頼する。設定をコピーせず、リンク側で初期化・設定更新しない。配置・権限・内容の不正や読めない場合は停止する。
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
