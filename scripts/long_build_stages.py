"""Артефакты и готовность этапов длинной сборки (isekai / 10h)."""

from __future__ import annotations

from pathlib import Path

CACHE_NAME = ".video_cache"
VIDEO_WITH_INTRO = "video_with_intro.mp4"
VIDEO_ALL = "video_all.mp4"
TAIL_VIDEO = "tail_video.mp4"
STORY_WITH_AUDIO = "story_with_audio.mp4"
AMBIENT_BED = "ambient_bed.m4a"
AMBIENT_TAIL = "ambient_tail.m4a"
OUTPUT_NAME = "final_video.mp4"
STAGE_VIDEO_OK = "long_stage_video.ok"
STAGE_STORY_MUX_OK = "long_stage_story_mux.ok"
STAGE_TAIL_VIDEO_OK = "long_stage_tail_video.ok"
STAGE_AMBIENT_OK = "long_stage_ambient.ok"


def cache_dir(episode_dir: Path) -> Path:
    return episode_dir / CACHE_NAME


def video_for_mux(episode_dir: Path) -> Path | None:
    cache = cache_dir(episode_dir)
    with_intro = cache / VIDEO_WITH_INTRO
    if with_intro.is_file() and with_intro.stat().st_size > 0:
        return with_intro
    plain = cache / VIDEO_ALL
    if plain.is_file() and plain.stat().st_size > 0:
        return plain
    return None


def video_stage_ready(episode_dir: Path) -> bool:
    cache = cache_dir(episode_dir)
    marker = cache / STAGE_VIDEO_OK
    if marker.is_file():
        return True
    target = video_for_mux(episode_dir)
    return target is not None and target.stat().st_size > 4096


def ambient_stage_ready(episode_dir: Path) -> bool:
    cache = cache_dir(episode_dir)
    bed = cache / AMBIENT_BED
    tail = cache / AMBIENT_TAIL
    marker = cache / STAGE_AMBIENT_OK
    if marker.is_file():
        return bed.is_file() and tail.is_file()
    return (
        bed.is_file()
        and tail.is_file()
        and bed.stat().st_size > 256
        and tail.stat().st_size > 256
    )


def mux_stage_ready(episode_dir: Path) -> bool:
    out = episode_dir / OUTPUT_NAME
    return out.is_file() and out.stat().st_size > 4096


def mark_video_stage(episode_dir: Path, video_path: Path) -> None:
    cache = cache_dir(episode_dir)
    cache.mkdir(exist_ok=True)
    (cache / STAGE_VIDEO_OK).write_text(
        f"{video_path.name}\n",
        encoding="utf-8",
    )


def mark_ambient_stage(episode_dir: Path) -> None:
    cache = cache_dir(episode_dir)
    cache.mkdir(exist_ok=True)
    (cache / STAGE_AMBIENT_OK).write_text("ok\n", encoding="utf-8")


def ambient_bed_path(episode_dir: Path) -> Path:
    return cache_dir(episode_dir) / AMBIENT_BED


def ambient_tail_path(episode_dir: Path) -> Path:
    return cache_dir(episode_dir) / AMBIENT_TAIL


def tail_video_path(episode_dir: Path) -> Path:
    return cache_dir(episode_dir) / TAIL_VIDEO


def story_with_audio_path(episode_dir: Path) -> Path:
    return cache_dir(episode_dir) / STORY_WITH_AUDIO


def story_mux_ready(episode_dir: Path) -> bool:
    cache = cache_dir(episode_dir)
    path = cache / STORY_WITH_AUDIO
    marker = cache / STAGE_STORY_MUX_OK
    return (marker.is_file() and path.is_file() and path.stat().st_size > 4096)


def tail_video_ready(episode_dir: Path) -> bool:
    cache = cache_dir(episode_dir)
    path = cache / TAIL_VIDEO
    marker = cache / STAGE_TAIL_VIDEO_OK
    return (marker.is_file() and path.is_file() and path.stat().st_size > 4096)


def mark_story_mux_stage(episode_dir: Path) -> None:
    cache = cache_dir(episode_dir)
    cache.mkdir(exist_ok=True)
    (cache / STAGE_STORY_MUX_OK).write_text("ok\n", encoding="utf-8")


def mark_tail_video_stage(episode_dir: Path) -> None:
    cache = cache_dir(episode_dir)
    cache.mkdir(exist_ok=True)
    (cache / STAGE_TAIL_VIDEO_OK).write_text("ok\n", encoding="utf-8")