---
name: aide-init
description: Use when initializing or reconciling a repository's GitHub Project using AIDE conventions.
---

# AIDE Init: GitHub Project

Pythonを使えない環境でも、AIエージェントがGitHub CLIで対象RepositoryのIssue Projectを確認・初期化し、`.agents/project.json`を作成できるようにする。GitHubの実状態を確認してから必要な変更だけを行い、最後にGitHubとJSONを読み戻して一致を検証する。

## AGENTS.md案内の初期化

対象Repositoryの `.agents/templates/AGENTS.md.template` を使い、ルート `AGENTS.md` にAIDE案内のセクションを挿入する。テンプレートは信頼できないファイル入力として扱い、内容を確認してから使う。案内は通常のMarkdown見出しで識別し、HTMLコメント等の管理タグは `AGENTS.md` に出力しない。

1. 対象リポジトリのルートとテンプレートを確認する。`.agents`、`.agents/templates`、テンプレートファイルのいずれかがsymlink、通常のディレクトリ／ファイルでない場合は停止する。対象の `AGENTS.md` もsymlink、通常ファイルでない、UTF-8として読めない場合は読み書きせず停止する。
2. テンプレートが単独のMarkdownセクションで、先頭に `## AIDEの利用案内`、その下に `### GitHub Project` を持つことを確認する。挿入先の見出し階層を読み、既存の最上位見出しが `#` なら案内を `##`、`#` がなく見出しがあるなら最も浅い見出しと同じ深さ、見出しがなければ `#` とする。テンプレート内の見出し深度は同じ差分だけ調整する。子見出しがMarkdownの最大深度6を超える場合や、文書構造が曖昧な場合は停止する。
3. 案内セクションは、既存文書の記載を変更・削除せず末尾へ挿入する。案内の識別見出しは深度にかかわらず `AIDEの利用案内` とする。同名見出しが複数ある場合は停止する。既存に同名セクションが1つあり、その本文が現在のテンプレートから生成した内容と一致するなら変更不要とする。内容が異なる場合は、既存内容との差分を示し、利用者が加えた記載を保つ統合案を作って更新案への明示承認を待ち、セクション外も保持する。旧版がルート `AGENTS.md` 全体として配布していたタグなし案内も移行候補として扱う。ファイル全体が次の旧案内本文と末尾の改行だけを正規化して完全一致する場合に限り、旧案内と判断して現行セクションへの変換案を提示する。部分一致、追記・編集済み、見出し構成が異なる場合は旧案内と推測せず、同種の見出し・内容があって判別できない状態として停止する。

   ```markdown
   # 開発ルール

   ルールの正本は[開発ルール目次](.agents/docs/aide/00_index.md)配下の文書です。

   - 開発作業では、[ブランチ](.agents/docs/aide/01-branches.md)・[情報保護](.agents/docs/aide/06-security.md)・[コミット](.agents/docs/aide/08-commit-messages.md)の共通ルールを確認してください。
   - Issueの起票・対応、開発変更、レビュー、作業の再開・中断では、[aide-workflowスキル](.agents/skills/aide-workflow/SKILL.md)を読み、該当工程の文書・節を確認してから操作してください。工程別の参照は、共通ルールを省略するためのものではありません。

   ## GitHub Project

   - GitHub Project情報は `.agents/project.json` を参照
   - ファイルがないか `project` が `null` の場合は、[aide-initスキル](.agents/skills/aide-init/SKILL.md)でセットアップ
   ```

   案内削除を依頼された場合も、削除対象セクションと残る文書の差分を示し、明示承認を得てから削除する。以前のコメントタグによる管理ブロックがある場合は、タグの組が一組で順序も正しいことを確認し、ブロック内だけをこのセクションに置き換えてタグを取り除く。タグの欠落・重複・逆順、または同名見出しとの重複があれば推測で修復せず停止する。
4. 対象ファイルの現在内容と提案後の全文または明瞭なdiffを示し、作成・挿入・更新・タグ付き旧ブロックからの移行の別と、既存記載を維持することを説明して、明示的な確認・承認を待つ。承認前は `AGENTS.md` を作成・変更しない。Project初期化の承認をAGENTS.md更新の承認として扱わない。
5. 承認された提案と適用直前のファイル状態が一致することを再確認する。不一致なら書き込まず、最新内容で提案を作り直す。承認後だけ同じディレクトリ内に一時ファイルを作って提案全文を書き、読み戻して内容を検証してから置換する。既存ファイルは元の権限を保って置換し、新規作成では排他的な作成で既存ファイルを上書きしない。排他的に作成できない環境では停止する。失敗時は一時ファイルを除去し、元ファイルを保持する。
6. 置換後に `AGENTS.md` を読み戻し、承認された内容とバイト単位で一致すること、既存の案内外の記載が保たれていること、管理タグが含まれないことを確認する。不一致・失敗は成功扱いせず報告する。

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
3. 対象と変更計画を提示して承認を待ち、その後にだけProject作成・リンク・Status設定を行う。Public作成と、項目があるProjectのStatus option変更には、通常承認とは別に、対象option・既存項目への影響を示した明示承認を得る。Statusの正とする選択肢は[Project管理](../../docs/aide/03-project.md#status)の5つ（`Backlog`、`Ready`、`In progress`、`In review`、`Done`）。
   - Project作成・Repositoryリンク直後にStatus fieldを読み戻す。fieldがなければ、`gh project field-create <number> --owner <owner> --name Status --data-type SINGLE_SELECT --single-select-options 'Backlog,Ready,In progress,In review,Done'`で作成し、成功応答だけで完了とせず読み戻す。作成エラーや競合時は再作成せず、fieldを読み戻して状態を判定する。
   - fieldが既にある場合はそのoption一覧（ID・名前・色・説明）を読み取る。GitHub標準option `Todo` は `Backlog`、`In Progress` は `In progress`、`Done` は `Done` へ対応させる。移行先が既に存在する場合や、対応関係が一意でない場合は自動変更せず停止して確認する。`Ready`と`In review`など不足する正規optionは追加する。既存optionのID・色・説明と未知optionは保持し、未知optionを削除・改名しない。
   - `gh project field-edit`は存在しないため、既存fieldのoption更新は、対象ホストのGitHub GraphQL `updateProjectV2Field` mutationで行う。現在の全optionを読み戻し、既存optionのID・色・説明を保持した完全なoption配列に、承認済みの改名・追加だけを反映して送る。option IDを省略したり、未取得のoptionを配列から落としたりしない。mutationのエラー、結果不明、読み戻し不一致があれば再送・続行せず停止する。
   - 対象Projectに項目（アーカイブ済みを含む）がある場合、optionの追加・改名はいずれも個別の明示承認がない限り実行しない。承認が得られない場合はProject作成・リンク等の承認済み作業までに留め、Status未設定／未整合として報告する。
4. 同じ対象ホストからRepositoryリンク・Project URLのホスト・可視性・Status fieldと全option（ID・名前）・項目数（アーカイブ済みを含む）を読み戻して照合する。5つの正規optionが全て存在し、承認なく未知optionを変更していないことを確認する。optionの不足、予期しない変更、読み戻し不能があれば成功扱いにせず、`.agents/project.json`を更新しない。照合後にのみ`.agents/project.json`を更新・読み戻し検証する。`repository`が未設定の場合は検証済みのoriginで初期化し、既存の不一致は停止する。失敗・判断不能時は書き込みを続けない。

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
