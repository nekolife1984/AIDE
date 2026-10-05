# AIDE

AIコーディングエージェントと開発するリポジトリ向けのGitHubテンプレートです。`AGENTS.md`から開発ルールを案内し、Issue・Project運用、品質、セキュリティの資料と、GitHub Project初期化スキルを提供します。

## 初めて使う

### 必要なもの

- Git
- GitHub CLI（`gh`）と、対象リポジトリ・Projectを操作できるGitHubアカウント

GitHubで **Use this template** を選んで自分のリポジトリを作成し、`<OWNER>`と`<REPOSITORY>`を置き換えてcloneします。

```sh
git clone https://github.com/<OWNER>/<REPOSITORY>.git
cd <REPOSITORY>
```

### GitHub認証とProjectの初期化

未認証の場合は、ブラウザーでGitHubにログインし、Project操作に必要な`project` scopeを付けます。

```sh
gh auth login --web --hostname github.com --scopes project
```

認証後は`gh auth status`でscopeを確認します。すでに認証済みで`project`が不足している場合だけ、追加します。

```sh
gh auth status
```

```sh
gh auth refresh -h github.com -s project
```

詳細：[初回認証（`gh auth login`）](https://cli.github.com/manual/gh_auth_login)、[認証状態の確認（`gh auth status`）](https://cli.github.com/manual/gh_auth_status)、[scopeの追加（`gh auth refresh`）](https://cli.github.com/manual/gh_auth_refresh)、[GitHub CLIのProject操作と必要なscope](https://cli.github.com/manual/gh_project)。

AIエージェントにGitHub Projectの初期化を依頼し、提示された変更計画を確認してから承認します。候補や状態が曖昧な場合は変更せず停止します。手順と安全境界は下記スキルを参照してください。

## 詳細

- [開発ルール目次](.agents/docs/00_index.md)
- [GitHub Project初期化の手順と安全境界](.agents/skills/aide-init/SKILL.md)
