# cli.py — コマンドラインの入口

[← 設計書トップ](index.md)

`triad/cli.py`は`python -m triad`（`bin/triad`経由）で起動される`argparse`ベースのCLIで、9個のサブコマンドを1つの`main()`から振り分ける。VS Codeのチャット担当AI（Codex拡張・Claude Code拡張）がこのCLIを内部で呼び出し、人間には結果だけを提示する——人間が直接このコマンドを打つことは想定していない。承認・差し戻し・判断回答の「人間の明示意思をどう確認するか」というTriad最大の信頼境界も、このファイルに集約されている。

## サブコマンド・ディスパッチ

```mermaid
flowchart TD
    Start["main(argv)"] --> Parse["parser().parse_args(argv)"]
    Parse --> Try["try:"]
    Try --> Cmd{"args.command"}
    Cmd -- doctor --> Doctor["doctor(json)<br/>Storeを作らず単独で実行"]
    Cmd -- init --> Init["store.initialize()<br/>タスクディレクトリ・state.json・<br/>ガイダンス・チャット操作規約を作成"]
    Cmd -- status --> Status["status(store, task_id, json, answer)"]
    Cmd -- run --> Run["Runner(store, PLATFORM_ROOT).run(task_id)"]
    Cmd -- advance --> Advance["verify_review_outcome →<br/>(必要なら redo_confirmation) →<br/>store.advance()"]
    Cmd -- approve --> Approve["human_confirmation →<br/>store.approve()"]
    Cmd -- request-change --> ReqChange["store.request_change()"]
    Cmd -- approve-change --> AppChange["human_confirmation(gate=change) →<br/>store.approve_change()"]
    Cmd -- decide --> Decide["decision_confirmation →<br/>store.resolve_decisions()"]
    Doctor & Init & Status & Run & Advance & Approve & ReqChange & AppChange & Decide --> Return["終了コード0・結果をstdoutへ"]
    Try -.捕捉.-> Except["except (PolicyError, ValueError,<br/>subprocess.SubprocessError):<br/>stderrへ'エラー: ...'、終了コード2"]
```

`doctor`以外の全コマンドはまず`Store(args.repo)`を構築する（`git rev-parse --show-toplevel`が失敗すればここで`PolicyError`）。

## status の human_actions 生成

`status --json`が返す`human_actions`配列が、チャット担当AIに「今、人間から何を引き出せばよいか」を伝える唯一の情報源である。

```mermaid
flowchart TD
    A["human_actions_for(store, task_id, current, decision_answers)"] --> B{"current が<br/>HUMAN_GATESの<br/>待機状態と一致？"}
    B -- はい --> C["approve用confirmationと<br/>request_revision用confirmationの<br/>2アクションを追加"]
    B -- いいえ --> D{"current == CHANGE_REQUEST?"}
    D -- はい --> E["approve_change用confirmationを追加"]
    D -- いいえ --> F["（次へ）"]
    C --> F
    E --> F
    F --> G{"pending_decisionsが<br/>存在するか？"}
    G -- はい --> H{"--answerが<br/>渡されているか？"}
    H -- はい --> I["回答束縛済みconfirmationを含む<br/>decideアクションを追加"]
    H -- いいえ --> J["answers_requiredだけを含む<br/>decideアクションを追加<br/>（confirmationはまだ計算しない）"]
    G -- いいえ --> K["終了"]
    I --> K
    J --> K
```

## 確認句（confirmation phrase）の2段階プロトコル

`approve`・`advance --outcome needs_changes`（差し戻し・作り直し時のみ）・`approve-change`・`decide`の4コマンドが対象。

```mermaid
sequenceDiagram
    participant Human as 人間
    participant Chat as VS Codeチャット担当AI
    participant CLI as triad CLI (cli.py)
    participant Files as Git追跡ファイル<br/>(対象成果物)

    Chat->>CLI: triad status <task_id> --json
    CLI->>Files: GATE_TARGETS等のSHA-256を計算
    CLI-->>Chat: human_actions[].confirmation =<br/>"approve TASK-1 plan a1b2c3d4e5f60718"<br/>(chat_confirmation_phrase = verb+task+subject+SHA256先頭16桁)
    Chat->>Human: 対象成果物・変更点・影響を提示して停止
    Human->>Chat: 「承認」等、対象操作を明示するメッセージ
    Chat->>CLI: triad approve <task_id> plan<br/>--human-confirmation "approve TASK-1 plan a1b2c3d4e5f60718"
    CLI->>Files: 現在のSHA-256から期待される確認句を再計算
    alt 一致する
        CLI->>CLI: confirmation_channel = "vscode-chat"
        CLI->>Files: store.approve() で承認レコード＋状態遷移
        CLI-->>Chat: 新しい状態
    else 一致しない（対象が変化・改ざん・古い句）
        CLI-->>Chat: PolicyError: 確認句が一致しません
    end
```

`--human-confirmation`を省略した場合は対話端末経路にフォールバックし、`sys.stdin/stdout`がTTYであることを要求したうえで平文フレーズ（例:`"approve TASK-1 plan"`）の入力を求める（`confirmation_channel = "terminal"`）。いずれの経路でも、呼び出し元プロセスの環境変数`TRIAD_AGENT_RUN`が`"1"`（`policy.scrub_environment()`がAI子プロセスに必ず設定する値）であれば即座に`PolicyError`で拒否し、AIが自分自身の判断で承認・差し戻し・判断回答を行うことを構造的に防ぐ。

`decide`用の`decision_confirmation()`は、確認句の元になるハッシュ対象に人間の回答文字列そのもの（正規化JSON）も含める。そのため異なる回答を渡すと確認句が一致せず、提示された回答と異なる内容を記録することはできない。

`advance`はさらにスコープを制限する。`--human-confirmation`は`REVISION_GATES`に該当する状態（`AWAITING_PLAN_APPROVAL`/`AWAITING_DELIVERY_APPROVAL`）で`--outcome needs_changes`を指定する場合にだけ許可され、それ以外（通常のフェーズ進行）で指定すると`PolicyError`になる。

## verify_review_outcome()

`advance`実行前に、現在の状態が`review=True`のフェーズであれば、対応するレビュー成果物のYAMLフロントマターにある`verdict:`行を読み、CLIに渡された`--outcome`と一致するかを検証する。一致しなければ`PolicyError`で停止する——レビューAIが成果物本文に書いた判定と、状態遷移に使われる結果が食い違うことを防ぐダブルチェックである。

## サブコマンド一覧

| コマンド | 目的 | 主な引数 |
|---|---|---|
| `doctor` | CLI・認証・Git・Dockerの利用可否を確認 | `--json` |
| `init` | タスクを作成 | `task_id`, `--title`, `--brief`, `--implementation-author` |
| `status` | 状態と次の担当・確認句を表示 | `task_id`, `--json`, `--answer`(複数可) |
| `run` | 現在フェーズのAI/Docker処理を実行 | `task_id` |
| `advance` | 証跡検証のうえ状態遷移 | `task_id`, `--outcome`, `--reason`, `--human-confirmation` |
| `approve` | 承認ゲートを承認 | `task_id`, `gate`(`plan`\|`delivery`), `--human-confirmation` |
| `request-change` | 承認済み計画への変更要求を作成 | `task_id`, `--reason`, `--actor` |
| `approve-change` | 変更要求を承認 | `task_id`, `--human-confirmation` |
| `decide` | 判断待ちへ回答 | `task_id`, `--answer`(複数可・必須), `--human-confirmation` |

運用視点での使い分けは[運用手順](../operations.md#チャット担当aiが使うcli)を参照。

## 関連ファイル

- 実装: [`triad/cli.py`](../../../triad/cli.py)
- 依存: [`model.py`](model.md)、[`store.py`](store.md)、[`runner.py`](runner.md)、[`policy.py`](policy.md)
