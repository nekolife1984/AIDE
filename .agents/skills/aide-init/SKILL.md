---
name: aide-init
description: Use when initializing or reconciling a repository's GitHub Project using AIDE conventions.
---

# AIDE Init: GitHub Project

Pythonを使えない環境でも、AIエージェントがGitHub CLIで対象RepositoryのIssue Projectを確認・初期化し、`.agents/project.json`を作成できるようにする。GitHubの実状態を確認してから必要な変更だけを行い、最後にGitHubとJSONを読み戻して一致を検証する。

## AGENTS.md案内の初期化

既存リポジトリ向けインストーラーは `AGENTS.md` を直接コピーしない。導入された `.agents/templates/AGENTS.md.template` をAIDE管理案内のテンプレートとして使い、`aide-init` の実行時に対象リポジトリのルート `AGENTS.md` へ反映する。テンプレートは信頼できないファイル入力として扱い、内容を確認してから使う。

1. 対象リポジトリのルートとテンプレートを確認する。`.agents`、`.agents/templates`、テンプレートファイルのいずれかがsymlink、通常のディレクトリ／ファイルでない場合は停止する。対象の `AGENTS.md` もsymlink、通常ファイルでない、UTF-8として読めない場合は読み書きせず停止する。
2. テンプレート内の `<!-- BEGIN AIDE-MANAGED GUIDANCE -->` と `<!-- END AIDE-MANAGED GUIDANCE -->` が各1個あり、順序・対応が正しいことを確認する。対象 `AGENTS.md` に同じマーカーがない場合は既存内容を保持して末尾へブロックを追加する。既存内容が空でなければ、追記の区切りとして既存末尾が改行なら空行を1行、改行でなければ改行を2つ加える。ただし既存ファイル全体がマーカーを除いたテンプレート本文と完全一致する場合は、旧インストーラーがコピーしたAIDE案内とみなし、重複追加せずその内容を管理ブロックで囲む提案をする。開始・終了マーカーが片方だけ、重複、逆順など不正な場合は自動修復せず停止する。正しいブロックが1つある場合はその範囲だけをテンプレートの内容へ更新し、範囲外は一切変更しない。
3. 対象ファイルの現在内容と提案後の全文または明瞭なdiffを示し、作成・追記・更新の別と、既存の範囲外の内容を保持することを説明して、明示的な確認・承認を待つ。承認前は `AGENTS.md` を作成・変更しない。Project初期化の承認をAGENTS.md更新の承認として扱わない。
4. 承認された提案と適用直前のファイル状態が一致することを再確認する。不一致なら書き込まず、最新内容で提案を作り直す。承認後だけ同じディレクトリ内に一時ファイルを作って提案全文を書き、書き込み後に全文・マーカー数を検証してから置換する。既存ファイルは元の権限を保って置換し、新規作成では排他的な作成で既存ファイルを上書きしない。排他的に作成できない環境では停止する。失敗時は一時ファイルを除去し、元ファイルを保持する。
5. 置換後に `AGENTS.md` を読み戻し、承認された内容とバイト単位で一致すること、マーカーが1組だけで範囲外の既存内容が保たれていることを確認する。不一致・失敗は成功扱いせず報告する。

この案内はProjectの確認・初期化と別個の承認を要する。利用者がAGENTS.md変更を承認しない場合はそのファイルを変更せず、他の承認済みProject手順だけを続行してよい。

## 実行

Repository rootで、GitHub CLI (`gh`) の認証とProject操作権限を確認する。GitHub.comではホストを`github.com`とする。GitHub Enterpriseでは組織のWebホスト名（ホスト名のみ。scheme・path・末尾のslashなし）を先に確定し、全ての`gh`コマンドで同じホストを使う。推奨はコマンド実行環境に`GH_HOST=<enterprise-host>`を設定する方法。設定がコマンド環境に継承されない場合は、各コマンドが対応していると確認できた場合に限り`--hostname <enterprise-host>`を使う。Enterpriseのコマンドで`github.com`を指定・フォールバックしてはならない。

Enterpriseでの認証例（`GH_HOST`は以降の全`gh`コマンドに継承させる）：

```sh
export GH_HOST=github.example.com
gh auth login --web --hostname "$GH_HOST" --scopes project
gh auth status --hostname "$GH_HOST"
```

すでに認証済みで`project` scopeが不足する場合だけ`gh auth refresh --hostname "$GH_HOST" --scopes project`を実行する。GitHub.comの既存利用では`GH_HOST`を設定せず、以下の標準手順を維持する。

```sh
gh auth login --web --hostname github.com --scopes project
gh auth status --hostname github.com
gh auth refresh --hostname github.com --scopes project
```

コマンド実行前に、`GH_HOST`（または明示したhostname）、`gh auth status`の対象アカウント、Repositoryの`origin` URLのホストが一致することを確認する。originは秘密情報を出さない形式で取得し、HTTPSならURLのauthority、SSHなら`[user@]host:path`のhost部分を比較する。GitHub.com利用時はoriginが`github.com`でなければ停止する。Enterprise利用時はoriginが指定ホストと完全一致しなければ停止する。URLの形式が判別不能、ホストが曖昧、認証が別ホストにしかない場合も停止し、ホストを推測したりGitHub.comへ切り替えたりしない。

1. 対象ホストを固定し、全ての`gh`認証確認・検索・取得・作成・更新・読み戻しに同一の`GH_HOST`、または各コマンド対応時の同一`--hostname`を適用する。`GH_HOST`が空や意図しない値でないことを確認し、指定ホストの`gh auth status`が成功し、対象アカウントにProject操作権限があることを確認する。`origin`・認証先・`.agents/project.json`を照合する。有効な未設定状態は`repository: null`、`project: null`、`project_defaults: {title: null, visibility: PRIVATE}`の組合せだけ。それ以外は保存済み情報をGitHubと比較し、不完全な設定や不一致は停止する。
2. `project`が未設定なら、前項で固定・検証したホストに対するProject検索でRepositoryにリンク済みの候補を探す。取得したProject URLのホストも指定ホストと完全一致することを確認する。GitHub EnterpriseではAPI/CLIの検索結果、Project URL、Repository originのいずれかが別ホストを指す場合、GitHub.comへ誤接続する可能性があるため停止し、読み書きを行わない。同名の未リンクProjectは、同じ可視性で空の場合のみ再利用する。候補の重複・閉鎖・Issue無効・owner/Repository不一致・取得不完全なら停止する。
3. 対象と変更計画を提示して承認を待ち、その後にだけProject作成・リンク・Status設定を行う。Public作成と、項目があるProjectのStatus変更には、通常承認とは別に影響を示した明示承認を得る。GitHub既定optionは対応を確認して移行し、未知optionは上書きしない。
4. 同じ対象ホストからRepositoryリンク・Project URLのホスト・可視性・Status/options・項目数（アーカイブ済みを含む）を読み戻して照合した後、`.agents/project.json`を更新・読み戻し検証する。`repository`が未設定の場合は検証済みのoriginで初期化し、既存の不一致は停止する。失敗・判断不能時は書き込みを続けない。

`.agents/project.json`はGit管理対象の共有設定で、変更は作業ツリーに現れる。公開リポジトリでは内容も公開される。Private Projectの識別情報を記載する場合は、対象の公開Repositoryと公開する識別情報を示し、書き込む前に別途明示承認を得る。

## アウトプット

`.agents/project.json`は次の共有設定を保持する。テンプレートの初期状態では`repository`と`project`を`null`にする。この状態では`project_defaults`は新規Projectの既定値としてのみ使う。title未指定時はRepository名を使い、Project選定後は実際のRepositoryとProject情報（owner、owner_type、number、node_id、title、url、visibility、repository）を設定する。field/option IDや認証情報は含めない。

```json
{
  "schema_version": 1,
  "repository": null,
  "project_defaults": {"title": null, "visibility": "PRIVATE"},
  "project": null
}
```

完了時は対象ProjectのURL、確認・変更した内容、キャッシュの検証結果を報告する。停止した場合は理由と未実施の変更を報告する。

## 安全境界

- origin・認証先・Projectリンク・可視性・Status・設定の不一致、曖昧な候補、取得不完全、権限不足では推測で選択・変更しない。項目のあるProjectのStatus変更は個別承認がない限り行わず、未知optionは上書きしない。項目数はアーカイブ済みも含めて確認する。
- 設定ファイルのsymlinkや不正な配置を拒否し、GitHubの状態を検証した後にのみ書き込む。公開される前提で内容を扱い、秘密情報、token、field/option IDは保存しない。
- forkを含む別Repositoryへテンプレートを再利用するとき、コピーされた設定がsource Repositoryに結び付いていればProjectの探索・選択・変更をせず停止する。ユーザーが承認して中立な初期設定へ戻すまで、sourceの既定値やProjectを使わない。
