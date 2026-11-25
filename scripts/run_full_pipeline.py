"""Полный конвейер студии: тексты (если нет) → озвучка → кадры и обложка → видео сна."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from animation_settings import load_episode_animation, resolve_video_build_script, uses_comfyui_animation
from job_progress import emit_progress
from stdio_utf8 import configure_utf8_stdio, subprocess_env
from studio_ai import (
    episode_plan_summary,
    generate_episode_script_pack,
    load_plot_brief,
    script_needs_generation,
)

SLEEP_CHANNEL = "channel_02_sleep_history"


def run_step(script: str, episode_dir: Path) -> int:
    command = [sys.executable, "-u", str(_SCRIPTS / script), str(episode_dir)]
    print(">", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=str(ROOT), env=subprocess_env())
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Full pipeline для эпизода")
    parser.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    episode_dir = args.episode.expanduser().resolve()
    if not episode_dir.is_dir():
        print(f"Папка не найдена: {episode_dir}", file=sys.stderr)
        return 1

    from isekai_config import load_isekai_settings

    is_isekai = bool(load_isekai_settings(episode_dir).get("TARGET_LANG"))
    is_sleep = SLEEP_CHANNEL in episode_dir.parts or is_isekai
    total = 5 if is_sleep else 4
    emit_progress(0, total, "Старт полного конвейера")

    channel_slug = episode_dir.parents[1].name
    brief = load_plot_brief(episode_dir)
    plan = episode_plan_summary(episode_dir, channel_slug)
    if brief:
        print(f"plot_brief.txt: {brief[:200]}{'…' if len(brief) > 200 else ''}", flush=True)
    print(f"episode_config → {plan}", flush=True)

    if script_needs_generation(episode_dir):
        emit_progress(1, total, "ИИ: сценарий, RU, промпты, metadata")
        err = generate_episode_script_pack(episode_dir)
        if err:
            print(err, file=sys.stderr)
            return 1
    else:
        emit_progress(1, total, "Сценарий уже есть — пропуск ИИ")

    emit_progress(2, total, "Озвучка уникальных фраз" if is_isekai else "Озвучка EN + RU")
    script_path = episode_dir / "script.md"
    if not script_path.is_file():
        print("Нет script.md после генерации.", file=sys.stderr)
        return 1
    voice_cmd = [sys.executable, "-u", str(_SCRIPTS / "generate_voice.py"), str(script_path)]
    if subprocess.run(voice_cmd, cwd=str(ROOT), env=subprocess_env()).returncode != 0:
        return 1

    emit_progress(3, total, "Кадры и обложка")
    if run_step("run_images_pack.py", episode_dir) != 0:
        return 1

    if is_isekai:
        emit_progress(4, total, "Сборка длинного видео из кэша кадров")
        emit_progress(5, total, "Рендер final_video.mp4 (build_sleep_video.py)")
        if run_step("build_sleep_video.py", episode_dir) != 0:
            return 1
    elif is_sleep:
        anim = load_episode_animation(episode_dir)
        if uses_comfyui_animation(anim):
            emit_progress(4, total, "ComfyUI: image-to-video (выбранные кадры)")
            if run_step("animate_frames.py", episode_dir) != 0:
                return 1
        else:
            mode = str(anim.get("animation_mode", "ffmpeg_25d"))
            emit_progress(4, total, f"Сборка: {mode} (ComfyUI пропущен)")
            print(f"Метод анимации: {mode} — animate_frames.py не запускаем.")
        build_script = resolve_video_build_script(anim)
        emit_progress(5, total, f"Рендер final_video.mp4 ({build_script})")
        if run_step(build_script, episode_dir) != 0:
            return 1
    else:
        emit_progress(4, total, "Финал (без рендера)")
        print("Не канал сна — рендер видео пропущен.")

    emit_progress(total, total, "Готово")
    print("Полный конвейер завершён.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())