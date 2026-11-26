"""Обновление с master: отказ на грязном дереве и запрос рестарта Docker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_update import docker_restart_request, pull_master  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "readme.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "readme.txt")
    _git(repo, "commit", "-m", "init")


def test_pull_master_stops_on_local_edits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "readme.txt").write_text("dirty\n", encoding="utf-8")
    ok, log = pull_master(repo)
    assert ok is False
    assert "незакоммиченные" in log
    assert "dirty" in (repo / "readme.txt").read_text(encoding="utf-8")


def test_docker_restart_request_targets_named_container() -> None:
    raw = docker_restart_request("youtobe-studio", timeout_sec=3).decode("ascii")
    assert raw.startswith("POST /v1.41/containers/youtobe-studio/restart?t=3 ")
    assert "Content-Length: 0" in raw
