# 実装設計書 — triad/ パッケージ

この文書群は、[アーキテクチャ](../architecture.md)が説明する「なぜこう設計したか」を踏まえたうえで、`triad/`パッケージと`bin/`スクリプトが「実際にどう実装されているか」をモジュール単位のクラス図・フローチャート・シーケンス図で示す実装設計書である。対象はこのオーケストレーター基盤自身のコードであり、個々のアプリケーション実装ではない。

## モジュール一覧

| ファイル | 責務 | 詳細 |
|---|---|---|
| `triad/model.py` | 状態機械の定義（`State`/`Outcome`/`TRANSITIONS`/`PHASES`等）。副作用なし | [model.md](model.md) |
| `triad/store.py` | Gitバックエンドの永続化。状態遷移の検証、ロック、承認記録 | [store.md](store.md) |
| `triad/runner.py` | 現在フェーズのAI・Docker処理を実行するエンジン | [runner.md](runner.md) |
| `triad/adapters.py` | Codex/Claude/AntigravityのCLIサブプロセス起動 | [adapters.md](adapters.md) |
| `triad/policy.py` | セキュリティ・検証ユーティリティ、`PolicyError` | [policy.md](policy.md) |
| `triad/cli.py` | `argparse`ベースのコマンドライン入口、確認句プロトコル | [cli.md](cli.md) |
| `bin/triad` / `bin/triad-new` | CLI起動シム／新規アプリ一括作成スクリプト | [bin.md](bin.md) |

## パッケージ依存関係

```mermaid
graph TD
    binTriad["bin/triad<br/>(シェルシム)"] --> cli
    binNew["bin/triad-new<br/>(Bash)"] --> binTriad

    cli["triad/cli.py"] --> store["triad/store.py"]
    cli --> runner["triad/runner.py"]
    cli --> model["triad/model.py"]
    cli --> policy["triad/policy.py"]

    runner --> store
    runner --> adapters["triad/adapters.py"]
    runner --> model
    runner --> policy

    store --> model
    store --> policy

    adapters --> policy

    contracts["contracts/*.schema.json"] -.検証対象.-> adapters
    contracts -.検証対象.-> store
```

`policy.py`は他のどのTriadモジュールにも依存しない末端の共通基盤であり、`model.py`はデータ定義のみで他モジュールに依存しない。`cli.py`が唯一の実行エントリポイントで、`Store`と`Runner`の両方を組み立てて各サブコマンドへ配る。

## クラス関係図

```mermaid
classDiagram
    class State { <<StrEnum>> }
    class Outcome { <<StrEnum>> }
    class PhaseSpec { <<frozen dataclass>> }
    class PolicyError { <<RuntimeError>> }
    class RunResult { <<frozen dataclass>> }

    class Store {
        +Path repo
        +Path root
        +Path meta
        +advance() dict
        +approve() dict
        +task_lock() contextmanager
    }
    class Runner {
        +Store store
        +Adapter adapter
        +run(task_id) Path
    }
    class Adapter {
        +run_artifact() RunResult
        +run_workspace() RunResult
    }

    Runner "1" *-- "1" Adapter : self.adapter
    Runner ..> Store : uses
    Adapter ..> RunResult : creates
    Store ..> PolicyError : raises
    Runner ..> PolicyError : raises
    Adapter ..> PolicyError : raises
    Store ..> State : reads/writes
    Store ..> Outcome : validates against TRANSITIONS
    Runner ..> PhaseSpec : reads PHASES
    Runner ..> State : reads DELIBERATION_STATES

    note for PolicyError "triad/policy.pyで定義。\nstore/runner/adapters/cliが\n共通して送出する唯一の例外型"
    note for State "triad/model.pyで定義。\n27状態。model.py自身は\n他モジュールに依存しない"
```

`Store`・`Runner`・`Adapter`はいずれも状態やインスタンスを持たない`model.py`のテーブル（`TRANSITIONS`・`PHASES`・`HUMAN_GATES`等）と、`policy.py`の純粋関数を「参照するだけ」で協調しており、継承関係は存在しない——Triad全体はコンポジション（has-a／uses）中心の薄い層構造である。

## End-to-Endシーケンス（1フェーズぶんの通常サイクル）

チャット担当AI（Codex拡張／Claude Code拡張）が1フェーズを進める際に内部で行う、`status → run → advance`の典型的な1サイクル。

```mermaid
sequenceDiagram
    participant Human as 人間
    participant Chat as VS Codeチャット担当AI
    participant CLI as triad CLI
    participant Runner
    participant Store
    participant Adapter
    participant AI as 外部AI CLI<br/>(codex/claude/agy)

    Human->>Chat: 「続きを進めて」
    Chat->>CLI: triad status <task_id> --json
    CLI->>Store: load_state()（verify_guidance + verify_frozen）
    Store-->>CLI: 現在状態・担当・human_actions
    CLI-->>Chat: JSON（owner, expected_artifact, human_actions）

    Chat->>CLI: triad run <task_id>
    CLI->>Runner: run(task_id)
    Runner->>Store: load_state() / task_dir()
    Runner->>Runner: resolved_agent() でエージェント解決
    Runner->>Adapter: run_artifact(agent, prompt, cwd, timeout)
    Adapter->>AI: サブプロセス起動（環境スクラビング済み）
    AI-->>Adapter: 構造化JSON出力
    Adapter-->>Runner: RunResult(payload検証済み)
    Runner->>Store: write_text()で成果物を保存<br/>append_audit()で監査ログ記録
    Runner-->>CLI: 成果物パス
    CLI-->>Chat: 成果物パス

    Chat->>CLI: triad advance <task_id> --outcome success
    CLI->>Store: verify_review_outcome() → advance()
    Store->>Store: task_lock() → TRANSITIONS照合 →<br/>REQUIRED_ON_EXIT確認 → _transition()
    Store-->>CLI: 新しい状態
    CLI-->>Chat: 新しい状態
    Chat->>Chat: git add + git commit（ローカルコミット）
    Chat-->>Human: 進捗を要約して報告
```

人間承認ゲート（`AWAITING_PLAN_APPROVAL`/`AWAITING_DELIVERY_APPROVAL`）に到達した場合の確認句プロトコルは[cli.md](cli.md#確認句confirmation-phraseの2段階プロトコル)を参照。

## 状態遷移（マクロ視点）

詳細な全27状態の遷移図は[model.md](model.md#状態遷移図全状態全遷移)を参照。ここでは俯瞰用の簡略版のみを示す。

```mermaid
stateDiagram-v2
    [*] --> 計画マクロフェーズ
    計画マクロフェーズ --> AWAITING_PLAN_APPROVAL : 3AIの調査・提案・統合・要件・設計・計画
    AWAITING_PLAN_APPROVAL --> 計画マクロフェーズ : 人間 needs_changes（差し戻し）
    AWAITING_PLAN_APPROVAL --> 成果物マクロフェーズ : 人間 approve【承認ゲート1】
    成果物マクロフェーズ --> AWAITING_DELIVERY_APPROVAL : タスク分解・実装・レビュー・ビルド/テスト・E2E・完成資料
    AWAITING_DELIVERY_APPROVAL --> 成果物マクロフェーズ : 人間 needs_changes（FIXへ差し戻し）
    AWAITING_DELIVERY_APPROVAL --> DELIVERED : 人間 approve【承認ゲート2】
    成果物マクロフェーズ --> CHANGE_REQUEST : 承認済み計画との矛盾を検知
    CHANGE_REQUEST --> 計画マクロフェーズ : 人間 approve_change（承認を無効化し再開）
    DELIVERED --> [*]
```

## contracts/ のスキーマ

`contracts/`はコードを持たないJSON Schema（Draft 2020-12）定義であり、`model.py`のテーブル・各CLIの構造化出力・`Runner`が書くファイルの整合性を機械的に保証する契約境界である。

| スキーマ | 検証対象 | 生成元 | 消費元 |
|---|---|---|---|
| `agent-output.schema.json` | 各AI CLIが返す`{summary, content, verdict, human_decisions}` | 外部AI CLI（codex/claude/agy） | [`Adapter._valid_payload`](adapters.md) |
| `handoff.schema.json` | フェーズ開始前に書く引き継ぎ情報 | [`Runner._write_handoff`](runner.md) | （人間・AIが参照する記録） |
| `state.schema.json` | `state.json`（27状態のenumを含む） | [`Store`](store.md)（唯一の書き込み元） | `tests/test_contracts.py`が`model.State`との一致を検証 |

```mermaid
graph LR
    subgraph 外部AI CLI
        Codex["codex exec"]
        Claude["claude -p"]
        Agy["agy -p"]
    end
    Codex & Claude & Agy -- "--output-schema / --json-schema" --> AOS["agent-output.schema.json"]
    AOS -- 検証 --> Adapter["Adapter._valid_payload()"]

    Runner["Runner._write_handoff()"] -- 生成 --> HOS["handoff.schema.json"]

    Store["Store（state.json書き込み）"] -- 準拠 --> SS["state.schema.json"]
    SS -.整合性テスト.-> ModelState["model.State（27状態）"]
```

## 関連ドキュメント

- 設計判断の根拠: [アーキテクチャ](../architecture.md)
- 人間向け運用手順: [運用手順](../operations.md)
- 文書全体の構成: [文書案内](../../README.md)
