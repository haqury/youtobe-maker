"""Поля тайминга эпизода из episode_config.json (кадры, fade, ambient-хвост)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from channel_settings import channel_dir_from_episode

DEFAULT_SECONDS_PER_FRAME = 40.0
DEFAULT_VOICE_FADE_OUT_SEC = 12.0
DEFAULT_AMBIENT_TAIL_SEC = 0.0

FRAME_COUNT_MIN = 8
FRAME_COUNT_MAX = 80
SECONDS_PER_FRAME_MIN = 25.0
SECONDS_PER_FRAME_MAX = 60.0
VOICE_FADE_MIN = 0.0
VOICE_FADE_MAX = 120.0
AMBIENT_TAIL_MIN = 0.0
AMBIENT_TAIL_MAX = 600.0


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _parse_target_frame_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return max(FRAME_COUNT_MIN, min(FRAME_COUNT_MAX, num))


def apply_timing_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать поля тайминга в config-словаре (на месте + возврат)."""
    cfg["target_frame_count"] = _parse_target_frame_count(cfg.get("target_frame_count"))
    cfg["seconds_per_frame"] = _clamp_float(
        cfg.get("seconds_per_frame", DEFAULT_SECONDS_PER_FRAME),
        SECONDS_PER_FRAME_MIN,
        SECONDS_PER_FRAME_MAX,
        DEFAULT_SECONDS_PER_FRAME,
    )
    cfg["voice_fade_out_sec"] = _clamp_float(
        cfg.get("voice_fade_out_sec", DEFAULT_VOICE_FADE_OUT_SEC),
        VOICE_FADE_MIN,
        VOICE_FADE_MAX,
        DEFAULT_VOICE_FADE_OUT_SEC,
    )
    cfg["ambient_tail_sec"] = _clamp_float(
        cfg.get("ambient_tail_sec", DEFAULT_AMBIENT_TAIL_SEC),
        AMBIENT_TAIL_MIN,
        AMBIENT_TAIL_MAX,
        DEFAULT_AMBIENT_TAIL_SEC,
    )
    return cfg


def auto_frame_count(avg_minutes: float, seconds_per_frame: float) -> int:
    return max(FRAME_COUNT_MIN, int(round(avg_minutes * 60.0 / seconds_per_frame)))


def resolve_target_frame_count(cfg: dict[str, Any]) -> tuple[int, bool]:
    """(count, manual)."""
    manual = cfg.get("target_frame_count")
    if manual is not None and int(manual) > 0:
        return int(manual), True
    avg_min = (float(cfg["target_min_minutes"]) + float(cfg["target_max_minutes"])) / 2.0
    spf = float(cfg.get("seconds_per_frame", DEFAULT_SECONDS_PER_FRAME))
    return auto_frame_count(avg_min, spf), False


def episode_timing_from_config(episode_dir: Path) -> dict[str, float | int | None]:
    from studio_ai import load_episode_config

    channel_dir = channel_dir_from_episode(episode_dir)
    channel_slug = channel_dir.name
    cfg = load_episode_config(episode_dir, channel_slug, channel_dir)
    return {
        "target_frame_count": cfg.get("target_frame_count"),
        "seconds_per_frame": float(cfg["seconds_per_frame"]),
        "voice_fade_out_sec": float(cfg["voice_fade_out_sec"]),
        "ambient_tail_sec": float(cfg["ambient_tail_sec"]),
    }