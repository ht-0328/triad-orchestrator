import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from triad.model import Outcome, State
from triad.policy import PolicyError
from triad.store import Store


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Test workflow", "claude")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, content: str = "evidence\n") -> None:
        self.store.write_text(self.store.task_dir("TASK-1") / relative, content)

    def reach_plan_approval(self) -> None:
        self.write("input/intake.md", "# 調査・協議ブリーフ\n")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "brief ready")
        self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "Antigravity unavailable")
        self.write("artifacts/solution-proposal.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "claude", "proposal ready")
        self.write("reviews/proposal-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "codex", "proposal reviewed")
        self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "Antigravity unavailable")
        self.write("artifacts/synthesis.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "synthesis ready")
        self.write("reviews/synthesis-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "claude", "synthesis reviewed")
        self.write("artifacts/requirements.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "claude", "requirements ready")
        self.write("reviews/requirements-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "codex", "requirements reviewed")
        self.write("artifacts/design.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "claude", "design ready")
        self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "Antigravity unavailable")
        self.write("reviews/design-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "codex", "design reviewed")
        self.write("artifacts/plan.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "plan ready")
        self.write("reviews/plan-review.md", "---\nverdict: approve\n---\n")
        state = self.store.advance("TASK-1", Outcome.APPROVE, "claude", "plan reviewed")
        self.assertEqual(state["state"], State.AWAITING_PLAN_APPROVAL.value)

    def approve_plan(self) -> None:
        self.reach_plan_approval()
        self.store.approve("TASK-1", "plan")

    def reach_delivery_approval(self) -> None:
        self.approve_plan()
        self.write("artifacts/task-plan.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "task plan ready")
        self.write("reviews/task-plan-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "claude", "task plan reviewed")
        self.write("evidence/implementation.json", "{}\n")
        self.store.advance("TASK-1", Outcome.SUCCESS, "claude", "implemented")
        self.write("reviews/code-review.md", "---\nverdict: approve\n---\n")
        self.store.advance("TASK-1", Outcome.APPROVE, "codex", "code reviewed")
        self.write("evidence/build-test.md", "build ok\n")
        self.store.advance("TASK-1", Outcome.SUCCESS, "orchestrator", "build passed")
        self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "Antigravity unavailable")
        self.write("artifacts/delivery-summary.md")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "delivery summary ready")
        self.write("reviews/delivery-review.md", "---\nverdict: approve\n---\n")
        state = self.store.advance("TASK-1", Outcome.APPROVE, "claude", "delivery reviewed")
        self.assertEqual(state["state"], State.AWAITING_DELIVERY_APPROVAL.value)

    def test_explicit_degraded_state_is_recorded(self):
        self.write("input/intake.md", "# 調査・協議ブリーフ\n")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "brief ready")
        state = self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "OAuth pending")
        self.assertEqual(state["state"], State.SOLUTION_PROPOSAL.value)
        self.assertEqual(state["degraded"][0]["phase"], State.RESEARCH.value)
        stub = self.store.task_dir("TASK-1") / "artifacts" / "research.md"
        self.assertIn("SKIPPED", stub.read_text(encoding="utf-8"))

    def test_plan_approval_freezes_all_fourteen_targets(self):
        self.reach_plan_approval()
        state = self.store.approve("TASK-1", "plan")
        self.assertEqual(state["state"], State.TASK_BREAKDOWN.value)
        approval_path = self.store.root / state["approvals"]["plan"]
        record = json.loads(approval_path.read_text(encoding="utf-8"))
        self.assertEqual(len(record["targets"]), 14)

        self.write("artifacts/requirements.md", "silently changed\n")
        with self.assertRaisesRegex(PolicyError, "承認済み成果物が変更されています"):
            self.store.load_state("TASK-1")

    def test_change_request_is_rejected_before_plan_approval(self):
        self.reach_plan_approval()
        with self.assertRaisesRegex(PolicyError, "変更要求を作成することはできません"):
            self.store.request_change("TASK-1", "claude", "too early")

    def test_change_request_after_plan_approval_restarts_deliberation(self):
        self.approve_plan()
        request = self.store.request_change("TASK-1", "claude", "external constraint changed")
        self.assertTrue(request.is_file())
        state = self.store.approve_change("TASK-1")
        self.assertEqual(state["state"], State.INTAKE.value)
        self.assertEqual(state["approvals"], {})
        self.assertTrue(state["superseded_approvals"])

    def test_invalid_transition_is_rejected(self):
        with self.assertRaises(PolicyError):
            self.store.advance("TASK-1", Outcome.APPROVE, "codex", "invalid")

    def test_intake_continues_to_research_without_human_approval(self):
        self.write("input/intake.md", "# 調査・協議ブリーフ\n")
        state = self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "brief ready")
        self.assertEqual(state["state"], State.RESEARCH.value)

    def test_plan_requires_human_approval_and_can_be_revised(self):
        self.reach_plan_approval()

        state = self.store.advance(
            "TASK-1",
            Outcome.NEEDS_CHANGES,
            "human",
            "対象利用者と優先順位を具体化する",
        )
        self.assertEqual(state["state"], State.INTAKE.value)
        feedback = self.store.task_dir("TASK-1") / "input" / "plan-feedback-001.md"
        self.assertIn("対象利用者", feedback.read_text(encoding="utf-8"))
        self.assertIn("artifacts/plan.md", feedback.read_text(encoding="utf-8"))

    def test_plan_approval_freezes_deliberation_artifacts(self):
        self.approve_plan()
        self.write("artifacts/solution-proposal.md", "silently changed\n")
        with self.assertRaisesRegex(PolicyError, "承認済み成果物が変更されています"):
            self.store.load_state("TASK-1")

    def test_delivery_needs_changes_returns_to_fix_without_reopening_plan(self):
        self.reach_delivery_approval()
        state = self.store.advance("TASK-1", Outcome.NEEDS_CHANGES, "human", "レイアウト崩れを修正")
        self.assertEqual(state["state"], State.FIX.value)
        self.assertIn("plan", state["approvals"])
        feedback = self.store.task_dir("TASK-1") / "input" / "delivery-feedback-001.md"
        self.assertIn("レイアウト崩れ", feedback.read_text(encoding="utf-8"))

    def test_delivery_approval_freezes_build_evidence(self):
        self.reach_delivery_approval()
        state = self.store.approve("TASK-1", "delivery")
        self.assertEqual(state["state"], State.DELIVERED.value)
        self.write("evidence/build-test.md", "silently changed\n")
        with self.assertRaisesRegex(PolicyError, "承認済み成果物が変更されています"):
            self.store.load_state("TASK-1")

    def test_pending_human_decision_blocks_transition_until_resolved(self):
        self.write("input/intake.md", "# 調査・協議ブリーフ\n")
        self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "brief ready")
        self.store.advance("TASK-1", Outcome.SKIP, "orchestrator", "OAuth pending")
        self.write("artifacts/solution-proposal.md")
        decision = self.store.task_dir("TASK-1") / "decisions" / "0001-solution-proposal.json"
        self.store.write_json(
            decision,
            {
                "task_id": "TASK-1",
                "phase": State.SOLUTION_PROPOSAL.value,
                "status": "pending",
                "questions": ["Choose A or B"],
            },
        )
        with self.assertRaisesRegex(PolicyError, "未解決の人間判断があります"):
            self.store.advance("TASK-1", Outcome.SUCCESS, "codex", "premature")
        self.store.resolve_decisions("TASK-1", ["Choose B"])
        state = self.store.advance("TASK-1", Outcome.SUCCESS, "claude", "decision recorded")
        self.assertEqual(state["state"], State.PROPOSAL_REVIEW.value)

    def test_concurrent_advance_on_same_task_is_serialized(self):
        self.reach_plan_approval()
        history_path = self.store.task_dir("TASK-1") / "history.jsonl"
        lines_before = len(history_path.read_text(encoding="utf-8").splitlines())

        def attempt():
            return self.store.advance("TASK-1", Outcome.NEEDS_CHANGES, "human", "concurrent redo")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = []
            for future in futures:
                try:
                    results.append(("ok", future.result()))
                except PolicyError as error:
                    results.append(("error", str(error)))

        outcomes = [kind for kind, _ in results]
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(outcomes.count("error"), 1)
        lines_after = len(history_path.read_text(encoding="utf-8").splitlines())
        self.assertEqual(lines_after - lines_before, 1)

    def test_guidance_is_snapshotted_once_and_hash_verified(self):
        knowledge = self.repo / "platform-knowledge"
        templates = self.repo / "platform-templates"
        knowledge.mkdir()
        templates.mkdir()
        (knowledge / "rules.md").write_text("# ルール\n", encoding="utf-8")
        (templates / "design.md").write_text("# 設計\n", encoding="utf-8")

        manifest = self.store.install_guidance({"knowledge": knowledge, "templates": templates})
        copied = self.store.meta / "guidance" / "knowledge" / "rules.md"
        self.assertEqual(copied.read_text(encoding="utf-8"), "# ルール\n")
        (knowledge / "rules.md").write_text("# 更新後\n", encoding="utf-8")
        self.store.install_guidance({"knowledge": knowledge, "templates": templates})
        self.assertEqual(copied.read_text(encoding="utf-8"), "# ルール\n")

        added = self.store.meta / "guidance" / "knowledge" / "追加.md"
        added.write_text("追加\n", encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "ファイル構成が変更されています"):
            self.store.load_state("TASK-1")
        added.unlink()

        copied.unlink()
        with self.assertRaisesRegex(PolicyError, "ファイル構成が変更されています"):
            self.store.load_state("TASK-1")
        copied.write_text("# ルール\n", encoding="utf-8")

        copied.write_text("改ざん\n", encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "固定済みガイダンスが変更されています"):
            self.store.install_guidance({"knowledge": knowledge, "templates": templates})
        with self.assertRaisesRegex(PolicyError, "固定済みガイダンスが変更されています"):
            self.store.load_state("TASK-1")
        self.assertTrue(manifest.is_file())

    def test_guidance_source_symlink_is_rejected(self):
        actual = self.repo / "actual-guidance"
        actual.mkdir()
        (actual / "rules.md").write_text("# ルール\n", encoding="utf-8")
        symbolic = self.repo / "symbolic-guidance"
        symbolic.symlink_to(actual, target_is_directory=True)

        with self.assertRaisesRegex(PolicyError, "シンボリックリンク"):
            self.store.install_guidance({"knowledge": symbolic})

    def test_chat_interface_preserves_existing_agent_instructions_and_is_idempotent(self):
        source = self.repo / "chat-source"
        source.mkdir()
        (source / "chat-interface.md").write_text("# VS Code chat\n", encoding="utf-8")
        for filename in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            (source / filename).write_text(
                "<!-- triad-chat-interface -->\nRead the shared guide.\n",
                encoding="utf-8",
            )
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Existing rules\n", encoding="utf-8")

        self.store.install_chat_interface(source)
        self.store.install_chat_interface(source)

        content = agents.read_text(encoding="utf-8")
        self.assertIn("# Existing rules", content)
        self.assertEqual(content.count("<!-- triad-chat-interface -->"), 1)
        self.assertTrue((self.repo / "CLAUDE.md").is_file())
        self.assertTrue((self.repo / "GEMINI.md").is_file())
        self.assertTrue((self.store.meta / "chat-interface.md").is_file())

    def test_initialize_registers_task_lock_in_gitignore(self):
        gitignore = self.repo / ".gitignore"
        self.assertTrue(gitignore.is_file())
        self.assertIn(".ai-dev/tasks/*/.lock", gitignore.read_text(encoding="utf-8").splitlines())

    def test_initialize_appends_task_lock_entry_to_existing_gitignore(self):
        temp = tempfile.TemporaryDirectory()
        try:
            repo = Path(temp.name)
            git(repo, "init", "-b", "main")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")
            (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            git(repo, "add", "README.md", ".gitignore")
            git(repo, "commit", "-m", "initial")
            store = Store(repo)
            store.initialize("TASK-1", "Test", "claude")
            content = (repo / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("node_modules/", content)
            self.assertIn(".ai-dev/tasks/*/.lock", content.splitlines())
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
