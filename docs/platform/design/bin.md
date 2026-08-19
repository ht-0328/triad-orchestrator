# bin/ — 起動スクリプト

[← 設計書トップ](index.md)

`bin/`配下には2つのBashスクリプトがある。`bin/triad`は既存タスクを操作する薄いシムで、`bin/triad-new`は新しいアプリケーションのGitリポジトリと最初のタスクを一括で作る唯一の場所である。いずれも通常は人間が直接打つのではなく、VS Codeのチャット担当AIが内部で実行する。

## bin/triad（3行のシム）

```bash
export PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m triad "$@"
```

`PYTHONPATH`にプラットフォームのルート（`triad-orchestrator/`）を追加したうえで、受け取った引数をそのまま`python3 -m triad`（実体は[`triad/cli.py`](cli.md)の`main()`）へ`exec`で引き渡す。プロセスを置き換える`exec`なので、シェル自体は残らない。

## bin/triad-new のフロー

```mermaid
flowchart TD
    A["引数解析<br/>(--parent/--name/--task-id/--title/\n--implementation-author/--no-plan/-y)"] --> B["前提コマンド確認<br/>(git, python3, 必要ならcodex)"]
    B --> C{"対話端末か？<br/>[[ -t 0 ]]"}
    C -- はい --> D["不足項目を対話プロンプトで補完<br/>(親ディレクトリ・プロジェクト名・\nタスクID・タイトル・実装AI)"]
    C -- いいえ --> E{"--name と --yes が<br/>両方指定されているか？"}
    E -- いいえ --> ErrNonInteractive["エラー終了<br/>（非対話には--name --yesが必須）"]
    E -- はい --> F["デフォルト値で確定"]
    D --> G["入力値の形式検証<br/>(プロジェクト名/タスクID正規表現、\nimplementation-authorがclaude/codex、\nタイトル非空・改行なし)"]
    F --> G
    G --> H["作成先パスを解決<br/>(triad-orchestrator自身の中は拒否、\n書き込み権限確認、既存パス拒否)"]
    H --> I["git config user.name/user.email<br/>（環境変数優先）を確認"]
    I --> J{"-y / --yes か？"}
    J -- いいえ --> K["作成内容を提示し y/N 確認"]
    J -- はい --> L["確認省略"]
    K --> L
    L --> M["mkdir + git init -b main<br/>README.md / .gitignore を生成"]
    M --> N["./bin/triad init &lt;task_id&gt; --title ... --brief ...<br/>--implementation-author ... --repo &lt;target&gt;<br/>を呼び出す"]
    N --> O["git add README.md .gitignore<br/>AGENTS.md CLAUDE.md GEMINI.md .ai-dev<br/>→ git commit (chore: プロジェクトを初期化)"]
    O --> P{"--no-plan か？"}
    P -- はい --> Q["state=INTAKEのまま終了<br/>チャット担当AIが後で'triad run'すべき<br/>コマンドを画面へ表示"]
    P -- いいえ --> R["./bin/triad run &lt;task_id&gt; --repo &lt;target&gt;<br/>（Codexが調査・協議ブリーフを作成）"]
    R --> S{"成功したか？"}
    S -- いいえ --> ErrRun["エラー終了<br/>（プロジェクトは作成済み、再試行コマンドを提示）"]
    S -- はい --> T["./bin/triad advance &lt;task_id&gt; --outcome success<br/>--repo &lt;target&gt;<br/>(state: INTAKE → RESEARCH)"]
    T --> U["git add .ai-dev<br/>→ git commit (docs(ai-dev): 調査ブリーフを作成)"]
    U --> V["完了メッセージと、チャット担当AIが<br/>次に実行すべき'triad status --json'コマンドを表示"]
```

## bin/triad-new が bin/triad を複数回呼び出すシーケンス

`--no-plan`を指定しない既定経路では、`bin/triad-new`自身が`bin/triad`を2回呼び出し、その間に`state.json`が2段階で進む。

```mermaid
sequenceDiagram
    participant New as bin/triad-new
    participant Triad as bin/triad<br/>(= python -m triad)
    participant Store
    participant Codex as codex CLI

    New->>Triad: init <task_id> --title ... --implementation-author ...
    Triad->>Store: initialize()
    Store-->>Triad: state.json 作成（state=INTAKE）
    New->>New: git add + git commit（プロジェクト初期化）

    New->>Triad: run <task_id>
    Triad->>Store: load_state() → state=INTAKE
    Triad->>Codex: Runner経由でINTAKEフェーズを実行
    Codex-->>Triad: input/intake.md を生成
    New->>Triad: advance <task_id> --outcome success
    Triad->>Store: advance() → state=RESEARCH
    New->>New: git add .ai-dev + git commit（調査ブリーフ作成）

    Note over New,Store: 以降はチャット担当AIが status/run/advance を<br/>繰り返し呼び出して3AI協議を進める（bin/triad-newの役目はここで終わる）
```

## 関連ファイル

- 実装: [`bin/triad`](../../../bin/triad)、[`bin/triad-new`](../../../bin/triad-new)
- 呼び出し先: [`cli.py`](cli.md)（`init`/`run`/`advance`サブコマンド）
- テスト: [`tests/test_bootstrap.py`](../../../tests/test_bootstrap.py)（`bin/triad-new`のサブプロセスレベルテスト、`tests/fixtures/bin/codex`の偽実行ファイルでCodex呼び出しをスタブ化）
