from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .model import GATE_TARGETS, HUMAN_GATES, PHASES, REVISION_GATES, Outcome, State, resolved_agent
from .policy import PolicyError, sha256_bytes, sha256_file
from .runner import Runner
from .store import Store


PLATFORM_ROOT = Path(__file__).resolve().parent.parent


class JapaneseArgumentParser(argparse.ArgumentParser):
    """argparseの固定表示を日本語化するパーサー。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置引数"
        self._optionals.title = "オプション"
        self.add_argument("-h", "--help", action="help", help="このヘルプを表示して終了する")

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "使用方法:", 1)

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "使用方法:", 1)


def parser() -> argparse.ArgumentParser:
    root = JapaneseArgumentParser(prog="triad", description="Gitを正本とする3AI開発オーケストレーター")
    root.add_argument("--version", action="version", version=__version__, help="バージョンを表示して終了する")
    commands = root.add_subparsers(dest="command", required=True, title="コマンド")

    doctor = commands.add_parser("doctor", help="CLI、認証、Git、Dockerの利用可否を確認する")
    doctor.add_argument("--json", action="store_true", help="結果をJSON形式で表示する")

    init = commands.add_parser("init", help="アプリケーションのGitリポジトリにワークフロータスクを作成する")
    init.add_argument("task_id", help="作成するタスクのID")
    init.add_argument("--title", required=True, help="タスクの表題")
    init.add_argument("--brief", help="3AIが調査・提案・統合する、人間からの簡単な依頼")
    init.add_argument("--implementation-author", choices=("claude", "codex"), default="claude", help="主力実装を担当するAI")
    add_repo(init)

    status = commands.add_parser("status", help="タスクの状態と次の担当を表示する")
    status.add_argument("task_id", help="確認するタスクのID")
    status.add_argument("--json", action="store_true", help="結果をJSON形式で表示する")
    status.add_argument(
        "--answer",
        action="append",
        help="判断回答に束縛されたチャット確認句を得るため、質問順に指定する回答",
    )
    add_repo(status)

    run = commands.add_parser("run", help="現在のフェーズに割り当てられたAIまたはDocker処理を実行する")
    run.add_argument("task_id", help="実行するタスクのID")
    add_repo(run)

    advance = commands.add_parser("advance", help="証跡を検証し、現在フェーズの状態遷移を進める")
    advance.add_argument("task_id", help="進めるタスクのID")
    advance.add_argument("--outcome", choices=tuple(item.value for item in Outcome), required=True, help="現在フェーズの結果")
    advance.add_argument("--reason", default="フェーズの出力を検証済み", help="状態遷移の理由")
    add_human_confirmation(advance)
    add_repo(advance)

    approve = commands.add_parser("approve", help="固定された成果物一式を人間が承認する")
    approve.add_argument("task_id", help="承認するタスクのID")
    approve.add_argument("gate", choices=tuple(HUMAN_GATES), help="承認対象のゲート（plan=計画承認、delivery=成果物完成確認）")
    add_human_confirmation(approve)
    add_repo(approve)

    change = commands.add_parser("request-change", help="実装を停止して承認済み計画自体の変更を提案する")
    change.add_argument("task_id", help="変更要求を作成するタスクのID")
    change.add_argument("--reason", required=True, help="変更が必要な具体的理由")
    change.add_argument("--actor", choices=("codex", "claude", "antigravity", "human"), required=True, help="変更要求の作成者")
    add_repo(change)

    approve_change = commands.add_parser("approve-change", help="変更要求を人間が承認する")
    approve_change.add_argument("task_id", help="変更要求を承認するタスクのID")
    add_human_confirmation(approve_change)
    add_repo(approve_change)

    decide = commands.add_parser("decide", help="AIが提示した判断事項へ人間が回答する")
    decide.add_argument("task_id", help="判断事項へ回答するタスクのID")
    decide.add_argument("--answer", action="append", required=True, help="質問順に指定する回答。複数回指定可能")
    add_human_confirmation(decide)
    add_repo(decide)
    return root


def add_repo(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repo", type=Path, default=Path.cwd(), help="対象アプリケーションのGitリポジトリ")


def add_human_confirmation(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--human-confirmation",
        help="VS Codeチャットで人間が直前に明示した操作を中継する確認句",
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(args.json)
        store = Store(args.repo)
        if args.command == "init":
            state = store.initialize(
                args.task_id,
                args.title,
                args.implementation_author,
                guidance_sources={
                    "knowledge": PLATFORM_ROOT / "docs" / "knowledge",
                    "templates": PLATFORM_ROOT / "templates" / "artifacts",
                },
                chat_interface_source=PLATFORM_ROOT / "templates" / "project",
                brief=args.brief,
            )
            print(store.task_dir(args.task_id).relative_to(store.root))
            print(f"state={state['state']}")
            return 0
        if args.command == "status":
            return status(store, args.task_id, args.json, args.answer)
        if args.command == "run":
            output = Runner(store, PLATFORM_ROOT).run(args.task_id)
            print(output.relative_to(store.root))
            return 0
        if args.command == "advance":
            outcome = Outcome(args.outcome)
            verify_review_outcome(store, args.task_id, outcome)
            current = store.current(args.task_id)
            revision_gate = REVISION_GATES.get(current)
            is_redo = revision_gate is not None and outcome is Outcome.NEEDS_CHANGES
            confirmation_channel = None
            if is_redo:
                confirmation_channel = redo_confirmation(
                    store,
                    args.task_id,
                    revision_gate,
                    args.reason,
                    args.human_confirmation,
                )
            elif args.human_confirmation is not None:
                raise PolicyError("--human-confirmationは人間による計画の差し戻し・成果物の作り直しにだけ指定できます")
            actor = "human" if is_redo else "orchestrator"
            state = store.advance(
                args.task_id,
                outcome,
                actor,
                args.reason,
                confirmation_channel=confirmation_channel,
            )
            print(state["state"])
            return 0
        if args.command == "approve":
            confirmation_channel = human_confirmation(
                store,
                args.task_id,
                args.gate,
                args.human_confirmation,
            )
            state = store.approve(
                args.task_id,
                args.gate,
                confirmation_channel=confirmation_channel,
            )
            print(state["state"])
            return 0
        if args.command == "request-change":
            path = store.request_change(
                args.task_id,
                args.actor,
                args.reason,
            )
            print(path.relative_to(store.root))
            return 0
        if args.command == "approve-change":
            confirmation_channel = human_confirmation(
                store,
                args.task_id,
                "change",
                args.human_confirmation,
            )
            state = store.approve_change(
                args.task_id,
                confirmation_channel=confirmation_channel,
            )
            print(state["state"])
            return 0
        if args.command == "decide":
            confirmation_channel = decision_confirmation(
                store,
                args.task_id,
                args.answer,
                args.human_confirmation,
            )
            path = store.resolve_decisions(
                args.task_id,
                args.answer,
                confirmation_channel=confirmation_channel,
            )
            print(path.relative_to(store.root))
            return 0
        raise PolicyError(f"未対応のコマンドです: {args.command}")
    except (PolicyError, ValueError, subprocess.SubprocessError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2


def status(
    store: Store,
    task_id: str,
    as_json: bool,
    decision_answers: list[str] | None = None,
) -> int:
    state = store.load_state(task_id)
    current = State(state["state"])
    pending_decisions = store.pending_decisions(task_id)
    owner = "human"
    artifact = None
    if current is State.BUILD_TEST:
        owner = "orchestrator/docker"
        artifact = "evidence/build-test.md"
    elif current in PHASES:
        owner = resolved_agent(current, state["implementation_author"])
        artifact = PHASES[current].artifact
    if pending_decisions:
        owner = "human"
    human_actions = human_actions_for(store, task_id, current, decision_answers)
    summary = {
        "task_id": task_id,
        "state": current.value,
        "owner": owner,
        "expected_artifact": artifact,
        "degraded": state.get("degraded", []),
        "active_approvals": state.get("approvals", {}),
        "pending_decisions": [str(path.relative_to(store.root)) for path in pending_decisions],
        "human_actions": human_actions,
    }
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0


def human_actions_for(
    store: Store,
    task_id: str,
    current: State,
    decision_answers: list[str] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for gate, (expected, _) in HUMAN_GATES.items():
        if current is expected:
            targets = confirmation_targets(store, task_id, gate)
            actions.append(
                {
                    "action": "approve",
                    "gate": gate,
                    "targets": targets,
                    "confirmation": chat_confirmation_phrase(task_id, "approve", gate, targets),
                }
            )
            actions.append(
                {
                    "action": "request_revision",
                    "gate": gate,
                    "targets": targets,
                    "confirmation": chat_confirmation_phrase(task_id, "revise", gate, targets),
                }
            )
            break
    if current is State.CHANGE_REQUEST:
        targets = confirmation_targets(store, task_id, "change")
        actions.append(
            {
                "action": "approve_change",
                "targets": targets,
                "confirmation": chat_confirmation_phrase(task_id, "approve", "change", targets),
            }
        )
    pending = store.pending_decisions(task_id, current)
    if pending:
        if len(pending) != 1:
            raise PolicyError(f"{current.value}には判断待ち一式が1件必要ですが、{len(pending)}件見つかりました")
        record = json.loads(pending[0].read_text(encoding="utf-8"))
        questions = record.get("questions", [])
        if not isinstance(questions, list):
            raise PolicyError("判断待ち記録のquestionsが不正です")
        action: dict[str, Any] = {
            "action": "decide",
            "targets": decision_confirmation_targets(store, pending, None),
            "answers_required": len(questions),
        }
        if decision_answers is not None:
            if len(decision_answers) != len(questions):
                raise PolicyError(f"回答は{len(questions)}件必要ですが、{len(decision_answers)}件受け取りました")
            targets = decision_confirmation_targets(store, pending, decision_answers)
            action["targets"] = targets
            action["confirmation"] = chat_confirmation_phrase(
                task_id,
                "record",
                current.value,
                targets,
            )
        actions.append(action)
    elif decision_answers is not None:
        raise PolicyError("判断待ちがないため--answerは指定できません")
    return actions


def confirmation_targets(store: Store, task_id: str, gate: str) -> list[dict[str, str]]:
    if gate in GATE_TARGETS:
        task = store.task_dir(task_id)
        return [
            {
                "path": relative,
                "sha256": sha256_file(task / relative) if (task / relative).is_file() else "MISSING",
            }
            for relative in GATE_TARGETS[gate]
        ]
    if gate == "change":
        state = store.load_state(task_id)
        relative = state.get("change_request")
        path = store.root / relative if isinstance(relative, str) else None
        return [
            {
                "path": relative or "MISSING",
                "sha256": sha256_file(path) if path is not None and path.is_file() else "MISSING",
            }
        ]
    raise PolicyError(f"確認対象が不明です: {gate}")


def chat_confirmation_phrase(
    task_id: str,
    verb: str,
    subject: str,
    targets: list[dict[str, str]],
) -> str:
    serialized = json.dumps(targets, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    challenge = sha256_bytes(serialized.encode("utf-8"))[:16]
    return f"{verb} {task_id} {subject} {challenge}"


def decision_confirmation_targets(
    store: Store,
    pending: list[Path],
    answers: list[str] | None,
) -> list[dict[str, str]]:
    targets = [
        {"path": str(path.relative_to(store.root)), "sha256": sha256_file(path)}
        for path in pending
    ]
    if answers is not None:
        serialized = json.dumps(answers, ensure_ascii=False, separators=(",", ":"))
        targets.append(
            {
                "path": "<human-answers>",
                "sha256": sha256_bytes(serialized.encode("utf-8")),
            }
        )
    return targets


def verify_review_outcome(store: Store, task_id: str, outcome: Outcome) -> None:
    state_data = store.load_state(task_id)
    state = State(state_data["state"])
    if state not in PHASES or not PHASES[state].review:
        return
    artifact = store.artifact_path(task_id, PHASES[state].artifact or "")
    if not artifact.is_file():
        return
    verdict = None
    for line in artifact.read_text(encoding="utf-8").splitlines()[:12]:
        if line.startswith("verdict:"):
            verdict = line.split(":", 1)[1].strip()
            break
    expected = "needs_changes" if outcome is Outcome.NEEDS_CHANGES else outcome.value
    if verdict != expected:
        raise PolicyError(f"指定した結果{outcome.value}がレビュー判定{verdict!r}と一致しません")


def human_confirmation(
    store: Store,
    task_id: str,
    gate: str,
    supplied_confirmation: str | None = None,
) -> str:
    if os.environ.get("TRIAD_AGENT_RUN") == "1":
        raise PolicyError("AIアダプターのプロセスから承認を実行することはできません")
    phrase = f"approve {task_id} {gate}"
    if supplied_confirmation is not None:
        expected = chat_confirmation_phrase(
            task_id,
            "approve",
            gate,
            confirmation_targets(store, task_id, gate),
        )
        if supplied_confirmation != expected:
            raise PolicyError("VS Codeチャットから渡された承認用の確認句が一致しません")
        return "vscode-chat"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PolicyError("承認には人間が操作する対話端末、またはVS Codeチャットでの明示確認が必要です")
    print(f"人間による承認を要求しています: task={task_id}, gate={gate}")
    for target in confirmation_targets(store, task_id, gate):
        print(f"  {target['path']}: {target['sha256']}")
    entered = input(f"次の文字列を正確に入力してください: '{phrase}': ")
    if entered != phrase:
        raise PolicyError("承認用の確認文字列が一致しません")
    return "terminal"


def redo_confirmation(
    store: Store,
    task_id: str,
    gate: str,
    reason: str,
    supplied_confirmation: str | None = None,
) -> str:
    """AWAITING_PLAN_APPROVAL（差し戻し）とAWAITING_DELIVERY_APPROVAL（作り直し）の
    NEEDS_CHANGESを人間が明示した場合だけ中継する確認句を検証する。"""

    if gate not in {"plan", "delivery"}:
        raise PolicyError(f"差し戻し・作り直し対象が不明です: {gate}")
    if os.environ.get("TRIAD_AGENT_RUN") == "1":
        raise PolicyError("AIアダプターのプロセスから差し戻し・作り直しを実行することはできません")
    phrase = f"revise {task_id} {gate}"
    if supplied_confirmation is not None:
        expected = chat_confirmation_phrase(
            task_id,
            "revise",
            gate,
            confirmation_targets(store, task_id, gate),
        )
        if supplied_confirmation != expected:
            raise PolicyError("VS Codeチャットから渡された確認句が一致しません")
        return "vscode-chat"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PolicyError("差し戻し・作り直しには対話端末、またはVS Codeチャットでの明示確認が必要です")
    label = "計画の差し戻し" if gate == "plan" else "成果物の作り直し"
    print(f"人間による{label}: task={task_id}, gate={gate}")
    for target in confirmation_targets(store, task_id, gate):
        print(f"  {target['path']}: {target['sha256']}")
    print(f"  理由: {reason}")
    entered = input(f"次の文字列を正確に入力してください: '{phrase}': ")
    if entered != phrase:
        raise PolicyError("確認文字列が一致しません")
    return "terminal"


def decision_confirmation(
    store: Store,
    task_id: str,
    answers: list[str],
    supplied_confirmation: str | None = None,
) -> str:
    if os.environ.get("TRIAD_AGENT_RUN") == "1":
        raise PolicyError("AIアダプターのプロセスから人間の判断を記録することはできません")
    state = store.load_state(task_id)
    current = State(state["state"])
    pending = store.pending_decisions(task_id, current)
    if len(pending) != 1:
        raise PolicyError(f"{current.value}には判断待ち一式が1件必要ですが、{len(pending)}件見つかりました")
    record = json.loads(pending[0].read_text(encoding="utf-8"))
    questions = record.get("questions", [])
    if len(questions) != len(answers):
        raise PolicyError(f"回答は{len(questions)}件必要ですが、{len(answers)}件受け取りました")
    phrase = f"record {task_id} {current.value}"
    if supplied_confirmation is not None:
        targets = decision_confirmation_targets(store, pending, answers)
        expected = chat_confirmation_phrase(task_id, "record", current.value, targets)
        if supplied_confirmation != expected:
            raise PolicyError("VS Codeチャットから渡された判断記録用の確認句が一致しません")
        return "vscode-chat"
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PolicyError("判断の記録には人間が操作する対話端末、またはVS Codeチャットでの明示確認が必要です")
    print(f"人間の判断: task={task_id}, phase={current.value}")
    for index, (question, answer) in enumerate(zip(questions, answers, strict=True), start=1):
        print(f"  Q{index}: {question}")
        print(f"  A{index}: {answer}")
    entered = input(f"次の文字列を正確に入力してください: '{phrase}': ")
    if entered != phrase:
        raise PolicyError("判断記録用の確認文字列が一致しません")
    return "terminal"


def doctor(as_json: bool) -> int:
    result: dict[str, Any] = {"platform_version": __version__, "commands": {}, "docker_daemon": False}
    checks = {
        "codex": (["codex", "--version"], ["codex", "login", "status"]),
        "claude": (["claude", "--version"], ["claude", "auth", "status"]),
        "antigravity": (["agy", "--version"], None),
        "docker": (["docker", "--version"], None),
        "git": (["git", "--version"], None),
    }
    for name, (version_command, auth_command) in checks.items():
        path = shutil.which(version_command[0])
        entry: dict[str, Any] = {"installed": bool(path), "path": path, "version": None, "authenticated": None}
        if path:
            version = subprocess.run(version_command, capture_output=True, text=True, check=False, timeout=10)
            entry["version"] = (version.stdout or version.stderr).strip().splitlines()[-1]
            if auth_command:
                auth = subprocess.run(auth_command, capture_output=True, text=True, check=False, timeout=15)
                entry["authenticated"] = auth.returncode == 0
        result["commands"][name] = entry
    if result["commands"]["docker"]["installed"]:
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        result["docker_daemon"] = daemon.returncode == 0
        result["docker_server_version"] = daemon.stdout.strip() or None
    result["notes"] = [
        "Antigravityの認証は、初回の対話式'agy'ログインで確認します。doctorはOAuthを開始しません。",
        "APIキーの環境変数は必要とせず、検査も行いません。",
    ]
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for name, entry in result["commands"].items():
            auth = " 認証済み=はい" if entry["authenticated"] is True else " 認証済み=いいえ" if entry["authenticated"] is False else ""
            print(f"{name}: インストール済み={entry['installed']} バージョン={entry['version']!r}{auth}")
        print(f"Dockerデーモン: {result['docker_daemon']}")
        for note in result["notes"]:
            print(f"注記: {note}")
    required = ("codex", "claude", "antigravity", "docker", "git")
    return 0 if all(result["commands"][name]["installed"] for name in required) else 1
