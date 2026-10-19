---
name: aide-init
description: Use when initializing or reconciling a repository's GitHub Project using AIDE conventions.
---

# AIDE Init: GitHub Project

Pythonを使えない環境でも、AIエージェントがGitHub CLIで対象RepositoryのIssue Projectを確認・初期化する。利用者に**共有する／共有しない**を選択してもらい、共有ならtrackedな`.agents/project.json`、非共有ならclone固有の`.agents/project.local.json`へ保存する。GitHubの実状態と公開範囲を確認して必要な承認を得た後にだけ変更し、最後にGitHubと保存先JSONを読み戻して一致を検証する。

## AGENTS.md案内の初期化

対象Repositoryの `.agents/templates/aide/AGENTS.md.template` を使い、ルート `AGENTS.md` にAIDE案内のセクションを挿入する。テンプレートは信頼できないファイル入力として扱い、内容を確認してから使う。案内は通常のMarkdown見出しで識別し、HTMLコメント等の管理タグは `AGENTS.md` に出力しない。

1. 対象リポジトリのルートとテンプレートを確認する。`.agents`、`.agents/templates`、`.agents/templates/aide`、テンプレートファイルのいずれかがsymlink、通常のディレクトリ／ファイルでない場合は停止する。対象の `AGENTS.md` もsymlink、通常ファイルでない、UTF-8として読めない場合は読み書きせず停止する。
2. テンプレートが単独のMarkdownセクションで、先頭に `## AIDEの利用案内`、その下に `### GitHub Project` を持つことを確認する。挿入先の見出し階層を読み、既存の最上位見出しが `#` なら案内を `##`、`#` がなく見出しがあるなら最も浅い見出しと同じ深さ、見出しがなければ `#` とする。テンプレート内の見出し深度は同じ差分だけ調整する。子見出しがMarkdownの最大深度6を超える場合や、文書構造が曖昧な場合は停止する。
3. 案内セクションは、既存文書の記載を変更・削除せず末尾へ挿入する。案内の識別見出しは深度にかかわらず `AIDEの利用案内` とする。同名見出しが複数ある場合は停止する。既存に同名セクションが1つあり、その本文が現在のテンプレートから生成した内容と一致するなら変更不要とする。内容が異なる場合は、既存内容との差分を示し、利用者が加えた記載を保つ統合案を作って更新案への明示承認を待ち、セクション外も保持する。

ルート `AGENTS.md` 全体として配布したタグなし案内は、以下の**完全な文書**に一致する場合だけ、既知の移行候補として扱う。対象は現行配布版の1候補のみで、部分一致や複数文書をつないだ判定はしない。比較時に許される正規化は、ファイル末尾にある改行コード1つ（LFまたはCRLF）の有無だけ。他の空白、改行、文字の変更・追記・削除は一致とみなさない。一意に一致した場合も自動更新せず、現行セクションへの変換案を提示して明示承認を待つ。一致する候補がない、複数候補に曖昧、一部編集・追記済みの場合は既知版と推測せず停止する。

#### 現行配布版（PR #66後、fa60a6a）

```markdown
# 開発ルール

ルールの正本は[開発ルール目次](.agents/docs/aide/00_index.md)配下の文書です。

- 開発作業では、[ブランチ](.agents/docs/aide/01-branches.md)・[情報保護](.agents/docs/aide/06-security.md)・[コミット](.agents/docs/aide/08-commit-messages.md)の共通ルールを確認してください。
- Issueの起票・対応、開発変更、レビュー、作業の再開・中断では、[aide-workflowスキル](.agents/skills/aide-workflow/SKILL.md)を読み、該当工程の文書・節を確認してから操作してください。工程別の参照は、共通ルールを省略するためのものではありません。

## GitHub Project

- GitHub Project情報は `.agents/project.local.json` があればそれを、なければテンプレート設定 `.agents/project.json` を参照
- ローカル設定がなくテンプレート設定の `project` が `null` の場合は、[aide-initスキル](.agents/skills/aide-init/SKILL.md)で初期化
```

案内削除を依頼された場合も、削除対象セクションと残る文書の差分を示し、明示承認を得てから削除する。以前のコメントタグによる管理ブロックがある場合は、タグの組が一組で順序も正しいことを確認し、ブロック内だけをこのセクションに置き換えてタグを取り除く。タグの欠落・重複・逆順、または同名見出しとの重複があれば推測で修復せず停止する。
4. 対象ファイルの現在内容と提案後の全文または明瞭なdiffを示し、作成・挿入・更新・タグ付き旧ブロックからの移行の別と、既存記載を維持することを説明して、明示的な確認・承認を待つ。承認前は `AGENTS.md` を作成・変更しない。Project初期化の承認をAGENTS.md更新の承認として扱わない。
5. 承認された提案と適用直前のファイル状態が一致することを再確認する。不一致なら書き込まず、最新内容で提案を作り直す。承認後だけ同じディレクトリ内に一時ファイルを作って提案全文を書き、読み戻して内容を検証してから置換する。既存ファイルは元の権限を保って置換し、新規作成では排他的な作成で既存ファイルを上書きしない。排他的に作成できない環境では停止する。失敗時は一時ファイルを除去し、元ファイルを保持する。
6. 置換後に `AGENTS.md` を読み戻し、承認された内容とバイト単位で一致すること、既存の案内外の記載が保たれていること、管理タグが含まれないことを確認する。不一致・失敗は成功扱いせず報告する。

この案内はProjectの確認・初期化と別個の承認を要する。利用者がAGENTS.md変更を承認しない場合はそのファイルを変更せず、他の承認済みProject手順だけを続行してよい。

## 実行

副作用を伴う工程へ進む前に、実行位置を含む全前提条件を最初の副作用より前に判定する。判定不能やコマンド失敗は成功扱いせず、後続のGitHub操作・設定書き込みを行わない。

開発開始時の読み取り専用参照は[Project設定の解決規則](../../docs/aide/03-project.md#読み取り専用の設定解決)に従う。リンク作業ツリーから主側の設定を参照できても、以下の初期化・設定更新のプリフライトは緩和しない。

### 主作業ツリーのプリフライト

Repository rootで、GitHub CLIの認証確認・Project検索・作成・リンク・Status変更・両設定ファイルの書き込みより前に、次の確認を行う。`aide-init`は主作業ツリーからのみ実行し、リンク作業ツリーでは実行しない。`.agents/project.json`の変更は共有の選択、または承認済みの非共有への切替に限る。

```sh
if ! is_bare=$(git rev-parse --is-bare-repository 2>/dev/null); then
  printf '%s\n' 'Error: unable to verify a non-bare Git repository.' >&2
  exit 1
fi
if [ "$is_bare" != "false" ]; then
  printf '%s\n' 'Error: aide-init requires a non-bare primary working tree.' >&2
  exit 1
fi
if ! git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null); then
  printf '%s\n' 'Error: unable to resolve the Git directory.' >&2
  exit 1
fi
if ! git_common_dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
  printf '%s\n' 'Error: unable to resolve the common Git directory.' >&2
  exit 1
fi
if [ -z "$git_dir" ] || [ -z "$git_common_dir" ] || [ "$git_dir" != "$git_common_dir" ]; then
  printf '%s\n' 'Error: aide-init must run from the primary working tree.' >&2
  exit 1
fi
```

この判定が成功した場合だけ、既存のstep 1へ進む。リンク作業ツリー、bare repository、Git未導入、リポジトリ外、想定外出力、その他の失敗では停止し、リモート・ローカルの副作用を起こさない。

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

1. 対象ホストを固定し、全ての`gh`認証確認・検索・取得・作成・更新・読み戻しに同一の`GH_HOST`、または各コマンド対応時の同一`--hostname`を適用する。`GH_HOST`が空や意図しない値でないことを確認し、指定ホストの`gh auth status`が成功し、対象アカウントにProject操作権限があることを確認する。`origin`・認証先・両設定ファイルを[設定解決規則](../../docs/aide/03-project.md#読み取り専用の設定解決)で照合する。`.agents/project.json`は通常ファイルで、中立テンプレート値または完全な共有設定でなければ停止する。`.agents/project.local.json`が存在する場合は通常ファイルでmode `0600`、完全なschemaを持ち、保存値をGitHubと照合する。ローカル設定が存在するのに不完全・不正・origin不一致ならテンプレートへフォールバックせず停止する。両設定に実値があれば同じ値でも競合として停止し、優先順位で選択しない。両方未設定の場合だけ中立テンプレート値を初期化の既定値として使う。
   - GitHubへの変更・設定書き込みの前に、次の「共有方式の選択と保存先切替」を実行する。既存設定がある場合も現在の方式を示して、維持か切替かを利用者に選択してもらう。未回答・拒否・判断不能なら副作用なしで停止する。
2. `project`が未設定なら、前項で固定・検証したホストに対するProject検索でRepositoryにリンク済みの候補を探す。取得したProject URLのホストも指定ホストと完全一致することを確認する。GitHub EnterpriseではAPI/CLIの検索結果、Project URL、Repository originのいずれかが別ホストを指す場合、GitHub.comへ誤接続する可能性があるため停止し、読み書きを行わない。同名の未リンクProjectは、同じ可視性で空の場合のみ再利用する。候補の重複・閉鎖・Issue無効・owner/Repository不一致・取得不完全なら停止する。
3. 対象と変更計画を提示して承認を待ち、その後にだけProject作成・リンク・Status設定を行う。Public作成と、項目があるProjectのStatus option変更には、通常承認とは別に、対象option・既存項目への影響を示した明示承認を得る。Statusの正とする選択肢は[Project管理](../../docs/aide/03-project.md#status)の5つ（`Backlog`、`Ready`、`In progress`、`In review`、`Done`）。
   - Project作成・Repositoryリンク直後にStatus fieldを読み戻す。fieldがなければ、正規の5 optionsを持つStatus fieldを作成する。作成エラーや競合時は再作成せず、fieldを読み戻して状態を判定する。成功応答だけで完了とせず、作成後も必ず読み戻す。
   - fieldが既にある場合はそのoption一覧（ID・名前・色・説明）を読み取る。GitHub標準option `Todo` は `Backlog`、`In Progress` は `In progress`、`Done` は `Done` へ対応させる。移行先が既に存在する場合や、対応関係が一意でない場合は自動変更せず停止して確認する。`Ready`と`In review`など不足する正規optionは追加する。既存optionのID・色・説明と未知optionは保持し、未知optionを削除・改名しない。
   - 既存fieldのoption更新では、対象ホスト上のGitHub Project fieldを編集できるCLIまたはAPIを使う。利用する手段がoptionを安全に更新できることを確認できない場合は推測で実行せず停止する。現在の全optionを読み戻し、既存optionのID・色・説明を保持した上で、承認済みの改名・追加だけを反映する。option IDを省略したり、未取得のoptionを更新対象から落としたりしない。実行エラー、結果不明、読み戻し不一致があれば再実行・続行せず停止する。
   - 対象Projectに項目（アーカイブ済みを含む）がある場合、optionの追加・改名はいずれも個別の明示承認がない限り実行しない。承認が得られない場合はProject作成・リンク等の承認済み作業までに留め、Status未設定／未整合として報告する。
4. 同じ対象ホストからRepositoryリンク・Project URLのホスト・可視性・Status fieldと全option（ID・名前）・項目数（アーカイブ済みを含む）を読み戻して照合する。5つの正規optionが全て存在し、承認なく未知optionを変更していないことを確認する。optionの不足、予期しない変更、読み戻し不能があれば成功扱いにせず、設定を書き込まない。照合後にのみ、承認された保存先へ次節の保存手順で作成・更新する。JSON内容、GitHubとの一致、共有／非共有の配置条件を読み戻し検証する。`repository`が未設定の場合は検証済みのoriginで初期化し、既存の不一致は停止する。失敗・判断不能時は書き込みを続けない。

### 共有方式の選択と保存先切替

1. 次の2択を提示し、明示回答を待つ。既定の提案は非共有とし、無回答を承認に変換しない。方式の選択だけでは書き込みの承認とせず、対象と変更計画を提示してから承認を得る。
   - **共有する**：`.agents/project.json`に保存する。Repositoryの読者がProjectのowner・名前・URL・識別情報を読め、Git履歴やfork／テンプレートにも残る。Private Projectでも同じで、Repositoryを将来公開すると公開範囲が広がる。Projectのアクセス権は別途必要であり、共有設定は権限を付与しない。
   - **共有しない**：`.agents/project.local.json`に保存する。`.agents/project.json`は中立値を維持する。各cloneの主側で初期化が必要。同じcloneのリンク側は主側を読み取り専用で参照できる。
2. 共有の選択時は、Repositoryの実際の可視性、Projectの可視性、Gitに保存する項目と公開対象を説明し、**公開範囲への個別の明示承認**を得る。Private Repositoryも読者への共有であり、この承認を省略しない。Project作成やPublic Project作成の承認で代用しない。共有先の`.agents/project.json`がGit追跡対象で無視されないことを確認する。未追跡なら中立ファイルの追跡を利用者に依頼して停止する。勝手にstage・commit・pushしない。ローカル設定を維持または新規作成する場合は`git check-ignore -q --no-index -- .agents/project.local.json`が成功し、Gitで追跡されていないことを確認する。
3. 切替の場合は両ファイルの変更・削除対象を計画に含め、個別に承認を得る。非共有→共有では検証済みのローカル設定を共有先へ保存・読み戻し後に、内容が承認時と同じローカル設定だけを削除する。共有→非共有ではローカル設定をmode `0600`で保存・読み戻し後に、共有先を中立値へ戻す。**過去のGit履歴からは削除されない**ことを説明し、履歴改変は行わない。異なるProjectへの切替や両実値の競合解消を自動移行に混ぜず停止し、別途承認された解決計画を求める。
4. 書き込み直前に実行位置・origin・GitHubの識別情報・可視性・Statusと、両ファイルの配置・Git状態・内容・権限が承認時から変わっていないことを再確認する。不一致なら再提案して承認を待つ。初回保存・維持更新・切替とも、変更前bytesと権限を保護された一時領域に保持し、同じディレクトリ内の一時ファイルへ新しいJSONを書き、読み戻してから置換する。新規作成は排他的に行い、既存を上書きしない。共有ファイルは元の権限を維持し、ローカルと一時ファイルはmode `0600`とする。symlinkを辿らず、不安全な配置・排他的操作ができない環境では停止する。
5. 変更後に両設定の完全なschema・origin・GitHub照合と保存方式の不変条件（共有の実値だけ、または中立値＋ローカル実値）を読み戻す。途中失敗は成功扱いせず、他者の変更を上書きしないことを再確認して変更前へ復旧する。復旧不能や切替途中の両実値が残った場合は後続操作を止め、値を開示せず復旧が必要な相対パス・状態を報告する。復旧を検証するまで通常の設定解決へ進めない。一時ファイル・退避データを後始末し、認証情報を保存しない。Gitへのstage・commit・pushは設定保存とは別の許可を要する。

非共有のローカル設定をIssue、PR、ログ、installer/NPM配布物へ出力しない。ローカル設定がGitで無視されていない、または権限を安全に設定できない場合は書き込みを停止する。共有設定も許可された保存先以外のログやIssueへ転載しない。

Gitの仕組みとしての総称は[ブランチ戦略](../../docs/aide/01-branches.md)で定義し、設定の更新場所は[Project管理](../../docs/aide/03-project.md)に従う。

## アウトプット

初期値および非共有時の`.agents/project.json`は次の中立値とする。`project_defaults`は新規Projectの既定値として使う。

```json
{
  "schema_version": 1,
  "repository": null,
  "project_defaults": {"title": null, "visibility": "PRIVATE"},
  "project": null
}
```

共有／非共有の実値は同じschemaを使い、保存先だけを変える。中立値も実値もJSONの重複キーは全階層で拒否し、後勝ちの解釈をしない。トップレベルは`schema_version: 1`（整数・bool不可）、`repository: "OWNER/REPO"`、`project_defaults: {title: null, visibility: PRIVATE}`、`project`の4キーに限定する。`project`は`owner`（Repository owner）、`owner_type`（`User`または`Organization`）、`number`（正の整数・bool不可）、`node_id`、`title`、`url`、`visibility`（`PRIVATE`または`PUBLIC`）、`repository`の8キーだけとする。文字列は空でなく、`repository`は両箇所ともoriginに一致し、URLはoriginのホスト・owner_type・owner・numberと一致しなければならない。余分なキー・認証情報・token・field/option IDを含めない。両実値の共存は許さない。

完了時はProject URLをユーザーへの直接応答でのみ報告する。確認・変更内容とローカル設定のignore・権限・整合性確認結果を伝える。Issue・PR・ログにはProject URLやローカル設定値を記録せず、安全な状態・検証の要約だけを残す。設定内容そのものは出力しない。停止した場合は理由と未実施の変更を報告する。

## 安全境界

- origin・認証先・Projectリンク・可視性・Status・設定の不一致、曖昧な候補、取得不完全、権限不足では推測で選択・変更しない。項目のあるProjectのStatus変更は個別承認がない限り行わず、未知optionは上書きしない。項目数はアーカイブ済みも含めて確認する。
- 設定ファイルのsymlinkや不正な配置を拒否し、GitHubの状態を検証した後にのみ承認された保存先へ書き込む。ローカル設定はGit ignoreを確認し、mode `0600`で保存する。ログ・Issue・PRへ値を表示せず、秘密情報、token、field/option IDは保存しない。
- forkを含む別Repositoryへ共有設定を再利用するとき、originと不一致ならsource Repositoryの設定を使わず停止する。ユーザーの確認なしに設定をnullへ書き換えない。ローカル設定が存在してoriginと不一致の場合もProject操作を行わず停止する。AIDE自身のテンプレート源泉は中立値を保ち、installer/NPM配布物には専用中立テンプレートだけを含める。
