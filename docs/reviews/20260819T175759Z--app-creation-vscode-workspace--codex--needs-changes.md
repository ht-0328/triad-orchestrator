---
review_id: REV-20260819T175759Z-app-creation-vscode-workspace
reviewer: codex
reviewed_author: claude-code
reviewed_target: uncommitted working tree diff (fixes for REV-20260819T174300Z-app-creation-vscode-workspace)
baseline_sha: dea3d477a8873ce966f77338c78d9f9b470dd35a
verdict: needs-changes
reviewed_at: 2026-08-19T17:57:59Z
supersedes: REV-20260819T174300Z-app-creation-vscode-workspace
---

## 対象範囲

前回レビュー(`REV-20260819T174300Z-app-creation-vscode-workspace`、判定needs_changes)で指摘したMajor 2件・Minor 3件に対するClaude Codeの修正。

## 確認方法

`codex exec`（`--sandbox read-only`、`--skip-git-repo-check`、`--ephemeral`、`--ignore-user-config`）で`git status --short`と`git diff`を再確認し、Claude Codeが主張する各修正が実際の差分に反映されているかを検証した。

## 指摘

- **Major** — [`bin/triad-new:217`](../../bin/triad-new#L217), [`README.md:81`](../../README.md#L81), [`docs/platform/operations.md:19`](../../docs/platform/operations.md#L19): `.claude/settings.json`が許可するのは`bin/triad-open-workspace`だけになったが、生成後の案内表示（`triad-new`が印字するコマンド）とドキュメントは依然として直接`code <file>`を実行するよう記述していた。許可設定と実際の運用導線が一致しておらず、前回Major 2への対応として新設したラッパーが通常の使用経路で使われない状態だった。
- **Minor** — [`tests/test_workspace.py:89`](../../tests/test_workspace.py#L89): symlinkの回帰テストが、リンク先が実在する通常のsymlinkのケースのみで、前回指摘の直接原因だったdangling symlink（リンク先が存在しない）のケースを含んでいなかった。

## 確認済みの良い点

- 出力先symlinkは`--force`の有無にかかわらず拒否される。
- 一時ファイルは出力先と同じディレクトリに作られ、`mv`は宛先symlinkのリンク先を追従せずディレクトリエントリを置換する。
- 双方向のディレクトリ包含チェックが追加されている。
- READMEは`triad-new`による生成と、チャット担当AIによる後続のオープンを明確に分離している。
- `Bash(code:*)`は削除され、限定ラッパー`bin/triad-open-workspace`のみが許可されている。
- ラッパーは引数を1個に限定し、拡張子・直接のsymlink・実ファイル・有効なJSONを検証してから`code -- "$TARGET"`を実行するため、CLIオプション注入を防止している。
- 新規単体テスト（`tests/test_workspace.py`）とbootstrap統合テストの追加が確認できる。

## 残存リスク

- Major指摘（案内・文書とラッパーの不整合）が未解決の間は、実際にはラッパー経由でしか`code`を起動できないにもかかわらず、人間・チャット担当AIが誤って直接`code`コマンドを実行しようとする可能性がある。

## 判定

**needs_changes** — Major指摘1件が未解決のため。
