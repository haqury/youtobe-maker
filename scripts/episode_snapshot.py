"""Снапшоты файлов эпизода для отката после ИИ-генерации."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

HISTORY_DIR = "history"
FILE_SCRIPT = "script.md"
FILE_SCRIPT_RU = "script_ru.md"
FILE_VISUAL = "visual_prompts.md"
FILE_METADATA = "metadata.md"
PLOT_BRIEF_TXT = "plot_brief.txt"
SNAPSHOT_NAMES = (
    FILE_SCRIPT,
    FILE_SCRIPT_RU,
    FILE_VISUAL,
    FILE_METADATA,
    PLOT_BRIEF_TXT,
)


def _word_count(text: str) -> int:
    from studio_ai import count_words

    return count_words(text)


def _safe_reason(reason_label: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", (reason_label or "snapshot").strip())
    return cleaned[:48] or "snapshot"


def save_episode_snapshot(episode_dir: Path, reason_label: str) -> Path | None:
    """Копирует ключевые файлы эпизода в history/; None если script.md ещё нет."""
    episode_dir = episode_dir.resolve()
    if not (episode_dir / FILE_SCRIPT).is_file():
        return None
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = episode_dir / HISTORY_DIR / f"{stamp}_{_safe_reason(reason_label)}"
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in SNAPSHOT_NAMES:
        src = episode_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied.append(name)
    en_path = dest / FILE_SCRIPT
    words_en = _word_count(en_path.read_text(encoding="utf-8-sig")) if en_path.is_file() else 0
    manifest = {
        "reason": reason_label,
        "created_at": stamp,
        "files": copied,
        "words_en": words_en,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dest


def list_episode_snapshots(episode_dir: Path) -> list[dict[str, object]]:
    """Новые снапшоты первыми."""
    root = episode_dir.resolve() / HISTORY_DIR
    if not root.is_dir():
        return []
    rows: list[dict[str, object]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        words = 0
        reason = ""
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                words = int(data.get("words_en", 0))
                reason = str(data.get("reason", ""))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if not words and (path / FILE_SCRIPT).is_file():
            words = _word_count((path / FILE_SCRIPT).read_text(encoding="utf-8-sig"))
        label = path.name
        if reason:
            label = f"{path.name} — {reason}"
        rows.append({"id": path.name, "path": path, "label": label, "words_en": words})
    return rows


def restore_episode_snapshot(episode_dir: Path, snapshot_id: str) -> str | None:
    episode_dir = episode_dir.resolve()
    if not snapshot_id.strip():
        return "Не выбрана версия."
    snap_dir = episode_dir / HISTORY_DIR / snapshot_id
    if not snap_dir.is_dir():
        return f"Снапшот не найден: {snapshot_id}"
    save_episode_snapshot(episode_dir, "before_restore")
    for name in SNAPSHOT_NAMES:
        src = snap_dir / name
        if src.is_file():
            shutil.copy2(src, episode_dir / name)
    return None


def snapshot_display_line(row: dict[str, object]) -> str:
    words = int(row.get("words_en", 0))
    return f"{row.get('label', row.get('id'))} ({words} слов EN)"