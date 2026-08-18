# CLI機能の基準情報 — 2026-08-19

この文書は、アダプター設計に使用した公式情報と、ローカル環境で確認したバージョンを記録する。実行時には、各バージョン固有のコマンドヘルプを正とする。

| CLI | ローカルバージョン | 個人契約による認証 | 非対話実行の入口 | 構造化出力・安全制御 |
|---|---:|---|---|---|
| Codex | `codex-cli 0.147.0` | ChatGPT契約で`codex login` | `codex exec` | JSONL、出力スキーマ、最終メッセージファイル、読み取り専用／ワークスペースサンドボックス、一時セッション |
| Claude Code | `2.1.233` | Claude.ai Proログイン | `claude -p` | JSON／ストリームJSON、JSON Schema、計画／編集許可モード、最大ターン数、セッション非保存 |
| Antigravity | `agy 1.1.14` | Google OAuth | `agy -p` | JSON／ストリームJSON、JSON Schema、計画モード、サンドボックス、出力タイムアウト |

2026-08-17時点の記録からの差分：Codexは`0.148.0-alpha.9`（アルファ）から安定版`0.147.0`系へ変わり、Antigravityは`1.1.13`から`1.1.14`へ更新された。

`codex exec --help`を再確認したところ、ワークスペース書き込みフェーズで使用していた`--ask-for-approval untrusted`は現行バージョンに存在せず（`error: unexpected argument`で即座に失敗する）、`--approve-for-me`（「workspace-writeサンドボックスを使って承認要求を自動レビューする」の意）に置き換わっていた。さらに`--approve-for-me`は明示的な`--sandbox <MODE>`と併用できない（`--approve-for-me`自体がworkspace-writeサンドボックスを意味するため）。`triad/adapters.py`の`run_workspace`のCodexコマンドから`--sandbox workspace-write`を削除し、`--ask-for-approval untrusted`を`--approve-for-me`へ置き換えた。読み取り専用フェーズの`--sandbox read-only`・`--output-schema`・`--output-last-message`・`--skip-git-repo-check`と、Claude/Antigravityの非対話フラグ（`--permission-mode`、`--output-format`、`--json-schema`、`--no-session-persistence`、`--allowedTools`、`agy`の`--mode`／`--sandbox`／`--print-timeout`）は現行バージョンでも変更なく動作することを確認した。

Claude Codeの`--max-turns`は現行の`--help`出力に見当たらないが、実行時に引数エラーにはならず、指定した通りの結果を返した（未対応フラグとしてエラーにはしないが、`--help`が更新されていない可能性がある）。挙動が不確実なため、`adapters.py`では従来通り保持しつつ、今後`--help`に再掲載されるか、または明確な代替フラグが判明した時点で見直す。

公式参照先：

- OpenAIの認証：<https://learn.chatgpt.com/docs/auth>
- OpenAI Codexの開発者向けコマンド／`codex exec`：<https://learn.chatgpt.com/docs/developer-commands?surface=cli>
- OpenAI Codexのサンドボックスと承認の安全性：<https://learn.chatgpt.com/docs/agent-approvals-security>
- Anthropic Claude CodeのCLIリファレンス：<https://code.claude.com/docs/en/cli-usage>
- Google Antigravity CLIのインストールと自動化コードラボ：<https://codelabs.developers.google.com/antigravity-cli-hands-on>
- Google Antigravityの仕様駆動CLIコードラボ：<https://codelabs.developers.google.com/sdd-agy-cli>

この環境では、Google公式インストーラー`https://antigravity.google/cli/install.sh`を使用した。実行前にダウンロードして内容を確認済みであり、インストーラーはリリースデータをSHA-512で検証する。インストール先の実行ファイルは`/home/th/.local/bin/agy`である。
