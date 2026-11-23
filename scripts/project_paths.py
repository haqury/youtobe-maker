"""Общие пути репозитория (studio / pipeline / channels)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STUDIO_DIR = REPO_ROOT / "studio"
PIPELINE_DIR = REPO_ROOT / "scripts"
CHANNELS_DIR = REPO_ROOT / "channels"
CONFIG_DIR = REPO_ROOT / "config"
TEMPLATES_DIR = REPO_ROOT / "templates"
TOOLS_DIR = REPO_ROOT / "tools"
MODELS_DIR = REPO_ROOT / "models"
LOGS_DIR = REPO_ROOT / "logs"
MARKET_RADAR_DIR = REPO_ROOT / "market_radar"


def episode_media_dir(episode_dir: Path) -> Path:
    """Каталог рендера внутри эпизода (картинки, mp3, mp4)."""
    return episode_dir.resolve()


def episode_images_dir(episode_dir: Path) -> Path:
    return episode_dir / "images"