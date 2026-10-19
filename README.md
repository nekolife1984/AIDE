# AIDE

AIコーディングエージェントと開発するリポジトリ向けのGitHubテンプレートです。`AGENTS.md`から開発ルールを案内し、Issue・Project運用、品質、セキュリティの資料と、開発フロー・GitHub Project初期化スキルを提供します。ルールの正本は文書、工程の実行順序と参照先はスキルで管理します。

## 初めて使う

### 必要なもの

- Git
- GitHub CLI（`gh`）と、対象リポジトリ・Projectを操作できるGitHubアカウント

GitHubで **Use this template** を選んで自分のリポジトリを作成します。GitHub.comなら`github.com`、GitHub Enterpriseなら自組織のホストを使い、`<OWNER>`と`<REPOSITORY>`を置き換えてcloneします。以降の認証ホスト・origin・Project URLも同じホストに揃えてください。

```sh
git clone https://<HOST>/<OWNER>/<REPOSITORY>.git
cd <REPOSITORY>
```

### GitHub認証とProjectの初期化

未認証の場合は、ブラウザーでGitHubにログインし、Project操作に必要な`project` scopeを付けます。GitHub Enterpriseでは、認証・Project操作を同じホストに固定するため、先に`GH_HOST`を設定してください。

GitHub Enterpriseを使う場合は、以下のようにホスト名を指定します。以降の`gh`コマンドでも`GH_HOST`を維持し、Repositoryの`origin`およびProject URLのホストが一致することを確認してください。不一致または認証不足の場合は操作を停止します。

```sh
export GH_HOST=github.example.com
gh auth login --web --hostname "$GH_HOST" --scopes project
gh auth status --hostname "$GH_HOST"
# すでに認証済みでproject scopeが不足している場合のみ実行
gh auth refresh --hostname "$GH_HOST" --scopes project
```

GitHub.comを使う場合のみ、`github.com`を指定して認証してください。認証後はscopeを確認し、すでに認証済みで`project` scopeが不足する場合だけ追加します。

```sh
gh auth login --web --hostname github.com --scopes project
gh auth status --hostname github.com
# すでに認証済みでproject scopeが不足している場合のみ実行
gh auth refresh --hostname github.com --scopes project
```

詳細：[初回認証（`gh auth login`）](https://cli.github.com/manual/gh_auth_login)、[認証状態の確認（`gh auth status`）](https://cli.github.com/manual/gh_auth_status)、[scopeの追加（`gh auth refresh`）](https://cli.github.com/manual/gh_auth_refresh)、[GitHub CLIのProject操作と必要なscope](https://cli.github.com/manual/gh_project)。

AIエージェントにGitHub Projectの初期化を依頼し、提示された変更計画を確認してから承認します。候補や状態が曖昧な場合は変更せず停止します。手順と安全境界は下記スキルを参照してください。

## 既存リポジトリへ導入する

AIDEをGitHubテンプレートとして新規作成せず、既存リポジトリにAIDEの共通運用資料を追加する場合は、`npx`から実行できます。コピー対象は [`scripts/aide_install_manifest.json`](scripts/aide_install_manifest.json) に限定され、中立な`.agents/project.json`テンプレートは含まれますが、`.agents/project.local.json`、`AGENTS.md`本体、認証情報、ローカルキャッシュは含まれません。Project設定は専用の中立テンプレート [`scripts/templates/project.json`](scripts/templates/project.json) から供給し、配布元cloneの共有設定はコピー・npm梱包しません。導入先に共有設定がある場合は競合として保持し、値を差分出力せず停止します。`AGENTS.md`用の案内はテンプレートとして配布されます。`.agents/project.local.json`はclone固有の設定としてGitで無視してください。

Node.js 18以降とPython 3.9以降が必要です。まずdry-runで追加・同一・移動・競合と、旧配置・新配置が共存する場合の旧配置ファイルの削除対象を確認します。dry-runはファイルを変更しません。インストーラーは導入先の `AGENTS.md` を作成・変更しません。

```sh
npx --yes --package=github:nekolife1984/AIDE aide-install /path/to/existing-repository --dry-run
```

計画に問題がなければ、明示的に `--apply` を付けて適用します。

```sh
npx --yes --package=github:nekolife1984/AIDE aide-install /path/to/existing-repository --apply
```

この方法では公開GitHubリポジトリからCLIを取得します。ネットワーク接続が必要です。AIDEをclone済みの場合は、同梱のPythonスクリプトを直接実行することもできます。

```sh
python3 /path/to/AIDE/scripts/aide_install.py /path/to/existing-repository --dry-run
python3 /path/to/AIDE/scripts/aide_install.py /path/to/existing-repository --apply
```

通常のコピー対象に競合が1件でもあれば、全件を表示して書き込みを停止します。競合ファイルを手動で退避または統合してから再実行してください。`AGENTS.md`は配布されたテンプレートを使い、`aide-init`が変更案を提示して確認・承認を得てから作成または既存内容へ追記します。インストール時に `AGENTS.md` は作成・変更しません。

既存の `.gitignore` は、Gitで `.agents/project.local.json` が無視されるか確認します（Gitが必要です）。不足時は末尾への追記案を表示し、`--apply` を対話端末（TTY）で実行して `y` または `yes` と回答した場合だけ追記します。既存内容と権限は保持します。拒否・入力終了・非対話実行では、他の導入ファイルも含めて変更せず停止します。既に無視される場合は確認も重複追記もしません。下位の `.agents/.gitignore` の否定ルール等により追記後も無視されない場合は、追記と導入ファイルをロールバックして停止するため、ルールを手動で統合してください。`.env` 等の他の統合案は引き続き手動で確認・反映します。

`.gitignore` がない場合は、dry-runで `[追加] .gitignore` と表示し、`--apply` でマニフェストで指定された配布内容から作成します。dry-runは確認や書き込みを行わず、同一ファイルはそのまま保持され、再実行できます。

## Project設定をチームで共有する

`aide-init`は主作業ツリーで実行し、**共有する／共有しない**を選択します。未回答・未承認のまま保存先を決めません。

- **共有する**：Git管理対象の`.agents/project.json`へ保存。同じRepositoryをcloneしたメンバーは、自分のGitHub認証とProjectアクセス権で共有設定を照合して利用できます。未追跡の初期設定ファイルは、先に利用者がGit追跡対象にしてください。
- **共有しない**：`.agents/project.json`は中立値のまま、`.agents/project.local.json`（ignore対象・mode `0600`）へ保存。各cloneで初期化します。従来のローカル設定もそのまま利用できます。

共有するとPrivate Projectでも名前・URL・識別情報がRepositoryの読者やGit履歴へ公開されるため、保存前に公開範囲への個別承認を求めます。設定共有はProjectへのアクセス権を付与しません。非共有へ戻しても過去の履歴は消えません。共有・ローカルの両設定に実値がある場合は優先順位で選ばず停止します。切替は主側の`aide-init`で変更・削除内容を確認してから行い、stage・commit・pushは別途承認が必要です。

AIDE自身のテンプレート源泉は中立値を維持します。共有設定を持つ別Repositoryをfork／テンプレートとして再利用した場合は、origin不一致で停止し、承認なしに設定を中立化しません。詳しくは[Project設定の管理](.agents/docs/aide/03-project.md#project設定の管理)を参照してください。

## 詳細

- [開発ルール目次](.agents/docs/aide/00_index.md)
- [開発フローの実行順序と参照先](.agents/skills/aide-workflow/SKILL.md)
- [GitHub Project初期化の手順と安全境界](.agents/skills/aide-init/SKILL.md)
- [既存リポジトリ向け導入スクリプト](scripts/aide_install.py)
- [導入対象マニフェスト](scripts/aide_install_manifest.json)
