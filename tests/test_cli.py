import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from triad.cli import (
    decision_confirmation,
    human_actions_for,
    human_confirmation,
    main,
    parser,
    redo_confirmation,
    verify_review_outcome,
)
from triad.model import Outcome, State
from triad.policy import PolicyError
from triad.store import Store


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def advance_to_requirements_review(store: Store) -> None:
    task = store.task_dir("TASK-1")
    store.write_text(task / "input" / "intake.md", "brief\n")
    store.advance("TASK-1", Outcome.SUCCESS, "codex", "brief")
    store.advance("TASK-1", Outcome.SKIP, "orchestrator", "research unavailable")
    store.write_text(task / "artifacts" / "solution-proposal.md", "proposal\n")
    store.advance("TASK-1", Outcome.SUCCESS, "claude", "proposal")
    store.write_text(task / "reviews" / "proposal-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "codex", "review")
    store.advance("TASK-1", Outcome.SKIP, "orchestrator", "fact check unavailable")
    store.write_text(task / "artifacts" / "synthesis.md", "synthesis\n")
    store.advance("TASK-1", Outcome.SUCCESS, "codex", "synthesis")
    store.write_text(task / "reviews" / "synthesis-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "claude", "review")
    store.write_text(task / "artifacts" / "requirements.md", "requirements\n")
    store.advance("TASK-1", Outcome.SUCCESS, "claude", "requirements")


def reach_plan_gate(store: Store) -> None:
    task = store.task_dir("TASK-1")
    advance_to_requirements_review(store)
    store.write_text(task / "reviews" / "requirements-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "codex", "requirements reviewed")
    store.write_text(task / "artifacts" / "design.md", "design\n")
    store.advance("TASK-1", Outcome.SUCCESS, "claude", "design ready")
    store.advance("TASK-1", Outcome.SKIP, "orchestrator", "design research unavailable")
    store.write_text(task / "reviews" / "design-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "codex", "design reviewed")
    store.write_text(task / "artifacts" / "plan.md", "plan\n")
    store.advance("TASK-1", Outcome.SUCCESS, "codex", "plan ready")
    store.write_text(task / "reviews" / "plan-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "claude", "plan reviewed")


def reach_delivery_gate(store: Store) -> None:
    reach_plan_gate(store)
    store.approve("TASK-1", "plan")
    task = store.task_dir("TASK-1")
    store.write_text(task / "artifacts" / "task-plan.md", "task plan\n")
    store.advance("TASK-1", Outcome.SUCCESS, "codex", "task plan ready")
    store.write_text(task / "reviews" / "task-plan-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "claude", "task plan reviewed")
    store.write_text(task / "evidence" / "implementation.json", "{}\n")
    store.advance("TASK-1", Outcome.SUCCESS, "claude", "implemented")
    store.write_text(task / "reviews" / "code-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "codex", "code reviewed")
    store.write_text(task / "evidence" / "build-test.md", "build ok\n")
    store.advance("TASK-1", Outcome.SUCCESS, "orchestrator", "build passed")
    store.advance("TASK-1", Outcome.SKIP, "orchestrator", "e2e unavailable")
    store.write_text(task / "artifacts" / "delivery-summary.md", "delivery\n")
    store.advance("TASK-1", Outcome.SUCCESS, "codex", "delivery summary ready")
    store.write_text(task / "reviews" / "delivery-review.md", "---\nverdict: approve\n---\n")
    store.advance("TASK-1", Outcome.APPROVE, "claude", "delivery reviewed")


class ReviewOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Review outcome", "claude")
        advance_to_requirements_review(self.store)
        self.store.write_text(
            self.store.task_dir("TASK-1") / "reviews" / "requirements-review.md",
            "---\nverdict: approve\n---\n",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_matching_review_outcome_is_allowed(self):
        verify_review_outcome(self.store, "TASK-1", Outcome.APPROVE)

    def test_mismatched_review_outcome_is_rejected(self):
        with self.assertRaisesRegex(PolicyError, "レビュー判定"):
            verify_review_outcome(self.store, "TASK-1", Outcome.NEEDS_CHANGES)


class JapaneseCliTests(unittest.TestCase):
    def test_root_help_is_japanese(self):
        help_text = parser().format_help()
        self.assertIn("使用方法:", help_text)
        self.assertIn("オプション:", help_text)
        self.assertIn("タスクの状態と次の担当を表示する", help_text)

    def test_plan_and_delivery_are_the_only_human_approval_gates(self):
        arguments = parser().parse_args(["approve", "TASK-1", "plan", "--repo", "."])
        self.assertEqual(arguments.gate, "plan")
        arguments = parser().parse_args(["approve", "TASK-1", "delivery", "--repo", "."])
        self.assertEqual(arguments.gate, "delivery")
        with self.assertRaises(SystemExit):
            parser().parse_args(["approve", "TASK-1", "proposal", "--repo", "."])

    def test_request_change_no_longer_accepts_restart_from(self):
        with self.assertRaises(SystemExit):
            parser().parse_args(
                [
                    "request-change",
                    "TASK-1",
                    "--actor",
                    "claude",
                    "--reason",
                    "method changed",
                    "--restart-from",
                    "proposal",
                ]
            )

    def test_redo_confirmation_rejects_noninteractive_process(self):
        store = Mock()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys.stdin, "isatty", return_value=False),
            self.assertRaisesRegex(PolicyError, "対話端末"),
        ):
            redo_confirmation(store, "TASK-1", "plan", "revise scope")

    def test_redo_confirmation_rejects_agent_process(self):
        store = Mock()
        with (
            patch.dict(os.environ, {"TRIAD_AGENT_RUN": "1"}),
            self.assertRaisesRegex(PolicyError, "AIアダプター"),
        ):
            redo_confirmation(store, "TASK-1", "plan", "revise scope")

    def test_chat_confirmation_is_rejected_inside_agent_adapter(self):
        store = Mock()
        with (
            patch.dict(os.environ, {"TRIAD_AGENT_RUN": "1"}),
            self.assertRaisesRegex(PolicyError, "AIアダプター"),
        ):
            human_confirmation(store, "TASK-1", "plan", "approve TASK-1 plan")


class ChatCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Chat approval", "claude")
        reach_plan_gate(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_exposes_chat_confirmation_action(self):
        actions = human_actions_for(self.store, "TASK-1", self.store.current("TASK-1"))
        self.assertRegex(actions[0]["confirmation"], r"^approve TASK-1 plan [0-9a-f]{16}$")
        self.assertRegex(actions[1]["confirmation"], r"^revise TASK-1 plan [0-9a-f]{16}$")
        self.assertEqual(actions[0]["targets"][0]["path"], "input/intake.md")
        self.assertEqual(actions[0]["targets"][-1]["path"], "reviews/plan-review.md")

    def test_chat_confirmation_is_bound_to_current_artifact_hash(self):
        actions = human_actions_for(self.store, "TASK-1", self.store.current("TASK-1"))
        confirmation = actions[0]["confirmation"]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                human_confirmation(self.store, "TASK-1", "plan", confirmation),
                "vscode-chat",
            )
            self.store.write_text(
                self.store.task_dir("TASK-1") / "artifacts" / "plan.md",
                "# Changed after presentation\n",
            )
            with self.assertRaisesRegex(PolicyError, "確認句が一致しません"):
                human_confirmation(self.store, "TASK-1", "plan", confirmation)

    def test_cli_records_vscode_chat_approval_channel(self):
        confirmation = human_actions_for(
            self.store,
            "TASK-1",
            self.store.current("TASK-1"),
        )[0]["confirmation"]
        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main(
                [
                    "approve",
                    "TASK-1",
                    "plan",
                    "--human-confirmation",
                    confirmation,
                    "--repo",
                    str(self.repo),
                ]
            )

        self.assertEqual(result, 0)
        record_path = next((self.store.task_dir("TASK-1") / "approvals").glob("plan-*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["confirmation_channel"], "vscode-chat")

    def test_cli_records_hash_bound_chat_revision(self):
        action = human_actions_for(
            self.store,
            "TASK-1",
            self.store.current("TASK-1"),
        )[1]
        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main(
                [
                    "advance",
                    "TASK-1",
                    "--outcome",
                    "needs_changes",
                    "--reason",
                    "Narrow the audience",
                    "--human-confirmation",
                    action["confirmation"],
                    "--repo",
                    str(self.repo),
                ]
            )

        self.assertEqual(result, 0)
        feedback = self.store.task_dir("TASK-1") / "input" / "plan-feedback-001.md"
        content = feedback.read_text(encoding="utf-8")
        self.assertIn("`vscode-chat`", content)
        self.assertRegex(content, r"artifacts/plan\.md`: `[0-9a-f]{64}`")

    def test_decision_confirmation_is_bound_to_answer_content(self):
        self.store.approve("TASK-1", "plan")
        decision = self.store.task_dir("TASK-1") / "decisions" / "0001-task-breakdown.json"
        self.store.write_json(
            decision,
            {
                "task_id": "TASK-1",
                "phase": "TASK_BREAKDOWN",
                "status": "pending",
                "questions": ["Choose A or B"],
            },
        )
        current = self.store.current("TASK-1")
        pending_action = human_actions_for(self.store, "TASK-1", current)[0]
        self.assertNotIn("confirmation", pending_action)
        self.assertEqual(pending_action["answers_required"], 1)

        answer = "Choose B"
        action = human_actions_for(self.store, "TASK-1", current, [answer])[0]
        self.assertEqual(action["targets"][-1]["path"], "<human-answers>")
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(PolicyError, "確認句が一致しません"),
        ):
            decision_confirmation(
                self.store,
                "TASK-1",
                ["Choose A"],
                action["confirmation"],
            )

        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main(
                [
                    "decide",
                    "TASK-1",
                    "--answer",
                    answer,
                    "--human-confirmation",
                    action["confirmation"],
                    "--repo",
                    str(self.repo),
                ]
            )
        self.assertEqual(result, 0)
        record = json.loads(decision.read_text(encoding="utf-8"))
        self.assertEqual(record["answers"][0]["answer"], answer)
        self.assertEqual(record["confirmation_channel"], "vscode-chat")


class DeliveryGateRedoConfirmationTests(unittest.TestCase):
    """AWAITING_DELIVERY_APPROVAL（成果物完成確認）の作り直しループも、
    AWAITING_PLAN_APPROVALと同じ確認句機構で保護されることを検証する。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Delivery redo", "claude")
        reach_delivery_gate(self.store)
        self.assertEqual(self.store.current("TASK-1"), State.AWAITING_DELIVERY_APPROVAL)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_exposes_delivery_redo_action(self):
        actions = human_actions_for(self.store, "TASK-1", self.store.current("TASK-1"))
        self.assertEqual(actions[0]["action"], "approve")
        self.assertEqual(actions[0]["gate"], "delivery")
        self.assertEqual(actions[1]["action"], "request_revision")
        self.assertRegex(actions[1]["confirmation"], r"^revise TASK-1 delivery [0-9a-f]{16}$")

    def test_cli_rebuilds_via_fix_with_chat_confirmation(self):
        action = human_actions_for(self.store, "TASK-1", self.store.current("TASK-1"))[1]
        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main(
                [
                    "advance",
                    "TASK-1",
                    "--outcome",
                    "needs_changes",
                    "--reason",
                    "ボタンの配置を修正してほしい",
                    "--human-confirmation",
                    action["confirmation"],
                    "--repo",
                    str(self.repo),
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(self.store.current("TASK-1"), State.FIX)
        feedback = self.store.task_dir("TASK-1") / "input" / "delivery-feedback-001.md"
        self.assertIn("`vscode-chat`", feedback.read_text(encoding="utf-8"))
        self.assertIn("ボタンの配置", feedback.read_text(encoding="utf-8"))


class HumanConfirmationScopeTests(unittest.TestCase):
    """--human-confirmationは差し戻し・作り直しのNEEDS_CHANGES以外には使えない。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Scope check", "claude")

    def tearDown(self):
        self.temp.cleanup()

    def test_human_confirmation_is_rejected_for_ordinary_transitions(self):
        self.store.write_text(self.store.task_dir("TASK-1") / "input" / "intake.md", "brief\n")
        with (
            patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = main(
                [
                    "advance",
                    "TASK-1",
                    "--outcome",
                    "success",
                    "--human-confirmation",
                    "revise TASK-1 plan deadbeefdeadbeef",
                    "--repo",
                    str(self.repo),
                ]
            )
        self.assertEqual(result, 2)
        self.assertEqual(self.store.current("TASK-1"), State.INTAKE)


if __name__ == "__main__":
    unittest.main()
