from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    INTAKE = "INTAKE"
    RESEARCH = "RESEARCH"
    SOLUTION_PROPOSAL = "SOLUTION_PROPOSAL"
    PROPOSAL_REVIEW = "PROPOSAL_REVIEW"
    PROPOSAL_FACT_CHECK = "PROPOSAL_FACT_CHECK"
    SYNTHESIS = "SYNTHESIS"
    SYNTHESIS_REVIEW = "SYNTHESIS_REVIEW"
    REQUIREMENTS = "REQUIREMENTS"
    REQUIREMENTS_REVIEW = "REQUIREMENTS_REVIEW"
    DESIGN = "DESIGN"
    DESIGN_RESEARCH = "DESIGN_RESEARCH"
    DESIGN_REVIEW = "DESIGN_REVIEW"
    PLAN = "PLAN"
    PLAN_REVIEW = "PLAN_REVIEW"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    TASK_BREAKDOWN = "TASK_BREAKDOWN"
    TASK_PLAN_REVIEW = "TASK_PLAN_REVIEW"
    IMPLEMENTATION = "IMPLEMENTATION"
    CODE_REVIEW = "CODE_REVIEW"
    FIX = "FIX"
    BUILD_TEST = "BUILD_TEST"
    E2E_VERIFY = "E2E_VERIFY"
    DELIVERY_PREP = "DELIVERY_PREP"
    DELIVERY_REVIEW = "DELIVERY_REVIEW"
    AWAITING_DELIVERY_APPROVAL = "AWAITING_DELIVERY_APPROVAL"
    DELIVERED = "DELIVERED"
    CHANGE_REQUEST = "CHANGE_REQUEST"


class Outcome(StrEnum):
    SUCCESS = "success"
    APPROVE = "approve"
    NEEDS_CHANGES = "needs_changes"
    FAIL = "fail"
    SKIP = "skip"


TRANSITIONS: dict[State, dict[Outcome, State]] = {
    State.INTAKE: {Outcome.SUCCESS: State.RESEARCH},
    State.RESEARCH: {
        Outcome.SUCCESS: State.SOLUTION_PROPOSAL,
        Outcome.SKIP: State.SOLUTION_PROPOSAL,
    },
    State.SOLUTION_PROPOSAL: {Outcome.SUCCESS: State.PROPOSAL_REVIEW},
    State.PROPOSAL_REVIEW: {
        Outcome.APPROVE: State.PROPOSAL_FACT_CHECK,
        Outcome.NEEDS_CHANGES: State.SOLUTION_PROPOSAL,
    },
    State.PROPOSAL_FACT_CHECK: {
        Outcome.SUCCESS: State.SYNTHESIS,
        Outcome.SKIP: State.SYNTHESIS,
    },
    State.SYNTHESIS: {Outcome.SUCCESS: State.SYNTHESIS_REVIEW},
    State.SYNTHESIS_REVIEW: {
        Outcome.APPROVE: State.REQUIREMENTS,
        Outcome.NEEDS_CHANGES: State.SYNTHESIS,
    },
    State.REQUIREMENTS: {Outcome.SUCCESS: State.REQUIREMENTS_REVIEW},
    State.REQUIREMENTS_REVIEW: {
        Outcome.APPROVE: State.DESIGN,
        Outcome.NEEDS_CHANGES: State.REQUIREMENTS,
    },
    State.DESIGN: {Outcome.SUCCESS: State.DESIGN_RESEARCH},
    State.DESIGN_RESEARCH: {
        Outcome.SUCCESS: State.DESIGN_REVIEW,
        Outcome.SKIP: State.DESIGN_REVIEW,
    },
    State.DESIGN_REVIEW: {
        Outcome.APPROVE: State.PLAN,
        Outcome.NEEDS_CHANGES: State.DESIGN,
    },
    State.PLAN: {Outcome.SUCCESS: State.PLAN_REVIEW},
    State.PLAN_REVIEW: {
        Outcome.APPROVE: State.AWAITING_PLAN_APPROVAL,
        Outcome.NEEDS_CHANGES: State.PLAN,
    },
    State.AWAITING_PLAN_APPROVAL: {Outcome.NEEDS_CHANGES: State.INTAKE},
    State.TASK_BREAKDOWN: {Outcome.SUCCESS: State.TASK_PLAN_REVIEW},
    State.TASK_PLAN_REVIEW: {
        Outcome.APPROVE: State.IMPLEMENTATION,
        Outcome.NEEDS_CHANGES: State.TASK_BREAKDOWN,
    },
    State.IMPLEMENTATION: {Outcome.SUCCESS: State.CODE_REVIEW},
    State.CODE_REVIEW: {
        Outcome.APPROVE: State.BUILD_TEST,
        Outcome.NEEDS_CHANGES: State.FIX,
    },
    State.FIX: {Outcome.SUCCESS: State.CODE_REVIEW},
    State.BUILD_TEST: {Outcome.SUCCESS: State.E2E_VERIFY, Outcome.FAIL: State.FIX},
    State.E2E_VERIFY: {
        Outcome.SUCCESS: State.DELIVERY_PREP,
        Outcome.SKIP: State.DELIVERY_PREP,
        Outcome.FAIL: State.FIX,
    },
    State.DELIVERY_PREP: {Outcome.SUCCESS: State.DELIVERY_REVIEW},
    State.DELIVERY_REVIEW: {
        Outcome.APPROVE: State.AWAITING_DELIVERY_APPROVAL,
        Outcome.NEEDS_CHANGES: State.DELIVERY_PREP,
    },
    State.AWAITING_DELIVERY_APPROVAL: {Outcome.NEEDS_CHANGES: State.FIX},
}


# 承認ゲート名からの対応表。approve()は(現在状態, 承認後の遷移先)を、
# advance()のNEEDS_CHANGESはTRANSITIONSの差し戻し先を使う。
HUMAN_GATES: dict[str, tuple[State, State]] = {
    "plan": (State.AWAITING_PLAN_APPROVAL, State.TASK_BREAKDOWN),
    "delivery": (State.AWAITING_DELIVERY_APPROVAL, State.DELIVERED),
}


REVISION_GATES: dict[State, str] = {
    State.AWAITING_PLAN_APPROVAL: "plan",
    State.AWAITING_DELIVERY_APPROVAL: "delivery",
}


DELIBERATION_STATES = frozenset(
    {
        State.INTAKE,
        State.RESEARCH,
        State.SOLUTION_PROPOSAL,
        State.PROPOSAL_REVIEW,
        State.PROPOSAL_FACT_CHECK,
        State.SYNTHESIS,
        State.SYNTHESIS_REVIEW,
        State.REQUIREMENTS,
        State.REQUIREMENTS_REVIEW,
        State.DESIGN,
        State.DESIGN_RESEARCH,
        State.DESIGN_REVIEW,
        State.PLAN,
        State.PLAN_REVIEW,
    }
)


@dataclass(frozen=True)
class PhaseSpec:
    agent: str
    artifact: str | None
    purpose: str
    review: bool = False
    mutates_workspace: bool = False


PHASES: dict[State, PhaseSpec] = {
    State.INTAKE: PhaseSpec(
        "codex",
        "input/intake.md",
        "人間の短い依頼から、解決方法を確定せずに目的、利用者、制約、調査論点、仮定を整理する",
    ),
    State.RESEARCH: PhaseSpec(
        "antigravity", "artifacts/research.md", "公式情報・競合・技術制約を一次情報中心で調査する"
    ),
    State.SOLUTION_PROPOSAL: PhaseSpec(
        "claude",
        "artifacts/solution-proposal.md",
        "依頼整理と調査結果から複数の実現案を比較し、要件・設計上の制約を踏まえた推奨案を作る",
    ),
    State.PROPOSAL_REVIEW: PhaseSpec(
        "codex",
        "reviews/proposal-review.md",
        "依頼と調査から独立見解を組み立てたうえでClaude案と比較し、代替案、見落とし、選定根拠をレビューする",
        review=True,
    ),
    State.PROPOSAL_FACT_CHECK: PhaseSpec(
        "antigravity",
        "artifacts/proposal-fact-check.md",
        "Claude案とCodexレビューに含まれる外部事実、現行仕様、競合前提を一次情報で再検証する",
    ),
    State.SYNTHESIS: PhaseSpec(
        "codex",
        "artifacts/synthesis.md",
        "3AIの調査・提案・レビューを統合し、候補比較、採用する方針、採否理由、仮定、リスクを決定する",
    ),
    State.SYNTHESIS_REVIEW: PhaseSpec(
        "claude",
        "reviews/synthesis-review.md",
        "統合方針が調査と各案を公平かつ正確に反映しているか独立レビューする",
        review=True,
    ),
    State.REQUIREMENTS: PhaseSpec(
        "claude", "artifacts/requirements.md", "不足と矛盾を明示し、検証可能な要件を定義する"
    ),
    State.REQUIREMENTS_REVIEW: PhaseSpec(
        "codex", "reviews/requirements-review.md", "要件を独立レビューする", review=True
    ),
    State.DESIGN: PhaseSpec(
        "claude", "artifacts/design.md", "アーキテクチャ、API、データ、運用、テストを設計する"
    ),
    State.DESIGN_RESEARCH: PhaseSpec(
        "antigravity", "artifacts/design-research.md", "設計上の外部前提と現行仕様を実地確認する"
    ),
    State.DESIGN_REVIEW: PhaseSpec(
        "codex", "reviews/design-review.md", "設計を独立レビューする", review=True
    ),
    State.PLAN: PhaseSpec(
        "codex",
        "artifacts/plan.md",
        "統合方針・要件・設計・各レビュー判定・リスク・仮定を、人間が一度で判断できる計画書へ統合する",
    ),
    State.PLAN_REVIEW: PhaseSpec(
        "claude",
        "reviews/plan-review.md",
        "計画書一式が要件・設計・レビュー結果を漏れなく正確に反映し、人間へ提示できる状態か独立レビューする",
        review=True,
    ),
    State.TASK_BREAKDOWN: PhaseSpec(
        "codex", "artifacts/task-plan.md", "承認済み計画を依存関係と検証条件を含む実装タスクへ分解する"
    ),
    State.TASK_PLAN_REVIEW: PhaseSpec(
        "claude", "reviews/task-plan-review.md", "Codex作成の実装タスク計画を独立レビューする", review=True
    ),
    State.IMPLEMENTATION: PhaseSpec(
        "dynamic", "evidence/implementation.json", "承認済み計画とタスク計画の範囲内で実装する", mutates_workspace=True
    ),
    State.CODE_REVIEW: PhaseSpec(
        "dynamic",
        "reviews/code-review.md",
        "実装者とは異なるAIがコードとDocker Composeの実行境界を独立レビューする",
        review=True,
    ),
    State.FIX: PhaseSpec(
        "dynamic", "evidence/fix.json", "レビュー、テスト、または人間の作り直し指示を承認済み計画の範囲で反映する", mutates_workspace=True
    ),
    State.E2E_VERIFY: PhaseSpec(
        "antigravity", "evidence/e2e-report.md", "実ブラウザで操作・表示・再現性を検証する"
    ),
    State.DELIVERY_PREP: PhaseSpec(
        "codex",
        "artifacts/delivery-summary.md",
        "変更内容、検証証跡、既知の制約、残存リスクを統合する（実行承認ではない）",
    ),
    State.DELIVERY_REVIEW: PhaseSpec(
        "claude", "reviews/delivery-review.md", "Codex作成の成果物完成資料を独立レビューする", review=True
    ),
}


REQUIRED_ON_EXIT: dict[State, tuple[str, ...]] = {
    **{
        state: (spec.artifact,)
        for state, spec in PHASES.items()
        if spec.artifact is not None
    },
    State.BUILD_TEST: ("evidence/build-test.md",),
}


GATE_TARGETS: dict[str, tuple[str, ...]] = {
    "plan": (
        "input/intake.md",
        "artifacts/research.md",
        "artifacts/solution-proposal.md",
        "reviews/proposal-review.md",
        "artifacts/proposal-fact-check.md",
        "artifacts/synthesis.md",
        "reviews/synthesis-review.md",
        "artifacts/requirements.md",
        "reviews/requirements-review.md",
        "artifacts/design.md",
        "artifacts/design-research.md",
        "reviews/design-review.md",
        "artifacts/plan.md",
        "reviews/plan-review.md",
    ),
    "delivery": (
        "artifacts/task-plan.md",
        "reviews/task-plan-review.md",
        "evidence/build-test.md",
        "reviews/code-review.md",
        "evidence/e2e-report.md",
        "artifacts/delivery-summary.md",
        "reviews/delivery-review.md",
    ),
}


def resolved_agent(state: State, implementation_author: str) -> str:
    spec = PHASES[state]
    if spec.agent != "dynamic":
        return spec.agent
    if state is State.IMPLEMENTATION:
        return implementation_author
    if state is State.FIX:
        return implementation_author
    if state is State.CODE_REVIEW:
        return "claude" if implementation_author == "codex" else "codex"
    raise ValueError(f"状態{state}に対応するAIの動的選択規則がありません")
