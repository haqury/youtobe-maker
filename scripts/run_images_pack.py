"""Картинки и обложка в одной фоновой задаче."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from job_progress import emit_progress

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate_images + generate_thumbnail")
    parser.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    emit_progress(0, 2, "Старт")
    steps = [
        ("generate_images.py", "Генерация кадров"),
        ("generate_thumbnail.py", "Генерация обложки"),
    ]
    for index, (script, label) in enumerate(steps, start=1):
        emit_progress(index, 2, label)
        command = [sys.executable, "-u", str(SCRIPTS / script), str(episode_dir)]
        result = subprocess.run(command, cwd=str(ROOT))
        if result.returncode != 0:
            return result.returncode
    emit_progress(2, 2, "Готово")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())