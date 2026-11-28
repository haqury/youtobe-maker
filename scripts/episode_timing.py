"""Поля тайминга эпизода из episode_config.json (кадры, fade, ambient-хвост)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from channel_settings import channel_dir_from_episode

DEFAULT_SECONDS_PER_FRAME = 40.0
DEFAULT_VOICE_FADE_OUT_SEC = 12.0
DEFAULT_AMBIENT_TAIL_SEC = 0.0
DEFAULT_TARGET_TOTAL_MINUTES = 600.0
DEFAULT_AMBIENT_VOLUME = 0.12
DEFAULT_AMBIENT_CATEGORY = "mix"
DEFAULT_FPS_START = 24
DEFAULT_FPS_END = 2
DEFAULT_FPS_INTERVAL_MIN = 30.0
DEFAULT_DIM_MIN_BRIGHTNESS = 0.50
DEFAULT_DIM_START_MINUTES = 37.5
DEFAULT_DIM_FADE_SEC = 90.0
DIM_MIN_BRIGHTNESS_MIN = 0.05
DIM_MIN_BRIGHTNESS_MAX = 1.0
DIM_START_MINUTES_MIN = 0.0
DIM_START_MINUTES_MAX = 240.0
DIM_FADE_SEC_MIN = 15.0
DIM_FADE_SEC_MAX = 600.0

FRAME_COUNT_MIN = 8
FRAME_COUNT_MAX = 80
SECONDS_PER_FRAME_MIN = 25.0
SECONDS_PER_FRAME_MAX = 60.0
VOICE_FADE_MIN = 0.0
VOICE_FADE_MAX = 120.0
AMBIENT_TAIL_MIN = 0.0
AMBIENT_TAIL_MAX = 600.0
TARGET_TOTAL_MIN = 0.0
TARGET_TOTAL_MAX = 720.0
AMBIENT_VOLUME_MIN = 0.05
AMBIENT_VOLUME_MAX = 0.30
FPS_MIN = 1
FPS_MAX = 30
FPS_INTERVAL_MIN = 5.0
FPS_INTERVAL_MAX = 180.0


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
    cfg["ambient_tail_enabled"] = bool(cfg.get("ambient_tail_enabled", False))
    cfg["target_total_minutes"] = _clamp_float(
        cfg.get("target_total_minutes", DEFAULT_TARGET_TOTAL_MINUTES),
        TARGET_TOTAL_MIN,
        TARGET_TOTAL_MAX,
        DEFAULT_TARGET_TOTAL_MINUTES,
    )
    category = str(cfg.get("ambient_category") or DEFAULT_AMBIENT_CATEGORY).strip()
    if any(token in category for token in ("/", "\\", "..")) or not category:
        category = DEFAULT_AMBIENT_CATEGORY
    cfg["ambient_category"] = category
    cfg["ambient_volume"] = _clamp_float(
        cfg.get("ambient_volume", DEFAULT_AMBIENT_VOLUME),
        AMBIENT_VOLUME_MIN,
        AMBIENT_VOLUME_MAX,
        DEFAULT_AMBIENT_VOLUME,
    )
    cfg["fps_start"] = int(round(_clamp_float(
        cfg.get("fps_start", DEFAULT_FPS_START),
        FPS_MIN,
        FPS_MAX,
        DEFAULT_FPS_START,
    )))
    cfg["fps_end"] = int(round(_clamp_float(
        cfg.get("fps_end", DEFAULT_FPS_END),
        FPS_MIN,
        FPS_MAX,
        DEFAULT_FPS_END,
    )))
    cfg["fps_interval_min"] = _clamp_float(
        cfg.get("fps_interval_min", DEFAULT_FPS_INTERVAL_MIN),
        FPS_INTERVAL_MIN,
        FPS_INTERVAL_MAX,
        DEFAULT_FPS_INTERVAL_MIN,
    )
    cfg["dim_during_speech"] = bool(cfg.get("dim_during_speech", True))
    cfg["dim_min_brightness"] = _clamp_float(
        cfg.get("dim_min_brightness", DEFAULT_DIM_MIN_BRIGHTNESS),
        DIM_MIN_BRIGHTNESS_MIN,
        DIM_MIN_BRIGHTNESS_MAX,
        DEFAULT_DIM_MIN_BRIGHTNESS,
    )
    cfg["dim_start_minutes"] = _clamp_float(
        cfg.get("dim_start_minutes", DEFAULT_DIM_START_MINUTES),
        DIM_START_MINUTES_MIN,
        DIM_START_MINUTES_MAX,
        DEFAULT_DIM_START_MINUTES,
    )
    cfg["dim_start_sec"] = cfg["dim_start_minutes"] * 60.0
    cfg["dim_fade_sec"] = _clamp_float(
        cfg.get("dim_fade_sec", DEFAULT_DIM_FADE_SEC),
        DIM_FADE_SEC_MIN,
        DIM_FADE_SEC_MAX,
        DEFAULT_DIM_FADE_SEC,
    )
    return cfg


def fps_at(t_sec: float, total_sec: float, start: int, end: int, interval_min: float) -> int:
    """Ступенями от start к end: каждый интервал — одна ступень, последняя равна end."""
    start_i = int(max(FPS_MIN, min(FPS_MAX, round(start))))
    end_i = int(max(FPS_MIN, min(FPS_MAX, round(end))))
    if interval_min <= 0 or start_i == end_i or total_sec <= 0:
        return start_i
    interval = float(interval_min) * 60.0
    slots = max(1, math.ceil(total_sec / interval))
    index = min(slots - 1, int(max(0.0, t_sec) // interval))
    if slots == 1:
        return start_i
    progress = index / (slots - 1)
    value = start_i + (end_i - start_i) * progress
    return max(FPS_MIN, min(FPS_MAX, int(round(value))))


def describe_fps_schedule(total_sec: float, start: int, end: int, interval_min: float) -> str:
    if total_sec <= 0:
        return f"{int(start)} → {int(end)} fps, шаг {float(interval_min):.0f} мин"
    interval = max(1.0, float(interval_min) * 60.0)
    slots = max(1, math.ceil(total_sec / interval))
    labels: list[str] = []
    for index in range(slots):
        t0 = index * interval
        t1 = min(total_sec, (index + 1) * interval)
        fps = fps_at(t0, total_sec, start, end, interval_min)
        labels.append(f"{t0 / 60:.0f}–{t1 / 60:.0f} мин: {fps} fps")
    if len(labels) > 6:
        labels = labels[:3] + ["…"] + labels[-2:]
    return " · ".join(labels)


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


def episode_timing_from_config(episode_dir: Path) -> dict[str, Any]:
    from studio_ai import load_episode_config

    channel_dir = channel_dir_from_episode(episode_dir)
    channel_slug = channel_dir.name
    cfg = load_episode_config(episode_dir, channel_slug, channel_dir)
    return {
        "target_frame_count": cfg.get("target_frame_count"),
        "seconds_per_frame": float(cfg["seconds_per_frame"]),
        "voice_fade_out_sec": float(cfg["voice_fade_out_sec"]),
        "ambient_tail_sec": float(cfg["ambient_tail_sec"]),
        "ambient_tail_enabled": bool(cfg["ambient_tail_enabled"]),
        "target_total_minutes": float(cfg["target_total_minutes"]),
        "ambient_category": str(cfg["ambient_category"]),
        "ambient_volume": float(cfg["ambient_volume"]),
        "fps_start": int(cfg["fps_start"]),
        "fps_end": int(cfg["fps_end"]),
        "fps_interval_min": float(cfg["fps_interval_min"]),
        "dim_during_speech": bool(cfg["dim_during_speech"]),
        "dim_min_brightness": float(cfg["dim_min_brightness"]),
        "dim_start_minutes": float(cfg["dim_start_minutes"]),
        "dim_start_sec": float(cfg["dim_start_sec"]),
        "dim_fade_sec": float(cfg["dim_fade_sec"]),
    }