# ブランチ戦略

## 方針

`main`のみを長期ブランチとし、変更ごとに短命ブランチを使う。

Gitの作業ツリー（working tree）はGitの仕組みを指す総称であり、clone直後を「主作業ツリー」、`git worktree add`で作成したものを「リンク作業ツリー」と呼ぶ。更新対象を指すときは主作業ツリーかリンク作業ツリーかを明示する。

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

## 作業場所の選択

作業ブランチは常に使い、作業場所は状況に応じて選ぶ。`main`への直接変更は禁止する。

- 主作業ツリー内の作業ブランチ：単独・逐次作業で、主作業ツリーがクリーンかつ他の作業に使われていない場合に選べる。
- リンク作業ツリー：複数Issue・エージェント・セッションで並行する場合、主作業ツリーの既存ブランチや変更を保護する場合、または作業環境を分離したい場合に選ぶ。
- 主作業ツリーに未コミット変更がある、他の作業が使用中、または判断に迷う場合はリンク作業ツリーを使う。

どちらの場所でも、最新の`origin/main`を作業ブランチの起点にする。主作業ツリー内で作業する場合は次のように開始する。

```bash
BRANCH_NAME="<branch-name>"
git fetch origin main
git switch -c "$BRANCH_NAME" origin/main
```

## リンク作業ツリー

Issueごとに異なる作業ブランチを1つ割り当て、ブランチをIssue間で共有しない。リンク作業ツリーは選択制とし、同じブランチを複数の場所でチェックアウトしない。

リンク作業ツリーを使う場合は、主作業ツリーで最新の`origin/main`を取得し、絶対パスを指定して作成する。

```bash
WORKTREE_PATH="/absolute/path/to/AIDE-<issue>"
BRANCH_NAME="<branch-name>"
git fetch origin main
git worktree add "$WORKTREE_PATH" -b "$BRANCH_NAME" origin/main
```

リンク作業ツリーを削除する前に、追跡対象外・Git無視対象も含めて状態を確認する。必要なファイルはリンク作業ツリーの外へ退避し、退避先で読み戻してから削除する。保持対象が残っている、または削除対象を特定できない場合は削除しない。

```bash
WORKTREE_PATH="/absolute/path/to/AIDE-<issue>"
git -C "$WORKTREE_PATH" status --short --ignored --untracked-files=all
# 出力を確認し、必要なファイルを退避・検証してから続ける
git worktree remove "$WORKTREE_PATH"
git worktree list
```

マージと完了確認後に作業ブランチを削除する。リンク作業ツリーを使った場合は、リンクの削除後にブランチを削除する。

リンク作業ツリーでは`.agents/project.json`と`.agents/project.local.json`を更新せず、ローカル設定がない場合も`aide-init`を実行しない。必要な初期化・更新は、リンク作業ツリーを作成する前に主作業ツリーで完了する。

主作業ツリー内の作業ブランチを使い終えたら、未コミット変更がないことを確認して`main`へ戻る。
