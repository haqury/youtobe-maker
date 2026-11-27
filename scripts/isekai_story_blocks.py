"""Блоки сюжета (ACT/RECALL) и оценка таймкодов по плейлисту."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ambient_tail import probe_duration
from isekai_voice import PLAY_RE, PLAYLIST_RE, parse_script, plan_for_play, cache_name, voice_setting_key


def list_story_blocks(episode_dir: Path) -> list[str]:
    script = episode_dir / "script.md"
    if not script.is_file():
        return []
    blocks, _plays = parse_script(script.read_text(encoding="utf-8-sig"))
    order: list[str] = []
    for line in script.read_text(encoding="utf-8-sig").splitlines():
        raw = line.strip()
        if raw.startswith("## BLOCK:"):
            name = raw.split(":", 1)[1].strip()
            if name in blocks and name not in order:
                order.append(name)
    return order


def _step_duration(step, cache: Path, settings, bias: float, pitch: str, voices: dict) -> float:
    from isekai_voice import Step, Line

    if step.line is None:
        return max(0.0, float(step.seconds))
    line = step.line
    voice = voices.get(line.role)
    if not voice:
        voice_key = voice_setting_key(line.role)
        voice = settings.get(voice_key, "")
    name = cache_name(line.chunk_id, line.role, voice, line.speed, line.text, bias=bias, pitch=pitch)
    path = cache / name
    if path.is_file():
        return probe_duration(path) * (1.0 / max(step.speed_mult, 0.01))
    return max(2.0, len(line.text) * 0.06)


def block_time_ranges(episode_dir: Path) -> list[dict]:
    """Порядок PLAYLIST_SEQUENCE → start/end в секундах (по кэшу audio_cache)."""
    script = episode_dir / "script.md"
    if not script.is_file():
        return []
    text = script.read_text(encoding="utf-8-sig")
    blocks, plays = parse_script(text)
    if not plays:
        return []
    from isekai_config import load_isekai_settings, speech_pitch, speech_speed_bias

    settings = load_isekai_settings(episode_dir)
    bias = speech_speed_bias(settings)
    pitch = speech_pitch(settings)
    cache = episode_dir / "audio_cache"
    voices: dict[str, str] = {}
    for block_items in blocks.values():
        for item in block_items:
            if hasattr(item, "role"):
                slot = item.role
                if slot not in voices:
                    vk = voice_setting_key(slot)
                    voices[slot] = settings.get(vk, "")
    cursor = 0.0
    rows: list[dict] = []
    for play in plays:
        start = cursor
        steps = plan_for_play(blocks, play)
        for step in steps:
            cursor += _step_duration(step, cache, settings, bias, pitch, voices)
        rows.append(
            {
                "block": play.block,
                "start_sec": round(start, 2),
                "end_sec": round(cursor, 2),
                "mode": play.mode,
                "repeat": play.repeat,
            }
        )
    return rows


@dataclass(frozen=True)
class PlaylistPart:
    """Одна строка PLAY: … в script.md — ровно как в PLAYLIST_SEQUENCE."""

    index: int
    blocks: tuple[str, ...]
    mode: str
    repeat: int
    speed_mult: float
    extra_pause: float
    start_sec: float | None
    end_sec: float | None
    duration_sec: float | None
    source_line: str


def _play_groups_from_script(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """(params, block names) на каждую строку PLAY: в файле."""
    groups: list[tuple[str, tuple[str, ...]]] = []
    in_playlist = False
    for raw in text.splitlines():
        line = raw.strip()
        if PLAYLIST_RE.match(line):
            in_playlist = True
            continue
        if not in_playlist:
            continue
        match = PLAY_RE.match(line)
        if not match:
            continue
        names = tuple(
            name.strip().rstrip(",")
            for name in match.group(1).split(",")
            if name.strip()
        )
        if names:
            groups.append((match.group(2).strip(), names))
    return groups


def playlist_build_parts(episode_dir: Path) -> list[PlaylistPart]:
    from isekai_voice import Play, _parse_play

    script = episode_dir / "script.md"
    if not script.is_file():
        return []
    text = script.read_text(encoding="utf-8-sig")
    _, plays = parse_script(text)
    if not plays:
        return []
    ranges = block_time_ranges(episode_dir)
    line_groups = _play_groups_from_script(text)
    range_i = 0
    parts: list[PlaylistPart] = []
    for part_index, (params, names) in enumerate(line_groups):
        parsed: Play = _parse_play(names[0], params)
        n = len(names)
        chunk = ranges[range_i : range_i + n]
        range_i += n
        start = float(chunk[0]["start_sec"]) if chunk else None
        end = float(chunk[-1]["end_sec"]) if chunk else None
        dur = max(0.05, end - start) if start is not None and end is not None else None
        preview = ", ".join(names)
        if len(names) > 3:
            preview = f"{names[0]}, {names[1]}, … ({len(names)} блоков)"
        parts.append(
            PlaylistPart(
                index=part_index,
                blocks=names,
                mode=parsed.mode,
                repeat=parsed.repeat,
                speed_mult=parsed.speed_mult,
                extra_pause=parsed.extra_pause,
                start_sec=start,
                end_sec=end,
                duration_sec=dur,
                source_line=f"PLAY: {preview} (mode={parsed.mode}, repeat={parsed.repeat})",
            )
        )
    return parts


def story_segment_for_part(episode_dir: Path, part_index: int) -> dict[str, float | int | tuple[str, ...] | str] | None:
    parts = playlist_build_parts(episode_dir)
    if part_index < 0 or part_index >= len(parts):
        return None
    part = parts[part_index]
    if part.start_sec is None or part.duration_sec is None:
        return None
    return {
        "play_part": part_index,
        "start_sec": part.start_sec,
        "end_sec": part.end_sec,
        "duration_sec": part.duration_sec,
        "blocks": part.blocks,
        "playlist_mode": part.mode,
    }


def story_segment_for_blocks(
    episode_dir: Path,
    blocks: list[str],
    *,
    playlist_mode: str = "full",
) -> dict[str, float | tuple[str, ...] | str] | None:
    """Старый API: ищет **первое** вхождение блоков в режиме (лучше --play-part)."""
    if not blocks:
        return None
    parts = playlist_build_parts(episode_dir)
    want = tuple(blocks)
    for part in parts:
        if part.mode == playlist_mode and part.blocks == want:
            return story_segment_for_part(episode_dir, part.index)
    ranges = block_time_ranges(episode_dir)
    if not ranges:
        return None
    name_set = {name.strip() for name in blocks if name.strip()}
    filtered = [row for row in ranges if row["block"] in name_set and row.get("mode") == playlist_mode]
    if not filtered:
        return None
    start = float(filtered[0]["start_sec"])
    end = float(filtered[-1]["end_sec"])
    return {
        "start_sec": start,
        "end_sec": end,
        "duration_sec": max(0.05, end - start),
        "blocks": tuple(blocks),
        "playlist_mode": playlist_mode,
    }


def list_act_blocks(episode_dir: Path) -> list[str]:
    return [name for name in list_story_blocks(episode_dir) if name.startswith("ACT_")]