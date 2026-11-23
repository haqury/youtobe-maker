"""Фоновая генерация текстового пакета эпизода через Workers AI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from job_progress import emit_progress
from studio_ai import generate_episode_script_pack


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ИИ: script / script_ru / visual_prompts / metadata")
    parser.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    emit_progress(0, 20, "Поглавная генерация (outline + 5 глав + промпты + RU)")
    err = generate_episode_script_pack(episode_dir)
    if err:
        print(err, file=sys.stderr)
        emit_progress(19, 20, "Ошибка")
        return 1
    emit_progress(20, 20, "Тексты сохранены")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())