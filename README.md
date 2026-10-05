# AIDE

AIコーディングエージェントと開発するリポジトリ向けのGitHubテンプレートです。`AGENTS.md`から開発ルールを案内し、Issue・Project運用、品質、セキュリティの資料と、GitHub Project初期化スキルを提供します。

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

AIDEをGitHubテンプレートとして新規作成せず、既存リポジトリにAIDEの共通運用資料を追加する場合は、AIDEのclone内からPython 3.9以降で導入スクリプトを実行します。コピー対象は [`scripts/aide_install_manifest.json`](scripts/aide_install_manifest.json) に限定されています。`.agents/project.json`、認証情報、ローカルキャッシュは含まれません。

まずdry-runで追加・同一・競合と、既存 `AGENTS.md` / `.gitignore` に対する統合案を確認します。dry-runはファイルを変更しません。

```sh
python3 /path/to/AIDE/scripts/aide_install.py /path/to/existing-repository --dry-run
```

計画に問題がなければ、明示的に `--apply` を付けて適用します。

```sh
python3 /path/to/AIDE/scripts/aide_install.py /path/to/existing-repository --apply
```

Node.js 18以降とPython 3.9以降がある場合は、AIDEのcloneなしで `npx` から実行できます。最初にdry-runを確認し、適用する場合は `--apply` を付けます。

```sh
npx --yes --package=github:nekolife1984/AIDE aide-install /path/to/existing-repository --dry-run
npx --yes --package=github:nekolife1984/AIDE aide-install /path/to/existing-repository --apply
```

この方法では公開GitHubリポジトリからCLIを取得します。ネットワーク接続が必要です。

通常のコピー対象に競合が1件でもあれば、全件を表示して書き込みを停止します。競合ファイルを手動で退避または統合してから再実行してください。既存の `AGENTS.md` と `.gitignore` は変更せず、内容が異なる場合は出力された統合案を手動で取り込んでください。同一ファイルはそのまま保持され、再実行できます。

## 詳細

- [開発ルール目次](.agents/docs/00_index.md)
- [GitHub Project初期化の手順と安全境界](.agents/skills/aide-init/SKILL.md)
- [既存リポジトリ向け導入スクリプト](scripts/aide_install.py)
- [導入対象マニフェスト](scripts/aide_install_manifest.json)
