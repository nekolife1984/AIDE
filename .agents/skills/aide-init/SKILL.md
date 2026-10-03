---
name: aide-init
description: Use when initializing or reconciling a repository's GitHub Project using AIDE conventions.
---

# AIDE Init: GitHub Project

Project初期化の実処理は、同梱スクリプトを唯一の実行手順とする。対象RepositoryのIssue Projectを作成・再利用・リンクし、AIDE標準Statusを検証する。

## 実行

Repository rootで実行する。必要条件はGitHub CLI (`gh`) の認証と`project` scope。

```sh
python3 .agents/skills/aide-init/scripts/aide_init.py --dry-run
python3 .agents/skills/aide-init/scripts/aide_init.py
```

初回はdry-runの対象・変更計画を確認してから実行する。非対話実行では`--yes`で計画を承認する。新規ProjectをPublicにする場合は、追加で`--approve-public-project`が必要（例: `--yes --approve-public-project`）。Python標準ライブラリ以外の依存はない。

## 安全境界

- origin、GitHub Repository、`.agents/project.json`を照合する。設定済みキャッシュはProject一覧を再探索せず、保存されたProjectを直接読み戻す。
- 初回探索ではRepository直結Projectを再利用する。リンク済みが複数、閉鎖済み、候補が曖昧な場合は停止する。未リンクProjectは同名かつ空の場合だけ再利用し、共有・項目ありの候補、Issue無効、認証不足、破損/不一致キャッシュでは停止する。
- `--dry-run`はGitHub・キャッシュを書き換えない。書き込み前に計画を表示し、Project作成時は可視性を明示してからRepositoryへリンクする。
- Statusは必ず読み戻してから作成・更新する。GitHub既定の`Todo` / `In Progress` / `Done`はAIDE標準へ移行し、対応するoption IDを保持する。アーカイブ済み・未アーカイブ双方を含む項目数で空状態を判定する。
- 項目があるProjectでStatusが欠落または不一致の場合は既定で停止する。項目と影響を確認した場合だけ`--approve-status-update-with-items`で明示承認する。未知のoption、読み戻し失敗、想定外Repositoryは承認フラグでも変更しない。
- GitHubの状態を再取得・検証した後だけ`.agents/project.json`をatomic更新する。Tokenなど秘密情報や変動するfield/option IDは保存しない。

## テスト

```sh
python3 -m unittest discover -s .agents/skills/aide-init/tests -v
```

GitHub CLIをモックし、dry-run、初回探索、衝突、Status更新、失敗時のキャッシュ保護を検証する。
