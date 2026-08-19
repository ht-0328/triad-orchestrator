# model.py — 状態機械の定義

[← 設計書トップ](index.md)

`triad/model.py`はTriadの状態機械そのものを表す、副作用を持たない宣言的なデータモジュールである。`State`（状態）と`Outcome`（結果）の2つの列挙型、状態遷移表`TRANSITIONS`、承認ゲート対応表、各状態の担当AIと成果物を表す`PhaseSpec`/`PHASES`、そして実装担当AIを動的に解決する`resolved_agent()`関数を定義する。`Store`と`Runner`はいずれもこのモジュールが持つテーブルを参照するだけで、`model.py`自身はインスタンスを持たない。

## State / Outcome

```mermaid
classDiagram
    class State {
        <<StrEnum>>
        INTAKE
        RESEARCH
        SOLUTION_PROPOSAL
        PROPOSAL_REVIEW
        PROPOSAL_FACT_CHECK
        SYNTHESIS
        SYNTHESIS_REVIEW
        REQUIREMENTS
        REQUIREMENTS_REVIEW
        DESIGN
        DESIGN_RESEARCH
        DESIGN_REVIEW
        PLAN
        PLAN_REVIEW
        AWAITING_PLAN_APPROVAL
        TASK_BREAKDOWN
        TASK_PLAN_REVIEW
        IMPLEMENTATION
        CODE_REVIEW
        FIX
        BUILD_TEST
        E2E_VERIFY
        DELIVERY_PREP
        DELIVERY_REVIEW
        AWAITING_DELIVERY_APPROVAL
        DELIVERED
        CHANGE_REQUEST
    }
    class Outcome {
        <<StrEnum>>
        SUCCESS
        APPROVE
        NEEDS_CHANGES
        FAIL
        SKIP
    }
    class PhaseSpec {
        <<frozen dataclass>>
        +str agent
        +str|None artifact
        +str purpose
        +bool review
        +bool mutates_workspace
    }
```

`State`は27種類（うち26種類は`TRANSITIONS`で通常の状態遷移として直接到達し、`CHANGE_REQUEST`だけは`Store.request_change()`という別経路で到達する側路状態）。`Outcome`は5種類で、`SKIP`だけは`RESEARCH`・`PROPOSAL_FACT_CHECK`・`DESIGN_RESEARCH`・`E2E_VERIFY`の4状態でしか使えないという追加制約が`Store.advance()`側にある（`TRANSITIONS`自体には他の状態でも`SKIP`キーが存在しないため二重に防御されている）。

## 状態遷移図（全状態・全遷移）

`TRANSITIONS`（model.py 45-108行）を1対1でMermaid化したもの。計画マクロフェーズは全てAI主体で人間を止めない。成果物マクロフェーズの末尾で人間の承認ゲート2を経て`DELIVERED`に至る。

```mermaid
stateDiagram-v2
    [*] --> INTAKE

    state "計画マクロフェーズ（全AI主体・人間停止なし）" as Planning {
        INTAKE --> RESEARCH : success
        RESEARCH --> SOLUTION_PROPOSAL : success / skip
        SOLUTION_PROPOSAL --> PROPOSAL_REVIEW : success
        PROPOSAL_REVIEW --> PROPOSAL_FACT_CHECK : approve
        PROPOSAL_REVIEW --> SOLUTION_PROPOSAL : needs_changes
        PROPOSAL_FACT_CHECK --> SYNTHESIS : success / skip
        SYNTHESIS --> SYNTHESIS_REVIEW : success
        SYNTHESIS_REVIEW --> REQUIREMENTS : approve
        SYNTHESIS_REVIEW --> SYNTHESIS : needs_changes
        REQUIREMENTS --> REQUIREMENTS_REVIEW : success
        REQUIREMENTS_REVIEW --> DESIGN : approve
        REQUIREMENTS_REVIEW --> REQUIREMENTS : needs_changes
        DESIGN --> DESIGN_RESEARCH : success
        DESIGN_RESEARCH --> DESIGN_REVIEW : success / skip
        DESIGN_REVIEW --> PLAN : approve
        DESIGN_REVIEW --> DESIGN : needs_changes
        PLAN --> PLAN_REVIEW : success
        PLAN_REVIEW --> AWAITING_PLAN_APPROVAL : approve
        PLAN_REVIEW --> PLAN : needs_changes
    }

    AWAITING_PLAN_APPROVAL --> INTAKE : 人間 needs_changes（差し戻し）
    AWAITING_PLAN_APPROVAL --> TASK_BREAKDOWN : 人間 approve（承認ゲート1・HUMAN_GATES経由）

    state "成果物マクロフェーズ（計画承認後のビルド・検証）" as Delivery {
        TASK_BREAKDOWN --> TASK_PLAN_REVIEW : success
        TASK_PLAN_REVIEW --> IMPLEMENTATION : approve
        TASK_PLAN_REVIEW --> TASK_BREAKDOWN : needs_changes
        IMPLEMENTATION --> CODE_REVIEW : success
        CODE_REVIEW --> BUILD_TEST : approve
        CODE_REVIEW --> FIX : needs_changes
        FIX --> CODE_REVIEW : success
        BUILD_TEST --> E2E_VERIFY : success
        BUILD_TEST --> FIX : fail
        E2E_VERIFY --> DELIVERY_PREP : success / skip
        E2E_VERIFY --> FIX : fail
        DELIVERY_PREP --> DELIVERY_REVIEW : success
        DELIVERY_REVIEW --> AWAITING_DELIVERY_APPROVAL : approve
        DELIVERY_REVIEW --> DELIVERY_PREP : needs_changes
    }

    AWAITING_DELIVERY_APPROVAL --> FIX : 人間 needs_changes（作り直し）
    AWAITING_DELIVERY_APPROVAL --> DELIVERED : 人間 approve（承認ゲート2・HUMAN_GATES経由）
    DELIVERED --> [*]

    Delivery --> CHANGE_REQUEST : request_change（TASK_BREAKDOWN〜AWAITING_DELIVERY_APPROVALのどこからでも発議可）
    CHANGE_REQUEST --> INTAKE : 人間 approve_change（既存承認を無効化して再開）
```

補足:

- `AWAITING_PLAN_APPROVAL --> TASK_BREAKDOWN`と`AWAITING_DELIVERY_APPROVAL --> DELIVERED`は`TRANSITIONS`表には存在しない。これらは`Store.approve()`が`HUMAN_GATES`表を参照して直接遷移させる、人間承認専用の経路である。
- `CHANGE_REQUEST`への遷移は`TRANSITIONS`表の外にあり、`Store.request_change()`内のハードコードされた許可リスト（計画マクロフェーズと`AWAITING_PLAN_APPROVAL`・`CHANGE_REQUEST`・`DELIVERED`を除く全状態）で判定する。
- `CHANGE_REQUEST --> INTAKE`は`Store.approve_change()`が行う。既存の`approvals`をすべて`superseded_approvals`へ退避し、空にしてから遷移する。

## 承認ゲート・差し戻し対応表

| テーブル | 内容 |
|---|---|
| `HUMAN_GATES` | ゲート名(`plan`/`delivery`) → (待機状態, 承認後の遷移先)。`plan`: `AWAITING_PLAN_APPROVAL`→`TASK_BREAKDOWN`。`delivery`: `AWAITING_DELIVERY_APPROVAL`→`DELIVERED`。 |
| `REVISION_GATES` | 待機状態 → ゲート名。差し戻しフィードバックファイル名（`plan-feedback-NNN.md`等）の決定に使う。 |
| `GATE_TARGETS` | ゲート名 → 固定対象の相対パス一覧。`plan`は14件（`input/intake.md`〜`reviews/plan-review.md`）、`delivery`は7件（`artifacts/task-plan.md`〜`reviews/delivery-review.md`）。承認時にこれら全てのSHA-256を承認記録へ焼き付ける。 |
| `REQUIRED_ON_EXIT` | 各状態 → その状態を抜けるために存在すべき成果物パス。`PHASES`の`artifact`から自動生成し、`BUILD_TEST`だけ`evidence/build-test.md`を手動追加。 |
| `DELIBERATION_STATES` | 計画マクロフェーズ14状態（`INTAKE`〜`PLAN_REVIEW`）のfrozenset。この間は`human_decisions`を空にしなければならない。 |

## PhaseSpec / PHASES

各状態に対応する`PhaseSpec(agent, artifact, purpose, review, mutates_workspace)`を`PHASES`辞書で保持する。`agent`が`"dynamic"`の状態（`IMPLEMENTATION`・`CODE_REVIEW`・`FIX`）だけは`resolved_agent()`で実行時に解決する。

```mermaid
graph LR
    subgraph 固定担当のフェーズ
        A1["INTAKE → codex"]
        A2["RESEARCH / PROPOSAL_FACT_CHECK / DESIGN_RESEARCH / E2E_VERIFY → antigravity"]
        A3["SOLUTION_PROPOSAL / REQUIREMENTS / DESIGN → claude"]
        A4["PROPOSAL_REVIEW / SYNTHESIS / REQUIREMENTS_REVIEW / DESIGN_REVIEW / PLAN / TASK_BREAKDOWN / DELIVERY_PREP → codex"]
        A5["SYNTHESIS_REVIEW / PLAN_REVIEW / TASK_PLAN_REVIEW / DELIVERY_REVIEW → claude"]
    end
    subgraph 動的解決のフェーズ
        B1["IMPLEMENTATION / FIX → dynamic"]
        B2["CODE_REVIEW → dynamic"]
    end
```

## resolved_agent() の解決ロジック

```mermaid
flowchart TD
    Start["resolved_agent(state, implementation_author)"] --> Q1{"PHASES[state].agent<br/>は dynamic か？"}
    Q1 -- いいえ --> R1["spec.agentをそのまま返す"]
    Q1 -- はい --> Q2{"state は？"}
    Q2 -- IMPLEMENTATION --> R2["implementation_authorを返す"]
    Q2 -- FIX --> R2
    Q2 -- CODE_REVIEW --> R3["implementation_authorと逆のAIを返す<br/>(codexならclaude、それ以外ならcodex)"]
    Q2 -- それ以外 --> R4["ValueError:<br/>動的選択規則がありません"]
```

`CODE_REVIEW`で実装者と逆のAIを選ぶことで、自作コードの自己レビューを構造的に防いでいる。`implementation_author`は`state.json`に固定され、タスク作成時（`init`）に`claude`または`codex`のいずれかで確定する。

## 関連ファイル

- 実装: [`triad/model.py`](../../../triad/model.py)
- 参照元: [`triad/store.py`](store.md)（`TRANSITIONS`/`HUMAN_GATES`/`REVISION_GATES`/`GATE_TARGETS`/`REQUIRED_ON_EXIT`）、[`triad/runner.py`](runner.md)（`PHASES`/`DELIBERATION_STATES`/`resolved_agent`）
- 契約: [`contracts/state.schema.json`](../../../contracts/state.schema.json)が`State`と同じ27種類の`enum`を持つ（`tests/test_contracts.py`が一致を検証）
