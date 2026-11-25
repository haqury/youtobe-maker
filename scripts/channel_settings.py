"""Настройки канала: channel_settings.json и brand_assets/."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_NAME = "channel_settings.json"
BRAND_DIR_NAME = "brand_assets"

INTRO_BRAND_KEYS = (
    "use_intro",
    "crossfade_sec",
    "intro_text",
    "intro_text_start",
    "intro_text_duration",
    "intro_fade_sec",
    "intro_text_y",
)

DEFAULT_CHANNEL_SETTINGS: dict[str, Any] = {
    "branding": {
        "use_intro": False,
        "use_outro": False,
        "crossfade_sec": 1.0,
        "use_watermark": False,
        "watermark_opacity": 0.25,
        "intro_text": "",
        "intro_text_start": 1.2,
        "intro_text_duration": 2.6,
        "intro_fade_sec": 1.0,
        "intro_text_y": 0.0,
        "intro_text_style": "gold_serif",
        "intro_override": False,
        "apply_intro_to_series": False,
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

INTRO_TEXT_STYLES = ("gold_serif", "light_sans", "small_italic")

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
    for flag in ("use_intro", "use_outro", "use_watermark", "intro_override", "apply_intro_to_series"):
        branding[flag] = bool(branding.get(flag))
    branding["intro_text"] = str(branding.get("intro_text") or "").strip()
    branding["intro_text_start"] = _clamp_unit(branding.get("intro_text_start"), 0.0, 30.0, 1.2)
    branding["intro_text_duration"] = _clamp_unit(branding.get("intro_text_duration"), 0.2, 20.0, 2.6)
    branding["intro_fade_sec"] = _clamp_unit(branding.get("intro_fade_sec"), 0.1, 5.0, 1.0)
    branding["intro_text_y"] = _clamp_unit(branding.get("intro_text_y"), -400.0, 400.0, 0.0)
    style = str(branding.get("intro_text_style") or "gold_serif").strip()
    branding["intro_text_style"] = style if style in INTRO_TEXT_STYLES else "gold_serif"
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


def _clamp_unit(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, number))


def channel_root_from_episode(episode_dir: Path) -> Path:
    """channels/<канал>/… — папка канала, а не playlists/<серия>."""
    resolved = episode_dir.resolve()
    for ancestor in resolved.parents:
        if ancestor.name == "channels":
            relative = resolved.relative_to(ancestor)
            return ancestor / relative.parts[0]
    return resolved.parents[1]


def playlist_dir_from_episode(episode_dir: Path) -> Path | None:
    resolved = episode_dir.resolve()
    parts = resolved.parts
    if "playlists" not in parts:
        return None
    index = parts.index("playlists")
    if index + 1 >= len(parts):
        return None
    return Path(*parts[: index + 2])


def channel_display_name(channel_dir: Path) -> str:
    config = channel_dir / "channel_config.md"
    if config.is_file():
        try:
            lines = config.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("channel name:"):
                name = stripped.split(":", 1)[1].strip()
                if name:
                    return name
            if stripped.startswith("Название канала:"):
                name = stripped.split(":", 1)[1].strip()
                if name:
                    return name
    return channel_dir.name


def resolve_episode_branding(episode_dir: Path) -> dict[str, Any]:
    """Интро серии берётся из канала, пока в json серии нет intro_override: true."""
    root = channel_root_from_episode(episode_dir)
    channel_branding = dict(load_channel_settings(root).get("branding", {}))
    playlist = playlist_dir_from_episode(episode_dir)
    if playlist is None:
        return channel_branding
    path = settings_path(playlist)
    if not path.is_file():
        return channel_branding
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raw = {}
    raw_branding = raw.get("branding") if isinstance(raw, dict) else None
    local = dict(load_channel_settings(playlist).get("branding", {}))
    if isinstance(raw_branding, dict) and raw_branding.get("intro_override") is True:
        return local
    for key in INTRO_BRAND_KEYS:
        if key in channel_branding:
            local[key] = channel_branding[key]
    return local


def resolve_intro_source(episode_dir: Path) -> Path | None:
    """intro.mp4 серии, если он есть, иначе файл канала."""
    playlist = playlist_dir_from_episode(episode_dir)
    if playlist is not None:
        local = brand_asset_path(playlist, "intro")
        if local.is_file():
            return local
    shared = brand_asset_path(channel_root_from_episode(episode_dir), "intro")
    if shared.is_file():
        return shared
    return None


def apply_intro_to_series(channel_dir: Path, branding: dict[str, Any]) -> int:
    """Записывает поля интро канала в каждую папку playlists/<серия>."""
    playlists = channel_dir / "playlists"
    if not playlists.is_dir():
        return 0
    count = 0
    for series in sorted(path for path in playlists.iterdir() if path.is_dir()):
        current = load_channel_settings(series)
        block = dict(current.get("branding") or {})
        for key in INTRO_BRAND_KEYS:
            if key in branding:
                block[key] = branding[key]
        block["intro_override"] = False
        current["branding"] = block
        save_channel_settings(series, current)
        count += 1
    return count


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