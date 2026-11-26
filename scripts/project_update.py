"""Подтянуть origin/master и перезапустить контейнер студии."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

MASTER_BRANCH = "master"
CONTAINER_NAME = os.environ.get("STUDIO_CONTAINER_NAME", "youtobe-studio")
DOCKER_SOCK = Path("/var/run/docker.sock")


def git_available() -> bool:
    return shutil.which("git") is not None


def _git(root: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    text = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    return text or f"git завершился с кодом {proc.returncode}"


def project_status(root: Path) -> dict[str, str]:
    """Текущая ветка и короткий HEAD. Пустые строки, если git недоступен."""
    if not git_available() or not (root / ".git").exists():
        return {"branch": "", "head": "", "dirty": ""}
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(root, "log", "-1", "--oneline")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {
        "branch": (branch.stdout or "").strip(),
        "head": (head.stdout or "").strip(),
        "dirty": (dirty.stdout or "").strip(),
    }


def pull_master(root: Path) -> tuple[bool, str]:
    """Переключить чистую копию на master и fast-forward с origin/master."""
    if not git_available():
        return False, "git не найден. Пересоберите образ студии: в нём должен быть git."
    if not (root / ".git").exists():
        return False, f"В {root} нет .git — обновление с GitHub отсюда невозможно."

    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0:
        return False, _combined(dirty)
    if (dirty.stdout or "").strip():
        return (
            False,
            "Есть незакоммиченные правки в отслеживаемых файлах. Обновление остановлено.\n"
            + dirty.stdout.strip(),
        )

    lines: list[str] = []
    fetch = _git(root, "fetch", "origin", MASTER_BRANCH, timeout=180)
    lines.append(_combined(fetch))
    if fetch.returncode != 0:
        return False, "\n".join(lines)

    checkout = _git(root, "checkout", MASTER_BRANCH)
    lines.append(_combined(checkout))
    if checkout.returncode != 0:
        return False, "\n".join(lines)

    pull = _git(root, "pull", "--ff-only", "origin", MASTER_BRANCH, timeout=180)
    lines.append(_combined(pull))
    if pull.returncode != 0:
        return False, "\n".join(lines)

    head = _git(root, "log", "-1", "--oneline")
    lines.append((head.stdout or "").strip())
    return True, "\n".join(line for line in lines if line)


def docker_restart_request(name: str, timeout_sec: int = 3) -> bytes:
    return (
        f"POST /v1.41/containers/{name}/restart?t={timeout_sec} HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode("ascii")


def _restart_via_socket(name: str) -> tuple[bool, str]:
    request = docker_restart_request(name)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(60)
            sock.connect(str(DOCKER_SOCK))
            sock.sendall(request)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        return False, f"Не удалось перезапустить контейнер через docker.sock: {exc}"
    text = b"".join(chunks).decode("utf-8", "replace")
    status = text.split("\r\n", 1)[0]
    ok = " 204 " in f" {status} " or status.endswith(" 204")
    if ok:
        return True, f"Контейнер {name} перезапускается."
    return False, status or text[:400] or "Docker не ответил на restart."


def _restart_via_cli(root: Path, name: str) -> tuple[bool, str]:
    compose = subprocess.run(
        ["docker", "compose", "restart", "studio"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if compose.returncode == 0:
        text = f"{compose.stdout or ''}{compose.stderr or ''}".strip()
        return True, text or "docker compose restart studio"
    direct = subprocess.run(
        ["docker", "restart", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    text = "\n".join(
        part.strip()
        for part in (
            compose.stdout,
            compose.stderr,
            direct.stdout,
            direct.stderr,
        )
        if part and part.strip()
    )
    if direct.returncode == 0:
        return True, text or f"docker restart {name}"
    return False, text or "docker restart не удался"


def restart_project(root: Path) -> tuple[bool, str]:
    """Перезапуск youtobe-studio: CLI на хосте или сокет демона внутри контейнера."""
    name = CONTAINER_NAME
    if shutil.which("docker"):
        return _restart_via_cli(root, name)
    if DOCKER_SOCK.exists():
        return _restart_via_socket(name)
    return (
        False,
        "Docker недоступен. На хосте нужен docker в PATH, "
        "в контейнере — смонтированный /var/run/docker.sock.",
    )


def schedule_restart(root: Path, delay_sec: float = 2.0) -> None:
    """Отложенный рестарт, чтобы Streamlit успел отдать ответ до остановки процесса."""

    def _run() -> None:
        import time

        time.sleep(delay_sec)
        restart_project(root)

    threading.Thread(target=_run, name="studio-docker-restart", daemon=True).start()
