from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import GATE_TARGETS, HUMAN_GATES, REVISION_GATES, Outcome, REQUIRED_ON_EXIT, State, TRANSITIONS
from .policy import PolicyError, reject_secrets, sha256_file


TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")

CHAT_INTERFACE_AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")

TASK_LOCK_GITIGNORE_ENTRY = ".ai-dev/tasks/*/.lock"


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.root = self._git_root()
        self.meta = self.root / ".ai-dev"

    def _git_root(self) -> Path:
        result = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise PolicyError(f"Gitリポジトリではありません: {self.repo}")
        return Path(result.stdout.strip()).resolve()

    def task_dir(self, task_id: str) -> Path:
        if not TASK_ID.fullmatch(task_id):
            raise PolicyError("タスクIDは2～64文字の英数字、ピリオド、アンダースコア、ハイフンで指定してください")
        return self.meta / "tasks" / task_id

    @contextlib.contextmanager
    def task_lock(self, task_id: str, timeout: float = 10.0):
        """同一タスクへの同時操作を防ぐアドバイザリロック。

        人間が複数のチャット窓口（例：Codex拡張とClaude Code拡張）を同時に開き、
        同じタスクへ同時に操作を投げるレースを防ぐ。ロックファイル自体はGit追跡対象外
        （initialize()が対象リポジトリの.gitignoreへ冪等に登録する）。
        """

        lock_path = self.task_dir(task_id) / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PolicyError(
                            "別のTriad操作が進行中のため待機がタイムアウトしました。少し待って再試行してください。"
                        )
                    time.sleep(0.2)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def initialize(
        self,
        task_id: str,
        title: str,
        implementation_author: str,
        guidance_sources: dict[str, Path] | None = None,
        chat_interface_source: Path | None = None,
        brief: str | None = None,
    ) -> dict[str, Any]:
        if implementation_author not in {"claude", "codex"}:
            raise PolicyError("implementation_authorにはclaudeまたはcodexを指定してください")
        request = (brief or title).strip()
        if not request:
            raise PolicyError("簡単な依頼は空にできません")
        task = self.task_dir(task_id)
        if task.exists():
            raise PolicyError(f"タスクはすでに存在します: {task_id}")
        for name in (
            "input",
            "artifacts",
            "reviews",
            "evidence",
            "approvals",
            "change-requests",
            "handoffs",
            "decisions",
        ):
            (task / name).mkdir(parents=True, exist_ok=True)
        (self.meta / "audit").mkdir(parents=True, exist_ok=True)
        self._ensure_task_lock_gitignore_entry()
        state: dict[str, Any] = {
            "schema_version": 1,
            "task_id": task_id,
            "title": title,
            "state": State.INTAKE.value,
            "implementation_author": implementation_author,
            "sequence": 0,
            "degraded": [],
            "approvals": {},
            "created_at": now(),
            "updated_at": now(),
        }
        self.write_json(task / "state.json", state, check_secret=False)
        initial_brief = (
            f"# 人間からの簡単な依頼：{task_id}\n\n"
            f"{request}\n\n"
            "## AIへの指示\n\n"
            "この依頼を出発点として、まず目的、対象利用者、対象範囲、制約、仮定、調査論点を整理する。"
            "この段階では実現方法を確定しない。以後、Antigravityの調査、Claudeの提案、Codexの独立評価、"
            "Antigravityの事実確認、Codexの統合、Claudeの要件・設計とその独立レビューを経て、"
            "Codexが一つの計画書へ統合し、Claudeが最終レビューしてから、人間へ計画承認を求める。\n"
        )
        self.write_text(task / "input" / "brief.md", initial_brief)
        self.append_history(task_id, None, State.INTAKE, "human", "簡単な依頼からタスクを初期化")
        project_file = self.meta / "project.json"
        if not project_file.exists():
            self.write_json(
                project_file,
                {
                    "schema_version": 1,
                    "compose": {
                        "build_test": ["docker", "compose", "run", "--rm", "test"],
                    },
                    "forbidden_operations": [
                        "git push",
                        "本番デプロイ",
                        "破壊的なデータベース操作",
                        "履歴書き換え",
                    ],
                },
                check_secret=False,
            )
        if guidance_sources:
            self.install_guidance(guidance_sources)
        if chat_interface_source:
            self.install_chat_interface(chat_interface_source)
        return state

    def _ensure_task_lock_gitignore_entry(self) -> None:
        gitignore = self.root / ".gitignore"
        if gitignore.is_file():
            existing = gitignore.read_text(encoding="utf-8")
            if TASK_LOCK_GITIGNORE_ENTRY in existing.splitlines():
                return
            separator = "" if existing == "" or existing.endswith("\n") else "\n"
            content = existing + separator + f"# triad-task-lock\n{TASK_LOCK_GITIGNORE_ENTRY}\n"
        else:
            content = f"# triad-task-lock\n{TASK_LOCK_GITIGNORE_ENTRY}\n"
        self.write_text(gitignore, content, check_secret=False)

    def install_chat_interface(self, source: Path) -> None:
        """チャット操作面を初回だけ設定し、導入済みの固定版は暗黙更新しない。"""

        source = source.resolve()
        interface_source = source / "chat-interface.md"
        if not interface_source.is_file():
            raise PolicyError(f"チャット操作ガイドが見つかりません: {interface_source}")
        interface_target = self.meta / "chat-interface.md"
        if not interface_target.exists():
            self.write_text(interface_target, interface_source.read_text(encoding="utf-8"))

        for filename in CHAT_INTERFACE_AGENT_FILES:
            block_source = source / filename
            if not block_source.is_file():
                raise PolicyError(f"チャット用AI指示が見つかりません: {block_source}")
            block = block_source.read_text(encoding="utf-8").strip()
            target = self.root / filename
            if not target.exists():
                self.write_text(target, block + "\n", check_secret=False)
                continue
            current = target.read_text(encoding="utf-8")
            marker = "<!-- triad-chat-interface -->"
            if marker not in current:
                separator = "" if current.endswith("\n\n") else "\n" if current.endswith("\n") else "\n\n"
                self.write_text(target, current + separator + block + "\n", check_secret=False)

    def install_guidance(self, sources: dict[str, Path]) -> Path:
        """採用済み知識とテンプレートを対象リポジトリへ固定する。"""

        guidance = self.meta / "guidance"
        manifest_path = guidance / "manifest.json"
        if manifest_path.is_file():
            self.verify_guidance()
            return manifest_path

        files: dict[str, str] = {}
        for section, source in sorted(sources.items()):
            if not re.fullmatch(r"[a-z][a-z0-9-]*", section):
                raise PolicyError(f"ガイダンス区分名が不正です: {section}")
            if source.is_symlink():
                raise PolicyError(f"ガイダンスの取得元にシンボリックリンクは使用できません: {source}")
            source = source.resolve()
            if not source.is_dir():
                raise PolicyError(f"ガイダンスの取得元が見つかりません: {source}")
            symbolic_links = [path for path in source.rglob("*") if path.is_symlink()]
            if symbolic_links:
                raise PolicyError(f"ガイダンスにシンボリックリンクは使用できません: {symbolic_links[0]}")
            for path in sorted(source.rglob("*.md")):
                relative = Path(section) / path.relative_to(source)
                destination = guidance / relative
                self.write_text(destination, path.read_text(encoding="utf-8"))
                files[str(relative)] = sha256_file(destination)
        if not files:
            raise PolicyError("固定するガイダンス文書がありません")
        self.write_json(
            manifest_path,
            {
                "schema_version": 1,
                "snapshot_at": now(),
                "files": files,
            },
            check_secret=False,
        )
        return manifest_path

    def load_state(self, task_id: str, verify_frozen: bool = True) -> dict[str, Any]:
        path = self.task_dir(task_id) / "state.json"
        if not path.exists():
            raise PolicyError(f"不明なタスクです: {task_id}")
        state = json.loads(path.read_text(encoding="utf-8"))
        if verify_frozen:
            self.verify_guidance()
            self.verify_frozen(state)
        return state

    def verify_guidance(self) -> None:
        guidance = self.meta / "guidance"
        manifest_path = guidance / "manifest.json"
        if not manifest_path.is_file():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = manifest.get("files", {})
        actual = {
            str(path.relative_to(guidance))
            for path in guidance.rglob("*")
            if path.is_file() and path != manifest_path
        }
        if actual != set(expected):
            raise PolicyError("固定済みガイダンスのファイル構成が変更されています")
        for relative, digest in expected.items():
            path = guidance / relative
            if sha256_file(path) != digest:
                raise PolicyError(f"固定済みガイダンスが変更されています: {relative}")

    def current(self, task_id: str) -> State:
        return State(self.load_state(task_id)["state"])

    def advance(
        self,
        task_id: str,
        outcome: Outcome,
        actor: str,
        reason: str,
        confirmation_channel: str | None = None,
    ) -> dict[str, Any]:
        with self.task_lock(task_id):
            state = self.load_state(task_id)
            current = State(state["state"])
            if current not in TRANSITIONS or outcome not in TRANSITIONS[current]:
                raise PolicyError(f"{current.value}から結果{outcome.value!r}を指定することはできません")
            if outcome is Outcome.SKIP and current not in {
                State.RESEARCH,
                State.PROPOSAL_FACT_CHECK,
                State.DESIGN_RESEARCH,
                State.E2E_VERIFY,
            }:
                raise PolicyError("明示的にスキップできるのはAntigravityの調査・E2Eフェーズだけです")
            pending = self.pending_decisions(task_id, current)
            if pending:
                raise PolicyError(
                    "未解決の人間判断があります: " + ", ".join(str(path.relative_to(self.root)) for path in pending)
                )
            self._require_artifacts(task_id, current, outcome)
            target = TRANSITIONS[current][outcome]
            revision_gate = REVISION_GATES.get(current)
            if revision_gate is not None and outcome is Outcome.NEEDS_CHANGES:
                feedback_dir = self.task_dir(task_id) / "input"
                number = len(list(feedback_dir.glob(f"{revision_gate}-feedback-*.md"))) + 1
                target_lines = []
                for relative in GATE_TARGETS[revision_gate]:
                    target_path = self.task_dir(task_id) / relative
                    digest = sha256_file(target_path) if target_path.is_file() else "MISSING"
                    target_lines.append(f"- `{relative}`: `{digest}`")
                heading = "計画への修正依頼" if revision_gate == "plan" else "成果物の作り直し依頼"
                self.write_text(
                    feedback_dir / f"{revision_gate}-feedback-{number:03d}.md",
                    f"# {heading} {number:03d}\n\n"
                    f"- 確認経路: `{confirmation_channel or 'unspecified'}`\n\n"
                    "## 対象とSHA-256\n\n"
                    + "\n".join(target_lines)
                    + "\n\n## 理由\n\n"
                    f"{reason.strip()}\n",
                )
            if outcome is Outcome.SKIP:
                state["degraded"].append({"phase": current.value, "at": now(), "reason": reason})
                for relative in REQUIRED_ON_EXIT.get(current, ()):
                    self.write_text(
                        self.task_dir(task_id) / relative,
                        f"# スキップ：{current.value}\n\n"
                        f"- 状態: `SKIPPED`\n"
                        f"- 記録日時: `{now()}`\n"
                        f"- 理由: {reason}\n",
                    )
            return self._transition(task_id, state, current, target, actor, reason)

    def pending_decisions(self, task_id: str, phase: State | None = None) -> list[Path]:
        pending: list[Path] = []
        for path in sorted((self.task_dir(task_id) / "decisions").glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") != "pending":
                continue
            if phase is None or record.get("phase") == phase.value:
                pending.append(path)
        return pending

    def resolve_decisions(
        self,
        task_id: str,
        answers: list[str],
        confirmation_channel: str | None = None,
    ) -> Path:
        with self.task_lock(task_id):
            state = self.load_state(task_id)
            current = State(state["state"])
            pending = self.pending_decisions(task_id, current)
            if len(pending) != 1:
                raise PolicyError(f"{current.value}には判断待ち一式が1件必要ですが、{len(pending)}件見つかりました")
            path = pending[0]
            record = json.loads(path.read_text(encoding="utf-8"))
            questions = record.get("questions", [])
            if len(answers) != len(questions):
                raise PolicyError(f"回答は{len(questions)}件必要ですが、{len(answers)}件受け取りました")
            record.update(
                {
                    "status": "resolved",
                    "resolved_by": "human",
                    "resolved_at": now(),
                    "confirmation_channel": confirmation_channel or "unspecified",
                    "answers": [
                        {"question": question, "answer": answer}
                        for question, answer in zip(questions, answers, strict=True)
                    ],
                }
            )
            self.write_json(path, record)
            self.append_history(task_id, current, current, "human", "判断待ちの人間判断を解決")
            return path

    def approve(
        self,
        task_id: str,
        gate: str,
        confirmation_channel: str | None = None,
    ) -> dict[str, Any]:
        if gate not in HUMAN_GATES:
            raise PolicyError(f"不明な承認ゲートです: {gate}")
        with self.task_lock(task_id):
            state = self.load_state(task_id)
            current = State(state["state"])
            expected, target = HUMAN_GATES[gate]
            if current is not expected:
                raise PolicyError(f"承認ゲート{gate}には状態{expected.value}が必要です。現在の状態は{current.value}です")
            task = self.task_dir(task_id)
            targets: list[dict[str, str]] = []
            for relative in GATE_TARGETS[gate]:
                path = task / relative
                if not path.is_file():
                    raise PolicyError(f"承認対象が見つかりません: {relative}")
                targets.append({"path": relative, "sha256": sha256_file(path)})
            record = {
                "schema_version": 1,
                "task_id": task_id,
                "gate": gate,
                "approved_by": "human",
                "approved_at": now(),
                "confirmation_channel": confirmation_channel or "unspecified",
                "targets": targets,
            }
            approval_path = task / "approvals" / f"{gate}-{state['sequence'] + 1}.json"
            self.write_json(approval_path, record, check_secret=False)
            state["approvals"][gate] = str(approval_path.relative_to(self.root))
            return self._transition(task_id, state, current, target, "human", f"{gate}承認ゲートを承認")

    def request_change(self, task_id: str, actor: str, reason: str) -> Path:
        if not reason.strip():
            raise PolicyError("変更要求には具体的な理由が必要です")
        with self.task_lock(task_id):
            state = self.load_state(task_id)
            current = State(state["state"])
            # 変更要求はplan承認済みの成果物マクロフェーズだけで発議できる。計画承認前
            # （計画マクロフェーズ本体とAWAITING_PLAN_APPROVAL自体）は、advanceの
            # NEEDS_CHANGESで計画を直接差し戻せるため、より重い変更要求は不要である。
            if current in {
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
                State.AWAITING_PLAN_APPROVAL,
                State.CHANGE_REQUEST,
                State.DELIVERED,
            }:
                raise PolicyError(f"状態{current.value}から変更要求を作成することはできません")
            number = len(list((self.task_dir(task_id) / "change-requests").glob("request-*.md"))) + 1
            path = self.task_dir(task_id) / "change-requests" / f"request-{number:03d}.md"
            self.write_text(
                path,
                f"# 変更要求 {number:03d}\n\n"
                f"- 要求者: `{actor}`\n"
                f"- 要求日時: `{now()}`\n"
                f"- 変更前の状態: `{current.value}`\n\n"
                f"## 理由\n\n{reason.strip()}\n\n"
                "## 承認時の影響\n\n"
                "計画承認を無効化し、3AIの調査・提案からINTAKEで再開する。\n\n"
                "## 人間の判断\n\n判断待ち\n",
            )
            state["change_request"] = str(path.relative_to(self.root))
            self._transition(task_id, state, current, State.CHANGE_REQUEST, actor, reason)
            return path

    def approve_change(
        self,
        task_id: str,
        confirmation_channel: str | None = None,
    ) -> dict[str, Any]:
        with self.task_lock(task_id):
            state = self.load_state(task_id)
            current = State(state["state"])
            if current is not State.CHANGE_REQUEST:
                raise PolicyError("承認待ちの変更要求がありません")
            request_path = self.root / state["change_request"]
            effect = "計画承認を無効化し、3AIの調査・提案からINTAKEで再開する"
            decision_path = request_path.with_suffix(".approved.json")
            self.write_json(
                decision_path,
                {
                    "schema_version": 1,
                    "task_id": task_id,
                    "approved_by": "human",
                    "approved_at": now(),
                    "confirmation_channel": confirmation_channel or "unspecified",
                    "request": str(request_path.relative_to(self.root)),
                    "request_sha256": sha256_file(request_path),
                    "effect": effect,
                },
                check_secret=False,
            )
            state["superseded_approvals"] = [
                *state.get("superseded_approvals", []),
                *state.get("approvals", {}).values(),
            ]
            state["approvals"] = {}
            state.pop("change_request", None)
            return self._transition(
                task_id,
                state,
                current,
                State.INTAKE,
                "human",
                f"変更要求を承認し、{effect}",
            )

    def verify_frozen(self, state: dict[str, Any]) -> None:
        for gate, record_path in state.get("approvals", {}).items():
            path = self.root / record_path
            if not path.is_file():
                raise PolicyError(f"固定された承認記録が見つかりません: {record_path}")
            record = json.loads(path.read_text(encoding="utf-8"))
            for target in record.get("targets", []):
                artifact = self.task_dir(state["task_id"]) / target["path"]
                if not artifact.is_file() or sha256_file(artifact) != target["sha256"]:
                    raise PolicyError(
                        f"承認済み成果物が変更されています: {target['path']}。人間が承認する変更要求を作成してください。"
                    )

    def _require_artifacts(self, task_id: str, current: State, outcome: Outcome) -> None:
        if outcome is Outcome.SKIP:
            return
        for relative in REQUIRED_ON_EXIT.get(current, ()):
            path = self.task_dir(task_id) / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise PolicyError(f"必要な成果物が存在しないか空です: {relative}")

    def _transition(
        self,
        task_id: str,
        state: dict[str, Any],
        current: State,
        target: State,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        state["state"] = target.value
        state["sequence"] = int(state["sequence"]) + 1
        state["updated_at"] = now()
        self.write_json(self.task_dir(task_id) / "state.json", state, check_secret=False)
        self.append_history(task_id, current, target, actor, reason)
        return state

    def append_history(
        self, task_id: str, previous: State | None, target: State, actor: str, reason: str
    ) -> None:
        record = {
            "at": now(),
            "from": previous.value if previous else None,
            "to": target.value,
            "actor": actor,
            "reason": reason,
        }
        path = self.task_dir(task_id) / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_audit(self, record: dict[str, Any]) -> None:
        path = self.meta / "audit" / "cli-calls.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def artifact_path(self, task_id: str, relative: str) -> Path:
        task = self.task_dir(task_id).resolve()
        target = (task / relative).resolve()
        if task not in target.parents:
            raise PolicyError("成果物のパスがタスクディレクトリの外を指しています")
        return target

    @staticmethod
    def write_text(path: Path, content: str, check_secret: bool = True) -> None:
        if check_secret:
            reject_secrets(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(content)
            temp = Path(handle.name)
        os.replace(temp, path)

    @staticmethod
    def write_json(path: Path, value: Any, check_secret: bool = True) -> None:
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if check_secret:
            reject_secrets(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(content)
            temp = Path(handle.name)
        os.replace(temp, path)
