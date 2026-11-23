"""Маппинг участков → реген помеченных кадров → пересборка видео."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from episode_review import apply_video_segments_to_images, flagged_stems, load_review
from generate_images import regenerate_flagged_images
from job_progress import emit_progress

SLEEP_CHANNEL = "channel_02_sleep_history"
ROOT = Path(__file__).resolve().parents[1]


def build_script_for(episode_dir: Path) -> str:
    if SLEEP_CHANNEL in episode_dir.parts:
        return "build_sleep_video.py"
    return "make_episode.py"


def run_pipeline(episode_dir: Path) -> int:
    review = load_review(episode_dir)
    stems = sorted(flagged_stems(review))
    segment_count = len([s for s in review.get("video_segments", []) if str(s.get("note", "")).strip()])
    total = 1 + len(stems) + 1
    if total < 2:
        total = 2

    emit_progress(0, total, "Подготовка")
    emit_progress(1, total, "Маппинг участков видео на кадры")
    added, updated = apply_video_segments_to_images(episode_dir)
    print(f"Участков с текстом: {segment_count}. Новых пометок: {added}, обновлено: {updated}.")

    stems = sorted(flagged_stems(load_review(episode_dir)))
    if stems:
        def on_frame(index: int, frame_total: int, label: str) -> None:
            emit_progress(1 + index, total, label)

        regenerate_flagged_images(episode_dir, on_frame=on_frame)
    elif total > 2:
        emit_progress(2, total, "Нет помеченных кадров — пропуск регенерации")

    script = build_script_for(episode_dir)
    emit_progress(total, total, f"Сборка видео ({script})")
    command = [sys.executable, "-u", str(_SCRIPTS / script), str(episode_dir)]
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"Сборка завершилась с кодом {result.returncode}", file=sys.stderr)
        return result.returncode

    emit_progress(total, total, "Готово")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Применить review.json и пересобрать видео.")
    parser.add_argument("episode", type=Path, help="Папка эпизода")
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1
    try:
        return run_pipeline(episode_dir)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())