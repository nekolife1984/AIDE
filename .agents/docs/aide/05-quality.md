# 完了条件・検証基準

## 完了条件

- Issueごとに、成果を客観的に判定できる条件を記載する。
- 「改善する」「対応する」など曖昧な表現を避け、期待結果を明記する。
- 各条件に対応する確認方法を定める。

## 検証

- 変更内容に応じて、テスト・Lint・型チェック・ビルド・手動確認など必要な検証を選ぶ。
- 振る舞いを変えるコードには、その変更を確認するテストを追加・更新する。
- PRには実行した検証と実際の結果を記録する。手動確認は操作と観察結果を記載する。
- 未実施・失敗した検証は成功扱いにしない。理由と残作業を記録し、未解決の完了条件があればIssueを完了にしない。
- 設定済みの必須CIがある場合は、その成功を確認する。CIがない場合は、関連するローカル検証を実行する。

用語機械検証の許可リストは、[ブランチ戦略](01-branches.md)の現行定義行を唯一の正とする。定義行を変更する場合は、同一PR内で許可リストも再凍結する。用語の検出と失敗判定は既存の機械検証が担い、この説明はその代替ではない。

## AIDEのCI

このCIはAIDEテンプレート自体の検証専用で、生成先リポジトリではジョブを実行しない。チェッカーの単体テストはCPython 3.11〜3.14、全Markdown文書の走査はCPython 3.14で実行する。ローカルでは次を実行する。

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pip install --requirement requirements-ci.txt
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s scripts/tests -v
PYTHONDONTWRITEBYTECODE=1 python scripts/check_markdown_links.py
```

Markdown検証は`requirements-ci.txt`で固定したGitHub Flavored Markdownパーサーを使う。

`.github/workflows/ci.yml`はAIDEリポジトリの`main`向けPull Requestと`main`へのpushで検証を実行する。GitHubテンプレートから生成されたリポジトリへWorkflowファイルが複製されても、AIDE以外では全ジョブをスキップする。パスフィルターで必須チェックが未実行にならないよう、AIDE内の検証は毎回実行する。

## 完了判定

完了条件をすべて検証し、必要な変更をマージした後にIssueを閉じ、Projectを `Done` にする。
