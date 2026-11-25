"""Уникальный эмбиент-хвост после озвучки.

Берёт файлы из assets/ambient/<категория>, режет на куски 10–25 мин,
перемешивает по seed имени папки эпизода и склеивает через acrossfade.
Под речь кладётся отдельная дорожка той же библиотеки, в конце ролика — fade out.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AMBIENT_ROOT = ROOT / "assets" / "ambient"
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

MIN_SEGMENT_SEC = 10 * 60
MAX_SEGMENT_SEC = 25 * 60
SEGMENT_XFADE_MIN = 4.0
SEGMENT_XFADE_MAX = 6.0
JOIN_XFADE_MIN = 5.0
JOIN_XFADE_MAX = 8.0
END_FADE_SEC = 15.0
VOLUME_JITTER_DB = 1.5
TONE_JITTER_DB = 1.2
MAX_SEGMENTS = 2400
CHUNK_SIZE = 6

DIM_START_SEC = 37.5 * 60
DIM_FADE_SEC = 90.0
DIM_LEVEL = 0.12

CATEGORY_LABELS = {
    "mix": "Случайный микс",
    "rain": "Дождь",
    "fireplace": "Очаг",
    "night_wind": "Ночной ветер",
    "water": "Вода",
}


@dataclass(frozen=True)
class AmbientTailSettings:
    enabled: bool
    target_total_sec: float
    category: str
    volume: float
    voice_fade_out_sec: float


@dataclass(frozen=True)
class AmbientSegment:
    path: Path
    start_sec: float
    duration_sec: float
    gain_db: float
    tone_db: float
    xfade_sec: float


def category_label(slug: str) -> str:
    return CATEGORY_LABELS.get(slug, slug.replace("_", " "))


def list_ambient_categories(root: Path | None = None) -> list[str]:
    base = root or AMBIENT_ROOT
    if not base.is_dir():
        return []
    names = [
        path.name
        for path in base.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    return sorted(names, key=str.lower)


def safe_category(category: str) -> str:
    text = (category or "mix").strip()
    if text.lower() in {"", "mix", "random"}:
        return "mix"
    if any(token in text for token in ("/", "\\", "..")):
        return "mix"
    return text


def _audio_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES
        ),
        key=lambda path: path.name.lower(),
    )


def collect_tracks(category: str, root: Path | None = None) -> list[Path]:
    base = root or AMBIENT_ROOT
    cat = safe_category(category)
    files: list[Path] = []
    if cat == "mix":
        files.extend(_audio_files(base))
        for name in list_ambient_categories(base):
            files.extend(_audio_files(base / name))
    else:
        files.extend(_audio_files(base / cat))
    unique = {path.resolve(): path for path in files}
    return [unique[key] for key in sorted(unique, key=lambda item: str(item).lower())]


def episode_seed(episode_dir: Path, salt: str) -> int:
    digest = hashlib.sha256(f"{episode_dir.name}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def tail_seconds(voice_sec: float, settings: AmbientTailSettings) -> float:
    if not settings.enabled or settings.target_total_sec <= 0 or voice_sec <= 0:
        return 0.0
    return max(0.0, settings.target_total_sec - voice_sec)


def dim_start_for(voice_sec: float) -> float:
    """Затемнение с ~37.5 мин или с начала хвоста — что наступит раньше."""
    return min(DIM_START_SEC, max(0.0, voice_sec))


def brightness_at(t: float, dim_start: float) -> float:
    if t <= dim_start:
        return 1.0
    if t >= dim_start + DIM_FADE_SEC:
        return DIM_LEVEL
    progress = (t - dim_start) / DIM_FADE_SEC
    smooth = progress * progress * (3.0 - 2.0 * progress)
    return 1.0 + (DIM_LEVEL - 1.0) * smooth


def describe_tail(
    voice_sec: float | None,
    *,
    enabled: bool,
    target_minutes: float,
    estimated_voice_sec: float | None = None,
) -> str:
    if voice_sec is None:
        base = float(estimated_voice_sec or 0)
        speech = (
            f"речи ещё нет, оценка {base / 60:.0f} мин"
            if estimated_voice_sec
            else "речи ещё нет"
        )
        source = "оценка сценария"
    else:
        base = voice_sec
        speech = f"речь {voice_sec / 60:.1f} мин"
        source = "voiceover.mp3"
    if not enabled or target_minutes <= 0:
        return f"{speech}. Хвост не добавляется."
    tail = max(0.0, target_minutes * 60.0 - base)
    if tail <= 0:
        return (
            f"{speech} ({source}). Цель {target_minutes:.0f} мин не длиннее речи — хвост 0 мин."
        )
    total = base + tail
    return (
        f"{speech} ({source}). Хвост {tail / 60:.1f} мин. Итог {total / 60:.1f} мин."
    )


def settings_from_episode(episode_dir: Path) -> AmbientTailSettings:
    from episode_timing import episode_timing_from_config

    timing = episode_timing_from_config(episode_dir)
    return AmbientTailSettings(
        enabled=bool(timing["ambient_tail_enabled"]),
        target_total_sec=float(timing["target_total_minutes"]) * 60.0,
        category=safe_category(str(timing["ambient_category"])),
        volume=float(timing["ambient_volume"]),
        voice_fade_out_sec=float(timing["voice_fade_out_sec"]),
    )


def timeline_length(segments: list[AmbientSegment]) -> float:
    if not segments:
        return 0.0
    total = segments[0].duration_sec
    for segment in segments[1:]:
        total += segment.duration_sec - segment.xfade_sec
    return total


def plan_segments(
    tracks: list[tuple[Path, float]],
    target_sec: float,
    rng: random.Random,
) -> list[AmbientSegment]:
    usable = [(path, dur) for path, dur in tracks if dur >= 1.0]
    if target_sec <= 0:
        return []
    if not usable:
        raise RuntimeError("В библиотеке эмбиента нет файлов длиннее 1 секунды.")

    deck = list(usable)
    rng.shuffle(deck)
    index = 0
    planned: list[AmbientSegment] = []
    stalls = 0
    while timeline_length(planned) < target_sec - 0.2:
        if len(planned) >= MAX_SEGMENTS:
            raise RuntimeError(
                "Эмбиент слишком короткий: чтобы набрать хвост, нужно больше длинных записей "
                f"(сейчас уже {len(planned)} сегментов)."
            )
        if index >= len(deck):
            rng.shuffle(deck)
            index = 0
        path, dur = deck[index]
        index += 1
        hi = min(MAX_SEGMENT_SEC, dur)
        lo = min(MIN_SEGMENT_SEC, hi)
        seg_len = hi if hi - lo < 0.05 else rng.uniform(lo, hi)
        start_max = max(0.0, dur - seg_len)
        start = rng.uniform(0.0, start_max) if start_max > 0.05 else 0.0
        seg_len = min(seg_len, max(0.0, dur - start))
        xfade = 0.0
        if planned:
            xfade = rng.uniform(SEGMENT_XFADE_MIN, SEGMENT_XFADE_MAX)
            xfade = min(xfade, planned[-1].duration_sec * 0.45, seg_len * 0.45)
        remaining = target_sec - timeline_length(planned)
        add = seg_len - xfade
        if add > remaining:
            seg_len = remaining + xfade
            if start + seg_len > dur:
                seg_len = min(dur, remaining + xfade)
                start = max(0.0, dur - seg_len)
            if planned:
                xfade = min(xfade, planned[-1].duration_sec * 0.45, seg_len * 0.45)
            add = seg_len - xfade
        if add < 0.25 or seg_len < 0.5:
            stalls += 1
            if stalls > len(usable) * 3:
                raise RuntimeError("Не удалось набрать длительность эмбиента из текущих файлов.")
            continue
        stalls = 0
        planned.append(
            AmbientSegment(
                path=path,
                start_sec=start,
                duration_sec=seg_len,
                gain_db=rng.uniform(-VOLUME_JITTER_DB, VOLUME_JITTER_DB),
                tone_db=rng.uniform(-TONE_JITTER_DB, TONE_JITTER_DB),
                xfade_sec=xfade,
            )
        )
    return planned


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _require(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ({what}): {(proc.stderr or '')[-800:]}")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    import re

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _segment_filter(index: int, segment: AmbientSegment) -> str:
    return (
        f"[{index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        f"volume={segment.gain_db:.2f}dB,"
        f"equalizer=f=1400:t=q:w=1:g={segment.tone_db:.2f}[s{index}]"
    )


def _render_chunk(segments: list[AmbientSegment], dest: Path) -> None:
    args: list[str] = []
    filters: list[str] = []
    for index, segment in enumerate(segments):
        args += [
            "-ss",
            f"{segment.start_sec:.3f}",
            "-t",
            f"{segment.duration_sec:.3f}",
            "-i",
            str(segment.path),
        ]
        filters.append(_segment_filter(index, segment))
    last = "s0"
    for index in range(1, len(segments)):
        out = f"a{index}"
        xfade = max(0.05, segments[index].xfade_sec)
        filters.append(
            f"[{last}][s{index}]acrossfade=d={xfade:.3f}:c1=tri:c2=tri[{out}]"
        )
        last = out
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        [
            *args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{last}]",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(dest),
        ]
    )
    _require(proc, dest.name)


def _acrossfade_files(left: Path, right: Path, dest: Path, xfade: float) -> None:
    proc = _run(
        [
            "-i",
            str(left),
            "-i",
            str(right),
            "-filter_complex",
            f"[0:a][1:a]acrossfade=d={max(0.05, xfade):.3f}:c1=tri:c2=tri[a]",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(dest),
        ]
    )
    _require(proc, "acrossfade chunks")


def _trim_exact(source: Path, dest: Path, duration_sec: float) -> None:
    proc = _run(
        [
            "-i",
            str(source),
            "-af",
            f"atrim=0:{duration_sec:.3f},apad=whole_dur={duration_sec:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(dest),
        ]
    )
    _require(proc, "trim ambient")


def render_segments(
    segments: list[AmbientSegment],
    output: Path,
    duration_sec: float,
) -> Path:
    if not segments:
        raise RuntimeError("Нет сегментов эмбиента для рендера.")
    output.parent.mkdir(parents=True, exist_ok=True)
    groups = [segments[index : index + CHUNK_SIZE] for index in range(0, len(segments), CHUNK_SIZE)]
    temps: list[Path] = []
    try:
        chunk_files: list[Path] = []
        for number, group in enumerate(groups):
            chunk = output.with_name(f"{output.stem}_chunk{number:03d}.m4a")
            _render_chunk(group, chunk)
            temps.append(chunk)
            chunk_files.append(chunk)
        acc = chunk_files[0]
        for number, (chunk, group) in enumerate(zip(chunk_files[1:], groups[1:]), start=1):
            joined = output.with_name(f"{output.stem}_join{number:03d}.m4a")
            _acrossfade_files(acc, chunk, joined, group[0].xfade_sec or 5.0)
            temps.append(joined)
            acc = joined
        if acc != output:
            _trim_exact(acc, output, duration_sec)
        else:
            trimmed = output.with_name(f"{output.stem}_exact.m4a")
            _trim_exact(acc, trimmed, duration_sec)
            temps.append(trimmed)
            trimmed.replace(output)
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)
    return output


def load_track_durations(paths: list[Path]) -> list[tuple[Path, float]]:
    loaded: list[tuple[Path, float]] = []
    for path in paths:
        dur = probe_duration(path)
        if dur >= 1.0:
            loaded.append((path, dur))
    return loaded


def render_unique_ambient(
    episode_dir: Path,
    category: str,
    duration_sec: float,
    output: Path,
    salt: str,
) -> Path:
    tracks = collect_tracks(category)
    if not tracks:
        folder = "assets/ambient/" if safe_category(category) == "mix" else f"assets/ambient/{safe_category(category)}/"
        raise RuntimeError(
            f"Нет аудио в {folder}. Положите mp3/wav/m4a без речи или выберите другую категорию."
        )
    loaded = load_track_durations(tracks)
    rng = random.Random(episode_seed(episode_dir, salt))
    segments = plan_segments(loaded, duration_sec, rng)
    print(
        f"Эмбиент ({salt}): {len(segments)} сегментов, цель {duration_sec / 60:.1f} мин, "
        f"категория {safe_category(category)}."
    )
    return render_segments(segments, output, duration_sec)


def mix_voice_with_tail(
    *,
    episode_dir: Path,
    video: Path,
    voice: Path,
    bed: Path,
    tail: Path,
    voice_dur: float,
    total_dur: float,
    volume: float,
    voice_fade_sec: float,
    output: Path,
    lead_sec: float = 0.0,
) -> None:
    """Тихий фон под речь, crossfade 5–8 с в хвост, fade out последние 15 с.

    lead_sec сдвигает речь: эмбиент при этом уже звучит с 00:00.
    total_dur — длина готового видео, уже вместе с интро.
    """
    rng = random.Random(episode_seed(episode_dir, "join"))
    tail_dur = probe_duration(tail)
    xfade = rng.uniform(JOIN_XFADE_MIN, JOIN_XFADE_MAX)
    xfade = min(xfade, max(0.4, voice_dur * 0.45), max(0.4, tail_dur * 0.45))
    fade = min(max(0.0, voice_fade_sec), max(0.0, voice_dur * 0.9))
    lead = max(0.0, float(lead_sec))
    speech_end = voice_dur + lead
    end_fade = END_FADE_SEC if total_dur >= 60 else min(END_FADE_SEC, max(0.4, total_dur * 0.2))
    end_st = max(0.0, total_dur - end_fade)
    vol = max(0.05, min(0.30, volume))
    fade_at = max(0.0, speech_end - fade)
    if lead > 0.02:
        lead_ms = int(round(lead * 1000))
        voice_head = f"[1:a]aformat=channel_layouts=stereo,adelay={lead_ms}|{lead_ms}"
    else:
        voice_head = "[1:a]anull"
    if fade > 0.05:
        voice_chain = f"{voice_head},afade=t=out:st={fade_at:.3f}:d={fade:.3f}[voice]"
    else:
        voice_chain = f"{voice_head}[voice]"
    bed_chain = f"[2:a]volume={vol:.4f}"
    if lead > 0.02:
        bed_chain += ",afade=t=in:st=0:d=1.5"
    bed_chain += f",atrim=0:{speech_end:.3f},asetpts=PTS-STARTPTS[bed]"
    graph = ";".join(
        [
            voice_chain,
            bed_chain,
            f"[3:a]volume={vol:.4f},atrim=0:{max(tail_dur, 0.1):.3f},asetpts=PTS-STARTPTS[tail]",
            "[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]",
            f"[mix][tail]acrossfade=d={xfade:.3f}:c1=tri:c2=tri[joined]",
            f"[joined]afade=t=out:st={end_st:.3f}:d={end_fade:.3f}[a]",
        ]
    )
    print(
        f"Сведение: фон {vol:.0%} под речь, переход в хвост {xfade:.1f} с, "
        f"затухание в конце {end_fade:.1f} с."
    )
    proc = _run(
        [
            "-i",
            str(video),
            "-i",
            str(voice),
            "-stream_loop",
            "-1",
            "-i",
            str(bed),
            "-i",
            str(tail),
            "-filter_complex",
            graph,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{total_dur:.3f}",
            "-threads",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    _require(proc, "mix tail")
