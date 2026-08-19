# runner.py — フェーズ実行エンジン

[← 設計書トップ](index.md)

`triad/runner.py`の`Runner`クラスは、現在の状態に割り当てられたAIまたはDocker処理を実際に実行する。`Store`が「何を・いつ」遷移させるかを管理するのに対し、`Runner`は「どう実行するか」を担う。読み取り専用の成果物作成（`Adapter.run_artifact`）、ワークスペースを書き換える実装・修正（`_workspace_run`）、Docker Composeによるビルド・テスト（`_build_test`）の3系統の実行経路を持つ。

## クラス図

```mermaid
classDiagram
    class Runner {
        +Store store
        +Adapter adapter
        +Path platform_root
        +dict config
        +run(task_id) Path
        -_workspace_run(task_id, state, agent, prompt, timeout, artifact) Path
        -_build_test(task_id) Path
        -_write_handoff(task_id, state, agent, expected_output, timeout) Path
        -_snapshot_protected(root) dict
        -_protected_drift(before, root) list~str~
        -_restore_protected(before, root) void
        -_prompt(task_id, state, agent, purpose, review, mutates) str
        -_markdown(task_id, state, agent, payload)$ str
        -_record_decisions(task_id, state, artifact, payload) Path
    }
    class Adapter {
        +run_artifact(agent, prompt, cwd, timeout) RunResult
        +run_workspace(agent, prompt, cwd, timeout) RunResult
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
    Runner "1" *-- "1" Adapter : self.adapter
    Runner ..> Store : store.load_state / write_text / task_lock
    Adapter ..> RunResult : creates
    Runner ..> RunResult : consumes
```

`Runner(store, platform_root)`のコンストラクタは`Adapter(platform_root)`を内部で生成し、`config/agents.json`（エージェントごとのタイムアウト秒数・自動リトライ回数）を読み込む。

## run() のディスパッチ

```mermaid
flowchart TD
    Start["run(task_id)"] --> LoadState["store.load_state()"]
    LoadState --> IsBuildTest{"state == BUILD_TEST?"}
    IsBuildTest -- はい --> BuildTest["_build_test(task_id)へ"]
    IsBuildTest -- いいえ --> InPhases{"state は<br/>PHASESに存在するか？"}
    InPhases -- いいえ --> ErrPhase["PolicyError:<br/>AIが実行できない状態です"]
    InPhases -- はい --> Resolve["resolved_agent(state, implementation_author)"]
    Resolve --> AgyCheck{"agent==antigravity<br/>かつ利用不可か？"}
    AgyCheck -- はい --> ErrAgy["PolicyError:<br/>advance --outcome skipを明示せよ"]
    AgyCheck -- いいえ --> BuildPrompt["_prompt()でプロンプト生成"]
    BuildPrompt --> Mutates{"spec.mutates_workspace?"}
    Mutates -- はい --> WorkspaceRun["_workspace_run()へ<br/>（IMPLEMENTATION / FIX）"]
    Mutates -- いいえ --> ArtifactPath["読み取り専用経路へ"]
```

### 読み取り専用経路（成果物作成・レビューフェーズ）

```mermaid
flowchart TD
    A["_write_handoff()で<br/>handoffs/NNNN-<phase>.jsonを記録"] --> B["最大 1+automatic_retries.read_only 回<br/>Adapter.run_artifact()を呼ぶ"]
    B --> C["毎回append_audit()で監査ログ記録"]
    C --> D{"exit_code==0 かつ<br/>payload!=None?"}
    D -- はい --> E["ループを抜ける"]
    D -- いいえ --> F{"timed_out?"}
    F -- はい --> E
    F -- いいえ --> G{"リトライ回数<br/>残っているか？"}
    G -- はい --> B
    G -- いいえ --> E
    E --> H{"timed_out?"}
    H -- はい --> ErrTimeout["PolicyError: タイムアウト"]
    H -- いいえ --> I{"exit_code!=0?"}
    I -- はい --> ErrExit["PolicyError: 終了コード異常"]
    I -- いいえ --> J{"payload is None?"}
    J -- はい --> ErrPayload["PolicyError: 有効な構造化出力なし"]
    J -- いいえ --> K{"review状態なのに<br/>verdict==not_applicable?<br/>または非review状態なのに<br/>verdict!=not_applicable?"}
    K -- 矛盾あり --> ErrVerdict["PolicyError: verdict規約違反"]
    K -- 整合 --> L{"DELIBERATION_STATES中<br/>かつ human_decisionsが<br/>空でない?"}
    L -- はい --> ErrDeliberation["PolicyError:<br/>3AI協議中は人間判断で停止できません"]
    L -- いいえ --> M["_markdown()で本文生成し<br/>artifact_path()へwrite_text()"]
    M --> N["_record_decisions()で<br/>human_decisionsがあれば<br/>decisions/NNNN-<phase>.jsonを作成"]
```

## _workspace_run()（使い捨てクローンによる実行境界）

`IMPLEMENTATION`・`FIX`はソースツリーを書き換える唯一のフェーズである。AIの子プロセスが正本の作業ツリーを直接触ることは一度もなく、必ず使い捨てのローカルクローンの中で実行される。

```mermaid
sequenceDiagram
    participant Runner
    participant Repo as 正本の作業ツリー<br/>(store.root)
    participant Clone as 使い捨てクローン<br/>(tempdir/repo)
    participant Adapter
    participant AI as AI CLIサブプロセス<br/>(claude / codex)

    Runner->>Runner: task_lock()取得
    Runner->>Repo: git status --porcelain（クリーンか確認）
    alt 未コミット変更がある
        Runner-->>Runner: PolicyError（先にコミットせよ）
    end
    Runner->>Repo: _write_handoff()でhandoffs/NNNN.jsonを書く
    Runner->>Repo: baseline_head = git rev-parse HEAD
    Runner->>Clone: git clone --local --no-hardlinks Repo Clone
    Runner->>Clone: git remote remove origin
    Runner->>Clone: handoff.jsonをコピー
    Runner->>Clone: _snapshot_protected()<br/>(.ai-dev, AGENTS.md, CLAUDE.md, GEMINI.md の内容+権限を記録)
    Runner->>Adapter: run_workspace(agent, prompt, Clone, timeout)
    Adapter->>AI: サブプロセス起動（環境スクラビング済み）
    AI-->>Clone: ファイルを編集
    AI-->>Adapter: 終了
    Adapter-->>Runner: RunResult
    Runner->>Runner: append_audit()
    Runner->>Clone: _protected_drift()で保護ファイルの改変を検出
    Runner->>Clone: _isolated_changed_paths()で変更パス一覧を取得
    alt 保護パス違反 or timed_out or exit_code!=0
        Runner->>Runner: patch = 空（何も適用しない）
    else 正常終了
        Runner->>Runner: validate_changed_paths(changed)
        Runner->>Clone: git diff --binary baseline_head（パッチ生成）
    end
    alt パッチが空でない
        Runner->>Repo: git apply --binary --whitespace=nowarn（検証済みパッチだけ適用）
    end
    Runner->>Repo: 適用後の実差分をvalidate_changed_paths()で再検証
    alt 保護パス違反が検出されていた
        Runner-->>Runner: PolicyError（ソースパッチは適用済みでない）
    end
    Runner->>Repo: evidence/<phase>.json に変更パス一覧等を記録
```

保護対象（`.ai-dev/`配下・`AGENTS.md`・`CLAUDE.md`・`GEMINI.md`・`approvals/*`・`state.json`・`history.jsonl`）が使い捨てクローン内で変更された場合、`git apply`自体を行わない（パッチを丸ごと破棄する）ため、正本の作業ツリーには一切影響しない。クローンはテンポラリディレクトリごと関数を抜けた時点で自動的に削除される。

## _build_test()（Docker Composeによる検証）

```mermaid
flowchart TD
    A["task_lock()取得・<br/>作業ツリークリーン確認"] --> B["project.jsonからbuild_testコマンドを取得<br/>（docker compose runで始まり<br/>--rmを含み危険フラグを含まないことを検証）"]
    B --> C["使い捨てクローンを作成<br/>（git clone --local --no-hardlinks + remote remove）"]
    C --> D["_preflight_compose_files()<br/>（Composeファイルが1つだけ・オーバーライド不在・<br/>include/extends/env_fileキー不在を確認）"]
    D --> E["docker compose config --no-env-resolution --format json<br/>で設定を解決"]
    E --> F["validate_compose_model()<br/>（policy.pyの禁止項目チェック）"]
    F --> G["docker compose run --rm test を<br/>タイムアウト3600秒で実行"]
    G --> H{"タイムアウトしたか？"}
    H -- はい --> I["SIGTERM → 10秒待機 → SIGKILL"]
    H -- いいえ --> J["正常終了"]
    I --> K["docker compose down --remove-orphans で後始末"]
    J --> L["stdout+stderrの末尾65536文字をreject_secrets()でスキャン"]
    K --> L
    L --> M["evidence/build-test.md に<br/>終了コード・タイムアウト有無・出力を記録"]
```

## 保護パスのスナップショット/ドリフト検出

`_snapshot_protected()`は使い捨てクローン内の`.ai-dev/`配下および`AGENTS.md`・`CLAUDE.md`・`GEMINI.md`の内容とパーミッションを実行前に記録する。`_protected_drift()`は実行後に同じ集合を再取得し、内容が変化したパスの一覧を返す。1件でもあれば`_workspace_run`はパッチ適用そのものを取りやめる。`_restore_protected()`はテスト・障害復旧用のヘルパーで、記録済みのスナップショットへ内容とパーミッションを書き戻す。

## プロンプト生成 `_prompt()`

各AI呼び出しのプロンプトは`_prompt()`が動的に組み立てる。フェーズの`purpose`に加え、次の3種のルールを状態に応じて切り替えて埋め込む。

| ルール | 分岐条件 | 内容 |
|---|---|---|
| `verdict_rule` | `spec.review` | レビューなら`approve`/`needs_changes`必須、非レビューなら`not_applicable`必須 |
| `mutation_rule` | `spec.mutates_workspace` | 書き込み許可（`.ai-dev`等は編集禁止、build/test/commit禁止）か、完全読み取り専用か |
| `decision_rule` | `state in DELIBERATION_STATES` | 計画マクロフェーズ中は質問で停止禁止（仮定・選択肢・リスクを本文へ）、それ以外は重要事項のみ`human_decisions`へ |

プロンプトは「Git追跡ファイルだけが共有文脈であり、他AIの非公開チャットやセッションを使わないこと」「承認済み成果物のハッシュは不変であり、変更が必要なら停止してhuman_decisionsへ記載すること」を毎回明示する。

## 関連ファイル

- 実装: [`triad/runner.py`](../../../triad/runner.py)
- 依存: [`adapters.py`](adapters.md)（`Adapter`/`RunResult`）、[`model.py`](model.md)（`PHASES`/`DELIBERATION_STATES`/`resolved_agent`）、[`policy.py`](policy.md)（`is_protected_path`/`validate_changed_paths`/`validate_compose_model`等）、[`store.py`](store.md)
