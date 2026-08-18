import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PLATFORM_ROOT / "bin" / "triad-new"
FAKE_BIN = PLATFORM_ROOT / "tests" / "fixtures" / "bin"


class NewProjectScriptTests(unittest.TestCase):
    def environment(self, executable_directory: Path | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Triad Test",
                "GIT_AUTHOR_EMAIL": "triad-test@example.invalid",
                "GIT_COMMITTER_NAME": "Triad Test",
                "GIT_COMMITTER_EMAIL": "triad-test@example.invalid",
            }
        )
        if executable_directory is not None:
            environment["PATH"] = f"{executable_directory}:{environment['PATH']}"
        return environment

    def run_script(
        self, *arguments: str, executable_directory: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SCRIPT), *arguments],
            cwd=PLATFORM_ROOT,
            env=self.environment(executable_directory),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_creates_separate_git_repository_and_first_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_script(
                "--parent",
                temporary,
                "--name",
                "sample-app",
                "--task-id",
                "DEMO-001",
                "--title",
                "TODOアプリを作る",
                "--yes",
                executable_directory=FAKE_BIN,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            project = Path(temporary) / "sample-app"
            self.assertTrue((project / ".git").is_dir())
            self.assertTrue((project / "README.md").is_file())
            self.assertTrue((project / "AGENTS.md").is_file())
            self.assertTrue((project / "CLAUDE.md").is_file())
            self.assertTrue((project / "GEMINI.md").is_file())
            chat_interface = project / ".ai-dev" / "chat-interface.md"
            self.assertIn("VS Code", chat_interface.read_text(encoding="utf-8"))
            state_path = project / ".ai-dev" / "tasks" / "DEMO-001" / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "RESEARCH")
            self.assertEqual(state["implementation_author"], "claude")
            intake = project / ".ai-dev" / "tasks" / "DEMO-001" / "input" / "intake.md"
            self.assertIn("調査・協議ブリーフ", intake.read_text(encoding="utf-8"))

            log = subprocess.run(
                ["git", "-C", str(project), "log", "--format=%s%n%b"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn("chore: プロジェクトを初期化", log)
            self.assertIn("docs(ai-dev): DEMO-001の調査ブリーフを作成", log)
            self.assertIn("Triad-Task: DEMO-001", log)
            self.assertIn("Produced-By: triad-orchestrator", log)
            self.assertIn("Produced-By: codex", log)
            status = subprocess.run(
                ["git", "-C", str(project), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertEqual(status, "")
            self.assertIn("調査・協議ブリーフを作成しました", result.stdout)
            self.assertIn("VS Codeチャット", result.stdout)
            self.assertIn("この時点では人間の承認は求めません", result.stdout)

    def test_can_initialize_without_running_ai_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_script(
                "--parent",
                temporary,
                "--name",
                "manual-plan-app",
                "--title",
                "後から初期計画を作る",
                "--no-plan",
                "--yes",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            task = Path(temporary) / "manual-plan-app" / ".ai-dev" / "tasks" / "APP-001"
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "INTAKE")
            self.assertTrue((task / "input" / "brief.md").is_file())
            self.assertFalse((task / "input" / "intake.md").exists())
            self.assertIn(" run ", result.stdout)

    def test_rejects_project_inside_orchestrator_repository(self):
        result = self.run_script(
            "--parent",
            str(PLATFORM_ROOT),
            "--name",
            "unsafe-bootstrap-test",
            "--title",
            "作成してはならない",
            "--no-plan",
            "--yes",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("triad-orchestratorの外", result.stderr)
        self.assertFalse((PLATFORM_ROOT / "unsafe-bootstrap-test").exists())

    def test_rejects_existing_target_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "existing-app"
            project.mkdir()
            marker = project / "keep.txt"
            marker.write_text("unchanged\n", encoding="utf-8")

            result = self.run_script(
                "--parent",
                temporary,
                "--name",
                project.name,
                "--title",
                "既存ディレクトリを上書きしない",
                "--no-plan",
                "--yes",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("すでに存在", result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged\n")


if __name__ == "__main__":
    unittest.main()
