from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .policy import PolicyError, scrub_environment, sha256_bytes


MAX_CAPTURE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class RunResult:
    agent: str
    exit_code: int
    duration_seconds: float
    payload: dict[str, Any] | None
    stdout: str
    stderr: str
    timed_out: bool = False


class Adapter:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.schema_path = project_root / "contracts" / "agent-output.schema.json"
        self.schema = json.loads(self.schema_path.read_text(encoding="utf-8"))

    def available(self, agent: str) -> bool:
        return shutil.which(self._executable(agent)) is not None

    def run_artifact(self, agent: str, prompt: str, cwd: Path, timeout: int) -> RunResult:
        output_file: Path | None = None
        if agent == "codex":
            handle = tempfile.NamedTemporaryFile(prefix="triad-codex-", suffix=".json", delete=False)
            handle.close()
            output_file = Path(handle.name)
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--output-schema",
                str(self.schema_path),
                "--output-last-message",
                str(output_file),
                prompt,
            ]
        elif agent == "claude":
            claude_schema = {key: value for key, value in self.schema.items() if key != "$schema"}
            command = [
                "claude",
                "-p",
                "--permission-mode",
                "plan",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(claude_schema, separators=(",", ":")),
                "--max-turns",
                "12",
                "--no-session-persistence",
                prompt,
            ]
        elif agent == "antigravity":
            command = [
                "agy",
                "-p",
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--output-format",
                "json",
                "--json-schema",
                str(self.schema_path),
                "--print-timeout",
                f"{timeout}s",
                prompt,
            ]
        else:
            raise PolicyError(f"不明なAIです: {agent}")
        result = self._run(agent, command, prompt, cwd, timeout)
        payload = None
        try:
            if output_file is not None and output_file.stat().st_size:
                payload = self._extract_payload(output_file.read_text(encoding="utf-8"))
            if payload is None:
                payload = self._extract_payload(result.stdout)
        finally:
            if output_file is not None:
                output_file.unlink(missing_ok=True)
        return RunResult(**{**result.__dict__, "payload": payload})

    def run_workspace(self, agent: str, prompt: str, cwd: Path, timeout: int) -> RunResult:
        if agent == "claude":
            command = [
                "claude",
                "-p",
                "--permission-mode",
                "acceptEdits",
                "--output-format",
                "json",
                "--max-turns",
                "40",
                "--no-session-persistence",
                "--allowedTools",
                "Read",
                "Edit",
                "Write",
                "Glob",
                "Grep",
                "Bash(git diff:*)",
                "Bash(git status:*)",
                prompt,
            ]
        elif agent == "codex":
            # --approve-for-meはそれ自体がworkspace-writeサンドボックスを意味し、
            # 明示的な--sandboxとは併用できない（指定すると引数エラーになる）。
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--approve-for-me",
                "-c",
                "sandbox_workspace_write.network_access=false",
                "-c",
                'web_search="disabled"',
                prompt,
            ]
        else:
            raise PolicyError(f"AI {agent}にはソースを変更する権限がありません")
        return self._run(agent, command, prompt, cwd, timeout)

    def _run(self, agent: str, command: list[str], prompt: str, cwd: Path, timeout: int) -> RunResult:
        if not self.available(agent):
            raise PolicyError(f"CLIがインストールされていません: {self._executable(agent)}")
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=scrub_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout_raw, stderr_raw = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout_raw, stderr_raw = process.communicate()
        duration = time.monotonic() - started
        stdout = stdout_raw[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_raw[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace")
        return RunResult(
            agent=agent,
            exit_code=process.returncode if process.returncode is not None else 1,
            duration_seconds=duration,
            payload=None,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )

    @staticmethod
    def _extract_payload(raw: str) -> dict[str, Any] | None:
        raw = raw.strip()
        if not raw:
            return None
        candidates: list[Any] = []
        try:
            candidates.append(json.loads(raw))
        except json.JSONDecodeError:
            for line in reversed(raw.splitlines()):
                try:
                    candidates.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        while candidates:
            value = candidates.pop(0)
            if isinstance(value, dict):
                if {"summary", "content", "verdict", "human_decisions"} <= value.keys():
                    return value if Adapter._valid_payload(value) else None
                for key in ("structured_output", "result", "output", "message"):
                    nested = value.get(key)
                    if isinstance(nested, (dict, list)):
                        candidates.append(nested)
                    elif isinstance(nested, str):
                        try:
                            candidates.append(json.loads(nested))
                        except json.JSONDecodeError:
                            pass
            elif isinstance(value, list):
                candidates.extend(reversed(value))
        return None

    @staticmethod
    def _valid_payload(value: dict[str, Any]) -> bool:
        if set(value) != {"summary", "content", "verdict", "human_decisions"}:
            return False
        if not isinstance(value["summary"], str) or not value["summary"].strip():
            return False
        if not isinstance(value["content"], str) or not value["content"].strip():
            return False
        if value["verdict"] not in {"not_applicable", "approve", "needs_changes"}:
            return False
        decisions = value["human_decisions"]
        return isinstance(decisions, list) and all(isinstance(item, str) for item in decisions)

    @staticmethod
    def audit_record(task_id: str, phase: str, result: RunResult, prompt: str) -> dict[str, Any]:
        return {
            "at": datetime.now(UTC).isoformat(),
            "task_id": task_id,
            "phase": phase,
            "agent": result.agent,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "exit_code": result.exit_code,
            "duration_seconds": round(result.duration_seconds, 3),
            "timed_out": result.timed_out,
            "structured_output": result.payload is not None,
        }

    @staticmethod
    def _executable(agent: str) -> str:
        return {"codex": "codex", "claude": "claude", "antigravity": "agy"}.get(agent, agent)
