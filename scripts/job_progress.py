"""Единый формат прогресса для логов фоновых задач."""

from __future__ import annotations

import sys


def emit_progress(current: int, total: int, text: str) -> None:
    total = max(1, total)
    current = max(0, min(current, total))
    print(f"[PROGRESS] {current}/{total} | {text}", flush=True)