import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from triad.adapters import RunResult
from triad.model import State
from triad.policy import PolicyError
from triad.runner import Runner
from triad.store import Store


PLATFORM_ROOT = Path(__file__).resolve().parent.parent


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class RunnerProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "AGENTS.md").write_text("protected\n", encoding="utf-8")
        git(self.repo, "add", "AGENTS.md")
        git(self.repo, "commit", "-m", "initial")
        self.store = Store(self.repo)
        self.store.initialize("TASK-1", "Protection", "claude")
        self.runner = Runner(self.store, PLATFORM_ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def test_protected_snapshot_is_restored_without_touching_source(self):
        before = self.runner._snapshot_protected()
        state_path = self.store.task_dir("TASK-1") / "state.json"
        state_path.write_text("corrupted\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("corrupted\n", encoding="utf-8")
        source = self.repo / "src.py"
        source.write_text("useful partial change\n", encoding="utf-8")

        violations = self.runner._protected_drift(before)
        self.assertIn("AGENTS.md", violations)
        self.assertTrue(any(path.endswith("state.json") for path in violations))
        self.runner._restore_protected(before)

        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), "protected\n")
        self.assertTrue(state_path.read_text(encoding="utf-8").startswith("{"))
        self.assertEqual(source.read_text(encoding="utf-8"), "useful partial change\n")

    def test_agent_questions_create_pending_decision_record(self):
        artifact = self.store.task_dir("TASK-1") / "artifacts" / "solution-proposal.md"
        self.store.write_text(artifact, "evidence\n")
        decision = self.runner._record_decisions(
            "TASK-1",
            State.SOLUTION_PROPOSAL,
            artifact,
            {"human_decisions": ["Choose a boundary"]},
        )
        self.assertIsNotNone(decision)
        self.assertEqual(len(self.store.pending_decisions("TASK-1")), 1)

    def test_workspace_run_applies_only_source_patch_from_disposable_clone(self):
        git(self.repo, "add", ".ai-dev", ".gitignore")
        git(self.repo, "commit", "-m", "workflow")

        class SourceOnlyAdapter:
            def run_workspace(inner_self, agent, prompt, cwd, timeout):
                (cwd / "src.py").write_text("isolated change\n", encoding="utf-8")
                return RunResult(agent, 0, 0.1, None, "", "")

        self.runner.adapter = SourceOnlyAdapter()
        output = self.runner._workspace_run(
            "TASK-1", State.IMPLEMENTATION, "claude", "test", 10, "evidence/implementation.json"
        )
        self.assertEqual((self.repo / "src.py").read_text(encoding="utf-8"), "isolated change\n")
        self.assertTrue(output.is_file())

    def test_workspace_run_discards_patch_if_clone_touches_protected_file(self):
        git(self.repo, "add", ".ai-dev", ".gitignore")
        git(self.repo, "commit", "-m", "workflow")

        class ViolatingAdapter:
            def run_workspace(inner_self, agent, prompt, cwd, timeout):
                (cwd / "AGENTS.md").write_text("corrupted\n", encoding="utf-8")
                (cwd / "src.py").write_text("must not be applied\n", encoding="utf-8")
                return RunResult(agent, 0, 0.1, None, "", "")

        self.runner.adapter = ViolatingAdapter()
        with self.assertRaisesRegex(PolicyError, "使い捨てクローン"):
            self.runner._workspace_run(
                "TASK-1", State.IMPLEMENTATION, "claude", "test", 10, "evidence/implementation.json"
            )
        self.assertEqual((self.repo / "AGENTS.md").read_text(encoding="utf-8"), "protected\n")
        self.assertFalse((self.repo / "src.py").exists())

    def test_workspace_run_discards_partial_patch_on_agent_failure(self):
        git(self.repo, "add", ".ai-dev", ".gitignore")
        git(self.repo, "commit", "-m", "workflow")

        class FailingAdapter:
            def run_workspace(inner_self, agent, prompt, cwd, timeout):
                (cwd / "src.py").write_text("partial\n", encoding="utf-8")
                return RunResult(agent, 1, 0.1, None, "", "failed")

        self.runner.adapter = FailingAdapter()
        with self.assertRaisesRegex(PolicyError, "失敗"):
            self.runner._workspace_run(
                "TASK-1", State.IMPLEMENTATION, "claude", "test", 10, "evidence/implementation.json"
            )
        self.assertFalse((self.repo / "src.py").exists())

    def test_compose_preflight_rejects_external_resolution_features(self):
        for key in ("include", "extends", "env_file"):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "compose.yaml").write_text(
                    f"services:\n  test:\n    {key}: /outside/file.yaml\n",
                    encoding="utf-8",
                )
                with self.assertRaises(PolicyError):
                    self.runner._preflight_compose_files(root)

    def test_compose_preflight_rejects_coexisting_override_file(self):
        for override_name in (
            "compose.override.yaml",
            "compose.override.yml",
            "docker-compose.override.yml",
            "docker-compose.override.yaml",
        ):
            with self.subTest(override_name=override_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "compose.yaml").write_text("services:\n  test:\n    image: alpine\n", encoding="utf-8")
                (root / override_name).write_text("services:\n  test:\n    privileged: true\n", encoding="utf-8")
                with self.assertRaisesRegex(PolicyError, "オーバーライドファイル"):
                    self.runner._preflight_compose_files(root)

    def test_handoff_includes_guidance_hashes(self):
        knowledge = self.repo / "platform-knowledge"
        knowledge.mkdir()
        (knowledge / "rules.md").write_text("# ルール\n", encoding="utf-8")
        self.store.install_guidance({"knowledge": knowledge})

        handoff = self.runner._write_handoff("TASK-1", State.SYNTHESIS, "codex", "artifacts/synthesis.md", 30)
        record = json.loads(handoff.read_text(encoding="utf-8"))
        relative = ".ai-dev/guidance/knowledge/rules.md"
        self.assertIn(relative, record["inputs"])
        self.assertIn(relative, record["input_sha256"])

    def test_deliberation_prompt_defers_questions_until_integrated_proposal(self):
        prompt = self.runner._prompt(
            "TASK-1",
            State.SOLUTION_PROPOSAL,
            "claude",
            "複数案を作る",
            False,
            False,
        )
        self.assertIn("3AIの協議が終わる前に追加質問で停止しない", prompt)
        self.assertIn("human_decisionsは空", prompt)

    def test_deliberation_rejects_agent_questions_even_if_prompt_is_ignored(self):
        class QuestioningAdapter:
            @staticmethod
            def available(agent):
                return True

            @staticmethod
            def run_artifact(agent, prompt, cwd, timeout):
                return RunResult(
                    agent,
                    0,
                    0.1,
                    {
                        "summary": "question",
                        "content": "# Brief\n",
                        "verdict": "not_applicable",
                        "human_decisions": ["Choose A or B"],
                    },
                    "",
                    "",
                )

            @staticmethod
            def audit_record(task_id, phase, result, prompt):
                return {"task_id": task_id, "phase": phase}

        self.runner.adapter = QuestioningAdapter()
        with self.assertRaisesRegex(PolicyError, "3AIの協議中は人間判断で停止できません"):
            self.runner.run("TASK-1")
        self.assertEqual(self.store.pending_decisions("TASK-1"), [])


if __name__ == "__main__":
    unittest.main()
