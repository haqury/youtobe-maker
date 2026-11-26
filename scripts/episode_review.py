"""Флаги проблемных кадров и участков видео (review.json в папке эпизода)."""

from __future__ import annotations

import json
import re
from pathlib import Path

REVIEW_NAME = "review.json"
STEM_RE = re.compile(r"^(\d{2})")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_ANIMATE_FRAMES = ("01", "02", "03")


def review_path(episode_dir: Path) -> Path:
    return episode_dir / REVIEW_NAME


def normalize_problem_images(raw: list | None) -> list[dict]:
    if not raw:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name = Path(item).name
        elif isinstance(item, dict):
            name = Path(str(item.get("name") or item.get("file") or "")).name
        else:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        note = ""
        if isinstance(item, dict):
            note = str(item.get("note", "")).strip()
        out.append({"name": name, "note": note})
    return sorted(out, key=lambda row: row["name"])


def stem_from_filename(filename: str) -> str:
    match = STEM_RE.match(Path(filename).name)
    return match.group(1) if match else Path(filename).stem


def flagged_image_names(data: dict) -> set[str]:
    return {row["name"] for row in normalize_problem_images(data.get("problem_images"))}


def flagged_stems(data: dict) -> set[str]:
    return {stem_from_filename(row["name"]) for row in normalize_problem_images(data.get("problem_images"))}


def is_image_path_flagged(data: dict, filename: str) -> bool:
    stem = stem_from_filename(filename)
    return stem in flagged_stems(data)


def disk_names_for_flagged(data: dict, image_names: list[str]) -> set[str]:
    stems = flagged_stems(data)
    return {name for name in image_names if stem_from_filename(name) in stems}


def image_note_map(data: dict) -> dict[str, str]:
    return {row["name"]: row.get("note", "").strip() for row in normalize_problem_images(data.get("problem_images"))}


def set_problem_image(data: dict, name: str, *, flagged: bool, note: str | None = None) -> None:
    name = Path(name).name
    stem = stem_from_filename(name)
    entries = {stem_from_filename(row["name"]): row for row in normalize_problem_images(data.get("problem_images"))}
    if flagged:
        row = entries.get(stem, {"name": name, "note": ""})
        row["name"] = name
        if note is not None:
            row["note"] = note.strip()
        entries[stem] = row
    elif stem in entries:
        del entries[stem]
    data["problem_images"] = sorted(entries.values(), key=lambda row: row["name"])


def flag_images_batch(data: dict, names: list[str], note: str) -> None:
    text = note.strip()
    for raw in names:
        set_problem_image(data, Path(raw).name, flagged=True, note=text if text else None)


def update_video_segment_note(data: dict, index: int, note: str) -> None:
    segments = data.get("video_segments", [])
    if 0 <= index < len(segments):
        segments[index]["note"] = note.strip()
        data["video_segments"] = segments


def remove_video_segment(data: dict, index: int) -> None:
    segments = data.get("video_segments", [])
    if 0 <= index < len(segments):
        segments.pop(index)
        data["video_segments"] = segments


def toggle_problem_image(data: dict, name: str) -> bool:
    """Переключает флаг. Возвращает True, если кадр теперь помечен."""
    name = Path(name).name
    stem = stem_from_filename(name)
    entries = {stem_from_filename(row["name"]): row for row in normalize_problem_images(data.get("problem_images"))}
    if stem in entries:
        del entries[stem]
        flagged = False
    else:
        entries[stem] = {"name": name, "note": ""}
        flagged = True
    data["problem_images"] = sorted(entries.values(), key=lambda row: row["name"])
    return flagged


def normalize_animate_frames(raw: object) -> list[str]:
    if raw is None:
        return list(DEFAULT_ANIMATE_FRAMES)
    items = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        match = STEM_RE.match(text)
        stem = match.group(1) if match else text.zfill(2)[:2]
        if not stem.isdigit():
            continue
        stem = stem.zfill(2)
        if stem in seen:
            continue
        seen.add(stem)
        out.append(stem)
    return sorted(out) if out else list(DEFAULT_ANIMATE_FRAMES)


def toggle_animate_frame(data: dict, stem: str) -> bool:
    stem = stem.zfill(2)[:2]
    current = set(normalize_animate_frames(data.get("animate_frames")))
    if stem in current:
        current.remove(stem)
        enabled = False
    else:
        current.add(stem)
        enabled = True
    data["animate_frames"] = sorted(current)
    return enabled


def is_animate_frame(data: dict, stem: str) -> bool:
    stem = stem.zfill(2)[:2]
    return stem in set(normalize_animate_frames(data.get("animate_frames")))


def load_review(episode_dir: Path) -> dict:
    path = review_path(episode_dir)
    if not path.is_file():
        return {
            "problem_images": [],
            "video_segments": [],
            "animate_frames": list(DEFAULT_ANIMATE_FRAMES),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {
            "problem_images": [],
            "video_segments": [],
            "animate_frames": list(DEFAULT_ANIMATE_FRAMES),
        }
    data["problem_images"] = normalize_problem_images(data.get("problem_images"))
    data.setdefault("video_segments", [])
    data["animate_frames"] = normalize_animate_frames(data.get("animate_frames"))
    return data


def save_review(episode_dir: Path, data: dict) -> None:
    segments = data.get("video_segments", [])
    cleaned_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = round(float(segment.get("start", 0)), 2)
            end = round(float(segment.get("end", 0)), 2)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        cleaned_segments.append(
            {
                "start": start,
                "end": end,
                "note": str(segment.get("note", "")).strip(),
            }
        )
    payload = dict(data)
    payload["problem_images"] = normalize_problem_images(data.get("problem_images"))
    payload["video_segments"] = cleaned_segments
    payload["animate_frames"] = normalize_animate_frames(data.get("animate_frames"))
    review_path(episode_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_image_flagged(episode_dir: Path, image_name: str) -> bool:
    return image_name in flagged_image_names(load_review(episode_dir))


def voice_duration_seconds(episode_dir: Path) -> float | None:
    voice_path = episode_dir / "voiceover.mp3"
    if not voice_path.is_file():
        return None
    try:
        from moviepy.audio.io.AudioFileClip import AudioFileClip

        clip = AudioFileClip(str(voice_path))
        try:
            duration = float(clip.duration or 0)
        finally:
            clip.close()
        return duration if duration > 0 else None
    except Exception:
        return None


def list_episode_images(episode_dir: Path) -> list[str]:
    image_dir = episode_dir / "images"
    if not image_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def image_indices_for_segment(duration: float, image_count: int, start: float, end: float) -> list[int]:
    if duration <= 0 or image_count <= 0 or end <= start:
        return []
    slot = duration / image_count
    hits: list[int] = []
    for index in range(image_count):
        t0 = index * slot
        t1 = duration if index == image_count - 1 else (index + 1) * slot
        if end > t0 and start < t1:
            hits.append(index + 1)
    return hits


def merge_note(existing: str, extra: str) -> str:
    extra = extra.strip()
    if not extra:
        return existing.strip()
    if not existing.strip():
        return extra
    if extra in existing:
        return existing.strip()
    return f"{existing.strip()}; {extra}"


def apply_video_segments_to_images(episode_dir: Path) -> tuple[int, int]:
    """По участкам видео помечает пересекающиеся кадры и дописывает комментарии.

    Возвращает (число новых пометок, число обновлённых комментариев).
    """
    review = load_review(episode_dir)
    segments = review.get("video_segments", [])
    if not segments:
        return 0, 0
    duration = voice_duration_seconds(episode_dir)
    names = list_episode_images(episode_dir)
    if duration is None or not names:
        return 0, 0

    entries = {row["name"]: row for row in normalize_problem_images(review.get("problem_images"))}
    added = 0
    updated = 0
    for segment in segments:
        note = str(segment.get("note", "")).strip()
        if not note:
            continue
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        for number in image_indices_for_segment(duration, len(names), start, end):
            stem = f"{number:02d}"
            image_name = next((n for n in names if n.startswith(stem)), f"{stem}.jpg")
            prefix = f"[видео {start:.1f}–{end:.1f} с] "
            merged = merge_note(entries.get(image_name, {}).get("note", ""), prefix + note)
            if image_name not in entries:
                entries[image_name] = {"name": image_name, "note": merged}
                added += 1
            elif entries[image_name].get("note", "") != merged:
                entries[image_name]["note"] = merged
                updated += 1
    review["problem_images"] = sorted(entries.values(), key=lambda row: row["name"])
    save_review(episode_dir, review)
    return added, updated