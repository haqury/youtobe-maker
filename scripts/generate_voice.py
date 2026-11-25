"""Озвучка script.md и при наличии script_ru.md через edge-tts."""

from __future__ import annotations

import argparse
import asyncio
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import edge_tts
import imageio_ffmpeg

from job_progress import emit_progress
from stdio_utf8 import configure_utf8_stdio

SLEEP_CHANNEL = "channel_02_sleep_history"
SLEEP_VOICE_EN = "en-US-BrianNeural"
DEFAULT_PITCH = "-4Hz"
SLEEP_RATE = "-12%"
DEFAULT_VOICE_EN = "en-US-AndrewMultilingualNeural"
DEFAULT_RATE_EN = "+0%"
VOICE_RU = "ru-RU-DmitryNeural"
VOICE_RU_FALLBACKS = (
    "ru-RU-DmitryNeural",
    "ru-RU-SvetlanaNeural",
    "ru-RU-DariyaNeural",
)

CHUNK_MAX_CHARS = 280
TTS_CHUNK_TIMEOUT_SEC = 120
TTS_ATTEMPTS = 3
CHUNK_PAUSE_SEC = (1.5, 3.0)

BRACKET_RE = re.compile(r"\[[^\]]*\]")
MARKDOWN_RE = re.compile(r"[#*_>]")
BLANK_RUN_RE = re.compile(r"\n{3,}")
SPACE_RE = re.compile(r"[ \t]{2,}")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def clean_script(text: str) -> str:
    """Убрать теги, комментарии в скобках и символы Markdown, которые нельзя читать вслух."""
    text = BRACKET_RE.sub("", text)
    text = MARKDOWN_RE.sub("", text)
    lines = [SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return BLANK_RUN_RE.sub("\n\n", text).strip()


def split_for_tts(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Разбить длинный сценарий — Edge TTS отваливается на одном куске ~5k символов."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer.strip():
            chunks.append(buffer.strip())
        buffer = ""

    def emit_piece(piece: str) -> None:
        nonlocal buffer
        piece = piece.strip()
        if not piece:
            return
        while len(piece) > max_chars:
            chunks.append(piece[:max_chars].strip())
            piece = piece[max_chars:].strip()
        if not buffer:
            buffer = piece
            return
        candidate = f"{buffer}\n\n{piece}"
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            flush()
            buffer = piece

    for paragraph in (p.strip() for p in text.split("\n\n") if p.strip()):
        if len(paragraph) <= max_chars:
            emit_piece(paragraph)
            continue
        for sentence in SENTENCE_SPLIT_RE.split(paragraph):
            emit_piece(sentence)

    flush()
    return chunks or [text[:max_chars]]


def concat_mp3(parts: list[Path], output: Path) -> None:
    if not parts:
        raise RuntimeError("Нет фрагментов для склейки озвучки.")
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        return
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    list_file = output.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in parts),
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat: {(proc.stderr or proc.stdout or '')[:400]}"
            )
    finally:
        list_file.unlink(missing_ok=True)


def resolve_script_path(raw: Path) -> tuple[Path, Path]:
    path = raw.expanduser().resolve()
    if path.is_dir():
        script = path / "script.md"
        if not script.is_file():
            raise FileNotFoundError(f"В папке нет script.md: {path}")
        return script, path
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")
    return path, path.parent


def _channel_audio(episode_dir: Path) -> dict:
    try:
        from channel_settings import channel_dir_from_episode, load_channel_settings

        channel_dir = channel_dir_from_episode(episode_dir)
        return load_channel_settings(channel_dir).get("audio", {})
    except Exception:
        return {}


def english_voice(episode_dir: Path) -> tuple[str, str]:
    audio = _channel_audio(episode_dir)
    voice = str(audio.get("voice_en", "")).strip()
    rate = str(audio.get("speech_rate", "")).strip()
    if not voice:
        voice = SLEEP_VOICE_EN if SLEEP_CHANNEL in episode_dir.parts else DEFAULT_VOICE_EN
    if not rate:
        rate = SLEEP_RATE if SLEEP_CHANNEL in episode_dir.parts else DEFAULT_RATE_EN
    return voice, rate


def russian_voice(episode_dir: Path) -> tuple[str, str]:
    audio = _channel_audio(episode_dir)
    voice = str(audio.get("voice_ru", "")).strip() or VOICE_RU
    rate = str(audio.get("speech_rate", "")).strip()
    if not rate:
        rate = SLEEP_RATE if SLEEP_CHANNEL in episode_dir.parts else DEFAULT_RATE_EN
    return voice, rate


async def _write_stream(communicate: edge_tts.Communicate, output: Path) -> None:
    bytes_written = 0
    with output.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk.get("type") != "audio":
                continue
            data = chunk.get("data") or b""
            if data:
                handle.write(data)
                bytes_written += len(data)
    if bytes_written < 256:
        raise RuntimeError(
            "No audio was received. Please verify that your parameters are correct."
        )


def _discard_output(output: Path) -> bool:
    """Удалить обрезок mp3. После таймаута Windows часто ещё держит файл."""
    for delay in (0.0, 0.25, 0.5, 1.0, 1.5):
        if delay:
            time.sleep(delay)
        try:
            output.unlink(missing_ok=True)
        except PermissionError:
            continue
        return True
    return False


def _ensure_mp3_output(output: Path) -> None:
    if not output.is_file():
        raise RuntimeError("No audio was received. Please verify that your parameters are correct.")
    if output.stat().st_size < 256:
        raise RuntimeError(
            "No audio was received. Please verify that your parameters are correct."
        )


async def synthesize_chunk_cli(
    text: str,
    voice: str,
    rate: str,
    output: Path,
    pitch: str = DEFAULT_PITCH,
) -> None:
    """Запасной путь: отдельный процесс edge-tts (иногда стабильнее WebSocket в asyncio)."""
    if not _discard_output(output):
        raise PermissionError(f"Файл занят другим процессом: {output}")
    text_file = output.with_suffix(".cli_input.txt")
    try:
        text_file.write_text(text, encoding="utf-8")
        # Значения вроде -12% argparse CLI воспринимает как новый флаг без «=».
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "edge_tts",
            "--voice",
            voice,
            f"--rate={rate}",
            f"--pitch={pitch}",
            "--file",
            str(text_file),
            "--write-media",
            str(output),
            "--no-progress",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=TTS_CHUNK_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"edge_tts CLI код {proc.returncode}: {err[:400]}")
        _ensure_mp3_output(output)
    finally:
        text_file.unlink(missing_ok=True)
        if not output.is_file():
            _discard_output(output)


async def synthesize_chunk(
    text: str,
    voice: str,
    rate: str,
    output: Path,
    pitch: str = DEFAULT_PITCH,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, TTS_ATTEMPTS + 1):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

            async def run() -> None:
                _discard_output(output)
                await _write_stream(communicate, output)

            await asyncio.wait_for(run(), timeout=TTS_CHUNK_TIMEOUT_SEC)
            return
        except Exception as exc:
            last_error = exc
            _discard_output(output)
            if attempt < TTS_ATTEMPTS:
                print(
                    f"TTS stream повтор {attempt}/{TTS_ATTEMPTS}: {exc!r}",
                    file=sys.stderr,
                )
                await asyncio.sleep(2.0 * attempt)
    detail = repr(last_error) if last_error else "неизвестная ошибка"
    raise RuntimeError(f"stream: {detail}") from last_error


async def synthesize_chunk_with_voices(
    text: str,
    voices: tuple[str, ...],
    rate: str,
    output: Path,
    pitch: str = DEFAULT_PITCH,
) -> str:
    errors: list[str] = []
    for voice in voices:
        try:
            await synthesize_chunk(text, voice, rate, output, pitch=pitch)
            if voice != voices[0]:
                print(f"TTS: использован запасной голос {voice}")
            return voice
        except Exception as exc:
            errors.append(f"{voice} stream: {exc!r}")
            _discard_output(output)
            try:
                await synthesize_chunk_cli(text, voice, rate, output, pitch=pitch)
                print(f"TTS: часть через CLI (голос {voice})")
                if voice != voices[0]:
                    print(f"TTS: использован запасной голос {voice}")
                return voice
            except Exception as cli_exc:
                errors.append(f"{voice} CLI: {cli_exc!r}")
                _discard_output(output)
    hint = (
        " Проверьте интернет/VPN и повторите позже: Edge TTS ходит на сервера Microsoft."
    )
    raise RuntimeError("Edge TTS: " + "; ".join(errors) + hint)


async def synthesize(
    text: str,
    voice: str,
    rate: str,
    output: Path,
    *,
    voice_fallbacks: tuple[str, ...] | None = None,
    pitch: str = DEFAULT_PITCH,
) -> None:
    voices = voice_fallbacks or (voice,)
    chunks = split_for_tts(text)
    if len(chunks) == 1:
        await synthesize_chunk_with_voices(chunks[0], voices, rate, output, pitch=pitch)
        return

    print(f"TTS: {len(chunks)} частей (≤{CHUNK_MAX_CHARS} симв. на запрос)")
    temp_dir = output.parent / f".tts_chunks_{output.stem}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    voice_lock_path = temp_dir / "_locked_voice.txt"
    part_paths: list[Path] = []
    try:
        locked_voice: str | None = None
        if voice_lock_path.is_file():
            saved = voice_lock_path.read_text(encoding="utf-8").strip()
            if saved:
                locked_voice = saved
        for index, chunk in enumerate(chunks, start=1):
            part = temp_dir / f"{index:03d}.mp3"
            if part.is_file() and part.stat().st_size > 256:
                print(f"TTS часть {index}/{len(chunks)}: уже готова, пропуск ({part.name})")
                part_paths.append(part)
                continue
            print(f"TTS часть {index}/{len(chunks)}: {len(chunk)} симв.")
            use_voices = (locked_voice,) if locked_voice else voices
            locked_voice = await synthesize_chunk_with_voices(
                chunk, use_voices, rate, part, pitch=pitch
            )
            voice_lock_path.write_text(locked_voice, encoding="utf-8")
            part_paths.append(part)
            if index < len(chunks):
                pause = random.uniform(*CHUNK_PAUSE_SEC)
                print(f"TTS: пауза {pause:.1f} с перед следующей частью")
                await asyncio.sleep(pause)
        concat_mp3(part_paths, output)
        voice_lock_path.unlink(missing_ok=True)
    finally:
        if output.is_file() and output.stat().st_size > 256:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def voice_file(
    script_path: Path,
    output_path: Path,
    voice: str,
    rate: str,
    label: str,
) -> None:
    raw = script_path.read_text(encoding="utf-8-sig")
    text = clean_script(raw)
    if not text:
        raise RuntimeError(f"{label}: после очистки сценарий пуст.")
    print(f"{label}: голос {voice}, темп {rate}")
    print(f"{label}: символов {len(text)} -> {output_path.name}")
    fallbacks = VOICE_RU_FALLBACKS if label == "RU" else (voice,)
    await synthesize(
        text,
        voice,
        rate,
        output_path,
        voice_fallbacks=fallbacks,
    )


def needs_refresh(script: Path, output: Path) -> bool:
    if not output.is_file():
        return True
    return script.stat().st_mtime > output.stat().st_mtime


async def voice_episode(episode_dir: Path, script_en: Path) -> None:
    """Isekai-разметка уходит в isekai_voice. В mode=target_only слоты с
    суффиксом _repeat не попадают в план: фаза 4 говорит каждую фразу
    изучаемого языка один раз, без медленного дубля.
    Isekai: только script.md и audio_cache/ эпизода (без channel.db).
    """
    from isekai_voice import maybe_render

    if await maybe_render(episode_dir, script_en):
        return
    script_ru = episode_dir / "script_ru.md"
    steps = 1 + (1 if script_ru.is_file() else 0)
    step = 0

    output_en = episode_dir / "voiceover.mp3"
    step += 1
    emit_progress(step, steps, "Озвучка EN (voiceover.mp3)")
    if needs_refresh(script_en, output_en):
        voice_en, rate_en = english_voice(episode_dir)
        await voice_file(script_en, output_en, voice_en, rate_en, "EN")
    else:
        print("EN: voiceover.mp3 актуален, пропускаю.")

    if not script_ru.is_file():
        print("script_ru.md нет — русскую дорожку не делаю.")
        emit_progress(steps, steps, "Готово")
        return

    output_ru = episode_dir / "voiceover_ru.mp3"
    step += 1
    emit_progress(step, steps, "Озвучка RU (voiceover_ru.mp3)")
    if not needs_refresh(script_ru, output_ru):
        print("RU: voiceover_ru.mp3 актуален, пропускаю.")
        emit_progress(steps, steps, "Готово")
        return

    voice_ru, rate_ru = russian_voice(episode_dir)
    await voice_file(script_ru, output_ru, voice_ru, rate_ru, "RU")
    emit_progress(steps, steps, "Готово")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Озвучить script.md → voiceover.mp3 и script_ru.md → voiceover_ru.mp3."
    )
    parser.add_argument(
        "script",
        type=Path,
        help="Путь к script.md или папке эпизода",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        script_path, episode_dir = resolve_script_path(args.script)
        asyncio.run(voice_episode(episode_dir, script_path))
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except UnicodeDecodeError as exc:
        print(f"Сценарий должен быть в UTF-8: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Озвучка не удалась: {exc}", file=sys.stderr)
        return 1

    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())