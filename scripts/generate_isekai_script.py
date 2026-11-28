"""Сценарий isekai-сна из трёх частей в script.md.

1. Основной сюжет на родном языке (ACT_1–ACT_6).
2. RECALL: сопоставление каждой сцены, перевод 1:1.
3. Тот же сюжет на выбранном языке (PLOT_TARGET_1–3).

Короткие вставки TARGET и их _translate внутри актов в RECALL и PLOT_TARGET не копируются.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from rebuild_isekai_v4 import write_three_part_script


def generate(episode_dir: Path) -> int:
    try:
        return write_three_part_script(episode_dir)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(
            "Использование: python scripts/generate_isekai_script.py <папка эпизода>",
            file=sys.stderr,
        )
        return 2
    return generate(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
