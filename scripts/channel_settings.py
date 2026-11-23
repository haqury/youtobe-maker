"""Настройки канала: channel_settings.json и brand_assets/."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_NAME = "channel_settings.json"
BRAND_DIR_NAME = "brand_assets"

DEFAULT_CHANNEL_SETTINGS: dict[str, Any] = {
    "branding": {
        "use_intro": False,
        "use_outro": False,
        "crossfade_sec": 1.0,
        "use_watermark": False,
        "watermark_opacity": 0.25,
    },
    "audio": {
        "voice_en": "en-US-AndrewMultilingualNeural",
        "voice_ru": "ru-RU-DmitryNeural",
        "speech_rate": "-12%",
        "ambient_volume": 0.08,
    },
    "visual": {
        "style_suffix": "",
        "default_animation_method": "ffmpeg_25d",
    },
    "seo": {
        "description_footer_en": "",
        "description_footer_ru": "",
        "default_tags": [],
        "default_publish_time_est": "18:00",
    },
}

BRAND_FILES = {
    "intro": "intro.mp4",
    "outro": "outro.mp4",
    "watermark": "watermark.png",
    "ambient": "default_ambient.mp3",
}


def brand_assets_dir(channel_dir: Path) -> Path:
    return channel_dir / BRAND_DIR_NAME


def settings_path(channel_dir: Path) -> Path:
    return channel_dir / SETTINGS_NAME


def ensure_brand_assets_dir(channel_dir: Path) -> Path:
    path = brand_assets_dir(channel_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def normalize_channel_settings(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    merged = _deep_merge(DEFAULT_CHANNEL_SETTINGS, data)
    branding = merged["branding"]
    branding["crossfade_sec"] = float(branding.get("crossfade_sec", 1.0))
    branding["watermark_opacity"] = min(
        0.5, max(0.1, float(branding.get("watermark_opacity", 0.25)))
    )
    for flag in ("use_intro", "use_outro", "use_watermark"):
        branding[flag] = bool(branding.get(flag))
    audio = merged["audio"]
    audio["ambient_volume"] = min(1.0, max(0.0, float(audio.get("ambient_volume", 0.08))))
    audio["speech_rate"] = str(audio.get("speech_rate", "-12%")).strip() or "-12%"
    method = str(merged["visual"].get("default_animation_method", "ffmpeg_25d"))
    if method not in {"ffmpeg_25d", "remotion", "comfyui"}:
        method = "ffmpeg_25d"
    merged["visual"]["default_animation_method"] = method
    tags = merged["seo"].get("default_tags")
    if isinstance(tags, str):
        merged["seo"]["default_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    elif not isinstance(tags, list):
        merged["seo"]["default_tags"] = []
    return merged


def load_channel_settings(channel_dir: Path) -> dict[str, Any]:
    channel_dir = channel_dir.resolve()
    path = settings_path(channel_dir)
    if not path.is_file():
        save_channel_settings(channel_dir, DEFAULT_CHANNEL_SETTINGS.copy())
        return normalize_channel_settings(DEFAULT_CHANNEL_SETTINGS.copy())
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        raw = {}
    return normalize_channel_settings(raw)


def save_channel_settings(channel_dir: Path, data: dict[str, Any]) -> None:
    channel_dir = channel_dir.resolve()
    ensure_brand_assets_dir(channel_dir)
    normalized = normalize_channel_settings(data)
    settings_path(channel_dir).write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def brand_asset_path(channel_dir: Path, kind: str) -> Path:
    name = BRAND_FILES.get(kind, kind)
    return brand_assets_dir(channel_dir) / name


def channel_dir_from_episode(episode_dir: Path) -> Path:
    return episode_dir.resolve().parents[1]


def apply_style_suffix(prompt: str, channel_dir: Path) -> str:
    text = (prompt or "").strip()
    if not text:
        return text
    settings = load_channel_settings(channel_dir)
    suffix = str(settings.get("visual", {}).get("style_suffix", "")).strip()
    if not suffix:
        return text
    low = text.lower()
    if suffix.lower() in low:
        return text
    joined = f"{text.rstrip(', ')}"
    if not joined.endswith(","):
        joined += ","
    out = f"{joined} {suffix}"
    return out.strip()[:2048]


def resolve_ambient_track(channel_dir: Path, fallback: Path | None) -> tuple[Path | None, float]:
    settings = load_channel_settings(channel_dir)
    volume = float(settings.get("audio", {}).get("ambient_volume", 0.08))
    custom = brand_asset_path(channel_dir, "ambient")
    if custom.is_file():
        return custom, volume
    return fallback, volume