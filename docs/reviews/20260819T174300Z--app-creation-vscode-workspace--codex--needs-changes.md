---
review_id: REV-20260819T174300Z-app-creation-vscode-workspace
reviewer: codex
reviewed_author: claude-code
reviewed_target: uncommitted working tree diff (bin/triad-workspace new file; bin/triad-new, .claude/settings.json, docs/platform/operations.md, README.md changes)
baseline_sha: dea3d477a8873ce966f77338c78d9f9b470dd35a
verdict: needs-changes
reviewed_at: 2026-08-19T17:43:00Z
supersedes: null
---

## 対象範囲

新規アプリ作成時にデフォルト作成先を`~/workspace`とし、AIがプロジェクト名を提案し、triad-orchestratorと新規アプリの両方を含むVS Codeマルチルートワークスペースファイルを自動生成する機能。対象ファイル:

- `bin/triad-workspace`（新規）
- `bin/triad-new`（変更）
- `.claude/settings.json`（`permissions.allow`へ`Bash(code:*)`追加）
- `docs/platform/operations.md`, `README.md`（記述更新）

## 確認方法

`codex exec`（`--sandbox read-only`、`--skip-git-repo-check`、`--ephemeral`、`--ignore-user-config`）を用いて、リポジトリの未コミット差分（`git status --short` / `git diff`、ベースコミット`dea3d47`）を読み取り専用で確認した。ホスト環境に書き込み可能な一時ディレクトリがなく、Docker Composeへのソケットアクセス権もなかったため、テストの実行はできず、`git diff --check`のみ実行した。

## 指摘

- **Major** — [`bin/triad-workspace:111`](../../bin/triad-workspace#L111): 出力先の存在確認が`[[ -e "$WORKSPACE_FILE" ]]`のみであり、dangling symlinkを検出できない。`--force`指定時は通常のsymlinkも許容してしまうため、リポジトリ外のリンク先へ書き込み・上書きできる。symlinkを明示的に拒否し、同一ディレクトリ内で一時ファイルへ生成後に原子的に置換する方式にすべき。
- **Major** — [`.claude/settings.json:49`](../../.claude/settings.json#L49): `Bash(code:*)`は、ワークスペースを開く操作だけでなく、拡張機能のインストール・削除など`code` CLIの状態変更操作全般を確認なしで許可してしまう。読み取り専用または副作用が限定的な操作に限るという本リポジトリの許可方針に対して範囲が広すぎる。検証済みの`*.code-workspace`だけを開く専用ラッパーを用意し、そのラッパーだけを許可リストへ追加する設計にすべき。
- **Minor** — [`bin/triad-workspace:85`](../../bin/triad-workspace#L85): `--app-dir`と`--platform-dir`が「存在するディレクトリ」であることしか検証されていない。`--platform-dir`が`--app-dir`の内側にある逆方向の包含関係が未検証。
- **Minor** — [`README.md:81`](../../README.md#L81): 「作成時に…自動生成され、`code`コマンドで開き直されます」という記述は、`triad-new`自身が生成と再オープンの両方を行うようにも読める。実装どおり「`triad-new`が生成し、その完了後にチャット担当AIが別ステップで`code`を実行する」と明記すべき（`operations.md`の記述は分離を正しく表している）。
- **Minor** — 新規スクリプトおよび新しい生成物に対するテストが存在しない。`tests/test_bootstrap.py`はワークスペースファイルの内容・相対パス・`.gitignore`除外・既存ファイル/symlink時の挙動・`--force`・`code`非搭載環境での`triad-new`成功を検証していない。`bin/triad-workspace`単体のテストも必要。

## 確認済みの良い点

- `bin/triad-new`は`code`を一切呼ばず、開き直しコマンドを表示するだけなので、VS Code CLIが存在しないテストコンテナ（`docker compose run --rm test`）を直接壊す設計にはなっていない。
- `*.code-workspace`は初回コミット前に`.gitignore`へ追加され、ワークスペースファイルの生成はコミット後に行われるため、作成直後のGit状態を汚さない。

## 残存リスク

- 上記Major 2件が未修正の間は、(1)`--app-dir`にsymlinkを含む既存ディレクトリを指定した場合の書き込み先侵害、(2)`code`コマンドの全許可による意図しない拡張機能操作、のリスクが残る。

## 判定

**needs_changes** — Major指摘2件が未解決のため。Minor指摘3件は次回レビューまでに解消することが望ましい。
