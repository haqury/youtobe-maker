"""Скачать только указанный кусок YouTube-ролика, без всего видео целиком."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import imageio_ffmpeg
import yt_dlp
from yt_dlp.utils import download_range_func


def parse_time(value: str) -> float:
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("Пустой таймкод.")
    parts = text.split(":")
    if len(parts) == 1:
        try:
            seconds = float(parts[0])
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Непонятный таймкод: {value}") from exc
    elif len(parts) in (2, 3):
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"Непонятный таймкод: {value}") from exc
        if len(numbers) == 2:
            seconds = numbers[0] * 60 + numbers[1]
        else:
            seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    else:
        raise argparse.ArgumentTypeError("Таймкод: секунды, ММ:СС или ЧЧ:ММ:СС.")
    if seconds < 0:
        raise argparse.ArgumentTypeError("Таймкод не может быть отрицательным.")
    return seconds


def download(url: str, start: float, end: float, output: Path, browser: str | None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    template = str(output.with_suffix("")) + ".%(ext)s"
    options = {
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": template,
        "noplaylist": True,
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
    }
    if browser:
        options["cookiesfrombrowser"] = (browser,)
    with yt_dlp.YoutubeDL(options) as loader:
        loader.download([url])

    produced = output if output.is_file() else output.with_suffix(".mp4")
    if not produced.is_file():
        matches = sorted(output.parent.glob(output.stem + ".*"), key=lambda path: path.stat().st_mtime)
        media = [path for path in matches if path.suffix.lower() in {".mp4", ".mkv", ".webm"}]
        if not media:
            raise FileNotFoundError(f"yt-dlp не сохранил файл в {output.parent}")
        produced = media[-1]
    if produced != output:
        if output.exists():
            output.unlink()
        produced.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Вырезать фрагмент YouTube-ролика по таймкодам."
    )
    parser.add_argument("--url", required=True, help="Ссылка на ролик")
    parser.add_argument("--start", required=True, type=parse_time, help="Начало: секунды, ММ:СС или ЧЧ:ММ:СС")
    parser.add_argument("--end", required=True, type=parse_time, help="Конец: секунды, ММ:СС или ЧЧ:ММ:СС")
    parser.add_argument("--out", required=True, type=Path, help="Куда сохранить mp4")
    parser.add_argument(
        "--cookies-from-browser",
        help="Браузер, где вы вошли в YouTube: edge, chrome или firefox",
    )
    args = parser.parse_args(argv)
    if args.end <= args.start:
        print("Конец должен быть позже начала.", file=sys.stderr)
        return 1
    try:
        saved = download(
            args.url,
            args.start,
            args.end,
            args.out.expanduser().resolve(),
            args.cookies_from_browser,
        )
    except Exception as exc:
        print(f"Не удалось скачать фрагмент: {exc}", file=sys.stderr)
        return 1
    print(f"Готово: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
