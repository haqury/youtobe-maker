"""Isekai / comprehensible-input озвучка: разметка [id|ROLE|speed] + PLAYLIST_SEQUENCE.

Чистый парсинг + рендер через кэш mp3 и ffmpeg. Старые prose-сценарии сюда не попадают
(maybe_render возвращает False). Самотест без сети: python scripts/isekai_voice.py --self-test
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from isekai_config import load_isekai_settings, speech_pitch, speech_speed_bias

SPEECH_RE = re.compile(r"^\[(\d+)\|([A-Za-z][A-Za-z0-9_]*)\|(\d+(?:\.\d+)?)\]\s+(\S.*?)\s*$")
DETECT_RE = re.compile(r"^\[\d+\|[A-Za-z][A-Za-z0-9_]*\|\d+(?:\.\d+)?\]\s+\S", re.MULTILINE)
PLAYLIST_RE = re.compile(r"^##[ \t]+PLAYLIST_SEQUENCE[ \t]*$", re.MULTILINE)
BLOCK_RE = re.compile(r"^##[ \t]+BLOCK:[ \t]*(\S+)[ \t]*$")
PAUSE_RE = re.compile(r"^\[PAUSE\|(\d+(?:\.\d+)?)\]\s*$")
PLAY_RE = re.compile(r"^PLAY:\s*(.+?)\s*\((.*)\)\s*$")

KNOWN_ROLES = ("MENTOR_NATIVE", "MENTOR_TARGET", "NPC_TARGET")


def voice_setting_key(slot: str) -> str:
    """Слот разметки → ключ голоса в channel_config.md.

    MENTOR_TARGET_es и MENTOR_TARGET_es_slow делят голос MENTOR_TARGET.
    transitions_bank_* говорит голосом MENTOR_NATIVE.
    Старые слоты без суффикса языка остаются как есть.
    """
    low = slot.lower()
    if low.startswith("transitions_bank"):
        return "MENTOR_NATIVE"
    if low.startswith("mentor_target") or low.startswith("narrator_target"):
        return "MENTOR_TARGET"
    if low.startswith("mentor_native") or low.startswith("narrator_native"):
        return "MENTOR_NATIVE"
    if low.startswith("npc_target"):
        return "NPC_TARGET"
    return slot
MODES = ("full", "target_only", "shuffle_target")
DEFAULT_EXTRA_PAUSE = 2.0  # для target-режимов, если extra_pause не указан
SAMPLE_RATE = 24000
MP3_ENCODERS = ("libmp3lame", "mp3_mf")  # libmp3lame первым; mp3_mf — запасной (Windows)
VELVET_AFILTER = "lowpass=f=5500,bass=g=3:f=120,loudnorm=I=-18:TP=-2:LRA=7"
SLEEP_PITCH = "-4Hz"


@dataclass(frozen=True)
class Line:
    chunk_id: str
    role: str
    speed: float
    text: str


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass(frozen=True)
class Play:
    block: str
    mode: str
    speed_mult: float
    extra_pause: float
    repeat: int
    apply_full_extra: bool = False


@dataclass(frozen=True)
class Step:
    """Один шаг плана: речь (с множителем темпа) или тишина."""

    line: Line | None
    seconds: float
    speed_mult: float = 1.0


def is_isekai_script(text: str) -> bool:
    return bool(DETECT_RE.search(text)) and bool(PLAYLIST_RE.search(text))


def is_target_role(role: str) -> bool:
    """Фразы сна: слот содержит _TARGET_ и это не повтор _slow или _repeat.

    Старые слоты MENTOR_TARGET и NPC_TARGET без суффикса языка тоже входят.
    Категория банка how_it_sounds_in_target не считается целевой речью.
    """
    low = role.lower()
    if low.startswith("transitions_bank") or "_slow" in low or "_repeat" in low:
        return False
    if "_target_" in low:
        return True
    return low in {"mentor_target", "npc_target"}


def parse_script(text: str) -> tuple[dict[str, list[Line | Pause]], list[Play]]:
    blocks: dict[str, list[Line | Pause]] = {}
    plays: list[Play] = []
    current: str | None = None
    in_playlist = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            block = BLOCK_RE.match(line)
            if block:
                current = block.group(1)
                in_playlist = False
                blocks.setdefault(current, [])
            elif PLAYLIST_RE.match(line):
                current = None
                in_playlist = True
            elif line.startswith("##"):
                # прочие markdown-заголовки не меняют секцию, но и не читаются
                pass
            continue
        if in_playlist:
            play = PLAY_RE.match(line)
            if play:
                names = [name.strip().rstrip(",") for name in play.group(1).split(",")]
                params = play.group(2)
                for name in names:
                    if name:
                        plays.append(_parse_play(name, params))
            continue
        if current is None:
            continue
        speech = SPEECH_RE.match(line)
        if speech:
            blocks[current].append(
                Line(speech.group(1), speech.group(2), float(speech.group(3)), speech.group(4))
            )
            continue
        pause = PAUSE_RE.match(line)
        if pause:
            blocks[current].append(Pause(float(pause.group(1))))
    return blocks, plays


def _parse_play(block: str, params: str) -> Play:
    values: dict[str, str] = {}
    for part in params.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
    mode = values.get("mode", "full")
    if mode not in MODES:
        raise SystemExit(f"Неизвестный mode в PLAY: {mode} (блок {block}). Допустимо: {', '.join(MODES)}.")
    try:
        return Play(
            block=block,
            mode=mode,
            speed_mult=float(values.get("speed_mult", "1")),
            extra_pause=float(values.get("extra_pause", str(DEFAULT_EXTRA_PAUSE))),
            repeat=max(1, int(values.get("repeat", "1"))),
            apply_full_extra="extra_pause" in values,
        )
    except ValueError as exc:
        raise SystemExit(f"Некорректные параметры PLAY ({block}): {params} — {exc}")


def plan_for_play(blocks: dict[str, list[Line | Pause]], play: Play) -> list[Step]:
    if play.block not in blocks:
        raise SystemExit(f"Неизвестный блок в PLAY: {play.block}")
    items = blocks[play.block]
    steps: list[Step] = []
    for rep in range(play.repeat):
        if play.mode == "full":
            for item in items:
                if isinstance(item, Line):
                    steps.append(Step(item, 0.0, play.speed_mult))
                    if play.apply_full_extra and play.extra_pause > 0:
                        steps.append(Step(None, play.extra_pause))
                else:
                    steps.append(Step(None, item.seconds))
            continue
        targets = [i for i in items if isinstance(i, Line) and is_target_role(i.role)]
        if play.mode == "shuffle_target":
            random.Random(f"{play.block}:{rep}:{play.mode}").shuffle(targets)
        for item in targets:
            steps.append(Step(item, 0.0, play.speed_mult))
            if play.extra_pause > 0:
                steps.append(Step(None, play.extra_pause))
    return steps


def build_plan(text: str) -> list[Step]:
    blocks, plays = parse_script(text)
    if not plays:
        raise SystemExit("В PLAYLIST_SEQUENCE нет строк PLAY:.")
    plan: list[Step] = []
    for play in plays:
        plan.extend(plan_for_play(blocks, play))
    return plan


def rate_for_speed(speed: float) -> str:
    pct = round((speed - 1) * 100)
    return f"{pct:+d}%"


def effective_tts_speed(line_speed: float, bias: float) -> float:
    return max(0.5, min(1.0, line_speed * bias))


def cache_name(
    chunk_id: str,
    role: str,
    voice: str,
    speed: float,
    text: str,
    *,
    bias: float = 1.0,
    pitch: str = "",
) -> str:
    digest = hashlib.md5(
        f"{voice}\n{format(bias, 'g')}\n{format(speed, 'g')}\n{pitch}\n{text}".encode("utf-8")
    ).hexdigest()
    return f"{chunk_id}_{role}_{digest}.mp3"


# ---------------------------------------------------------------- ffmpeg

def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _encode_mp3(pre_args: list[str], output: Path) -> None:
    """ffmpeg с mp3-кодеком: сначала libmp3lame, при ошибке — запасной."""
    last = ""
    for encoder in MP3_ENCODERS:
        proc = _run_ffmpeg([*pre_args, "-c:a", encoder, "-ar", str(SAMPLE_RATE), "-ac", "1", str(output)])
        if proc.returncode == 0 and output.is_file() and output.stat().st_size > 0:
            return
        last = f"{encoder}: {(proc.stderr or '')[:300]}"
        output.unlink(missing_ok=True)
    raise RuntimeError(f"ffmpeg не смог записать mp3 ({last})")


def ensure_silence(cache: Path, seconds: float) -> Path:
    ms = round(seconds * 1000)
    path = cache / f"silence_{ms}.mp3"
    if path.is_file() and path.stat().st_size > 0:
        return path
    _encode_mp3(
        ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono", "-t", f"{ms / 1000:.3f}"],
        path,
    )
    return path


def _atempo_chain(factor: float) -> str:
    parts: list[str] = []
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    parts.append(f"atempo={factor:.4f}")
    return ",".join(parts)


def ensure_tempo(source: Path, speed_mult: float) -> Path:
    if speed_mult == 1:
        return source
    path = source.with_name(f"{source.stem}_x{format(speed_mult, 'g')}.mp3")
    if path.is_file() and path.stat().st_size > 0:
        return path
    _encode_mp3(["-i", str(source), "-filter:a", _atempo_chain(speed_mult)], path)
    return path


def velvet_filter(atempo: float | None = None) -> str:
    """Ночная обработка: тёплый низ, срез шипящих, ровная громкость.

    atempo только для уже синтезированного файла, который не хочется озвучивать заново.
    """
    parts: list[str] = []
    if atempo is not None and abs(atempo - 1.0) > 1e-6:
        parts.append(_atempo_chain(atempo))
    parts.append(VELVET_AFILTER)
    return ",".join(parts)


def concat_to_mp3(parts: list[Path], output: Path, cache: Path) -> None:
    list_file = cache / "_concat_list.txt"
    escaped = (str(p.resolve()).replace("\\", "/").replace("'", "'\\''") for p in parts)
    list_file.write_text("\n".join(f"file '{p}'" for p in escaped) + "\n", encoding="utf-8")
    audio_filter = velvet_filter()
    try:
        proc = _run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-af", audio_filter,
                "-c:a", "libmp3lame", "-ar", str(SAMPLE_RATE), "-ac", "1", str(output),
            ]
        )
        if proc.returncode != 0:
            output.unlink(missing_ok=True)
            proc = _run_ffmpeg(
                [
                    "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-af", audio_filter,
                    "-c:a", MP3_ENCODERS[1], "-ar", str(SAMPLE_RATE), "-ac", "1", str(output),
                ]
            )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat: {(proc.stderr or '')[:400]}")
    finally:
        list_file.unlink(missing_ok=True)


def soften_master(source: Path, output: Path, *, atempo: float | None = None) -> None:
    """Та же бархатная цепочка на готовом mp3, без повторного синтеза реплик."""
    _encode_mp3(["-i", str(source), "-af", velvet_filter(atempo)], output)


# ---------------------------------------------------------------- TTS

async def _synthesize(text: str, voice: str, rate: str, output: Path, pitch: str = SLEEP_PITCH) -> None:
    try:
        from generate_voice import synthesize_chunk_with_voices
    except ImportError:
        synthesize_chunk_with_voices = None
    if synthesize_chunk_with_voices is not None:
        # Тот же путь, что у обычной озвучки: после TimeoutError вебсокета
        # пробует edge-tts CLI, иначе одна реплика обрывает всю ночь.
        await synthesize_chunk_with_voices(text, (voice,), rate, output, pitch=pitch)
        return
    import edge_tts

    output.unlink(missing_ok=True)
    with output.open("wb") as handle:
        async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
            if chunk.get("type") == "audio":
                handle.write(chunk.get("data") or b"")


def _voices_for(plan: list[Step], settings: dict[str, str]) -> dict[str, str]:
    voices: dict[str, str] = {}
    for step in plan:
        if step.line is None or step.line.role in voices:
            continue
        slot = step.line.role
        voice_key = voice_setting_key(slot)
        voice = settings.get(voice_key, "").strip()
        if not voice:
            hint = "Ключи голосов: " + ", ".join(KNOWN_ROLES) + "."
            raise SystemExit(
                f"Для слота {slot} не задан голос {voice_key} в channel_config.md "
                f"(строка вида `{voice_key}: es-ES-AlvaroNeural`). {hint}"
            )
        voices[slot] = voice
    return voices


def _take_path(cache: Path, line: Line, voice: str, bias: float = 1.0, pitch: str = "") -> Path:
    """Mp3 только в audio_cache эпизода (имя по тексту/голосу/темпу). Без channel.db."""
    filename = cache_name(
        line.chunk_id, line.role, voice, line.speed, line.text, bias=bias, pitch=pitch
    )
    return cache / filename


async def render(episode_dir: Path, script_path: Path) -> Path:
    text = script_path.read_text(encoding="utf-8-sig")
    plan = build_plan(text)  # падает до сети, если блок/режим неизвестны
    settings = load_isekai_settings(episode_dir)
    bias = speech_speed_bias(settings)
    pitch = speech_pitch(settings)
    voices = _voices_for(plan, settings)
    cache = episode_dir / "audio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    cached = synthesized = 0
    speech_files: dict[tuple[str, str, float, str], Path] = {}
    for step in plan:
        line = step.line
        if line is None:
            continue
        key = (line.chunk_id, line.role, line.speed, line.text)
        if key in speech_files:
            continue
        voice = voices[line.role]
        path = _take_path(cache, line, voice, bias, pitch)
        if path.is_file() and path.stat().st_size > 0:
            cached += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            tts_speed = effective_tts_speed(line.speed, bias)
            print(f"TTS {line.chunk_id} {line.role}: {line.text[:60]}")
            await _synthesize(line.text, voice, rate_for_speed(tts_speed), path, pitch=pitch)
            synthesized += 1
        speech_files[key] = path
    print(f"Isekai озвучка: из кэша {cached}, синтезировано {synthesized} реплик.")

    parts: list[Path] = []
    for step in plan:
        if step.line is None:
            parts.append(ensure_silence(cache, step.seconds))
        else:
            line = step.line
            source = speech_files[(line.chunk_id, line.role, line.speed, line.text)]
            parts.append(ensure_tempo(source, step.speed_mult))
    output = episode_dir / "voiceover.mp3"
    concat_to_mp3(parts, output, cache)
    print(f"Isekai: {len(parts)} фрагментов -> {output.name}")
    return output


def phrase_inventory(episode_dir: Path, text: str | None = None) -> dict:
    """Уникальные реплики и попадает ли mp3 в audio_cache. Без синтеза и без ffmpeg."""
    script_path = episode_dir / "script.md"
    if text is None:
        if not script_path.is_file():
            return {"ok": False, "error": "Нет script.md", "rows": [], "plays": []}
        text = script_path.read_text(encoding="utf-8-sig")
    if not is_isekai_script(text):
        return {
            "ok": False,
            "error": "В script.md нет строк [ID|ROLE|speed] и секции ## PLAYLIST_SEQUENCE.",
            "rows": [],
            "plays": [],
        }
    try:
        blocks, plays = parse_script(text)
        plan = build_plan(text)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc), "rows": [], "plays": []}
    settings = load_isekai_settings(episode_dir)
    bias = speech_speed_bias(settings)
    pitch = speech_pitch(settings)
    cache = episode_dir / "audio_cache"
    rows: list[dict] = []
    seen: set[tuple[str, str, float]] = set()
    for block_name, items in blocks.items():
        for item in items:
            if not isinstance(item, Line):
                continue
            key = (item.chunk_id, item.role, item.speed, item.text)
            if key in seen:
                continue
            seen.add(key)
            voice = settings.get(voice_setting_key(item.role), "").strip()
            filename = (
                cache_name(
                    item.chunk_id, item.role, voice, item.speed, item.text, bias=bias, pitch=pitch
                )
                if voice
                else ""
            )
            path = cache / filename if filename else None
            cached = bool(path and path.is_file() and path.stat().st_size > 0)
            rows.append(
                {
                    "block": block_name,
                    "id": item.chunk_id,
                    "role": item.role,
                    "speed": item.speed,
                    "text": item.text,
                    "voice": voice or "— нет в channel_config.md",
                    "cached": cached,
                }
            )
    return {
        "ok": True,
        "error": "",
        "rows": rows,
        "plays": [
            {
                "block": play.block,
                "mode": play.mode,
                "repeat": play.repeat,
                "speed_mult": play.speed_mult,
                "extra_pause": play.extra_pause,
            }
            for play in plays
        ],
        "unique": len(rows),
        "cached": sum(1 for row in rows if row["cached"]),
        "missing": sum(1 for row in rows if not row["cached"]),
        "timeline_lines": sum(1 for step in plan if step.line is not None),
        "target_lang": settings.get("TARGET_LANG", ""),
        "native_lang": settings.get("NATIVE_LANG", ""),
    }


async def maybe_render(episode_dir: Path, script_path: Path) -> bool:
    text = script_path.read_text(encoding="utf-8-sig")
    if not is_isekai_script(text):
        return False
    await render(episode_dir, script_path)
    return True


# ---------------------------------------------------------------- self-test

def self_test() -> None:
    root = _SCRIPTS.parent
    sample = root / "templates" / "template_isekai_sleep" / "episodes" / "EP01_example" / "script.md"
    text = sample.read_text(encoding="utf-8-sig")
    assert is_isekai_script(text), "образец не распознан как isekai"
    blocks, plays = parse_script(text)
    assert len(blocks) == 2, f"ожидалось 2 блока, найдено {len(blocks)}"
    assert len(plays) == 6, f"ожидалось 6 PLAY, найдено {len(plays)}"

    def play(block: str, mode: str, **kw: float) -> Play:
        return Play(block, mode, kw.get("speed_mult", 1.0), kw.get("extra_pause", 2.0), int(kw.get("repeat", 1)))

    full = plan_for_play(blocks, play("ACT_1_FOREST", "full"))
    roles = {s.line.role for s in full if s.line}
    assert "MENTOR_TARGET_es" in roles, roles
    assert any(role.startswith("transitions_bank_") for role in roles), roles
    assert any(s.line is None for s in full), "в full нет пауз"

    target = plan_for_play(blocks, play("ACT_1_FOREST", "target_only", speed_mult=0.92, repeat=2))
    assert not any(s.line and not is_target_role(s.line.role) for s in target)
    assert all(s.line is None or "_slow" not in s.line.role.lower() for s in target)
    assert len([s for s in target if s.line]) == 8  # 4 целевые фразы x 2 повтора

    shuf1 = plan_for_play(blocks, play("ACT_1_FOREST", "shuffle_target", speed_mult=0.85, extra_pause=7, repeat=4))
    shuf2 = plan_for_play(blocks, play("ACT_1_FOREST", "shuffle_target", speed_mult=0.85, extra_pause=7, repeat=4))
    assert all(s.line is None or is_target_role(s.line.role) for s in shuf1)
    assert shuf1 == shuf2, "shuffle нестабилен"
    assert len(build_plan(text)) > len(full)

    assert rate_for_speed(1.0) == "+0%" and rate_for_speed(0.9) == "-10%" and rate_for_speed(0.82) == "-18%"
    plain = cache_name("001", "MENTOR_TARGET_es", "v", 0.82, "Hola.")
    slow = cache_name("001", "MENTOR_TARGET_es_slow", "v", 0.80, "Hola.")
    bank = cache_name("001", "transitions_bank_describe_in_native", "v", 0.90, "Look.")
    assert plain.startswith("001_MENTOR_TARGET_es_")
    assert len({plain, slow, bank}) == 3
    assert plain == cache_name("001", "MENTOR_TARGET_es", "v", 0.82, "Hola.")
    assert plain != cache_name("001", "MENTOR_TARGET_es", "v", 0.82, "Hola!")
    assert voice_setting_key("transitions_bank_apology_mentor_spoke") == "MENTOR_NATIVE"
    assert voice_setting_key("MENTOR_TARGET_es_slow") == "MENTOR_TARGET"
    assert voice_setting_key("NPC_TARGET_es") == "NPC_TARGET"
    assert voice_setting_key("NARRATOR_NATIVE_en") == "MENTOR_NATIVE"
    assert voice_setting_key("NARRATOR_TARGET_es") == "MENTOR_TARGET"
    assert voice_setting_key("NARRATOR_NATIVE_en_translate") == "MENTOR_NATIVE"
    assert not is_target_role("NARRATOR_NATIVE_en_translate")
    assert is_target_role("NARRATOR_TARGET_es") and not is_target_role("NARRATOR_NATIVE_en")
    assert is_target_role("MENTOR_TARGET_es") and is_target_role("NPC_TARGET_es")
    assert is_target_role("MENTOR_TARGET") and not is_target_role("MENTOR_TARGET_es_slow")
    assert not is_target_role("NARRATOR_TARGET_es_repeat")
    assert voice_setting_key("NARRATOR_TARGET_es_repeat") == "MENTOR_TARGET"
    mini = {
        "R": [
            Line("1", "NARRATOR_NATIVE_en", 0.88, "Rain."),
            Line("1", "NARRATOR_TARGET_es", 0.82, "Llueve."),
            Line("1", "NARRATOR_TARGET_es_repeat", 0.78, "Llueve."),
        ]
    }
    full_r = plan_for_play(mini, play("R", "full"))
    only_r = plan_for_play(mini, play("R", "target_only"))
    assert [s.line.role for s in full_r if s.line] == [
        "NARRATOR_NATIVE_en",
        "NARRATOR_TARGET_es",
        "NARRATOR_TARGET_es_repeat",
    ]
    assert [s.line.role for s in only_r if s.line] == ["NARRATOR_TARGET_es"]
    assert not is_target_role("transitions_bank_npc_question")
    assert not is_target_role("transitions_bank_how_it_sounds_in_target")

    for bad_block, bad_mode in (("NOPE", "full"), ("ACT_1_FOREST", "nope")):
        try:
            plan_for_play(blocks, _parse_play(bad_block, f"mode={bad_mode}"))
        except SystemExit:
            pass
        else:
            raise AssertionError("ожидался SystemExit")

    prose = "The night was quiet.\n\n[Pause] A soft wind moved through the room.\n\n## Chapter 1\nHe slept."
    assert not is_isekai_script(prose)
    assert not is_isekai_script("[001|MENTOR_NATIVE|0.90] Hello.\n")  # нет PLAYLIST_SEQUENCE
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        script = folder / "script.md"
        script.write_text(prose, encoding="utf-8")
        assert asyncio.run(maybe_render(folder, script)) is False
        assert not (folder / "audio_cache").exists() and not (folder / "voiceover.mp3").exists()
    inv = phrase_inventory(sample.parent)
    assert inv["ok"] and inv["unique"] == 18 and inv["missing"] == 18, inv
    assert inv["timeline_lines"] > inv["unique"]
    print("ISEKAI_SELF_TEST_OK")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args == ["--self-test"]:
        self_test()
        return 0
    print("Использование: python scripts/isekai_voice.py [--self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
