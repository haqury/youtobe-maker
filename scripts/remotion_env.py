"""Проверка Node/Remotion и подготовка props для рендера."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REMOTION_DIR = ROOT / "remotion_studio"
PUBLIC_EPISODE = REMOTION_DIR / "public" / "current_episode"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".webm"}
STEM_RE = re.compile(r"^(\d{2})")
MIN_VIDEO_BYTES = 4096
AMBIENT_DIR = ROOT / "assets" / "ambient"


def node_status() -> dict[str, Any]:
    node = shutil.which("node")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    modules_ok = (REMOTION_DIR / "node_modules" / "remotion").is_dir()
    return {
        "node": bool(node),
        "npm": bool(npm),
        "node_path": node or "",
        "npm_path": npm or "",
        "remotion_dir": REMOTION_DIR.is_dir(),
        "node_modules": modules_ok,
        "ready": bool(node and npm and REMOTION_DIR.is_dir() and modules_ok),
    }


def ensure_npm_install() -> tuple[bool, str]:
    status = node_status()
    if not status["node"] or not status["npm"]:
        return False, "Не найдены node или npm в PATH."
    if status["node_modules"]:
        return True, "node_modules уже установлен"
    result = subprocess.run(
        ["npm", "install"],
        cwd=str(REMOTION_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-800:]
        return False, f"npm install не удался: {tail}"
    return True, "npm install завершён"


def parse_chapters(script_path: Path) -> list[str]:
    if not script_path.is_file():
        return []
    chapters: list[str] = []
    for line in script_path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if text.startswith("## "):
            chapters.append(text.removeprefix("## ").strip())
    return chapters


def is_usable_video(path: Path) -> bool:
    try:
        return path.suffix.lower() in VIDEO_SUFFIXES and path.stat().st_size >= MIN_VIDEO_BYTES
    except OSError:
        return False


def list_episode_images(images_dir: Path) -> list[Path]:
    by_stem: dict[str, Path] = {}
    videos: dict[str, Path] = {}
    images: dict[str, Path] = {}
    if not images_dir.is_dir():
        return []
    for path in images_dir.iterdir():
        if not path.is_file():
            continue
        match = STEM_RE.match(path.name)
        if not match:
            continue
        stem = match.group(1)
        suffix = path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            videos[stem] = path
        elif suffix in IMAGE_SUFFIXES:
            images[stem] = path
    for stem in sorted(set(videos) | set(images)):
        video = videos.get(stem)
        if video is not None and is_usable_video(video):
            by_stem[stem] = video
        elif stem in images:
            by_stem[stem] = images[stem]
    return [by_stem[k] for k in sorted(by_stem)]


def first_ambient() -> Path | None:
    if not AMBIENT_DIR.is_dir():
        return None
    tracks = sorted(AMBIENT_DIR.glob("*.mp3"), key=lambda p: p.name.lower())
    return tracks[0] if tracks else None


def stage_episode_assets(episode_dir: Path) -> dict[str, Any]:
    voice = episode_dir / "voiceover.mp3"
    if not voice.is_file():
        raise FileNotFoundError(f"Нет voiceover.mp3: {voice}")
    images = list_episode_images(episode_dir / "images")
    if not images:
        raise FileNotFoundError(f"Нет кадров в {episode_dir / 'images'}")

    if PUBLIC_EPISODE.exists():
        for child in PUBLIC_EPISODE.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
    else:
        PUBLIC_EPISODE.mkdir(parents=True, exist_ok=True)

    shutil.copy2(voice, PUBLIC_EPISODE / "voiceover.mp3")
    rel_images: list[str] = []
    for src in images:
        dest_name = src.name
        dest = PUBLIC_EPISODE / dest_name
        shutil.copy2(src, dest)
        rel_images.append(f"current_episode/{dest_name}")

    ambient_rel = None
    ambient = first_ambient()
    if ambient and ambient.is_file():
        shutil.copy2(ambient, PUBLIC_EPISODE / "ambient.mp3")
        ambient_rel = "current_episode/ambient.mp3"

    chapters = parse_chapters(episode_dir / "script.md")
    return {
        "images": rel_images,
        "voiceoverRel": "current_episode/voiceover.mp3",
        "ambientRel": ambient_rel,
        "chapters": chapters,
    }


def remotion_settings_from_animation(anim: dict[str, Any]) -> dict[str, Any]:
    kb = float(anim.get("ken_burns_intensity", anim.get("kenBurnsIntensity", 0.85)))
    return {
        "enableChapterTitles": bool(anim.get("remotion_chapter_titles", True)),
        "enableParticles": bool(anim.get("remotion_particles", True)),
        "enableVignette": bool(anim.get("remotion_vignette", True)),
        "kenBurnsIntensity": max(0.2, min(1.2, kb)),
        "crossfadeSeconds": float(anim.get("remotion_crossfade", 1.5)),
    }