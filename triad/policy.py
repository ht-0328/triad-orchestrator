from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any


class PolicyError(RuntimeError):
    pass


SENSITIVE_ENV_NAMES = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "KUBECONFIG",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CERT_PATH",
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "COMPOSE_PROJECT_NAME",
}

SENSITIVE_ENV_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_", "GCP_", "KUBE_", "CI_JOB_TOKEN")

FORBIDDEN_CHANGED_PREFIXES = (
    ".git/",
    ".ai-dev/",
)

FORBIDDEN_CHANGED_FILES = {
    ".ai-dev/project.json",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
}

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*[^\s]{12,}"),
)

COMPOSE_OVERRIDE_FILENAMES = (
    "compose.override.yaml",
    "compose.override.yml",
    "docker-compose.override.yml",
    "docker-compose.override.yaml",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scrub_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(source if source is not None else os.environ)
    for name in tuple(env):
        if name in SENSITIVE_ENV_NAMES or any(name.startswith(prefix) for prefix in SENSITIVE_ENV_PREFIXES):
            env.pop(name, None)
    env["TRIAD_AGENT_RUN"] = "1"
    return env


def validate_changed_paths(paths: list[str]) -> None:
    violations: list[str] = []
    for raw in paths:
        normalized = raw.strip().replace("\\", "/")
        if is_protected_path(normalized):
            violations.append(normalized)
    if violations:
        raise PolicyError("AIが保護パスを変更しました: " + ", ".join(sorted(set(violations))))


def is_protected_path(raw: str) -> bool:
    normalized = raw.strip().replace("\\", "/")
    if normalized in FORBIDDEN_CHANGED_FILES:
        return True
    if any(normalized.startswith(prefix) for prefix in FORBIDDEN_CHANGED_PREFIXES):
        return True
    return "/approvals/" in normalized or normalized.endswith("/state.json") or normalized.endswith("/history.jsonl")


def reject_secrets(text: str) -> None:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise PolicyError("出力に認証情報が含まれている可能性があるため、保存を拒否しました")


def reject_compose_override_files(root: Path) -> None:
    """Composeの自動マージ対象になるオーバーライドファイルの併存を拒否する。

    validate_compose_modelは`docker compose config`が解決した後のモデルを検証するため
    オーバーライドで注入された危険な設定自体は検出できるが、_preflight_compose_filesの
    生キー走査（include/extends/env_file）はメインファイル1つしか読まない。オーバーライド
    ファイルの存在自体を早期に拒否し、多重定義による見落としの芽を摘む。
    """

    found = [name for name in COMPOSE_OVERRIDE_FILENAMES if (root / name).is_file()]
    if found:
        raise PolicyError(
            "自動テストではComposeオーバーライドファイルを使用できません: " + ", ".join(sorted(found))
        )


def validate_compose_model(model: dict[str, Any], repo_root: Path) -> None:
    root = repo_root.resolve()
    violations: list[str] = []
    services = model.get("services")
    if not isinstance(services, dict) or not services:
        raise PolicyError("解決済みComposeモデルにサービスがありません")
    for name, raw_service in services.items():
        if not isinstance(raw_service, dict):
            violations.append(f"{name}: サービスモデルが不正です")
            continue
        service = raw_service
        for flag in ("privileged",):
            if service.get(flag) is True:
                violations.append(f"{name}: {flag}=true")
        for key in ("network_mode", "pid", "ipc", "userns_mode", "cgroup"):
            if service.get(key) == "host":
                violations.append(f"{name}: {key}=host")
        if service.get("devices"):
            violations.append(f"{name}: ホストデバイスは使用できません")
        if service.get("cap_add"):
            violations.append(f"{name}: Linuxケイパビリティの追加は使用できません")
        for option in service.get("security_opt") or []:
            if "unconfined" in str(option).lower():
                violations.append(f"{name}: 制限なしのセキュリティオプション")
        build = service.get("build")
        context = build.get("context") if isinstance(build, dict) else build
        if context and not _within_root(Path(str(context)), root):
            violations.append(f"{name}: ビルドコンテキストがリポジトリ外を指しています: {context}")
        if isinstance(build, dict):
            dockerfile = build.get("dockerfile")
            if dockerfile and not _within_root(Path(str(dockerfile)), root):
                violations.append(f"{name}: Dockerfileがリポジトリ外を指しています: {dockerfile}")
            extra_contexts = build.get("additional_contexts") or {}
            if not isinstance(extra_contexts, dict):
                violations.append(f"{name}: 追加ビルドコンテキストが不正です")
            else:
                for label, extra_context in extra_contexts.items():
                    if not _within_root(Path(str(extra_context)), root):
                        violations.append(f"{name}: 追加ビルドコンテキスト{label}がリポジトリ外を指しています")
        if service.get("env_file"):
            violations.append(f"{name}: env_fileは自動テストで使用できません")
        for volume in service.get("volumes") or []:
            if not isinstance(volume, dict):
                violations.append(f"{name}: 未解決または短縮形のボリューム構文は使用できません")
                continue
            if volume.get("type") != "bind":
                continue
            source = str(volume.get("source") or "")
            if not source or not _within_root(Path(source), root):
                violations.append(f"{name}: バインド元がリポジトリ外を指しています: {source or '<空>'}")
            if source.endswith("docker.sock"):
                violations.append(f"{name}: Dockerソケットのバインドは禁止です")
    for collection in ("configs", "secrets"):
        for name, item in (model.get(collection) or {}).items():
            if isinstance(item, dict) and item.get("file") and not _within_root(Path(str(item["file"])), root):
                violations.append(f"{collection}.{name}: ファイルがリポジトリ外を指しています")
    if violations:
        raise PolicyError("安全でないDocker Composeモデルです: " + "; ".join(violations))


def _within_root(path: Path, root: Path) -> bool:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=False)
    return resolved == root or root in resolved.parents
