"""Настройки оживления кадров (2.5D FFmpeg / ComfyUI I2V) в review.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS_DIR = ROOT / "assets" / "overlays"

DEFAULT_ANIMATION: dict[str, Any] = {
    "animation_mode": "ffmpeg_25d",
    "enable_ken_burns": True,
    "enable_breathing_light": True,
    "overlay_file": "none",
    "overlay_files": [],
    "overlay_opacity": 0.22,
    "overlay_random_mix": True,
    "overlay_max_layers": 3,
    "overlay_variants": 12,
    "comfy_frames": ["01", "02", "03"],
    "remotion_chapter_titles": True,
    "remotion_particles": True,
    "remotion_vignette": True,
    "ken_burns_intensity": 0.85,
    "remotion_crossfade": 1.5,
}

VALID_MODES = ("ffmpeg_25d", "comfyui", "comfyui_i2v", "remotion")


def list_overlay_files() -> list[str]:
    if not OVERLAYS_DIR.is_dir():
        return []
    return sorted(path.name for path in OVERLAYS_DIR.glob("*.mp4") if path.is_file())


def _normalize_overlay_list(raw_files: object, legacy_file: str) -> list[str]:
    items: list[str] = []
    if raw_files is not None:
        if isinstance(raw_files, (list, tuple, set)):
            candidates = [str(item).strip() for item in raw_files]
        elif isinstance(raw_files, str):
            candidates = [part.strip() for part in raw_files.split(",")]
        else:
            candidates = [str(raw_files).strip()]
        seen: set[str] = set()
        for token in candidates:
            low = token.lower()
            if low in {"", "none", "off"}:
                continue
            normalized = "auto" if low == "auto" else token
            if normalized not in seen:
                seen.add(normalized)
                items.append(normalized)
        return items
    low_legacy = legacy_file.strip().lower()
    if low_legacy in {"", "none", "off"}:
        return []
    if low_legacy == "auto":
        return ["auto"]
    return [legacy_file.strip()]


def normalize_animation_settings(raw: object | None) -> dict[str, Any]:
    out = dict(DEFAULT_ANIMATION)
    has_raw_files = isinstance(raw, dict) and "overlay_files" in raw and raw["overlay_files"] is not None
    if isinstance(raw, dict):
        for key in DEFAULT_ANIMATION:
            if key in raw and raw[key] is not None:
                out[key] = raw[key]
    mode = str(out.get("animation_mode", "ffmpeg_25d")).strip()
    if mode == "comfyui_i2v":
        mode = "comfyui"
    out["animation_mode"] = mode if mode in VALID_MODES else "ffmpeg_25d"
    out["enable_ken_burns"] = bool(out.get("enable_ken_burns", True))
    out["enable_breathing_light"] = bool(out.get("enable_breathing_light", True))
    overlay = str(out.get("overlay_file", "none")).strip() or "none"
    overlay_files = _normalize_overlay_list(
        out.get("overlay_files") if has_raw_files else None,
        overlay,
    )
    out["overlay_files"] = overlay_files
    out["overlay_file"] = overlay_files[0] if overlay_files else "none"
    try:
        out["overlay_opacity"] = min(0.4, max(0.1, float(out["overlay_opacity"])))
    except (TypeError, ValueError):
        out["overlay_opacity"] = 0.22
    out["overlay_random_mix"] = bool(out.get("overlay_random_mix", True))
    try:
        out["overlay_max_layers"] = max(1, min(5, int(out.get("overlay_max_layers", 3))))
    except (TypeError, ValueError):
        out["overlay_max_layers"] = 3
    try:
        out["overlay_variants"] = max(1, min(48, int(out.get("overlay_variants", 12))))
    except (TypeError, ValueError):
        out["overlay_variants"] = 12
    frames = out.get("comfy_frames")
    if isinstance(frames, list):
        stems: list[str] = []
        seen: set[str] = set()
        for item in frames:
            stem = str(item).strip().zfill(2)[:2]
            if stem.isdigit() and stem not in seen:
                seen.add(stem)
                stems.append(stem)
        out["comfy_frames"] = sorted(stems) if stems else list(DEFAULT_ANIMATION["comfy_frames"])
    else:
        out["comfy_frames"] = list(DEFAULT_ANIMATION["comfy_frames"])
    out["remotion_chapter_titles"] = bool(out.get("remotion_chapter_titles", True))
    out["remotion_particles"] = bool(out.get("remotion_particles", True))
    out["remotion_vignette"] = bool(out.get("remotion_vignette", True))
    try:
        out["ken_burns_intensity"] = max(0.2, min(1.2, float(out.get("ken_burns_intensity", 0.85))))
    except (TypeError, ValueError):
        out["ken_burns_intensity"] = 0.85
    try:
        out["remotion_crossfade"] = max(0.5, min(4.0, float(out.get("remotion_crossfade", 1.5))))
    except (TypeError, ValueError):
        out["remotion_crossfade"] = 1.5
    return out


def _resolve_single_overlay(token: str, episode_dir: Path | None) -> Path | None:
    candidate = OVERLAYS_DIR / token
    if candidate.is_file():
        return candidate
    if episode_dir:
        local = episode_dir / "images" / token
        if local.is_file() and local.suffix.lower() == ".mp4":
            return local
    return None


def resolve_overlay_paths(settings: dict[str, Any], episode_dir: Path | None = None) -> list[Path]:
    if "overlay_files" in settings and settings["overlay_files"] is not None:
        tokens = _normalize_overlay_list(settings["overlay_files"], str(settings.get("overlay_file", "none")))
    else:
        tokens = _normalize_overlay_list(None, str(settings.get("overlay_file", "none")))
    resolved: list[Path] = []
    seen: set[Path] = set()
    for token in tokens:
        if token.lower() == "auto":
            for name in list_overlay_files():
                path = OVERLAYS_DIR / name
                key = path.resolve()
                if path.is_file() and key not in seen:
                    seen.add(key)
                    resolved.append(path)
            continue
        path = _resolve_single_overlay(token, episode_dir)
        if path is not None:
            key = path.resolve()
            if key not in seen:
                seen.add(key)
                resolved.append(path)
    return resolved


def resolve_overlay_path(settings: dict[str, Any], episode_dir: Path | None = None) -> Path | None:
    paths = resolve_overlay_paths(settings, episode_dir)
    return paths[0] if paths else None


def infer_animation_method_from_review(review: dict) -> str | None:
    """Угадываем метод для UI, если эпизод создавался до блока animation."""
    explicit = review.get("animation_method") or review.get("animation_mode")
    if explicit:
        mode = str(explicit).strip()
        if mode == "comfyui_i2v":
            return "comfyui"
        if mode in VALID_MODES:
            if mode in ("comfyui", "comfyui_i2v"):
                return "comfyui"
            if mode == "remotion":
                return "remotion"
            return "ffmpeg_25d"
    if isinstance(review.get("animation"), dict):
        return str(review["animation"].get("animation_mode", "ffmpeg_25d"))
    if isinstance(review.get("animate_i2v"), dict):
        return "comfyui"
    return None


def load_episode_animation(episode_dir: Path) -> dict[str, Any]:
    from episode_review import load_review

    review = load_review(episode_dir)
    raw = review.get("animation")
    if not isinstance(raw, dict) and review.get("animation_mode"):
        raw = {
            "animation_mode": review.get("animation_mode"),
            "enable_breathing_light": review.get("enable_breathing_light"),
            "overlay_file": review.get("overlay_file"),
            "overlay_files": review.get("overlay_files"),
            "comfy_frames": review.get("animate_frames"),
        }
    settings = normalize_animation_settings(raw)
    if review.get("animate_frames"):
        settings["comfy_frames"] = normalize_animation_settings(
            {"comfy_frames": review.get("animate_frames")}
        )["comfy_frames"]
    return settings


def uses_comfyui_animation(settings: dict[str, Any]) -> bool:
    mode = str(settings.get("animation_mode", "ffmpeg_25d"))
    return mode in {"comfyui", "comfyui_i2v"}


def uses_remotion_build(settings: dict[str, Any]) -> bool:
    return str(settings.get("animation_mode", "ffmpeg_25d")) == "remotion"


def resolve_video_build_script(settings: dict[str, Any]) -> str:
    if uses_remotion_build(settings):
        return "build_remotion_video.py"
    return "build_sleep_video.py"


def save_episode_animation(episode_dir: Path, settings: dict[str, Any]) -> None:
    from episode_review import load_review, normalize_animate_frames, save_review

    normalized = normalize_animation_settings(settings)
    review = load_review(episode_dir)
    review["animation"] = normalized
    review["animation_mode"] = normalized["animation_mode"]
    review["animation_method"] = normalized["animation_mode"]
    if normalized["animation_mode"] == "comfyui":
        review["animate_frames"] = normalize_animate_frames(normalized["comfy_frames"])
    save_review(episode_dir, review)