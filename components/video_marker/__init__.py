"""Плеер с отметкой участков по текущему времени (Streamlit custom component)."""

from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_ROOT = Path(__file__).resolve().parent

_video_marker = components.declare_component(
    "video_mark_controls",
    path=str(_ROOT),
)


def video_marker(*, key: str | None = None, video_url: str | None = None, **_: object) -> dict | None:
    """Панель ⏺/⏹: время с video Streamlit на странице. video_url — для совместимости, не используется."""
    result = _video_marker(key=key, default=None)
    if not result:
        return None
    return dict(result)