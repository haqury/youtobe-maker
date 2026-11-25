"""Озвучка, картинки и — для канала сна — финальный ролик одной командой."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SLEEP_CHANNEL = "channel_02_sleep_history"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from animation_settings import load_episode_animation, resolve_video_build_script, uses_comfyui_animation


def resolve_episode(raw: str) -> Path:
    text = raw.strip().strip('"').strip("'")
    if not text:
        raise FileNotFoundError("Пустой путь к эпизоду.")

    candidate = Path(text)
    bases = [Path.cwd()]
    if candidate.is_absolute():
        options = [candidate]
    else:
        options = [(base / candidate) for base in bases]
        options.append(ROOT / candidate)
    for option in options:
        if option.is_dir():
            return option.resolve()

    token = text.replace("\\", "/").rstrip("/").split("/")[-1].casefold()
    matches: list[Path] = []
    channels = ROOT / "channels"
    if channels.is_dir():
        for episode in channels.glob("*/episodes/*"):
            if not episode.is_dir():
                continue
            name = episode.name.casefold()
            if name == token or name.startswith(token + "_"):
                matches.append(episode.resolve())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listed = "\n".join(str(path.relative_to(ROOT)) for path in matches)
        raise FileNotFoundError(f"ID {text} подходит нескольким папкам:\n{listed}")
    raise FileNotFoundError(f"Эпизод не найден: {text}")


def run_script(script_name: str, argument: str) -> None:
    command = [sys.executable, str(ROOT / "scripts" / script_name), argument]
    print()
    print(">", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return ""


def is_isekai_episode(episode_dir: Path) -> bool:
    config = _read_text(episode_dir.parents[1] / "channel_config.md")
    script = _read_text(episode_dir / "script.md")
    return "BASE_STYLE_PROMPT:" in config or "## PLAYLIST_SEQUENCE" in script


def make(episode_dir: Path) -> None:
    print(f"Эпизод: {episode_dir}")
    voice = episode_dir / "voiceover.mp3"
    voice_ru = episode_dir / "voiceover_ru.mp3"
    script_ru = episode_dir / "script_ru.md"
    need_en = not voice.is_file()
    need_ru = script_ru.is_file() and not voice_ru.is_file()
    if need_en or need_ru:
        script = episode_dir / "script.md"
        if not script.is_file():
            raise FileNotFoundError(f"Нет сценария: {script}")
        print("Шаг 1: озвучка (EN и при наличии script_ru.md — RU).")
        run_script("generate_voice.py", str(script))
    else:
        print("Шаг 1: voiceover.mp3 и voiceover_ru.mp3 уже есть, озвучку пропускаю.")

    print("Шаг 2: картинки.")
    run_script("generate_images.py", str(episode_dir))

    if SLEEP_CHANNEL in episode_dir.parts:
        anim = load_episode_animation(episode_dir)
        if uses_comfyui_animation(anim):
            print("Шаг 2b: ComfyUI image-to-video (выбранный метод).")
            run_script("animate_frames.py", str(episode_dir))
        else:
            print(f"Шаг 2b: {anim.get('animation_mode')} — ComfyUI пропускаем.")
        build_script = resolve_video_build_script(anim)
        print(f"Шаг 3: сборка final_video.mp4 ({build_script}).")
        run_script(build_script, str(episode_dir))
    elif is_isekai_episode(episode_dir):
        print("Шаг 3: isekai sleep, сборка final_video.mp4 (build_sleep_video.py).")
        run_script("build_sleep_video.py", str(episode_dir))
    else:
        print("Шаг 3: это не канал сна. Финальный рендер не запускаю, ассеты готовы.")

    print("Шаг 4: обложка thumbnail.jpg.")
    run_script("generate_thumbnail.py", str(episode_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Собрать эпизод: голос, картинки, ролик сна.")
    parser.add_argument("episode", help="Папка эпизода или его ID, например SH01")
    args = parser.parse_args(argv)
    try:
        episode_dir = resolve_episode(args.episode)
        make(episode_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print()
    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
