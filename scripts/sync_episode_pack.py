"""Фоновая переработка сценария по источнику истины (RU/EN)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from job_progress import emit_progress
from studio_ai import rework_episode_from_truth_source

INSTRUCTION_FILE = ".sync_instruction.txt"
LAST_INSTRUCTION_FILE = "sync_instruction_last.txt"


def _load_instruction(episode_dir: Path) -> str:
    for name in (INSTRUCTION_FILE, LAST_INSTRUCTION_FILE):
        path = episode_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig").strip()
        if text:
            return text
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Переработка эпизода по источнику истины")
    parser.add_argument("episode", type=Path)
    parser.add_argument(
        "--primary",
        choices=("en", "ru"),
        default="en",
        help="Источник истины: en = script.md, ru = script_ru.md",
    )
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    instruction = _load_instruction(episode_dir)

    emit_progress(0, 1, f"Переработка (источник={args.primary})")
    err = rework_episode_from_truth_source(
        episode_dir,
        primary_lang=args.primary,
        instruction=instruction,
    )
    if err:
        print(err, file=sys.stderr)
        emit_progress(1, 1, f"Ошибка: {err[:120]}")
        return 1
    instr_path = episode_dir / INSTRUCTION_FILE
    if instr_path.is_file():
        try:
            instr_path.unlink(missing_ok=True)
        except OSError:
            pass
    emit_progress(1, 1, "Переработка завершена")
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())