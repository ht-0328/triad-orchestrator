# adapters.py — AI CLIサブプロセスの起動

[← 設計書トップ](index.md)

`triad/adapters.py`の`Adapter`クラスは、Codex・Claude Code・Antigravity（`agy`）という3つの異質なCLIを、共通の`RunResult`型に正規化して呼び出す層である。エージェント固有のコマンドライン引数の組み立て、構造化出力の抽出・検証、サブプロセスのタイムアウト制御、環境変数のスクラビングをすべてここに集約する。`Runner`は「artifact系か workspace系か」だけを意識すればよく、各CLIの引数の違いを知る必要はない。

## クラス図

```mermaid
classDiagram
    class Adapter {
        +Path project_root
        +Path schema_path
        +dict schema
        +available(agent) bool
        +run_artifact(agent, prompt, cwd, timeout) RunResult
        +run_workspace(agent, prompt, cwd, timeout) RunResult
        -_run(agent, command, prompt, cwd, timeout) RunResult
        -_extract_payload(raw)$ dict|None
        -_valid_payload(value)$ bool
        +audit_record(task_id, phase, result, prompt)$ dict
        -_executable(agent)$ str
    }
    class RunResult {
        <<frozen dataclass>>
        +str agent
        +int exit_code
        +float duration_seconds
        +dict|None payload
        +str stdout
        +str stderr
        +bool timed_out
    }
    Adapter ..> RunResult : creates
```

`Adapter(project_root)`は`contracts/agent-output.schema.json`を読み込んでおき、`run_artifact`が各CLIへスキーマそのもの（またはインライン化したJSON）を渡す。

## run_artifact()（読み取り専用・構造化出力）

3エージェントでコマンド構築が完全に異なる。

```mermaid
sequenceDiagram
    participant Runner
    participant Adapter
    participant CLI as CLIサブプロセス

    Runner->>Adapter: run_artifact(agent, prompt, cwd, timeout)
    alt agent == codex
        Adapter->>CLI: codex exec --ephemeral --ignore-user-config<br/>--sandbox read-only --skip-git-repo-check<br/>--output-schema <schema> --output-last-message <tmpfile> <prompt>
        Note over Adapter,CLI: 出力は--output-last-messageで指定した一時ファイルから読む
    else agent == claude
        Adapter->>CLI: claude -p --permission-mode plan<br/>--output-format json --json-schema <inline-json><br/>--max-turns 12 --no-session-persistence <prompt>
        Note over Adapter,CLI: 出力はstdoutのJSONから読む
    else agent == antigravity
        Adapter->>CLI: agy -p --mode plan --sandbox<br/>--disable-slash-commands --output-format json<br/>--json-schema <schema-path> --print-timeout <timeout>s <prompt>
        Note over Adapter,CLI: 出力はstdoutのJSONから読む
    end
    CLI-->>Adapter: 終了コード・stdout・stderr
    Adapter->>Adapter: _extract_payload()でJSONを抽出<br/>（直接JSON／JSONL末尾行／structured_output等のネスト）
    Adapter->>Adapter: _valid_payload()でスキーマ整合を検証
    Adapter-->>Runner: RunResult(payload=検証済みdict または None)
```

`_extract_payload()`は生出力が素直なJSONでない場合に備え、JSONLの末尾行を試したり、`structured_output`/`result`/`output`/`message`といったキーの中にネストされたJSON（文字列またはオブジェクト）を再帰的に探索する。最終的に`{summary, content, verdict, human_decisions}`の4キーが過不足なく揃い、型が正しいものだけを有効なペイロードとして返す（`_valid_payload`）。

## run_workspace()（ワークスペース書き込み）

Antigravityは書き込み権限を持たない（`PolicyError`で拒否）。

```mermaid
flowchart LR
    A["run_workspace(agent, prompt, cwd, timeout)"] --> B{agent}
    B -- claude --> C["claude -p --permission-mode acceptEdits<br/>--output-format json --max-turns 40<br/>--no-session-persistence<br/>--allowedTools Read Edit Write Glob Grep<br/>'Bash(git diff:*)' 'Bash(git status:*)' &lt;prompt&gt;"]
    B -- codex --> D["codex exec --ephemeral --ignore-user-config<br/>--approve-for-me<br/>-c sandbox_workspace_write.network_access=false<br/>-c web_search=disabled &lt;prompt&gt;"]
    B -- antigravity --> E["PolicyError:<br/>AIにはソースを変更する権限がありません"]
```

Codexの`--approve-for-me`はそれ自体が`workspace-write`サンドボックスを意味し、明示的な`--sandbox`と併用すると引数エラーになる（`docs/research/cli-capabilities.md`に記録した2026-08-19時点のCLI仕様変更に対応済み）。

## _run()（共通サブプロセス実行）

```mermaid
flowchart TD
    A["_run(agent, command, prompt, cwd, timeout)"] --> B{"available(agent)?<br/>(shutil.which)"}
    B -- いいえ --> ErrMissing["PolicyError: CLIがインストールされていません"]
    B -- はい --> C["Popen(command, cwd=cwd,<br/>env=scrub_environment(),<br/>start_new_session=True)"]
    C --> D["process.communicate(timeout=timeout)"]
    D --> E{"TimeoutExpired?"}
    E -- はい --> F["os.killpg(pid, SIGTERM)"]
    F --> G["communicate(timeout=10)"]
    G --> H{"再度TimeoutExpired?"}
    H -- はい --> I["os.killpg(pid, SIGKILL)"]
    H -- いいえ --> J["終了"]
    I --> J
    E -- いいえ --> J
    J --> K["stdout/stderrを先頭2MB(MAX_CAPTURE_BYTES)で切り詰め"]
    K --> L["RunResult(agent, exit_code, duration_seconds,<br/>payload=None, stdout, stderr, timed_out)を返す"]
```

`scrub_environment()`は`OPENAI_API_KEY`等の機密環境変数と`AWS_`等のプレフィックス一致変数を除去したうえで、`TRIAD_AGENT_RUN=1`を必ず設定する。これはCLI側（`triad/cli.py`）が「AI子プロセスからは承認・差し戻し・判断回答を実行できない」ことを判定する唯一の目印である。

## audit_record()

`Runner`は`run_artifact`/`run_workspace`の呼び出しごとに`Adapter.audit_record(task_id, phase, result, prompt)`を呼び、`Store.append_audit()`経由で`.ai-dev/audit/cli-calls.jsonl`へ1行追記する。記録するのは`prompt_sha256`（プロンプト本文のハッシュのみ）・`agent`・`exit_code`・`duration_seconds`・`timed_out`・`structured_output`（payloadの有無）で、プロンプト本文や出力そのものは保存しない。

## 関連ファイル

- 実装: [`triad/adapters.py`](../../../triad/adapters.py)
- 契約: [`contracts/agent-output.schema.json`](../../../contracts/agent-output.schema.json)
- 利用元: [`runner.py`](runner.md)
- 依存: [`policy.py`](policy.md)（`scrub_environment`/`sha256_bytes`）
- 関連調査: [`docs/research/cli-capabilities.md`](../../research/cli-capabilities.md)（3 CLIのバージョン・フラグの一次調査記録）
