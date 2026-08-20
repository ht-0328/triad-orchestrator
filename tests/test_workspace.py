import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PLATFORM_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_SCRIPT = PLATFORM_ROOT / "bin" / "triad-workspace"
OPEN_SCRIPT = PLATFORM_ROOT / "bin" / "triad-open-workspace"
FAKE_BIN = PLATFORM_ROOT / "tests" / "fixtures" / "bin"


def path_without_code() -> str:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(
        entry for entry in entries if entry and not (Path(entry) / "code").is_file()
    )


def run(script: Path, *arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *arguments],
        cwd=PLATFORM_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class WorkspaceGenerationTests(unittest.TestCase):
    def environment(self, path: str | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        if path is not None:
            environment["PATH"] = path
        return environment

    def test_creates_workspace_file_with_correct_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())

            self.assertEqual(result.returncode, 0, result.stderr)
            workspace_file = app_dir / "todo-app.code-workspace"
            self.assertTrue(workspace_file.is_file())
            document = json.loads(workspace_file.read_text(encoding="utf-8"))
            self.assertEqual(document["folders"][0], {"name": "todo-app", "path": "."})
            self.assertEqual(document["folders"][1]["name"], PLATFORM_ROOT.name)
            resolved_platform = (app_dir / document["folders"][1]["path"]).resolve()
            self.assertEqual(resolved_platform, PLATFORM_ROOT.resolve())

    def test_custom_name_overrides_basename(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()

            result = run(
                WORKSPACE_SCRIPT,
                "--app-dir",
                str(app_dir),
                "--name",
                "custom-name",
                env=self.environment(),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((app_dir / "custom-name.code-workspace").is_file())
            self.assertFalse((app_dir / "todo-app.code-workspace").exists())

    def test_rejects_existing_file_without_force_then_force_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()
            run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())
            self.assertEqual(result.returncode, 2)
            self.assertIn("既に", result.stderr)

            result = run(
                WORKSPACE_SCRIPT, "--app-dir", str(app_dir), "--force", env=self.environment()
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_symlink_destination_even_with_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()
            marker = Path(temporary) / "outside-marker.txt"
            marker.write_text("untouched\n", encoding="utf-8")
            symlink = app_dir / "todo-app.code-workspace"
            symlink.symlink_to(marker)

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())
            self.assertEqual(result.returncode, 2)

            result = run(
                WORKSPACE_SCRIPT, "--app-dir", str(app_dir), "--force", env=self.environment()
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("シンボリックリンク", result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched\n")

    def test_rejects_dangling_symlink_destination_even_with_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()
            nonexistent_target = Path(temporary) / "does-not-exist.txt"
            symlink = app_dir / "todo-app.code-workspace"
            symlink.symlink_to(nonexistent_target)

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())
            self.assertEqual(result.returncode, 2)

            result = run(
                WORKSPACE_SCRIPT, "--app-dir", str(app_dir), "--force", env=self.environment()
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("シンボリックリンク", result.stderr)
            self.assertFalse(nonexistent_target.exists())

    def test_rejects_invalid_default_name_but_accepts_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "bad name!"
            app_dir.mkdir()

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), env=self.environment())
            self.assertEqual(result.returncode, 2)
            self.assertIn("ワークスペース名", result.stderr)

            result = run(
                WORKSPACE_SCRIPT,
                "--app-dir",
                str(app_dir),
                "--name",
                "fixed-name",
                env=self.environment(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((app_dir / "fixed-name.code-workspace").is_file())

    def test_rejects_app_dir_inside_platform_dir(self):
        result = run(
            WORKSPACE_SCRIPT,
            "--app-dir",
            str(PLATFORM_ROOT / "bin"),
            env=self.environment(),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("triad-orchestratorの外", result.stderr)

    def test_rejects_platform_dir_inside_app_dir(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            nested_platform = app_dir / "nested-platform"
            nested_platform.mkdir()

            result = run(
                WORKSPACE_SCRIPT,
                "--app-dir",
                str(app_dir),
                "--platform-dir",
                str(nested_platform),
                env=self.environment(),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("--platform-dir", result.stderr)

    def test_open_flag_launches_code_via_wrapper_when_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()
            record_file = Path(temporary) / "code-invocation.txt"

            environment = self.environment(path=f"{FAKE_BIN}:{os.environ['PATH']}")
            environment["FAKE_CODE_RECORD"] = str(record_file)

            result = run(WORKSPACE_SCRIPT, "--app-dir", str(app_dir), "--open", env=environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(record_file.is_file())
            self.assertIn("todo-app.code-workspace", record_file.read_text(encoding="utf-8"))

    def test_open_flag_fails_clearly_without_code_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary) / "todo-app"
            app_dir.mkdir()

            result = run(
                WORKSPACE_SCRIPT,
                "--app-dir",
                str(app_dir),
                "--open",
                env=self.environment(path=path_without_code()),
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("codeコマンドが見つかりません", result.stderr)


class OpenWorkspaceWrapperTests(unittest.TestCase):
    def environment(self, path: str | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        if path is not None:
            environment["PATH"] = path
        return environment

    def test_rejects_non_workspace_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "not-a-workspace.txt"
            target.write_text("{}", encoding="utf-8")

            result = run(OPEN_SCRIPT, str(target), env=self.environment())
            self.assertEqual(result.returncode, 2)
            self.assertIn("*.code-workspace", result.stderr)

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            real_file = Path(temporary) / "real.code-workspace"
            real_file.write_text("{}", encoding="utf-8")
            symlink = Path(temporary) / "link.code-workspace"
            symlink.symlink_to(real_file)

            result = run(OPEN_SCRIPT, str(symlink), env=self.environment())
            self.assertEqual(result.returncode, 2)
            self.assertIn("シンボリックリンク", result.stderr)

    def test_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "broken.code-workspace"
            target.write_text("{not valid json", encoding="utf-8")

            result = run(OPEN_SCRIPT, str(target), env=self.environment())
            self.assertEqual(result.returncode, 2)
            self.assertIn("JSON", result.stderr)

    def test_opens_valid_workspace_file_via_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "valid.code-workspace"
            target.write_text(
                json.dumps({"folders": [{"path": "."}]}), encoding="utf-8"
            )
            record_file = Path(temporary) / "code-invocation.txt"

            environment = self.environment(path=f"{FAKE_BIN}:{os.environ['PATH']}")
            environment["FAKE_CODE_RECORD"] = str(record_file)

            result = run(OPEN_SCRIPT, str(target), env=environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(target), record_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
