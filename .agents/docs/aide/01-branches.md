# ブランチ戦略

## 方針

`main`のみを長期ブランチとし、変更ごとに短命ブランチを使う。

Gitの作業ツリーは、clone直後の「主作業ツリー」と、`git worktree add`で作成した「リンク作業ツリー（linked worktree）」を区別して扱う。

## 命名

| 用途 | 形式 |
|---|---|
| 機能・テンプレート | `feat/<内容>` |
| 不具合修正 | `fix/<内容>` |
| 文書 | `docs/<内容>` |
| 保守・CI | `chore/<内容>` |

## フロー

`main` → 作業ブランチ → 実装・検証 → PR → Squash Merge → ブランチ削除

`main`への直接pushは禁止。

## リンク作業ツリー

Issue単位の並行開発では、1 Issueにつき1ブランチと1リンク作業ツリーを割り当てる。同じブランチを複数のリンク作業ツリーでチェックアウトしない。

主作業ツリーで最新の`origin/main`を取得し、絶対パスを指定して作成する。

```bash
git fetch origin main
git worktree add /absolute/path/to/AIDE-<issue> -b <branch-name> origin/main
```

作業はリンク作業ツリー内で行い、完了後は未コミット変更がないことを確認してから、主作業ツリーで削除する。

```bash
git -C /absolute/path/to/AIDE-<issue> status --short
git worktree remove /absolute/path/to/AIDE-<issue>
git worktree list
```

リンク作業ツリーでは`.agents/project.json`を更新せず、`project.json`が未設定（`project`が`null`）の場合も`aide-init`を実行しない。必要な初期化は、リンク作業ツリーを作成する前に主作業ツリーで完了する。
